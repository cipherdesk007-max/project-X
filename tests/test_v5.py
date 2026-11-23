"""Prove della versione 5: launcher, pacchetti locali firmati, lavori sul computer e sul server, modello locale."""
import importlib.util
import io
import json
import os
import shutil
import socketserver
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from base import PW, ROOT, Base
from server import catalog, config, db, llm, local, ops

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
_spec = importlib.util.spec_from_file_location("sfb_launcher", str(ROOT / "launcher" / "alvea_launcher.py"))
L = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(L)
MAIL = {"sent": [], "logins": []}


class FakeSMTP(socketserver.StreamRequestHandler):
    def handle(self):
        w = lambda s: self.wfile.write((s + "\r\n").encode())
        w("220 finto")
        data, in_data = [], False
        for raw in self.rfile:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if in_data:
                if line == ".":
                    MAIL["sent"].append("\n".join(data)); in_data = False; w("250 ok")
                else:
                    data.append(line)
            elif line.upper().startswith("EHLO"):
                w("250 finto")
            elif line.upper() == "DATA":
                in_data = True; w("354 vai")
            elif line.upper() == "QUIT":
                w("221 ciao"); return
            else:
                w("250 ok")


class FakeIMAP(socketserver.StreamRequestHandler):
    def handle(self):
        w = lambda s: self.wfile.write((s + "\r\n").encode())
        w("* OK finto")
        for raw in self.rfile:
            tag, _, rest = raw.decode().strip().partition(" ")
            cmd = rest.split(" ")[0].upper()
            if cmd == "CAPABILITY":
                w("* CAPABILITY IMAP4rev1")
            elif cmd == "LOGIN":
                MAIL["logins"].append(rest)
            elif cmd == "SELECT":
                w("* 1 EXISTS")
            elif cmd == "SEARCH":
                w("* SEARCH 1")
            elif cmd == "FETCH":
                head = b"From: Cliente <cliente@esempio.it>\r\nSubject: Preventivo tagliando\r\nDate: Mon, 23 Nov 2026 10:00:00 +0100\r\n\r\n"
                self.wfile.write(b"* 1 FETCH (BODY[HEADER.FIELDS (FROM SUBJECT DATE)] {%d}\r\n" % len(head) + head + b")\r\n")
            w("%s OK fatto" % tag)
            if cmd == "LOGOUT":
                return


