"""Prove della versione 12: capacità del server, console del gestore, coda d'attesa sotto carico."""
import threading
import unittest
import urllib.request

from base import Base
from server import app, capacity, console, db


class Capacita(unittest.TestCase):
    def test_un_server_piccolo_viene_segnalato(self):
        r = capacity.report({"cores": 1, "ram_gb": 0.5, "disk_free_gb": 2.0, "ffmpeg": False, "ollama": False})
        states = dict((t.split(":")[0], s) for s, t in r["lines"])
        self.assertEqual((states["Memoria"], states["Processore"], states["Disco"], states["Montaggio video sul server"]),
                         ("NON REGGE", "ATTENZIONE", "NON REGGE", "NON REGGE"))
        self.assertEqual((r["videos"], r["companies_full"]), (0, 0))
        self.assertLess(r["people"], 900)

    def test_un_server_grande_regge_e_i_numeri_tornano(self):
        r = capacity.report({"cores": 8, "ram_gb": 16.0, "disk_free_gb": 500.0, "ffmpeg": True, "ollama": True})
        self.assertEqual([s for s, _ in r["lines"]], ["OK"] * len(r["lines"]))
        self.assertEqual((r["chats"], r["people"], r["videos"], r["companies_full"]), (300, 900, 4, 20))
        self.assertTrue(any("Stima, non garanzia" in a for a in r["assumptions"]))
        self.assertTrue(any("fornitore del modello" in a for a in r["assumptions"]), "la stima dichiara cosa non misura")

    def test_il_modello_locale_chiede_memoria_e_processore(self):
        r = capacity.report({"cores": 2, "ram_gb": 4.0, "disk_free_gb": 100.0, "ffmpeg": True, "ollama": True})
        line = [(s, t) for s, t in r["lines"] if "Ollama" in t]
        self.assertEqual(line[0][0], "NON REGGE")
        self.assertFalse(any("Ollama" in t for _, t in capacity.report({"cores": 2, "ram_gb": 4.0, "disk_free_gb": 100.0, "ffmpeg": True, "ollama": False})["lines"]))

    def test_piu_memoria_non_fa_mai_meno_persone(self):
        people = [capacity.report({"cores": 2, "ram_gb": g, "disk_free_gb": 50.0, "ffmpeg": True, "ollama": False})["people"] for g in (0.3, 0.5, 1, 2, 4, 8, 64)]
        self.assertEqual(people, sorted(people))
        self.assertLess(people[0], people[-1])
        self.assertIn("Memoria", capacity.text())


class Console(Base):
    def setUp(self):
        self.gestore = self.login("gestore@test.it")

    def test_solo_il_gestore_e_solo_i_comandi_dell_elenco(self):
        admin, _ = self.new_tenant("Console Uno", "start")
        self.assertEqual(self.call("GET", "/api/admin/console", token=admin)[0], 403)
        self.assertEqual(self.call("POST", "/api/admin/console", {"command": "capacita"}, admin)[0], 403)
        self.assertEqual(self.call("GET", "/api/admin/console")[0], 401)
        status, out = self.call("GET", "/api/admin/console", token=self.gestore)
        self.assertEqual(status, 200)
        self.assertEqual([c["id"] for c in out["commands"]], ["capacita", "modelli", "collaudo", "produzione", "registro", "aziende", "firma"])
        self.assertGreater(out["capacity"]["people"], 0)
        for bad in ("ls", "capacita; ls", "capacita --help", "../manage.py", "rm -rf /", "", "CAPACITA", ["capacita"]):
            self.assertEqual(self.call("POST", "/api/admin/console", {"command": bad}, self.gestore)[0], 400, bad)

    def test_un_comando_gira_e_resta_nel_registro(self):
        status, out = self.call("POST", "/api/admin/console", {"command": "capacita"}, self.gestore)
        self.assertEqual((status, out["code"]), (200, 0), out)
        self.assertIn("Memoria:", out["output"])
        status, out = self.call("POST", "/api/admin/console", {"command": "aziende"}, self.gestore)
        self.assertEqual((status, out["code"]), (200, 0), out)
        con = db.connect()
        logged = [r["detail"] for r in con.execute("SELECT detail FROM audit WHERE action='console_comando' ORDER BY id")]
        con.close()
        self.assertTrue(logged and "aziende" in logged[-1] and any("capacita" in d for d in logged))

    def test_prova_dei_modelli(self):
        import manage
        report = manage.model_check()
        names = [t.split(" ")[0].rstrip(":") for _, t in report]
        self.assertEqual(names, ["Kimi", "Claude", "ChatGPT", "Locale"])
        self.assertEqual([s for s, _ in report], ["OK"] * 4, report)  # nelle prove risponde il modello finto
        self.assertTrue(all("ha risposto" in t and "secondi" in t for _, t in report))
        status, out = self.call("POST", "/api/admin/console", {"command": "modelli"}, self.gestore)
        self.assertEqual((status, out["code"]), (200, 0), out)
        self.assertIn("Kimi", out["output"])
        from server import llm
        real = llm.chat

        def broken(*a, **k):
            raise llm.LLMError("401 chiave non valida")
        llm.chat = broken
        try:
            bad = manage.model_check()
        finally:
            llm.chat = real
        self.assertEqual([s for s, _ in bad], ["ERRORE"] * 4)
        self.assertIn("401 chiave non valida", bad[0][1])

    def test_un_comando_alla_volta(self):
        self.assertTrue(console._busy.acquire(blocking=False))
        try:
            self.assertEqual(self.call("POST", "/api/admin/console", {"command": "capacita"}, self.gestore)[0], 409)
        finally:
            console._busy.release()
        self.assertEqual(self.call("POST", "/api/admin/console", {"command": "capacita"}, self.gestore)[0], 200)

    def test_centocinquanta_richieste_insieme_passano_tutte(self):
        self.assertGreaterEqual(app.Server.request_queue_size, 128)
        results, lock = [], threading.Lock()

        def one():
            try:
                with urllib.request.urlopen(self.base + "/api/health", timeout=60) as r:
                    code = r.status
            except Exception as e:  # una connessione rifiutata è proprio il difetto che questa prova sorveglia
                code = type(e).__name__
            with lock:
                results.append(code)

        threads = [threading.Thread(target=one) for _ in range(150)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(set(results), {200}, "con la coda d'attesa corta alcune connessioni venivano rifiutate")


if __name__ == "__main__":
    unittest.main()
