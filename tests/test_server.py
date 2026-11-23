"""Prove della base del server: accessi, pacchetti, aziende separate, approvazioni, registro.

Si lanciano tutte con:  python3 -m unittest discover -s tests -v
"""
import json
import os
import sqlite3
import urllib.request

from base import PW, TELEGRAM_CALLS, Base
from server import audit, auth, connectors, db, llm, ops, tools  # noqa: F401


class TestAccesso(Base):
    def test_password_sbagliata_e_giusta(self):
        status, out = self.call("POST", "/api/login", {"email": "admin@start.it", "password": "sbagliata-sbagliata"})
        self.assertEqual(status, 401)
        self.assertNotIn("token", out)
        token = self.login("admin@start.it")
        status, me = self.call("GET", "/api/me", token=token)
        self.assertEqual((status, me["tenant"]["package_id"], me["user"]["role"]), (200, "start", "admin"))

    def test_senza_token_e_dopo_uscita(self):
        self.assertEqual(self.call("GET", "/api/me")[0], 401)
        self.assertEqual(self.call("POST", "/api/chat", {"message": "ciao", "model": "claude"})[0], 401)
        token = self.login("admin@business.it")
        self.assertEqual(self.call("POST", "/api/logout", {}, token)[0], 200)
        self.assertEqual(self.call("GET", "/api/me", token=token)[0], 401)

    def test_blocco_dopo_troppi_tentativi(self):
        for _ in range(auth.MAX_FAILS):
            self.call("POST", "/api/login", {"email": "nessuno@test.it", "password": "x" * 12})
        self.assertEqual(self.call("POST", "/api/login", {"email": "nessuno@test.it", "password": "x" * 12})[0], 429)

    def test_password_non_in_chiaro_nel_database(self):
        con = db.connect()
        stored = con.execute("SELECT pw_hash FROM users WHERE email='admin@start.it'").fetchone()[0]
        con.close()
        self.assertTrue(stored.startswith("pbkdf2_sha256$"))
        self.assertNotIn(PW, stored)


class TestPacchetti(Base):
    def test_agente_fuori_pacchetto_rifiutato_dal_server(self):
        start = self.login("admin@start.it")
        status, out = self.chat(start, "mostra gli ordini", agent_id="shopify")
        self.assertEqual(status, 403)
        self.assertIn("Business", out["error"])
        self.assertEqual(self.chat(start, "prepara un messaggio", agent_id="whatsapp")[0], 403)
        business = self.login("admin@business.it")
        self.assertEqual(self.chat(business, "ciao", agent_id="shopify")[0], 200)
        self.assertEqual(self.chat(business, "ciao", agent_id="whatsapp")[0], 403)
        completo = self.login("admin@completo.it")
        self.assertEqual(self.chat(completo, "ciao", agent_id="whatsapp")[0], 200)

    def test_modello_chatgpt_solo_da_business(self):
        self.assertEqual(self.chat(self.login("admin@start.it"), "ciao", model="chatgpt")[0], 403)
        self.assertEqual(self.chat(self.login("admin@business.it"), "ciao", model="chatgpt")[0], 200)

    def test_il_modello_riceve_solo_gli_strumenti_del_pacchetto(self):
        fake = [s for s in [llm.DEMO_BASE] if s]
        self.assertTrue(fake)
        start = self.login("admin@start.it")
        status, out = self.chat(start, "aggiungi lead Mario Rossi mario@rossi.it")
        self.assertEqual(status, 200)
        self.assertEqual(out["events"], [])  # il CRM non è in Start: nessuno strumento chiamato
        self.assertEqual(self.call("GET", "/api/data/leads", token=start)[1]["rows"], [])

    def test_strumento_fuori_pacchetto_rifiutato_anche_se_il_modello_lo_chiede(self):
        con = db.connect()
        tenant = dict(con.execute("SELECT * FROM tenants WHERE package_id='start'").fetchone())
        user = dict(con.execute("SELECT * FROM users WHERE tenant_id=?", (tenant["id"],)).fetchone())
        from server import agents, catalog
        events = []
        out = agents._run_tool(tools.Ctx(con, tenant, user), catalog.entitled_agents("start"),
                               {"id": "x", "name": "crm_aggiungi_lead", "input": {"nome": "Intruso"}}, events)
        self.assertIn("non disponibile", out)
        self.assertEqual(events[0]["status"], "rifiutato")
        self.assertEqual(con.execute("SELECT COUNT(*) FROM leads WHERE tenant_id=?", (tenant["id"],)).fetchone()[0], 0)
        con.close()

    def test_piu_utenti_solo_da_business(self):
        new = {"email": "collega@start.it", "name": "Collega", "role": "member", "password": PW}
        self.assertEqual(self.call("POST", "/api/users", new, self.login("admin@start.it"))[0], 403)
        new["email"] = "collega@business.it"
        self.assertEqual(self.call("POST", "/api/users", new, self.login("admin@business.it"))[0], 200)

    def test_collegamento_di_agente_non_incluso_rifiutato(self):
        start = self.login("admin@start.it")
        self.assertEqual(self.call("PUT", "/api/connectors/shopify", {"shop_domain": "x.myshopify.com"}, start)[0], 403)


