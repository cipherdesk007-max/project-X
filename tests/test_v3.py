"""Prove della versione 3: agenti scelti dall'azienda, registrazione autonoma, archivio dei file."""
import hashlib
import io
import os
import sqlite3
import tempfile
import time
from pathlib import Path

from base import PW, TELEGRAM_CALLS, Base
from server import catalog, db, llm, media, ops, registration, scheduler, team

PNG = b"\x89PNG\r\n\x1a\n" + b"contenuto di una immagine di prova " * 20
MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"fotogrammi finti " * 200
MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x21" + b"audio finto " * 50
N = [0]


def last_system_prompt():
    for req in reversed(llm.DEMO_SERVER.requests):
        if req["path"].endswith("/v1/messages"):
            return req["body"]["system"]
    return ""


class V3(Base):
    def register(self, **over):
        N[0] += 1
        body = {"company": "Azienda Registrata %d" % N[0], "name": "Titolare", "email": "titolare%d@registrata.it" % N[0],
                "password": PW, "agents": ["email", "crm"]}
        body.update(over)
        status, out = self.call("POST", "/api/register", body)
        return status, out, body

    def set_agent(self, token, agent_id, is_active=None, config=None):
        body = {"agent_id": agent_id}
        if is_active is not None:
            body["is_active"] = is_active
        if config is not None:
            body["config"] = config
        return self.call("POST", "/api/tenant/agents", body, token)


