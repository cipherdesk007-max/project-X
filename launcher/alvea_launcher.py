#!/usr/bin/env python3
"""Alvea Launcher: il programma che gira sul computer del cliente.

Cosa fa:
  verifica      controlla questo computer e dice, agente per agente, se può girare qui. Non serve un account.
  collega       collega questo computer a un'azienda con il codice creato nel Centro di controllo, scheda Computer.
  avvia         scarica i pacchetti scelti dall'azienda, ne controlla la firma ed esegue i lavori che arrivano.
  posta, cartella-dev, comando-dev, modello     impostazioni che restano SOLO su questo computer.

Non contiene chiavi di modelli o servizi e non ne riceve. Usa solo la libreria standard di Python (3.9 o successivo).
"""
import argparse
import getpass
import hashlib
import http.client
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VERSION = "1.0.0"
# Nel programma già pronto (dmg per macOS, exe per Windows) i file accanto sono quelli accanto all'eseguibile, non al codice impacchettato.
HERE = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
HOME = Path(os.environ.get("ALVEA_HOME") or str(Path.home() / ".alvea"))
DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")
EXTRA_PATHS = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", r"C:\ffmpeg\bin", r"C:\Program Files\ffmpeg\bin"]
VERDICTS = {"gira": "GIRA", "non_gira": "NON GIRA", "solo_cloud": "SOLO SUI NOSTRI SERVER", "in_arrivo": "COMPUTER ADATTO, COLLEGAMENTO IN ARRIVO"}


class LauncherError(Exception):
    def __init__(self, message, status=0):
        Exception.__init__(self, message)
        self.status = status


# ---------- impostazioni ----------

def load_cfg():
    cfg = {"server": "", "public_key": None, "token": "", "pacchetti": {}}
    for p in (HERE / "server.json", HOME / "launcher.json"):
        if p.is_file():
            cfg.update(json.loads(p.read_text(encoding="utf-8")))
    return cfg


def save_cfg(cfg):
    HOME.mkdir(parents=True, exist_ok=True)
    p = HOME / "launcher.json"
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        p.chmod(0o600)  # qui dentro ci sono il gettone del computer e, se inserita, la password della posta
    except OSError:
        pass


# ---------- colloquio con il server ----------

class Api:
    def __init__(self, cfg):
        self.base, self.token = str(cfg.get("server") or "").rstrip("/"), cfg.get("token") or ""
        if not self.base.startswith(("http://", "https://")):
            raise LauncherError("Manca l'indirizzo del server Alvea (file server.json oppure --server).")

    def call(self, method, path, body=None, timeout=40):
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode("utf-8") if body is not None else None, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                message = json.loads(e.read(4000).decode("utf-8")).get("error") or ""
            except ValueError:
                message = ""
            raise LauncherError(message or "Il server ha risposto con errore %d." % e.code, e.code)
        except (OSError, ValueError) as e:
            raise LauncherError("Server non raggiungibile: %s" % getattr(e, "reason", e))

    def download(self, url, dest):
        """Scarica un file dell'azienda. Accetta solo collegamenti firmati di questo stesso server."""
        if not str(url).startswith("/api/files/"):
            raise LauncherError("Collegamento al file non valido.")
        try:
            with urllib.request.urlopen(self.base + url, timeout=120) as r, open(dest, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
        except OSError as e:
            raise LauncherError("Non riesco a scaricare un file dall'archivio: %s" % getattr(e, "reason", getattr(e, "code", e)))
        return dest

    def upload(self, path, file_path, name):
        """Consegna un file prodotto da un lavoro, letto a pezzi: va bene anche per video grandi."""
        boundary = "----alvea" + hashlib.sha256(os.urandom(16)).hexdigest()[:24]
        head = ('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\nContent-Type: application/octet-stream\r\n\r\n' % (
            boundary, name.replace('"', "_"))).encode("utf-8")
        tail = ("\r\n--%s--\r\n" % boundary).encode("ascii")

        def parts():
            yield head
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    yield chunk
            yield tail

        u = urllib.parse.urlsplit(self.base)
        con = (http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection)(u.hostname, u.port, timeout=600)
        try:
            con.request("POST", path, body=parts(), headers={"Content-Type": "multipart/form-data; boundary=" + boundary, "Authorization": "Bearer " + self.token,
                                                              "Content-Length": str(len(head) + os.path.getsize(file_path) + len(tail))})
            r = con.getresponse()
            data = json.loads(r.read().decode("utf-8") or "{}")
        except (OSError, ValueError) as e:
            raise LauncherError("Consegna del file non riuscita: %s" % e)
        finally:
            con.close()
        if r.status != 200:
            raise LauncherError(data.get("error") or "Il server ha rifiutato il file (%d)." % r.status, r.status)
        return data


# ---------- questo computer ----------

def ram_gb():
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return round(int(line.split()[1]) / 1048576.0, 1)
        if sys.platform == "darwin":
            return round(int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout) / 1073741824.0, 1)
        if os.name == "nt":
            import ctypes

            class Mem(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong)] + [
                    (n, ctypes.c_ulonglong) for n in ("a", "b", "c", "d", "e", "f")]
            m = Mem()
            m.length = ctypes.sizeof(Mem)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return round(m.total / 1073741824.0, 1)
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1073741824.0, 1)
    except (OSError, ValueError, AttributeError):
        return 0


