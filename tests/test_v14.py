"""Prove della versione 14: prezzi regolabili dal pannello (pacchetto più agenti accesi) e otto agenti nuovi."""
import unittest

from base import Base
from server import agents, catalog, db, pricing, tools
from test_v8 import STRIPE, V8


class Prezzi(V8):
    def prices(self, **values):
        self.assertEqual(self.save({k: str(v) for k, v in values.items()})[0], 200)

    def keys(self):
        self.assertEqual(self.save({"STRIPE_SECRET_KEY": "sk_test_prova", "STRIPE_WEBHOOK_SECRET": "whsec_prova"})[0], 200)

    def toggle(self, token, agent_id, on):
        return self.call("POST", "/api/tenant/agents", {"agent_id": agent_id, "is_active": on}, token)

    def test_senza_prezzi_impostati_non_cambia_niente(self):
        a, tid = self.new_tenant("Prezzi Zero", "start")
        q = self.call("GET", "/api/billing", token=a)[1]["quote"]
        self.assertEqual((q["configured"], q["total"]), (False, 0))
        self.assertNotIn("€", self.fetch("/")[2].decode("utf-8"), "il sito non mostra prezzi che nessuno ha deciso")
        self.assertIsNone(self.toggle(a, "email", False)[1]["price"])

    def test_pacchetto_piu_agenti_accesi(self):
        self.keys()
        self.prices(PRICE_START=49, PRICE_BUSINESS=99, PRICE_COMPLETO=199, PRICE_PER_AGENT=5)
        a, tid = self.new_tenant("Prezzi Uno", "start")
        b, tid_b = self.new_tenant("Prezzi Due", "business")
        n = len(self.call("GET", "/api/me", token=a)[1]["entitled"])
        q = self.call("GET", "/api/billing", token=a)[1]["quote"]
        self.assertEqual((q["base"], q["per_agent"], q["agents"], q["total"]), (49, 5, n, 49 + 5 * n))
        self.assertTrue(self.call("GET", "/api/billing", token=a)[1]["enabled"], "con i prezzi nel pannello non servono prezzi creati a mano su Stripe")
        self.assertEqual(self.call("GET", "/api/billing", token=b)[1]["quote"]["base"], 99)
        page = self.fetch("/")[2].decode("utf-8")
        for text in ("49 € al mese + 5 € al mese per ogni agente acceso", "99 € al mese", "199 € al mese"):
            self.assertIn(text, page)
        STRIPE["posts"].clear()
        self.assertEqual(self.call("POST", "/api/billing/checkout", {}, a)[0], 200)
        form = STRIPE["posts"][-1]["form"]
        self.assertEqual((form["line_items[0][price_data][unit_amount]"], form["line_items[0][price_data][currency]"],
                          form["line_items[0][price_data][recurring][interval]"], form["line_items[0][price_data][product_data][name]"]),
                         (str((49 + 5 * n) * 100), "eur", "month", "Alvea Start"))
        self.assertNotIn("line_items[0][price]", form)
        self.assertEqual(self.call("GET", "/api/tenant/agents", token=a)[1]["per_agent"], 5, "la scheda Agenti sa quanto costa accenderne uno")
        # abbonata: spegnere un agente abbassa il prezzo su Stripe; l'altra azienda non c'entra
        self.assertEqual(self.hook({"id": "evt_p1", "type": "checkout.session.completed", "data": {"object": {"client_reference_id": str(tid), "customer": "cus_P", "subscription": "sub_P"}}})[0], 200)
        STRIPE["posts"].clear()
        status, out = self.call("POST", "/api/billing/checkout", {}, a)
        self.assertEqual(status, 409, "chi è già abbonato non può aprire un secondo abbonamento e pagare due volte")
        self.assertEqual(STRIPE["posts"], [])
        status, out = self.toggle(a, "email", False)
        self.assertEqual((status, out["price"]), (200, 49 + 5 * (n - 1)))
        self.assertEqual([(p.get("method", "POST"), p["path"]) for p in STRIPE["posts"]], [("GET", "/v1/subscriptions/sub_P"), ("POST", "/v1/subscription_items/si_1")])
        sent = STRIPE["posts"][-1]["form"]
        self.assertEqual((sent["price_data[unit_amount]"], sent["price_data[product]"], sent["price_data[currency]"], sent["price_data[recurring][interval]"]),
                         (str((49 + 5 * (n - 1)) * 100), "prod_1", "eur", "month"))
        STRIPE["posts"].clear()
        self.assertIsNone(self.toggle(a, "email", False)[1]["price"], "nessuna modifica, nessuna chiamata")
        self.assertIsNone(self.toggle(b, "shopify", False)[1]["price"], "chi non è abbonato non ha niente da aggiornare")
        self.assertEqual(STRIPE["posts"], [])
        # se Stripe non risponde, l'agente si accende comunque e l'errore resta nel registro
        STRIPE["status"] = 400
        status, out = self.toggle(a, "email", True)
        self.assertEqual(status, 200)
        self.assertIn("Stripe non ha aggiornato il prezzo", out["price"])
        self.assertIn("email", self.call("GET", "/api/me", token=a)[1]["entitled"])
        STRIPE["status"] = 200
        self.assertEqual(self.toggle(a, "email", True)[1]["price"], 49 + 5 * n, "alla modifica dopo, anche se non cambia niente, il prezzo si riallinea")
        STRIPE["posts"].clear()
        self.assertIsNone(self.toggle(a, "email", True)[1]["price"])
        self.assertEqual(STRIPE["posts"], [], "prezzo già giusto: Stripe non viene richiamato")
        # il gestore cambia pacchetto: cambia anche il prezzo
        STRIPE["posts"].clear()
        self.assertEqual(self.call("POST", "/api/admin/tenants/%d" % tid, {"package_id": "business"}, self.gestore)[0], 200)
        self.assertEqual(STRIPE["posts"][-1]["path"], "/v1/subscription_items/si_1")
        self.assertEqual(self.call("GET", "/api/billing", token=a)[1]["quote"]["base"], 99)

    def test_un_prezzo_scritto_male_non_diventa_un_addebito_enorme(self):
        self.prices(PRICE_START=99999999, PRICE_PER_AGENT=99999999)
        self.assertEqual((pricing.package_price("start"), pricing.per_agent()), (pricing.MAX_EURO, pricing.MAX_EURO))

    def test_solo_il_pacchetto_oppure_solo_gli_agenti(self):
        self.keys()
        self.prices(PRICE_BUSINESS=120)
        a, tid = self.new_tenant("Prezzi Tre", "business")
        q = self.call("GET", "/api/billing", token=a)[1]["quote"]
        self.assertEqual((q["total"], q["agents"], q["per_agent"]), (120, 0, 0), "senza prezzo per agente conta solo il pacchetto")
        self.assertIsNone(self.toggle(a, "shopify", False)[1]["price"])
        self.prices(PRICE_BUSINESS=0, PRICE_PER_AGENT=7)
        n = len(self.call("GET", "/api/me", token=a)[1]["entitled"])
        self.assertEqual(self.call("GET", "/api/billing", token=a)[1]["quote"]["total"], 7 * n)
        self.assertIn("7 € al mese per ogni agente acceso", self.fetch("/")[2].decode("utf-8"))
        con = db.connect()
        self.addCleanup(con.close)
        self.assertEqual(pricing.quote(con, {"id": 999999, "package_id": "start"})["total"], 0, "un'azienda senza agenti accesi non paga agenti")


