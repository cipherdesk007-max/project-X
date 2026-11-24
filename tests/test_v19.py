"""Prove della versione 25: profili per tipo di attività (agenti consigliati, contesto per gli agenti, richieste d'esempio)."""
import os
import unittest

from base import Base, PW, ROOT

from server import agents, auth, catalog, db, ops, registration, team


class TipiDiAttivita(Base):
    def setUp(self):
        registration.reset_throttle()
        auth.reset_fails()

    def register(self, **over):
        body = {"company": "Attività %s" % over.get("email", "x").split("@")[0], "name": "Titolare", "email": "t@attivita.it", "password": PW, "agents": ["email"], "accept": True}
        body.update(over)
        return self.call("POST", "/api/register", body)

    def test_il_catalogo_delle_attivita_e_completo_e_coerente(self):
        kinds = catalog.activities()
        ids = [a["id"] for a in kinds]
        self.assertGreaterEqual(len(kinds), 12)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids[-1], "altro", "«Altro» resta in fondo, per chi non si riconosce in nessuna")
        start = catalog.entitled_agents("start")
        for a in kinds:
            self.assertGreaterEqual(len([x for x in a["agents"] if x in start]), 3, "%s: anche chi ha il pacchetto più piccolo deve avere almeno tre agenti consigliati" % a["id"])
            self.assertTrue(all(catalog.agent(x) for x in a["agents"]), a["id"])
            self.assertEqual(len(a["tries"]), 3)
            self.assertIn(a["for"], ("azienda", "persona", "tutti"))
            if a["id"] != "altro":
                self.assertGreater(len(a["context"]), 60, "%s: il contesto deve dire agli agenti qualcosa di utile" % a["id"])
        self.assertTrue([a for a in kinds if a["for"] == "persona"] and [a for a in kinds if a["for"] == "azienda"], "ce n'è per le aziende e per chi è da solo")
        self.assertIsNone(catalog.activity("inesistente"))
        self.assertIn("consigli medici", catalog.activity("salute")["context"], "dove serve, il contesto mette anche i paletti")

    def test_un_catalogo_scritto_male_non_parte(self):
        """Il catalogo si modifica a mano: un errore lì deve fermare il server all'avvio, non arrivare ai clienti."""
        import copy
        good = copy.deepcopy(catalog.load())
        catalog.validate(good)
        broken = {"agente che non esiste": lambda a: a["agents"].append("inesistente"), "agente ripetuto": lambda a: a["agents"].append(a["agents"][0]),
                  "nessun agente": lambda a: a.update(agents=[]), "due sole richieste": lambda a: a["tries"].pop(), "richiesta vuota": lambda a: a["tries"].__setitem__(0, " "),
                  "destinatari sbagliati": lambda a: a.update({"for": "chiunque"}), "senza nome": lambda a: a.update(name="")}
        for what, change in broken.items():
            data = copy.deepcopy(good)
            change(data["activities"][0])
            with self.assertRaises(AssertionError, msg=what):
                catalog.validate(data)
        data = copy.deepcopy(good)
        data["activities"].append(copy.deepcopy(data["activities"][0]))
        with self.assertRaises(AssertionError, msg="attività doppia"):
            catalog.validate(data)

    def test_alla_registrazione_si_sceglie_l_attivita_e_gli_agenti_la_conoscono(self):
        info = self.call("GET", "/api/register")[1]
        self.assertEqual([a["id"] for a in info["activities"]], [a["id"] for a in catalog.activities()])
        self.assertNotIn("context", info["activities"][0], "al modulo servono solo nome e agenti")
        status, out = self.register(email="auto@attivita.it", activity="auto")
        self.assertEqual(status, 200, out)
        me = self.call("GET", "/api/me", token=out["token"])[1]
        self.assertEqual(me["tenant"]["activity"], "auto")
        con = db.connect()
        self.addCleanup(con.close)
        tenant = dict(con.execute("SELECT * FROM tenants WHERE id=?", (me["tenant"]["id"],)).fetchone())
        prompt = agents.system_prompt(tenant, "contenuti", ["contenuti", "email"])
        self.assertIn("Chi stai aiutando: Auto e moto", prompt)
        self.assertIn("anno, chilometri", prompt)
        self.assertNotIn("Lavora da solo", prompt)
        status, solo = self.register(email="creator@attivita.it", activity="creator", kind="persona", company="")
        tenant_solo = dict(con.execute("SELECT * FROM tenants WHERE id=?", (self.call("GET", "/api/me", token=solo["token"])[1]["tenant"]["id"],)).fetchone())
        self.assertIn("Lavora da solo", agents.system_prompt(tenant_solo, "contenuti", ["contenuti"]))
        status, plain = self.register(email="niente@attivita.it")
        tenant_plain = dict(con.execute("SELECT * FROM tenants WHERE id=?", (self.call("GET", "/api/me", token=plain["token"])[1]["tenant"]["id"],)).fetchone())
        self.assertNotIn("Chi stai aiutando", agents.system_prompt(tenant_plain, "contenuti", ["contenuti"]), "senza attività non cambia niente")
        self.assertNotIn("Chi stai aiutando", agents.system_prompt(dict(tenant_plain, activity="altro"), "contenuti", ["contenuti"]))
        for bad in ("inventata", "auto' OR '1'='1", "<script>"):
            self.assertEqual(self.register(email="bad@attivita.it", activity=bad)[0], 400, bad)
        self.assertEqual(self.chat(out["token"], "Scrivi l'annuncio di questa auto: Panda 2019", "centrale")[0], 200)
        steps = self.call("GET", "/api/summary", token=out["token"])[1]
        self.assertEqual(steps["tries"], catalog.activity("auto")["tries"], "le richieste d'esempio sono quelle del mestiere")
        self.assertEqual(self.call("GET", "/api/summary", token=plain["token"])[1]["tries"], catalog.activity("altro")["tries"])

    def test_con_un_pacchetto_piccolo_si_consigliano_solo_agenti_che_si_possono_accendere(self):
        os.environ["SFB_REGISTER_PACKAGE"] = "start"
        self.addCleanup(lambda: os.environ.__setitem__("SFB_REGISTER_PACKAGE", "completo"))
        info = self.call("GET", "/api/register")[1]
        start = catalog.entitled_agents("start")
        for a in info["activities"]:
            self.assertTrue(a["agents"] and set(a["agents"]) <= set(start), a["id"])

    def test_dal_pannello_si_cambia_attivita_e_si_accendono_i_consigliati(self):
        admin, tid = self.new_tenant("Attività Pannello", "start")
        other, other_id = self.new_tenant("Attività Altra", "start")
        con = db.connect()
        self.addCleanup(con.close)
        ops.create_user(con, tid, "operatore@attivita-pannello.it", "Operatore", "member", PW)
        self.assertEqual(self.call("POST", "/api/tenant/activity", {"activity": "ristorazione"}, self.login("operatore@attivita-pannello.it"))[0], 403)
        self.assertEqual(self.call("POST", "/api/tenant/activity", {"activity": "inventata"}, admin)[0], 400)
        for agent_id in ("email", "calendario", "traduzioni", "ricerca"):
            self.assertEqual(self.call("POST", "/api/tenant/agents", {"agent_id": agent_id, "is_active": False}, admin)[0], 200)
        status, out = self.call("POST", "/api/tenant/activity", {"activity": "ristorazione"}, admin)
        self.assertEqual((status, out["activity"], out["turned_on"]), (200, "ristorazione", []), "senza chiederlo non si accende niente")
        active = lambda t: set(self.call("GET", "/api/me", token=t)[1]["entitled"])
        self.assertNotIn("email", active(admin))
        status, out = self.call("POST", "/api/tenant/activity", {"activity": "ristorazione", "turn_on": True}, admin)
        self.assertEqual(sorted(out["turned_on"]), sorted(["Email", "Calendario", "Traduzioni"]), "accesi i consigliati che stanno nel pacchetto Start, non quelli di pacchetti superiori")
        now = active(admin)
        self.assertTrue({"email", "calendario", "traduzioni"} <= now and "ricerca" not in now and "recensioni" not in now, "gli altri restano come li aveva lasciati")
        self.assertEqual(self.call("GET", "/api/me", token=admin)[1]["tenant"]["activity"], "ristorazione")
        self.assertEqual(self.call("GET", "/api/me", token=other)[1]["tenant"]["activity"], "", "l'altra azienda non è toccata")
        self.assertEqual(self.call("POST", "/api/tenant/activity", {"activity": ""}, admin)[1]["activity"], "", "si può anche togliere")
        actions = [r[0] for r in con.execute("SELECT action FROM audit WHERE tenant_id=? AND action='attivita_impostata'", (tid,))]
        self.assertEqual(len(actions), 3, "ogni cambio resta nel registro")

    def test_ogni_agente_ha_tre_consigli_e_ognuno_funziona_davvero(self):
        """Un consiglio è una promessa: toccarlo e mandarlo deve dare una risposta, non un errore."""
        import copy
        every = catalog.load()["agents"]
        seen = set()
        for a in every:
            self.assertEqual(len(a["tries"]), 3, a["id"])
            for t in a["tries"]:
                self.assertTrue(8 <= len(t) <= 90, "%s: «%s» deve stare su un pulsante" % (a["id"], t))
                self.assertNotIn(t, seen, "consiglio ripetuto: %s" % t)
                seen.add(t)
        for round_ in range(3):  # tre aziende, per non superare il limite di messaggi al minuto di un utente
            token, _ = self.new_tenant("Consigli %d" % round_, "completo")
            for a in every:
                status, out = self.chat(token, a["tries"][round_], a["id"])
                self.assertEqual(status, 200, (a["id"], a["tries"][round_], out))
                self.assertTrue(out["reply"], a["id"])
        token, tid = self.new_tenant("Consigli Strumenti", "completo")
        tools_used = lambda agent, n: [e["tool"] for e in self.chat(token, catalog.agent(agent)["tries"][n], agent)[1]["events"]]
        self.assertEqual(tools_used("calendario", 0), ["calendario_elenca_eventi"])
        self.assertEqual(tools_used("crm", 1), ["crm_elenca_lead"])
        self.assertEqual(tools_used("report", 0), ["report_riepilogo"])
        self.assertEqual(tools_used("team", 0), ["team_elenca_utenti"])
        self.assertEqual(tools_used("email", 0), ["email_leggi_recenti"])
        bad = copy.deepcopy(catalog.load())
        bad["agents"][0]["tries"] = bad["agents"][0]["tries"][:2]
        with self.assertRaises(AssertionError):
            catalog.validate(bad)
        con = db.connect()
        self.addCleanup(con.close)
        ops.create_user(con, tid, "operatore@consigli-strumenti.it", "Operatore", "member", PW)
        member = self.call("GET", "/api/summary", token=self.login("operatore@consigli-strumenti.it"))[1]
        self.assertEqual((len(member["tries"]), "first_steps" in member), (3, False), "anche un operatore ha i consigli per il Centrale, senza i primi passi")

    def test_i_consigli_compaiono_solo_a_richiesta(self):
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "web" / "app.css").read_text(encoding="utf-8")
        self.assertIn('id: "tipsList", hidden: !tipsOpen()', js, "l'elenco nasce nascosto")
        self.assertIn('window.localStorage.getItem("alvea_consigli") === "1"', js, "aperto solo se l'utente l'ha lasciato aperto")
        self.assertIn('text: "Consigli"', js)
        self.assertEqual(js.count("tipsBox("), 3, "il tasto c'è nella chat della vista compatta e in quella dell'Ufficio")
        self.assertNotIn("Prova a chiedere", js, "niente esempi sempre in vista nei Primi passi")
        self.assertIn(".tips[hidden] { display: none; }", css, "nascosto vuol dire nascosto, anche con la disposizione a righe")

    def test_il_sito_e_le_schermate_mostrano_le_attivita(self):
        html = self.fetch("/")[2].decode("utf-8")
        self.assertIn('<section id="per-chi">', html)
        for a in catalog.activities():
            self.assertEqual(("<h3>%s</h3>" % a["name"].replace("&", "&amp;")) in html.split('id="per-chi"')[1].split("</section>")[0], a["id"] != "altro", a["id"])
        self.assertNotIn("style=", html)
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        for piece in ('id: "reg_activity"', 'activity: val("reg_activity")', '"/tenant/activity"', "S.sum.tries"):
            self.assertIn(piece, js, piece)


if __name__ == "__main__":
    unittest.main()