class TestDueFormatiModello(Base):
    def test_giro_completo_con_strumento_in_entrambi_i_formati(self):
        token = self.login("admin@business.it")
        for model, name in (("claude", "Anna Formato Anthropic"), ("kimi", "Bruno Formato Completions")):
            status, out = self.chat(token, "aggiungi lead " + name, model=model)
            self.assertEqual(status, 200, out)
            self.assertEqual(out["events"], [{"tool": "crm_aggiungi_lead", "status": "eseguito"}])
            self.assertIn("salvato", out["reply"])
        names = [r["name"] for r in self.call("GET", "/api/data/leads", token=token)[1]["rows"]]
        self.assertIn("Anna Formato Anthropic", names)
        self.assertIn("Bruno Formato Completions", names)

    def test_la_conversazione_continua(self):
        token = self.login("admin@business.it")
        first = self.chat(token, "ciao")[1]
        status, second = self.chat(token, "elenca i lead", conversation_id=first["conversation_id"])
        self.assertEqual((status, second["conversation_id"]), (200, first["conversation_id"]))
        conv = self.call("GET", "/api/conversations/%d" % first["conversation_id"], token=token)[1]
        self.assertEqual([m["role"] for m in conv["messages"]], ["user", "assistant", "user", "assistant"])


class TestIsolamentoAziende(Base):
    def test_dati_conversazioni_e_approvazioni_non_passano_tra_aziende(self):
        a = self.login("admin@business.it")
        b = self.login("admin@completo.it")
        out = self.chat(a, "aggiungi lead Segreto Di Business")[1]
        rows_b = self.call("GET", "/api/data/leads", token=b)[1]["rows"]
        self.assertNotIn("Segreto Di Business", [r["name"] for r in rows_b])
        self.assertNotIn("Segreto Di Business", self.chat(b, "elenca i lead")[1]["reply"])
        self.assertEqual(self.call("GET", "/api/conversations/%d" % out["conversation_id"], token=b)[0], 404)
        self.assertEqual(self.chat(b, "ciao", conversation_id=out["conversation_id"])[0], 404)
        self.call("PUT", "/api/connectors/telegram", {"bot_token": "111:AAA", "default_chat_id": "42"}, a)
        pending = self.chat(a, "manda su telegram: riservato")[1]["events"][0]["approval_id"]
        self.assertEqual(self.call("POST", "/api/approvals/%d/approve" % pending, {}, b)[0], 404)
        self.assertEqual([x for x in self.call("GET", "/api/approvals", token=b)[1]["approvals"] if x["id"] == pending], [])
        self.assertEqual(self.call("POST", "/api/approvals/%d/reject" % pending, {}, a)[0], 200)

    def test_ogni_tabella_di_dati_ha_il_tenant(self):
        con = db.connect()
        for table in ("conversations", "messages", "approvals", "audit", "connectors", "leads", "events", "drafts", "usage"):
            cols = [r[1] for r in con.execute("PRAGMA table_info(%s)" % table)]
            self.assertIn("tenant_id", cols, table)
        con.close()


