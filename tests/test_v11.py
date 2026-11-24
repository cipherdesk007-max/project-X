"""Prove della versione 11: sito pubblico servito dallo stesso server e due agenti nuovi (Assistenza, Amministrazione)."""
import html
import os
import re
import unittest

from base import Base, PW
from server import agents, catalog, db, tools


class Sito(Base):
    def page(self, path="/"):
        status, headers, data = self.fetch(path)
        self.assertEqual(status, 200, path)
        return headers, data.decode("utf-8")

    def test_il_sito_mostra_tutti_gli_agenti_e_i_pacchetti_del_catalogo(self):
        headers, page = self.page()
        self.assertIn("<title>Alvea — agenti AI per la tua azienda</title>", page)
        cat = catalog.load()
        self.assertEqual(page.count('<li class="agent">'), len(cat["agents"]))
        for a in cat["agents"]:
            for text in (a["name"], a["does"], a["acts"]):
                self.assertIn(html.escape(text), page, a["id"])
        total = 0
        for p in cat["packages"]:
            total += len(p["adds"])
            self.assertIn("<h3>%s</h3><p class=\"count\"><b>%d</b> agenti</p>" % (p["name"], total), page)
        self.assertIn("<span data-count>%d</span> agenti · un solo sistema" % len(cat["agents"]), page)
        for m in cat["models"]:
            self.assertIn(m["name"], page)

    def test_il_sito_rispetta_le_regole_di_sicurezza_e_non_promette_cose_inventate(self):
        headers, page = self.page()
        self.assertEqual(headers["Content-Security-Policy"], self.fetch("/app")[1]["Content-Security-Policy"])
        self.assertNotIn(" style=", page, "niente stili in linea")
        self.assertEqual(re.findall(r"<script[^>]*>", page), ['<script src="/sito.js">'], "un solo script, e da file")
        self.assertEqual(re.findall(r"https?://", page), [], "nessuna risorsa da altri siti")
        for src in re.findall(r'(?:src|href)="(/[^"#]*)', page):
            status, headers, _ = self.fetch(src)
            if "download?os=" in src:  # il launcher già pronto si scarica dal rilascio su GitHub
                self.assertEqual(status, 302, src)
                self.assertTrue(headers["Location"].startswith("https://github.com/cipherdesk007-max/project-X/releases/download/launcher/Alvea-Launcher"), headers["Location"])
            else:
                self.assertEqual(status, 200, "collegamento rotto: %s" % src)
        for anchor in re.findall(r'href="#([^"]+)"', page):
            self.assertIn('id="%s"' % anchor, page, "ancora senza destinazione: #%s" % anchor)
        low = page.lower()
        for forbidden in ("€", "alvea.ai", "garanti", "100%", "numero uno", "leader"):  # i prezzi compaiono solo se il gestore li imposta: vedi test_v14
            self.assertNotIn(forbidden, low, "il sito non mette prezzi né promesse che il catalogo non contiene")
        self.assertEqual(self.fetch("/sito.css")[0], 200)

    def test_i_collegamenti_dell_applicazione_passano_dal_sito_senza_perdersi(self):
        _, app = self.page("/app")
        self.assertIn("<title>alvea</title>", app)
        js = self.fetch("/sito.js")[2].decode("utf-8")
        self.assertIn('window.location.replace("/app" + hash)', js)
        pattern = re.search(r"/\^#\((.+?)\)/", js).group(1)
        rx = re.compile("^#(%s)" % pattern)
        for link in ("#registrati", "#reimposta-" + "a" * 30, "#pagamento-ok", "#pagamento-annullato", "#abbonamento", "#crediti-ok", "#crediti-annullato", "#google-ok", "#verifica-ok"):
            self.assertTrue(rx.match(link), "questo collegamento dell'applicazione resterebbe sul sito: %s" % link)
        _, page = self.page()
        for anchor in re.findall(r'href="(#[^"]+)"', page):
            self.assertFalse(rx.match(anchor), "questa sezione del sito verrebbe scambiata per un collegamento dell'applicazione: %s" % anchor)
        app_js = self.fetch("/app.js")[2].decode("utf-8")
        for used in re.findall(r'"(#[a-z]+-?)', app_js):
            if used in ("#registrati", "#reimposta-", "#pagamento-", "#abbonamento", "#google-", "#verifica-"):
                self.assertTrue(rx.match(used + ("x" if used.endswith("-") else "")), used)

    def test_le_animazioni_non_nascondono_niente_a_chi_non_le_vede(self):
        css, js = self.fetch("/sito.css")[2].decode("utf-8"), self.fetch("/sito.js")[2].decode("utf-8")
        hidden = [line for line in css.splitlines() if re.search(r"opacity:\s*0(?![.\d])", line) and line.strip().startswith(".") and not line.strip().startswith((".js ", ".moved "))]
        self.assertEqual(hidden, [], "una regola che nasconde senza «.js» lascerebbe la pagina vuota a chi ha gli script spenti")
        self.assertIn('root.classList.add("js")', js)
        self.assertIn('root.classList.remove("js")', js, "se l'animazione va in errore la pagina torna tutta visibile")
        self.assertIn('"IntersectionObserver" in window', js)
        reduced = css.split("@media (prefers-reduced-motion: reduce) {")[1]
        for rule in (".js .reveal", "animation: none"):
            self.assertIn(rule, reduced, "chi ha chiesto meno movimento vede la pagina ferma")
        self.assertNotIn("eval(", js)
        self.assertNotIn("innerHTML", js)

    def test_launcher_gia_pronto_per_sistema(self):
        from server import local
        self.assertTrue(self.call("GET", "/api/launcher/catalog")[1]["public_key"], "il programma scaricato da solo prende la chiave dal server")
        self.assertEqual(self.fetch("/api/launcher/download?os=mac-intel")[1]["Location"], local.RELEASE + "Alvea-Launcher-Intel.dmg")
        status, headers, data = self.fetch("/api/launcher/download?os=altro")
        self.assertEqual((status, data[:2]), (200, b"PK"), "per gli altri sistemi resta la versione con Python")
        os.environ["LAUNCHER_URL_WINDOWS"] = "https://scarica.esempio.it/Alvea.exe"
        self.addCleanup(lambda: os.environ.pop("LAUNCHER_URL_WINDOWS", None))
        self.assertEqual(self.fetch("/api/launcher/download?os=windows")[1]["Location"], "https://scarica.esempio.it/Alvea.exe")
        os.environ["LAUNCHER_URL_WINDOWS"] = "javascript:alert(1)"
        self.assertEqual(self.fetch("/api/launcher/download?os=windows")[1]["Location"], local.RELEASE + "Alvea-Launcher.exe", "un indirizzo non https viene ignorato")

    def test_il_sito_si_puo_spegnere(self):
        os.environ["SFB_SITE"] = "0"
        self.addCleanup(lambda: os.environ.pop("SFB_SITE", None))
        self.assertIn("<title>alvea</title>", self.page()[1], "senza sito la pagina iniziale torna a essere l'applicazione")


