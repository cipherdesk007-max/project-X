"""Prove della versione 29: richieste che arrivano insieme. Al lancio più persone fanno la stessa cosa nello stesso istante,
Stripe rimanda gli avvisi, un doppio tocco manda due richieste: ogni effetto deve avvenire una volta sola e i limiti devono tenere."""
import hashlib
import hmac
import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from base import PW, Base

from server import auth, billing, config, credits, db, news, registration


def together(n, fn):
    """Lancia `fn(i)` da `n` fili che partono nello stesso istante. Ritorna i risultati nell'ordine dei fili."""
    gate, out = threading.Barrier(n), [None] * n

    def one(i):
        gate.wait()
        try:
            out[i] = fn(i)
        except Exception as e:  # un errore di rete conta come risposta, non deve far sparire il filo
            out[i] = (599, {"error": repr(e)})
    threads = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    return out


class Insieme(Base):
    def setUp(self):
        registration.reset_throttle()
        auth.reset_fails()
        self.saved = dict(config.OVERRIDES)
        self.addCleanup(self.restore)

    def restore(self):
        config.OVERRIDES.clear()
        config.OVERRIDES.update(self.saved)

    def one(self, sql, *params):
        con = db.connect()
        try:
            return con.execute(sql, params).fetchone()[0]
        finally:
            con.close()

    def hook(self, event, secret="whsec_gara"):
        raw = json.dumps(event).encode()
        stamp = str(int(time.time()))
        sig = hmac.new(secret.encode(), stamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
        req = urllib.request.Request(self.base + "/api/billing/webhook", raw, {"Content-Type": "application/json", "Stripe-Signature": "t=%s,v1=%s" % (stamp, sig)}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def broken_once(self, event):
        """Manda l'avviso mentre il server inciampa subito dopo averlo segnato come visto, prima di applicarlo (come uno spegnimento a metà)."""
        real, state = billing._tenant_for, {"broken": True}

        def flaky(*a, **k):
            if state["broken"]:
                state["broken"] = False
                raise RuntimeError("guasto di prova a metà dell'avviso")
            return real(*a, **k)
        billing._tenant_for = flaky
        try:
            return self.hook(event)
        finally:
            billing._tenant_for = real

    # ---------- soldi ----------
    def test_lo_stesso_avviso_di_stripe_arrivato_venti_volte_insieme_conta_una_volta(self):
        admin, tid = self.new_tenant("Gara Ricarica", "start")
        config.OVERRIDES.update({"STRIPE_WEBHOOK_SECRET": "whsec_gara", "SFB_CREDITS": "1"})
        event = {"id": "evt_gara_ricarica", "type": "checkout.session.completed", "data": {"object": {
            "mode": "payment", "payment_status": "paid", "client_reference_id": str(tid), "customer": "cus_gara", "metadata": {"tenant_id": str(tid), "kind": "crediti", "credits": "3000"}}}}
        answers = together(20, lambda i: self.hook(event))
        self.assertEqual({s for s, _ in answers}, {200}, answers)
        self.assertEqual(self.one("SELECT COALESCE(SUM(amount),0) FROM credit_ledger WHERE tenant_id=? AND kind='acquisto'", tid), 3000, "una ricarica pagata una volta vale una volta")
        self.assertEqual(len([1 for _, o in answers if not o.get("duplicate")]), 1)

    def test_un_avviso_di_stripe_che_si_rompe_a_meta_viene_applicato_quando_stripe_lo_rimanda(self):
        admin, tid = self.new_tenant("Gara Rotta", "start")
        config.OVERRIDES.update({"STRIPE_WEBHOOK_SECRET": "whsec_gara", "SFB_CREDITS": "1"})
        event = {"id": "evt_gara_rotta", "type": "checkout.session.completed", "data": {"object": {
            "mode": "payment", "payment_status": "paid", "client_reference_id": str(tid), "customer": "cus_rotta", "metadata": {"tenant_id": str(tid), "kind": "crediti", "credits": "500"}}}}
        first = self.broken_once(event)
        self.assertGreaterEqual(first[0], 500, "Stripe deve sapere che l'avviso non è andato a buon fine, così lo rimanda")
        second = self.hook(event)
        self.assertEqual(second[0], 200, second)
        self.assertEqual(self.one("SELECT COALESCE(SUM(amount),0) FROM credit_ledger WHERE tenant_id=? AND kind='acquisto'", tid), 500,
                         "il cliente ha pagato: i crediti devono arrivare, una volta sola, anche se il primo tentativo si è rotto")
        third = self.hook(event)
        self.assertEqual((third[0], third[1].get("duplicate")), (200, True))
        self.assertEqual(self.one("SELECT COALESCE(SUM(amount),0) FROM credit_ledger WHERE tenant_id=? AND kind='acquisto'", tid), 500)

    def test_un_pagamento_di_abbonamento_che_si_rompe_a_meta_sposta_la_scadenza_al_secondo_tentativo(self):
        admin, tid = self.new_tenant("Gara Abbonamento", "start")
        config.OVERRIDES.update({"STRIPE_WEBHOOK_SECRET": "whsec_gara"})
        con = db.connect()
        con.execute("UPDATE tenants SET stripe_customer='cus_abb', stripe_subscription='sub_abb' WHERE id=?", (tid,))
        con.close()
        end = int(time.time()) + 30 * 86400
        event = {"id": "evt_gara_fattura", "type": "invoice.paid", "data": {"object": {"customer": "cus_abb", "subscription": "sub_abb", "lines": {"data": [{"period": {"end": end}}]}}}}
        first = self.broken_once(event)
        self.assertGreaterEqual(first[0], 500)
        self.assertEqual(self.hook(event)[0], 200)
        self.assertTrue(self.one("SELECT paid_until FROM tenants WHERE id=?", tid), "ha pagato: la scadenza deve spostarsi")
        self.assertEqual(self.one("SELECT COUNT(*) FROM audit WHERE tenant_id=? AND action='pagamento_ricevuto'", tid), 1)

    def test_venti_risposte_insieme_non_scalano_dai_crediti_inclusi_piu_di_quanti_sono(self):
        admin, tid = self.new_tenant("Gara Crediti", "start")
        config.OVERRIDES.update({"SFB_CREDITS": "1", "CREDITS_START": "100", "CREDIT_RATE_CLAUDE": "1000"})
        con = db.connect()
        credits.grant(con, tid, 100000, "acquisto", "per la prova")
        tenant = dict(con.execute("SELECT * FROM tenants WHERE id=?", (tid,)).fetchone())
        con.close()

        def spend(i):
            c = db.connect()
            try:
                return credits.charge(c, tenant, None, "claude", 30)  # 30 crediti l'una
            finally:
                c.close()
        self.assertEqual(together(20, spend), [30] * 20)
        included = self.one("SELECT COALESCE(SUM(from_included),0) FROM credit_ledger WHERE tenant_id=? AND kind='consumo'", tid)
        extra = self.one("SELECT COALESCE(SUM(from_extra),0) FROM credit_ledger WHERE tenant_id=? AND kind='consumo'", tid)
        self.assertEqual((included, extra), (100, 500), "i 100 inclusi si usano una volta sola; il resto viene dai comprati")

    # ---------- codici e collegamenti ----------
    def test_un_codice_di_collegamento_usato_da_venti_computer_insieme_ne_collega_uno(self):
        admin, tid = self.new_tenant("Gara Codice", "completo")
        code = self.call("POST", "/api/devices/code", {}, admin)[1]["code"]
        answers = together(20, lambda i: self.call("POST", "/api/launcher/pair", {"code": code, "name": "PC %d" % i, "specs": {"os": "linux"}, "checks": {}, "launcher_version": "1.2.0"}))
        self.assertEqual(sorted(s for s, _ in answers).count(200), 1, [s for s, _ in answers])
        self.assertEqual(self.one("SELECT COUNT(*) FROM devices WHERE tenant_id=?", tid), 1)

    def test_la_stessa_email_registrata_dieci_volte_insieme_crea_una_sola_azienda(self):
        config.OVERRIDES.update({"SFB_REGISTRATION": "1", "SFB_REGISTER_PER_IP_HOUR": "1000"})
        body = {"company": "Gara Registrazione", "name": "Rita", "email": "rita@gara-registrazione.it", "password": PW, "agents": ["email"], "accept": True}
        answers = together(10, lambda i: self.call("POST", "/api/register", body))
        codes = sorted(s for s, _ in answers)
        self.assertEqual(codes.count(200), 1, answers)
        self.assertFalse([s for s in codes if s >= 500], answers)
        self.assertEqual(self.one("SELECT COUNT(*) FROM users WHERE email=?", "rita@gara-registrazione.it"), 1)
        self.assertEqual(self.one("SELECT COUNT(*) FROM tenants WHERE name=?", "Gara Registrazione"), 1, "nessuna azienda rimasta a metà, senza il suo amministratore")

    def test_lo_stesso_utente_aggiunto_dieci_volte_insieme_non_da_errori_interni(self):
        admin, tid = self.new_tenant("Gara Utenti", "completo")
        answers = together(10, lambda i: self.call("POST", "/api/users", {"email": "doppio@gara-utenti.it", "name": "Doppio", "role": "member", "password": PW}, admin))
        codes = sorted(s for s, _ in answers)
        self.assertEqual((codes.count(200), [s for s in codes if s >= 500]), (1, []), answers)

    # ---------- limiti del pacchetto ----------
    def test_venti_lavori_automatici_creati_insieme_non_superano_il_limite_del_pacchetto(self):
        admin, tid = self.new_tenant("Gara Lavori", "start")  # Start: 2 lavori automatici
        job = {"name": "Riepilogo", "prompt": "fammi un riepilogo", "model": "claude", "kind": "interval", "every_minutes": 15}
        answers = together(20, lambda i: self.call("POST", "/api/automations", dict(job, name="Lavoro %d" % i), admin))
        self.assertEqual(self.one("SELECT COUNT(*) FROM automations WHERE tenant_id=?", tid), 2, sorted(s for s, _ in answers))
        self.assertEqual(sorted(s for s, _ in answers).count(200), 2)
        self.assertFalse([s for s, _ in answers if s >= 500], answers)

    def test_venti_documenti_caricati_insieme_non_superano_il_limite_del_pacchetto(self):
        admin, tid = self.new_tenant("Gara Documenti", "start")  # Start: 5 documenti
        answers = together(20, lambda i: self.call("POST", "/api/memory/documents", {"title": "Listino %d" % i, "text": "Il prodotto %d costa %d euro." % (i, i)}, admin))
        self.assertEqual(self.one("SELECT COUNT(*) FROM memory WHERE tenant_id=? AND kind='documento'", tid), 5, sorted(s for s, _ in answers))
        self.assertFalse([s for s, _ in answers if s >= 500], answers)

    def test_il_limite_dei_documenti_tiene_anche_se_tra_il_conto_e_la_scrittura_passa_del_tempo(self):
        """Come la prova sopra, ma senza affidarsi al caso: ogni richiesta, dopo aver contato i documenti, resta ferma un attimo prima di scrivere."""
        from server import memory
        admin, tid = self.new_tenant("Gara Documenti Lenta", "start")
        con = db.connect()
        tenant = dict(con.execute("SELECT * FROM tenants WHERE id=?", (tid,)).fetchone())
        user = dict(con.execute("SELECT * FROM users WHERE tenant_id=? LIMIT 1", (tid,)).fetchone())
        con.close()

        def add(i):
            mine = db.connect()
            real = mine.execute

            class Slow:
                def __getattr__(self, name):  # tutto il resto è la connessione vera
                    return getattr(mine, name)

                def execute(self, sql, params=()):
                    out = real(sql, params)
                    if sql.startswith("SELECT COUNT(*) FROM memory"):
                        time.sleep(0.15)
                    return out
            try:
                memory.add_document(Slow(), tenant, user, "Listino lento %d" % i, "Il prodotto %d costa %d euro." % (i, i))
                return 200
            except Exception as e:
                return getattr(e, "status", None) or repr(e)
            finally:
                mine.close()
        codes = together(8, add)
        self.assertEqual(self.one("SELECT COUNT(*) FROM memory WHERE tenant_id=? AND kind='documento'", tid), 5, codes)
        self.assertEqual(sorted(codes, key=str), [200] * 5 + [403] * 3)

    # ---------- azioni ----------
    def test_un_approvazione_toccata_venti_volte_insieme_fa_partire_l_azione_una_volta(self):
        admin, tid = self.new_tenant("Gara Approvazione", "completo")
        self.assertEqual(self.call("PUT", "/api/connectors/telegram", {"bot_token": "999:ZZZ", "default_chat_id": "777"}, admin)[0], 200)
        import base
        before = len(base.TELEGRAM_CALLS)
        out = self.chat(admin, "manda su telegram: arrivo domani alle 9")[1]
        approval = out["events"][0]["approval_id"]
        answers = together(20, lambda i: self.call("POST", "/api/approvals/%d/approve" % approval, {}, admin))
        self.assertEqual(sorted(s for s, _ in answers).count(200), 1, [s for s, _ in answers])
        self.assertEqual(len(base.TELEGRAM_CALLS) - before, 1, "un messaggio approvato parte una volta sola")

    def test_il_registro_resta_una_catena_intatta_anche_con_cento_scritture_insieme(self):
        from server import audit
        admin, tid = self.new_tenant("Gara Registro", "completo")

        def write(i):
            c = db.connect()
            try:
                for k in range(5):
                    audit.record(c, tid, None, "prova_di_gara", {"filo": i, "giro": k})
            finally:
                c.close()
        together(20, write)
        con = db.connect()
        ok, rows, broken, _ = audit.verify(con, tid)
        con.close()
        self.assertTrue(ok, "catena rotta alla riga %s" % broken)
        self.assertGreaterEqual(rows, 100)

    # ---------- quello che si è visto solo con cento aziende insieme ----------
    def pc(self, admin, name="PC"):
        code = self.call("POST", "/api/devices/code", {}, admin)[1]["code"]
        out = self.call("POST", "/api/launcher/pair", {"code": code, "name": name, "specs": {"os": "linux"}, "checks": {"pkg:dev": {"ok": True}}, "launcher_version": "1.2.0"})[1]
        return out["token"], out["device_id"]

    def job(self, tid):
        con = db.connect()
        cur = con.execute("INSERT INTO jobs(tenant_id,agent_id,tool,package,place,args,requested_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                          (tid, "dev", "dev_elenca_file", "dev", "locale", "{}", 1, db.now()))
        con.close()
        return cur.lastrowid

    def test_prendere_un_lavoro_mentre_un_altro_scrive_non_da_database_occupato(self):
        """Il launcher leggeva l'elenco dei lavori e, con la lettura ancora aperta, scriveva: se nel frattempo scriveva chiunque altro,
        la sua scrittura falliva all'istante. Qui «chiunque altro» scrive esattamente in quel momento."""
        from server import local
        admin, tid = self.new_tenant("Gara Lettura Aperta", "completo")
        token, device_id = self.pc(admin)
        job_id = self.job(tid)
        self.job(tid), self.job(tid)  # più lavori in coda: è con l'elenco ancora da scorrere che la lettura resta aperta
        con, other = db.connect(), db.connect()
        device = dict(con.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone())
        tenant = dict(con.execute("SELECT * FROM tenants WHERE id=?", (tid,)).fetchone())
        real = con.execute

        class Busy:
            def execute(self, sql, params=()):
                if sql.startswith("UPDATE jobs SET status='in_corso'"):
                    other.execute("UPDATE tenants SET name=name WHERE id=?", (tid,))  # un'altra richiesta scrive proprio adesso
                return real(sql, params)
        try:
            got = local.claim(Busy(), device, tenant, 0)
        finally:
            con.close()
            other.close()
        self.assertEqual((got or {}).get("id"), job_id)

    def test_un_launcher_in_attesa_non_scrive_di_continuo_e_viene_svegliato_subito(self):
        from server import local
        admin, tid = self.new_tenant("Gara Campanello", "completo")
        token, device_id = self.pc(admin)
        con = db.connect()
        device = dict(con.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone())
        tenant = dict(con.execute("SELECT * FROM tenants WHERE id=?", (tid,)).fetchone())
        real, writes = con.execute, []

        class Counting:
            def execute(self, sql, params=()):
                if sql.startswith("UPDATE devices SET last_seen"):
                    writes.append(time.time())
                return real(sql, params)
        started = time.time()
        self.assertIsNone(local.claim(Counting(), device, tenant, 3))
        self.assertGreaterEqual(time.time() - started, 2.9)
        self.assertLessEqual(len(writes), 2, "in tre secondi di attesa a vuoto «visto adesso» si scrive una volta, non quindici")
        # e quando il lavoro arriva non si aspetta il giro successivo: chi lo crea suona il campanello
        got = {}

        def wait_for_work():
            mine = db.connect()  # ogni filo ha la sua connessione, come ogni richiesta del server
            try:
                got.update(job=local.claim(mine, device, tenant, 8), at=time.time())
            finally:
                mine.close()
        waiting = threading.Thread(target=wait_for_work)
        waiting.start()
        time.sleep(0.6)
        job_id, rung = self.job(tid), time.time()
        local.ring(tid)
        waiting.join(15)
        con.close()
        self.assertEqual((got.get("job") or {}).get("id"), job_id)
        self.assertLess(got["at"] - rung, 0.25, "svegliato dal campanello, non dal giro di controllo")

    def test_cinquanta_computer_collegati_dallo_stesso_ufficio_passano_e_i_codici_sbagliati_no(self):
        admin, tid = self.new_tenant("Gara Ufficio", "completo")
        for i in range(50):
            code = self.call("POST", "/api/devices/code", {}, admin)[1]["code"]
            status, out = self.call("POST", "/api/launcher/pair", {"code": code, "name": "PC %d" % i, "specs": {"os": "linux"}, "checks": {}, "launcher_version": "1.2.0"})
            self.assertEqual(status, 200, (i, out))
        registration.reset_throttle()
        codes = [self.call("POST", "/api/launcher/pair", {"code": "AAAA-AA%02d" % i, "name": "x"})[0] for i in range(33)]
        self.assertEqual((codes[:30], codes[30:]), ([403] * 30, [429] * 3), "chi prova codici a caso viene fermato come prima")
        registration.reset_throttle()

    def test_il_database_scrive_svelto_ma_i_pagamenti_vanno_sempre_sul_disco(self):
        con = db.connect()
        self.assertEqual(con.execute("PRAGMA synchronous").fetchone()[0], 1, "NORMAL: la scelta consigliata con il registro WAL")
        self.assertEqual(con.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        db.durable(con)
        self.assertEqual(con.execute("PRAGMA synchronous").fetchone()[0], 2, "FULL per chi non può perdere nemmeno l'ultima scrittura")
        con.close()
        seen, real = [], db.durable
        db.durable = lambda c: (seen.append(1), real(c))[1]
        config.OVERRIDES.update({"STRIPE_WEBHOOK_SECRET": "whsec_gara"})
        try:
            self.assertEqual(self.hook({"id": "evt_gara_disco", "type": "invoice.paid", "data": {"object": {"customer": "nessuno"}}})[0], 200)
        finally:
            db.durable = real
        self.assertEqual(seen, [1], "ogni avviso di Stripe viene scritto in modo garantito prima di rispondere «ricevuto»")

    def test_il_server_oltre_il_limite_dice_di_riprovare_e_non_errore_interno(self):
        import sqlite3
        from server import app
        admin, tid = self.new_tenant("Gara Sovraccarico", "start")
        real = app.r_summary
        routes = [(i, r) for i, r in enumerate(app.ROUTES) if r[-1] is real]
        self.assertTrue(routes)

        def busy(q):
            raise sqlite3.OperationalError("database is locked")
        for i, r in routes:
            app.ROUTES[i] = r[:-1] + (busy,)
        try:
            status, out = self.call("GET", "/api/summary", token=admin)
        finally:
            for i, r in routes:
                app.ROUTES[i] = r
        self.assertEqual(status, 503, out)
        self.assertIn("Riprova tra qualche secondo", out["error"])

    def test_un_cliente_che_chiude_a_meta_lascia_una_riga_nel_registro_non_una_pagina(self):
        import contextlib
        import io
        from server import app
        out = io.StringIO()
        with contextlib.redirect_stderr(out):
            try:
                raise BrokenPipeError(32, "Broken pipe")
            except BrokenPipeError:
                app.Server.handle_error(self.server if hasattr(self, "server") else app.Server.__new__(app.Server), None, ("127.0.0.1", 1))
        self.assertNotIn("Traceback", out.getvalue())
        self.assertLessEqual(len(out.getvalue().splitlines()), 1)


class FornitoreInDifficolta(unittest.TestCase):
    """Il fornitore del modello che per un attimo non ce la fa: si riprova, senza dirlo a chi lavora. Gli errori veri non si riprovano."""

    def setUp(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from server import llm
        plan, calls = [], []

        class Fornitore(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                calls.append(self.path)
                step = plan.pop(0) if plan else 200
                if step == "cade":  # la connessione cade prima di qualunque risposta
                    self.connection.close()
                    return
                body = json.dumps({"content": [{"type": "text", "text": "eccomi"}], "stop_reason": "end_turn", "usage": {}} if step == 200 else {"error": {"message": "guasto %s" % step}}).encode()
                self.send_response(step)
                if step == 429:
                    self.send_header("Retry-After", "0.01")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Fornitore)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.llm, self.plan, self.calls = llm, plan, calls
        self.saved, self.pauses = dict(config.OVERRIDES), llm.PAUSES
        llm.PAUSES = (0.01, 0.01)
        import os
        self.demo = os.environ.get("SFB_DEMO")
        os.environ["SFB_DEMO"] = "0"
        config.OVERRIDES.update({"ANTHROPIC_API_KEY": "k", "ANTHROPIC_BASE_URL": "http://127.0.0.1:%d" % self.server.server_address[1]})
        self.addCleanup(self.restore)

    def restore(self):
        import os
        config.OVERRIDES.clear()
        config.OVERRIDES.update(self.saved)
        self.llm.PAUSES = self.pauses
        os.environ["SFB_DEMO"] = self.demo or "1"
        self.server.shutdown()
        self.server.server_close()

    def ask(self):
        return self.llm.chat("claude", "s", [{"role": "user", "content": "ciao"}], [])["text"]

    def test_un_sovraccarico_di_un_attimo_non_arriva_a_chi_lavora(self):
        for trouble in ([529, 529], [503], [429], ["cade"], ["cade", 500]):
            self.plan[:], self.calls[:] = list(trouble), []
            self.assertEqual(self.ask(), "eccomi", trouble)
            self.assertEqual(len(self.calls), len(trouble) + 1, trouble)

    def test_dopo_tre_tentativi_falliti_lo_si_dice(self):
        self.plan[:], self.calls[:] = [529, 529, 529, 200], []
        with self.assertRaises(self.llm.LLMError) as e:
            self.ask()
        self.assertIn("529", str(e.exception))
        self.assertEqual(len(self.calls), 3, "tre tentativi in tutto, non di più")

    def test_una_chiave_sbagliata_o_una_richiesta_rifiutata_non_si_riprovano(self):
        for code in (400, 401, 403, 404):
            self.plan[:], self.calls[:] = [code, 200], []
            with self.assertRaises(self.llm.LLMError):
                self.ask()
            self.assertEqual(len(self.calls), 1, code)

    def test_i_tentativi_hanno_una_misura(self):
        self.assertEqual(self.pauses, (1.0, 3.0), "tre tentativi in tutto e al massimo quattro secondi di pausa: di più terrebbe ferma la persona senza motivo")

    def test_la_pausa_chiesta_dal_fornitore_si_rispetta_solo_se_breve(self):
        self.assertEqual((self.llm._pause(0, "2"), self.llm._pause(1, "3600"), self.llm._pause(0, "boh"), self.llm._pause(1, None)), (2.0, 0.01, 0.01, 0.01))


if __name__ == "__main__":
    unittest.main()