class TestApprovazioni(Base):
    def test_azione_esterna_parte_solo_dopo_approvazione_di_un_amministratore(self):
        admin = self.login("admin@completo.it")
        self.call("POST", "/api/users", {"email": "operatore@completo.it", "name": "Operatore", "role": "member", "password": PW}, admin)
        member = self.login("operatore@completo.it")
        self.assertEqual(self.call("PUT", "/api/connectors/telegram", {"bot_token": "x"}, member)[0], 403)
        self.assertEqual(self.call("PUT", "/api/connectors/telegram", {"bot_token": "999:ZZZ", "default_chat_id": "777"}, admin)[0], 200)

        before = len(TELEGRAM_CALLS)
        status, out = self.chat(member, "manda su telegram: arrivo domani alle 9")
        self.assertEqual(status, 200)
        event = out["events"][0]
        self.assertEqual(event["status"], "in attesa di approvazione")
        self.assertEqual(len(TELEGRAM_CALLS), before, "non deve partire nulla prima dell'approvazione")

        approval_id = event["approval_id"]
        self.assertEqual(self.call("POST", "/api/approvals/%d/approve" % approval_id, {}, member)[0], 403)
        self.assertEqual(len(TELEGRAM_CALLS), before)
        status, done = self.call("POST", "/api/approvals/%d/approve" % approval_id, {}, admin)
        self.assertEqual((status, done["status"]), (200, "executed"))
        self.assertEqual(len(TELEGRAM_CALLS), before + 1)
        sent = TELEGRAM_CALLS[-1]
        self.assertEqual(sent["body"], {"chat_id": "777", "text": "arrivo domani alle 9"})
        self.assertIn("/bot999:ZZZ/sendMessage", sent["path"])
        # una seconda approvazione non rimanda il messaggio
        self.assertEqual(self.call("POST", "/api/approvals/%d/approve" % approval_id, {}, admin)[0], 404)
        self.assertEqual(len(TELEGRAM_CALLS), before + 1)

    def test_rifiuto_non_esegue(self):
        admin = self.login("admin@completo.it")
        self.call("PUT", "/api/connectors/telegram", {"bot_token": "999:ZZZ", "default_chat_id": "777"}, admin)
        before = len(TELEGRAM_CALLS)
        approval_id = self.chat(admin, "manda su telegram: da non inviare")[1]["events"][0]["approval_id"]
        self.assertEqual(self.call("POST", "/api/approvals/%d/reject" % approval_id, {}, admin)[1]["status"], "rejected")
        self.assertEqual(len(TELEGRAM_CALLS), before)

    def test_i_segreti_non_escono_dalle_api(self):
        admin = self.login("admin@completo.it")
        self.call("PUT", "/api/connectors/telegram", {"bot_token": "999:ZZZ-SEGRETO", "default_chat_id": "777"}, admin)
        status, out = self.call("GET", "/api/connectors", token=admin)
        self.assertEqual(status, 200)
        self.assertNotIn("ZZZ-SEGRETO", json.dumps(out))
        tg = [c for c in out["connectors"] if c["name"] == "telegram"][0]
        self.assertTrue([f for f in tg["fields"] if f["key"] == "bot_token"][0]["set"])


class TestRegistro(Base):
    def test_catena_valida_e_manomissione_rilevata(self):
        admin = self.login("admin@business.it")
        self.chat(admin, "aggiungi lead Per Il Registro")
        status, out = self.call("GET", "/api/audit/verify", token=admin)
        self.assertEqual((status, out["ok"]), (200, True))
        self.assertGreater(out["entries"], 2)
        tenant_id = self.call("GET", "/api/me", token=admin)[1]["tenant"]["id"]
        con = sqlite3.connect(os.environ["SFB_DB"])
        row_id, original = con.execute("SELECT id, detail FROM audit WHERE tenant_id=? ORDER BY id LIMIT 1 OFFSET 1", (tenant_id,)).fetchone()
        con.execute("UPDATE audit SET detail=? WHERE id=?", ('{"manomesso": true}', row_id))
        con.commit()
        out = self.call("GET", "/api/audit/verify", token=admin)[1]
        self.assertEqual((out["ok"], out["first_broken_id"]), (False, row_id))
        con.execute("UPDATE audit SET detail=? WHERE id=?", (original, row_id))
        con.commit()
        con.close()
        self.assertTrue(self.call("GET", "/api/audit/verify", token=admin)[1]["ok"])

    def test_registro_solo_per_amministratori(self):
        admin = self.login("admin@completo.it")
        self.call("POST", "/api/users", {"email": "lettore@completo.it", "name": "Lettore", "role": "member", "password": PW}, admin)
        member = self.login("lettore@completo.it")
        for path in ("/api/audit", "/api/users", "/api/connectors", "/api/usage"):
            self.assertEqual(self.call("GET", path, token=member)[0], 403, path)


