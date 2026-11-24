"""Prova del launcher già costruito (exe per Windows, programma del dmg per macOS) contro un server Alvea vero, acceso qui.

Uso: python strumenti/prova_launcher.py <cartella che contiene il programma>
Fa quello che fa un cliente: scarica dal server il file con l'indirizzo e la chiave, verifica il computer,
collega il computer con un codice usa e getta, lascia il launcher acceso e controlla che il server lo veda.
Esce con 1 se un passaggio non riesce. Gira nelle prove automatiche su macOS e Windows veri.
"""
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = "8799"
BASE = "http://127.0.0.1:" + PORT


def call(method, path, body=None, token=None):
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, json.dumps(body).encode() if body is not None else None, headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
        return json.loads(data) if data[:1] in (b"{", b"[") else data


def main():
    folder = Path(sys.argv[1]).resolve()
    shots = Path(sys.argv[sys.argv.index("--foto") + 1]).resolve() if "--foto" in sys.argv else None
    binary = next((p for p in (folder / "alvea-launcher.exe", folder / "alvea-launcher") if p.is_file()), None)
    if not binary:
        print("ERRORE: in %s non c'è il programma del launcher" % folder)
        return 1
    env = dict(os.environ, SFB_PORT=PORT, SFB_PUBLIC_URL=BASE, SFB_DEMO="1", SFB_DEMO_DELAY_MS="0", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    demo = subprocess.run([sys.executable, "manage.py", "demo"], cwd=str(ROOT), env=env, capture_output=True, text=True, encoding="utf-8").stdout
    password = dict(re.findall(r"email: (\S+)\s+password: (\S+)", demo))["completo@demo.local"]
    log = open(os.path.join(tempfile.mkdtemp(prefix="alvea-server-"), "server.log"), "w+", encoding="utf-8", errors="replace")
    server = subprocess.Popen([sys.executable, "-m", "server"], cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)
    home = tempfile.mkdtemp(prefix="alvea-casa-")
    work = tempfile.mkdtemp(prefix="alvea-lavoro-")
    lenv = dict(os.environ, ALVEA_HOME=home, PYTHONIOENCODING="utf-8")
    failed, running = [], None

    def step(name, ok, detail=""):
        print("%-7s %s%s" % ("OK" if ok else "ERRORE", name, (" — " + detail) if detail else ""), flush=True)
        if not ok:
            failed.append(name)

    def photo(name):
        """Apre la finestra del launcher, la fotografa e la lascia chiudere da sola. Solo su Windows e macOS, dove c'è uno schermo."""
        if not shots:
            return
        shots.mkdir(parents=True, exist_ok=True)
        target = shots / name
        win = subprocess.Popen([str(binary), "finestra"], env=dict(lenv, ALVEA_FINESTRA_PROVA="16"))
        time.sleep(10)
        try:
            if sys.platform == "darwin":
                subprocess.run(["screencapture", "-x", str(target)], timeout=30)
            else:
                from PIL import ImageGrab
                ImageGrab.grab().save(str(target))
        except Exception as e:
            print("foto non riuscita: %s" % e, flush=True)
        alive = win.poll() is None
        try:
            win.wait(timeout=40)
        except Exception:
            win.kill()
        step("la finestra del launcher si apre (%s)" % name, alive and target.is_file() and target.stat().st_size > 5000)

    def launcher(*args):
        r = subprocess.run([str(binary)] + list(args), env=lenv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    try:
        up = False
        for _ in range(120):
            try:
                call("GET", "/api/health")
                up = True
                break
            except Exception:
                if server.poll() is not None:
                    break
                time.sleep(0.5)
        if not up:  # il server non è partito: si mostra perché, invece di un errore di connessione più avanti
            log.seek(0)
            print("ERRORE   il server di prova non è partito (uscita %s). Ultime righe:\n%s" % (server.poll(), log.read()[-3000:]), flush=True)
            return 1
        z = zipfile.ZipFile(io.BytesIO(call("GET", "/api/launcher/download")))
        info = json.loads(z.read("alvea-launcher/server.json"))
        (folder / "server.json").write_text(json.dumps(info), encoding="utf-8")
        step("il server consegna indirizzo e chiave", bool(info.get("public_key")) and info.get("server") == BASE)
        code, out = launcher("verifica")
        print(out[:2500], flush=True)
        agents = len(call("GET", "/api/launcher/catalog")["agents"])
        lines = [l for l in out.splitlines() if re.search(r"GIRA|SOLO SUI NOSTRI SERVER", l)]
        step("verifica del computer senza account", code == 0 and len(lines) >= agents, "%d righe per %d agenti" % (len(lines), agents))
        step("le lettere accentate si leggono", "è" in out or "à" in out or "ò" in out)
        photo("1-prima-del-collegamento.png")
        code, out = launcher("cartella-dev", work)
        step("scelta della cartella di lavoro", code == 0, out.strip()[-160:])
        token = call("POST", "/api/login", {"email": "completo@demo.local", "password": password})["token"]
        pair = call("POST", "/api/devices/code", {}, token)["code"]
        code, out = launcher("collega", pair, "--nome", "Prova automatica")
        step("collegamento con il codice usa e getta", code == 0, out.strip()[-200:])
        photo("2-collegato.png")
        running = subprocess.Popen([str(binary), "avvia"], env=lenv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        device = {}
        for _ in range(40):
            time.sleep(1.5)
            devices = call("GET", "/api/devices", token=token)["devices"]
            device = devices[0] if devices else {}
            if any(k.startswith("pkg:") and v.get("ok") for k, v in (device.get("checks") or {}).items()):
                break
        checks = device.get("checks") or {}
        ready = sorted(k[4:] for k, v in checks.items() if k.startswith("pkg:") and v.get("ok"))
        step("il server vede il computer", device.get("name") == "Prova automatica", "%s, %s core" % ((device.get("specs") or {}).get("os", "?"), (device.get("specs") or {}).get("cores", "?")))
        step("pacchetti scaricati, firma controllata e pronti", "dev" in ready, "pronti: %s" % (", ".join(ready) or "nessuno"))
        step("il launcher resta acceso", running.poll() is None)
        code, out = launcher("collega", pair)
        step("lo stesso codice non vale due volte", code != 0)
    finally:
        for proc in (running, server):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except Exception:
                    proc.kill()
    print("\nProva del launcher %s: %d passaggi falliti." % ("NON superata" if failed else "superata", len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
