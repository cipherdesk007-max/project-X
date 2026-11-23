"""Prove della versione 13: crediti per modello, ricarica mensile, acquisto di ricariche, crediti dal gestore."""
import json
import unittest
from datetime import datetime, timezone

from base import PW
from server import credits, db, ops
from test_v8 import STRIPE, V8


class Crediti(V8):
    def on(self, **more):
        values = {"SFB_CREDITS": "1", "CREDIT_RATE_KIMI": "2", "CREDIT_RATE_CLAUDE": "10", "CREDIT_RATE_CHATGPT": "8",
                  "CREDITS_START": "10", "CREDITS_BUSINESS": "50", "CREDITS_COMPLETO": "200"}
        values.update(more)
        self.assertEqual(self.save(values)[0], 200)

    def tenant(self, tid):
        con = db.connect()
        row = dict(con.execute("SELECT * FROM tenants WHERE id=?", (tid,)).fetchone())
        con.close()
        return row

    def view(self, token):
        return self.call("GET", "/api/credits", token=token)[1]

    def test_da_spenti_non_cambia_niente(self):
        a, tid = self.new_tenant("Crediti Spenti", "start")
        self.assertFalse(self.view(a)["enabled"])
        self.assertEqual(self.chat(a, "ciao")[0], 200)
        con = db.connect()
        self.assertEqual(con.execute("SELECT COUNT(*) FROM credit_ledger WHERE tenant_id=?", (tid,)).fetchone()[0], 0)
        con.close()
        self.assertIsNone([t for t in self.call("GET", "/api/admin/tenants", token=self.gestore)[1]["tenants"] if t["id"] == tid][0]["credits"])

    def test_ogni_modello_ha_il_suo_costo(self):
        self.on()
        self.assertEqual((credits.cost("kimi", 1000), credits.cost("claude", 1000), credits.cost("chatgpt", 1000)), (2, 10, 8))
        self.assertEqual((credits.cost("kimi", 1), credits.cost("kimi", 1001), credits.cost("kimi", 0)), (1, 3, 0), "si arrotonda per eccesso, mai sotto un credito")
        self.assertEqual((credits.cost("locale", 999999), credits.rate("sconosciuto")), (0, 0), "il modello locale non costa")
        t = {"id": 1, "package_id": "start"}
        self.assertTrue(credits.applies(t, "kimi", {}))
        self.assertFalse(credits.applies(t, "kimi", {"moonshot_api_key": "sk-sua"}), "con la propria chiave non si consumano crediti")
        self.assertTrue(credits.applies(t, "claude", {"moonshot_api_key": "sk-sua"}), "la chiave di un modello non vale per un altro")
        self.assertFalse(credits.applies(t, "locale", {}))

    def test_una_chat_scala_i_crediti_del_modello_usato(self):
        self.on()
        a, tid = self.new_tenant("Crediti Chat", "completo")
        b, tid_b = self.new_tenant("Crediti Altra", "completo")
        self.assertEqual(self.view(a)["available"], 200)
        self.assertEqual(self.chat(a, "Scrivi due righe", model="kimi")[0], 200)
        con = db.connect()
        self.addCleanup(con.close)
        tokens = con.execute("SELECT input_tokens+output_tokens FROM usage WHERE tenant_id=? ORDER BY id DESC LIMIT 1", (tid,)).fetchone()[0]
        row = con.execute("SELECT amount, from_included, from_extra, model FROM credit_ledger WHERE tenant_id=? AND kind='consumo'", (tid,)).fetchone()
        self.assertEqual(tuple(row), (credits.cost("kimi", tokens), credits.cost("kimi", tokens), 0, "kimi"))
        v = self.view(a)
        self.assertEqual((v["available"], v["included_left"], v["extra"]), (200 - row[0], 200 - row[0], 0))
        self.assertEqual([(m["model"], m["credits"]) for m in v["month"]], [("kimi", row[0])])
        self.assertEqual({r["model"]: r["per_1000"] for r in v["rates"]}, {"kimi": 2, "claude": 10, "chatgpt": 8, "locale": 0})
        self.assertEqual(self.view(b)["available"], 200, "l'altra azienda non è toccata")
        self.assertEqual(self.chat(a, "Scrivi due righe", model="claude")[0], 200)
        spent = [tuple(r) for r in con.execute("SELECT model, amount FROM credit_ledger WHERE tenant_id=? AND kind='consumo' ORDER BY id", (tid,))]
        self.assertGreater(spent[1][1], spent[0][1], "la stessa domanda con Claude costa più crediti che con Kimi")

    def test_prima_gli_inclusi_poi_i_comprati_e_a_zero_ci_si_ferma(self):
        self.on()
        a, tid = self.new_tenant("Crediti Ordine", "start")
        con = db.connect()
        self.addCleanup(con.close)
        t = self.tenant(tid)
        credits.grant(con, tid, 5, "regalo")
        self.assertEqual(credits.charge(con, t, None, "kimi", 3000), 6)
        b = credits.balance(con, t)
        self.assertEqual((b["included_left"], b["extra"], b["available"]), (4, 5, 9))
        self.assertEqual(credits.charge(con, t, None, "kimi", 3000), 6)
        b = credits.balance(con, t)
        self.assertEqual((b["included_left"], b["extra"], b["available"]), (0, 3, 3), "finiti gli inclusi si passa ai comprati")
        self.assertEqual(self.chat(a, "ciao", model="kimi")[0], 200, "con crediti rimasti si lavora")
        credits.charge(con, t, None, "kimi", 5000)
        self.assertLessEqual(credits.balance(con, t)["available"], 0)
        status, out = self.chat(a, "ciao", model="kimi")
        self.assertEqual(status, 402, out)
        self.assertIn("Crediti finiti", out["error"])
        self.assertEqual(self.chat(a, "ciao", model="locale")[0], 200, "il modello locale resta disponibile")
        self.assertIn("Crediti finiti", [n["title"] for n in self.call("GET", "/api/news", token=a)[1]["news"]])
        # il primo del mese gli inclusi tornano pieni; il debito sui comprati resta
        nxt = datetime(2099, 1, 1, tzinfo=timezone.utc)
        future = credits.balance(con, t, now=nxt)
        self.assertEqual((future["included_left"], future["resets_on"]), (10, "2099-02-01"))
        self.assertEqual(future["extra"], credits.balance(con, t)["extra"])
        self.assertEqual(credits.resets_on(datetime(2026, 12, 31, tzinfo=timezone.utc)), "2027-01-01")

    def test_il_gestore_aggiunge_e_toglie_crediti(self):
        self.on()
        a, tid = self.new_tenant("Crediti Gestore", "business")
        grant = lambda v, token=None: self.call("POST", "/api/admin/tenants/%d" % tid, {"credits_add": v}, token or self.gestore)[0]
        self.assertEqual(grant(500), 200)
        self.assertEqual(grant(-200), 200)
        v = self.view(a)
        self.assertEqual((v["extra"], v["available"]), (300, 350))
        self.assertEqual([m["amount"] for m in v["moves"]], [-200, 500])
        self.assertEqual(grant(500, a), 403, "un'azienda non si regala crediti da sola")
        self.assertEqual(grant("tanti"), 400)
        self.assertEqual(self.view(a)["extra"], 300)
        self.assertEqual([t for t in self.call("GET", "/api/admin/tenants", token=self.gestore)[1]["tenants"] if t["id"] == tid][0]["credits"], 350)
        self.assertEqual(self.call("POST", "/api/admin/tenants/999999", {"credits_add": 5}, self.gestore)[0], 404)

    def test_comprare_una_ricarica_con_la_carta(self):
        self.on()
        a, tid = self.new_tenant("Crediti Compra", "start")
        b, tid_b = self.new_tenant("Crediti Guarda", "start")
        con = db.connect()
        self.addCleanup(con.close)
        ops.create_user(con, tid, "operatore@crediti-compra.it", "Operatore", "member", PW)
        self.assertFalse(self.view(a)["pack"]["buyable"])
        self.assertEqual(self.call("POST", "/api/credits/checkout", {"quantity": 1}, a)[0], 400, "senza ricarica impostata non si compra")
        self.stripe_on()
        self.assertEqual(self.save({"CREDIT_PACK_SIZE": "1000", "STRIPE_PRICE_CREDITS": "price_crediti"})[0], 200)
        self.assertTrue(self.view(a)["pack"]["buyable"])
        self.assertEqual(self.call("POST", "/api/credits/checkout", {"quantity": 1}, self.login("operatore@crediti-compra.it"))[0], 403)
        for bad in (0, 21, -1, "molte"):
            self.assertEqual(self.call("POST", "/api/credits/checkout", {"quantity": bad}, a)[0], 400, bad)
        STRIPE["posts"].clear()
        status, out = self.call("POST", "/api/credits/checkout", {"quantity": 3}, a)
        self.assertEqual(status, 200, out)
        form = STRIPE["posts"][-1]["form"]
        self.assertEqual((form["mode"], form["line_items[0][price]"], form["line_items[0][quantity]"], form["metadata[credits]"], form["metadata[kind]"]),
                         ("payment", "price_crediti", "3", "3000", "crediti"))
        con.execute("UPDATE tenants SET stripe_subscription='sub_attivo', stripe_customer='cus_C' WHERE id=?", (tid,))
        paid = {"id": "evt_c1", "type": "checkout.session.completed", "data": {"object": {
            "mode": "payment", "payment_status": "paid", "client_reference_id": str(tid), "customer": "cus_C", "subscription": None,
            "metadata": {"tenant_id": str(tid), "kind": "crediti", "credits": "3000"}}}}
        self.assertEqual(self.hook(paid)[0], 200)
        self.assertEqual((self.view(a)["extra"], self.view(b)["extra"]), (3000, 0))
        self.assertTrue(json.loads(self.hook(paid)[2])["duplicate"])
        self.assertEqual(self.view(a)["extra"], 3000, "lo stesso avviso rimandato non raddoppia i crediti")
        self.assertEqual(self.tenant(tid)["stripe_subscription"], "sub_attivo", "una ricarica non tocca l'abbonamento")
        unpaid = json.loads(json.dumps(paid).replace("evt_c1", "evt_c2").replace('"paid"', '"unpaid"'))
        self.assertEqual(self.hook(unpaid)[0], 200)
        forged = json.loads(json.dumps(paid).replace("evt_c1", "evt_c3"))
        self.assertEqual(self.hook(forged, secret="un-altro-segreto")[0], 400)
        other = json.loads(json.dumps(paid).replace("evt_c1", "evt_c4").replace('"crediti"', '"altro"'))
        self.assertEqual(self.hook(other)[0], 200)
        self.assertEqual(self.view(a)["extra"], 3000, "non pagato, firma falsa o altro tipo di pagamento: nessun credito")
        self.assertIn("Crediti aggiunti", [n["title"] for n in self.call("GET", "/api/news", token=a)[1]["news"]])


