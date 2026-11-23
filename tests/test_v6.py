"""Prove della versione 6: recupero della password via email e tetto mensile di consumo per azienda."""
import os
import re
import time
import unittest

from base import PW, Base
from server import db, registration


class Recupero(Base):
    def setUp(self):
        registration.reset_throttle()
        self.sent = []
        real = registration.send_mail
        registration.send_mail = lambda to, subject, text: self.sent.append({"to": to, "subject": subject, "text": text})
        os.environ["SFB_SMTP_HOST"] = "smtp.esempio.test"
        self.addCleanup(lambda: (setattr(registration, "send_mail", real), os.environ.__setitem__("SFB_SMTP_HOST", "")))

    def mail(self, count=1):
        end = time.time() + 3
        while len(self.sent) < count and time.time() < end:  # l'email parte da un filo a parte
            time.sleep(0.02)
        return self.sent

    def token(self):
        return re.search(r"#reimposta-([A-Za-z0-9_-]+)", self.mail()[-1]["text"]).group(1)

    def forgot(self, email):
        return self.call("POST", "/api/password/forgot", {"email": email})

    def reset(self, token, password):
        return self.call("POST", "/api/password/reset", {"token": token, "password": password})

    def test_percorso_completo_e_collegamento_usa_e_getta(self):
        old, _ = self.new_tenant("Recupero Uno", "start")
        email = "admin@recupero-uno.it"
        self.assertTrue(self.call("GET", "/api/register")[1]["password_recovery"])
        self.assertEqual(self.forgot("  ADMIN@Recupero-Uno.it ")[0], 200, "maiuscole e spazi non contano")
        self.assertEqual([m["to"] for m in self.mail()], [email])
        tok = self.token()
        self.assertEqual(self.reset(tok, "corta")[0], 400)
        self.assertEqual(self.call("POST", "/api/login", {"email": email, "password": PW})[0], 200, "password corta rifiutata: resta la vecchia")
        new = "una-password-nuova-e-lunga"
        self.assertEqual(self.reset(tok, new)[0], 200, "dopo un rifiuto per password corta il collegamento vale ancora")
        self.assertEqual(self.call("POST", "/api/login", {"email": email, "password": PW})[0], 401, "la vecchia password non vale più")
        self.assertEqual(self.call("GET", "/api/me", token=old)[0], 401, "chi era entrato con la vecchia password deve rientrare")
        self.assertEqual(self.call("GET", "/api/me", token=self.login(email, new))[0], 200)
        self.assertEqual(self.reset(tok, "un-altra-password-lunga")[0], 400, "il collegamento vale una volta sola")
        self.assertEqual(self.call("POST", "/api/login", {"email": email, "password": new})[0], 200)
        con = db.connect()
        rows = [dict(r) for r in con.execute("SELECT * FROM email_tokens")]
        actions = [r["action"] for r in con.execute("SELECT action FROM audit WHERE action LIKE 'password%'")]
        con.close()
        self.assertNotIn(tok, str(rows), "sul server resta solo l'impronta del collegamento")
        self.assertIn("password_recupero_chiesto", actions)
        self.assertIn("password_reimpostata", actions)

    def test_la_risposta_non_rivela_chi_e_cliente(self):
        self.new_tenant("Recupero Due", "start")
        known, unknown = self.forgot("admin@recupero-due.it"), self.forgot("nessuno@altrove.it")
        self.assertEqual(known, unknown, "stessa risposta, che l'indirizzo esista o no")
        self.assertEqual(len(self.mail()), 1)
        time.sleep(0.3)
        self.assertEqual([m["to"] for m in self.sent], ["admin@recupero-due.it"], "nessuna email a indirizzi sconosciuti")

    def test_collegamenti_scaduti_sbagliati_o_di_altro_tipo(self):
        _, tid = self.new_tenant("Recupero Tre", "start")
        email = "admin@recupero-tre.it"
        self.assertEqual(self.reset("x" * 43, "una-password-nuova-e-lunga")[0], 400)
        con = db.connect()  # un collegamento nato per confermare l'indirizzo non deve poter cambiare la password
        uid = con.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"]
        con.execute("INSERT INTO email_tokens(token_hash,user_id,expires_at,kind) VALUES(?,?,?,'verifica')",
                    (registration._hash("collegamento-di-verifica-" + "v" * 24), uid, int(time.time()) + 600))
        con.close()
        self.assertEqual(self.reset("collegamento-di-verifica-" + "v" * 24, "una-password-nuova-e-lunga")[0], 400)
        self.assertEqual(self.call("POST", "/api/login", {"email": email, "password": PW})[0], 200)
        self.forgot(email)
        first = self.token()
        self.forgot(email)
        self.mail(2)
        second = self.token()
        self.assertEqual(self.reset(first, "una-password-nuova-e-lunga")[0], 400, "un collegamento nuovo annulla il precedente")
        con = db.connect()
        self.assertFalse(registration.verify(con, second), "un collegamento per la password non vale come verifica dell'indirizzo")
        self.assertEqual(con.execute("SELECT COUNT(*) FROM email_tokens WHERE kind='password'").fetchone()[0], 1,
                         "un collegamento per la password non si consuma come verifica dell'indirizzo")
        con.execute("UPDATE email_tokens SET expires_at=1 WHERE kind='password'")
        con.close()
        self.assertEqual(self.reset(second, "una-password-nuova-e-lunga")[0], 400, "scaduto")
        self.assertEqual(self.call("POST", "/api/login", {"email": email, "password": PW})[0], 200)
        # azienda sospesa: niente email
        con = db.connect()
        con.execute("UPDATE tenants SET active=0 WHERE id=?", (tid,))
        con.close()
        before = len(self.sent)
        self.assertEqual(self.forgot(email)[0], 200)
        time.sleep(0.3)
        self.assertEqual(len(self.sent), before)

    def test_limite_di_richieste_e_server_senza_posta(self):
        for _ in range(5):
            self.assertEqual(self.forgot("qualcuno@esempio.it")[0], 200)
        self.assertEqual(self.forgot("qualcuno@esempio.it")[0], 429, "cinque richieste all'ora per indirizzo di rete")
        registration.reset_throttle()
        os.environ["SFB_SMTP_HOST"] = ""
        status, out = self.forgot("qualcuno@esempio.it")
        self.assertEqual(status, 400)
        self.assertIn("amministratore", out["error"])
        self.assertFalse(self.call("GET", "/api/register")[1]["password_recovery"])