class TestGestore(Base):
    def test_crea_azienda_e_cambia_pacchetto(self):
        owner = self.login("gestore@test.it")
        admin = self.login("admin@start.it")
        self.assertEqual(self.call("GET", "/api/admin/tenants", token=admin)[0], 403)
        self.assertEqual(self.chat(owner, "ciao")[0], 403)  # il gestore non entra nei dati delle aziende
        status, out = self.call("POST", "/api/admin/tenants", {"name": "Nuova Srl", "package_id": "start", "admin_email": "capo@nuova.it",
                                                              "admin_name": "Capo", "admin_password": PW}, owner)
        self.assertEqual(status, 200, out)
        capo = self.login("capo@nuova.it")
        self.assertEqual(self.chat(capo, "ciao", agent_id="crm")[0], 403)
        self.assertEqual(self.call("POST", "/api/admin/tenants/%d" % out["id"], {"package_id": "business"}, owner)[0], 200)
        self.assertEqual(self.chat(capo, "ciao", agent_id="crm")[0], 200)
        self.assertEqual(self.call("POST", "/api/admin/tenants/%d" % out["id"], {"active": False}, owner)[0], 200)
        self.assertEqual(self.call("GET", "/api/me", token=capo)[0], 401)  # azienda sospesa: fuori subito

    def test_azienda_senza_amministratore_non_viene_creata(self):
        owner = self.login("gestore@test.it")
        bad = {"name": "Monca Srl", "package_id": "start", "admin_email": "x@monca.it", "admin_name": "X", "admin_password": "corta"}
        self.assertEqual(self.call("POST", "/api/admin/tenants", bad, owner)[0], 400)
        names = [t["name"] for t in self.call("GET", "/api/admin/tenants", token=owner)[1]["tenants"]]
        self.assertNotIn("Monca Srl", names)


class TestSicurezzaStrumenti(Base):
    def test_lettura_pagine_rifiuta_reti_interne(self):
        for url in ("http://127.0.0.1:8787/api/health", "http://localhost/", "http://169.254.169.254/latest/meta-data/",
                    "http://10.0.0.1/", "file:///etc/passwd"):
            with self.assertRaises(connectors.ConnectorError, msg=url):
                connectors.fetch_page(url)

    def test_lettura_pagine_solo_indirizzi_dell_utente_o_della_ricerca(self):
        con = db.connect()
        tenant = dict(con.execute("SELECT * FROM tenants WHERE package_id='start'").fetchone())
        user = dict(con.execute("SELECT * FROM users WHERE tenant_id=?", (tenant["id"],)).fetchone())
        ctx = tools.Ctx(con, tenant, user)
        with self.assertRaises(tools.ToolError):  # indirizzo "suggerito" da un testo esterno: rifiutato
            tools.ricerca_leggi_pagina(ctx, {"url": "https://esempio-malevolo.test/?dati=lead"})
        ctx.allowed_urls.add("http://127.0.0.1:1/")
        with self.assertRaises(connectors.ConnectorError):  # permesso dall'utente ma rete interna: rifiutato
            tools.ricerca_leggi_pagina(ctx, {"url": "http://127.0.0.1:1/"})
        con.close()

    def test_ultimo_amministratore_non_si_puo_togliere(self):
        admin = self.login("admin@start.it")
        me = self.call("GET", "/api/me", token=admin)[1]["user"]
        self.assertEqual(self.call("POST", "/api/users/%d" % me["id"], {"role": "member"}, admin)[0], 400)
        self.assertEqual(self.call("POST", "/api/users/%d" % me["id"], {"active": False}, admin)[0], 400)

    def test_pagine_e_intestazioni(self):
        with urllib.request.urlopen(self.base + "/", timeout=10) as r:
            self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])
            self.assertIn(b"<title>alvea.ai</title>", r.read())
        self.assertEqual(self.call("GET", "/../manage.py")[0], 404)
        self.assertEqual(self.call("GET", "/api/non-esiste")[0], 404)

    def test_credenziali_cifrate_quando_la_chiave_e_impostata(self):
        try:
            from cryptography.fernet import Fernet
        except BaseException as e:
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            self.skipTest("pacchetto cryptography non installato o non funzionante")
        os.environ["SFB_SECRET_KEY"] = Fernet.generate_key().decode("ascii")
        try:
            con = db.connect()
            tenant_id = con.execute("SELECT id FROM tenants WHERE package_id='completo'").fetchone()[0]
            connectors.save(con, tenant_id, "ricerca", {"brave_api_key": "CHIAVE-IN-CHIARO"})
            stored = con.execute("SELECT config, encrypted FROM connectors WHERE tenant_id=? AND name='ricerca'", (tenant_id,)).fetchone()
            self.assertEqual(stored["encrypted"], 1)
            self.assertNotIn("CHIAVE-IN-CHIARO", stored["config"])
            self.assertEqual(connectors.load(con, tenant_id, "ricerca")["brave_api_key"], "CHIAVE-IN-CHIARO")
            connectors.delete(con, tenant_id, "ricerca")
            con.close()
        finally:
            os.environ["SFB_SECRET_KEY"] = ""


if __name__ == "__main__":
    unittest.main()