class TestAgentiScelti(V3):
    def test_spegnere_un_agente_lo_blocca_davvero_e_solo_per_quell_azienda(self):
        a, _ = self.new_tenant("Scelta A", "business")
        b, _ = self.new_tenant("Scelta B", "business")
        self.assertIn("crm", self.call("GET", "/api/me", token=a)[1]["entitled"])
        status, out = self.set_agent(a, "crm", is_active=False)
        self.assertEqual(status, 200, out)
        self.assertFalse([x for x in out["agents"] if x["agent_id"] == "crm"][0]["is_active"])
        # chat diretta con l'agente spento: rifiutata
        status, out = self.chat(a, "elenca i lead", agent_id="crm")
        self.assertEqual(status, 403)
        self.assertIn("spento", out["error"])
        # il centrale non riceve più i suoi strumenti: il lead non viene creato
        out = self.chat(a, "aggiungi lead Non Deve Esistere")[1]
        self.assertEqual(out["events"], [])
        self.assertEqual(self.call("GET", "/api/data/leads", token=a)[1]["rows"], [])
        me = self.call("GET", "/api/me", token=a)[1]
        self.assertNotIn("crm", me["entitled"])
        self.assertIn("crm", me["ceiling"])
        # l'altra azienda, stesso pacchetto, non è toccata
        self.assertEqual(self.chat(b, "aggiungi lead Azienda B Lavora")[1]["events"], [{"tool": "crm_aggiungi_lead", "status": "eseguito"}])
        self.assertIn("crm", self.call("GET", "/api/me", token=b)[1]["entitled"])
        # riaccendendolo torna a funzionare
        self.assertEqual(self.set_agent(a, "crm", is_active=True)[0], 200)
        self.assertEqual(self.chat(a, "ciao", agent_id="crm")[0], 200)

    def test_il_pacchetto_resta_il_tetto_massimo(self):
        admin, _ = self.new_tenant("Tetto Start", "start")
        status, out = self.set_agent(admin, "whatsapp", is_active=True)
        self.assertEqual(status, 403)
        self.assertIn("Completo", out["error"])
        self.assertEqual(self.set_agent(admin, "inesistente", is_active=True)[0], 404)
        self.assertEqual(self.set_agent(admin, "email")[0], 400)
        self.assertEqual(self.set_agent(admin, "email", is_active="sì")[0], 400)
        # più modifiche insieme: se una non è ammessa non ne passa nessuna
        status, _ = self.call("POST", "/api/tenant/agents", {"agents": [{"agent_id": "email", "is_active": False},
                                                                         {"agent_id": "shopify", "is_active": True}]}, admin)
        self.assertEqual(status, 403)
        self.assertIn("email", self.call("GET", "/api/me", token=admin)[1]["entitled"])
        self.assertEqual(self.chat(admin, "ciao", agent_id="whatsapp")[0], 403)

    def test_solo_un_amministratore_sceglie_gli_agenti(self):
        boss, _ = self.new_tenant("Scelta Ruoli", "business")
        self.call("POST", "/api/users", {"email": "op@scelta-ruoli.it", "name": "Op", "role": "member", "password": PW}, boss)
        member = self.login("op@scelta-ruoli.it")
        self.assertEqual(self.set_agent(member, "crm", is_active=False)[0], 403)
        self.assertEqual(self.call("GET", "/api/tenant/agents", token=member)[0], 200)
        self.assertEqual(self.call("POST", "/api/tenant/agents", {"agent_id": "crm", "is_active": False})[0], 401)

    def test_modello_e_utenti_seguono_gli_agenti_accesi(self):
        admin, _ = self.new_tenant("Scelta Dipendenze", "business")
        self.assertEqual(self.chat(admin, "ciao", model="chatgpt")[0], 200)
        self.set_agent(admin, "chatgpt", is_active=False)
        self.assertEqual(self.chat(admin, "ciao", model="chatgpt")[0], 403)
        self.assertNotIn("chatgpt", [m["id"] for m in self.call("GET", "/api/me", token=admin)[1]["models"]])
        self.set_agent(admin, "team", is_active=False)
        new = {"email": "nuovo@scelta-dipendenze.it", "name": "Nuovo", "role": "member", "password": PW}
        status, out = self.call("POST", "/api/users", new, admin)
        self.assertEqual(status, 403)
        self.assertIn("Agenti", out["error"])

    def test_azione_in_attesa_non_parte_se_l_agente_viene_spento(self):
        admin, _ = self.new_tenant("Scelta Approvazioni", "start")
        self.call("PUT", "/api/connectors/telegram", {"bot_token": "12:SPENTO", "default_chat_id": "5"}, admin)
        approval = self.chat(admin, "manda su telegram: non deve partire")[1]["events"][0]["approval_id"]
        self.set_agent(admin, "telegram", is_active=False)
        before = len(TELEGRAM_CALLS)
        out = self.call("POST", "/api/approvals/%d/approve" % approval, {}, admin)[1]
        self.assertEqual(out["status"], "failed")
        self.assertEqual(len(TELEGRAM_CALLS), before)

    def test_lavoro_automatico_con_agente_spento_fallisce_senza_agire(self):
        admin, tid = self.new_tenant("Scelta Automazioni", "business")
        job = {"name": "Lead", "prompt": "aggiungi lead Fantasma", "model": "claude", "kind": "interval", "every_minutes": 15, "agent_id": "crm"}
        self.assertEqual(self.call("POST", "/api/automations", job, admin)[0], 200)
        self.set_agent(admin, "crm", is_active=False)
        self.assertEqual(self.call("POST", "/api/automations", dict(job, name="Altro"), admin)[0], 403)
        con = db.connect()
        con.execute("UPDATE automations SET next_run_at=? WHERE tenant_id=?", (int(time.time()) - 5, tid))
        con.close()
        self.assertEqual(scheduler.tick(), 1)
        auto = self.call("GET", "/api/automations", token=admin)[1]["automations"][0]
        self.assertEqual(auto["last_status"], "errore")
        self.assertEqual(self.call("GET", "/api/data/leads", token=admin)[1]["rows"], [])

    def test_istruzioni_per_agente_solo_nella_propria_azienda(self):
        a, _ = self.new_tenant("Istruzioni A", "business")
        b, _ = self.new_tenant("Istruzioni B", "business")
        status, out = self.set_agent(a, "crm", config={"istruzioni": "Dai sempre del lei ai clienti."})
        self.assertEqual(status, 200, out)
        self.chat(a, "ciao", agent_id="crm")
        self.assertIn("Dai sempre del lei ai clienti.", last_system_prompt())
        self.chat(a, "ciao", agent_id="email")
        self.assertNotIn("Dai sempre del lei", last_system_prompt(), "le istruzioni di un agente non vanno a un altro agente")
        self.chat(a, "ciao")
        self.assertIn("Dai sempre del lei ai clienti.", last_system_prompt(), "il centrale le vede tutte")
        self.chat(b, "ciao", agent_id="crm")
        self.assertNotIn("Dai sempre del lei", last_system_prompt())
        self.assertEqual(self.set_agent(a, "crm", config={"altro": 1})[0], 400)
        self.assertEqual(self.set_agent(a, "crm", config={"istruzioni": "x" * (team.MAX_INSTRUCTIONS + 1)})[0], 400)
        self.set_agent(a, "crm", is_active=False)
        self.chat(a, "ciao")
        self.assertNotIn("Dai sempre del lei", last_system_prompt(), "un agente spento non porta istruzioni")

    def test_cambio_pacchetto_allarga_o_restringe_senza_perdere_le_scelte(self):
        admin, tid = self.new_tenant("Scelta Cambio", "start")
        self.set_agent(admin, "telegram", is_active=False)
        con = db.connect()
        ops.update_tenant(con, tid, package_id="business")
        con.close()
        me = self.call("GET", "/api/me", token=admin)[1]
        self.assertIn("crm", me["entitled"], "gli agenti nuovi del pacchetto partono accesi")
        self.assertNotIn("telegram", me["entitled"], "una scelta già fatta resta")
        self.set_agent(admin, "shopify", is_active=False)
        con = db.connect()
        ops.update_tenant(con, tid, package_id="start")
        con.close()
        me = self.call("GET", "/api/me", token=admin)[1]
        self.assertNotIn("crm", me["entitled"])
        self.assertEqual(self.chat(admin, "ciao", agent_id="crm")[0], 403)
        con = db.connect()
        ops.update_tenant(con, tid, package_id="business")
        con.close()
        me = self.call("GET", "/api/me", token=admin)[1]
        self.assertIn("crm", me["entitled"])
        self.assertNotIn("shopify", me["entitled"])

    def test_aziende_della_versione_precedente_tengono_tutti_i_loro_agenti(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "v2.db")
            old = sqlite3.connect(path)
            old.executescript(
                "CREATE TABLE tenants(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, package_id TEXT NOT NULL, "
                "active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, stopped INTEGER NOT NULL DEFAULT 0);"
                "CREATE TABLE users(id INTEGER PRIMARY KEY, tenant_id INTEGER, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL, "
                "role TEXT NOT NULL, pw_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);"
                "INSERT INTO tenants(name,package_id,created_at) VALUES('Cliente v2','business','2026-11-23T10:00:00Z');"
                "INSERT INTO users(tenant_id,email,name,role,pw_hash,created_at) VALUES(1,'a@v2.it','A','admin','x','2026-11-23T10:00:00Z');")
            old.commit()
            old.close()
            con = db.connect(path)
            db.init(con)
            tenant = dict(con.execute("SELECT * FROM tenants").fetchone())
            user = dict(con.execute("SELECT * FROM users").fetchone())
            self.assertEqual(team.active(con, tenant), catalog.entitled_agents("business"))
            self.assertEqual((tenant["origin"], tenant["platform_keys"], user["email_verified"]), ("gestore", 1, 1))
            con.execute("UPDATE tenant_agents SET is_active=0 WHERE agent_id='crm'")
            db.init(con)  # un secondo avvio non riaccende ciò che è stato spento
            self.assertNotIn("crm", team.active(con, tenant))
            con.close()


