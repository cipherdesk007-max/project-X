"""Prova di carico «come al lancio»: un server vero, installato da zero, con molte aziende che lavorano insieme.

Che cosa fa girare, tutto nello stesso momento:
- per ogni azienda due persone che chattano con gli agenti, fanno scrivere lead, aprono le schermate;
- per ogni azienda un computer collegato (un launcher finto) che aspetta lavori e li esegue;
- visitatori sul sito pubblico e qualcuno che sbaglia password;
- poi, a metà di un'altra ondata, il server viene spento di colpo (come una mancanza di corrente) e riavviato.

Che cosa controlla alla fine: nessun errore interno, tempi di risposta, ogni azienda vede solo i suoi dati, il registro di ogni
azienda è una catena intatta, il database è integro, nessun lavoro è rimasto appeso, la memoria non è cresciuta senza motivo.

Il modello è finto (risponde dopo un'attesa imitata): la prova misura il server, non i fornitori dei modelli.

Uso:  python3 strumenti/prova_carico.py [aziende=24] [secondi=40]
"""
import http.client
import json
import os
import random
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MODEL_MS = int(os.environ.get("ALVEA_CARICO_MODELLO_MS", "700"))
os.environ["SFB_DEMO_DELAY_MS"] = str(MODEL_MS)
from server import fake_llm  # noqa: E402

