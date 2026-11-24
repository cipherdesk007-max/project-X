"""Prove della versione 23: accesso in due passaggi con il codice di un'app (TOTP, RFC 6238)."""
import argparse
import base64
import json
import os
import re
import unittest
import zipfile

from base import Base, PW

import manage
from server import auth, db, ops, tenantdata, twofactor


class Clock:
    """Orologio finto per i codici: le prove decidono che ora è, senza aspettare 30 secondi veri."""

    def __init__(self, now):
        self.now = now

    def time(self):
        return self.now


class DuePassaggi(Base):
    def setUp(self):
        auth.reset_fails()
        self.clock = Clock(1900000000.0)
        real = twofactor.time
        twofactor.time = self.clock
        self.addCleanup(lambda: setattr(twofactor, "time", real))

    def code(self, secret, shift=0):
        return twofactor.code_at(secret, int(self.clock.now // 30) + shift)

    def later(self, seconds=30):
        self.clock.now += seconds

    def enter(self, email, code=None, password=PW):
        body = {"email": email, "password": password}
        if code is not None:
            body["code"] = code
        return self.call("POST", "/api/login", body)

    def activate(self, name, package="start"):
        """Azienda nuova con i due passaggi attivi. Ritorna (gettone, email, chiave, codici di riserva)."""
        token, _ = self.new_tenant(name, package)
        email = "admin@%s.it" % name.lower().replace(" ", "-")
        secret = self.call("POST", "/api/2fa/setup", {"password": PW}, token)[1]["secret"]
        status, out = self.call("POST", "/api/2fa/enable", {"code": self.code(secret)}, token)
        self.assertEqual(status, 200, out)
        self.later()
        return token, email, secret, out["recovery"]

    def test_i_codici_sono_quelli_dello_standard(self):
        """I valori di prova pubblicati nell'RFC 6238: se tornano questi, torna anche quello che mostra Google Authenticator."""
        secret = base64.b32encode(b"12345678901234567890").decode()
        for moment, expected in ((59, "287082"), (1111111109, "081804"), (1111111111, "050471"), (1234567890, "005924"), (2000000000, "279037"), (20000000000, "353130")):
            self.assertEqual(twofactor.code_at(secret, moment // 30), expected, moment)
        self.assertEqual(len(base64.b32decode(twofactor.new_secret())), 20, "chiave di 160 bit, come raccomandato")
        self.assertNotEqual(twofactor.new_secret(), twofactor.new_secret())
        link = twofactor.uri("ABCDEFGH", "capo+prova@esempio.it")
        self.assertEqual(link, "otpauth://totp/Alvea%3Acapo%2Bprova%40esempio.it?secret=ABCDEFGH&issuer=Alvea&algorithm=SHA1&digits=6&period=30")

    def test_attivazione_e_accesso_con_il_codice(self):
        token, _ = self.new_tenant("Passaggi Uno", "start")
        email = "admin@passaggi-uno.it"
        other = self.login(email)
        self.assertEqual(self.call("GET", "/api/2fa", token=token)[1], {"on": False, "recovery_left": 0})
        self.assertEqual(self.call("POST", "/api/2fa/setup", {"password": "sbagliata"}, token)[0], 403, "senza la password non si comincia")
        self.assertEqual(self.call("POST", "/api/2fa/enable", {"code": "123456"}, token)[0], 400, "senza chiave non si conferma niente")
        status, setup = self.call("POST", "/api/2fa/setup", {"password": PW}, token)
        self.assertEqual(status, 200, setup)
        self.assertRegex(setup["secret"], r"^[A-Z2-7]{32}$")
        self.assertIn("secret=%s&issuer=Alvea" % setup["secret"], setup["uri"])
        self.assertEqual(self.enter(email)[0], 200, "finché non è confermato non cambia niente: nessuno resta chiuso fuori a metà")
        for wrong in ("000000", "12345", "abcdef", "", None, self.code(setup["secret"], 5)):
            auth.reset_fails()
            self.assertEqual(self.call("POST", "/api/2fa/enable", {"code": wrong}, token)[0], 400, wrong)
        auth.reset_fails()
        status, out = self.call("POST", "/api/2fa/enable", {"code": " %s " % self.code(setup["secret"])}, token)
        self.assertEqual(status, 200, out)
        self.assertEqual(len(out["recovery"]), 8)
        self.assertTrue(all(re.fullmatch(r"[a-z2-9]{4}-[a-z2-9]{4}", c) for c in out["recovery"]) and len(set(out["recovery"])) == 8)
        self.assertEqual(self.call("GET", "/api/me", token=token)[1]["user"]["two_factor"], True, "chi lo ha attivato resta dentro")
        self.assertEqual(self.call("GET", "/api/me", token=other)[0], 401, "gli altri accessi aperti devono rientrare")
        self.assertEqual(self.call("GET", "/api/2fa", token=token)[1], {"on": True, "recovery_left": 8})
        self.assertEqual(self.call("POST", "/api/2fa/setup", {"password": PW}, token)[0], 409, "una chiave nuova non può sostituire quella attiva")
        status, out = self.enter(email)
        self.assertEqual((status, out.get("two_factor"), "token" in out), (401, True, False), "password giusta ma senza codice: non si entra")
        self.assertEqual(self.enter(email, self.code(setup["secret"]))[0], 401, "il codice usato per attivare non vale una seconda volta")
        self.later()
        status, out = self.enter(email, self.code(setup["secret"]))
        self.assertEqual(status, 200, out)
        self.assertEqual(self.call("GET", "/api/me", token=out["token"])[0], 200)
        self.assertEqual(self.enter(email, self.code(setup["secret"]))[0], 401, "lo stesso codice, letto alle spalle, non fa entrare un altro")
        self.later()
        self.assertEqual(self.enter(email, self.code(setup["secret"], -1))[0], 401, "nemmeno tornando indietro a un codice già passato")
        self.assertEqual(self.enter(email, self.code(setup["secret"], 1))[0], 200, "un telefono avanti di mezzo minuto entra lo stesso")
        self.later(120)
        self.assertEqual(self.enter(email, self.code(setup["secret"], -3))[0], 401, "un codice di un minuto e mezzo fa non vale più")
        self.assertEqual(self.enter(email, self.code(setup["secret"]), password="sbagliata")[0], 401, "il codice giusto con la password sbagliata non basta")
        con = db.connect()
        self.addCleanup(con.close)
        actions = [r[0] for r in con.execute("SELECT action FROM audit WHERE action LIKE 'due_passaggi%'")]
        self.assertIn("due_passaggi_attivati", actions)

    def test_codici_di_riserva_una_volta_sola(self):
        token, email, secret, recovery = self.activate("Passaggi Due")
        first = recovery[0]
        self.assertEqual(self.enter(email, first.upper().replace("-", " "))[0], 200, "scritto in maiuscolo e con lo spazio va bene lo stesso")
        self.assertEqual(self.enter(email, first)[0], 401, "vale una volta sola")
        self.assertEqual(self.call("GET", "/api/2fa", token=token)[1]["recovery_left"], 7)
        for wrong in ("aaaa-aaaa", first[:-1], first + "x", "-", recovery[1][:4]):
            auth.reset_fails()
            self.assertEqual(self.enter(email, wrong)[0], 401, wrong)
        auth.reset_fails()
        self.assertEqual(self.enter(email, recovery[1])[0], 200)
        con = db.connect()
        self.addCleanup(con.close)
        stored = con.execute("SELECT totp_codes FROM users WHERE email=?", (email,)).fetchone()[0]
        for code in recovery:
            self.assertNotIn(code.replace("-", ""), stored, "nel database c'è solo l'impronta dei codici")

    def test_il_codice_non_si_indovina_a_tentativi(self):
        token, email, secret, _ = self.activate("Passaggi Tre")
        self.assertEqual([self.enter(email)[0] for _ in range(8)], [401] * 8, "chiedere il codice non è un errore: non ferma nessuno")
        codes = [self.enter(email, "%06d" % n)[0] for n in range(100000, 100007)]
        self.assertEqual(codes, [401] * 5 + [429] * 2, "cinque codici sbagliati e l'accesso è fermo per qualche minuto")
        self.assertEqual(self.enter(email, self.code(secret))[0], 429, "fermo anche con il codice giusto: provare a caso non conviene")
        auth.reset_fails()
        self.assertEqual(self.enter(email, self.code(secret))[0], 200)
        u = dict(db.connect().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone())
        self.later()
        con = db.connect()
        self.addCleanup(con.close)
        self.assertEqual((twofactor._accept(con, u, self.code(secret)), twofactor._accept(con, u, self.code(secret))), ("app", None), "due accessi nello stesso istante con lo stesso codice: ne passa uno")

    def test_una_sessione_rubata_non_basta_per_toglierli_o_metterli(self):
        token, email, secret, recovery = self.activate("Passaggi Quattro")
        self.later()
        for body in ({}, {"password": PW}, {"code": self.code(secret)}, {"password": "sbagliata", "code": self.code(secret)}, {"password": PW, "code": "000000"}):
            self.assertEqual(self.call("POST", "/api/2fa/disable", body, token)[0], 403, body)
            auth.reset_fails()
        self.assertEqual(self.call("GET", "/api/2fa", token=token)[1]["on"], True)
        victim, _ = self.new_tenant("Passaggi Cinque", "start")
        self.assertEqual([self.call("POST", "/api/2fa/setup", {"password": "tentativo-%d" % i}, victim)[0] for i in range(7)], [403] * 5 + [429] * 2,
                         "con la sessione di un altro non si indovina la sua password da qui")
        auth.reset_fails()
        self.assertEqual(self.call("POST", "/api/2fa/disable", {"password": PW, "code": self.code(secret)}, token)[0], 200)
        self.assertEqual(self.enter(email)[0], 200, "tolti i due passaggi si entra con la sola password")
        self.assertEqual(self.call("POST", "/api/2fa/disable", {"password": PW, "code": "1"}, token)[0], 409)
        token2, email2, secret2, recovery2 = self.activate("Passaggi Sei")
        self.assertEqual(self.call("POST", "/api/2fa/disable", {"password": PW, "code": recovery2[3]}, token2)[0], 200, "anche con un codice di riserva")

    def test_cambiare_la_password_non_scavalca_il_codice(self):
        token, email, secret, _ = self.activate("Passaggi Sette")
        con = db.connect()
        self.addCleanup(con.close)
        row = con.execute("SELECT id, tenant_id FROM users WHERE email=?", (email,)).fetchone()
        con.execute("UPDATE users SET pw_hash=? WHERE id=?", (auth.hash_password("password-cambiata-789"), row["id"]))
        status, out = self.enter(email, password="password-cambiata-789")
        self.assertEqual((status, out.get("two_factor")), (401, True), "chi ha preso la posta e cambiato la password trova ancora il codice")
        self.assertEqual(self.enter(email, self.code(secret), password="password-cambiata-789")[0], 200)

    def test_la_chiave_e_cifrata_e_non_esce_mai(self):
        try:
            from cryptography.fernet import Fernet
        except Exception:
            self.skipTest("senza il modulo cryptography la chiave resta in chiaro, come le altre credenziali")
        os.environ["SFB_SECRET_KEY"] = Fernet.generate_key().decode()
        self.addCleanup(lambda: os.environ.__setitem__("SFB_SECRET_KEY", ""))
        token, email, secret, recovery = self.activate("Passaggi Otto")
        con = db.connect()
        self.addCleanup(con.close)
        row = dict(con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone())
        self.assertTrue(row["totp_secret"].startswith("f:") and secret not in row["totp_secret"], "nel database la chiave è cifrata")
        self.assertEqual(self.enter(email, self.code(secret))[0], 200)
        for path in ("/api/me", "/api/2fa", "/api/users"):
            blob = json.dumps(self.call("GET", path, token=token)[1])
            self.assertNotIn(secret, blob, path)
            self.assertNotIn("totp_secret", blob, path)
            self.assertNotIn("totp_codes", blob, path)
        path = os.path.join(self.enterContext(__import__("tempfile").TemporaryDirectory()), "esportazione.zip")
        tenantdata.export(con, tenantdata.find(con, "Passaggi Otto"), path)
        with zipfile.ZipFile(path) as z:
            users = json.loads(z.read("dati/users.json"))
            blob = b"".join(z.read(n) for n in z.namelist())
        self.assertFalse([k for k in users[0] if k in ("totp_secret", "totp_codes", "totp_last", "pw_hash")], "né chiave né codici nell'esportazione")
        for code in recovery:
            self.assertNotIn(code.replace("-", "").encode(), blob)
        self.assertNotIn(row["totp_secret"].encode(), blob)
        os.environ["SFB_SECRET_KEY"] = Fernet.generate_key().decode()
        status, out = self.enter(email, "123456")
        self.assertEqual(status, 500, "con la chiave di cifratura cambiata l'errore dice cosa fare, non lascia entrare")
        self.assertIn("togli-due-passaggi", out["error"])

    def test_il_gestore_e_il_comando_di_emergenza(self):
        advice = lambda: [(s, t) for s, t in manage.production_report() if "due passaggi" in t]
        self.assertEqual(advice()[0][0], "CONSIGLIO", "finché il gestore entra con la sola password viene ricordato")
        g = self.login("gestore@test.it")
        secret = self.call("POST", "/api/2fa/setup", {"password": PW}, g)[1]["secret"]
        self.assertEqual(self.call("POST", "/api/2fa/enable", {"code": self.code(secret)}, g)[0], 200)
        self.assertEqual(advice()[0][0], "OK")
        self.later()
        self.assertEqual(self.enter("gestore@test.it")[0], 401)
        self.assertEqual(self.enter("gestore@test.it", self.code(secret))[0], 200)
        self.assertEqual(manage.cmd_togli_due_passaggi(argparse.Namespace(email="nessuno@esempio.it")), 1)
        self.assertEqual(manage.cmd_togli_due_passaggi(argparse.Namespace(email="Gestore@test.it ")), 0)
        self.assertEqual(self.call("GET", "/api/me", token=g)[0], 401, "dopo l'emergenza gli accessi aperti cadono")
        g = self.login("gestore@test.it")
        self.assertEqual(self.call("GET", "/api/2fa", token=g)[1]["on"], False)
        con = db.connect()
        self.addCleanup(con.close)
        last = [r[0] for r in con.execute("SELECT action FROM audit WHERE tenant_id=0 ORDER BY id DESC LIMIT 3")]
        self.assertIn("due_passaggi_tolti_dal_server", last, "resta scritto nel registro della piattaforma")


if __name__ == "__main__":
    unittest.main()