class PagaPrima(V8):
    """La verifica del computer è gratuita; collegarlo, scaricare i pacchetti dell'azienda e ricevere lavori no."""

    def state(self, tid, **cols):
        con = db.connect()
        for k, v in cols.items():
            con.execute("UPDATE tenants SET %s=? WHERE id=?" % k, (v, tid))
        con.close()

    def code(self, token):
        return self.call("POST", "/api/devices/code", {}, token)

    def test_in_prova_o_senza_pagamento_il_computer_non_si_collega(self):
        a, tid = self.new_tenant("Paga Prima Uno", "completo")
        far = "2099-01-01"
        self.assertEqual(self.code(a)[0], 200, "un'azienda creata dal gestore senza scadenza può collegare i computer")
        self.state(tid, paid_until=far, on_trial=1)
        status, out = self.code(a)
        self.assertEqual(status, 402, out)
        self.assertIn("prova gratuita", out["error"])
        self.assertIn("prova gratuita", self.call("GET", "/api/devices", token=a)[1]["blocked"])
        self.assertEqual(self.chat(a, "ciao")[0], 200, "in prova gli agenti lavorano comunque sui nostri server")
        # verificare il computer resta gratuito e senza account
        self.assertEqual(self.call("GET", "/api/launcher/catalog")[0], 200)
        # il primo pagamento sblocca
        self.stripe_on()
        self.state(tid, stripe_customer="cus_PP")
        end = 4102444800  # 2100-01-01
        self.assertEqual(self.hook({"id": "evt_pp1", "type": "invoice.paid", "data": {"object": {"customer": "cus_PP", "lines": {"data": [{"period": {"end": end}}]}}}})[0], 200)
        self.assertEqual(self.code(a)[0], 200)
        self.assertEqual(self.call("GET", "/api/devices", token=a)[1]["blocked"], "")
        # chi si è registrato da solo e non ha mai pagato, con i pagamenti attivi
        self.state(tid, paid_until="", on_trial=0, origin="registrazione")
        self.assertEqual(self.code(a)[0], 402)
        self.state(tid, origin="gestore")
        self.assertEqual(self.code(a)[0], 200)
        # la data messa a mano dal gestore (bonifico) vale come pagamento e chiude la prova
        self.state(tid, on_trial=1, paid_until=far)
        self.assertEqual(self.code(a)[0], 402)
        self.assertEqual(self.call("POST", "/api/admin/tenants/%d" % tid, {"paid_until": "2098-06-30"}, self.gestore)[0], 200)
        self.assertEqual(self.code(a)[0], 200)

    def test_un_computer_gia_collegato_si_ferma_quando_l_abbonamento_scade(self):
        a, tid = self.new_tenant("Paga Prima Due", "completo")
        b, tid_b = self.new_tenant("Paga Prima Tre", "completo")
        tokens = {}
        for name, t in (("a", a), ("b", b)):
            code = self.code(t)[1]["code"]
            status, out = self.call("POST", "/api/launcher/pair", {"code": code, "name": "PC " + name})
            self.assertEqual(status, 200, out)
            tokens[name] = out["token"]
        self.assertEqual(self.call("GET", "/api/launcher/sync", token=tokens["a"])[0], 200)
        self.state(tid, paid_until="2020-01-01")
        self.assertEqual(self.call("GET", "/api/launcher/sync", token=tokens["a"])[0], 402)
        self.assertEqual(self.call("POST", "/api/launcher/jobs/claim", {"wait": 0}, tokens["a"])[0], 402)
        self.assertEqual(self.code(a)[0], 402)
        self.assertEqual(self.call("GET", "/api/launcher/sync", token=tokens["b"])[0], 200, "l'altra azienda continua")
        self.assertEqual(self.call("POST", "/api/launcher/jobs/claim", {"wait": 0}, tokens["b"])[0], 200)
        # un codice preso prima della scadenza non vale dopo
        self.state(tid, paid_until="")
        code = self.code(a)[1]["code"]
        self.state(tid, paid_until="2020-01-01")
        self.assertEqual(self.call("POST", "/api/launcher/pair", {"code": code, "name": "PC tardi"})[0], 402)
        self.state(tid, paid_until="")
        self.assertEqual(self.call("GET", "/api/launcher/sync", token=tokens["a"])[0], 200, "rinnovato: il computer riprende senza ricollegarlo")


for _name in [n for n in dir(V8) if n.startswith("test_")]:
    setattr(PagaPrima, _name, None)
    setattr(Crediti, _name, None)  # le prove della versione 8 non vanno rieseguite da qui
del V8

if __name__ == "__main__":
    unittest.main()