class FakeSearx(BaseHTTPRequestHandler):
    allow = True

    def log_message(self, *a):
        pass

    def do_GET(self):
        ok = FakeSearx.allow and "format=json" in self.path
        data = json.dumps({"results": [{"title": "Officina aperta", "url": "https://esempio.it/officina", "content": "Orari <b>nuovi</b>"}]}).encode() if ok else b"no"
        self.send_response(200 if ok else 403)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def make_media(folder):
    out = {}
    def ff(name, *args):
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + list(args) + [str(folder / name)], check=True)
        out[name] = (folder / name).read_bytes()
    ff("uno.mp4", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=2", "-f", "lavfi", "-i", "sine=frequency=500:duration=2", "-pix_fmt", "yuv420p", "-shortest")
    ff("due.mp4", "-f", "lavfi", "-i", "color=c=blue:size=640x360:rate=15:duration=1", "-pix_fmt", "yuv420p")  # senza audio
    ff("foto.png", "-f", "lavfi", "-i", "color=c=red:size=200x200", "-frames:v", "1")
    ff("musica.mp3", "-f", "lavfi", "-i", "sine=frequency=220:duration=3")
    return out


class V5(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.signed = local.ensure_signed()
        cls.tmp = Path(tempfile.mkdtemp(prefix="sfb-v5-"))
        cls.media = make_media(cls.tmp) if FFMPEG else {}
        cls.servers = []
        for handler in (FakeSMTP, FakeIMAP):
            srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
            srv.daemon_threads = True
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            cls.servers.append(srv)
        cls.searx = ThreadingHTTPServer(("127.0.0.1", 0), FakeSearx)
        threading.Thread(target=cls.searx.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        for srv in cls.servers + [cls.searx]:
            srv.shutdown()
        shutil.rmtree(str(cls.tmp), ignore_errors=True)
        super().tearDownClass()

    # ---------- attrezzi ----------
    def dev_call(self, method, path, body=None, token=None):
        return self.call(method, path, body, token)

    def pair(self, admin, name="PC di prova", pacchetti=None, run=True):
        """Collega un launcher vero all'azienda e, se richiesto, lo lascia in ascolto in un filo a parte."""
        L.HOME = Path(tempfile.mkdtemp(prefix="sfb-home-", dir=str(self.tmp)))
        cfg = {"server": self.base, "public_key": local.public_key(), "token": "", "pacchetti": pacchetti or {}}
        code = self.call("POST", "/api/devices/code", {}, admin)[1]["code"]
        api = L.Api(cfg)
        checks, _ = L.verdicts(api, cfg, L.specs())
        out = api.call("POST", "/api/launcher/pair", {"code": code, "name": name, "specs": L.specs(), "checks": checks, "launcher_version": L.VERSION})
        cfg["token"] = out["token"]
        if run:
            stop = threading.Event()
            t = threading.Thread(target=L.run, args=(cfg, stop, lambda *a: None, 1, 3600), daemon=True)
            t.start()
            self.addCleanup(lambda: (stop.set(), t.join(8)))
            for _ in range(100):
                if any(d["online"] for d in self.call("GET", "/api/devices", token=admin)[1]["devices"]):
                    break
                time.sleep(0.05)
        return cfg

    def mode(self, admin, agent_id, mode):
        return self.call("POST", "/api/tenant/agents", {"agent_id": agent_id, "mode": mode}, admin)

    def load_media(self, token, names=("uno.mp4", "due.mp4", "foto.png", "musica.mp3")):
        return {n: self.upload(token, n, self.media[n])[1]["id"] for n in names}

    def jobs(self, admin):
        return self.call("GET", "/api/devices", token=admin)[1]["jobs"]

    def wait_job(self, admin, status="fatto", seconds=60):
        end = time.time() + seconds
        while time.time() < end:
            j = self.jobs(admin)
            if j and j[0]["status"] in ("fatto", "errore"):
                self.assertEqual(j[0]["status"], status, j[0]["result"])
                return j[0]
            time.sleep(0.1)
        self.fail("lavoro non finito: %s" % self.jobs(admin)[:1])

    def approve_last(self, admin):
        pending = [a for a in self.call("GET", "/api/approvals", token=admin)[1]["approvals"] if a["status"] == "pending"]
        return self.call("POST", "/api/approvals/%d/approve" % pending[0]["id"], {}, admin)[1]

    def probe(self, data):
        p = self.tmp / ("esito-%d.mp4" % time.time_ns())
        p.write_bytes(data)
        out = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height", "-of", "json", str(p)],
                                        capture_output=True, text=True).stdout)
        video = [s for s in out["streams"] if s["codec_type"] == "video"][0]
        return float(out["format"]["duration"]), video["width"], video["height"], sorted(s["codec_type"] for s in out["streams"])

    # ---------- catalogo e firma ----------
    def test_ogni_agente_dice_dove_puo_girare_e_il_catalogo_e_pubblico(self):
        status, cat = self.call("GET", "/api/launcher/catalog")
        self.assertEqual(status, 200)
        self.assertEqual(len(cat["agents"]), 18)
        self.assertEqual({a["id"] for a in cat["agents"]} - {a["id"] for a in catalog.load()["agents"]}, set())
        for a in cat["agents"]:
            self.assertIn(a["run"]["cloud"], (True, False), a["id"])
            self.assertTrue(a["run"]["cloud"] or (a["run"]["local"] and a["run"]["local"]["ready"]), "%s deve poter girare da qualche parte" % a["id"])
            if a["run"]["local"] and a["run"]["local"]["ready"]:
                self.assertIn(a["run"]["local"]["package"], local.package_names(), a["id"])
        self.assertEqual({a["id"] for a in cat["agents"] if a["run"]["local"] and a["run"]["local"]["ready"]}, {"video", "dev", "email"})
        self.assertNotIn("sk-", json.dumps(cat))

    @unittest.skipUnless(shutil.which("openssl"), "serve openssl")
    def test_pacchetto_manomesso_o_firmato_da_altri_non_viene_installato(self):
        self.assertTrue(self.signed)
        L.HOME = Path(tempfile.mkdtemp(dir=str(self.tmp)))
        cfg = {"server": self.base, "public_key": local.public_key(), "token": ""}
        api = L.Api(cfg)
        self.assertEqual(L.install(api, cfg, "dev"), "1.0.0")
        self.assertTrue((L.HOME / "packages" / "dev" / "worker.py").is_file())
        real = api.call

        def tampered(method, path, body=None, timeout=40):
            out = real(method, path, body, timeout)
            out["files"]["worker.py"] += "\nimport os  # codice aggiunto da un intruso\n"
            return out

        api.call = tampered
        with self.assertRaises(L.LauncherError) as e:
            L.install(api, cfg, "video")
        self.assertIn("FIRMA NON VALIDA", str(e.exception))
        self.assertFalse((L.HOME / "packages" / "video").exists(), "niente su disco se la firma non torna")
        api.call = real
        other = dict(local.public_key(), n=hex(int(local.public_key()["n"], 16) + 2)[2:])
        with self.assertRaises(L.LauncherError):
            L.install(api, dict(cfg, public_key=other), "video")
        with self.assertRaises(L.LauncherError):
            L.install(api, dict(cfg, public_key=None), "video")
        for url in ("http://127.0.0.1:1/api/files/1/content", "https://esempio.it/api/files/1/content", "/etc/passwd", "//esempio.it/api/files/1"):
            with self.assertRaises(L.LauncherError) as e:  # il launcher scarica solo collegamenti firmati del proprio server
                api.download(url, str(self.tmp / "non-deve-esistere"))
            self.assertIn("non valido", str(e.exception), url)
        self.assertFalse((self.tmp / "non-deve-esistere").exists())
        body, raw = local.bundle("video")
        sig = local.signed("video")["signature"]
        self.assertTrue(local.verify(local.public_key(), raw, sig))
        self.assertFalse(local.verify(local.public_key(), raw + b" ", sig))
        self.assertFalse(local.verify(local.public_key(), raw, "00" * (len(sig) // 2)))
        self.assertFalse(local.verify({"n": "ff", "e": 65537}, raw, "01"))

    def test_controllo_prima_della_produzione(self):
        spec = importlib.util.spec_from_file_location("sfb_manage", str(ROOT / "manage.py"))
        manage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(manage)
        report = dict((text.split(":")[0].split(" ")[0], state) for state, text in manage.production_report())
        states = [s for s, _ in manage.production_report()]
        self.assertIn("DA FARE", states, "in demo e senza https il server non è pronto per i clienti")
        texts = " ".join(t for _, t in manage.production_report())
        self.assertIn("SFB_DEMO=1", texts)
        self.assertIn("https", texts)
        self.assertNotIn(str(config.get("SFB_SECRET_KEY") or "nessuna-chiave-impostata"), texts, "il rapporto non stampa segreti")
        self.assertTrue(report)

    def test_launcher_scaricabile_senza_segreti(self):
        status, headers, data = self.fetch("/api/launcher/download")
        self.assertEqual(status, 200)
        z = zipfile.ZipFile(io.BytesIO(data))
        names = z.namelist()
        self.assertIn("alvea-launcher/alvea_launcher.py", names)
        info = json.loads(z.read("alvea-launcher/server.json"))
        self.assertEqual(info["public_key"], local.public_key())
        everything = b"".join(z.read(n) for n in names)
        self.assertNotIn(b"BEGIN", everything, "la chiave privata non viaggia mai")
        self.assertNotIn(b"API_KEY", everything)
        self.assertFalse(any(n.endswith((".pem", ".env", ".db")) for n in names))

    # ---------- collegamento dei computer ----------
    def test_codice_usa_e_getta_e_gettone_valido_solo_per_il_launcher(self):
        a, tid = self.new_tenant("Computer A", "completo")
        b, _ = self.new_tenant("Computer B", "completo")
        con = db.connect()
        ops.create_user(con, tid, "operatore@computer-a.it", "Operatore", "member", PW)
        con.close()
        self.assertEqual(self.call("POST", "/api/devices/code", {}, self.login("operatore@computer-a.it"))[0], 403)
        code = self.call("POST", "/api/devices/code", {}, a)[1]["code"]
        self.assertEqual(self.call("POST", "/api/launcher/pair", {"code": "AAAA-AAAA"})[0], 403)
        status, out = self.call("POST", "/api/launcher/pair", {"code": code.lower(), "name": "Portatile"})
        self.assertEqual((status, out["tenant"]), (200, "Computer A"))
        self.assertEqual(self.call("POST", "/api/launcher/pair", {"code": code})[0], 403, "il codice vale una volta sola")
        tok = out["token"]
        self.assertEqual(self.call("GET", "/api/launcher/sync", token=tok)[0], 200)
        for path in ("/api/me", "/api/devices", "/api/summary", "/api/files"):
            self.assertEqual(self.call("GET", path, token=tok)[0], 401, "il gettone del computer non apre %s" % path)
        self.assertEqual(self.call("POST", "/api/chat", {"message": "ciao", "agent_id": "centrale", "model": "claude"}, tok)[0], 401)
        for path in ("/api/launcher/sync",):
            self.assertEqual(self.call("GET", path, token=a)[0], 401, "il gettone di un utente non vale come computer")
            self.assertEqual(self.call("GET", path)[0], 401)
        self.assertEqual(self.call("POST", "/api/launcher/jobs/claim", {}, a)[0], 401)
        self.assertEqual([d["name"] for d in self.call("GET", "/api/devices", token=a)[1]["devices"]], ["Portatile"])
        self.assertEqual(self.call("GET", "/api/devices", token=b)[1]["devices"], [], "l'altra azienda non vede il computer")
        dev_id = self.call("GET", "/api/devices", token=a)[1]["devices"][0]["id"]
        self.assertEqual(self.call("DELETE", "/api/devices/%d" % dev_id, token=b)[0], 404)
        self.assertEqual(self.call("DELETE", "/api/devices/%d" % dev_id, token=a)[0], 200)
        self.assertEqual(self.call("GET", "/api/launcher/sync", token=tok)[0], 401, "computer scollegato")
        con = db.connect()
        con.execute("UPDATE device_codes SET expires_at=1")
        old = self.call("POST", "/api/devices/code", {}, a)[1]["code"]
        con.execute("UPDATE device_codes SET expires_at=1")
        con.close()
        self.assertEqual(self.call("POST", "/api/launcher/pair", {"code": old})[0], 403, "codice scaduto")
        con = db.connect()
        self.assertNotIn(tok, json.dumps([dict(r) for r in con.execute("SELECT * FROM devices")]), "sul server resta solo l'impronta del gettone")
        con.close()

    def test_dove_gira_un_agente_si_puo_scegliere_solo_se_ha_senso(self):
        a, _ = self.new_tenant("Dove Gira", "completo")
        self.assertEqual(self.mode(a, "shopify", "locale")[0], 400)
        self.assertEqual(self.mode(a, "ascolto", "locale")[0], 400, "verifica soltanto: il collegamento non è incluso")
        self.assertEqual(self.mode(a, "dev", "cloud")[0], 400)
        self.assertEqual(self.mode(a, "video", "altrove")[0], 400)
        self.assertEqual(self.mode(a, "video", "locale")[0], 200)
        agents = {x["agent_id"]: x for x in self.call("GET", "/api/tenant/agents", token=a)[1]["agents"]}
        self.assertEqual((agents["video"]["mode"], agents["dev"]["mode"], agents["crm"]["mode"]), ("locale", "locale", "cloud"))
        self.assertTrue(agents["video"]["is_active"], "cambiare dove gira non spegne l'agente")

    @unittest.skipUnless(FFMPEG, "serve FFmpeg")
    def test_verifica_del_computer_senza_account(self):
        L.HOME = Path(tempfile.mkdtemp(dir=str(self.tmp)))
        lines = []
        checks, _ = L.verdicts(L.Api({"server": self.base}), {"server": self.base, "public_key": local.public_key()}, L.specs(), log=lines.append)
        self.assertEqual(checks["agent:video"]["verdict"], "gira", checks["agent:video"])
        self.assertIn("un minuto di video", checks["agent:video"]["why"])
        self.assertEqual(checks["agent:shopify"]["verdict"], "solo_cloud")
        self.assertEqual(checks["agent:ascolto"]["verdict"], "non_gira")
        self.assertIn("faster-whisper", checks["agent:ascolto"]["why"])
        self.assertEqual(checks["agent:dev"]["verdict"], "gira")
        self.assertFalse(checks["pkg:modello"]["ok"], "qui Ollama non c'è")
        self.assertEqual(len(lines), 19, "18 agenti più il modello locale")
        weak = dict(L.specs(), ram_gb=2, cores=1, disk_free_gb=0.5)
        ok, why = L.check_spec(catalog.agent("video")["run"]["local"], weak)
        self.assertFalse(ok)
        self.assertEqual(len(why), 3, why)

    # ---------- video ----------
    @unittest.skipUnless(FFMPEG, "serve FFmpeg")
    def test_montaggio_sul_server_con_file_solo_della_propria_azienda(self):
        a, tid = self.new_tenant("Video Cloud", "completo")
        b, _ = self.new_tenant("Video Cloud Altra", "completo")
        ids, other = self.load_media(a), self.load_media(b, ("foto.png",))
        status, out = self.chat(a, "monta un video bozza: Promo dicembre", agent_id="video")
        self.assertEqual(status, 200, out)
        self.assertIn("in coda sui nostri server", out["reply"])
        job = self.jobs(a)[0]
        self.assertEqual((job["status"], job["place"], job["tool"]), ("in_coda", "cloud", "video_monta"))
        self.assertEqual(self.jobs(b), [])
        self.assertIsNotNone(local.cloud_tick())
        done = self.wait_job(a)
        assets = self.call("GET", "/api/files", token=a)[1]["assets"]
        made = [x for x in assets if x["id"] == done["asset_id"]][0]
        self.assertEqual((made["kind"], made["name"]), ("video", "Promo dicembre.mp4"))
        duration, w, h, streams = self.probe(self.fetch(made["url"])[2])
        self.assertAlmostEqual(duration, 6.0, delta=0.4)   # 2 s + 1 s + immagine 3 s
        self.assertEqual((w, h, streams), (540, 960, ["audio", "video"]))
        _, d = self.call("GET", "/api/office/agents/video", token=a)
        self.assertEqual([(o["kind"], o["label"]) for o in d["outputs"]], [("video", "sui nostri server")])
        self.assertEqual(self.fetch(d["outputs"][0]["url"])[0], 200)
        self.assertTrue(any("Lavoro n. %d finito sui nostri server" % done["id"] in r["text"] for r in d["log"]), d["log"])
        self.assertEqual(self.call("GET", "/api/office/agents/video", token=b)[1]["outputs"], [])
        # un piano non può usare file di un'altra azienda, né una musica che non è audio
        con = db.connect()
        ctx = __import__("server.tools", fromlist=["x"]).Ctx(con, dict(con.execute("SELECT * FROM tenants WHERE id=?", (tid,)).fetchone()), {"id": 1})
        tools = __import__("server.tools", fromlist=["x"])
        with self.assertRaises(tools.ToolError):
            tools.video_prepara(ctx, {"titolo": "x", "clip": str(other["foto.png"])})
        with self.assertRaises(tools.ToolError):
            tools.video_prepara(ctx, {"titolo": "x", "clip": str(ids["musica.mp3"])})
        with self.assertRaises(tools.ToolError):
            tools.video_prepara(ctx, {"titolo": "x", "clip": "tutte", "musica": str(ids["foto.png"])})
        plan = tools.video_prepara(ctx, {"titolo": "Con musica", "clip": "%d,%d" % (ids["foto.png"], ids["uno.mp4"]), "musica": str(ids["musica.mp3"]),
                                         "formato": "quadrato", "secondi_per_clip": 1})["plan"]
        con.close()
        self.assertEqual(([i["asset"]["id"] for i in plan["items"]], plan["width"], plan["music"]["id"]), ([ids["foto.png"], ids["uno.mp4"]], 1080, ids["musica.mp3"]))
        self.assertNotIn("url", json.dumps(plan), "i collegamenti ai file si creano solo quando un computer prende il lavoro")
        con = db.connect()
        ctx = tools.Ctx(con, dict(con.execute("SELECT * FROM tenants WHERE id=?", (tid,)).fetchone()), {"id": 1})
        again = [i["asset"]["id"] for i in tools.video_prepara(ctx, {"titolo": "Secondo", "clip": "tutte"})["plan"]["items"]]
        con.close()
        self.assertNotIn(done["asset_id"], again, "«tutte» non rimonta i video già prodotti")
        self.assertEqual(len(again), 3)

    @unittest.skipUnless(FFMPEG, "serve FFmpeg")
    def test_montaggio_sul_computer_del_cliente_dall_inizio_alla_fine(self):
        a, _ = self.new_tenant("Video Locale", "completo")
        ids = self.load_media(a)
        self.assertEqual(self.mode(a, "video", "locale")[0], 200)
        status, out = self.chat(a, "monta un video bozza: Reel officina", agent_id="video")
        self.assertIn("sul computer dell'azienda", out["reply"])
        self.assertIn("Partirà appena un computer", out["reply"])
        self.assertIsNone(local.cloud_tick(), "un lavoro per il computer non viene eseguito dal server")
        office = self.call("GET", "/api/summary?office=1", token=a)[1]["office"]
        self.assertEqual([(x["state"], x["detail"]) for x in office["agents"] if x["id"] == "video"], [("da_collegare", "Da collegare al suo servizio")])
        self.pair(a, "Mac dello studio")
        done = self.wait_job(a)
        self.assertEqual((done["place"], done["device"], done["progress"]), ("locale", "Mac dello studio", 100))
        made = [x for x in self.call("GET", "/api/files", token=a)[1]["assets"] if x["id"] == done["asset_id"]][0]
        duration, w, h, streams = self.probe(self.fetch(made["url"])[2])
        self.assertAlmostEqual(duration, 6.0, delta=0.4)
        self.assertEqual((w, h), (540, 960))
        status = self.call("GET", "/api/me", token=a)[1]["agent_status"]
        self.assertEqual(status["video"], "Sul computer")
        self.assertEqual([o["label"] for o in self.call("GET", "/api/office/agents/video", token=a)[1]["outputs"]], ["sul computer"])

    def test_stato_sta_montando_e_regole_dei_lavori(self):
        a, tid = self.new_tenant("Video Stati", "completo")
        b, _ = self.new_tenant("Video Stati Altra", "completo")
        self.assertEqual(self.mode(a, "video", "locale")[0], 200)
        self.upload(a, "foto.png", b"\x89PNG\r\n\x1a\n" + b"immagine di prova " * 30)
        cfg = self.pair(a, "Fisso", run=False)
        other = self.pair(b, "Intruso", run=False)
        con = db.connect()
        con.execute("UPDATE devices SET checks=? WHERE tenant_id IN (?, (SELECT id FROM tenants WHERE name='Video Stati Altra'))", (json.dumps({"pkg:video": {"ok": True}}), tid))
        con.close()
        self.assertEqual(self.chat(a, "monta un video: Stati", agent_id="video")[0], 200)
        video = lambda: [x for x in self.call("GET", "/api/summary?office=1", token=a)[1]["office"]["agents"] if x["id"] == "video"][0]
        self.assertEqual(video()["detail"], "1 lavoro in coda: parte tra poco")
        self.assertIsNone(self.call("POST", "/api/launcher/jobs/claim", {}, other["token"])[1]["job"], "il computer di un'altra azienda non riceve il lavoro")
        con = db.connect()
        con.execute("UPDATE devices SET checks=? WHERE tenant_id=?", (json.dumps({"pkg:video": {"ok": False}, "pkg:dev": {"ok": True}}), tid))
        self.assertIsNone(self.call("POST", "/api/launcher/jobs/claim", {}, cfg["token"])[1]["job"], "un computer che non ha passato la verifica non riceve il lavoro")
        con.execute("UPDATE devices SET checks=? WHERE tenant_id=?", (json.dumps({"pkg:video": {"ok": True}}), tid))
        con.close()
        self.assertEqual(self.call("POST", "/api/stop", {"active": True}, a)[0], 200)
        self.assertIsNone(self.call("POST", "/api/launcher/jobs/claim", {}, cfg["token"])[1]["job"], "arresto d'emergenza: niente lavori")
        self.assertEqual(self.call("POST", "/api/stop", {"active": False}, a)[0], 200)
        job = self.call("POST", "/api/launcher/jobs/claim", {}, cfg["token"])[1]["job"]
        self.assertEqual(job["tool"], "video_monta")
        url = job["args"]["plan"]["items"][0]["asset"]["url"]
        self.assertEqual(self.fetch(url)[0], 200, "il computer scarica il file con un collegamento firmato")
        self.assertIsNone(self.call("POST", "/api/launcher/jobs/claim", {}, cfg["token"])[1]["job"], "un lavoro non si prende due volte")
        self.assertEqual((video()["state"], video()["detail"]), ("monta", "Sta montando un video: 0% sul computer «Fisso»"))
        self.assertEqual(self.call("POST", "/api/launcher/jobs/%d/progress" % job["id"], {"progress": 42}, other["token"])[0], 404)
        self.assertEqual(self.call("POST", "/api/launcher/jobs/%d/result" % job["id"], {"ok": True, "text": "finto"}, other["token"])[0], 404)
        self.assertEqual(self.call("POST", "/api/launcher/jobs/%d/progress" % job["id"], {"progress": 42}, cfg["token"])[0], 200)
        self.assertEqual(video()["detail"], "Sta montando un video: 42% sul computer «Fisso»")
        self.assertEqual(self.call("POST", "/api/stop", {"active": True}, a)[0], 200)
        self.assertEqual(video()["state"], "fermo")
        self.assertEqual(self.call("POST", "/api/stop", {"active": False}, a)[0], 200)
        # il computer sparisce a metà: il lavoro torna in coda, e dopo due tentativi viene chiuso come non riuscito
        con = db.connect()
        con.execute("UPDATE jobs SET claimed_at=1 WHERE id=?", (job["id"],))
        again = self.call("POST", "/api/launcher/jobs/claim", {}, cfg["token"])[1]["job"]
        self.assertEqual(again["id"], job["id"])
        con.execute("UPDATE jobs SET claimed_at=1 WHERE id=?", (job["id"],))
        self.assertIsNone(self.call("POST", "/api/launcher/jobs/claim", {}, cfg["token"])[1]["job"])
        con.close()
        self.assertEqual(self.jobs(a)[0]["status"], "errore")
        self.assertEqual(video()["state"], "pronto")

    # ---------- Dev ----------
    def test_agente_dev_lavora_solo_nella_cartella_scelta_e_con_i_comandi_permessi(self):
        a, _ = self.new_tenant("Dev Locale", "completo")
        self.assertIn("Nessun computer", self.chat(a, "elenca i file della cartella", agent_id="dev")[1]["reply"])
        work = Path(tempfile.mkdtemp(dir=str(self.tmp)))
        (work / "app.py").write_text("print('ciao')\n", encoding="utf-8")
        (work.parent / "segreto.txt").write_text("contenuto-segreto-987", encoding="utf-8")
        self.pair(a, "PC sviluppo", {"dev": {"cartella": str(work), "comandi": {"prove": ["python3", "-c", "print('2 prove ok')"]}}})
        self.assertIn("app.py (14 byte)", self.chat(a, "elenca i file della cartella", agent_id="dev")[1]["reply"])
        read = self.chat(a, "leggi il file app.py", agent_id="dev")[1]["reply"]
        self.assertIn("print('ciao')", read)
        self.assertIn("NON FIDATO", read.upper())
        for bad in ("../segreto.txt", "/etc/hostname", "sotto/../../segreto.txt"):
            reply = self.chat(a, "leggi il file %s" % bad, agent_id="dev")[1]["reply"]
            self.assertNotIn("contenuto-segreto-987", reply, bad)
            self.assertIn("Errore", reply, bad)
        out = self.chat(a, "scrivi il file note/todo.txt: prima riga", agent_id="dev")[1]
        self.assertTrue(any(e.get("approval_id") for e in out["events"]), "scrivere sul computer richiede approvazione")
        self.assertFalse((work / "note" / "todo.txt").exists())
        self.assertEqual(self.approve_last(a)["status"], "executed")
        self.assertEqual((work / "note" / "todo.txt").read_text(encoding="utf-8"), "prima riga")
        self.chat(a, "scrivi il file ../fuori.txt: non deve esistere", agent_id="dev")
        self.assertEqual(self.approve_last(a)["status"], "failed")
        self.assertFalse((work.parent / "fuori.txt").exists())
        self.chat(a, "esegui prove", agent_id="dev")
        self.assertIn("2 prove ok", self.approve_last(a)["result"])
        self.chat(a, "esegui cancellatutto", agent_id="dev")
        res = self.approve_last(a)
        self.assertEqual(res["status"], "failed")
        self.assertIn("non permesso", res["result"])
        self.assertEqual(self.call("GET", "/api/me", token=a)[1]["agent_status"]["dev"], "Sul computer")

    # ---------- Email ----------
    def test_posta_dal_computer_e_la_password_non_arriva_al_server(self):
        a, _ = self.new_tenant("Posta Locale", "completo")
        self.assertEqual(self.mode(a, "email", "locale")[0], 200)
        secret = "password-della-casella-XYZ"
        self.pair(a, "PC ufficio", {"email": {"imap_host": "127.0.0.1", "imap_port": self.servers[1].server_address[1], "smtp_host": "127.0.0.1",
                                              "smtp_port": self.servers[0].server_address[1], "utente": "ufficio@esempio.it", "password": secret,
                                              "senza_tls": True, "senza_accesso": True}})
        self.assertIn("Preventivo tagliando", self.chat(a, "leggi la posta", agent_id="email")[1]["reply"])
        self.assertTrue(any(secret in x for x in MAIL["logins"]), "l'accesso alla casella parte dal computer")
        self.chat(a, "invia a cliente@esempio.it: confermo l'appuntamento", agent_id="email")
        self.assertEqual(MAIL["sent"], [])
        self.assertEqual(self.approve_last(a)["status"], "executed")
        self.assertTrue(any("confermo l'appuntamento" in m and "To: cliente@esempio.it" in m for m in MAIL["sent"]), MAIL["sent"])
        raw = sqlite3.connect(str(config.db_path()))
        dump = "\n".join(raw.iterdump())
        raw.close()
        self.assertNotIn(secret, dump, "la password della casella non è mai passata dal server")

    # ---------- modello locale e ricerca privata ----------
    def test_modello_locale_sul_computer_del_cliente(self):
        a, _ = self.new_tenant("Modello Locale", "completo")
        self.assertEqual(self.chat(a, "ciao", model="locale")[0], 200)  # in demo, senza computer, risponde il modello finto del server
        b, _ = self.new_tenant("Modello Locale Altra", "completo")
        models = lambda t: {m["id"]: m["configured"] for m in self.call("GET", "/api/me", token=t)[1]["models"]}
        self.assertIn("locale", models(a))
        real_specs = L.specs
        L.specs = lambda: dict(real_specs(), cores=8, ram_gb=16)  # il computer di prova ha 2 core: per il modello ne servono 4
        self.addCleanup(lambda: setattr(L, "specs", real_specs))
        self.pair(a, "PC con Ollama", {"modello": {"indirizzo": llm.DEMO_BASE + "/v1", "modello": "llama3.1"}})
        self.assertTrue(models(a)["locale"])
        before = len(llm.DEMO_SERVER.requests)
        status, out = self.chat(a, "aggiungi lead Cliente Locale", model="locale")
        self.assertEqual(status, 200, out)
        self.assertIn("Cliente Locale", out["reply"])
        seen = [r["body"] for r in llm.DEMO_SERVER.requests[before:]]
        self.assertTrue(seen and all(r["model"] == "llama3.1" for r in seen), "la richiesta è passata dal computer, con il suo modello")
        self.assertEqual([j for j in self.jobs(a) if j["tool"] == "llm_chat"], [], "le chiamate al modello non affollano l'elenco dei lavori")
        con = db.connect()
        passed = con.execute("SELECT COUNT(*) FROM jobs j JOIN tenants t ON t.id=j.tenant_id WHERE t.name='Modello Locale' AND j.tool='llm_chat' AND j.status='fatto'").fetchone()[0]
        con.close()
        self.assertEqual(passed, len(seen), "ogni richiesta al modello è stata un lavoro eseguito dal computer del cliente")
        self.assertFalse(models(b)["locale"] and not config.demo_mode(), "l'altra azienda non usa il computer di questa")
        with self.assertRaises(Exception) as e:  # il pacchetto parla solo con questo stesso computer
            L.load_package("modello").llm_chat({"body": {}}, type("E", (), {"config": {"indirizzo": "http://esempio.it/v1"}})())
        self.assertIn("su questo computer", str(e.exception))

    def test_ricerca_con_searxng_del_gestore(self):
        a, tid = self.new_tenant("Ricerca Privata", "completo")
        con = db.connect()
        con.execute("UPDATE tenants SET platform_keys=1 WHERE id=?", (tid,))
        con.close()
        os.environ["SEARXNG_URL"] = "http://127.0.0.1:%d" % self.searx.server_address[1]
        self.addCleanup(lambda: os.environ.__setitem__("SEARXNG_URL", ""))
        from server import tools
        con = db.connect()
        ctx = tools.Ctx(con, dict(con.execute("SELECT * FROM tenants WHERE id=?", (tid,)).fetchone()), {"id": 1})
        out = tools.ricerca_web(ctx, {"domanda": "officina aperta"})
        self.assertIn("Officina aperta", out)
        self.assertIn("Orari nuovi", out)
        self.assertIn("https://esempio.it/officina", ctx.allowed_urls)
        FakeSearx.allow = False
        self.addCleanup(lambda: setattr(FakeSearx, "allow", True))
        with self.assertRaises(Exception) as e:
            tools.ricerca_web(ctx, {"domanda": "officina"})
        self.assertIn("json", str(e.exception))
        con.close()
        self.assertEqual(self.call("GET", "/api/me", token=a)[1]["agent_status"]["ricerca"], "Collegato")


if __name__ == "__main__":
    unittest.main()
