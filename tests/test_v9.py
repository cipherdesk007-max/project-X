"""Prove della versione 9: addebito non riuscito, giorni di tolleranza, disdetta e gestione della carta su Stripe, email prima della scadenza."""
import json
import os
import time
import unittest
from datetime import datetime, timedelta, timezone

from base import PW
from server import billing, db, ops, registration, scheduler
from test_v8 import STRIPE, V8


def day(delta):
    return (datetime.now(timezone.utc).date() + timedelta(days=delta)).isoformat()


class V9(V8):
    def setUp(self):
        super().setUp()
        self.sent, real = [], registration.send_mail
        registration.send_mail = lambda to, subject, text: self.sent.append((to, subject, text))
        billing.ASYNC = False
        self.addCleanup(lambda: (setattr(registration, "send_mail", real), setattr(billing, "ASYNC", True)))

    def set_tenant(self, tid, **cols):
        con = db.connect()
        for k, v in cols.items():
            con.execute("UPDATE tenants SET %s=? WHERE id=?" % k, (v, tid))
        con.close()

    def sub(self, token):
        return self.call("GET", "/api/me", token=token)[1]["tenant"]["subscription"]

    def link(self, tid, customer="cus_9", subscription="sub_9"):
        self.assertEqual(self.hook({"id": "evt_l_%s" % customer, "type": "checkout.session.completed",
                                    "data": {"object": {"client_reference_id": str(tid), "customer": customer, "subscription": subscription}}})[0], 200)

    # ---------- addebito non riuscito ----------
    def test_addebito_non_riuscito_avvisa_e_si_chiude_al_pagamento(self):
        a, tid = self.new_tenant("Fallito Uno", "completo")
        b, tid_b = self.new_tenant("Fallito Due", "completo")
        con = db.connect()
        ops.create_user(con, tid, "operatore@fallito-uno.it", "Operatore", "member", PW)
        con.close()
        self.stripe_on()
        self.assertEqual(self.save({"SFB_SMTP_HOST": "smtp.alvea.test"})[0], 200)
        self.link(tid, "cus_F", "sub_F")
        self.assertFalse(self.sub(a)["payment_failed"])
        failed = {"id": "evt_f1", "type": "invoice.payment_failed", "data": {"object": {"customer": "cus_F", "amount_due": 9900, "currency": "eur"}}}
        self.assertEqual(self.hook(failed)[0], 200)
        self.assertTrue(self.sub(a)["payment_failed"])
        self.assertTrue(self.call("GET", "/api/billing", token=a)[1]["payment_failed"])
        self.assertFalse(self.sub(b)["payment_failed"], "l'altra azienda non è toccata")
        self.assertEqual([(to, s) for to, s, _ in self.sent], [("admin@fallito-uno.it", "Alvea.ai: pagamento non riuscito")], "solo agli amministratori di quell'azienda")
        self.assertIn("/#abbonamento", self.sent[0][2])
        self.assertIn("Pagamento non riuscito", [n["title"] for n in self.call("GET", "/api/news", token=a)[1]["news"]])
        self.assertTrue(json.loads(self.hook(failed)[2])["duplicate"])
        self.assertEqual(len(self.sent), 1, "lo stesso avviso rimandato non manda una seconda email")
        end = int(time.time()) + 30 * 86400
        self.assertEqual(self.hook({"id": "evt_f2", "type": "invoice.paid", "data": {"object": {"customer": "cus_F", "lines": {"data": [{"period": {"end": end}}]}}}})[0], 200)
        self.assertFalse(self.sub(a)["payment_failed"], "al primo pagamento riuscito l'avviso sparisce")

    # ---------- tolleranza ----------
    def test_giorni_di_tolleranza(self):
        a, tid = self.new_tenant("Tolleranza Uno", "completo")
        self.set_tenant(tid, paid_until=day(-2))
        s = self.sub(a)
        self.assertEqual((s["expired"], s["grace"]), (True, False), "senza tolleranza: in pausa dal giorno dopo")
        self.assertEqual(self.chat(a, "ciao")[0], 402)
        self.assertEqual(self.save({"SFB_GRACE_DAYS": "3"})[0], 200)
        s = self.sub(a)
        self.assertEqual((s["expired"], s["grace"], s["grace_left"]), (False, True, 1))
        self.assertEqual(self.chat(a, "ciao")[0], 200, "nei giorni di tolleranza gli agenti lavorano")
        self.set_tenant(tid, paid_until=day(-3))
        self.assertEqual((self.sub(a)["expired"], self.sub(a)["grace_left"]), (False, 0), "ultimo giorno di tolleranza")
        self.set_tenant(tid, paid_until=day(-4))
        self.assertEqual((self.sub(a)["expired"], self.sub(a)["grace"]), (True, False))
        self.assertEqual(self.chat(a, "ciao")[0], 402)
        self.assertEqual(self.save({"SFB_GRACE_DAYS": "9000"})[0], 200)
        self.set_tenant(tid, paid_until=day(-31))
        self.assertTrue(self.sub(a)["expired"], "la tolleranza non supera mai i 30 giorni")
        self.set_tenant(tid, paid_until=day(1))
        self.assertEqual((self.sub(a)["expired"], self.sub(a)["grace"]), (False, False))

    # ---------- gestione su Stripe e disdetta ----------
    def test_gestione_della_carta_e_disdetta(self):
        a, tid = self.new_tenant("Gestione Uno", "completo")
        b, tid_b = self.new_tenant("Gestione Due", "completo")
        con = db.connect()
        ops.create_user(con, tid, "operatore@gestione-uno.it", "Operatore", "member", PW)
        con.close()
        self.stripe_on()
        self.assertFalse(self.call("GET", "/api/billing", token=a)[1]["portal"])
        self.assertEqual(self.call("POST", "/api/billing/portal", {}, a)[0], 400, "prima di aver pagato non c'è niente da gestire")
        self.link(tid, "cus_G", "sub_G")
        self.assertEqual(self.call("POST", "/api/billing/portal", {}, self.login("operatore@gestione-uno.it"))[0], 403)
        self.assertEqual(self.call("POST", "/api/billing/portal", {}, b)[0], 400)
        STRIPE["posts"].clear()
        status, out = self.call("POST", "/api/billing/portal", {}, a)
        self.assertEqual(status, 200, out)
        sent = STRIPE["posts"][-1]
        self.assertEqual((sent["path"], sent["auth"], sent["form"]["customer"]), ("/v1/billing_portal/sessions", "Bearer sk_test_prova", "cus_G"))
        self.assertTrue(sent["form"]["return_url"].endswith("/#abbonamento"))
        STRIPE["status"] = 400
        self.assertEqual(self.call("POST", "/api/billing/portal", {}, a)[0], 502)
        # il cliente disdice dalla pagina di Stripe: resta attivo fino alla fine del periodo
        end = int(datetime(2031, 5, 20, 9, tzinfo=timezone.utc).timestamp())
        upd = lambda n, **o: self.hook({"id": "evt_u%d" % n, "type": "customer.subscription.updated", "data": {"object": dict({"id": "sub_G", "customer": "cus_G"}, **o)}})[0]
        self.assertEqual(upd(1, cancel_at_period_end=True, items={"data": [{"current_period_end": end}]}), 200)
        bill = self.call("GET", "/api/billing", token=a)[1]
        self.assertEqual((bill["cancel_at"], bill["subscribed"]), ("2031-05-20", True))
        self.assertEqual(self.call("GET", "/api/billing", token=b)[1]["cancel_at"], "")
        self.assertEqual(upd(2, cancel_at_period_end=True, current_period_end=end), 200)
        titles = [n["title"] for n in self.call("GET", "/api/news", token=a)[1]["news"]]
        self.assertEqual(titles.count("Disdetta registrata"), 1, "un aggiornamento che non cambia la data non ripete l'avviso")
        self.assertEqual(upd(3, cancel_at_period_end=False, cancel_at=None), 200)
        self.assertEqual(self.call("GET", "/api/billing", token=a)[1]["cancel_at"], "", "disdetta ritirata")
        self.assertEqual(upd(4, cancel_at=end + 86400), 200)
        self.assertEqual(self.call("GET", "/api/billing", token=a)[1]["cancel_at"], "2031-05-21")
        self.assertEqual(self.hook({"id": "evt_u5", "type": "customer.subscription.deleted", "data": {"object": {"id": "sub_G", "customer": "cus_G"}}})[0], 200)
        bill = self.call("GET", "/api/billing", token=a)[1]
        self.assertEqual((bill["cancel_at"], bill["subscribed"], bill["portal"]), ("", False, True), "le ricevute restano consultabili")

    # ---------- email prima della scadenza ----------
    def test_email_prima_della_scadenza_una_sola_volta(self):
        _, soon = self.new_tenant("Scade Presto", "completo")
        _, far = self.new_tenant("Scade Tardi", "completo")
        _, gone = self.new_tenant("Gia Scaduta", "completo")
        _, auto = self.new_tenant("Rinnovo Automatico", "completo")
        _, quit_ = self.new_tenant("Ha Disdetto", "completo")
        _, off = self.new_tenant("Sospesa", "completo")
        self.set_tenant(soon, paid_until=day(5))
        self.set_tenant(far, paid_until=day(8))
        self.set_tenant(gone, paid_until=day(-1))
        self.set_tenant(auto, paid_until=day(3), stripe_subscription="sub_auto")
        self.set_tenant(quit_, paid_until=day(3), stripe_subscription="sub_q", stripe_cancel_at=day(3))
        self.set_tenant(off, paid_until=day(2), active=0)
        con = db.connect()
        self.addCleanup(con.close)
        con.execute("UPDATE tenants SET expiry_notice_for=paid_until WHERE id NOT IN (?,?,?,?,?,?)", (soon, far, gone, auto, quit_, off))  # le aziende delle altre prove
        self.assertEqual(billing.remind(con), 0, "senza posta non parte niente")
        self.assertEqual(con.execute("SELECT expiry_notice_for FROM tenants WHERE id=?", (soon,)).fetchone()[0], "", "e niente viene segnato come avvisato")
        self.assertEqual(self.save({"SFB_SMTP_HOST": "smtp.alvea.test"})[0], 200)
        self.assertEqual(billing.remind(con), 2)
        self.assertEqual(sorted(to for to, _, _ in self.sent), ["admin@ha-disdetto.it", "admin@scade-presto.it"])
        text = [t for to, _, t in self.sent if to == "admin@scade-presto.it"][0]
        self.assertIn("/".join(reversed(day(5).split("-"))), text)
        self.assertIn("contatta chi ti ha venduto", text, "senza Stripe non si promette il pagamento con carta")
        self.assertEqual(billing.remind(con), 0, "una sola email per scadenza")
        self.assertEqual(scheduler.reminders(), 0)
        self.set_tenant(soon, paid_until=day(6))
        self.stripe_on()
        self.assertEqual(billing.remind(con), 1, "se la scadenza cambia, l'avviso riparte per la data nuova")
        self.assertIn("/#abbonamento", self.sent[-1][2])
        self.set_tenant(far, paid_until=day(7))
        real = registration.send_mail

        def broken(to, subject, text):
            raise OSError("posta giù")
        registration.send_mail = broken
        self.assertEqual(billing.remind(con), 0, "un invio fallito non ferma il giro e non conta come avviso mandato")
        registration.send_mail = real
        before = len(self.sent)
        self.assertEqual(billing.remind(con), 1, "e al giro dopo si riprova")
        self.assertEqual(self.sent[before][0], "admin@scade-tardi.it")


for _name in [n for n in dir(V8) if n.startswith("test_")]:
    setattr(V9, _name, None)  # le prove della versione 8 non vanno rieseguite da qui
del V8

if __name__ == "__main__":
    unittest.main()
