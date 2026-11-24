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


def window_flow(binary, token, work, shots, step, first_device, first_process):
    """La finestra provata davvero: un copione le fa premere i suoi pulsanti (lo stesso gesto di un clic, fatto dal programma),
    poi dalla finestra collegata passa un lavoro vero dell'Agente Dev. Serve uno schermo: gira su Windows e macOS."""
    home = tempfile.mkdtemp(prefix="alvea-casa-finestra-")
    env = dict(os.environ, ALVEA_HOME=home, PYTHONIOENCODING="utf-8")
    script, result = Path(home) / "copione.json", Path(home) / "esito.json"

    def play(steps, code="", stay=0):
        if result.exists():
            result.unlink()
        script.write_text(json.dumps({"server": BASE, "codice": code, "passi": steps, "esito": str(result), "resta": stay}), encoding="utf-8")
        return subprocess.Popen([str(binary), "finestra"], env=dict(env, ALVEA_FINESTRA_COPIONE=str(script)))

    def outcome(seconds=150):
        for _ in range(seconds * 2):
            if result.is_file() and result.stat().st_size > 2:
                time.sleep(0.3)
                return json.loads(result.read_text(encoding="utf-8"))
            time.sleep(0.5)
        return None

    def shot(name):
        try:
            if sys.platform == "darwin":
                subprocess.run(["screencapture", "-x", str(shots / name)], timeout=30)
            else:
                from PIL import ImageGrab
                ImageGrab.grab().save(str(shots / name))
        except Exception as e:
            print("foto non riuscita: %s" % e, flush=True)

    def close(win):
        try:
            win.wait(timeout=60)
        except Exception:
            win.kill()

    win = play(["verifica"], stay=3)
    r = outcome() or {"passi": [{"stato": "la finestra non ha scritto l'esito", "errore": True}], "righe": []}
    shot("3-verifica-dal-pulsante.png")
    close(win)
    agents = [l for l in r["righe"] if l.startswith(("●", "○"))]
    step("nella finestra, il pulsante «Verifica questo computer» fa la verifica", not r["passi"][0]["errore"] and "Verifica fatta" in r["passi"][0]["stato"] and len(agents) >= 3,
         "%s — %d agenti elencati" % (r["passi"][0]["stato"][:70], len(agents)))
    win = play(["collega"], code="", stay=1)
    r = outcome() or {"passi": [{"stato": "", "errore": False}]}
    close(win)
    step("senza codice il pulsante «Collega» spiega cosa manca", r["passi"][0]["errore"] and "codice" in r["passi"][0]["stato"], r["passi"][0]["stato"][:90])
    win = play(["collega"], code="AAAA-AAAA", stay=1)
    r = outcome() or {"passi": [{"stato": "", "errore": False}]}
    close(win)
    step("con un codice sbagliato il pulsante «Collega» lo dice", r["passi"][0]["errore"] and not r.get("in_ascolto"), r["passi"][0]["stato"][:90])
    # il primo computer, collegato da riga di comando, si spegne: il lavoro deve passare dalla finestra
    if first_process and first_process.poll() is None:
        first_process.terminate()
    if first_device.get("id"):
        call("DELETE", "/api/devices/%d" % first_device["id"], token=token)
    subprocess.run([str(binary), "cartella-dev", work], env=env, capture_output=True, timeout=120)
    (Path(work) / "prova-alvea.txt").write_text("un file messo qui dalla prova", encoding="utf-8")
    win = play(["collega"], code=call("POST", "/api/devices/code", {}, token)["code"], stay=120)
    r = outcome() or {"passi": [{"stato": "la finestra non ha scritto l'esito", "errore": True, "in_ascolto": False}]}
    time.sleep(2)
    shot("4-collegato-dal-pulsante.png")
    step("nella finestra, il pulsante «Collega» collega il computer e resta in ascolto", not r["passi"][0]["errore"] and r.get("in_ascolto") is True, r["passi"][0]["stato"][:90])
    ready = False
    for _ in range(40):
        devices = [d for d in call("GET", "/api/devices", token=token)["devices"] if d.get("online") and ((d.get("checks") or {}).get("pkg:dev") or {}).get("ok")]
        if devices:
            ready = True
            break
        time.sleep(1.5)
    step("il server vede il computer collegato dalla finestra, con l'Agente Dev pronto", ready)
    try:
        reply = call("POST", "/api/chat", {"message": "elenca i file della cartella", "agent_id": "dev", "model": "kimi"}, token)
    except Exception as e:
        reply = {"reply": "errore: %s" % e, "events": []}
    step("un lavoro vero passa dalla finestra: l'Agente Dev elenca i file della cartella sul computer",
         "prova-alvea.txt" in str(reply.get("reply")) and [e.get("status") for e in reply.get("events", [])] == ["eseguito"], str(reply.get("reply"))[:160])
    jobs = call("GET", "/api/devices", token=token).get("jobs") or [{}]
    step("il server segna il lavoro come fatto da quel computer", (jobs[0].get("tool"), jobs[0].get("status")) == ("dev_elenca_file", "fatto") and bool(jobs[0].get("device")),
         "%s, %s, computer «%s»" % (jobs[0].get("tool"), jobs[0].get("status"), jobs[0].get("device")))
    if win.poll() is None:
        win.terminate()
    close(win)


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
        if shots:
            window_flow(binary, token, work, shots, step, device, running)
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