class AgentiPerTutti(Base):
    def test_ventotto_agenti_tutti_completi(self):
        cat = catalog.load()
        self.assertEqual(len(cat["agents"]), 28)
        owner = {}
        for t in tools.TOOLS:
            owner.setdefault(t["agent"], []).append(t["name"])
        for agent_id in ("personale", "traduzioni", "facebook", "seo", "recensioni", "youtube", "tiktok", "contratti"):
            self.assertEqual(owner.get(agent_id), [agent_id + "_salva_bozza"])
            self.assertIn("non", agents.ROLES[agent_id], "il ruolo dice anche cosa l'agente non fa")
        self.assertIn("legale", agents.ROLES["contratti"])
        self.assertIn("finte", agents.ROLES["recensioni"])
        counts, total = [], 0
        for p in cat["packages"]:
            total += len(p["adds"])
            counts.append(total)
        self.assertEqual(counts, [9, 19, 28])
        self.assertEqual(sorted(x for p in cat["packages"] for x in p["adds"]), sorted(a["id"] for a in cat["agents"]), "ogni agente sta in un pacchetto, una volta sola")
        self.assertEqual(len({tuple(a["pos"]) for a in cat["agents"]}), 28)

    def test_le_bozze_degli_agenti_nuovi_restano_loro(self):
        con = db.connect()
        self.addCleanup(con.close)
        from server import ops
        tid = ops.create_tenant(con, "Agenti Tutti", "completo", "admin@agenti-tutti.it", "Admin", "una-password-lunga-123")
        ctx = tools.Ctx(con, dict(con.execute("SELECT * FROM tenants WHERE id=?", (tid,)).fetchone()), dict(con.execute("SELECT * FROM users WHERE tenant_id=?", (tid,)).fetchone()))
        fn = {t["name"]: t["fn"] for t in tools.TOOLS}
        fn["youtube_salva_bozza"](ctx, {"titolo": "Video di lancio", "testo": "Titolo, descrizione e capitoli"})
        fn["contratti_salva_bozza"](ctx, {"tipo": "lettera", "titolo": "Disdetta", "testo": "Gentile..."})
        rows = [tuple(r) for r in con.execute("SELECT agent_id, channel, title FROM drafts WHERE tenant_id=? ORDER BY id", (tid,))]
        self.assertEqual(rows, [("youtube", "video", "Video di lancio"), ("contratti", "lettera", "Disdetta")])
        with self.assertRaises(Exception):
            fn["tiktok_salva_bozza"](ctx, {"titolo": "Senza testo"})


for _name in [n for n in dir(V8) if n.startswith("test_")]:
    setattr(Prezzi, _name, None)
del V8

if __name__ == "__main__":
    unittest.main()