class AgentiNuovi(Base):
    def test_assistenza_e_amministrazione_sono_nel_motore(self):
        cat = catalog.load()
        self.assertEqual(len(cat["agents"]), 28)
        owner = {t["name"]: t["agent"] for t in tools.TOOLS}
        self.assertEqual((owner["assistenza_salva_risposta"], owner["amministrazione_salva_bozza"]), ("assistenza", "amministrazione"))
        for agent_id in ("assistenza", "amministrazione"):
            self.assertIn(agent_id, agents.ROLES)
            self.assertIn("non", agents.ROLES[agent_id], "il ruolo dice anche cosa l'agente non deve fare")
        for a in cat["agents"]:
            self.assertIn(a["id"], agents.ROLES, "agente senza ruolo nel motore: %s" % a["id"])
            for key in ("name", "icon", "does", "acts", "ext", "station", "run", "pos"):
                self.assertIn(key, a, "%s senza %s" % (a["id"], key))
        self.assertEqual(len({tuple(a["pos"]) for a in cat["agents"]}), 28, "due agenti nello stesso posto della schermata")
        icons = self.fetch("/app.js")[2].decode("utf-8")
        for a in cat["agents"]:
            self.assertIn("\n    %s: '" % a["icon"], icons, "manca il disegno dell'icona di %s" % a["id"])

    def test_i_due_agenti_rispondono_e_stanno_nel_pacchetto_giusto(self):
        start, _ = self.new_tenant("Nuovi Start", "start")
        business, _ = self.new_tenant("Nuovi Business", "business")
        ceiling = lambda t: self.call("GET", "/api/me", token=t)[1]["ceiling"]
        self.assertIn("assistenza", ceiling(start))
        self.assertNotIn("amministrazione", ceiling(start))
        self.assertIn("amministrazione", ceiling(business))
        self.assertEqual(self.chat(start, "Un cliente chiede gli orari", agent_id="assistenza")[0], 200)
        self.assertEqual(self.chat(business, "Prepara un preventivo", agent_id="amministrazione")[0], 200)
        self.assertGreaterEqual(self.chat(start, "Prepara un preventivo", agent_id="amministrazione")[0], 400, "fuori dal pacchetto non risponde")

    def test_le_bozze_restano_dell_agente_e_dell_azienda(self):
        _, tid = self.new_tenant("Nuovi Bozze", "completo")
        _, other = self.new_tenant("Nuovi Altra", "completo")
        con = db.connect()
        self.addCleanup(con.close)
        tenant = dict(con.execute("SELECT * FROM tenants WHERE id=?", (tid,)).fetchone())
        user = dict(con.execute("SELECT * FROM users WHERE tenant_id=?", (tid,)).fetchone())
        ctx = tools.Ctx(con, tenant, user)
        self.assertIn("Bozza salvata", tools.assistenza_salva_risposta(ctx, {"titolo": "Orari", "testo": "Siamo aperti dal lunedì al venerdì."}))
        self.assertIn("Bozza salvata", tools.amministrazione_salva_bozza(ctx, {"tipo": "sollecito", "titolo": "Fattura 12", "testo": "Gentile cliente..."}))
        rows = [tuple(r) for r in con.execute("SELECT agent_id, channel, title FROM drafts WHERE tenant_id=? ORDER BY id", (tid,))]
        self.assertEqual(rows, [("assistenza", "risposta al cliente", "Orari"), ("amministrazione", "sollecito", "Fattura 12")])
        self.assertEqual(con.execute("SELECT COUNT(*) FROM drafts WHERE tenant_id=?", (other,)).fetchone()[0], 0)
        with self.assertRaises(Exception):
            tools.assistenza_salva_risposta(ctx, {"titolo": "Senza testo"})
        with self.assertRaises(Exception):
            tools.amministrazione_salva_bozza(ctx, {"testo": "Senza titolo"})


if __name__ == "__main__":
    unittest.main()