class Tetto(Base):
    def cap(self, gestore, tid, value):
        return self.call("POST", "/api/admin/tenants/%d" % tid, {"monthly_token_cap": value}, gestore)

    def test_tetto_mensile_ferma_solo_quella_azienda_e_solo_i_modelli_a_pagamento(self):
        gestore = self.login("gestore@test.it")
        a, tid = self.new_tenant("Tetto Uno", "completo")
        b, _ = self.new_tenant("Tetto Due", "completo")
        self.assertEqual(self.cap(a, tid, 5)[0], 403, "solo il gestore decide il tetto")
        for bad in (-1, "tanto", 1.5, True):
            self.assertEqual(self.cap(gestore, tid, bad)[0], 400, repr(bad))
        self.assertEqual(self.chat(a, "ciao")[0], 200)
        used = self.call("GET", "/api/usage", token=a)[1]["tokens_month"]
        self.assertGreater(used, 0)
        self.assertEqual(self.cap(gestore, tid, used)[0], 200)
        status, out = self.chat(a, "ancora")
        self.assertEqual(status, 402, out)
        self.assertIn("Tetto di consumo", out["error"])
        self.assertEqual(self.call("GET", "/api/usage", token=a)[1]["tokens_month"], used, "una richiesta fermata non consuma")
        self.assertEqual(self.chat(a, "ciao", model="locale")[0], 200, "il modello locale non costa: resta disponibile")
        self.assertEqual(self.call("GET", "/api/usage", token=a)[1]["tokens_month"], used, "e non entra nel conto")
        self.assertEqual(self.chat(b, "ciao")[0], 200, "l'altra azienda non è toccata")
        mine = [t for t in self.call("GET", "/api/admin/tenants", token=gestore)[1]["tenants"] if t["id"] == tid][0]
        self.assertEqual((mine["monthly_token_cap"], mine["tokens_month"]), (used, used))
        # il mese dopo il conto riparte: i consumi vecchi non contano
        con = db.connect()
        con.execute("UPDATE usage SET created_at='2020-01-15T10:00:00Z' WHERE tenant_id=?", (tid,))
        con.close()
        self.assertEqual(self.chat(a, "mese nuovo")[0], 200)
        self.assertEqual(self.cap(gestore, tid, 0)[0], 200)
        self.assertEqual(self.chat(a, "senza tetto")[0], 200)
        self.assertEqual(self.call("GET", "/api/usage", token=a)[1]["token_cap"], 0)


if __name__ == "__main__":
    unittest.main()
