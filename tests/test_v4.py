"""Prove della versione 4: stati in tempo reale degli agenti e dati per la vista Ufficio 3D."""
import re
import sqlite3
import time
import unittest

from base import PW, ROOT, Base
from server import agents, app, catalog, db, live, llm, office, ops

STATES = {"fermo", "lavora", "monta", "ragiona", "attesa", "da_collegare", "pronto"}


class V4(Base):
    def setUp(self):
        live.reset()

    def office(self, token):
        status, out = self.call("GET", "/api/summary?office=1", token=token)
        self.assertEqual(status, 200, out)
        return out["office"]

    def states(self, token):
        o = self.office(token)
        out = {a["id"]: a["state"] for a in o["agents"]}
        out["centrale"] = o["central"]["state"]
        return out

    def detail(self, token, agent_id):
        return self.call("GET", "/api/office/agents/" + agent_id, token=token)

    def watch(self, tokens):
        """Fotografa lo stato dell'ufficio di più aziende mentre il modello risponde e mentre uno strumento lavora."""
        shots = {"modello": [], "strumento": []}
        real_chat, real_pause = llm.chat, live.demo_pause

        def chat(*a, **k):
            shots["modello"].append([self.office(t) for t in tokens])
            return real_chat(*a, **k)

        def pause():
            shots["strumento"].append([self.office(t) for t in tokens])

        llm.chat, live.demo_pause = chat, pause
        self.addCleanup(lambda: (setattr(llm, "chat", real_chat), setattr(live, "demo_pause", real_pause)))
        return shots

    @staticmethod
    def of(office_snapshot, agent_id):
        if agent_id == "centrale":
            return office_snapshot["central"]
        return [a for a in office_snapshot["agents"] if a["id"] == agent_id][0]

    # ---------- stati in tempo reale ----------
    def test_ragiona_e_lavora_si_vedono_solo_mentre_succede_e_solo_nella_propria_azienda(self):
        a, _ = self.new_tenant("Ufficio A", "completo")
        b, _ = self.new_tenant("Ufficio B", "completo")
        shots = self.watch([a, b])
        status, out = self.chat(a, "aggiungi lead Tizio Caio")
        self.assertEqual(status, 200, out)
        mine, other = shots["modello"][0]
        self.assertEqual(self.of(mine, "centrale")["state"], "ragiona")
        self.assertEqual(self.of(mine, "crm")["state"], "pronto", "il CRM non è ancora stato chiamato")
        self.assertEqual(self.of(other, "centrale")["state"], "pronto", "l'altra azienda non deve vedere niente")
        mine, other = shots["strumento"][0]
        self.assertEqual(self.of(mine, "crm")["state"], "lavora")
        self.assertIn("crm aggiungi lead", self.of(mine, "crm")["detail"])
        self.assertEqual(self.of(mine, "centrale")["state"], "ragiona", "il Centrale aspetta il risultato")
        self.assertEqual(self.of(other, "crm")["state"], "pronto")
        self.assertEqual(len(shots["modello"]), 2, "dopo lo strumento il modello viene richiamato")
        self.assertEqual(self.of(shots["modello"][1][0], "crm")["state"], "pronto", "finito lo strumento, il CRM torna fermo")
        after = self.states(a)
        self.assertNotIn("ragiona", after.values())
        self.assertNotIn("lavora", after.values())
        self.assertEqual(live.snapshot(1), {})

    def test_chat_diretta_con_un_agente_lo_mostra_al_lavoro(self):
        a, _ = self.new_tenant("Ufficio Diretto", "completo")
        shots = self.watch([a])
        self.assertEqual(self.chat(a, "aggiungi lead Sempronio", agent_id="crm")[0], 200)
        self.assertEqual(self.of(shots["modello"][0][0], "crm")["state"], "ragiona")
        self.assertEqual(self.of(shots["modello"][0][0], "centrale")["state"], "pronto")
        self.assertEqual(self.of(shots["strumento"][0][0], "crm")["state"], "lavora", "al lavoro prevale su sta ragionando")

    def test_errore_del_modello_non_lascia_l_agente_al_lavoro(self):
        a, tid = self.new_tenant("Ufficio Errore", "completo")
        real = llm.chat

        def broken(*args, **kw):
            raise llm.LLMError("modello irraggiungibile")

        llm.chat = broken
        self.addCleanup(lambda: setattr(llm, "chat", real))
        self.assertGreaterEqual(self.chat(a, "ciao")[0], 400)
        self.assertEqual(live.snapshot(tid), {})
        self.assertEqual(self.states(a)["centrale"], "pronto")

    def test_richiesta_rimasta_appesa_scade(self):
        run = live.begin(987, "crm")
        self.assertEqual(live.snapshot(987)["crm"]["phase"], "ragiona")
        live._runs[987][run]["since"] = time.time() - live.STALE_SECONDS - 1
        self.assertEqual(live.snapshot(987), {})

    def test_lavoro_automatico_si_vede_come_tale(self):
        a, _ = self.new_tenant("Ufficio Automatico", "completo")
        status, out = self.call("POST", "/api/automations", {"name": "Riepilogo", "prompt": "fammi un riepilogo", "model": "claude",
                                                             "kind": "interval", "every_minutes": 15, "agent_id": "report"}, a)
        self.assertEqual(status, 200, out)
        aid = out["id"]
        shots = self.watch([a])
        self.assertEqual(self.call("POST", "/api/automations/%d/run" % aid, {}, a)[0], 200)
        seen = self.of(shots["modello"][0][0], "report")
        self.assertEqual(seen["state"], "ragiona")
        self.assertIn("lavoro automatico", seen["detail"])
        status, d = self.detail(a, "report")
        self.assertTrue(any("Lavoro automatico «Riepilogo»: fatto" in r["text"] for r in d["log"]), d["log"])

    # ---------- stati che durano ----------
    def test_attesa_di_approvazione_da_collegare_e_fermo(self):
        a, _ = self.new_tenant("Ufficio Stati", "completo")
        b, _ = self.new_tenant("Ufficio Stati Altra", "completo")
        before = self.states(a)
        self.assertEqual(before["telegram"], "da_collegare")
        self.assertEqual(before["crm"], "pronto")
        self.assertTrue(set(before.values()) <= STATES)
        status, out = self.chat(a, "invia a cliente@esempio.it: buongiorno")
        self.assertEqual(status, 200, out)
        approval = [e["approval_id"] for e in out["events"] if e.get("approval_id")][0]
        o = self.office(a)
        self.assertEqual(self.of(o, "email")["state"], "attesa", "l'attesa prevale su da collegare")
        self.assertEqual(self.of(o, "email")["pending"], 1)
        self.assertEqual(self.states(b)["email"], "da_collegare", "l'altra azienda non ha richieste")
        self.assertEqual(self.call("POST", "/api/stop", {"active": True}, a)[0], 200)
        self.assertEqual(set(self.states(a).values()), {"fermo"}, "l'arresto prevale su tutto, Centrale compreso")
        self.assertNotIn("fermo", self.states(b).values())
        self.assertEqual(self.call("POST", "/api/stop", {"active": False}, a)[0], 200)
        self.assertEqual(self.states(a)["email"], "attesa")
        self.assertEqual(self.call("POST", "/api/approvals/%d/reject" % approval, {}, a)[0], 200)
        self.assertEqual(self.states(a)["email"], "da_collegare")

    def test_solo_gli_agenti_accesi_hanno_una_postazione(self):
        a, _ = self.new_tenant("Ufficio Scelti", "completo")
        b, _ = self.new_tenant("Ufficio Scelti Altra", "completo")
        s, _ = self.new_tenant("Ufficio Piccolo", "start")
        every = {x["id"] for x in catalog.load()["agents"]}
        self.assertEqual({x["id"] for x in self.office(a)["agents"]}, every)
        self.assertEqual({x["id"] for x in self.office(s)["agents"]}, set(catalog.entitled_agents("start")))
        for x in self.office(a)["agents"]:
            self.assertTrue(x["station"] and x["name"] and re.match(r"^#[0-9a-f]{6}$", x["tone"]), x)
        self.assertEqual(self.call("POST", "/api/tenant/agents", {"agent_id": "crm", "is_active": False}, a)[0], 200)
        self.assertNotIn("crm", {x["id"] for x in self.office(a)["agents"]})
        self.assertIn("crm", {x["id"] for x in self.office(b)["agents"]}, "la scelta vale solo per quell'azienda")
        self.assertEqual(self.detail(a, "crm")[0], 404)
        self.assertEqual(self.detail(b, "crm")[0], 200)
        self.assertEqual(self.detail(s, "crm")[0], 404, "fuori dal pacchetto")
        self.assertEqual(self.detail(a, "inesistente")[0], 404)
        self.assertEqual(self.detail(a, "centrale")[0], 200)
        self.assertEqual(self.call("GET", "/api/office/agents/crm")[0], 401)
        self.assertEqual(self.call("GET", "/api/summary?office=1")[0], 401)
        self.assertNotIn("office", self.call("GET", "/api/summary", token=a)[1], "senza richiesta esplicita il riepilogo resta leggero")

    # ---------- postazione: attività, elaborati, richieste ----------
    def test_elaborati_e_attivita_sono_dell_agente_giusto_e_dell_azienda_giusta(self):
        a, tid = self.new_tenant("Ufficio Elaborati", "completo")
        b, _ = self.new_tenant("Ufficio Elaborati Altra", "completo")
        self.assertEqual(self.chat(a, "salva bozza: testo riservato di A")[0], 200)
        self.assertEqual(self.chat(a, "aggiungi lead Cliente Riservato")[0], 200)
        self.assertEqual(self.chat(a, "fissa appuntamento: consegna riservata")[0], 200)
        _, d = self.detail(a, "contenuti")
        self.assertEqual([(o["kind"], o["text"]) for o in d["outputs"]], [("bozza", "testo riservato di A")])
        self.assertIn("Ha usato: contenuti salva bozza", [r["text"] for r in d["log"]])
        self.assertEqual(d["agent"]["id"], "contenuti")
        _, d = self.detail(a, "crm")
        self.assertEqual([o["title"] for o in d["outputs"]], ["Cliente Riservato"])
        self.assertNotIn("Ha usato: contenuti salva bozza", [r["text"] for r in d["log"]], "il registro del CRM non mostra il lavoro di un altro agente")
        _, d = self.detail(a, "calendario")
        self.assertEqual([o["kind"] for o in d["outputs"]], ["evento"])
        _, d = self.detail(a, "centrale")
        self.assertEqual(d["outputs"], [])
        self.assertTrue(any("Ha risposto in chat" in r["text"] for r in d["log"]))
        for agent_id in ("contenuti", "crm", "calendario", "centrale"):
            _, d = self.detail(b, agent_id)
            self.assertEqual((d["outputs"], d["log"], d["pending"]), ([], [], []), "l'altra azienda non vede niente (%s)" % agent_id)
        con = db.connect()
        user = con.execute("SELECT id FROM users WHERE tenant_id=?", (tid,)).fetchone()["id"]
        self.assertEqual([r["agent_id"] for r in con.execute("SELECT agent_id FROM drafts WHERE tenant_id=?", (tid,))], ["contenuti"],
                         "la bozza ricorda quale agente l'ha scritta")
        # una bozza per LinkedIn scritta dall'agente Contenuti resta alla postazione di Contenuti
        con.execute("INSERT INTO drafts(tenant_id,channel,title,body,created_by,created_at,agent_id) VALUES(?,?,?,?,?,?,?)",
                    (tid, "linkedin", "Scritta da Contenuti", "testo", user, db.now(), "contenuti"))
        con.commit()
        self.assertIn("Scritta da Contenuti", [o["title"] for o in self.detail(a, "contenuti")[1]["outputs"]])
        self.assertEqual(self.detail(a, "linkedin")[1]["outputs"], [])
        # bozze salvate prima della versione 4: senza agente, si attribuiscono dal canale
        for channel in ("linkedin", "generico"):
            con.execute("INSERT INTO drafts(tenant_id,channel,title,body,created_by,created_at) VALUES(?,?,?,?,?,?)",
                        (tid, channel, "Vecchia " + channel, "testo", user, db.now()))
        con.commit()
        con.close()
        self.assertEqual([o["title"] for o in self.detail(a, "linkedin")[1]["outputs"]], ["Vecchia linkedin"])
        self.assertIn("Vecchia generico", [o["title"] for o in self.detail(a, "contenuti")[1]["outputs"]])
        self.assertNotIn("Vecchia linkedin", [o["title"] for o in self.detail(a, "contenuti")[1]["outputs"]])

    def test_un_operatore_vede_solo_le_proprie_richieste_in_attesa(self):
        a, tid = self.new_tenant("Ufficio Operatori", "completo")
        con = db.connect()
        ops.create_user(con, tid, "uno@operatori.it", "Uno", "member", PW)
        ops.create_user(con, tid, "due@operatori.it", "Due", "member", PW)
        con.close()
        uno, due = self.login("uno@operatori.it"), self.login("due@operatori.it")
        self.assertEqual(self.chat(uno, "invia a cliente@esempio.it: preventivo")[0], 200)
        self.assertEqual(len(self.detail(uno, "email")[1]["pending"]), 1)
        self.assertEqual(self.detail(due, "email")[1]["pending"], [], "richiesta di un collega")
        self.assertEqual(len(self.detail(a, "email")[1]["pending"]), 1, "l'amministratore le vede tutte")
        self.assertEqual(self.states(due)["email"], "attesa", "lo stato dell'agente è lo stesso per tutta l'azienda")

    # ---------- file locali e regole di sicurezza della pagina ----------
    def test_motore_3d_locale_e_nessuna_risorsa_esterna(self):
        for path in ("/vendor/mini3d.js", "/office.js", "/app.js"):
            status, headers, body = self.fetch(path)
            self.assertEqual(status, 200, path)
            self.assertIn("javascript", headers["Content-Type"])
            self.assertIn("nosniff", headers["X-Content-Type-Options"])
        for path in ("/vendor/three.module.js", "/vendor/", "/vendor/../catalog.json", "/vendor/..%2f..%2f.env", "/office.py", "/vendor/mini3d.js/"):
            self.assertEqual(self.fetch(path)[0], 404, path)
        csp = self.fetch("/")[1]["Content-Security-Policy"]
        rules = dict(part.strip().split(" ", 1) for part in csp.split(";"))
        for name in ("default-src", "script-src", "style-src", "img-src", "media-src", "connect-src", "font-src"):
            self.assertEqual(rules[name], "'self'", name)
        for name in ("object-src", "worker-src", "frame-src", "frame-ancestors", "base-uri"):
            self.assertEqual(rules[name], "'none'", name)
        for word in ("unsafe-inline", "unsafe-eval", "http:", "https:", "data:", "blob:", "*"):
            self.assertNotIn(word, csp)
        page = self.fetch("/app")[2].decode("utf-8")
        self.assertEqual(re.findall(r'<script[^>]*src="([^"]+)"', page), ["/vendor/mini3d.js", "/office.js", "/app.js"])
        self.assertIsNone(re.search(r"<script(?![^>]*\bsrc=)", page), "nessuno script scritto nella pagina")
        for f in sorted((ROOT / "web").rglob("*")):
            if f.is_file() and f.suffix in (".js", ".css", ".html", ".svg"):
                text = f.read_text(encoding="utf-8")
                self.assertIsNone(re.search(r"(https?:)?//[a-z0-9.-]+\.[a-z]{2,}", text.replace("http://www.w3.org/2000/svg", "")), "indirizzo esterno in %s" % f.name)
                for word in ("eval(", "new Function", "importScripts", "document.write"):
                    self.assertNotIn(word, text, "%s in %s" % (word, f.name))
        self.assertEqual(set(app.STATIC), {"/", "/app", "/sito.css", "/sito.js", "/app.js", "/app.css", "/logo.png", "/logo-alvea.png", "/icon.png", "/icon-180.png", "/icon-512.png",
                                           "/manifest.webmanifest", "/favicon.ico", "/office.js", "/vendor/mini3d.js"})

    def test_ogni_agente_del_catalogo_ha_una_postazione_che_la_pagina_sa_disegnare(self):
        script = (ROOT / "web" / "office.js").read_text(encoding="utf-8")
        equip = script[script.index("var EQUIP = {"):script.index("function layout(")]
        drawn = set(re.findall(r"^    ([a-z_]+): function \(t\)", equip, re.M))
        for a in catalog.load()["agents"]:
            self.assertIn(a["station"], drawn, a["id"])
        self.assertIn("montaggio", drawn, "postazione pronta per il futuro agente video")
        page_states = set(re.findall(r"^    ([a-z_]+): \{ label:", script, re.M))
        self.assertEqual(page_states, STATES, "la pagina e il server usano gli stessi stati")
        for st in sorted(STATES):
            self.assertIn(".st-%s " % st, (ROOT / "web" / "app.css").read_text(encoding="utf-8"))

    def test_aggiornamento_dalla_versione_3_conserva_le_bozze(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        db.init(con)
        con.execute("ALTER TABLE drafts DROP COLUMN agent_id")  # com'era la tabella nella versione 3
        con.execute("INSERT INTO tenants(name,package_id,created_at) VALUES('Vecchia','start',?)", (db.now(),))
        con.execute("INSERT INTO drafts(tenant_id,channel,title,body,created_by,created_at) VALUES(1,'campagna','Saldi','testo',1,?)", (db.now(),))
        db.init(con)
        row = con.execute("SELECT * FROM drafts").fetchone()
        self.assertEqual((row["title"], row["agent_id"]), ("Saldi", None))
        self.assertEqual([o["title"] for o in office._outputs(con, {"id": 1}, "marketing")], ["Saldi"])
        con.close()


if __name__ == "__main__":
    unittest.main()