def specs():
    HOME.mkdir(parents=True, exist_ok=True)
    system = {"linux": "linux", "darwin": "macos", "win32": "windows"}.get(sys.platform, sys.platform)
    return {"os": system, "os_version": platform.platform()[:80], "arch": platform.machine(), "cores": os.cpu_count() or 1, "ram_gb": ram_gb(),
            "disk_free_gb": round(shutil.disk_usage(str(HOME)).free / 1073741824.0, 1), "python": platform.python_version(), "launcher": VERSION}


def find_tool(name):
    return shutil.which(name) or next((str(Path(d) / (name + ext)) for d in EXTRA_PATHS for ext in ("", ".exe") if (Path(d) / (name + ext)).is_file()), None)


def need_ok(need):
    label = need.get("label") or need.get("bin") or need.get("module") or need.get("service")
    if need.get("bin"):
        return (True, "") if find_tool(need["bin"]) else (False, "manca il programma %s" % label)
    if need.get("module"):
        try:
            return (True, "") if importlib.util.find_spec(need["module"]) else (False, "manca %s" % label)
        except (ImportError, ValueError):
            return False, "manca %s" % label
    if need.get("service"):
        host = urllib.parse.urlsplit(need["service"]).hostname
        if host not in ("127.0.0.1", "localhost"):
            return False, "servizio non locale"
        try:
            urllib.request.urlopen(need["service"], timeout=2).close()
            return True, ""
        except urllib.error.HTTPError:
            return True, ""  # risponde, anche se con un errore: il servizio è acceso
        except OSError:
            return False, "%s non è acceso su questo computer" % label
    return True, ""


def check_spec(spec, sp):
    why = []
    if sp["os"] not in (spec.get("os") or []):
        why.append("sistema %s non previsto" % sp["os"])
    if sp["ram_gb"] and sp["ram_gb"] < spec.get("ram_gb", 0) * 0.9:  # un computer «da 8 GB» ne dichiara un po' meno
        why.append("memoria %.1f GB, ne servono %d" % (sp["ram_gb"], spec["ram_gb"]))
    if sp["disk_free_gb"] < spec.get("disk_gb", 0):
        why.append("spazio libero %.0f GB, ne servono %d" % (sp["disk_free_gb"], spec["disk_gb"]))
    if sp["cores"] < spec.get("cores", 1):
        why.append("processore con %d core, ne servono %d" % (sp["cores"], spec["cores"]))
    for need in spec.get("needs") or []:
        ok, reason = need_ok(need)
        if not ok:
            why.append(reason)
    return not why, why


# ---------- pacchetti firmati ----------

def verify(public, raw, signature_hex):
    """Firma RSA PKCS#1 v1.5 con SHA-256. Senza una firma valida il codice non viene né salvato né eseguito."""
    try:
        n, e, sig = int(public["n"], 16), int(public["e"]), bytes.fromhex(signature_hex)
    except (KeyError, ValueError, TypeError):
        return False
    size = (n.bit_length() + 7) // 8
    if len(sig) != size or size < 256:
        return False
    digest = DIGEST_INFO + hashlib.sha256(raw).digest()
    return pow(int.from_bytes(sig, "big"), e, n).to_bytes(size, "big") == b"\x00\x01" + b"\xff" * (size - len(digest) - 3) + b"\x00" + digest


