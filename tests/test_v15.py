"""Prove della versione 20: pagine legali e dati dell'azienda, giro completo di agenti, strumenti e pacchetti,
lavori automatici dopo una discesa di pacchetto, registrazione aperta e chiusa dal pannello."""
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
import time
import unittest

from base import Base, PW, ROOT  # per primo: mette la cartella del progetto tra quelle da cui importare

import manage
from server import agents, catalog, db, legal, ops, registration, tools
from test_v8 import STRIPE, V8

AZIENDA = {"COMPANY_NAME": "Alvea Prova S.r.l.", "COMPANY_VAT": "IT01234567890", "COMPANY_ADDRESS": "Via Roma 1, 50100 Firenze",
           "COMPANY_EMAIL": "info@alvea-prova.it", "COMPANY_HOSTING": "Hetzner, Germania"}


class PagineLegali(V8):
    def page(self, path):
        status, headers, body = self.fetch(path)
        return status, body.decode("utf-8"), headers

    def test_tre_pagine_con_i_dati_dell_azienda_presi_dal_pannello(self):
        for path, title in (("/privacy", "Informativa sulla privacy"), ("/condizioni", "Condizioni di servizio"), ("/cookie", "Cookie")):
            status, html, headers = self.page(path)
            self.assertEqual(status, 200, path)
            self.assertIn("<h1>%s</h1>" % title, html)
            self.assertIn("default-src 'self'", headers.get("Content-Security-Policy", ""))
            self.assertNotIn("style=", html, "niente stili in linea: le regole di sicurezza li bloccherebbero")
            self.assertEqual(html.count("<script"), 1, "un solo script, quello del sito")
            self.assertNotIn("{", html, "nessun segnaposto rimasto vuoto")
        self.assertIn("[da compilare: ragione sociale]", self.page("/privacy")[1])
        self.assertNotIn('class="company"', self.page("/")[1], "senza dati la riga dell'azienda non compare")
        self.assertEqual(self.save(AZIENDA)[0], 200)
        for path in ("/privacy", "/condizioni"):
            html = self.page(path)[1]
            for value in ("Alvea Prova S.r.l.", "IT01234567890", "Via Roma 1, 50100 Firenze", "info@alvea-prova.it"):
                self.assertIn(value, html, (path, value))
            self.assertNotIn("da compilare", html, path)
        self.assertIn("Hetzner, Germania", self.page("/privacy")[1])
        home = self.page("/")[1]
        self.assertIn('<p class="company">Alvea Prova S.r.l. · P. IVA IT01234567890 · Via Roma 1, 50100 Firenze · info@alvea-prova.it</p>', home)
        for link in ('href="/privacy"', 'href="/condizioni"', 'href="/cookie"'):
            self.assertIn(link, home)

    def test_quello_che_scrive_il_gestore_non_diventa_codice_nella_pagina(self):
        self.assertEqual(self.save({"COMPANY_NAME": "<script>alert(1)</script> & figli"})[0], 200)
        for path in ("/privacy", "/condizioni", "/"):
            html = self.page(path)[1]
            self.assertNotIn("<script>alert", html, path)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; &amp; figli", html, path)
        self.assertEqual(self.save({"COMPANY_EMAIL": "non un indirizzo"})[0], 400)
        self.assertEqual(self.save({"COMPANY_EMAIL": "a@b"})[0], 400)

    def test_i_modelli_citati_nell_informativa_sono_quelli_del_catalogo(self):
        html = self.page("/privacy")[1]
        for m in catalog.load()["models"]:
            if m["id"] != "locale":
                self.assertIn(m["name"] + ", di ", html)

    def test_indirizzi_sbagliati_e_sito_spento(self):
        for path in ("/privacy/", "/Privacy", "/legale", "/condizioni.html"):
            self.assertEqual(self.page(path)[0], 404, path)
        self.assertEqual(self.raw("POST", "/privacy", {})[0], 404)
        os.environ["SFB_SITE"] = "0"
        self.addCleanup(lambda: os.environ.pop("SFB_SITE", None))
        self.assertEqual(self.page("/privacy")[0], 404, "senza sito le pagine legali non sono servite da qui")
        self.assertFalse(self.call("GET", "/api/register")[1]["terms"])
        body = {"company": "Senza Sito Srl", "name": "Titolare", "email": "titolare@senza-sito.it", "password": PW, "agents": ["email"]}
        self.assertEqual(self.call("POST", "/api/register", body)[0], 200, "senza sito non c'è niente da accettare")

    def test_chi_si_registra_accetta_le_condizioni_e_resta_scritto_quale_versione(self):
        self.assertTrue(self.call("GET", "/api/register")[1]["terms"])
        body = {"company": "Accetta Srl", "name": "Titolare", "email": "titolare@accetta.it", "password": PW, "agents": ["email"]}
        for accept in (None, False, "sì", 1):
            status, out = self.call("POST", "/api/register", dict(body, accept=accept))
            self.assertEqual(status, 400, accept)
            self.assertIn("Condizioni", out["error"])
        con = db.connect()
        self.addCleanup(con.close)
        self.assertIsNone(con.execute("SELECT 1 FROM users WHERE email='titolare@accetta.it'").fetchone(), "senza accettazione non nasce niente")
        status, out = self.call("POST", "/api/register", dict(body, accept=True))
        self.assertEqual(status, 200, out)
        tid = con.execute("SELECT tenant_id FROM users WHERE email='titolare@accetta.it'").fetchone()[0]
        row = con.execute("SELECT detail FROM audit WHERE tenant_id=? AND action='condizioni_accettate'", (tid,)).fetchone()
        self.assertEqual(json.loads(row["detail"]), {"versione": legal.VERSION})

    def test_il_controllo_di_produzione_segnala_i_dati_che_mancano(self):
        find = lambda: [(s, t) for s, t in manage.production_report() if "pagine legali" in t]
        state, text = find()[0]
        self.assertEqual(state, "DA FARE")
        for label in ("ragione sociale", "partita IVA", "sede legale", "email di contatto", "fornitore e paese del server"):
            self.assertIn(label, text)
        self.assertEqual(self.save(AZIENDA)[0], 200)
        self.assertEqual(find()[0][0], "OK")

    def test_il_collaudo_controlla_che_le_pagine_legali_ci_siano(self):
        report = manage.acceptance(self.base)
        self.assertIn((True, "Pagine legali presenti: privacy, condizioni, cookie"), report)
        os.environ["SFB_SITE"] = "0"
        self.addCleanup(lambda: os.environ.pop("SFB_SITE", None))
        self.assertFalse([t for _, t in manage.acceptance(self.base) if "legali" in t], "senza sito non le cerca")

    def test_pagamenti_attivi_anche_con_i_soli_prezzi_del_pannello(self):
        line = lambda: [(s, t) for s, t in manage.production_report() if "Pagamenti con carta" in t][0]
        self.assertEqual(self.save({"STRIPE_SECRET_KEY": "sk_test_prova", "STRIPE_WEBHOOK_SECRET": "whsec_prova"})[0], 200)
        self.assertEqual(line()[0], "CONSIGLIO", "con le chiavi ma senza nessun prezzo non si può pagare")
        self.assertEqual(self.save({"PRICE_START": "49"})[0], 200)
        self.assertEqual(line()[0], "OK")


