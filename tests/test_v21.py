"""Prove della versione 28: quello che si configura DOPO l'installazione, dal pannello e a server acceso, funziona davvero.

La prova vera sta in strumenti/prova_configurazione.py: installa un server da zero in una cartella a parte, lo avvia senza nessuna chiave
e scrive ogni impostazione dal pannello, controllando com'è prima e dopo. Qui la si lancia e si guarda che non fallisca niente."""
import re
import shutil
import subprocess
import sys
import unittest

from base import ROOT

try:
    import cryptography  # noqa: F401
    CRYPTO = True
except Exception:
    CRYPTO = False


class ConfiguratoDopo(unittest.TestCase):
    @unittest.skipUnless(CRYPTO and shutil.which("openssl"), "servono il modulo cryptography e openssl, come su un server installato")
    def test_un_server_vuoto_configurato_dal_pannello_funziona(self):
        import os
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")}
        r = subprocess.run([sys.executable, str(ROOT / "strumenti" / "prova_configurazione.py")], env=env, capture_output=True, text=True, timeout=400)
        tail = "\n".join(l for l in r.stdout.splitlines() if not l.startswith("OK "))[-6000:] + r.stderr[-2000:]
        self.assertEqual(r.returncode, 0, tail)
        done = re.search(r"superata: (\d+) controlli, 0 falliti", r.stdout)
        self.assertTrue(done, tail)
        self.assertGreaterEqual(int(done.group(1)), 85, "la prova si è accorciata: qualche gruppo del pannello non viene più controllato")

    def test_ogni_impostazione_del_pannello_viene_letta_al_momento_dell_uso(self):
        """Nessun file del server tiene da parte un'impostazione al caricamento: se lo facesse, cambiarla dal pannello non avrebbe effetto fino al riavvio."""
        import ast
        kept = []
        for f in sorted((ROOT / "server").glob("*.py")):
            src = f.read_text(encoding="utf-8")
            for node in ast.parse(src).body:
                if isinstance(node, (ast.Assign, ast.AnnAssign)) and re.search(r"config\.get|os\.environ|os\.getenv", ast.get_source_segment(src, node) or ""):
                    kept.append("%s:%d" % (f.name, node.lineno))
        self.assertEqual(kept, [])

    def test_i_messaggi_per_chi_usa_gli_agenti_non_rimandano_al_file_env(self):
        """Chi lavora dal pannello (o dal telefono) non ha un file da aprire: i messaggi dicono in quale gruppo delle impostazioni si scrive."""
        from server import config, google, llm
        saved = dict(config.OVERRIDES)
        import os
        old = {k: os.environ.pop(k, None) for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_MODEL", "SFB_DEMO", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET")}
        os.environ["SFB_DEMO"] = "0"
        try:
            config.OVERRIDES.clear()
            texts = []
            for model, setup in (("claude", {}), ("chatgpt", {"OPENAI_API_KEY": "x"}), ("locale", {})):
                config.OVERRIDES.clear()
                config.OVERRIDES.update(setup)
                with self.assertRaises(llm.LLMError) as e:
                    llm.chat(model, "s", [{"role": "user", "content": "ciao"}], [])
                texts.append(str(e.exception))
            config.OVERRIDES.clear()
            from server import connectors
            with self.assertRaises(connectors.ConnectorError) as e:
                google.start(None, 1, 1, ["calendario"])
            texts.append(str(e.exception))
        finally:
            config.OVERRIDES.clear()
            config.OVERRIDES.update(saved)
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        for t in texts:
            self.assertIn("pannello", t, t)
            self.assertNotIn(".env", t, t)
        self.assertIn("gruppo «Modelli»", texts[0])
        self.assertIn("gruppo «Google»", texts[3])

    def test_un_modello_che_non_risponde_in_tempo_viene_detto_con_l_attesa_scritta_nel_pannello(self):
        import os
        import threading
        import time
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from server import config, llm

        class Lento(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                time.sleep(3.5)
                try:
                    self.send_response(200)
                    self.end_headers()
                except OSError:
                    pass
        slow = ThreadingHTTPServer(("127.0.0.1", 0), Lento)
        slow.daemon_threads = True
        threading.Thread(target=slow.serve_forever, daemon=True).start()
        saved, demo = dict(config.OVERRIDES), os.environ.get("SFB_DEMO")
        os.environ["SFB_DEMO"] = "0"
        try:
            config.OVERRIDES.clear()
            self.assertEqual(llm.timeout(), 120)
            config.OVERRIDES.update({"SFB_LLM_TIMEOUT": "100000"})
            self.assertEqual(llm.timeout(), 900)
            config.OVERRIDES.update({"SFB_LLM_TIMEOUT": "2", "OLLAMA_BASE_URL": "http://127.0.0.1:%d/v1" % slow.server_address[1]})
            started = time.time()
            with self.assertRaises(llm.LLMError) as e:
                llm.chat("locale", "s", [{"role": "user", "content": "ciao"}], [])
            self.assertLess(time.time() - started, 3.2)
            self.assertIn("entro 2 secondi", str(e.exception))
            self.assertIn("gruppo «Modelli»", str(e.exception))
        finally:
            config.OVERRIDES.clear()
            config.OVERRIDES.update(saved)
            os.environ["SFB_DEMO"] = demo or "1"
            slow.shutdown()
            slow.server_close()

    def test_il_livello_di_ragionamento_va_solo_a_kimi_e_solo_se_scritto(self):
        from server import config, llm
        saved, sent = dict(config.OVERRIDES), []
        post = lambda url, headers, body: sent.append(body) or {"choices": [{"message": {"content": "ok"}}], "usage": {}}  # noqa: E731
        try:
            for effort, model in (("", "kimi"), ("low", "kimi"), ("MAX", "kimi"), ("altissimo", "kimi"), ("low", "chatgpt")):
                config.OVERRIDES.clear()
                config.OVERRIDES.update({"KIMI_REASONING_EFFORT": effort, "MOONSHOT_API_KEY": "k", "OPENAI_API_KEY": "k", "OPENAI_MODEL": "m"})
                llm._call_openai(llm.settings(model), "s", [{"role": "user", "content": "ciao"}], [], post)
        finally:
            config.OVERRIDES.clear()
            config.OVERRIDES.update(saved)
        self.assertEqual([b.get("reasoning_effort") for b in sent], [None, "low", "max", None, None])
        self.assertTrue(all("temperature" not in b and "thinking" not in b for b in sent), "Kimi K3 non vuole né temperatura né il parametro thinking")


if __name__ == "__main__":
    unittest.main()
