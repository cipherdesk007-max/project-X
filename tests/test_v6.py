"""Prove delle versioni 6 e 7: recupero della password, tetto mensile di consumo, abbonamento con scadenza e prova gratuita."""
import os
import re
import time
import unittest
from datetime import datetime, timedelta, timezone

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

    def test_avviso_all_80_per_cento_una_volta_sola(self):
        gestore = self.login("gestore@test.it")
        a, tid = self.new_tenant("Tetto Avviso", "completo")
        self.assertEqual(self.chat(a, "ciao")[0], 200)
        one = self.call("GET", "/api/usage", token=a)[1]["tokens_month"]
        self.assertEqual(self.cap(gestore, tid, int(one * 2.2))[0], 200)

        def warnings():
            con = db.connect()
            n = con.execute("SELECT COUNT(*) FROM news WHERE tenant_id=? AND title LIKE 'Consumo all%'", (tid,)).fetchone()[0]
            con.close()
            return n

        self.assertEqual(warnings(), 0, "al 45% nessun avviso")
        self.assertEqual(self.chat(a, "ciao")[0], 200)
        self.assertEqual(warnings(), 1, "superato l'80%: un avviso")
        self.assertEqual(self.chat(a, "ciao")[0], 200)
        self.assertEqual(warnings(), 1, "l'avviso non si ripete a ogni richiesta")
        self.assertEqual(self.chat(a, "ciao")[0], 402)


class Abbonamento(Base):
    def setUp(self):
        registration.reset_throttle()
        self.today = datetime.now(timezone.utc).date()

    def set(self, token, tid, value):
        return self.call("POST", "/api/admin/tenants/%d" % tid, {"paid_until": value}, token)

    def sub(self, token):
        full = self.call("GET", "/api/me", token=token)[1]["tenant"]["subscription"]
        return {k: full[k] for k in ("paid_until", "days_left", "expired")}  # le voci aggiunte dopo si provano in test_v9

    def test_dopo_la_scadenza_gli_agenti_vanno_in_pausa_ma_i_dati_restano(self):
        gestore = self.login("gestore@test.it")
        a, tid = self.new_tenant("Abbonato Uno", "completo")
        b, _ = self.new_tenant("Abbonato Due", "completo")
        self.assertEqual(self.sub(a), {"paid_until": "", "days_left": None, "expired": False}, "senza data non scade mai")
        self.assertEqual(self.set(a, tid, "2030-01-01")[0], 403, "solo il gestore sposta la scadenza")
        for bad in ("domani", "2026-13-40", 5, "01/02/2027"):
            self.assertEqual(self.set(gestore, tid, bad)[0], 400, repr(bad))
        out = self.chat(a, "invia a cliente@esempio.it: buongiorno")[1]
        approval = [e["approval_id"] for e in out["events"] if e.get("approval_id")][0]
        self.assertEqual(self.chat(a, "aggiungi lead Cliente Storico")[0], 200)
        self.assertEqual(self.set(gestore, tid, (self.today - timedelta(days=1)).isoformat())[0], 200)
        status, err = self.chat(a, "ciao")
        self.assertEqual(status, 402, err)
        self.assertIn("Abbonamento scaduto", err["error"])
        self.assertEqual(self.chat(a, "ciao", model="locale")[0], 402, "scaduto vale per ogni modello")
        self.assertEqual(self.call("POST", "/api/approvals/%d/approve" % approval, {}, a)[0], 402, "nemmeno le azioni in attesa partono")
        self.assertEqual((self.sub(a)["expired"], self.sub(a)["days_left"]), (True, -1))
        self.assertEqual(len(self.call("GET", "/api/data/leads", token=a)[1]["rows"]), 1, "i dati restano leggibili")
        self.assertEqual(self.chat(b, "ciao")[0], 200, "l'altra azienda non è toccata")
        self.assertFalse(self.sub(b)["expired"])
        self.assertEqual(self.set(gestore, tid, self.today.isoformat())[0], 200)
        self.assertEqual(self.sub(a), {"paid_until": self.today.isoformat(), "days_left": 0, "expired": False}, "l'ultimo giorno pagato è compreso")
        self.assertEqual(self.chat(a, "ciao")[0], 200)
        self.assertEqual(self.call("POST", "/api/approvals/%d/approve" % approval, {}, a)[0], 200, "rinnovato: l'azione rimasta in attesa si può approvare")
        mine = [t for t in self.call("GET", "/api/admin/tenants", token=gestore)[1]["tenants"] if t["id"] == tid][0]
        self.assertEqual(mine["paid_until"], self.today.isoformat())
        self.assertEqual(self.set(gestore, tid, "")[0], 200)
        self.assertEqual(self.sub(a)["paid_until"], "")

    def test_prova_gratuita_per_chi_si_registra_da_solo(self):
        body = {"company": "Prova Gratuita Srl", "name": "Titolare", "email": "titolare@prova-gratuita.it", "password": PW, "agents": ["email", "crm"]}
        os.environ["SFB_TRIAL_DAYS"] = "14"
        self.addCleanup(lambda: os.environ.__setitem__("SFB_TRIAL_DAYS", "0"))
        self.assertEqual(self.call("GET", "/api/register")[1]["trial_days"], 14)
        status, out = self.call("POST", "/api/register", body)
        self.assertEqual(status, 200, out)
        sub = self.sub(out["token"])
        self.assertEqual((sub["paid_until"], sub["days_left"], sub["expired"]), ((self.today + timedelta(days=14)).isoformat(), 14, False))
        status, blocked = self.call("POST", "/api/devices/code", {}, out["token"])
        self.assertEqual(status, 402, "in prova gratuita il computer non si collega: prima si paga")
        self.assertIn("prova gratuita", blocked["error"])
        os.environ["SFB_TRIAL_DAYS"] = "0"
        registration.reset_throttle()
        status, out = self.call("POST", "/api/register", dict(body, company="Senza Prova Srl", email="titolare@senza-prova.it"))
        self.assertEqual(status, 200, out)
        self.assertEqual(self.sub(out["token"])["paid_until"], "", "senza giorni di prova non c'è scadenza")


if __name__ == "__main__":
    unittest.main()