class Aspetto(Base):
    def test_le_caselle_di_spunta_non_prendono_l_aspetto_delle_caselle_di_testo(self):
        css = (ROOT / "web" / "app.css").read_text(encoding="utf-8")
        self.assertIn('.check input[type="checkbox"] { width: 18px; height: 18px; min-height: 0; padding: 0;', css)
        self.assertIn(".field .check { font-family: var(--font-display);", css)
        self.assertRegex(css, r"\.field \.check \{[^}]*text-transform: none")
        self.assertNotIn("var(--font)", css, "una variabile che non esiste lascerebbe il carattere sbagliato")
        self.assertIn(".legal ", (ROOT / "web" / "sito.css").read_text(encoding="utf-8"))

    def test_le_pagine_legali_si_leggono_anche_dopo_l_accesso(self):
        js = (ROOT / "web" / "sito.js").read_text(encoding="utf-8")
        self.assertIn('window.location.pathname === "/"', js, "solo la pagina iniziale porta chi è già entrato all'applicazione")


class CopieDiSicurezza(unittest.TestCase):
    def test_la_copia_e_integra_la_legge_solo_chi_la_fa_e_le_vecchie_spariscono(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst, copies = os.path.join(tmp, "alvea"), os.path.join(tmp, "copie")
            shutil.copytree(str(ROOT), dst, ignore=shutil.ignore_patterns("data", "storage", ".env", "__pycache__", ".git"))
            env = dict(os.environ, ALVEA_PASSWORD=PW)
            for key in ("SFB_DB", "SFB_STORAGE", "SFB_DEMO", "SFB_SECRET_KEY", "SFB_PUBLIC_URL"):
                env.pop(key, None)
            run = lambda *cmd: subprocess.run(list(cmd), cwd=dst, env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(run("bash", "deploy/installa.sh", "--prova", "--email", "gestore@copie.it").returncode, 0)
            os.makedirs(copies)
            old = os.path.join(copies, "alvea-20200101-000000.db")
            keep = os.path.join(copies, "appunti.txt")
            for path in (old, keep):
                open(path, "w").close()
                os.utime(path, (time.time() - 20 * 86400,) * 2)
            out = run("sh", "deploy/backup.sh", copies, "14")
            self.assertEqual(out.returncode, 0, out.stderr)
            made = sorted(f for f in os.listdir(copies) if f.startswith("alvea-"))
            made.sort(key=lambda f: f.endswith(".gz"))  # prima il database, poi i file
            self.assertEqual((len(made), made[0][-3:], made[1][-12:]), (2, ".db", "-file.tar.gz"), "la copia vecchia di 20 giorni è sparita, quella nuova c'è")
            self.assertTrue(os.path.exists(keep), "cancella solo le proprie copie")
            for f in made:
                self.assertEqual(stat.S_IMODE(os.stat(os.path.join(copies, f)).st_mode), 0o600, f)
            con = sqlite3.connect(os.path.join(copies, made[0]))
            self.assertEqual(con.execute("SELECT email FROM users").fetchall(), [("gestore@copie.it",)])
            con.close()
            with tarfile.open(os.path.join(copies, made[1])) as tar:
                self.assertIn(".env", tar.getnames())
            time.sleep(1.1)  # il nome della copia ha data e ora al secondo
            self.assertEqual(run("sh", "deploy/backup.sh", copies).returncode, 0, "senza giorni non cancella niente")
            self.assertEqual(len([f for f in os.listdir(copies) if f.endswith(".db")]), 2)

    def test_gli_script_del_server_sono_scritti_bene_e_spiegano_cosa_serve(self):
        for name in ("installa.sh", "aggiorna.sh", "ripristina.sh"):
            self.assertEqual(subprocess.run(["bash", "-n", str(ROOT / "deploy" / name)]).returncode, 0, name)
        self.assertEqual(subprocess.run(["sh", "-n", str(ROOT / "deploy" / "backup.sh")]).returncode, 0)
        script = (ROOT / "deploy" / "installa.sh").read_text(encoding="utf-8")
        self.assertNotIn("sudo -u", script, "su un server appena creato sudo può non esserci: si usa runuser")
        for unit in ("alvea.service", "alvea-backup.service", "alvea-backup.timer"):
            self.assertIn("deploy/" + unit, script, unit)
        self.assertIn("/var/backups/alvea 14", (ROOT / "deploy" / "alvea-backup.service").read_text(encoding="utf-8"))


class RegistrazioneDalPannello(V8):
    def field(self):
        fields = self.call("GET", "/api/admin/settings", token=self.gestore)[1]["fields"]
        return [f for f in fields if f["key"] == "SFB_REGISTRATION"][0]

    def test_aperta_e_chiusa_dal_pannello(self):
        """Nel file .env la registrazione è «aperta» o «chiusa», nel pannello è 1 o 0: prima il pannello poteva solo chiuderla."""
        is_open = lambda: self.call("GET", "/api/register")[1]["open"]
        self.assertEqual((is_open(), self.field()["value"], self.field()["source"]), (True, "1", ".env"), "«aperta» del file si legge come 1")
        self.assertEqual(self.save({"SFB_REGISTRATION": "1", "SFB_TRIAL_DAYS": "14"})[0], 200, "salvare il gruppo senza toccare la registrazione non deve dare errore")
        self.assertTrue(is_open(), "1 dal pannello vuol dire aperta")
        self.assertEqual(self.save({"SFB_REGISTRATION": "0"})[0], 200)
        self.assertEqual((is_open(), self.field()["value"], self.field()["source"]), (False, "0", "pannello"))
        self.assertEqual(self.save({"SFB_REGISTRATION": ""})[0], 200)
        self.assertTrue(is_open(), "campo vuoto: torna a valere il file")
        os.environ["SFB_REGISTRATION"] = "chiusa"
        self.addCleanup(lambda: os.environ.__setitem__("SFB_REGISTRATION", "aperta"))
        self.assertEqual((is_open(), self.field()["value"]), (False, "0"))
        self.assertEqual(self.save({"SFB_REGISTRATION": "1"})[0], 200)
        self.assertTrue(is_open(), "il pannello vale più del file")
        self.assertEqual(self.save({"SFB_REGISTRATION": "aperta"})[0], 400)


class GiroCompleto(Base):
    def test_ogni_agente_risponde_nel_suo_pacchetto_e_solo_lì(self):
        ids = [a["id"] for a in catalog.load()["agents"]]
        expected = {"start": 9, "business": 19, "completo": 28}
        for pkg, count in expected.items():
            token, _ = self.new_tenant("Matrice " + pkg, pkg)
            ceiling = set(self.call("GET", "/api/me", token=token)[1]["ceiling"])
            self.assertEqual(len(ceiling), count, pkg)
            for agent_id in ids + ["centrale"]:
                status, out = self.chat(token, "Ciao, cosa sai fare?", agent_id)
                if agent_id in ceiling or agent_id == "centrale":
                    self.assertEqual(status, 200, (pkg, agent_id, out))
                    self.assertTrue(out["reply"], (pkg, agent_id))
                else:
                    self.assertEqual(status, 403, (pkg, agent_id, out))
                    self.assertIn("non è nel pacchetto", out["error"])

    def test_i_modelli_di_ogni_pacchetto(self):
        for pkg, chatgpt in (("start", 403), ("business", 200), ("completo", 200)):
            token, _ = self.new_tenant("Modelli " + pkg, pkg)
            got = {m: self.chat(token, "Ciao", "centrale", m)[0] for m in ("kimi", "claude", "chatgpt", "locale")}
            self.assertEqual(got, {"kimi": 200, "claude": 200, "chatgpt": chatgpt, "locale": 200}, pkg)
            for bad in ("inventato", "", None, 7, ["claude"]):
                status, out = self.chat(token, "Ciao", "centrale", bad)
                self.assertEqual(status, 400, (pkg, bad, out))
                self.assertIn("Modello sconosciuto", out["error"])

    def test_ogni_strumento_regge_argomenti_mancanti_e_sbagliati(self):
        """Quello che arriva da un modello non è mai garantito: qualunque argomento deve dare un testo, mai un errore del server."""
        con = db.connect()
        self.addCleanup(con.close)
        tid = ops.create_tenant(con, "Giro strumenti", "completo", "admin@giro-strumenti.it", "Admin", PW)
        tenant = dict(con.execute("SELECT * FROM tenants WHERE id=?", (tid,)).fetchone())
        user = dict(con.execute("SELECT * FROM users WHERE tenant_id=?", (tid,)).fetchone())
        ids = [a["id"] for a in catalog.load()["agents"]]
        bad = [None, 0, -1, 10 ** 12, "", " ", "x" * 20000, [], {}, True, "2026-13-45", "'; DROP TABLE drafts;--", 3.7, ["a"], {"a": 1}]
        calls = 0
        for spec in tools.TOOLS:
            props = spec["schema"]["properties"]
            good = {p: "2026-12-01T10:00" if p in ("inizio", "fine", "dal", "al") else 3 if props[p]["type"] == "integer" else "prova" for p in props}
            for args in [good, {}, "non un oggetto", None, [1, 2]] + [dict(good, **{p: b}) for p in props for b in bad]:
                out = agents._run_tool(tools.Ctx(con, tenant, user), ids, {"name": spec["name"], "input": args}, [])
                self.assertIsInstance(out, str, (spec["name"], args))
                calls += 1
        self.assertGreater(calls, 1500)
        self.assertTrue(con.execute("SELECT 1 FROM drafts LIMIT 1").fetchone(), "la tabella delle bozze è ancora lì")
        for row in con.execute("SELECT tool, args FROM approvals WHERE tenant_id=?", (tid,)):
            args = json.loads(row["args"])
            for key in tools.BY_NAME[row["tool"]]["schema"]["required"]:
                value = args.get(key)
                self.assertTrue(value is not None and not isinstance(value, (list, dict)) and str(value).strip(), "approvazione chiesta senza «%s»: %s" % (key, row["tool"]))

    def test_nessuna_approvazione_per_una_richiesta_che_non_puo_partire(self):
        con = db.connect()
        self.addCleanup(con.close)
        tid = ops.create_tenant(con, "Giro approvazioni", "completo", "admin@giro-approvazioni.it", "Admin", PW)
        tenant = dict(con.execute("SELECT * FROM tenants WHERE id=?", (tid,)).fetchone())
        user = dict(con.execute("SELECT * FROM users WHERE tenant_id=?", (tid,)).fetchone())
        ids = [a["id"] for a in catalog.load()["agents"]]
        run = lambda name, args: agents._run_tool(tools.Ctx(con, tenant, user), ids, {"name": name, "input": args}, [])
        waiting = lambda: con.execute("SELECT COUNT(*) FROM approvals WHERE tenant_id=?", (tid,)).fetchone()[0]
        mail = {"a": "cliente@esempio.it", "oggetto": "Preventivo", "testo": "Buongiorno"}
        for args in (dict(mail, a="cliente"), dict(mail, a="a@b"), dict(mail, a="uno@esempio.it, due@esempio.it"), dict(mail, a=""), dict(mail, testo="  "), dict(mail, oggetto=None), {}):
            self.assertTrue(run("email_invia", args).startswith("Errore:"), args)
        for numero in ("abc", "123", "", "+39 333", "0" * 30):
            self.assertTrue(run("whatsapp_invia_messaggio", {"numero": numero, "testo": "Ciao"}).startswith("Errore:"), numero)
        self.assertTrue(run("telegram_invia_messaggio", {"testo": ""}).startswith("Errore:"))
        self.assertTrue(run("dev_scrivi_file", {"percorso": "a.txt"}).startswith("Errore:"))
        self.assertEqual(waiting(), 0, "nessuna di queste è arrivata davanti a un amministratore")
        self.assertIn("RICHIESTA DI APPROVAZIONE", run("email_invia", mail))
        self.assertIn("RICHIESTA DI APPROVAZIONE", run("whatsapp_invia_messaggio", {"numero": "+39 333 123 4567", "testo": "Ciao"}))
        self.assertEqual(waiting(), 2)

    def test_il_catalogo_e_coerente_con_motore_e_pannello(self):
        cat = catalog.load()
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        places = set()
        for a in cat["agents"]:
            self.assertIn(a["id"], agents.ROLES, a["id"])
            self.assertRegex(js, r"\b%s: '<" % a["icon"], "icona «%s» di %s" % (a["icon"], a["id"]))
            self.assertNotIn(tuple(a["pos"]), places, "due agenti nello stesso posto")
            places.add(tuple(a["pos"]))
            mine = [t for t in tools.TOOLS if t["agent"] == a["id"]]
            if "bozze" in a["acts"].lower():
                self.assertTrue(mine, "%s promette bozze ma non ha uno strumento per salvarle" % a["id"])
                self.assertFalse([t["name"] for t in mine if t["effect"] == "external"], "%s è «solo bozze» ma ha uno strumento che agisce fuori" % a["id"])
        for t in tools.TOOLS:
            self.assertTrue(t["agent"] is None or catalog.agent(t["agent"]), t["name"])
        seen = []
        for p in cat["packages"]:
            self.assertFalse(set(p["adds"]) & set(seen), "un agente sta in due pacchetti")
            seen += p["adds"]
            for key in ("automazioni", "documenti", "archivio_mb"):
                self.assertGreater(p["limits"][key], 0, (p["id"], key))
        self.assertEqual(sorted(seen), sorted(a["id"] for a in cat["agents"]), "ogni agente sta in un pacchetto")
        limits = [[p["limits"][k] for p in cat["packages"]] for k in ("automazioni", "documenti", "archivio_mb")]
        self.assertTrue(all(row == sorted(row) and len(set(row)) == 3 for row in limits), "ogni pacchetto dà più del precedente")


class DiscesaDiPacchetto(Base):
    def jobs(self, token):
        return self.call("GET", "/api/automations", token=token)[1]

    def make(self, token, n, agent="email"):
        status, out = self.call("POST", "/api/automations", {"name": "Lavoro %d" % n, "agent_id": agent, "model": "claude", "prompt": "Riepilogo",
                                                             "kind": "daily", "hour": 8, "minute": 0}, token)
        self.assertEqual(status, 200, out)

    def test_i_lavori_oltre_il_limite_vanno_in_pausa_e_non_si_riattivano(self):
        token, tid = self.new_tenant("Discesa Uno", "business")
        other, other_id = self.new_tenant("Discesa Due", "business")
        for n in range(6):
            self.make(token, n)
        self.make(other, 0)
        gestore = self.login("gestore@test.it")
        self.assertEqual(self.call("POST", "/api/admin/tenants/%d" % tid, {"package_id": "start"}, gestore)[0], 200)
        out = self.jobs(token)
        self.assertEqual((out["limit"], len(out["automations"]), [a["name"] for a in out["automations"] if a["active"]]), (2, 6, ["Lavoro 0", "Lavoro 1"]),
                         "restano attivi i due più vecchi, gli altri in pausa ma non cancellati")
        self.assertEqual(sum(1 for a in self.jobs(other)["automations"] if a["active"]), 1, "l'altra azienda non è toccata")
        news = json.dumps(self.call("GET", "/api/news", token=token)[1], ensure_ascii=False)
        self.assertIn("4 lavori automatici messi in pausa", news)
        paused = [a for a in out["automations"] if not a["active"]]
        status, err = self.call("POST", "/api/automations/%d" % paused[0]["id"], {"active": True}, token)
        self.assertEqual(status, 403, err)
        self.assertIn("al massimo 2", err["error"])
        first = out["automations"][0]
        self.assertEqual(self.call("POST", "/api/automations/%d" % first["id"], {"active": False}, token)[0], 200)
        self.assertEqual(self.call("POST", "/api/automations/%d" % paused[0]["id"], {"active": True}, token)[0], 200, "ne metto in pausa uno, ne riattivo un altro")
        self.assertEqual(self.call("POST", "/api/automations/%d" % paused[0]["id"], {"name": "Rinominato"}, token)[0], 200, "modificare un lavoro attivo resta possibile")
        self.assertEqual(sum(1 for a in self.jobs(token)["automations"] if a["active"]), 2)
        self.assertEqual(self.call("POST", "/api/admin/tenants/%d" % tid, {"package_id": "business"}, gestore)[0], 200)
        self.assertEqual(sum(1 for a in self.jobs(token)["automations"] if a["active"]), 2, "risalendo non riparte niente da solo")
        self.assertEqual(self.call("POST", "/api/automations/%d" % paused[1]["id"], {"active": True}, token)[0], 200)

    def test_un_agente_uscito_dal_pacchetto_non_lavora_nemmeno_da_lavoro_automatico(self):
        token, tid = self.new_tenant("Discesa Tre", "business")
        self.make(token, 0, "crm")
        self.assertEqual(self.chat(token, "aggiungi lead Mario Rossi mario@rossi.it", "crm")[0], 200)
        con = db.connect()
        self.addCleanup(con.close)
        ops.update_tenant(con, tid, package_id="start")
        self.assertEqual(self.chat(token, "elenca i lead", "crm")[0], 403)
        status, out = self.chat(token, "elenca i lead", "centrale")
        self.assertEqual((status, out["events"]), (200, []), "nemmeno passando dal Centrale")
        job = self.jobs(token)["automations"][0]
        status, out = self.call("POST", "/api/automations/%d/run" % job["id"], {}, token)
        self.assertEqual(out["status"], "errore")
        self.assertIn("non è nel pacchetto", out["output"])
        self.assertEqual(con.execute("SELECT COUNT(*) FROM leads WHERE tenant_id=?", (tid,)).fetchone()[0], 1, "i dati di prima restano")


class DiscesaConAbbonamento(V8):
    def test_se_stripe_non_risponde_tornano_attivi_anche_i_lavori(self):
        token, tid = self.new_tenant("Discesa Quattro", "business")
        for n in range(4):
            status, out = self.call("POST", "/api/automations", {"name": "Lavoro %d" % n, "agent_id": "email", "model": "claude", "prompt": "Riepilogo",
                                                                 "kind": "daily", "hour": 8, "minute": 0}, token)
            self.assertEqual(status, 200, out)
        self.assertEqual(self.save({"STRIPE_SECRET_KEY": "sk_test_prova", "STRIPE_WEBHOOK_SECRET": "whsec_prova", "PRICE_START": "49", "PRICE_BUSINESS": "99"})[0], 200)
        con = db.connect()
        self.addCleanup(con.close)
        con.execute("UPDATE tenants SET stripe_subscription='sub_D', stripe_customer='cus_D', stripe_amount=9900 WHERE id=?", (tid,))
        active = lambda: sum(1 for a in self.call("GET", "/api/automations", token=token)[1]["automations"] if a["active"])
        STRIPE["status"] = 400
        self.assertEqual(self.call("POST", "/api/billing/package", {"package_id": "start"}, token)[0], 502)
        self.assertEqual((self.call("GET", "/api/me", token=token)[1]["tenant"]["package_id"], active()), ("business", 4), "tutto come prima")
        STRIPE["status"] = 200
        self.assertEqual(self.call("POST", "/api/billing/package", {"package_id": "start"}, token)[0], 200)
        self.assertEqual(active(), 2)


# Le prove della versione 8 girano già nel loro file: qui servono solo i suoi aiuti.
for _name in [n for n in dir(V8) if n.startswith("test_")]:
    for _cls in (PagineLegali, RegistrazioneDalPannello, DiscesaConAbbonamento):
        setattr(_cls, _name, None)
del V8, _cls, _name  # altrimenti la classe rimasta nella variabile verrebbe eseguita due volte


if __name__ == "__main__":
    unittest.main()