def install(api, cfg, name):
    """Scarica un pacchetto, controlla la firma con la chiave pubblica arrivata insieme al launcher, lo salva. Ritorna la versione."""
    if not name.isalpha() or not name.islower():
        raise LauncherError("Nome di pacchetto non valido.")
    if not cfg.get("public_key"):
        raise LauncherError("Questo launcher non ha la chiave per controllare i pacchetti: scaricalo di nuovo dal sito.")
    pkg = api.call("GET", "/api/launcher/packages/" + name)
    raw = json.dumps({"name": pkg["name"], "version": pkg["version"], "files": pkg["files"]}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if pkg["name"] != name or not verify(cfg["public_key"], raw, pkg.get("signature") or ""):
        raise LauncherError("FIRMA NON VALIDA per il pacchetto «%s»: non lo installo." % name)
    folder = HOME / "packages" / name
    if folder.exists():
        shutil.rmtree(str(folder))
    for rel, text in pkg["files"].items():
        if ".." in rel or rel.startswith(("/", "\\")) or not rel.endswith((".py", ".json")):
            raise LauncherError("File non ammesso nel pacchetto «%s»." % name)
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text(text, encoding="utf-8")
    (folder / ".sha256").write_text(hashlib.sha256(raw).hexdigest(), encoding="ascii")
    return pkg["version"]


def installed_sha(name):
    p = HOME / "packages" / name / ".sha256"
    return p.read_text(encoding="ascii").strip() if p.is_file() else ""


def load_package(name):
    path = HOME / "packages" / name / "worker.py"
    if not path.is_file():
        raise LauncherError("Pacchetto «%s» non installato." % name)
    spec = importlib.util.spec_from_file_location("sfb_pacchetto_" + name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Env:
    """Quello che un pacchetto può usare: una cartella temporanea, le sue impostazioni locali, i programmi del computer."""

    def __init__(self, api, cfg, package, workdir, job_id=None):
        self.api, self.workdir, self.job_id = api, workdir, job_id
        self.config = dict((cfg.get("pacchetti") or {}).get(package) or {})

    def tool(self, name):
        return find_tool(name)

    def fetch(self, asset):
        ext = os.path.splitext(str(asset.get("name") or ""))[1].lower()[:6]
        return self.api.download(asset.get("url"), os.path.join(self.workdir, "file_%d%s" % (int(asset["id"]), ext if ext.replace(".", "").isalnum() else "")))

    def progress(self, pct, note=""):
        if self.job_id:
            try:
                self.api.call("POST", "/api/launcher/jobs/%d/progress" % self.job_id, {"progress": int(pct), "note": note}, timeout=15)
            except LauncherError:
                pass  # l'avanzamento è un'informazione: se non arriva il lavoro continua


# ---------- verifica ----------

def verdicts(api, cfg, sp, log=None):
    """Per ogni agente: può girare su questo computer? Ritorna {"agent:<id>" | "pkg:<nome>": {ok, verdict, why}} e il catalogo."""
    cat, out, pkg_done = api.call("GET", "/api/launcher/catalog"), {}, {}

    def package_check(name, spec):
        if name in pkg_done:
            return pkg_done[name]
        ok, why = check_spec(spec, sp)
        text = "; ".join(why)
        if ok:
            try:
                if installed_sha(name) == "" or log:
                    install(api, cfg, name)
                with tempfile.TemporaryDirectory(prefix="sfb-verifica-") as tmp:
                    ok, text = load_package(name).check(Env(api, cfg, name, tmp))
            except LauncherError as e:
                ok, text = False, str(e)
            except Exception as e:
                ok, text = False, "la prova del pacchetto è fallita (%s)" % type(e).__name__
        pkg_done[name] = (ok, text)
        out["pkg:" + name] = {"ok": ok, "verdict": "gira" if ok else "non_gira", "why": text}
        return pkg_done[name]

    for a in cat["agents"]:
        spec = a["run"].get("local")
        if not spec:
            v = {"ok": False, "verdict": "solo_cloud", "why": "usa servizi in rete: gira sui nostri server"}
        elif spec.get("ready"):
            ok, text = package_check(spec["package"], spec)
            v = {"ok": ok, "verdict": "gira" if ok else "non_gira", "why": text + ("" if ok or not a["run"]["cloud"] else " — puoi usarlo sui nostri server")}
        else:
            ok, why = check_spec(spec, sp)
            v = {"ok": False, "verdict": "in_arrivo" if ok else "non_gira",
                 "why": ("%s presente; per ora l'agente gira sui nostri server" % spec["engine"]) if ok else "; ".join(why) + " — gira sui nostri server"}
        out["agent:" + a["id"]] = v
        if log:
            log("  %-14s %-40s %s" % (a["name"], VERDICTS[v["verdict"]], v["why"]))
    if cat.get("model"):
        ok, text = package_check(cat["model"]["package"], cat["model"])
        if log:
            log("  %-14s %-40s %s" % ("Modello locale", "GIRA" if ok else "NON GIRA", text + ("" if ok else " — restano i modelli via API")))
    return out, cat


def cmd_verifica(cfg, args):
    api, sp = Api(cfg), specs()
    print("Computer: %s, %d core, %.0f GB di memoria, %.0f GB liberi, Python %s" % (sp["os_version"], sp["cores"], sp["ram_gb"], sp["disk_free_gb"], sp["python"]))
    print("Server:   %s\n" % api.base)
    checks, _ = verdicts(api, cfg, sp, log=print)
    if api.token:
        api.call("POST", "/api/launcher/report", {"specs": sp, "checks": checks, "launcher_version": VERSION})
        print("\nVerifica inviata all'azienda: la trovi nel Centro di controllo, scheda Computer.")
    else:
        print("\nQuesto computer non è ancora collegato a un'azienda: la verifica resta qui. Per collegarlo: collega CODICE")
    return 0


def cmd_collega(cfg, args):
    api, sp = Api(dict(cfg, token="")), specs()
    checks, _ = verdicts(api, cfg, sp)
    out = api.call("POST", "/api/launcher/pair", {"code": args.codice, "name": args.nome or platform.node() or "Computer", "specs": sp, "checks": checks,
                                                 "launcher_version": VERSION})
    cfg["token"] = out["token"]
    save_cfg(cfg)
    print("Collegato a «%s» come «%s»." % (out["tenant"], out["name"]))
    return 0


# ---------- lavori ----------

def execute(api, cfg, job):
    with tempfile.TemporaryDirectory(prefix="sfb-lavoro-") as tmp:
        try:
            module = load_package(job["package"])
            if job["tool"] not in module.TOOLS:
                raise LauncherError("Il pacchetto «%s» non ha lo strumento richiesto." % job["package"])
            out = module.TOOLS[job["tool"]](job["args"], Env(api, cfg, job["package"], tmp, job["id"]))
            if out.get("file"):
                api.upload("/api/launcher/jobs/%d/file" % job["id"], out["file"], out.get("file_name") or "risultato.mp4")
            ok, text = True, out.get("text") or "Fatto."
        except Exception as e:
            known = isinstance(e, LauncherError) or type(e).__name__ == "WorkerError"
            ok, text = False, str(e) if known else "Errore imprevisto sul computer: %s" % type(e).__name__
    api.call("POST", "/api/launcher/jobs/%d/result" % job["id"], {"ok": ok, "text": text})
    return ok, text


def synchronize(api, cfg, log=print):
    """Allinea i pacchetti a quelli scelti dall'azienda e manda la verifica aggiornata."""
    wanted = api.call("GET", "/api/launcher/sync")
    for p in wanted["packages"]:
        if installed_sha(p["name"]) != p["sha256"]:
            log("Installo il pacchetto «%s» %s" % (p["name"], install(api, cfg, p["name"])))
    sp = specs()
    checks, _ = verdicts(api, cfg, sp)
    api.call("POST", "/api/launcher/report", {"specs": sp, "checks": checks, "launcher_version": VERSION})
    return wanted


def run(cfg, stop=None, log=print, wait=15, sync_every=120):
    api, last = Api(cfg), 0
    if not api.token:
        raise LauncherError("Questo computer non è collegato: usa prima il comando collega.")
    stop = stop or threading.Event()
    while not stop.is_set():
        try:
            if time.time() - last > sync_every:
                wanted = synchronize(api, cfg, log)
                if not last:
                    log("In ascolto per «%s». Lascia aperta questa finestra. Per uscire: Ctrl+C." % wanted["tenant"])
                last = time.time()
            job = api.call("POST", "/api/launcher/jobs/claim", {"wait": wait}, timeout=wait + 20)["job"]
            if job:
                if job["tool"] != "llm_chat":
                    log("Lavoro n. %d: %s" % (job["id"], job["tool"]))
                ok, text = execute(api, cfg, job)
                if job["tool"] != "llm_chat":
                    log("  %s: %s" % ("fatto" if ok else "NON riuscito", text[:160]))
        except LauncherError as e:
            if e.status == 401:
                raise LauncherError("Questo computer è stato scollegato dall'azienda. Per ricollegarlo serve un codice nuovo.")
            log("Problema: %s. Riprovo tra poco." % e)
            stop.wait(5)
    return 0


def cmd_avvia(cfg, args):
    try:
        return run(cfg)
    except KeyboardInterrupt:
        print("\nLauncher chiuso.")
        return 0


def _set(cfg, package, **values):
    cfg.setdefault("pacchetti", {}).setdefault(package, {}).update({k: v for k, v in values.items() if v not in (None, "")})
    save_cfg(cfg)


def cmd_posta(cfg, args):
    _set(cfg, "email", imap_host=args.imap_host or input("Server IMAP (es. imap.tuodominio.it): "), smtp_host=args.smtp_host or input("Server SMTP: "),
         utente=args.utente or input("Indirizzo email: "), password=args.password or getpass.getpass("Password della casella (resta su questo computer): "),
         imap_port=args.imap_port, smtp_port=args.smtp_port)
    print("Casella salvata su questo computer. Il server Alvea non la riceve.")
    return 0


def cmd_cartella(cfg, args):
    folder = Path(args.percorso).expanduser().resolve()
    if not folder.is_dir():
        raise LauncherError("La cartella non esiste: %s" % folder)
    _set(cfg, "dev", cartella=str(folder))
    print("L'Agente Dev potrà leggere e scrivere solo dentro: %s" % folder)
    return 0


def cmd_comando(cfg, args):
    if not args.nome.isalnum() or not args.riga:
        raise LauncherError("Uso: comando-dev NOME programma argomenti...  (es. comando-dev prove python3 -m unittest)")
    commands = dict(((cfg.get("pacchetti") or {}).get("dev") or {}).get("comandi") or {})
    commands[args.nome.lower()] = list(args.riga)
    _set(cfg, "dev", comandi=commands)
    print("Comando permesso «%s»: %s" % (args.nome.lower(), " ".join(args.riga)))
    return 0


def cmd_modello(cfg, args):
    _set(cfg, "modello", indirizzo=args.indirizzo, modello=args.nome)
    print("Modello locale: %s" % json.dumps((cfg.get("pacchetti") or {}).get("modello") or {}, ensure_ascii=False))
    return 0


def cmd_guidato(cfg, args):
    print("Alvea Launcher %s\n" % VERSION)
    cmd_verifica(cfg, args)
    if not cfg.get("token"):
        code = input("\nCodice di collegamento (Centro di controllo, scheda Computer), oppure Invio per uscire: ").strip()
        if not code:
            return 0
        args.codice, args.nome = code, None
        cmd_collega(cfg, args)
    print()
    return cmd_avvia(load_cfg(), args)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # su Windows la finestra dei comandi non è in UTF-8: senza questo le lettere accentate fermano il programma
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(description="Alvea Launcher")
    p.add_argument("--server", help="indirizzo del server Alvea, se diverso da quello nel file server.json")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("verifica").set_defaults(fn=cmd_verifica)
    s = sub.add_parser("collega")
    s.add_argument("codice")
    s.add_argument("--nome")
    s.set_defaults(fn=cmd_collega)
    sub.add_parser("avvia").set_defaults(fn=cmd_avvia)
    s = sub.add_parser("posta")
    for flag in ("--imap-host", "--smtp-host", "--utente", "--password", "--imap-port", "--smtp-port"):
        s.add_argument(flag)
    s.set_defaults(fn=cmd_posta)
    s = sub.add_parser("cartella-dev")
    s.add_argument("percorso")
    s.set_defaults(fn=cmd_cartella)
    s = sub.add_parser("comando-dev")
    s.add_argument("nome")
    s.add_argument("riga", nargs=argparse.REMAINDER)
    s.set_defaults(fn=cmd_comando)
    s = sub.add_parser("modello")
    s.add_argument("--indirizzo")
    s.add_argument("--nome")
    s.set_defaults(fn=cmd_modello)
    args = p.parse_args(argv)
    cfg = load_cfg()
    if args.server:
        cfg["server"] = args.server
    try:
        return (args.fn if args.cmd else cmd_guidato)(cfg, args)
    except LauncherError as e:
        print("Errore: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
