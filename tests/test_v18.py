"""Prove della versione 24: registrazione anche per chi è da solo, primi passi per chi è appena arrivato,
applicazione sulla schermata del telefono."""
import json
import os
import unittest

from base import Base, PW, ROOT

from server import app, auth, db, ops, registration, twofactor


class PerChiArriva(Base):
    def setUp(self):
        registration.reset_throttle()
        auth.reset_fails()

    def register(self, **over):
        body = {"name": "Mario Rossi", "email": "mario@solo.it", "password": PW, "agents": ["email", "contenuti"], "accept": True}
        body.update(over)
        return self.call("POST", "/api/register", body)

    def test_ci_si_registra_anche_da_soli_senza_inventare_un_azienda(self):
        status, out = self.register(kind="persona")
        self.assertEqual(status, 200, out)
        me = self.call("GET", "/api/me", token=out["token"])[1]
        self.assertEqual((me["tenant"]["kind"], me["tenant"]["name"], me["user"]["role"]), ("persona", "Mario Rossi", "admin"))
        status, second = self.register(kind="persona", email="mario2@solo.it")
        self.assertEqual(status, 200, "due persone con lo stesso nome non si pestano i piedi")
        other = self.call("GET", "/api/me", token=second["token"])[1]["tenant"]
        self.assertTrue(other["name"].startswith("Mario Rossi") and other["id"] != me["tenant"]["id"])
        self.assertEqual(self.chat(out["token"], "aggiungi lead Cliente Solo cliente@esempio.it", "centrale")[0], 200)
        self.assertEqual(self.call("GET", "/api/data/leads", token=second["token"])[1]["rows"], [], "ognuno ha il suo spazio separato")
        status, err = self.register(email="azienda@solo.it")
        self.assertEqual(status, 400, "per un'azienda il nome resta obbligatorio")
        self.assertIn("azienda", err["error"])
        status, firm = self.register(email="azienda@solo.it", company="Rossi Srl", kind="qualcos'altro")
        self.assertEqual((status, self.call("GET", "/api/me", token=firm["token"])[1]["tenant"]["kind"]), (200, "azienda"), "un valore strano vale come azienda")
        status, named = self.register(email="studio@solo.it", company="Studio Rossi", kind="persona")
        self.assertEqual(self.call("GET", "/api/me", token=named["token"])[1]["tenant"]["name"], "Studio Rossi", "chi è da solo ma ha un nome d'attività può usarlo")
        self.assertEqual(self.register(email="vuoto@solo.it", kind="persona", name="  ")[0], 400)
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        for piece in ('"Solo per me"', '"Per la mia azienda"', 'kind: S.regKind || "azienda"', '"Crea il mio spazio"', '"Spazio personale"'):
            self.assertIn(piece, js, piece)
        self.assertIn("per chi lavora da solo", self.fetch("/")[2].decode("utf-8"))

    def test_primi_passi_con_quello_che_e_stato_fatto_davvero(self):
        os.environ["SFB_TRIAL_DAYS"] = "14"
        self.addCleanup(lambda: os.environ.__setitem__("SFB_TRIAL_DAYS", "0"))
        token = self.register(kind="persona", email="passi@solo.it")[1]["token"]
        steps = lambda t=None: {s["id"]: s for s in self.call("GET", "/api/summary", token=t or token)[1]["first_steps"]}
        s = steps()
        self.assertEqual(list(s), ["modello", "messaggio", "agenti", "collegamento", "sicurezza", "abbonamento"])
        self.assertEqual({k: v["done"] for k, v in s.items()}, {"modello": True, "messaggio": False, "agenti": True, "collegamento": False, "sicurezza": False, "abbonamento": False})
        self.assertIn("14 giorni", s["abbonamento"]["text"])
        self.assertEqual({k: v["tab"] for k, v in s.items()}, {"modello": "collegamenti", "messaggio": "", "agenti": "agenti", "collegamento": "collegamenti", "sicurezza": "sicurezza", "abbonamento": "abbonamento"})
        self.chat(token, "Ciao", "centrale")
        self.assertTrue(steps()["messaggio"]["done"])
        self.assertEqual(self.call("PUT", "/api/connectors/telegram", {"bot_token": "123456:prova"}, token)[0], 200)
        self.assertTrue(steps()["collegamento"]["done"])
        secret = self.call("POST", "/api/2fa/setup", {"password": PW}, token)[1]["secret"]
        import time
        self.assertEqual(self.call("POST", "/api/2fa/enable", {"code": twofactor.code_at(secret, int(time.time() // 30))}, token)[0], 200)
        self.assertTrue(steps()["sicurezza"]["done"])
        # un'azienda creata dal gestore, senza prova: niente passo dell'abbonamento, e gli agenti sono ancora da scegliere
        admin, tid = self.new_tenant("Passi Due", "start")
        s = steps(admin)
        self.assertEqual((list(s), s["agenti"]["done"]), (["modello", "messaggio", "agenti", "collegamento", "sicurezza"], False))
        self.assertEqual(self.call("POST", "/api/tenant/agents", {"agent_id": "email", "is_active": False}, admin)[0], 200)
        self.assertTrue(steps(admin)["agenti"]["done"])
        con = db.connect()
        ops.create_user(con, tid, "operatore@passi-due.it", "Operatore", "member", PW)
        con.close()
        self.assertNotIn("first_steps", self.call("GET", "/api/summary", token=self.login("operatore@passi-due.it"))[1], "i primi passi sono per chi amministra")
        real = app._models
        app._models = lambda q, keys=None: [{"id": "kimi", "name": "Kimi", "configured": False}]
        try:
            s = steps(admin)["modello"]
            self.assertEqual((s["done"], "chiave" in s["hint"]), (False, True), "senza un modello pronto lo dice per primo, con cosa fare")
        finally:
            app._models = real

    def test_l_applicazione_si_mette_sulla_schermata_del_telefono(self):
        status, heads, body = self.fetch("/manifest.webmanifest")
        manifest = json.loads(body)
        self.assertEqual((status, heads["Content-Type"]), (200, "application/manifest+json"))
        self.assertEqual((manifest["name"], manifest["start_url"], manifest["display"], manifest["theme_color"]), ("Alvea", "/app", "standalone", "#0b0f14"))
        for icon in manifest["icons"] + [{"src": "/icon-180.png", "sizes": "180x180"}]:
            status, heads, data = self.fetch(icon["src"])
            self.assertEqual((status, heads["Content-Type"], data[:8]), (200, "image/png", b"\x89PNG\r\n\x1a\n"), icon["src"])
            width = int.from_bytes(data[16:20], "big")
            self.assertEqual("%dx%d" % (width, int.from_bytes(data[20:24], "big")), icon["sizes"], "l'icona è grande quanto dichiara")
        page = self.fetch("/app")[2].decode("utf-8")
        for piece in ('<link rel="manifest" href="/manifest.webmanifest">', '<link rel="apple-touch-icon" href="/icon-180.png">', 'name="apple-mobile-web-app-capable" content="yes"', 'name="theme-color" content="#0b0f14"'):
            self.assertIn(piece, page, piece)
        self.assertNotIn("http", " ".join(i["src"] for i in manifest["icons"]), "tutto dallo stesso server, come vogliono le regole di sicurezza")


if __name__ == "__main__":
    unittest.main()
