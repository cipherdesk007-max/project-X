"""Prove di: lavori automatici, novità, arresto d'emergenza, memoria, accesso Google, aggiornamento del database."""
import json
import os
import sqlite3
import tempfile
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

from base import GOOGLE, PW, TELEGRAM_CALLS, Base
from server import catalog, connectors, db, google, llm, memory, ops, scheduler, tools


def last_system_prompt():
    """Istruzioni mandate al modello nell'ultima richiesta (formato Anthropic)."""
    for req in reversed(llm.DEMO_SERVER.requests):
        if req["path"].endswith("/v1/messages"):
            return req["body"]["system"]
    return ""


def make_due(tenant_id):
    con = db.connect()
    con.execute("UPDATE automations SET next_run_at=? WHERE tenant_id=?", (int(time.time()) - 5, tenant_id))
    con.close()


class TestAutomazioni(Base):
    JOB = {"name": "Riepilogo", "prompt": "fammi un riepilogo", "model": "claude", "kind": "interval", "every_minutes": 15}

    def test_limite_per_pacchetto_e_solo_amministratori(self):
        admin, _ = self.new_tenant("Limite Start", "start")
        for i in range(catalog.limit("start", "automazioni")):
            self.assertEqual(self.call("POST", "/api/automations", dict(self.JOB, name="Lavoro %d" % i), admin)[0], 200)
        status, out = self.call("POST", "/api/automations", self.JOB, admin)
        self.assertEqual(status, 403)
        self.assertIn("Start", out["error"])
        boss = self.login("admin@business.it")
        self.call("POST", "/api/users", {"email": "op-auto@business.it", "name": "Op", "role": "member", "password": PW}, boss)
        member = self.login("op-auto@business.it")
        self.assertEqual(self.call("POST", "/api/automations", self.JOB, member)[0], 403)
        self.assertEqual(self.call("GET", "/api/automations", token=member)[0], 403)

    def test_regole_su_orario_agente_e_modello(self):
        admin, _ = self.new_tenant("Regole Start", "start")
        self.assertEqual(self.call("POST", "/api/automations", dict(self.JOB, every_minutes=5), admin)[0], 400)
        self.assertEqual(self.call("POST", "/api/automations", dict(self.JOB, agent_id="crm"), admin)[0], 403)
        self.assertEqual(self.call("POST", "/api/automations", dict(self.JOB, model="chatgpt"), admin)[0], 403)
        self.assertEqual(self.call("POST", "/api/automations", dict(self.JOB, kind="daily", hour=25), admin)[0], 400)
        self.assertEqual(self.call("POST", "/api/automations", dict(self.JOB, prompt=""), admin)[0], 400)

    def test_prossima_esecuzione_giornaliera(self):
        from zoneinfo import ZoneInfo
        rome = ZoneInfo("Europe/Rome")
        now = datetime(2026, 11, 23, 13, 0, tzinfo=rome).timestamp()  # lunedì
        at = lambda *a: datetime(*a, tzinfo=rome).timestamp()  # noqa: E731
        self.assertEqual(scheduler.next_run("daily", 14, 0, "", None, now), at(2026, 11, 23, 14, 0))
        self.assertEqual(scheduler.next_run("daily", 9, 30, "", None, now), at(2026, 11, 24, 9, 30))
        self.assertEqual(scheduler.next_run("daily", 9, 30, "5", None, now), at(2026, 11, 28, 9, 30))  # solo sabato
        self.assertEqual(scheduler.next_run("daily", 13, 0, "0", None, now), at(2026, 11, 30, 13, 0))  # lunedì stessa ora: il prossimo
        self.assertEqual(scheduler.next_run("interval", None, None, "", 15, now), now + 900)
        # cambio dell'ora: il 29 marzo 2026 alle 9:00 resta alle 9:00 locali
        before = datetime(2026, 3, 28, 12, 0, tzinfo=rome).timestamp()
        self.assertEqual(scheduler.next_run("daily", 9, 0, "", None, before), at(2026, 3, 29, 9, 0))

    def test_il_lavoro_scaduto_parte_una_volta_e_lascia_una_novita(self):
        admin, tid = self.new_tenant("Giro Business", "business")
        job = dict(self.JOB, name="Lead del mattino", prompt="aggiungi lead Cliente Automatico", agent_id="crm")
        self.assertEqual(self.call("POST", "/api/automations", job, admin)[0], 200)
        self.assertEqual(scheduler.tick(), 0, "non ancora scaduto")
        make_due(tid)
        self.assertEqual(scheduler.tick(), 1)
        self.assertEqual(scheduler.tick(), 0, "già fatto: non riparte")
        names = [r["name"] for r in self.call("GET", "/api/data/leads", token=admin)[1]["rows"]]
        self.assertEqual(names, ["Cliente Automatico"])
        auto = self.call("GET", "/api/automations", token=admin)[1]["automations"][0]
        self.assertEqual((auto["last_status"], auto["active"]), ("ok", 1))
        self.assertGreater(auto["next_run_at"], time.time() + 800)
        news = self.call("GET", "/api/news", token=admin)[1]["news"]
        self.assertIn("Lead del mattino: fatto", [n["title"] for n in news])
        summary = self.call("GET", "/api/summary", token=admin)[1]
        self.assertEqual((summary["unread"] >= 1, summary["automations"]), (True, 1))
        self.call("POST", "/api/news/read", {}, admin)
        self.assertEqual(self.call("GET", "/api/summary", token=admin)[1]["unread"], 0)

    def test_due_processi_non_eseguono_lo_stesso_lavoro(self):
        admin, tid = self.new_tenant("Doppio Business", "business")
        self.call("POST", "/api/automations", dict(self.JOB, prompt="aggiungi lead Una Volta Sola", agent_id="crm"), admin)
        make_due(tid)
        inner = []
        # Un secondo processo legge lo stesso lavoro scaduto e lo esegue mentre il primo sta per farlo.
        outer = scheduler.tick(_between=lambda: inner.append(scheduler.tick()))
        self.assertEqual((inner, outer), ([1], 0))
        self.assertEqual(len(self.call("GET", "/api/data/leads", token=admin)[1]["rows"]), 1)

    def test_azione_esterna_di_un_lavoro_automatico_aspetta_approvazione(self):
        admin, tid = self.new_tenant("Esterno Start", "start")
        self.call("PUT", "/api/connectors/telegram", {"bot_token": "55:AUTO", "default_chat_id": "9"}, admin)
        self.call("POST", "/api/automations", dict(self.JOB, prompt="manda su telegram: buongiorno a tutti"), admin)
        before = len(TELEGRAM_CALLS)
        make_due(tid)
        self.assertEqual(scheduler.tick(), 1)
        self.assertEqual(len(TELEGRAM_CALLS), before, "un lavoro automatico non deve inviare nulla da solo")
        pending = [a for a in self.call("GET", "/api/approvals", token=admin)[1]["approvals"] if a["status"] == "pending"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(self.call("GET", "/api/summary", token=admin)[1]["pending"], 1)

    def test_pausa_dopo_tre_errori_di_fila(self):
        admin, tid = self.new_tenant("Errori Business", "business")
        self.call("POST", "/api/automations", dict(self.JOB, agent_id="crm", prompt="elenca i lead"), admin)
        con = db.connect()
        ops.update_tenant(con, tid, package_id="start")  # il CRM non è più nel pacchetto: il lavoro fallirà
        con.close()
        for _ in range(scheduler.MAX_FAILS):
            make_due(tid)
            self.assertEqual(scheduler.tick(), 1)
        auto = self.call("GET", "/api/automations", token=admin)[1]["automations"][0]
        self.assertEqual((auto["active"], auto["last_status"], auto["fail_count"]), (0, "errore", 3))
        make_due(tid)
        self.assertEqual(scheduler.tick(), 0, "in pausa: non riparte")
        titles = [n["title"] for n in self.call("GET", "/api/news", token=admin)[1]["news"]]
        self.assertTrue(any("messo in pausa" in t for t in titles), titles)

    def test_esegui_ora_modifica_e_rimozione(self):
        admin, tid = self.new_tenant("Manuale Start", "start")
        aid = self.call("POST", "/api/automations", self.JOB, admin)[1]["id"]
        status, out = self.call("POST", "/api/automations/%d/run" % aid, {}, admin)
        self.assertEqual((status, out["status"]), (200, "ok"))
        self.assertIn("Riepilogo", out["output"])
        self.assertEqual(self.call("POST", "/api/automations/%d" % aid, {"active": False}, admin)[0], 200)
        make_due(tid)
        self.assertEqual(scheduler.tick(), 0)
        other = self.login("admin@completo.it")
        self.assertEqual(self.call("POST", "/api/automations/%d/run" % aid, {}, other)[0], 404, "lavoro di un'altra azienda")
        self.assertEqual(self.call("DELETE", "/api/automations/%d" % aid, token=other)[0], 404)
        self.assertEqual(self.call("DELETE", "/api/automations/%d" % aid, token=admin)[0], 200)
        self.assertEqual(self.call("GET", "/api/automations", token=admin)[1]["automations"], [])


class TestArrestoEmergenza(Base):
    def test_ferma_chat_lavori_e_approvazioni_poi_riparte_senza_recuperi(self):
        admin, tid = self.new_tenant("Arresto Start", "start")
        self.call("PUT", "/api/connectors/telegram", {"bot_token": "77:STOP", "default_chat_id": "3"}, admin)
        approval = self.chat(admin, "manda su telegram: prima dell'arresto")[1]["events"][0]["approval_id"]
        self.call("POST", "/api/automations", {"name": "Giro", "prompt": "fammi un riepilogo", "model": "claude",
                                               "kind": "interval", "every_minutes": 15}, admin)
        self.assertEqual(self.call("POST", "/api/stop", {"active": True}, admin)[0], 200)
        self.assertTrue(self.call("GET", "/api/me", token=admin)[1]["tenant"]["stopped"])
        status, out = self.chat(admin, "ciao")
        self.assertEqual(status, 409)
        self.assertIn("Arresto", out["error"])
        before = len(TELEGRAM_CALLS)
        self.assertEqual(self.call("POST", "/api/approvals/%d/approve" % approval, {}, admin)[0], 409)
        self.assertEqual(len(TELEGRAM_CALLS), before)
        make_due(tid)
        self.assertEqual(scheduler.tick(), 0, "con l'arresto attivo i lavori non partono")
        self.assertEqual(self.call("POST", "/api/stop", {"active": False}, admin)[0], 200)
        self.assertEqual(scheduler.tick(), 0, "alla ripartenza i giri saltati non vengono recuperati")
        self.assertEqual(self.chat(admin, "ciao")[0], 200)
        self.assertEqual(self.call("POST", "/api/approvals/%d/approve" % approval, {}, admin)[1]["status"], "executed")
        self.assertEqual(len(TELEGRAM_CALLS), before + 1)

    def test_solo_un_amministratore_puo_fermare(self):
        boss = self.login("admin@completo.it")
        self.call("POST", "/api/users", {"email": "op-stop@completo.it", "name": "Op", "role": "member", "password": PW}, boss)
        self.assertEqual(self.call("POST", "/api/stop", {"active": True}, self.login("op-stop@completo.it"))[0], 403)


class TestMemoria(Base):
    def test_un_fatto_entra_nelle_istruzioni_solo_della_sua_azienda(self):
        a, _ = self.new_tenant("Memoria A", "start")
        b, _ = self.new_tenant("Memoria B", "start")
        self.assertEqual(self.call("POST", "/api/memory/facts", {"text": "Chiudiamo il giovedì pomeriggio."}, a)[0], 200)
        self.chat(a, "ciao")
        self.assertIn("Chiudiamo il giovedì pomeriggio.", last_system_prompt())
        self.chat(b, "ciao")
        self.assertNotIn("giovedì pomeriggio", last_system_prompt())
        self.assertEqual(self.call("GET", "/api/memory", token=b)[1]["facts"], [])

    def test_un_agente_puo_solo_proporre_e_serve_la_conferma(self):
        boss, _ = self.new_tenant("Memoria Proposte", "business")
        self.call("POST", "/api/users", {"email": "op@proposte.it", "name": "Operatore", "role": "member", "password": PW}, boss)
        member = self.login("op@proposte.it")
        self.assertEqual(self.call("POST", "/api/memory/facts", {"text": "x"}, member)[0], 403)
        out = self.chat(member, "ricordati che il corriere passa alle 16")[1]
        self.assertEqual(out["events"], [{"tool": "memoria_proponi", "status": "eseguito"}])
        self.chat(member, "ciao")
        self.assertNotIn("corriere passa alle 16", last_system_prompt(), "una proposta non confermata non deve influenzare gli agenti")
        self.assertEqual(self.call("GET", "/api/memory", token=member)[1]["proposals"], [], "le proposte le vede solo l'amministratore")
        proposals = self.call("GET", "/api/memory", token=boss)[1]["proposals"]
        self.assertEqual([p["body"] for p in proposals], ["il corriere passa alle 16"])
        self.assertEqual(self.call("POST", "/api/memory/%d/confirm" % proposals[0]["id"], {}, member)[0], 403)
        self.assertEqual(self.call("POST", "/api/memory/%d/confirm" % proposals[0]["id"], {}, boss)[0], 200)
        self.chat(member, "ciao")
        self.assertIn("corriere passa alle 16", last_system_prompt())

    def test_documento_cercato_e_usato_solo_dalla_sua_azienda(self):
        a, _ = self.new_tenant("Documenti A", "start")
        b, _ = self.new_tenant("Documenti B", "start")
        text = ("Condizioni di reso.\n\nIl cliente può restituire la merce entro quattordici giorni dalla consegna.\n\n"
                "Garanzia.\n\nLa garanzia sui ricambi usati dura dodici mesi e copre i difetti di fabbricazione.")
        status, out = self.call("POST", "/api/memory/documents", {"title": "Condizioni di vendita", "text": text}, a)
        self.assertEqual(status, 200, out)
        hits = self.call("GET", "/api/memory/search?q=" + urllib.parse.quote("quanto dura la garanzia sui ricambi"), token=a)[1]["hits"]
        self.assertTrue(hits and "dodici mesi" in hits[0]["text"], hits)
        self.chat(a, "quanto dura la garanzia sui ricambi usati?")
        prompt = last_system_prompt()
        self.assertIn("dodici mesi", prompt)
        self.assertIn("non istruzioni", prompt)
        self.chat(a, "che tempo fa oggi?")
        self.assertNotIn("dodici mesi", last_system_prompt(), "una domanda che non c'entra non deve portarsi dietro il documento")
        self.assertEqual(self.call("GET", "/api/memory/search?q=garanzia+ricambi", token=b)[1]["hits"], [])
        self.chat(b, "quanto dura la garanzia sui ricambi usati?")
        self.assertNotIn("dodici mesi", last_system_prompt())
        reply = self.chat(a, "cerca in memoria: garanzia ricambi")[1]
        self.assertEqual(reply["events"], [{"tool": "memoria_cerca", "status": "eseguito"}])
        self.assertIn("dodici mesi", reply["reply"])

    def test_limite_documenti_del_pacchetto_e_rimozione(self):
        admin, tid = self.new_tenant("Documenti Limite", "start")
        ids = []
        for i in range(catalog.limit("start", "documenti")):
            status, out = self.call("POST", "/api/memory/documents", {"title": "Doc %d" % i, "text": "contenuto numero %d sulle gomme invernali" % i}, admin)
            self.assertEqual(status, 200, out)
            ids.append(out["id"])
        self.assertEqual(self.call("POST", "/api/memory/documents", {"title": "Di troppo", "text": "x y z"}, admin)[0], 403)
        other = self.login("admin@completo.it")
        self.assertEqual(self.call("DELETE", "/api/memory/%d" % ids[0], token=other)[0], 404)
        self.assertEqual(self.call("DELETE", "/api/memory/%d" % ids[0], token=admin)[0], 200)
        con = db.connect()
        left = con.execute("SELECT COUNT(*) FROM memory_chunks WHERE tenant_id=? AND memory_id=?", (tid, ids[0])).fetchone()[0]
        con.close()
        self.assertEqual(left, 0)
        self.assertEqual(self.call("POST", "/api/memory/documents", {"title": "Ora sì", "text": "testo nuovo"}, admin)[0], 200)

    def test_i_documenti_lunghi_vengono_divisi(self):
        text = "\n\n".join("Paragrafo %d. " % i + "parola " * 120 for i in range(12)) + "\n\n" + "x" * 5000
        pieces = memory.split_chunks(text)
        self.assertGreater(len(pieces), 8)
        self.assertTrue(all(0 < len(p) <= memory.CHUNK_CHARS for p in pieces))
        self.assertEqual(self.call("POST", "/api/memory/documents", {"title": "T", "text": "a" * (memory.MAX_DOC_CHARS + 1)},
                                   self.login("admin@completo.it"))[0], 400)


class TestGoogle(Base):
    ALL = ["gmail_lettura", "gmail_invio", "calendario", "fogli"]

    def setUp(self):
        GOOGLE.update({"challenge": None, "scope": "", "revoked": False})
        for key in ("sent", "events", "revocations", "token_calls"):
            del GOOGLE[key][:]

    def begin(self, admin, services=None):
        status, headers, out = self.raw("POST", "/api/google/start", {"services": services or self.ALL}, admin)
        self.assertEqual(status, 200, out)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(out["url"]).query).items()}
        return q, headers["Set-Cookie"].split(";")[0], headers["Set-Cookie"], out["url"]

    def connect(self, admin, services=None, granted=None):
        """Percorso completo: avvio, consenso (simulato) e ritorno dal finto Google."""
        q, cookie, _, _ = self.begin(admin, services)
        GOOGLE["challenge"] = q["code_challenge"]
        GOOGLE["scope"] = granted if granted is not None else q["scope"]
        status, headers, _ = self.raw("GET", "/api/google/callback?code=codice-buono&state=" + q["state"], cookie=cookie)
        return status, headers["Location"]

    def ctx(self, tenant_id):
        con = db.connect()
        tenant = dict(con.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,)).fetchone())
        user = dict(con.execute("SELECT * FROM users WHERE tenant_id=?", (tenant_id,)).fetchone())
        return tools.Ctx(con, tenant, user)

    def test_richiesta_a_google_costruita_bene(self):
        admin, _ = self.new_tenant("Google Avvio", "start")
        q, cookie, set_cookie, url = self.begin(admin, ["calendario"])
        self.assertTrue(url.startswith(os.environ["GOOGLE_AUTH_URL"] + "?"))
        self.assertEqual((q["client_id"], q["response_type"], q["access_type"], q["prompt"], q["code_challenge_method"]),
                         ("id-prova", "code", "offline", "consent", "S256"))
        self.assertEqual(q["redirect_uri"], self.base + "/api/google/callback")
        self.assertEqual(q["scope"].split(), ["openid", "email", "https://www.googleapis.com/auth/calendar.events"])
        self.assertGreaterEqual(len(q["state"]), 40)
        self.assertEqual(len(q["code_challenge"]), 43)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=Lax", set_cookie)
        self.assertNotIn("segreto-prova", url)

    def test_collegamento_completo_e_token_mai_esposti(self):
        admin, tid = self.new_tenant("Google Completo", "start")
        self.assertEqual(self.call("GET", "/api/me", token=admin)[1]["agent_status"]["email"], "Da collegare")
        self.assertEqual(self.connect(admin), (302, "/#google-ok"))
        exchange = GOOGLE["token_calls"][-1]
        self.assertEqual((exchange["grant_type"], exchange["code"]), ("authorization_code", "codice-buono"))
        status, st = self.call("GET", "/api/google", token=admin)
        self.assertEqual((st["connected"], st["email"], sorted(st["granted"])), (True, "azienda@example.com", sorted(self.ALL)))
        blob = json.dumps(st) + json.dumps(self.call("GET", "/api/connectors", token=admin)[1])
        for secret in ("rt-1", "at-", "segreto-prova"):
            self.assertNotIn(secret, blob)
        me = self.call("GET", "/api/me", token=admin)[1]
        self.assertEqual((me["agent_status"]["email"], me["agent_status"]["calendario"]), ("Gmail collegato", "Google Calendar"))
        actions = [e["action"] for e in self.call("GET", "/api/audit", token=admin)[1]["entries"]]
        self.assertIn("google_collegato", actions)
        other = self.login("admin@completo.it")
        self.assertFalse(self.call("GET", "/api/google", token=other)[1]["connected"], "il collegamento è solo di quell'azienda")

    def test_ritorno_falsificato_rifiutato(self):
        admin, _ = self.new_tenant("Google Falso", "start")
        # state inventato
        self.assertEqual(self.raw("GET", "/api/google/callback?code=codice-buono&state=inventato")[1]["Location"], "/#google-stato")
        # state giusto ma da un altro browser (cookie mancante o diverso): rifiutato, e lo state è bruciato
        q, cookie, _, _ = self.begin(admin)
        GOOGLE["challenge"], GOOGLE["scope"] = q["code_challenge"], q["scope"]
        path = "/api/google/callback?code=codice-buono&state=" + q["state"]
        self.assertEqual(self.raw("GET", path)[1]["Location"], "/#google-browser")
        self.assertEqual(self.raw("GET", path, cookie=cookie)[1]["Location"], "/#google-stato")
        # codice sbagliato: Google rifiuta lo scambio
        q, cookie, _, _ = self.begin(admin)
        GOOGLE["challenge"] = q["code_challenge"]
        self.assertEqual(self.raw("GET", "/api/google/callback?code=altro&state=" + q["state"], cookie=cookie)[1]["Location"], "/#google-scambio")
        # consenso negato dall'utente
        q, cookie, _, _ = self.begin(admin)
        self.assertEqual(self.raw("GET", "/api/google/callback?error=access_denied&state=" + q["state"], cookie=cookie)[1]["Location"], "/#google-negato")
        # state scaduto
        q, cookie, _, _ = self.begin(admin)
        con = db.connect()
        con.execute("UPDATE oauth_states SET expires_at=?", (int(time.time()) - 1,))
        con.close()
        self.assertEqual(self.raw("GET", "/api/google/callback?code=codice-buono&state=" + q["state"], cookie=cookie)[1]["Location"], "/#google-scaduto")
        self.assertFalse(self.call("GET", "/api/google", token=admin)[1]["connected"])

    def test_chi_puo_collegare_e_a_quali_condizioni(self):
        boss, _ = self.new_tenant("Google Permessi", "business")
        self.call("POST", "/api/users", {"email": "op@google-permessi.it", "name": "Op", "role": "member", "password": PW}, boss)
        self.assertEqual(self.call("POST", "/api/google/start", {"services": self.ALL}, self.login("op@google-permessi.it"))[0], 403)
        self.assertEqual(self.call("POST", "/api/google/start", {"services": []}, boss)[0], 400)
        os.environ["SFB_ALLOW_PLAINTEXT_TOKENS"] = "0"
        try:  # senza cifratura i token della posta non si salvano
            status, out = self.call("POST", "/api/google/start", {"services": self.ALL}, boss)
            self.assertEqual(status, 400)
            self.assertIn("cifrate", out["error"])
        finally:
            os.environ["SFB_ALLOW_PLAINTEXT_TOKENS"] = "1"
        saved = os.environ.pop("GOOGLE_CLIENT_ID")
        try:
            self.assertEqual(self.call("POST", "/api/google/start", {"services": self.ALL}, boss)[0], 400)
        finally:
            os.environ["GOOGLE_CLIENT_ID"] = saved

    def test_gmail_lettura_e_invio_dopo_approvazione(self):
        admin, _ = self.new_tenant("Google Posta", "start")
        self.connect(admin)
        out = self.chat(admin, "leggi la posta")[1]
        self.assertEqual(out["events"], [{"tool": "email_leggi_recenti", "status": "eseguito"}])
        self.assertIn("Richiesta preventivo m1", out["reply"])
        self.assertIn("NON FIDATO", out["reply"])
        out = self.chat(admin, "manda una email a cliente@example.org: il preventivo è pronto")[1]
        approval = out["events"][0]["approval_id"]
        self.assertEqual(GOOGLE["sent"], [], "nessun invio prima dell'approvazione")
        done = self.call("POST", "/api/approvals/%d/approve" % approval, {}, admin)[1]
        self.assertEqual(done["status"], "executed", done)
        self.assertEqual(len(GOOGLE["sent"]), 1)
        self.assertIn("To: cliente@example.org", GOOGLE["sent"][0])
        self.assertIn("il preventivo", GOOGLE["sent"][0])

    def test_calendario_google_richiede_approvazione_quello_interno_no(self):
        plain, _ = self.new_tenant("Calendario Interno", "start")
        out = self.chat(plain, "fissa appuntamento: visita in officina")[1]
        self.assertEqual(out["events"][0]["status"], "eseguito")
        admin, tid = self.new_tenant("Calendario Google", "start")
        self.connect(admin)
        out = self.chat(admin, "fissa appuntamento: consegna auto")[1]
        self.assertEqual(out["events"][0]["status"], "in attesa di approvazione")
        self.assertEqual(GOOGLE["events"], [])
        self.call("POST", "/api/approvals/%d/approve" % out["events"][0]["approval_id"], {}, admin)
        event = GOOGLE["events"][0]
        self.assertEqual((event["summary"], event["start"]["timeZone"]), ("consegna auto", "Europe/Rome"))
        self.assertRegex(event["start"]["dateTime"], r"^\d{4}-\d\d-\d\dT10:00:00$")
        self.assertRegex(event["end"]["dateTime"], r"T11:00:00$")
        ctx = self.ctx(tid)
        listing = tools.calendario_elenca_eventi(ctx, {"dal": "2026-11-23T00:00", "al": "2026-11-30T23:59"})
        ctx.con.close()
        self.assertIn("Riunione fornitori", listing)
        self.assertEqual(GOOGLE["last_calendar_query"]["timeMin"], "2026-11-23T00:00:00+01:00")

    def test_fogli_lettura(self):
        admin, tid = self.new_tenant("Google Fogli", "start")
        self.connect(admin)
        sheet = "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit#gid=0"
        out = self.chat(admin, "leggi questo foglio " + sheet)[1]
        self.assertEqual(out["events"], [{"tool": "fogli_leggi", "status": "eseguito"}])
        self.assertIn("Novembre | 15150", out["reply"])
        self.assertTrue(GOOGLE["last_sheet_path"].startswith("/v4/spreadsheets/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/values/"))
        ctx = self.ctx(tid)
        with self.assertRaises(connectors.ConnectorError):
            tools.fogli_leggi(ctx, {"foglio": "non-un-foglio"})
        with self.assertRaises(connectors.ConnectorError):
            tools.fogli_leggi(ctx, {"foglio": sheet, "celle": "A1/../../altro"})
        ctx.con.close()

    def test_rinnovo_automatico_del_token(self):
        admin, tid = self.new_tenant("Google Rinnovo", "start")
        self.connect(admin)
        con = db.connect()
        cfg = connectors.load(con, tid, "google")
        first = cfg["access_token"]
        cfg["expires_at"] = int(time.time()) - 10  # scaduto: va rinnovato prima della chiamata
        connectors.store_raw(con, tid, "google", cfg)
        self.assertEqual(len(google.gmail_recent(con, tid, 2)), 2)
        second = connectors.load(con, tid, "google")["access_token"]
        self.assertNotEqual(first, second)
        self.assertEqual(GOOGLE["token_calls"][-1]["grant_type"], "refresh_token")
        GOOGLE["valid"].discard(second)  # Google lo invalida prima della scadenza: 401, rinnovo e nuovo tentativo
        self.assertEqual(len(google.gmail_recent(con, tid, 2)), 2)
        self.assertNotEqual(connectors.load(con, tid, "google")["access_token"], second)
        con.close()

    def test_collegamento_revocato_segnalato_senza_blocchi(self):
        admin, tid = self.new_tenant("Google Revocato", "start")
        self.connect(admin)
        con = db.connect()
        cfg = connectors.load(con, tid, "google")
        cfg["expires_at"] = 0
        connectors.store_raw(con, tid, "google", cfg)
        con.close()
        GOOGLE["revoked"] = True
        out = self.chat(admin, "leggi la posta")[1]
        self.assertEqual(out["events"][0]["status"], "errore")
        self.assertIn("ricollegarlo", out["reply"])
        st = self.call("GET", "/api/google", token=admin)[1]
        self.assertTrue(st["connected"] and "7 giorni" in st["broken"])
        self.assertEqual(self.call("GET", "/api/me", token=admin)[1]["agent_status"]["email"], "Da collegare")
        GOOGLE["revoked"] = False
        self.assertEqual(self.connect(admin), (302, "/#google-ok"))  # ricollegando torna a funzionare
        self.assertEqual(self.chat(admin, "leggi la posta")[1]["events"][0]["status"], "eseguito")

    def test_permessi_concessi_solo_in_parte(self):
        admin, tid = self.new_tenant("Google Parziale", "start")
        only_calendar = "openid https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/calendar.events"
        self.assertEqual(self.connect(admin, granted=only_calendar), (302, "/#google-ok"))
        self.assertEqual(self.call("GET", "/api/google", token=admin)[1]["granted"], ["calendario"])
        out = self.chat(admin, "leggi la posta")[1]
        self.assertEqual(out["events"][0]["status"], "errore")
        self.assertIn("non è collegata", out["reply"])
        self.assertEqual(self.connect(admin, granted="openid"), (302, "/#google-permessi"))

    def test_scollegamento_revoca_e_cancella(self):
        admin, tid = self.new_tenant("Google Scollega", "start")
        self.connect(admin)
        self.assertEqual(self.call("DELETE", "/api/google", token=admin)[0], 200)
        self.assertEqual(GOOGLE["revocations"], ["rt-1"])
        self.assertFalse(self.call("GET", "/api/google", token=admin)[1]["connected"])
        con = db.connect()
        self.assertIsNone(con.execute("SELECT 1 FROM connectors WHERE tenant_id=? AND name='google'", (tid,)).fetchone())
        con.close()

    def test_token_cifrati_nel_database_quando_la_chiave_e_impostata(self):
        try:
            from cryptography.fernet import Fernet
        except BaseException as e:
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            self.skipTest("pacchetto cryptography non installato o non funzionante")
        admin, tid = self.new_tenant("Google Cifrato", "start")
        os.environ["SFB_SECRET_KEY"] = Fernet.generate_key().decode("ascii")
        os.environ["SFB_ALLOW_PLAINTEXT_TOKENS"] = "0"
        try:
            self.assertEqual(self.connect(admin), (302, "/#google-ok"))
            raw = sqlite3.connect(os.environ["SFB_DB"])
            stored, encrypted = raw.execute("SELECT config, encrypted FROM connectors WHERE tenant_id=? AND name='google'", (tid,)).fetchone()
            raw.close()
            self.assertEqual(encrypted, 1)
            self.assertNotIn("rt-1", stored)
            self.assertEqual(self.chat(admin, "leggi la posta")[1]["events"][0]["status"], "eseguito")
            self.call("DELETE", "/api/google", token=admin)
        finally:
            os.environ["SFB_SECRET_KEY"] = ""
            os.environ["SFB_ALLOW_PLAINTEXT_TOKENS"] = "1"


class TestAggiornamento(Base):
    def test_un_database_della_versione_precedente_viene_aggiornato_senza_perdere_dati(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "vecchio.db")
            old = sqlite3.connect(path)
            old.executescript(
                "CREATE TABLE tenants(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, package_id TEXT NOT NULL, "
                "active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);"
                "INSERT INTO tenants(name,package_id,created_at) VALUES('Cliente Storico','business','2026-11-19T10:00:00Z');")
            old.commit()
            old.close()
            con = db.connect(path)
            db.init(con)
            db.init(con)  # rilanciabile senza errori
            row = dict(con.execute("SELECT * FROM tenants").fetchone())
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            con.close()
            self.assertEqual((row["name"], row["package_id"], row["stopped"]), ("Cliente Storico", "business", 0))
            self.assertTrue({"automations", "news", "memory", "memory_chunks", "oauth_states"} <= tables)

    def test_le_tabelle_nuove_hanno_tutte_il_tenant(self):
        con = db.connect()
        for table in ("automations", "news", "memory", "memory_chunks", "oauth_states"):
            self.assertIn("tenant_id", [r[1] for r in con.execute("PRAGMA table_info(%s)" % table)], table)
        con.close()