class TestRegistrazione(V3):
    def setUp(self):
        registration.reset_throttle()

    def test_registrazione_crea_azienda_amministratore_e_team_scelto(self):
        status, out, body = self.register(agents=["email", "crm", "whatsapp"])
        self.assertEqual(status, 200, out)
        self.assertFalse(out["verify"])
        me = self.call("GET", "/api/me", token=out["token"])[1]
        self.assertEqual((me["user"]["role"], me["user"]["email"], me["tenant"]["name"]), ("admin", body["email"], body["company"]))
        self.assertEqual(me["entitled"], ["crm", "email", "whatsapp"])
        self.assertEqual(len(me["ceiling"]), 18)
        self.assertTrue(me["tenant"]["own_keys_only"])
        self.assertEqual(self.chat(out["token"], "ciao", agent_id="shopify")[0], 403)
        self.assertEqual(self.set_agent(out["token"], "shopify", is_active=True)[0], 200)
        self.assertEqual(self.chat(out["token"], "ciao", agent_id="shopify")[0], 200)
        self.assertEqual(self.login(body["email"])[:0], "")  # la password scelta funziona
        con = db.connect()
        row = con.execute("SELECT origin, platform_keys FROM tenants WHERE id=?", (me["tenant"]["id"],)).fetchone()
        con.close()
        self.assertEqual((row["origin"], row["platform_keys"]), ("registrazione", 0))

    def test_la_nuova_azienda_non_vede_niente_delle_altre(self):
        boss = self.login("admin@business.it")
        conv = self.chat(boss, "aggiungi lead Riservato Al Cliente Storico")[1]["conversation_id"]
        _, out, _ = self.register(agents=["crm"])
        new = out["token"]
        self.assertEqual(self.call("GET", "/api/data/leads", token=new)[1]["rows"], [])
        self.assertEqual(self.call("GET", "/api/conversations/%d" % conv, token=new)[0], 404)
        self.assertNotIn("Riservato", self.chat(new, "elenca i lead")[1]["reply"])
        self.assertEqual(self.call("GET", "/api/admin/tenants", token=new)[0], 403)
        self.assertEqual([u["email"] for u in self.call("GET", "/api/users", token=new)[1]["users"]].__len__(), 1)

    def test_dati_non_validi_rifiutati(self):
        self.assertEqual(self.register(password="corta")[0], 400)
        self.assertEqual(self.register(email="non-una-email")[0], 400)
        self.assertEqual(self.register(company="")[0], 400)
        self.assertEqual(self.register(name="")[0], 400)
        self.assertEqual(self.register(agents=["inesistente"])[0], 400)
        self.assertEqual(self.register(agents="email")[0], 400)
        _, _, body = self.register()
        status, out, _ = self.register(email=body["email"])
        self.assertEqual(status, 409)
        con = db.connect()
        self.assertEqual(con.execute("SELECT COUNT(*) FROM tenants WHERE name=''").fetchone()[0], 0)
        con.close()

    def test_tetto_del_pacchetto_di_registrazione(self):
        os.environ["SFB_REGISTER_PACKAGE"] = "start"
        try:
            info = self.call("GET", "/api/register")[1]
            self.assertEqual(len(info["agents"]), 6)
            self.assertEqual(self.register(agents=["whatsapp"])[0], 403)
            status, out, _ = self.register(agents=["email"])
            self.assertEqual(status, 200)
            self.assertEqual(self.set_agent(out["token"], "whatsapp", is_active=True)[0], 403)
        finally:
            os.environ["SFB_REGISTER_PACKAGE"] = "completo"

    def test_nome_azienda_uguale_ammesso_senza_rivelare_chi_e_cliente(self):
        _, first, body = self.register(company="Autofficina Uguale")
        status, second, _ = self.register(company="Autofficina Uguale")
        self.assertEqual(status, 200, second)
        a = self.call("GET", "/api/me", token=first["token"])[1]["tenant"]
        b = self.call("GET", "/api/me", token=second["token"])[1]["tenant"]
        self.assertNotEqual(a["id"], b["id"])
        self.assertTrue(b["name"].startswith("Autofficina Uguale"))

    def test_limite_di_richieste_per_indirizzo(self):
        os.environ["SFB_REGISTER_PER_IP_HOUR"] = "3"
        try:
            codes = [self.register()[0] for _ in range(3)] + [self.register(password="corta")[0]]
            self.assertEqual(codes, [200, 200, 200, 429])
            status, out, _ = self.register()
            self.assertEqual(status, 429)
            self.assertIn("Troppe", out["error"])
        finally:
            os.environ["SFB_REGISTER_PER_IP_HOUR"] = "100000"

    def test_tetto_globale_di_registrazioni_all_ora(self):
        con = db.connect()
        done = con.execute("SELECT COUNT(*) FROM tenants WHERE origin='registrazione'").fetchone()[0]
        con.close()
        os.environ["SFB_REGISTER_PER_HOUR"] = str(done + 1)
        try:
            self.assertEqual(self.register()[0], 200)
            self.assertEqual(self.register()[0], 429)
            registration.reset_throttle()  # il tetto globale sta nel database: azzerare il contatore in memoria non lo aggira
            self.assertEqual(self.register()[0], 429)
        finally:
            os.environ["SFB_REGISTER_PER_HOUR"] = "100000"

    def test_registrazione_chiusa_e_campo_trappola(self):
        os.environ["SFB_REGISTRATION"] = "chiusa"
        try:
            self.assertEqual(self.register()[0], 403)
            self.assertFalse(self.call("GET", "/api/register")[1]["open"])
        finally:
            os.environ["SFB_REGISTRATION"] = "aperta"
        status, out, body = self.register(website="http://spam.example")
        self.assertEqual(status, 200)
        self.assertNotIn("token", out)
        con = db.connect()
        self.assertIsNone(con.execute("SELECT 1 FROM users WHERE email=?", (body["email"],)).fetchone())
        con.close()

    def test_le_aziende_registrate_non_usano_le_chiavi_della_piattaforma(self):
        _, out, _ = self.register(agents=["email"])
        token = out["token"]
        gestita = self.login("admin@start.it")
        saved = {k: os.environ.get(k) for k in ("SFB_DEMO", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL")}
        os.environ.update({"SFB_DEMO": "0", "ANTHROPIC_API_KEY": "chiave-del-gestore", "ANTHROPIC_BASE_URL": llm.DEMO_BASE})
        try:
            self.assertTrue(llm.configured("claude", {}, allow_platform=True))
            self.assertFalse(llm.configured("claude", {}, allow_platform=False))
            before = len(llm.DEMO_SERVER.requests)
            status, reply = self.chat(token, "ciao")
            self.assertEqual(status, 502)
            self.assertIn("Collegamenti", reply["error"])
            self.assertEqual(len(llm.DEMO_SERVER.requests), before, "nessuna chiamata al modello con la chiave del gestore")
            self.assertFalse(self.call("GET", "/api/me", token=token)[1]["models"][0]["configured"])
            self.assertEqual(self.chat(gestita, "ciao")[0], 200, "un'azienda creata dal gestore continua a usarle")
            # con la propria chiave l'azienda registrata lavora
            self.assertEqual(self.call("PUT", "/api/connectors/modelli", {"anthropic_api_key": "chiave-del-cliente"}, token)[0], 200)
            self.assertEqual(self.chat(token, "ciao")[0], 200)
            # il gestore può concedere le chiavi della piattaforma
            owner = self.login("gestore@test.it")
            tid = self.call("GET", "/api/me", token=token)[1]["tenant"]["id"]
            self.call("DELETE", "/api/connectors/modelli", token=token)
            self.assertEqual(self.chat(token, "ciao")[0], 502)
            self.assertEqual(self.call("POST", "/api/admin/tenants/%d" % tid, {"platform_keys": True}, owner)[0], 200)
            self.assertEqual(self.chat(token, "ciao")[0], 200)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


class TestVerificaEmail(V3):
    def setUp(self):
        registration.reset_throttle()
        self.sent = []
        self._real = registration.send_mail
        registration.send_mail = lambda to, subject, text: self.sent.append({"to": to, "subject": subject, "text": text})
        os.environ["SFB_SMTP_HOST"] = "smtp.esempio.test"

    def tearDown(self):
        registration.send_mail = self._real
        os.environ["SFB_SMTP_HOST"] = ""

    def link_token(self, mail):
        return mail["text"].split("/api/verify?token=")[1].split()[0]

    def test_con_smtp_configurato_si_entra_solo_dopo_la_conferma(self):
        self.assertTrue(self.call("GET", "/api/register")[1]["verify_email"])
        status, out, body = self.register()
        self.assertEqual((status, out["verify"], out["sent"]), (200, True, True))
        self.assertNotIn("token", out)
        self.assertEqual(self.sent[-1]["to"], body["email"])
        self.assertIn(self.base + "/api/verify?token=", self.sent[-1]["text"])
        status, reply = self.call("POST", "/api/login", {"email": body["email"], "password": PW})
        self.assertEqual(status, 403)
        self.assertIn("Conferma", reply["error"])
        self.assertEqual(self.call("POST", "/api/login", {"email": body["email"], "password": "password-sbagliata"})[0], 401)
        self.assertEqual(self.raw("GET", "/api/verify?token=inventato")[1]["Location"], "/#verifica-errore")
        token = self.link_token(self.sent[-1])
        self.assertEqual(self.raw("GET", "/api/verify?token=" + token)[1]["Location"], "/#verifica-ok")
        self.assertEqual(self.raw("GET", "/api/verify?token=" + token)[1]["Location"], "/#verifica-errore", "vale una volta sola")
        self.assertEqual(self.call("GET", "/api/me", token=self.login(body["email"]))[0], 200)

    def test_collegamento_scaduto_e_nuovo_invio(self):
        _, _, body = self.register()
        first = self.link_token(self.sent[-1])
        con = db.connect()
        con.execute("UPDATE email_tokens SET expires_at=?", (int(time.time()) - 1,))
        con.close()
        self.assertEqual(self.raw("GET", "/api/verify?token=" + first)[1]["Location"], "/#verifica-errore")
        n = len(self.sent)
        self.assertEqual(self.call("POST", "/api/register/resend", {"email": body["email"]}), (200, {"ok": True}))
        self.assertEqual(len(self.sent), n + 1)
        self.assertEqual(self.call("POST", "/api/register/resend", {"email": "sconosciuto@altrove.it"}), (200, {"ok": True}))
        self.assertEqual(len(self.sent), n + 1, "nessun messaggio a indirizzi non registrati, ma stessa risposta")
        self.assertEqual(self.raw("GET", "/api/verify?token=" + self.link_token(self.sent[-1]))[1]["Location"], "/#verifica-ok")

    def test_una_registrazione_mai_confermata_non_blocca_l_indirizzo_per_sempre(self):
        _, _, body = self.register(email="conteso@registrata.it")
        self.assertEqual(self.register(email="conteso@registrata.it")[0], 409, "finché il collegamento è valido l'indirizzo è occupato")
        con = db.connect()
        con.execute("UPDATE email_tokens SET expires_at=?", (int(time.time()) - 1,))
        old_tenant = con.execute("SELECT tenant_id FROM users WHERE email='conteso@registrata.it'").fetchone()[0]
        con.close()
        status, out, _ = self.register(email="conteso@registrata.it")
        self.assertEqual(status, 200, out)
        con = db.connect()
        new_tenant = con.execute("SELECT tenant_id FROM users WHERE email='conteso@registrata.it'").fetchone()[0]
        self.assertNotEqual(new_tenant, old_tenant, "il numero della vecchia azienda non viene riusato")
        self.assertEqual(con.execute("SELECT active FROM tenants WHERE id=?", (old_tenant,)).fetchone()[0], 0)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM users WHERE tenant_id=? AND active=1", (old_tenant,)).fetchone()[0], 0)
        con.close()
        self.assertEqual(self.raw("GET", "/api/verify?token=" + self.link_token(self.sent[-1]))[1]["Location"], "/#verifica-ok")
        self.assertEqual(self.call("GET", "/api/me", token=self.login("conteso@registrata.it"))[0], 200)


class TestArchivio(V3):
    def test_file_isolati_tra_aziende_su_api_e_su_disco(self):
        a, ta = self.new_tenant("Archivio A", "start")
        b, tb = self.new_tenant("Archivio B", "start")
        status, asset = self.upload(a, "logo.png", PNG)
        self.assertEqual(status, 200, asset)
        self.assertEqual((asset["kind"], asset["mime"], asset["size_bytes"], asset["name"]), ("immagine", "image/png", len(PNG), "logo.png"))
        root = Path(os.environ["SFB_STORAGE"])
        files = [p for p in (root / str(ta)).iterdir()]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_bytes(), PNG)
        self.assertRegex(files[0].name, r"^[0-9a-f]{32}\.png$")
        self.assertFalse((root / str(tb)).exists() and list((root / str(tb)).iterdir()))
        # l'altra azienda non lo vede, non lo cancella, non ne ottiene il collegamento
        self.assertEqual(self.call("GET", "/api/files", token=b)[1]["assets"], [])
        self.assertEqual(self.call("DELETE", "/api/files/%d" % asset["id"], token=b)[0], 404)
        self.assertTrue(files[0].exists())
        # il contenuto si apre solo con il collegamento firmato per QUEL file
        status, headers, data = self.fetch(asset["url"])
        self.assertEqual((status, headers["Content-Type"], data), (200, "image/png", PNG))
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.fetch("/api/files/%d/content" % asset["id"])[0], 403)
        _, other = self.upload(b, "altro.png", PNG + b"diverso")
        swapped = asset["url"].replace("/api/files/%d/" % asset["id"], "/api/files/%d/" % other["id"])
        self.assertEqual(self.fetch(swapped)[0], 403, "la firma di un file non apre un altro file")
        self.assertEqual(self.fetch(asset["url"][:-1] + ("0" if asset["url"][-1] != "0" else "1"))[0], 403)
        self.assertEqual(self.fetch(asset["url"].replace("?e=", "?e=1"))[0], 403)
        self.assertEqual(self.fetch("/api/files/999999/content?e=9999999999&s=" + "0" * 40)[0], 403)
        self.assertEqual(self.call("GET", "/api/files")[0], 401)

    def test_collegamento_scaduto_e_azienda_sospesa(self):
        admin, tid = self.new_tenant("Archivio Scadenza", "start")
        _, asset = self.upload(admin, "a.png", PNG)
        past = int(time.time()) - 5
        self.assertEqual(self.fetch("/api/files/%d/content?e=%d&s=%s" % (asset["id"], past, media._sign(asset["id"], tid, past)))[0], 403)
        self.assertEqual(self.fetch(asset["url"])[0], 200)
        con = db.connect()
        ops.update_tenant(con, tid, active=False)
        con.close()
        self.assertEqual(self.fetch(asset["url"])[0], 403, "azienda sospesa: i collegamenti smettono di funzionare")

    def test_video_a_pezzi_con_range(self):
        admin, _ = self.new_tenant("Archivio Video", "start")
        _, asset = self.upload(admin, "clip.mp4", MP4)
        self.assertEqual((asset["kind"], asset["mime"]), ("video", "video/mp4"))
        status, headers, data = self.fetch(asset["url"], {"Range": "bytes=10-109"})
        self.assertEqual((status, data, headers["Content-Range"]), (206, MP4[10:110], "bytes 10-109/%d" % len(MP4)))
        status, headers, data = self.fetch(asset["url"], {"Range": "bytes=-50"})
        self.assertEqual((status, data), (206, MP4[-50:]))
        status, headers, data = self.fetch(asset["url"], {"Range": "bytes=100-"})
        self.assertEqual((status, data), (206, MP4[100:]))
        self.assertEqual(self.fetch(asset["url"], {"Range": "bytes=%d-" % (len(MP4) + 5)})[0], 416)
        _, audio = self.upload(admin, "voce.mp3", MP3)
        self.assertEqual((audio["kind"], audio["mime"]), ("audio", "audio/mpeg"))

    def test_tipi_non_ammessi_e_contenuto_che_non_corrisponde(self):
        admin, tid = self.new_tenant("Archivio Tipi", "start")
        for name, data in (("pagina.html", b"<script>alert(1)</script>"), ("disegno.svg", b"<svg onload=alert(1)>"),
                           ("programma.exe", b"MZ\x90\x00"), ("senzaestensione", PNG), ("finto.png", b"<script>alert(1)</script>"),
                           ("finto.mp4", PNG), ("doppia.png.html", PNG)):
            status, out = self.upload(admin, name, data)
            self.assertEqual(status, 415, (name, out))
        self.assertEqual(self.upload(admin, "vuoto.png", b"")[0], 400)
        self.assertEqual(self.upload(admin, "a.png", PNG, field="altro")[0], 400)
        self.assertEqual(self.post_raw("/api/files/upload", b'{"file":"x"}', "application/json", admin)[0], 400)
        self.assertEqual(self.upload(None, "a.png", PNG)[0], 401)
        self.assertEqual(self.upload(self.login("gestore@test.it"), "a.png", PNG)[0], 403)
        folder = Path(os.environ["SFB_STORAGE"]) / str(tid)
        self.assertEqual([p.name for p in folder.iterdir()] if folder.exists() else [], [], "niente resta sul disco dopo un rifiuto")
        self.assertEqual(self.call("GET", "/api/files", token=admin)[1]["assets"], [])

    def test_il_nome_del_file_non_diventa_mai_un_percorso(self):
        admin, tid = self.new_tenant("Archivio Percorsi", "start")
        root = Path(os.environ["SFB_STORAGE"])
        before = {p for p in root.rglob("*")}
        for name in ("../../fuori.png", "..\\\\..\\\\fuori.png", "/etc/cron.d/fuori.png", "C:\\\\Windows\\\\fuori.png"):
            status, out = self.upload(admin, name, PNG + name.encode())
            self.assertEqual((status, out["name"]), (200, "fuori.png"), name)
        new = {p for p in root.rglob("*")} - before
        self.assertTrue(new and all(p.parent == root / str(tid) for p in new if p.is_file()), new)
        self.assertFalse((root.parent / "fuori.png").exists())
        con = db.connect()
        for r in con.execute("SELECT stored_name FROM media_assets WHERE tenant_id=?", (tid,)):
            self.assertRegex(r["stored_name"], r"^[0-9a-f]{32}\.png$")
        con.close()

    def test_limite_per_file_e_quota_del_pacchetto(self):
        admin, tid = self.new_tenant("Archivio Limiti", "start")
        os.environ["SFB_UPLOAD_MAX_MB"] = "1"
        try:
            status, out = self.upload(admin, "grande.png", PNG + b"\x00" * (media.MB + 10))
            self.assertEqual(status, 413, out)
            self.assertIn("1 MB", out["error"])
            # dichiarato enorme: rifiutato dalla sola dimensione, senza leggerlo
            status, out = self.upload(admin, "enorme.png", PNG + b"\x00" * (3 * media.MB))
            self.assertEqual(status, 413)
        finally:
            os.environ["SFB_UPLOAD_MAX_MB"] = "200"
        self.assertEqual(self.upload(admin, "ok.png", PNG)[0], 200)
        real = catalog.limit
        catalog.limit = lambda package_id, key: 1 if key == "archivio_mb" else real(package_id, key)  # quota di 1 MB
        try:
            self.assertEqual(self.upload(admin, "meta.png", PNG + b"\x01" * (700 * 1024))[0], 200)
            status, out = self.upload(admin, "troppo.png", PNG + b"\x02" * (700 * 1024))
            self.assertEqual(status, 413, out)
            self.assertIn("esaurito", out["error"])
        finally:
            catalog.limit = real
        info = self.call("GET", "/api/files", token=admin)[1]
        self.assertEqual(len(info["assets"]), 2)
        self.assertEqual(info["used_bytes"], sum(a["size_bytes"] for a in info["assets"]))
        folder = Path(os.environ["SFB_STORAGE"]) / str(tid)
        self.assertEqual(len(list(folder.iterdir())), 2, "i caricamenti rifiutati non lasciano file")
        self.assertEqual(self.call("POST", "/api/chat", {"message": "x" * 1100000, "model": "claude"}, admin)[0], 413,
                         "le altre rotte restano limitate a 1 MB di JSON")

    def test_file_grande_letto_a_pezzi_e_arrivato_intatto(self):
        admin, tid = self.new_tenant("Archivio Grande", "business")
        data = MP4 + os.urandom(1024) * (24 * 1024)  # circa 24 MB
        status, asset = self.upload(admin, "lungo.mp4", data, extra_fields={"nota": "campo in più, da ignorare"})
        self.assertEqual((status, asset["size_bytes"]), (200, len(data)))
        folder = Path(os.environ["SFB_STORAGE"]) / str(tid)
        stored = [p for p in folder.iterdir()][0]
        self.assertEqual(hashlib.sha256(stored.read_bytes()).hexdigest(), hashlib.sha256(data).hexdigest())
        status, again = self.upload(admin, "copia.mp4", data)
        self.assertEqual((status, again["duplicate"], again["id"]), (200, True, asset["id"]))
        self.assertEqual(len(list(folder.iterdir())), 1, "lo stesso file non occupa spazio due volte")

    def test_lettore_multipart_con_confini_spezzati_e_contenuto_insidioso(self):
        boundary = b"XyZ"
        tricky = PNG + b"\r\n--Xy\r\n-\r\n--" + b"\r" * 7 + b"\r\n--XY a--XyZ--\n--XyZ\r" + bytes(range(256)) * 3
        raw = (b"preambolo da ignorare\r\n--XyZ\r\nContent-Disposition: form-data; name=\"nota\"\r\n\r\nciao\r\n"
               b"--XyZ\r\nContent-Disposition: form-data; name=\"file\"; filename=\"t.png\"\r\nContent-Type: image/png\r\n\r\n"
               + tricky + b"\r\n--XyZ--\r\nepilogo")
        for chunk in (1, 2, 3, 5, 7, 16, 64, 4096):
            sink = io.BytesIO()
            name, size, sha, head = media.parse_multipart(media.Body(io.BytesIO(raw), len(raw)), boundary, lambda: sink, 10 ** 6, chunk=chunk)
            self.assertEqual((name, size, sink.getvalue()), ("t.png", len(tricky), tricky), "pezzi da %d byte" % chunk)
            self.assertEqual(sha, hashlib.sha256(tricky).hexdigest())
        with self.assertRaises(media.UploadError):  # corpo troncato
            media.parse_multipart(media.Body(io.BytesIO(raw[:-30]), len(raw) - 30), boundary, io.BytesIO, 10 ** 6, chunk=16)
        with self.assertRaises(media.UploadError):  # oltre il limite
            media.parse_multipart(media.Body(io.BytesIO(raw), len(raw)), boundary, io.BytesIO, 100, chunk=16)
        self.assertIsNone(media.parse_multipart(media.Body(io.BytesIO(b"--XyZ--\r\n"), 9), boundary, io.BytesIO, 100))
        # Se il confine compare dentro il contenuto il corpo è ambiguo: va rifiutato, non tagliato in silenzio.
        bad = raw.replace(tricky, PNG + b"\r\n--XyZ-dentro-il-file" + PNG)
        with self.assertRaises(media.UploadError):
            media.parse_multipart(media.Body(io.BytesIO(bad), len(bad)), boundary, io.BytesIO, 10 ** 6, chunk=16)

    def test_chi_puo_rimuovere(self):
        boss, tid = self.new_tenant("Archivio Ruoli", "business")
        self.call("POST", "/api/users", {"email": "op@archivio-ruoli.it", "name": "Op", "role": "member", "password": PW}, boss)
        member = self.login("op@archivio-ruoli.it")
        _, mine = self.upload(member, "mio.png", PNG + b"1")
        _, theirs = self.upload(boss, "capo.png", PNG + b"2")
        self.assertEqual(len(self.call("GET", "/api/files", token=member)[1]["assets"]), 2, "nell'azienda i file li vedono tutti")
        self.assertEqual(self.call("DELETE", "/api/files/%d" % theirs["id"], token=member)[0], 403)
        self.assertEqual(self.call("DELETE", "/api/files/%d" % mine["id"], token=member)[0], 200)
        self.assertEqual(self.call("DELETE", "/api/files/%d" % theirs["id"], token=boss)[0], 200)
        folder = Path(os.environ["SFB_STORAGE"]) / str(tid)
        self.assertEqual(list(folder.iterdir()), [])
        self.assertEqual(self.fetch(theirs["url"])[0], 403, "un file rimosso non si apre più")
        actions = [e["action"] for e in self.call("GET", "/api/audit", token=boss)[1]["entries"]]
        self.assertIn("file_caricato", actions)
        self.assertIn("file_rimosso", actions)

    def test_tabelle_nuove_con_il_tenant(self):
        con = db.connect()
        for table in ("tenant_agents", "media_assets"):
            self.assertIn("tenant_id", [r[1] for r in con.execute("PRAGMA table_info(%s)" % table)], table)
        con.close()
