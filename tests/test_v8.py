"""Prove della versione 8: impostazioni della piattaforma dal pannello del gestore e pagamenti con carta (Stripe)."""
import hashlib
import hmac
import http.client
import json
import os
import sqlite3
import threading
import time
import unittest
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from base import Base
from server import billing, config, db, registration, settings

STRIPE = {"posts": [], "status": 200}


class FakeStripe(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        STRIPE["posts"].append({"path": self.path, "auth": self.headers.get("Authorization"), "form": {}, "method": "GET"})
        data = json.dumps({"id": self.path.rsplit("/", 1)[-1], "items": {"data": [{"id": "si_1", "price": {"product": "prod_1"}}]}} if STRIPE["status"] == 200
                          else {"error": {"message": "No such subscription"}}).encode()
        self.send_response(STRIPE["status"])
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        form = dict(urllib.parse.parse_qsl(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()))
        STRIPE["posts"].append({"path": self.path, "auth": self.headers.get("Authorization"), "form": form})
        port = self.server.server_address[1]
        data = json.dumps({"id": "cs_test_1", "url": "http://127.0.0.1:%d/paga/cs_test_1" % port} if STRIPE["status"] == 200
                          else {"error": {"message": "No such price"}}).encode()
        self.send_response(STRIPE["status"])
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class V8(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stripe = ThreadingHTTPServer(("127.0.0.1", 0), FakeStripe)
        threading.Thread(target=cls.stripe.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.stripe.shutdown()
        super().tearDownClass()

    def setUp(self):
        registration.reset_throttle()
        STRIPE["posts"].clear()
        STRIPE["status"] = 200
        self.gestore = self.login("gestore@test.it")
        os.environ["STRIPE_API_BASE"] = "http://127.0.0.1:%d" % self.stripe.server_address[1]
        self.addCleanup(self.clean)

    def clean(self):
        con = db.connect()
        con.execute("DELETE FROM platform_settings")
        settings.load(con)
        con.close()
        os.environ["STRIPE_API_BASE"] = ""

    def save(self, values, token=None):
        return self.call("POST", "/api/admin/settings", {"values": values}, token or self.gestore)

    def fields(self):
        return {f["key"]: f for f in self.call("GET", "/api/admin/settings", token=self.gestore)[1]["fields"]}

    def send_raw(self, path, raw, headers):
        host = urllib.parse.urlsplit(self.base)
        con = http.client.HTTPConnection(host.hostname, host.port, timeout=30)
        con.request("POST", path, body=raw, headers=dict(headers, **{"Content-Length": str(len(raw))}))
        r = con.getresponse()
        out = (r.status, dict(r.getheaders()), r.read().decode("utf-8"))
        con.close()
        return out

    def hook(self, event, secret="whsec_prova", stamp=None, sig=None):
        raw = json.dumps(event).encode()
        stamp = int(stamp or time.time())
        sig = sig or hmac.new(secret.encode(), b"%d." % stamp + raw, hashlib.sha256).hexdigest()
        return self.send_raw("/api/billing/webhook", raw, {"Content-Type": "application/json", "Stripe-Signature": "t=%d,v1=%s" % (stamp, sig)})

    # ---------- impostazioni ----------
    def test_impostazioni_solo_del_gestore_e_segreti_mai_restituiti(self):
        admin, _ = self.new_tenant("Impostazioni Uno", "start")
        self.assertEqual(self.call("GET", "/api/admin/settings", token=admin)[0], 403)
        self.assertEqual(self.save({"SFB_SMTP_HOST": "smtp.intruso.it"}, admin)[0], 403)
        self.assertEqual(self.call("POST", "/api/admin/settings/test-email", {}, admin)[0], 403)
        self.assertEqual(self.call("GET", "/api/admin/settings")[0], 401)
        for bad in ({"SFB_SECRET_KEY": "x"}, {"SFB_PUBLIC_URL": "https://altrove.it"}, {"PATH": "/tmp"}, {"SFB_SMTP_PORT": "molte"},
                    {"SFB_REGISTRATION": "forse"}, {"SEARXNG_URL": "ftp://x"}, {"SFB_SMTP_HOST": "a\nb"}, {"OPENAI_API_KEY": "k" * 501}, {}):
            self.assertEqual(self.save(bad)[0], 400, bad)
        secret = "sk-ant-chiave-segretissima-123"
        status, out = self.save({"ANTHROPIC_API_KEY": secret, "SFB_SMTP_HOST": "smtp.alvea.test", "SFB_TRIAL_DAYS": "30"})
        self.assertEqual((status, out["changed"]), (200, ["ANTHROPIC_API_KEY", "SFB_SMTP_HOST", "SFB_TRIAL_DAYS"]))
        f = self.fields()
        self.assertEqual((f["ANTHROPIC_API_KEY"]["value"], f["ANTHROPIC_API_KEY"]["set"], f["ANTHROPIC_API_KEY"]["source"]), ("", True, "pannello"))
        self.assertEqual((f["SFB_SMTP_HOST"]["value"], f["SFB_SMTP_HOST"]["source"]), ("smtp.alvea.test", "pannello"))
        self.assertNotIn(secret, json.dumps(self.call("GET", "/api/admin/settings", token=self.gestore)[1]), "un segreto non torna mai indietro")
        self.assertEqual(config.get("ANTHROPIC_API_KEY"), secret, "ma il server lo usa")
        self.assertEqual(registration.smtp_cfg()["smtp_host"], "smtp.alvea.test")
        self.assertEqual(self.call("GET", "/api/register")[1]["trial_days"], 30, "vale subito, senza riavviare")
        con = db.connect()
        logged = [r["detail"] for r in con.execute("SELECT detail FROM audit WHERE action='impostazioni_cambiate'")]
        con.close()
        self.assertIn("ANTHROPIC_API_KEY", logged[-1])
        self.assertNotIn(secret, "".join(logged), "nel registro i nomi, non i valori")
        # campo vuoto: si torna al file .env
        os.environ["SFB_SMTP_HOST"] = "smtp.dal-file.test"
        self.addCleanup(lambda: os.environ.__setitem__("SFB_SMTP_HOST", ""))
        self.assertEqual(self.save({"SFB_SMTP_HOST": "", "ANTHROPIC_API_KEY": ""})[0], 200)
        f = self.fields()
        self.assertEqual((f["SFB_SMTP_HOST"]["value"], f["SFB_SMTP_HOST"]["source"]), ("smtp.dal-file.test", ".env"))
        self.assertFalse(f["ANTHROPIC_API_KEY"]["source"] == "pannello")
        # riavvio: le impostazioni salvate tornano in uso
        config.OVERRIDES.clear()
        self.assertEqual(config.get_int("SFB_TRIAL_DAYS", 0), 0)
        con = db.connect()
        settings.load(con)
        con.close()
        self.assertEqual(config.get_int("SFB_TRIAL_DAYS", 0), 30)

    def test_segreti_dal_pannello_solo_con_la_cifratura(self):
        os.environ["SFB_ALLOW_PLAINTEXT_TOKENS"] = "0"
        self.addCleanup(lambda: os.environ.__setitem__("SFB_ALLOW_PLAINTEXT_TOKENS", "1"))
        status, out = self.save({"OPENAI_API_KEY": "sk-prova"})
        self.assertEqual(status, 400, out)
        self.assertIn("SFB_SECRET_KEY", out["error"])
        self.assertEqual(self.save({"OPENAI_MODEL": "gpt-prova"})[0], 200, "i valori non segreti si salvano comunque")
        self.assertFalse(self.call("GET", "/api/admin/settings", token=self.gestore)[1]["encrypted"])

    def test_email_di_prova(self):
        self.assertEqual(self.call("POST", "/api/admin/settings/test-email", {}, self.gestore)[0], 400, "prima va salvato il server")
        sent, real = [], registration.send_mail
        registration.send_mail = lambda to, subject, text: sent.append(to)
        self.addCleanup(lambda: setattr(registration, "send_mail", real))
        self.assertEqual(self.save({"SFB_SMTP_HOST": "smtp.alvea.test"})[0], 200)
        status, out = self.call("POST", "/api/admin/settings/test-email", {}, self.gestore)
        self.assertEqual((status, out["to"], sent), (200, "gestore@test.it", ["gestore@test.it"]))

    # ---------- pagamenti ----------
    def stripe_on(self):
        self.assertEqual(self.save({"STRIPE_SECRET_KEY": "sk_test_prova", "STRIPE_WEBHOOK_SECRET": "whsec_prova", "STRIPE_PRICE_COMPLETO": "price_completo"})[0], 200)

    def test_pagamento_con_carta_dal_pannello_alla_scadenza(self):
        a, tid = self.new_tenant("Paga Uno", "completo")
        b, tid_b = self.new_tenant("Paga Due", "completo")
        s, _ = self.new_tenant("Paga Start", "start")
        self.assertFalse(self.call("GET", "/api/billing", token=a)[1]["enabled"])
        self.assertEqual(self.call("POST", "/api/billing/checkout", {}, a)[0], 400, "senza chiavi il pagamento non parte")
        self.stripe_on()
        self.assertTrue(self.call("GET", "/api/billing", token=a)[1]["enabled"])
        self.assertFalse(self.call("GET", "/api/billing", token=s)[1]["enabled"], "il pacchetto Start non ha un prezzo impostato")
        status, out = self.call("POST", "/api/billing/checkout", {}, a)
        self.assertEqual(status, 200, out)
        self.assertTrue(out["url"].endswith("/paga/cs_test_1"))
        sent = STRIPE["posts"][-1]
        self.assertEqual((sent["path"], sent["auth"]), ("/v1/checkout/sessions", "Bearer sk_test_prova"))
        self.assertEqual((sent["form"]["mode"], sent["form"]["line_items[0][price]"], sent["form"]["client_reference_id"]), ("subscription", "price_completo", str(tid)))
        self.assertNotIn("sk_test_prova", json.dumps(out))
        # Stripe avvisa: pagamento completato, poi fattura pagata fino a una certa data
        end = int(datetime(2031, 3, 15, 12, tzinfo=timezone.utc).timestamp())
        done = {"id": "evt_1", "type": "checkout.session.completed", "data": {"object": {"client_reference_id": str(tid), "customer": "cus_A", "subscription": "sub_A"}}}
        paid = {"id": "evt_2", "type": "invoice.paid", "data": {"object": {"customer": "cus_A", "amount_paid": 9900, "currency": "eur",
                                                                             "lines": {"data": [{"period": {"start": end - 2592000, "end": end}}]}}}}
        self.assertEqual(self.hook(done)[0], 200)
        self.assertEqual(self.hook(paid)[0], 200)
        sub = lambda t: self.call("GET", "/api/me", token=t)[1]["tenant"]["subscription"]["paid_until"]
        self.assertEqual(sub(a), "2031-03-15")
        self.assertEqual(sub(b), "", "l'altra azienda non è toccata")
        self.assertTrue(self.call("GET", "/api/billing", token=a)[1]["subscribed"])
        # lo stesso avviso rimandato non conta due volte; un avviso vecchio non accorcia la scadenza
        self.assertTrue(json.loads(self.hook(paid)[2])["duplicate"])
        older = dict(paid, id="evt_3")
        older["data"] = {"object": dict(paid["data"]["object"], lines={"data": [{"period": {"end": end - 5184000}}]})}
        self.assertEqual(self.hook(older)[0], 200)
        self.assertEqual(sub(a), "2031-03-15")
        con = db.connect()
        self.assertEqual(con.execute("SELECT COUNT(*) FROM news WHERE tenant_id=? AND title='Pagamento ricevuto'", (tid,)).fetchone()[0], 2)
        con.close()
        # avvisi senza firma buona non fanno niente
        forged = {"id": "evt_falso", "type": "invoice.paid", "data": {"object": {"customer": "cus_A", "lines": {"data": [{"period": {"end": end + 31536000}}]}}}}
        self.assertEqual(self.hook(forged, secret="un-altro-segreto")[0], 400)
        self.assertEqual(self.hook(forged, stamp=time.time() - 3600)[0], 400, "firma giusta ma vecchia di un'ora")
        self.assertEqual(self.hook(forged, sig="00" * 32)[0], 400)
        self.assertEqual(self.send_raw("/api/billing/webhook", json.dumps(forged).encode(), {"Content-Type": "application/json"})[0], 400)
        self.assertEqual(sub(a), "2031-03-15")
        unknown = {"id": "evt_4", "type": "invoice.paid", "data": {"object": {"customer": "cus_sconosciuto", "lines": {"data": [{"period": {"end": end}}]}}}}
        self.assertTrue(json.loads(self.hook(unknown)[2])["ignored"])
        self.assertEqual(sub(b), "")
        self.assertEqual(self.hook({"id": "evt_5", "type": "customer.subscription.deleted", "data": {"object": {"id": "sub_A", "customer": "cus_A"}}})[0], 200)
        self.assertFalse(self.call("GET", "/api/billing", token=a)[1]["subscribed"])
        self.assertEqual(sub(a), "2031-03-15", "disdetta: resta attivo fino a dove ha pagato")

    def test_solo_un_amministratore_paga_e_gli_errori_di_stripe_si_leggono(self):
        a, tid = self.new_tenant("Paga Tre", "completo")
        self.stripe_on()
        con = db.connect()
        from server import ops
        from base import PW
        ops.create_user(con, tid, "operatore@paga-tre.it", "Operatore", "member", PW)
        con.close()
        self.assertEqual(self.call("POST", "/api/billing/checkout", {}, self.login("operatore@paga-tre.it"))[0], 403)
        STRIPE["status"] = 400
        status, out = self.call("POST", "/api/billing/checkout", {}, a)
        self.assertEqual(status, 502, out)
        self.assertIn("Stripe", out["error"])
        self.assertEqual(self.save({"STRIPE_WEBHOOK_SECRET": ""})[0], 200)
        self.assertEqual(self.hook({"id": "evt_9", "type": "invoice.paid", "data": {"object": {}}})[0], 503, "senza segreto gli avvisi si rifiutano")

    def test_firma_di_stripe(self):
        raw, now = b'{"id":"evt"}', 1700000000
        good = hmac.new(b"whsec_x", b"1700000000." + raw, hashlib.sha256).hexdigest()
        self.assertTrue(billing.verify_signature(raw, "t=1700000000,v1=" + good, "whsec_x", now))
        self.assertTrue(billing.verify_signature(raw, "t=1700000000,v1=" + "0" * 64 + ",v1=" + good, "whsec_x", now), "durante un cambio di segreto arrivano due firme")
        self.assertTrue(billing.verify_signature(raw, "t=1700000000,v0=vecchia,v1=" + good, "whsec_x", now + 299))
        for header, secret, at, body in (("t=1700000000,v1=" + good, "whsec_x", now + 301, raw), ("t=1700000000,v1=" + good, "whsec_y", now, raw),
                                         ("t=1700000000,v1=" + good, "whsec_x", now, raw + b" "), ("v1=" + good, "whsec_x", now, raw),
                                         ("t=1700000000", "whsec_x", now, raw), ("", "whsec_x", now, raw), ("t=1700000000,v1=" + good, "", now, raw)):
            self.assertFalse(billing.verify_signature(body, header, secret, at), header)


if __name__ == "__main__":
    unittest.main()
