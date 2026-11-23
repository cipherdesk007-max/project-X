"""Prove della versione 10: collaudo di un server acceso, installazione con un comando, marchio."""
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import manage
from base import Base
from server import local

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which("openssl"), "serve openssl per firmare i pacchetti")
class Collaudo(Base):
    def setUp(self):
        local.sign_all()

    def test_collaudo_di_un_server_acceso(self):
        report = manage.acceptance(self.base)
        self.assertEqual([t for ok, t in report if not ok], [])
        self.assertEqual(len(report), 10)
        self.assertTrue(any("18 agenti" in t for _, t in report))
        self.assertTrue(any("dev, email, modello, video" in t for _, t in report))

    def test_collaudo_scrive_a_tutti_gli_agenti_accesi(self):
        self.new_tenant("Collaudo Uno", "completo")
        from base import PW
        report = manage.acceptance(self.base, "admin@collaudo-uno.it", PW, agents=True)
        self.assertEqual([t for ok, t in report if not ok], [])
        self.assertIn("Tutti i 18 agenti accesi rispondono", report[-1][1])
        start = manage.acceptance(self.base, "admin@collaudo-uno.it", PW)
        self.assertEqual(len(start), 11, "senza --agenti non scrive a nessuno")
        gestore = manage.acceptance(self.base, "gestore@test.it", PW)
        self.assertIn("Impostazioni della piattaforma raggiungibili", gestore[-1][1])

    def test_collaudo_segnala_quello_che_non_va(self):
        self.assertEqual([ok for ok, _ in manage.acceptance("http://127.0.0.1:9")], [False], "server spento: un solo errore, niente altro")
        bad = manage.acceptance(self.base, "gestore@test.it", "password-sbagliata-123")
        self.assertEqual([t for ok, t in bad if not ok], ["Accesso non riuscito come gestore@test.it"])
        real = local.signed
        local.signed = lambda name: None if name == "video" else real(name)
        try:
            failed = [t for ok, t in manage.acceptance(self.base) if not ok]
        finally:
            local.signed = real
        self.assertEqual(len(failed), 2, failed)
        self.assertIn("Pacchetti non firmati: video", failed[0])
        self.assertIn("non consegna: video", failed[1])

    def test_marchio_arancione_senza_resti_del_verde(self):
        for path in ("/app.css", "/office.js", "/app.js"):
            text = self.fetch(path)[2].decode("utf-8").lower()
            for old in ("#00c38a", "#4be3b4", "0, 195, 138"):
                self.assertNotIn(old, text, path)
        self.assertIn("--accent: #ff6a00", self.fetch("/app.css")[2].decode("utf-8"))
        for path in ("/logo.png", "/icon.png"):
            status, headers, data = self.fetch(path)
            self.assertEqual((status, data[:8]), (200, b"\x89PNG\r\n\x1a\n"), path)


@unittest.skipUnless(shutil.which("openssl") and shutil.which("bash"), "servono bash e openssl")
class Installazione(unittest.TestCase):
    def test_installazione_con_un_comando_su_una_copia_pulita(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / "alvea"
            shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(".env", "data", "storage", "__pycache__", ".git"))
            env = {k: v for k, v in os.environ.items() if not k.startswith(("SFB_", "STRIPE_", "OLLAMA_", "SEARXNG_"))}
            env["ALVEA_PASSWORD"] = "una-password-lunga-123"

            def run(*args):
                return subprocess.run(["bash", "deploy/installa.sh", "--prova"] + list(args), cwd=dst, env=env, capture_output=True, text=True, timeout=120)

            self.assertEqual(run().returncode, 2, "senza email si ferma prima di fare qualsiasi cosa")
            self.assertFalse((dst / ".env").exists())
            first = run("--email", "capo@esempio.it", "--porta", "8899")
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertIn("Pacchetti firmati: dev, email, modello, video", first.stdout)
            text = (dst / ".env").read_text(encoding="utf-8")
            self.assertIn("SFB_PORT=8899", text)
            self.assertEqual(os.stat(dst / ".env").st_mode & 0o077, 0, "il file con le chiavi lo legge solo il proprietario")
            try:
                import cryptography  # noqa: F401
                self.assertRegex(text, r"(?m)^SFB_SECRET_KEY=.{40,}$")
            except ImportError:
                pass
            con = sqlite3.connect(str(dst / "data" / "salesforb.db"))
            self.assertEqual([r[0] for r in con.execute("SELECT email FROM users WHERE role='superadmin'")], ["capo@esempio.it"])
            self.assertTrue((dst / "data" / "firma_pubblica.json").is_file())
            self.assertTrue((dst / "storage").is_dir())
            again = run("--email", "altro@esempio.it", "--porta", "7777")
            self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
            self.assertIn("non lo tocco", again.stdout)
            self.assertEqual((dst / ".env").read_text(encoding="utf-8"), text, "rilanciarlo non cambia le impostazioni")
            self.assertEqual([r[0] for r in con.execute("SELECT email FROM users WHERE role='superadmin'")], ["capo@esempio.it"], "né il gestore")
            con.close()
            self.assertEqual(subprocess.run(["bash", "deploy/installa.sh", "--email", "x@y.it"], cwd=dst, env=env, capture_output=True, text=True).returncode, 2,
                             "senza --prova chiede sudo e il dominio" if os.geteuid() else "senza --prova chiede il dominio")


if __name__ == "__main__":
    unittest.main()
