"""Prove della versione 30: interfaccia, sito, email e agenti anche in inglese.

L'italiano è la lingua di partenza; l'inglese è un dizionario (i18n/en.json) applicato ai testi nostri un attimo prima che escano.
Le prove guardano tre cose: che non manchi niente, che la lingua giusta arrivi dove deve (pagine, risposte, email, agenti),
e soprattutto che quello che scrivono le persone e gli agenti non venga mai toccato."""
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

from base import PW, ROOT, Base
from server import agents, db, i18n, ops, registration

EXACT = "Annulla"       # una voce esatta del dizionario
SHAPED = "Da Mario."    # una frase che combacia con un modello
LISTED = "Email, Video"  # un elenco di voci note
WRITTEN = (EXACT, SHAPED, LISTED)


class Lingua(Base):
    def ask(self, method, path, body=None, token=None, lang=None):
        req = urllib.request.Request(self.base + path, method=method, data=json.dumps(body).encode("utf-8") if body is not None else None)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", "Bearer " + token)
        if lang:
            req.add_header("X-Lang", lang)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def login(self, email, lang=None):
        status, out = self.ask("POST", "/api/login", {"email": email, "password": PW}, lang=lang)
        self.assertEqual(status, 200, out)
        return out["token"]

    # ---------- il dizionario ----------

    def test_non_manca_nessuna_traduzione(self):
        """Lo strumento rilegge il codice (errori, tabelle, catalogo, sito, interfaccia, frasi composte) e dice che cosa non ha traduzione."""
        r = subprocess.run([sys.executable, str(ROOT / "strumenti" / "traduzioni.py"), "en"], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-2000:])
        found = dict(re.findall(r"^(\w+)\s+mancano (\d+)$", r.stdout, re.M))
        self.assertEqual(set(found), {"server", "catalogo", "tabelle", "sito", "interfaccia", "composte"}, r.stdout[-2000:])
        self.assertEqual({k: v for k, v in found.items() if v != "0"}, {}, r.stdout[-3000:])

    def test_il_dizionario_e_ben_formato(self):
        raw = json.loads((ROOT / "i18n" / "en.json").read_text(encoding="utf-8"))
        self.assertGreater(len(raw["exact"]), 1000)
        self.assertGreater(len(raw["patterns"]), 150)
        for it, en in raw["exact"].items():
            self.assertTrue(isinstance(en, str) and en.strip(), "traduzione vuota per «%s»" % it)
        for it, en in raw["patterns"]:
            holes = len(i18n.PLACEHOLDER.findall(it))
            self.assertGreater(holes, 0, "un modello senza pezzi variabili è una voce esatta: «%s»" % it)
            used = [int(n) for n, _ in re.findall(r"\{(\d+)(:t)?\}", en)]
            self.assertTrue(used and max(used) < holes, "l'inglese usa un pezzo che l'italiano non ha: «%s»" % it)
            self.assertEqual(re.sub(r"\{\d+(:t)?\}", "", en).count("{"), 0, "segnaposto scritto male in «%s»" % en)

    def test_i_pezzi_dentro_una_frase_restano_quelli_scritti_dalla_persona(self):
        """Il riassunto di un'azione da approvare deve dire esattamente quello che partirà: oggetto e testo non si traducono mai."""
        said = i18n.text("Inviare una email a mario@esempio.it, oggetto «%s»: %s" % (EXACT, EXACT), "en", note=False)
        self.assertEqual(said, "Send an email to mario@esempio.it, subject «%s»: %s" % (EXACT, EXACT))
        self.assertEqual(i18n.text("Da %s." % EXACT, "en", note=False), "By %s." % EXACT)
        self.assertEqual(i18n.text("Lavoro automatico «%s»: errore" % EXACT, "en", note=False), "Automated job «%s»: error" % EXACT, "il nome resta, l'esito è nostro e si traduce")
        self.assertEqual(i18n.text("Sta usando: ricerca web sul computer «%s»" % EXACT, "en", note=False), "Using: web search on the computer «%s»" % EXACT)
        agent = i18n.text("L'Agente Ricerca è spento: un amministratore può attivarlo nel Centro di controllo, scheda Agenti.", "en", note=False)
        self.assertIn("Research Agent", agent, "il nome di un agente è un testo nostro: dentro la frase va tradotto")
        self.assertEqual(i18n.text("Testo che nessuno ha tradotto", "en", note=False), "Testo che nessuno ha tradotto", "senza traduzione resta l'italiano, non un errore")
        self.assertEqual(i18n.text(EXACT, "it"), EXACT)
        self.assertEqual(i18n.pick("EN-us"), "en")
        for odd in (None, "", "xx", "../en", "e" * 500, 7):
            self.assertEqual(i18n.pick(odd), "it", "una lingua sconosciuta o scritta male vale italiano: %r" % (odd,))

    # ---------- i dati delle persone ----------

    def fill(self, con, tenant_id, user_id):
        """Un'azienda in cui ogni cosa scritta da una persona o da un agente è, apposta, una parola che il dizionario conosce."""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        con.execute("UPDATE tenants SET name=?, activity=? WHERE id=?", (EXACT, SHAPED, tenant_id))
        con.execute("UPDATE users SET name=? WHERE id=?", (EXACT, user_id))
        con.execute("INSERT INTO leads(tenant_id,name,email,phone,company,status,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (tenant_id, EXACT, "a@b.it", "1", LISTED, "nuovo", SHAPED, now, now))
        con.execute("INSERT INTO events(tenant_id,title,starts_at,ends_at,location,notes,created_at) VALUES(?,?,?,?,?,?,?)",
                    (tenant_id, EXACT, "2026-12-01T10:00", "2026-12-01T11:00", LISTED, SHAPED, now))
        con.execute("INSERT INTO drafts(tenant_id,channel,title,body,created_by,created_at,agent_id) VALUES(?,?,?,?,?,?,?)", (tenant_id, "email", EXACT, SHAPED, user_id, now, "email"))
        for kind, status in (("fatto", "active"), ("fatto", "proposed"), ("documento", "active")):
            cur = con.execute("INSERT INTO memory(tenant_id,kind,title,body,status,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                              (tenant_id, kind, EXACT, SHAPED, status, user_id, now, now))
            con.execute("INSERT INTO memory_chunks(tenant_id,memory_id,pos,text) VALUES(?,?,0,?)", (tenant_id, cur.lastrowid, EXACT))
        con.execute("INSERT INTO automations(tenant_id,name,agent_id,model,prompt,kind,hour,minute,weekdays,next_run_at,last_run_at,last_status,last_output,created_by,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (tenant_id, EXACT, "report", "kimi", SHAPED, "giornaliero", 9, 0, "", int(time.time()) + 9999, now, "ok", EXACT, user_id, now))
        con.execute("INSERT INTO devices(tenant_id,name,token_hash,paired_by,specs,checks,launcher_version,last_seen,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (tenant_id, EXACT, "x" * 64, user_id, json.dumps({"os": EXACT, "cpu": SHAPED}), "{}", "1.2.0", int(time.time()), now))
        con.execute("INSERT INTO jobs(tenant_id,agent_id,tool,package,place,args,status,note,result,requested_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (tenant_id, "video", "video_monta", "video", "cloud", json.dumps({"titolo": EXACT}), "fatto", EXACT, "Il lavoro sul computer non è riuscito.", user_id, now))
        con.execute("INSERT INTO media_assets(tenant_id,kind,original_name,stored_name,mime,size_bytes,sha256,uploaded_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (tenant_id, "immagine", EXACT, "a.png", "image/png", 10, "0" * 64, user_id, now))
        con.execute("INSERT INTO credit_ledger(tenant_id,kind,amount,model,note,period,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)", (tenant_id, "ricarica", 5, "", EXACT, now[:7], user_id, now))
        conv = con.execute("INSERT INTO conversations(tenant_id,user_id,agent_id,created_at) VALUES(?,?,?,?)", (tenant_id, user_id, "centrale", now)).lastrowid
        for role in ("user", "assistant"):
            con.execute("INSERT INTO messages(tenant_id,conversation_id,role,content,meta,created_at) VALUES(?,?,?,?,?,?)",
                        (tenant_id, conv, role, EXACT, json.dumps({"agent": "centrale", "model": "kimi", "events": []}), now))
        con.execute("INSERT INTO approvals(tenant_id,requested_by,agent_id,tool,args,summary,created_at) VALUES(?,?,?,?,?,?,?)",
                    (tenant_id, user_id, "telegram", "telegram_invia_messaggio", json.dumps({"testo": EXACT}), "Inviare su Telegram: " + EXACT, now))
        con.execute("INSERT INTO news(tenant_id,kind,title,body,created_at) VALUES(?,?,?,?,?)", (tenant_id, "automazione", "Lavoro automatico «%s»: fatto" % EXACT, EXACT, now))
        con.execute("UPDATE tenant_agents SET config_json=? WHERE tenant_id=? AND agent_id='email'", (json.dumps({"instructions": EXACT, "tone": EXACT}), tenant_id))
        return conv

    def test_i_dati_delle_persone_non_vengono_mai_tradotti(self):
        con = db.connect()
        tenant_id = ops.create_tenant(con, "Azienda delle parole", "completo", "parole@lingua.it", "Admin parole", PW)
        user_id = con.execute("SELECT id FROM users WHERE email='parole@lingua.it'").fetchone()["id"]
        conv = self.fill(con, tenant_id, user_id)
        con.close()
        admin, boss = self.login("parole@lingua.it"), self.login("gestore@test.it")
        paths = ["/api/me", "/api/approvals", "/api/audit", "/api/users", "/api/connectors", "/api/usage", "/api/data/leads", "/api/data/events", "/api/data/drafts",
                 "/api/tenant/agents", "/api/files", "/api/summary", "/api/office/agents/email", "/api/office/agents/video", "/api/office/agents/report", "/api/office/agents/telegram",
                 "/api/credits", "/api/billing", "/api/devices", "/api/news", "/api/automations", "/api/memory", "/api/memory/search?q=" + EXACT, "/api/google",
                 "/api/conversations/%d" % conv]
        seen, changed, ours = [], [], 0

        def walk(it, en, where):
            nonlocal ours
            if isinstance(it, dict):
                for k in it:
                    walk(it[k], en.get(k) if isinstance(en, dict) else None, where + "." + k)
            elif isinstance(it, list):
                for i, v in enumerate(it):
                    walk(v, en[i] if isinstance(en, list) and i < len(en) else None, where + "[]")
            elif isinstance(it, str):
                inside = [w for w in WRITTEN if w in it]
                if it in WRITTEN:
                    seen.append(where)
                    if en != it:
                        changed.append((where, it, en))
                elif inside:
                    seen.append(where)
                    if not isinstance(en, str) or any(w not in en for w in inside):
                        changed.append((where, it, en))
                elif en != it:
                    ours += 1

        for token, some in ((admin, paths), (boss, ["/api/admin/tenants"])):
            for path in some:
                si, it = self.ask("GET", path, token=token, lang="it")
                se, en = self.ask("GET", path, token=token, lang="en")
                self.assertEqual((si, se), (200, 200), path)
                walk(it, en, path.split("?")[0])
        self.assertEqual(changed, [], "testi scritti da persone o agenti usciti diversi in inglese")
        for must in ("/api/me.tenant.name", "/api/me.user.name", "/api/data/leads.rows[].name", "/api/data/events.rows[].title", "/api/data/drafts.rows[].title",
                     "/api/automations.automations[].name", "/api/devices.devices[].name", "/api/files.assets[].name", "/api/memory.documents[].title",
                     "/api/users.users[].name", "/api/admin/tenants.tenants[].name", "/api/approvals.approvals[].summary", "/api/news.news[].body", "/api/news.news[].title"):
            self.assertIn(must, seen, "la prova non guarda più questo punto: forse la risposta ha cambiato forma")
        self.assertGreater(ours, 150, "intanto i testi nostri (agenti, pacchetti, avvisi) devono uscire in inglese")
        news = self.ask("GET", "/api/news", token=admin, lang="en")[1]["news"]
        self.assertIn("Automated job «%s»: done" % EXACT, [n["title"] for n in news])
        approvals = self.ask("GET", "/api/approvals", token=admin, lang="en")[1]["approvals"]
        self.assertEqual(approvals[0]["summary"], "Send on Telegram: " + EXACT)

    def test_l_interfaccia_non_ritraduce_quello_che_arriva_dal_server(self):
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        for data in ("raw: me.user.name", 'class: "text", raw: m.text', "raw: n.title", "raw: n.body", "raw: a.last_output", "raw: f.body", "raw: h.text", "raw: d.name", "raw: t.name",
                     "raw: o.title", "raw: o.text", "raw: x.now", "raw: r.text"):
            self.assertIn(data, js, "questo è un dato: va mostrato com'è")
        for inline in ("asIs(a.summary)", "asIs(p.body)", "raw: j.result"):
            self.assertIn(inline, js, "un dato messo dentro una riga entra com'è")
        self.assertIn('raw: c == null ? "" : String(c)', js, "le celle delle tabelle sono dati: le parole nostre le traduce chi riempie la riga")
        self.assertIn("(ours ? tx(piece, false) : piece)", js, "anche nel browser i pezzi dentro una frase si traducono solo se segnati come nostri")
        self.assertNotIn("/Conferma prima/", js, "nessuna decisione deve dipendere dal testo di un messaggio, che cambia con la lingua")
        self.assertIn("S.loginUnverified", js)
        for line in js.split("\n"):
            if "innerHTML" in line:
                self.assertTrue("tx(" not in line and "ICONS[name]" in line, "le traduzioni entrano come testo, mai come HTML: " + line.strip()[:120])

    # ---------- dove arriva la lingua ----------

    def test_le_risposte_seguono_la_lingua_di_chi_chiede(self):
        from server import auth
        self.addCleanup(auth.reset_fails)  # i tentativi sbagliati di questa prova non devono pesare sulle altre
        wrong = {"email": "nessuno@lingua.it", "password": "sbagliata-di-sicuro"}
        italian = self.ask("POST", "/api/login", wrong)[1]["error"]
        english = self.ask("POST", "/api/login", wrong, lang="en")[1]["error"]
        self.assertNotEqual(italian, english)
        self.assertIn("password", english.lower())
        self.assertFalse(re.search(r"\b(non|sbagliat\w+|oppure)\b", english), english)
        for odd in ("xx", "../../etc", "it"):
            self.assertEqual(self.ask("POST", "/api/login", wrong, lang=odd)[1]["error"], italian, "lingua sconosciuta: italiano")
        self.assertEqual(self.ask("GET", "/api/non-esiste", lang="en")[0], 404)
        token = self.login("admin@completo.it")
        it, en = self.ask("GET", "/api/me", token=token, lang="it")[1], self.ask("GET", "/api/me", token=token, lang="en")[1]
        names = lambda me: {a["id"]: a["name"] for a in me["catalog"]["agents"]}
        self.assertEqual(names(it)["ricerca"], "Ricerca")
        self.assertEqual(names(en)["ricerca"], "Research")
        self.assertEqual(set(names(it)), set(names(en)), "cambiano i testi, non gli identificativi")
        self.assertEqual(it["tenant"]["name"], en["tenant"]["name"])
        self.assertEqual(it["agent_status"], en["agent_status"], "gli stati sono valori su cui l'interfaccia decide: li traduce lei quando li mostra")

    def test_il_sito_esiste_anche_in_inglese(self):
        from server import site
        if not site.enabled():
            self.skipTest("sito spento")
        status, _, raw = self.fetch("/")
        home = raw.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn('<html lang="it"', home)
        self.assertIn('href="/en"', home, "dalla pagina italiana si passa a quella inglese")
        for path, back in (("/en", "/"), ("/en/privacy", "/privacy"), ("/en/terms", "/condizioni"), ("/en/cookies", "/cookie")):
            status, headers, raw = self.fetch(path)
            page = raw.decode("utf-8")
            self.assertEqual(status, 200, path)
            self.assertIn('<html lang="en"', page, path)
            self.assertIn('hreflang="it"', page, path)
            self.assertIn('href="%s"' % back, page, "da %s si torna alla pagina italiana" % path)
            words = re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S)
            words = " ".join(re.findall(r">([^<>]+)<", words))
            left = re.findall(r"\b(della|degli|perché|anche|nostri|sono|questo|vengono|puoi|gli agenti)\b", words)
            self.assertEqual(left, [], "testo italiano rimasto in %s" % path)
        en = self.fetch("/en")[2].decode("utf-8")
        self.assertIn("/app?lang=en", en, "chi entra dal sito inglese trova l'applicazione in inglese")
        self.assertIn('href="/en/privacy"', en)
        self.assertEqual(self.fetch("/en/non-esiste")[0], 404)
        self.assertEqual(self.fetch("/fr")[0], 404)

    def test_il_dizionario_si_scarica_e_nient_altro(self):
        status, headers, raw = self.fetch("/i18n/en.json")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Cache-Control"), "public, max-age=300", "è pubblico e uguale per tutti: può restare in memoria qualche minuto")
        self.assertEqual(self.fetch("/app.js")[1].get("Cache-Control"), "no-store", "tutto il resto continua a non restare in memoria")
        self.assertEqual(json.loads(raw.decode("utf-8")), json.loads((ROOT / "i18n" / "en.json").read_text(encoding="utf-8")))
        for path in ("/i18n/it.json", "/i18n/xx.json", "/i18n/en.json.bak", "/i18n/", "/i18n/..%2f.env", "/i18n/../.env", "/i18n/EN.json", "/i18n/en.json/"):
            self.assertEqual(self.fetch(path)[0], 404, path)
        self.assertEqual(self.raw("POST", "/i18n/en.json", {})[0], 404)

    def test_la_lingua_scelta_resta_e_la_seguono_email_e_agenti(self):
        con = db.connect()
        ops.create_tenant(con, "Azienda inglese", "start", "inglese@lingua.it", "Admin inglese", PW)
        lang = lambda: con.execute("SELECT lang FROM users WHERE email='inglese@lingua.it'").fetchone()["lang"]
        self.assertEqual(lang(), "it")
        token = self.login("inglese@lingua.it", "en")
        self.assertEqual(lang(), "en")
        self.login("inglese@lingua.it")  # il launcher o uno script: non indicano una lingua e non cambiano quella scelta
        self.assertEqual(lang(), "en")
        self.ask("GET", "/api/me", token=token, lang="it")  # ha cambiato lingua restando dentro
        self.assertEqual(lang(), "it")
        self.ask("GET", "/api/me", token=token)
        self.assertEqual(lang(), "it")
        self.ask("GET", "/api/me", token=token, lang="en")
        self.assertEqual(lang(), "en")

        sent, keep = [], registration.send_mail
        registration.send_mail = lambda to, subject, text: sent.append((to, subject, text))
        try:
            link = "https://alvea.esempio/api/verify?token=abc123"
            body = "Per attivare l'account apri questo collegamento entro 24 ore:\n\n%s\n\nSe non hai chiesto tu la registrazione, ignora questo messaggio." % link
            registration.mail(con, "inglese@lingua.it", "Conferma il tuo indirizzo per Alvea", body)
            registration.mail(con, "admin@start.it", "Conferma il tuo indirizzo per Alvea", body)
            registration.mail(con, "sconosciuto@altrove.it", "Conferma il tuo indirizzo per Alvea", body)
        finally:
            registration.send_mail = keep
            con.close()
        self.assertEqual(sent[0][1], "Confirm your address for Alvea")
        self.assertIn("To activate your account, open this link within 24 hours:", sent[0][2])
        self.assertIn("\n\n%s\n\n" % link, sent[0][2], "il collegamento arriva identico")
        self.assertFalse(re.search(r"\b(apri|questo|collegamento)\b", sent[0][2]))
        self.assertEqual(sent[1][1:], ("Conferma il tuo indirizzo per Alvea", body), "chi usa l'italiano riceve l'italiano")
        self.assertEqual(sent[2][1:], ("Conferma il tuo indirizzo per Alvea", body), "un indirizzo che non conosciamo: italiano")

        tenant = {"name": "Azienda inglese", "id": 1, "package_id": "start"}
        english = agents.system_prompt(tenant, "centrale", ["email"], lang="en")
        italian = agents.system_prompt(tenant, "centrale", ["email"])
        self.assertIn("Rispondi in inglese", english)
        self.assertNotIn("Lingua:", italian)
        self.assertTrue(english.startswith(italian.split("\n\nLingua:")[0][:200]), "le regole restano le stesse: si aggiunge solo la lingua")

    def test_gli_avvisi_di_pagamento_arrivano_a_ognuno_nella_sua_lingua(self):
        from server import billing, config
        con = db.connect()
        tenant_id = ops.create_tenant(con, "Officina Due Lingue", "start", "italiano@duelingue.it", "Admin italiano", PW)
        ops.create_user(con, tenant_id, "english@duelingue.it", "Admin inglese", "admin", PW)
        con.execute("UPDATE users SET lang='en' WHERE email='english@duelingue.it'")
        sent, keep, before = [], registration.send_mail, dict(config.OVERRIDES)
        registration.send_mail = lambda to, subject, text: sent.append((to, subject, text))
        config.OVERRIDES["SFB_SMTP_HOST"] = "posta.esempio.it"
        try:
            to = billing.mail_admins(con, tenant_id, "Alvea: l'abbonamento scade il 30/11/2026",
                                     "L'abbonamento di Officina Due Lingue scade il 30/11/2026.\n\nDopo quella data gli agenti vanno in pausa; i dati restano al loro posto.\n\n"
                                     "Per rinnovare con carta apri la scheda Abbonamento: https://alvea.esempio/#abbonamento\n", wait=True)
        finally:
            registration.send_mail = keep
            config.OVERRIDES.clear()
            config.OVERRIDES.update(before)
            con.close()
        self.assertEqual(to, ["italiano@duelingue.it", "english@duelingue.it"])
        got = {m[0]: m for m in sent}
        self.assertEqual(got["italiano@duelingue.it"][1], "Alvea: l'abbonamento scade il 30/11/2026")
        self.assertIn("L'abbonamento di Officina Due Lingue scade il 30/11/2026.", got["italiano@duelingue.it"][2])
        self.assertEqual(got["english@duelingue.it"][1], "Alvea: the subscription expires on 30/11/2026")
        self.assertIn("The subscription of Officina Due Lingue expires on 30/11/2026.", got["english@duelingue.it"][2], "il nome dell'azienda resta com'è")
        self.assertIn("https://alvea.esempio/#abbonamento", got["english@duelingue.it"][2])
        self.assertFalse(re.search(r"\b(scade|agenti|vanno|rinnovare)\b", got["english@duelingue.it"][2]))

    def test_chi_si_registra_in_inglese_resta_in_inglese(self):
        mail = "nuova-%d@lingua.it" % int(time.time() * 1000)
        body = {"kind": "azienda", "company": "Nuova in inglese", "name": "Anna Bianchi", "email": mail, "password": PW, "agents": [], "accept": True, "lang": "en"}
        status, out = self.ask("POST", "/api/register", body, lang="en")
        self.assertEqual(status, 200, out)
        con = db.connect()
        try:
            self.assertEqual(con.execute("SELECT lang FROM users WHERE email=?", (mail,)).fetchone()["lang"], "en")
            status, out = self.ask("POST", "/api/register", dict(body, email="x" + mail, company="Altra", lang="tedesco"))
            self.assertEqual(status, 200, out)
            self.assertEqual(con.execute("SELECT lang FROM users WHERE email=?", ("x" + mail,)).fetchone()["lang"], "it")
        finally:
            con.close()
        status, out = self.ask("POST", "/api/register", dict(body, email="non un indirizzo"), lang="en")
        self.assertEqual(status, 400)
        self.assertFalse(re.search(r"\b(non|indirizzo|valido)\b", out["error"]), out["error"])

    def test_l_indirizzo_non_confermato_si_riconosce_da_un_segnale(self):
        from server import auth
        self.addCleanup(auth.reset_fails)
        con = db.connect()
        ops.create_tenant(con, "Azienda in attesa", "start", "attesa@lingua.it", "Admin attesa", PW, email_verified=0)
        con.close()
        for lang in (None, "en"):
            status, out = self.ask("POST", "/api/login", {"email": "attesa@lingua.it", "password": PW}, lang=lang)
            self.assertEqual((status, out.get("unverified")), (403, True), out)
        self.assertNotEqual(self.ask("POST", "/api/login", {"email": "attesa@lingua.it", "password": PW})[1]["error"],
                            self.ask("POST", "/api/login", {"email": "attesa@lingua.it", "password": PW}, lang="en")[1]["error"])
        self.assertNotIn("unverified", self.ask("POST", "/api/login", {"email": "attesa@lingua.it", "password": "sbagliata-di-sicuro"})[1],
                         "con la password sbagliata non si viene a sapere se l'indirizzo è in attesa di conferma")