PW = "password-di-prova-1"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Carico:
    def __init__(self, tenants, seconds):
        self.tenants_n, self.seconds = tenants, seconds
        self.failed, self.count = [], 0
        self.samples, self.lock = [], threading.Lock()   # (nome, stato, secondi)
        self.proc = None

    # ---------- attrezzi ----------
    def step(self, name, ok, detail=""):
        self.count += 1
        print("%-7s %s%s" % ("OK" if ok else "ERRORE", name, (" — " + str(detail)[:400]) if detail and not ok else ""), flush=True)
        if not ok:
            self.failed.append(name)
        return ok

    def http(self, method, path, body=None, token=None, name=None, timeout=120):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        if token:
            headers["Authorization"] = "Bearer " + token
        started = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, data, headers, method=method), timeout=timeout) as r:
                status, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read()
        except (OSError, urllib.error.URLError, http.client.HTTPException) as e:
            status, raw = 0, json.dumps({"error": repr(e)}).encode()
        try:
            out = json.loads(raw)
        except ValueError:
            out = raw.decode("utf-8", "replace")
        if name:
            with self.lock:
                self.samples.append((name, status, time.time() - started))
        return status, out

    def manage(self, *args):
        r = subprocess.run([sys.executable, "manage.py"] + list(args), cwd=str(self.copy), env=self.env, capture_output=True, text=True, timeout=300)
        return r.returncode, r.stdout + r.stderr

    def start(self):
        self.log = open(str(self.work / "server.log"), "a", encoding="utf-8")
        self.proc = subprocess.Popen([sys.executable, "-m", "server"], cwd=str(self.copy), env=self.env, stdout=self.log, stderr=subprocess.STDOUT)
        for _ in range(200):
            if self.http("GET", "/api/health", timeout=3)[0] == 200:
                return True
            if self.proc.poll() is not None:
                return False
            time.sleep(0.15)
        return False

    def vitals(self):
        """Memoria (MB), fili e file aperti del server, letti dal sistema."""
        try:
            status = Path("/proc/%d/status" % self.proc.pid).read_text()
            rss = int(re.search(r"VmRSS:\s+(\d+)", status).group(1)) / 1024.0
            threads = int(re.search(r"Threads:\s+(\d+)", status).group(1))
            return round(rss, 1), threads, len(os.listdir("/proc/%d/fd" % self.proc.pid))
        except (OSError, AttributeError):
            return None, None, None

    def db(self):
        con = sqlite3.connect(str(self.copy / "data" / "salesforb.db"), timeout=30)
        con.row_factory = sqlite3.Row
        return con

    # ---------- preparazione ----------
    def prepare(self):
        self.work = Path(tempfile.mkdtemp(prefix="alvea-carico-"))
        self.copy = self.work / "alvea"
        shutil.copytree(str(ROOT), str(self.copy), ignore=shutil.ignore_patterns(".git", ".env", "data", "storage", "__pycache__", "tests", "*.pyc"))
        class Quieto(ThreadingHTTPServer):  # quando il server viene spento di colpo il modello finto resta a parlare da solo: non interessa
            request_queue_size = 1024  # il modello finto non deve essere lui il collo di bottiglia

            def handle_error(self, request, client_address):
                pass
        self.llm = Quieto(("127.0.0.1", 0), fake_llm._Handler)
        self.llm.requests, self.llm.daemon_threads, self.llm.request_queue_size = [], True, 512
        threading.Thread(target=self.llm.serve_forever, daemon=True).start()
        port = free_port()
        self.base = "http://127.0.0.1:%d" % port
        self.env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")}
        self.env.update({"PYTHONIOENCODING": "utf-8", "ANTHROPIC_BASE_URL": "http://127.0.0.1:%d" % self.llm.server_address[1]})
        code, out = self.manage("init", "--email", "gestore@prova.it", "--nome", "Gestore", "--password", PW)
        env_file = self.copy / ".env"
        text = env_file.read_text(encoding="utf-8")
        for name, value in (("SFB_DEMO", "0"), ("SFB_PORT", str(port)), ("SFB_PUBLIC_URL", self.base), ("ANTHROPIC_API_KEY", "sk-ant-prova-di-carico")):
            text, n = re.subn(r"(?m)^%s=.*$" % name, "%s=%s" % (name, value), text)
            if not n:
                text += "\n%s=%s\n" % (name, value)
        env_file.write_text(text, encoding="utf-8")
        if not self.step("il server parte", code == 0 and self.start(), out):
            return False
        self.boss = self.http("POST", "/api/login", {"email": "gestore@prova.it", "password": PW})[1]["token"]
        self.tenants = []
        started = time.time()
        for i in range(self.tenants_n):
            status, out = self.http("POST", "/api/admin/tenants", {"name": "Azienda %03d" % i, "package_id": "completo", "admin_email": "admin@a%03d.it" % i,
                                                                   "admin_name": "Admin %d" % i, "admin_password": PW}, self.boss)
            if status != 200:
                return self.step("creazione delle aziende", False, out)
            admin = self.http("POST", "/api/login", {"email": "admin@a%03d.it" % i, "password": PW}, name="accesso")[1]["token"]
            self.http("POST", "/api/users", {"email": "op@a%03d.it" % i, "name": "Operatore %d" % i, "role": "member", "password": PW}, admin)
            member = self.http("POST", "/api/login", {"email": "op@a%03d.it" % i, "password": PW}, name="accesso")[1]["token"]
            code = self.http("POST", "/api/devices/code", {}, admin)[1]["code"]
            device = self.http("POST", "/api/launcher/pair", {"code": code, "name": "PC %03d" % i, "specs": {"os": "linux", "cores": 4},
                                                              "checks": {"pkg:dev": {"ok": True}}, "launcher_version": "1.2.0"})[1]
            self.tenants.append({"n": i, "id": out["id"], "admin": admin, "member": member, "device": device["token"], "leads": 0, "jobs": 0})
        self.step("%d aziende create, ognuna con due persone e un computer collegato (%.0f secondi)" % (self.tenants_n, time.time() - started), True)
        return True

    # ---------- chi lavora ----------
    def person(self, tenant, token, stop, seed):
        rnd = random.Random(seed)
        marker = "T%03d" % tenant["n"]
        while not stop.is_set():
            roll = rnd.random()
            if roll < 0.35:
                self.http("POST", "/api/chat", {"message": "Buongiorno, riassumi le priorità di oggi per %s" % marker, "agent_id": "centrale", "model": "claude"}, token, "chat")
            elif roll < 0.50:
                with self.lock:
                    tenant["leads"] += 1
                    k = tenant["leads"]
                status, out = self.http("POST", "/api/chat", {"message": "aggiungi lead Cliente %s-%d cliente%d@%s.example.org" % (marker, k, k, marker.lower()),
                                                             "agent_id": "crm", "model": "claude"}, token, "chat con strumento")
                done = status == 200 and [e.get("status") for e in out.get("events", [])] == ["eseguito"]
                with self.lock:
                    tenant.setdefault("written", []).append(k) if done else tenant.setdefault("lost", []).append((k, status, str(out)[:120]))
            elif roll < 0.56:
                status, out = self.http("POST", "/api/chat", {"message": "elenca i file della cartella", "agent_id": "dev", "model": "claude"}, token, "lavoro sul computer")
                ok = status == 200 and ("carico-%03d.txt" % tenant["n"]) in str(out.get("reply"))
                with self.lock:
                    tenant["jobs"] += 1
                    if not ok:
                        tenant.setdefault("bad_jobs", []).append((status, str(out)[:160]))
            elif roll < 0.72:
                self.http("GET", "/api/summary", token=token, name="riepilogo")
            elif roll < 0.84:
                status, out = self.http("GET", "/api/data/leads", token=token, name="elenco lead")
                foreign = [l for l in (out.get("leads") or out.get("items") or []) if marker not in json.dumps(l)] if isinstance(out, dict) else []
                if foreign:
                    with self.lock:
                        tenant.setdefault("foreign", []).extend(foreign[:3])
            elif roll < 0.92:
                self.http("GET", "/api/news", token=token, name="novità")
            else:
                self.http("GET", "/api/me", token=token, name="profilo")
            stop.wait(rnd.uniform(0.15, 0.7))

    def computer(self, tenant, stop):
        """Il launcher di un'azienda: aspetta un lavoro, lo esegue (qui: risponde con l'elenco dei file) e manda il risultato."""
        while not stop.is_set():
            status, out = self.http("POST", "/api/launcher/jobs/claim", {"wait": 5}, tenant["device"], "launcher in attesa", timeout=40)
            job = out.get("job") if isinstance(out, dict) else None
            if status == 200 and job:
                self.http("POST", "/api/launcher/jobs/%d/result" % job["id"], {"ok": True, "text": "carico-%03d.txt (12 byte)" % tenant["n"]}, tenant["device"], "risultato del lavoro")
            elif status != 200:
                stop.wait(0.5)

    def visitor(self, stop, seed):
        rnd = random.Random(seed)
        while not stop.is_set():
            roll = rnd.random()
            if roll < 0.5:
                self.http("GET", "/", name="sito pubblico")
            elif roll < 0.8:
                self.http("GET", "/api/register", name="modulo di registrazione")
            else:
                status, _ = self.http("POST", "/api/login", {"email": "nessuno%d@esempio.it" % rnd.randint(0, 5), "password": "sbagliata-%d" % rnd.randint(0, 9)}, name="accesso sbagliato")
            stop.wait(rnd.uniform(0.1, 0.5))

    def wave(self, seconds, label):
        """Fa partire tutti. Ritorna la funzione che li ferma: prima le persone, e solo quando hanno finito i computer collegati,
        così l'ultimo lavoro chiesto trova ancora il suo computer acceso. Ritorna i fili rimasti bloccati."""
        stop, off, people, computers = threading.Event(), threading.Event(), [], []
        for t in self.tenants:
            people.append(threading.Thread(target=self.person, args=(t, t["admin"], stop, t["n"] * 2), daemon=True))
            people.append(threading.Thread(target=self.person, args=(t, t["member"], stop, t["n"] * 2 + 1), daemon=True))
            computers.append(threading.Thread(target=self.computer, args=(t, off), daemon=True))
        for i in range(6):
            people.append(threading.Thread(target=self.visitor, args=(stop, 1000 + i), daemon=True))
        print("\n== %s: %d fili per %d secondi (modello finto: %d ms a risposta)" % (label, len(people) + len(computers), seconds, MODEL_MS), flush=True)
        for t in people + computers:
            t.start()

        def finish():
            stop.set()
            for t in people:
                t.join(90)
            off.set()
            for t in computers:
                t.join(60)
            return [t for t in people + computers if t.is_alive()]
        return finish

    # ---------- le fasi ----------
    def load(self):
        idle = self.vitals()
        finish = self.wave(self.seconds, "Carico")
        peak = idle
        end = time.time() + self.seconds
        while time.time() < end:
            time.sleep(2)
            now = self.vitals()
            if now[0] and now[0] > (peak[0] or 0):
                peak = now
        stuck = finish()
        time.sleep(3)
        after = self.vitals()
        self.report()
        self.step("nessun filo della prova è rimasto bloccato in attesa del server", not stuck, len(stuck))
        bad = [(n, s) for n, s, _ in self.samples if s >= 500 or s == 0]
        self.step("nessun errore interno e nessuna connessione caduta su %d richieste" % len(self.samples), not bad, sorted(set(bad))[:8])
        unexpected = [(n, s) for n, s, _ in self.samples if 400 <= s < 500 and not (n == "accesso sbagliato" and s in (401, 429))]
        self.step("nessun rifiuto inatteso (gli unici «no» sono le password sbagliate)", not unexpected, sorted(set(unexpected))[:8])
        fast = [d for n, s, d in self.samples if n in ("riepilogo", "elenco lead", "novità", "profilo", "sito pubblico", "modulo di registrazione")]
        p95 = sorted(fast)[int(len(fast) * 0.95)] if fast else 99
        self.step("le schermate restano svelte sotto carico: 95 su 100 in meno di 1,5 secondi (misurato: %.2f s)" % p95, p95 < 1.5, p95)
        chats = sorted(d for n, s, d in self.samples if n == "chat")
        over = (chats[int(len(chats) * 0.95)] - MODEL_MS / 1000.0) if chats else 99
        self.step("una chat costa al server meno di 2 secondi oltre il tempo del modello, 95 volte su 100 (misurato: %.2f s)" % over, over < 2.0, over)
        self.step("memoria sotto carico entro 600 MB e fili tornati a riposo (a riposo %s MB e %s fili; al massimo %s MB e %s fili; dopo %s MB e %s fili)" % (
            idle[0], idle[1], peak[0], peak[1], after[0], after[1]), bool(peak[0]) and peak[0] < 600 and after[1] <= idle[1] + 12, (idle, peak, after))
        self.step("i file aperti tornano quelli di prima (prima %s, dopo %s)" % (idle[2], after[2]), after[2] is not None and after[2] <= idle[2] + 12, (idle, after))
        self.invariants("dopo il carico")

    def invariants(self, when):
        con = self.db()
        foreign, missing, extra, lost = [], [], [], []
        for t in self.tenants:
            rows = [dict(r) for r in con.execute("SELECT * FROM leads WHERE tenant_id=?", (t["id"],))]
            marker = "T%03d" % t["n"]
            names = [json.dumps(r, ensure_ascii=False) for r in rows]
            foreign += [n[:80] for n in names if marker not in n] + t.get("foreign", [])
            have = {int(m.group(1)) for n in names for m in [re.search(r"%s-(\d+)" % marker, n)] if m}
            missing += [(marker, k) for k in t.get("written", []) if k not in have]
            extra += [(marker, len(rows), len(have))] if len(rows) != len(have) else []
            lost += [(marker,) + x for x in t.get("lost", [])]
        self.step("%s: ogni azienda ha nei suoi lead solo i suoi, e chi li legge vede solo i suoi" % when, not foreign, foreign[:5])
        self.step("%s: ogni lead che l'agente ha detto di aver scritto c'è davvero, una volta sola" % when, not missing and not extra, (missing[:5], extra[:5]))
        if when == "dopo il carico":
            self.step("nessuna richiesta di scrivere un lead è andata persa sotto carico", not lost, lost[:5])
            bad_jobs = [(t["n"], t["bad_jobs"][:2]) for t in self.tenants if t.get("bad_jobs")]
            jobs = sum(t["jobs"] for t in self.tenants)
            self.step("tutti i %d lavori mandati ai computer collegati sono tornati con il risultato del computer giusto" % jobs, jobs > 0 and not bad_jobs, bad_jobs[:4])
            hanging = con.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('in_coda','in_corso')").fetchone()[0]
            self.step("nessun lavoro rimasto appeso", hanging == 0, hanging)
        check = con.execute("PRAGMA integrity_check").fetchone()[0]
        orphans = con.execute("SELECT COUNT(*) FROM tenants t WHERE NOT EXISTS(SELECT 1 FROM users u WHERE u.tenant_id=t.id AND u.role='admin')").fetchone()[0]
        con.close()
        self.step("%s: il database è integro e nessuna azienda è senza amministratore" % when, check == "ok" and orphans == 0, (check, orphans))
        code, out = self.manage("verifica-registro")
        self.step("%s: il registro di ogni azienda è una catena intatta" % when, code == 0 and "ROTT" not in out.upper() and "ERRORE" not in out.upper(), out[-500:])
        log = (self.work / "server.log").read_text(encoding="utf-8", errors="replace")
        self.step("%s: nel registro del server non c'è nessun errore interno né «database is locked»" % when, "Traceback" not in log and "database is locked" not in log,
                  log[max(0, log.find("Traceback") - 200):][:1500] if "Traceback" in log else log[log.find("database is locked") - 300:][:600])

    def report(self):
        by = {}
        for name, status, d in self.samples:
            by.setdefault(name, []).append(d)
        print("\n%-26s %8s %8s %8s %8s" % ("richiesta", "quante", "metà", "95 su 100", "peggiore"))
        for name in sorted(by, key=lambda n: -len(by[n])):
            v = sorted(by[name])
            print("%-26s %8d %7.2fs %8.2fs %7.2fs" % (name, len(v), v[len(v) // 2], v[int(len(v) * 0.95)], v[-1]))
        total = len(self.samples)
        print("%-26s %8d  (%.0f al secondo)\n" % ("in tutto", total, total / max(1, self.seconds)), flush=True)

    def slow_clients(self):
        """Trecento connessioni che si aprono e non dicono niente (un attacco semplice, o una rete pessima): chi lavora non deve accorgersene."""
        print("\n== Connessioni ferme", flush=True)
        host, port = self.base.split("//")[1].split(":")
        held = []
        for i in range(300):
            try:
                s = socket.create_connection((host, int(port)), timeout=5)
                s.sendall(b"POST /api/chat HTTP/1.1\r\nHost: x\r\nContent-Length: 500\r\n\r\n{" if i % 2 else b"GET /api/me HTTP/1.1\r\nHost: x\r\n")
                held.append(s)
            except OSError:
                break
        times = []
        t = self.tenants[0]
        for _ in range(20):
            started = time.time()
            status, _ = self.http("GET", "/api/summary", token=t["admin"], timeout=20)
            times.append((status, time.time() - started))
        worst = max(d for _, d in times)
        self.step("con %d connessioni ferme aperte, le richieste vere passano lo stesso (la peggiore: %.2f s)" % (len(held), worst),
                  len(held) >= 250 and all(s == 200 for s, _ in times) and worst < 2.0, times[:5])
        for s in held:
            try:
                s.close()
            except OSError:
                pass
        time.sleep(2)
        now = self.vitals()
        self.step("chiuse quelle connessioni, il server libera i fili (ora %s)" % now[1], now[1] is not None and now[1] < 60, now)

    def blackout(self):
        """A metà di un'ondata il server viene ucciso senza preavviso, come una mancanza di corrente, e riavviato."""
        before = len(self.samples)
        finish = self.wave(8, "Spegnimento brusco a metà lavoro")
        time.sleep(5)
        self.proc.send_signal(signal.SIGKILL)
        self.proc.wait(timeout=20)
        finish()
        cut = len([1 for _, s, _ in self.samples[before:] if s == 0 or s >= 500])
        print("spento di colpo: %d richieste erano in corso o sono arrivate a server spento" % cut, flush=True)
        self.samples = self.samples[:before]
        started = time.time()
        self.step("il server riparte da solo sullo stesso database", self.start(), "vedi %s" % (self.work / "server.log"))
        print("ripartito in %.1f secondi" % (time.time() - started), flush=True)
        t = self.tenants[0]
        status, out = self.http("GET", "/api/summary", token=t["admin"])
        self.step("chi era dentro è ancora dentro (la sessione vale ancora)", status == 200, (status, out))
        status, out = self.http("POST", "/api/chat", {"message": "aggiungi lead Cliente T000-9999 dopo@t000.example.org", "agent_id": "crm", "model": "claude"}, t["admin"])
        if status == 200 and [e.get("status") for e in out.get("events", [])] == ["eseguito"]:
            t.setdefault("written", []).append(9999)
        self.step("dopo il riavvio gli agenti lavorano e scrivono", status == 200 and 9999 in t.get("written", []), (status, out))
        # i lavori che erano in mano a un computer quando è mancata la corrente: non devono restare «in corso» per sempre
        con = self.db()
        con.execute("UPDATE jobs SET claimed_at=claimed_at-100000 WHERE status='in_corso'")
        con.commit()
        con.close()
        finish = self.wave(6, "Ripresa")
        time.sleep(6)
        finish()
        con = self.db()
        hanging = con.execute("SELECT COUNT(*) FROM jobs WHERE status='in_corso' AND claimed_at < ?", (int(time.time()) - 3600,)).fetchone()[0]
        con.close()
        self.step("i lavori rimasti a metà allo spegnimento vengono ripresi o chiusi, non restano appesi", hanging == 0, hanging)
        self.invariants("dopo lo spegnimento brusco")

    def run(self):
        try:
            if self.prepare():
                self.load()
                self.slow_clients()
                self.blackout()
        except Exception:
            import traceback
            self.step("la prova stessa si è interrotta", False, traceback.format_exc()[-1200:])
        finally:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            self.llm.shutdown()
            if not self.failed and not os.environ.get("ALVEA_TIENI"):
                shutil.rmtree(str(self.work), ignore_errors=True)
            else:
                print("\nCartella della prova tenuta: %s" % self.work)
        print("\nProva di carico %s: %d controlli, %d falliti." % ("NON superata" if self.failed else "superata", self.count, len(self.failed)))
        return 1 if self.failed else 0


if __name__ == "__main__":
    args = [int(a) for a in sys.argv[1:3]]
    sys.exit(Carico(args[0] if args else 24, args[1] if len(args) > 1 else 40).run())
