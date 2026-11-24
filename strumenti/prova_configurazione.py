"""Prova «configurato dopo»: un server installato da zero, senza niente, a cui il gestore mette le impostazioni dal pannello
mentre è acceso. Per ogni gruppo si controlla com'è PRIMA (un messaggio chiaro, mai un errore interno) e DOPO (la funzione lavora
davvero, con il valore scritto nel pannello), poi si riavvia il server e si controlla che tutto sia rimasto.

I servizi esterni (modelli, Stripe, Brave, SearXNG, Google, posta) sono finti e girano su questo computer: la prova non usa
Internet né chiavi vere. Dice quindi che il percorso pannello → server → servizio funziona, non che il servizio vero accetti.

Uso:  python3 strumenti/prova_configurazione.py
"""
import base64
import email
import hashlib
import hmac
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["SFB_DEMO_DELAY_MS"] = "0"
from server import fake_llm  # noqa: E402  (solo il modello finto: nessun altro pezzo del server gira in questo processo)

PW = "password-di-prova-1"
SEEN = {"llm": [], "servizi": [], "posta": [], "accessi_posta": []}


class Modello(fake_llm._Handler):
    """Il modello finto del server, che in più segna con quale chiave e a quale indirizzo è stato chiamato."""

    def do_POST(self):
        SEEN["llm"].append({"path": self.path, "key": self.headers.get("x-api-key") or self.headers.get("Authorization") or ""})
        super().do_POST()


class Servizi(BaseHTTPRequestHandler):
    """Stripe, Brave, SearXNG e Google finti: rispondono il minimo e segnano che cosa hanno ricevuto."""

    def log_message(self, *a):
        pass

    def _out(self, obj, status=200):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _note(self, form=None):
        parts = urllib.parse.urlsplit(self.path)
        SEEN["servizi"].append({"path": parts.path, "query": dict(urllib.parse.parse_qsl(parts.query)), "form": form or {},
                                "auth": self.headers.get("Authorization") or "", "brave": self.headers.get("X-Subscription-Token") or ""})
        return parts.path

    def do_GET(self):
        path = self._note()
        if path == "/res/v1/web/search":
            return self._out({"web": {"results": [{"title": "Risultato Brave", "url": "https://esempio.it/brave", "description": "trovato con Brave"}]}})
        if path == "/search":
            return self._out({"results": [{"title": "Risultato SearXNG", "url": "https://esempio.it/searx", "content": "trovato con SearXNG"}]})
        if path == "/v1/account":
            return self._out({"id": "acct_prova", "settings": {"dashboard": {"display_name": "Conto di prova"}}})
        if path.startswith("/v1/prices/"):
            pid = path.rsplit("/", 1)[-1]
            if pid == "price_inesistente":
                return self._out({"error": {"message": "No such price: 'price_inesistente'"}}, 404)
            every = None if "crediti" in pid else {"interval": "year" if "annuale" in pid else "month"}
            return self._out({"id": pid, "active": "archiviato" not in pid, "currency": "eur", "unit_amount": 4900, "recurring": every})
        self._out({"error": {"message": "sconosciuto"}}, 404)

    def do_POST(self):
        form = dict(urllib.parse.parse_qsl(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()))
        path = self._note(form)
        if path == "/v1/checkout/sessions":
            return self._out({"id": "cs_prova_1", "url": "http://127.0.0.1:%d/paga/cs_prova_1" % self.server.server_address[1]})
        if path == "/token":
            return self._out({"access_token": "accesso-finto", "refresh_token": "rinnovo-finto", "expires_in": 3600})
        self._out({"error": {"message": "sconosciuto"}}, 404)


class Posta(threading.Thread):
    """Un server di posta finto ma con la cifratura vera: STARTTLS su una porta qualsiasi, oppure cifrato dall'inizio (come la 465)."""

    def __init__(self, cert, key, implicit=False, port=0):
        super().__init__(daemon=True)
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(cert, key)
        self.implicit = implicit
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]

    def run(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self.talk, args=(conn,), daemon=True).start()

    def talk(self, conn):
        try:
            tls = self.implicit
            if tls:
                conn = self.ctx.wrap_socket(conn, server_side=True)
            f = conn.makefile("rwb", 0)
            say = lambda s: f.write((s + "\r\n").encode())  # noqa: E731
            say("220 finto ESMTP")
            data, in_data = [], False
            while True:
                raw = f.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                up = line.upper()
                if in_data:
                    if line == ".":
                        SEEN["posta"].append("\n".join(data))
                        data, in_data = [], False
                        say("250 ok")
                    else:
                        data.append(line)
                elif up.startswith(("EHLO", "HELO")):
                    say("250-finto")
                    say("250 AUTH PLAIN" if tls else "250 STARTTLS")  # la password si accetta solo dopo la cifratura
                elif up == "STARTTLS":
                    say("220 pronto")
                    conn = self.ctx.wrap_socket(conn, server_side=True)
                    f, tls = conn.makefile("rwb", 0), True
                    say = lambda s, f=f: f.write((s + "\r\n").encode())  # noqa: E731
                elif up.startswith("AUTH PLAIN"):
                    who = base64.b64decode(line.split()[2]).decode("utf-8", "replace").split("\x00")
                    SEEN["accessi_posta"].append({"utente": who[1], "password": who[2], "cifrato": tls})
                    say("235 ok")
                elif up == "DATA":
                    in_data = True
                    say("354 vai")
                elif up == "QUIT":
                    say("221 ciao")
                    break
                else:
                    say("250 ok")
            conn.close()
        except (OSError, ssl.SSLError, IndexError, ValueError):
            pass


def serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.requests, server.daemon_threads = [], True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:%d" % server.server_address[1]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Prova:
    def __init__(self):
        self.failed, self.count, self.proc, self.touched = [], 0, None, set()

    def step(self, name, ok, detail=""):
        self.count += 1
        print("%-7s %s%s" % ("OK" if ok else "ERRORE", name, (" — " + str(detail)[:260]) if detail and not ok else ""), flush=True)
        if not ok:
            self.failed.append(name)
        return ok

    def title(self, text):
        print("\n== %s" % text, flush=True)

    def http(self, method, path, body=None, token=None, raw=None, headers=None, cookie=None):
        """Ritorna (stato, risposta, intestazioni). Non segue i reindirizzamenti: servono per controllare dove mandano."""
        h = dict(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if body is not None:
            h["Content-Type"] = "application/json"
        if token:
            h["Authorization"] = "Bearer " + token
        if cookie:
            h["Cookie"] = cookie

        class Still(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        req = urllib.request.Request(self.base + path, data, h, method=method)
        try:
            r = urllib.request.build_opener(Still).open(req, timeout=90)
            status, out, heads = r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            status, out, heads = e.code, e.read(), e.headers
        try:
            out = json.loads(out)
        except ValueError:
            out = out.decode("utf-8", "replace")
        return status, out, heads

    def login(self, email):
        status, out, _ = self.http("POST", "/api/login", {"email": email, "password": PW})
        assert status == 200, (email, status, out)
        return out["token"]

    def save(self, **values):
        status, out, _ = self.http("POST", "/api/admin/settings", {"values": values}, self.boss)
        if status == 200:
            self.touched.update(k for k, v in values.items() if v != "")
        return status, out

    def chat(self, token, message, model="claude", agent="centrale"):
        status, out, _ = self.http("POST", "/api/chat", {"message": message, "agent_id": agent, "model": model}, token)
        return status, (out.get("reply") or out.get("error") or "") if isinstance(out, dict) else str(out), out

    def letter(self, n, seconds=10):
        """Il testo dell'email numero `n` ricevuta dal finto server di posta, decodificato come lo leggerebbe una persona. Alcune partono un attimo dopo la risposta."""
        end = time.time() + seconds
        while len(SEEN["posta"]) <= n and time.time() < end:
            time.sleep(0.1)
        if len(SEEN["posta"]) <= n:
            return ""
        msg = email.message_from_string(SEEN["posta"][n])
        return (msg.get_payload(decode=True) or b"").decode(msg.get_content_charset() or "utf-8", "replace")

    def manage(self, *args):
        r = subprocess.run([sys.executable, "manage.py"] + list(args), cwd=str(self.copy), env=self.env, capture_output=True, text=True, timeout=240)
        if os.environ.get("ALVEA_MOSTRA"):  # per leggere a occhio quello che vedrebbe il gestore
            print("\n--- manage.py %s\n%s---" % (" ".join(args[:1]), r.stdout + r.stderr), flush=True)
        return r.returncode, r.stdout + r.stderr

    def start(self):
        self.log = open(str(self.work / "server.log"), "a", encoding="utf-8")
        self.proc = subprocess.Popen([sys.executable, "-m", "server"], cwd=str(self.copy), env=self.env, stdout=self.log, stderr=subprocess.STDOUT)
        for _ in range(150):
            try:
                if self.http("GET", "/api/health")[0] == 200:
                    return True
            except OSError:
                pass
            if self.proc.poll() is not None:
                break
            time.sleep(0.2)
        return False

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    # ---------- preparazione: come una macchina appena installata ----------
    def prepare(self):
        self.work = Path(tempfile.mkdtemp(prefix="alvea-configurazione-"))
        self.copy = self.work / "alvea"
        shutil.copytree(str(ROOT), str(self.copy), ignore=shutil.ignore_patterns(".git", ".env", "data", "storage", "__pycache__", "tests", "*.pyc"))
        cert, key = str(self.work / "posta.pem"), str(self.work / "posta.key")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
                        "-keyout", key, "-out", cert], check=True, capture_output=True)
        self.llm, self.llm_url = serve(Modello)
        self.services, self.services_url = serve(Servizi)
        self.mail = Posta(cert, key)
        self.mail.start()
        try:  # la porta 465 (cifrata dall'inizio) si può aprire solo da amministratore: dove non si può, quel passaggio si salta
            self.mail465 = Posta(cert, key, implicit=True, port=465)
            self.mail465.start()
        except OSError:
            self.mail465 = None
        port = free_port()
        self.base = "http://127.0.0.1:%d" % port
        # l'ambiente del server: quello di una macchina pulita, più gli indirizzi dei servizi finti e il certificato della posta finta
        self.env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")}
        self.env.update({"PYTHONIOENCODING": "utf-8", "SSL_CERT_FILE": cert, "ANTHROPIC_BASE_URL": self.llm_url, "OPENAI_BASE_URL": self.llm_url + "/v1",
                         "STRIPE_API_BASE": self.services_url, "BRAVE_API_BASE": self.services_url, "GOOGLE_TOKEN_URL": self.services_url + "/token",
                         "GOOGLE_AUTH_URL": self.services_url + "/auth"})
        code, out = self.manage("init", "--email", "gestore@prova.it", "--nome", "Gestore", "--password", PW)
        env_file = self.copy / ".env"
        text = env_file.read_text(encoding="utf-8")
        self.step("«manage.py init» crea il file .env con la chiave di cifratura e il gestore", code == 0 and re.search(r"^SFB_SECRET_KEY=\S{40,}", text, re.M) is not None, out)
        for name, value in (("SFB_DEMO", "0"), ("SFB_PORT", str(port)), ("SFB_PUBLIC_URL", self.base), ("KIMI_BASE_URL", self.llm_url + "/v1"),
                            ("SFB_REGISTER_PER_IP_HOUR", "1000"), ("SFB_DEMO_DELAY_MS", "0")):  # quello che scrive l'installazione, più gli indirizzi di prova
            text, n = re.subn(r"(?m)^%s=.*$" % name, "%s=%s" % (name, value), text)
            if not n:
                text += "\n%s=%s\n" % (name, value)
        env_file.write_text(text, encoding="utf-8")
        self.step("il server parte senza nessuna chiave", self.start(), "vedi %s" % (self.work / "server.log"))
        self.boss = self.login("gestore@prova.it")
        status, out, _ = self.http("POST", "/api/admin/tenants", {"name": "Officina Prova", "package_id": "completo", "admin_email": "admin@officina.it",
                                                                  "admin_name": "Admin", "admin_password": PW}, self.boss)
        self.tenant_id = out.get("id") if isinstance(out, dict) else None
        self.step("il gestore crea un'azienda", status == 200 and bool(self.tenant_id), out)
        self.admin = self.login("admin@officina.it")
        status, out, _ = self.http("POST", "/api/admin/tenants", {"name": "Ritardatari", "package_id": "start", "admin_email": "admin@ritardatari.it",
                                                                  "admin_name": "Admin", "admin_password": PW}, self.boss)
        self.late_id, self.late = out.get("id"), self.login("admin@ritardatari.it")
        self.http("POST", "/api/admin/tenants", {"name": "Di Mezzo", "package_id": "business", "admin_email": "admin@dimezzo.it", "admin_name": "Admin", "admin_password": PW}, self.boss)
        self.middle = self.login("admin@dimezzo.it")

    # ---------- i gruppi del pannello ----------
    def pannello(self):
        self.title("Il pannello")
        code, out = self.manage("prova-servizi")
        self.step("«Prova dei servizi» su un server vuoto: tutto risulta «non configurato», niente errori", code == 0 and out.count("non configurato") >= 4 and "ERRORE" not in out and "Traceback" not in out, out)
        code, out = self.manage("prova-modelli")
        self.step("«Prova dei modelli» su un server vuoto: dice che cosa manca a ognuno", code == 0 and out.count("non configurato") == 4 and "Traceback" not in out, out)
        status, view, _ = self.http("GET", "/api/admin/settings", token=self.boss)
        fields = {f["key"]: f for f in view["fields"]}
        self.fields = fields
        self.step("le impostazioni si salvano cifrate", status == 200 and view.get("encrypted") is True, view if status != 200 else "")
        self.step("all'inizio nessun segreto è impostato", not [k for k, f in fields.items() if f["secret"] and f["set"]], [k for k, f in fields.items() if f["secret"] and f["set"]])
        self.step("un'azienda non vede e non cambia le impostazioni della piattaforma",
                  self.http("GET", "/api/admin/settings", token=self.admin)[0] == 403 and self.http("POST", "/api/admin/settings", {"values": {"PRICE_START": "1"}}, self.admin)[0] == 403)
        bad = [("SFB_SMTP_PORT", "abc"), ("COMPANY_EMAIL", "non-una-email"), ("SEARXNG_URL", "ftp://x"), ("SFB_CREDITS", "forse"), ("SFB_HOST", "0.0.0.0"), ("PRICE_START", "-5")]
        self.step("valori scritti male vengono rifiutati con una spiegazione", all(self.save(**{k: v})[0] == 400 for k, v in bad), [(k, self.save(**{k: v})[0]) for k, v in bad])

    def modelli(self):
        self.title("Modelli")
        for model, key_name in (("claude", "ANTHROPIC_API_KEY"), ("kimi", "MOONSHOT_API_KEY"), ("chatgpt", "OPENAI_API_KEY")):
            status, reply, _ = self.chat(self.admin, "ciao", model)
            self.step("%s prima: dice che manca la chiave e chi la deve mettere, senza parlare di file" % model, status == 502 and "Manca la chiave" in reply and key_name in reply
                      and "pannello" in reply and ".env" not in reply, (status, reply))
        status, reply, _ = self.chat(self.admin, "ciao", "locale")
        self.step("modello locale prima: dice che cosa serve", status == 502 and "ollama" in reply.lower() and ".env" not in reply, (status, reply))
        keys = {"ANTHROPIC_API_KEY": "sk-ant-prova-pannello-1", "MOONSHOT_API_KEY": "sk-kimi-prova-pannello-2", "OPENAI_API_KEY": "sk-openai-prova-pannello-3"}
        status, out = self.save(**keys)
        self.step("il gestore salva le tre chiavi dal pannello", status == 200 and sorted(out.get("changed") or []) == sorted(keys), out)
        _, view, _ = self.http("GET", "/api/admin/settings", token=self.boss)
        text = json.dumps(view)
        self.step("il pannello non restituisce mai una chiave, dice solo che c'è", not any(v in text for v in keys.values())
                  and all(f["set"] and f["value"] == "" and f["source"] == "pannello" for f in view["fields"] if f["key"] in keys))
        db_bytes = b"".join(p.read_bytes() for p in (self.copy / "data").glob("salesforb.db*"))
        self.step("nel database le chiavi non si leggono in chiaro", not any(v.encode() in db_bytes for v in keys.values()))
        for model, key, path, name in (("claude", keys["ANTHROPIC_API_KEY"], "/v1/messages", "claude-sonnet-5-5"), ("kimi", "Bearer " + keys["MOONSHOT_API_KEY"], "/v1/chat/completions", "kimi-k3")):
            before = len(SEEN["llm"])
            status, reply, _ = self.chat(self.admin, "ciao dal pannello", model)
            got = SEEN["llm"][before:] and SEEN["llm"][-1]
            sent = self.llm.requests[-1]["body"] if self.llm.requests else {}
            self.step("%s dopo: risponde, senza riavviare, con la chiave scritta nel pannello e il modello «%s»" % (model, name),
                      status == 200 and "ciao dal pannello" in reply and got and got["key"] == key and got["path"] == path and sent.get("model") == name, (status, reply, got, sent.get("model")))
        status, reply, _ = self.chat(self.admin, "ciao", "chatgpt")
        self.step("chatgpt con la sola chiave: dice che manca il nome del modello e dove scriverlo", status == 502 and "nome del modello" in reply and "pannello" in reply and ".env" not in reply, (status, reply))
        self.save(OPENAI_MODEL="gpt-prova", CLAUDE_MODEL="claude-altro-nome")
        status, reply, _ = self.chat(self.admin, "ciao", "chatgpt")
        self.step("chatgpt dopo aver scritto il nome del modello: risponde con quello", status == 200 and self.llm.requests[-1]["body"].get("model") == "gpt-prova"
                  and SEEN["llm"][-1]["key"] == "Bearer " + keys["OPENAI_API_KEY"], (status, reply))
        self.chat(self.admin, "ciao", "claude")
        self.step("cambiare il nome del modello Claude vale dalla richiesta successiva", self.llm.requests[-1]["body"].get("model") == "claude-altro-nome")
        self.save(KIMI_MODEL="kimi-altro-nome")
        self.chat(self.admin, "ciao", "kimi")
        self.step("anche il nome del modello Kimi si cambia dal pannello", self.llm.requests[-1]["body"].get("model") == "kimi-altro-nome")
        status, out = self.save(KIMI_REASONING_EFFORT="altissimo")
        self.step("un livello di ragionamento che non esiste viene rifiutato", status == 400 and "low" in str(out), (status, out))
        self.save(KIMI_REASONING_EFFORT="low")
        self.chat(self.admin, "ciao", "kimi")
        with_effort = self.llm.requests[-1]["body"].get("reasoning_effort")
        self.chat(self.admin, "ciao", "chatgpt")
        self.step("il livello di ragionamento scritto nel pannello va a Kimi e solo a Kimi", with_effort == "low" and "reasoning_effort" not in self.llm.requests[-1]["body"], with_effort)
        self.save(KIMI_REASONING_EFFORT="")
        self.chat(self.admin, "ciao", "kimi")
        self.step("lasciato vuoto, a Kimi non si manda niente in più (decide lui)", "reasoning_effort" not in self.llm.requests[-1]["body"])
        self.step("un'attesa fuori misura viene rifiutata", self.save(SFB_LLM_TIMEOUT="5")[0] == 400 and self.save(SFB_LLM_TIMEOUT="5000")[0] == 400)
        self.step("un'attesa dentro i limiti viene accettata (che poi venga rispettata lo controlla tests/test_v21.py, senza far aspettare qui)", self.save(SFB_LLM_TIMEOUT="10")[0] == 200)
        self.save(SFB_LLM_TIMEOUT="")
        self.save(CLAUDE_MODEL="", KIMI_MODEL="")
        self.chat(self.admin, "ciao", "claude")
        self.step("un campo svuotato torna al valore del file .env", self.llm.requests[-1]["body"].get("model") == "claude-sonnet-5-5")
        self.save(OLLAMA_BASE_URL=self.llm_url + "/v1", OLLAMA_MODEL="llama-prova")
        status, reply, _ = self.chat(self.admin, "ciao", "locale")
        self.step("modello locale dopo: risponde dall'indirizzo scritto nel pannello", status == 200 and self.llm.requests[-1]["body"].get("model") == "llama-prova", (status, reply))
        # uno strumento vero, andata e ritorno, con i due formati (Anthropic e Chat Completions)
        for model in ("claude", "kimi"):
            n = len(self.llm.requests)
            status, reply, out = self.chat(self.admin, "aggiungi lead Mario Rossi mario-%s@esempio.it" % model, model, "crm")
            events = [e.get("status") for e in out.get("events", [])] if isinstance(out, dict) else []
            calls = self.llm.requests[n:]
            ok = status == 200 and events == ["eseguito"] and len(calls) == 2
            if model == "kimi" and ok:  # i modelli che ragionano vogliono indietro il loro messaggio intero, ragionamento compreso
                back = [m for m in calls[1]["body"]["messages"] if m.get("role") == "assistant"]
                ok = bool(back) and back[-1].get("reasoning_content") == "demo" and bool(back[-1].get("tool_calls"))
            self.step("%s usa uno strumento e ne riceve il risultato%s" % (model, " (il ragionamento torna indietro intero)" if model == "kimi" else ""), ok, (status, reply, events, len(calls)))
        code, out = self.manage("prova-modelli")
        self.step("«Prova dei modelli» (console del gestore) trova le chiavi del pannello: quattro modelli rispondono e quattro fanno il giro con uno strumento",
                  code == 0 and len(re.findall(r"(?m)^OK .*ha risposto", out)) == 4 and len(re.findall(r"(?m)^OK .*usa uno strumento", out)) == 4 and "non configurato" not in out, out)

    def posta(self):
        self.title("Posta di servizio")
        status, out, _ = self.http("POST", "/api/admin/settings/test-email", {}, self.boss)
        self.step("posta prima: l'email di prova dice che cosa manca", status == 400 and "SMTP" in str(out), (status, out))
        _, info, _ = self.http("GET", "/api/register")
        self.step("senza posta chi si registra entra subito e non c'è il recupero della password", info.get("verify_email") is False and info.get("password_recovery") is False, info)
        self.save(SFB_SMTP_HOST="localhost", SFB_SMTP_PORT=str(self.mail.port), SFB_SMTP_USER="servizio@prova.it", SFB_SMTP_PASSWORD="segreto-posta-9", SFB_SMTP_FROM_NAME="Alvea Prova")
        status, out, _ = self.http("POST", "/api/admin/settings/test-email", {}, self.boss)
        last = SEEN["posta"][-1] if SEEN["posta"] else ""
        access = SEEN["accessi_posta"][-1] if SEEN["accessi_posta"] else {}
        self.step("posta dopo (porta con STARTTLS): l'email di prova parte davvero, cifrata, con utente e password del pannello",
                  status == 200 and access == {"utente": "servizio@prova.it", "password": "segreto-posta-9", "cifrato": True}
                  and "To: gestore@prova.it" in last and "Alvea Prova" in last and "servizio@prova.it" in last, (status, out, access, last[:200]))
        if self.mail465:
            n = len(SEEN["posta"])
            self.save(SFB_SMTP_PORT="465")
            status, out, _ = self.http("POST", "/api/admin/settings/test-email", {}, self.boss)
            self.step("posta sulla porta 465 (cifrata dall'inizio): parte", status == 200 and len(SEEN["posta"]) == n + 1 and SEEN["accessi_posta"][-1]["cifrato"], (status, out))
            self.save(SFB_SMTP_PORT=str(self.mail.port))
        else:
            print("-       porta 465 non provata: qui non si può aprire (serve essere amministratore)", flush=True)
        self.save(SFB_SMTP_PASSWORD="sbagliata", SFB_SMTP_HOST="localhost", SFB_SMTP_PORT=str(free_port()))
        status, out, _ = self.http("POST", "/api/admin/settings/test-email", {}, self.boss)
        self.step("con un server di posta che non risponde l'errore si legge", status == 502 and "non è partita" in str(out), (status, out))
        self.save(SFB_SMTP_PASSWORD="segreto-posta-9", SFB_SMTP_PORT=str(self.mail.port))
        _, info, _ = self.http("GET", "/api/register")
        self.step("con la posta chi si registra deve confermare l'email e c'è il recupero della password", info.get("verify_email") is True and info.get("password_recovery") is True, info)
        n = len(SEEN["posta"])
        status, out, _ = self.http("POST", "/api/register", {"company": "Nuova Con Posta", "name": "Anna", "email": "anna@nuova.it", "password": PW, "agents": ["email"], "accept": True})
        mail = self.letter(n)
        link = re.search(r"(/api/verify\?[^\s]+)", mail)
        blocked = self.http("POST", "/api/login", {"email": "anna@nuova.it", "password": PW})[0]
        self.step("la registrazione manda l'email di conferma e fino ad allora non si entra", status == 200 and bool(link) and blocked in (401, 403), (status, out, blocked, mail[:300]))
        if link:
            status, _, heads = self.http("GET", link.group(1))
            entered = self.http("POST", "/api/login", {"email": "anna@nuova.it", "password": PW})[0]
            self.step("il collegamento nell'email conferma e dopo si entra", status == 302 and heads.get("Location") == "/#verifica-ok" and entered == 200, (status, heads.get("Location"), entered))
        n = len(SEEN["posta"])
        status, out, _ = self.http("POST", "/api/password/forgot", {"email": "admin@officina.it"})
        mail = self.letter(n)
        self.step("il recupero della password manda il collegamento all'indirizzo pubblico del server", status == 200 and (self.base + "/#reimposta-") in mail, (status, out, mail[:300]))

    def ricerca(self):
        self.title("Ricerca sul web")
        n = len(SEEN["servizi"])
        status, out = self.search()
        self.step("ricerca prima: lo strumento dice che non è configurata", "non è configurata" in out and len(SEEN["servizi"]) == n, out)
        self.save(BRAVE_API_KEY="chiave-brave-pannello")
        status, out = self.search()
        last = SEEN["servizi"][-1]
        self.step("ricerca dopo la chiave Brave: la domanda arriva a Brave con quella chiave", "Risultato Brave" in out and last["path"] == "/res/v1/web/search" and last["brave"] == "chiave-brave-pannello", (out, last))
        self.save(SEARXNG_URL=self.services_url)
        status, out = self.search()
        last = SEEN["servizi"][-1]
        self.step("con SearXNG impostato si usa quello (formato JSON), senza mandargli la chiave di Brave", "Risultato SearXNG" in out and last["path"] == "/search" and last["query"].get("format") == "json" and not last["brave"], (out, last))
        self.save(SEARXNG_URL="")
        status, out = self.search()
        self.step("tolto SearXNG si torna a Brave", "Risultato Brave" in out, out)

    def search(self):
        """Fa girare lo strumento di ricerca come lo farebbe un agente: il modello finto lo sceglie quando legge «cerca sul web»."""
        status, reply, out = self.chat(self.admin, "cerca sul web: officine a Firenze", "claude", "ricerca")
        return status, reply

    def google(self):
        self.title("Google")
        status, out, _ = self.http("POST", "/api/google/start", {"services": ["calendario"]}, self.admin)
        self.step("Google prima: dice che non è configurato e dove si configura", status == 400 and "non è configurato" in str(out) and "pannello" in str(out) and ".env" not in str(out), (status, out))
        self.save(GOOGLE_CLIENT_ID="123-prova.apps.googleusercontent.com", GOOGLE_CLIENT_SECRET="segreto-google-pannello")
        status, out, heads = self.http("POST", "/api/google/start", {"services": ["calendario"]}, self.admin)
        url = out.get("url", "") if isinstance(out, dict) else ""
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.step("Google dopo: il collegamento porta l'ID client del pannello e l'indirizzo di ritorno di questo server",
                  status == 200 and query.get("client_id") == "123-prova.apps.googleusercontent.com" and query.get("redirect_uri") == self.base + "/api/google/callback", (status, out))
        cookie = (heads.get("Set-Cookie") or "").split(";")[0]
        status, _, heads = self.http("GET", "/api/google/callback?" + urllib.parse.urlencode({"state": query.get("state", ""), "code": "codice-finto"}), cookie=cookie)
        token_call = next((s for s in reversed(SEEN["servizi"]) if s["path"] == "/token"), {})
        self.step("al ritorno il server scambia il codice usando il segreto scritto nel pannello", status == 302 and heads.get("Location") == "/#google-ok"
                  and token_call.get("form", {}).get("client_secret") == "segreto-google-pannello", (status, heads.get("Location"), token_call.get("form")))
        _, state, _ = self.http("GET", "/api/google", token=self.admin)
        self.step("l'azienda risulta collegata a Google", isinstance(state, dict) and state.get("connected") is True, state)

    def registrazione(self):
        self.title("Registrazione")
        self.save(SFB_REGISTRATION="0")
        _, info, _ = self.http("GET", "/api/register")
        status, out, _ = self.http("POST", "/api/register", {"company": "Chiusa", "name": "X", "email": "x@chiusa.it", "password": PW, "agents": ["email"], "accept": True})
        self.step("registrazione chiusa dal pannello: nessuno si registra", info.get("open") is False and status == 403, (info.get("open"), status, out))
        self.save(SFB_REGISTRATION="1", SFB_TRIAL_DAYS="14", SFB_SMTP_HOST="")  # senza posta, per entrare subito
        self.save(SFB_SMTP_HOST="")
        _, info, _ = self.http("GET", "/api/register")
        status, out, _ = self.http("POST", "/api/register", {"company": "Prova Quattordici", "name": "Luca", "email": "luca@quattordici.it", "password": PW, "agents": ["email", "crm"], "accept": True})
        self.step("riaperta con 14 giorni di prova: ci si registra e si entra", info.get("open") is True and info.get("trial_days") == 14 and status == 200, (info.get("open"), info.get("trial_days"), status, out))
        self.newcomer = self.login("luca@quattordici.it")
        _, bill, _ = self.http("GET", "/api/billing", token=self.newcomer)
        until = (bill or {}).get("paid_until") if isinstance(bill, dict) else None
        self.step("la nuova azienda ha davvero 14 giorni davanti", until in (str(date.today() + timedelta(days=d)) for d in (13, 14, 15)), bill)
        status, reply, _ = self.chat(self.newcomer, "ciao", "claude")
        self.step("chi si registra da solo non usa le chiavi della piattaforma (scelta di partenza): gli viene detto di mettere la sua", status == 502 and "chiave" in reply.lower() and "Collegamenti" in reply
                  , (status, reply))
        self.save(SFB_REGISTER_PLATFORM_KEYS="1", SFB_TRIAL_DAYS="0")
        status, out, _ = self.http("POST", "/api/register", {"company": "Con Le Tue Chiavi", "name": "Sara", "email": "sara@chiavi.it", "password": PW, "agents": ["email"], "accept": True})
        sara = self.login("sara@chiavi.it")
        status, reply, _ = self.chat(sara, "ciao da Sara", "claude")
        self.step("con «usa le tue chiavi» acceso dal pannello, chi si registra dopo lavora subito con la chiave della piattaforma", status == 200 and "ciao da Sara" in reply
                  and SEEN["llm"][-1]["key"] == "sk-ant-prova-pannello-1", (status, reply))
        _, bill, _ = self.http("GET", "/api/billing", token=sara)
        self.step("con 0 giorni di prova la nuova azienda non ha scadenza", isinstance(bill, dict) and bill.get("paid_until") == "", bill)
        self.save(SFB_REGISTER_PLATFORM_KEYS="0")

    def azienda(self):
        self.title("Dati dell'azienda e pagine legali")
        status, page, _ = self.http("GET", "/privacy")
        self.step("prima: le pagine legali ci sono e segnano che cosa manca", status == 200 and "[da compilare" in page, status)
        self.save(COMPANY_NAME="Alvea Prova S.r.l.", COMPANY_VAT="IT01234567890", COMPANY_ADDRESS="Via Roma 1, 50100 Firenze", COMPANY_EMAIL="privacy@alveaprova.it",
                  COMPANY_HOSTING="Hetzner, Germania")
        early = self.http("GET", "/privacy")[1]
        self.step("i campi facoltativi lasciati vuoti (PEC, REA) non compaiono come cose da compilare", "PEC" not in early or "[da compilare: PEC" not in early, "")
        self.save(COMPANY_PEC="alveaprova@pec.it", COMPANY_REA="FI-123456")
        pages = [self.http("GET", p)[1] for p in ("/privacy", "/condizioni", "/cookie", "/")]
        self.step("dopo: ragione sociale, partita IVA e sede compaiono nelle tre pagine e in fondo al sito", all("Alvea Prova S.r.l." in p for p in pages) and "IT01234567890" in pages[0]
                  and "Hetzner, Germania" in pages[0] and "privacy@alveaprova.it" in pages[0], [("Alvea Prova S.r.l." in p) for p in pages])
        self.step("PEC e numero REA, una volta scritti, compaiono", "alveaprova@pec.it" in pages[0] + pages[3] and "FI-123456" in pages[0] + pages[3], "")
        self.step("in fondo al sito non resta niente «da compilare»", "[da compilare" not in pages[3], "")
        self.save(COMPANY_NAME="<script>alert(1)</script> & Figli")
        _, page, _ = self.http("GET", "/privacy")
        self.step("quello che scrive il gestore non diventa codice nella pagina", "<script>alert(1)" not in page and "&lt;script&gt;" in page)
        self.save(COMPANY_NAME="Alvea Prova S.r.l.")

    def prezzi_e_pagamenti(self):
        self.title("Prezzi e pagamenti")
        _, bill, _ = self.http("GET", "/api/billing", token=self.admin)
        status, out, _ = self.http("POST", "/api/billing/checkout", {}, self.admin)
        self.step("pagamenti prima: la scheda dice che la carta non è attiva e il pagamento spiega perché", isinstance(bill, dict) and not bill.get("enabled") and status == 400 and "non è attivo" in str(out), (bill, status, out))
        status, out, _ = self.http("POST", "/api/billing/webhook", raw=b"{}", headers={"Stripe-Signature": "t=1,v1=x"})
        self.step("senza il segreto del webhook gli avvisi di Stripe vengono rifiutati", status == 503, (status, out))
        self.save(PRICE_START="29", PRICE_BUSINESS="79", PRICE_COMPLETO="149", PRICE_PER_AGENT="5")
        _, home, _ = self.http("GET", "/")
        self.step("i prezzi scritti nel pannello compaiono sul sito", all(("%d" % p) in home for p in (29, 79, 149)), "")
        _, bill, _ = self.http("GET", "/api/billing", token=self.admin)
        self.step("con i soli prezzi, senza le chiavi di Stripe, la carta resta spenta", isinstance(bill, dict) and not bill.get("enabled"), bill)
        self.save(STRIPE_SECRET_KEY="sk_test_pannello_123", STRIPE_WEBHOOK_SECRET="whsec_pannello_456")
        _, bill, _ = self.http("GET", "/api/billing", token=self.admin)
        _, agents, _ = self.http("GET", "/api/tenant/agents", token=self.admin)
        on = len([a for a in (agents.get("agents") or []) if a.get("is_active")]) if isinstance(agents, dict) else 0
        status, out, _ = self.http("POST", "/api/billing/checkout", {}, self.admin)
        call = next((s for s in reversed(SEEN["servizi"]) if s["path"] == "/v1/checkout/sessions"), {})
        form = call.get("form", {})
        wanted = (149 + 5 * on) * 100
        self.step("pagamenti dopo: Stripe riceve la chiave del pannello e l'importo giusto (pacchetto più %d agenti accesi = %d euro al mese)" % (on, wanted // 100),
                  status == 200 and "cs_prova_1" in str(out) and call.get("auth") == "Bearer sk_test_pannello_123" and form.get("mode") == "subscription"
                  and form.get("line_items[0][price_data][unit_amount]") == str(wanted) and form.get("line_items[0][price_data][currency]") == "eur"
                  and form.get("success_url") == self.base + "/#pagamento-ok", (status, out, bill, form))
        status, page, _ = self.http("GET", "/")
        self.step("la pagina a cui Stripe rimanda il cliente esiste", status == 200)

        def event(kind, obj, secret="whsec_pannello_456", eid=None):
            raw = json.dumps({"id": eid or ("evt_%s_%d" % (kind, time.time() * 1000)), "type": kind, "data": {"object": obj}}).encode()
            t = str(int(time.time()))
            sig = hmac.new(secret.encode(), t.encode() + b"." + raw, hashlib.sha256).hexdigest()
            return self.http("POST", "/api/billing/webhook", raw=raw, headers={"Stripe-Signature": "t=%s,v1=%s" % (t, sig), "Content-Type": "application/json"})
        status, out, _ = event("checkout.session.completed", {"mode": "subscription", "client_reference_id": str(self.tenant_id), "customer": "cus_1", "subscription": "sub_1"}, secret="whsec_sbagliato")
        self.step("un avviso firmato con un altro segreto viene rifiutato", status == 400, (status, out))
        status, out, _ = event("checkout.session.completed", {"mode": "subscription", "client_reference_id": str(self.tenant_id), "customer": "cus_1", "subscription": "sub_1"})
        end = int(time.time()) + 30 * 86400
        status2, out2, _ = event("invoice.paid", {"customer": "cus_1", "subscription": "sub_1", "lines": {"data": [{"period": {"end": end}}]}})
        _, bill, _ = self.http("GET", "/api/billing", token=self.admin)
        self.step("un avviso firmato con il segreto del pannello viene accettato e la scadenza dell'azienda si sposta di un mese", status == 200 and status2 == 200
                  and isinstance(bill, dict) and bill.get("paid_until") == str(datetime.fromtimestamp(end, timezone.utc).date()) and bill.get("subscribed") is True
                  and bill.get("portal") is True, (status, out, status2, out2, bill))
        # i giorni di tolleranza: un'azienda scaduta da tre giorni lavora ancora solo se il gestore ne ha concessi di più
        late = str(date.today() - timedelta(days=3))
        status, out, _ = self.http("POST", "/api/admin/tenants/%d" % self.late_id, {"paid_until": late}, self.boss)
        blocked, why, _ = self.chat(self.late, "ciao da scaduti", "claude")
        self.save(SFB_GRACE_DAYS="7")
        allowed, reply, _ = self.chat(self.late, "ciao da scaduti", "claude")
        self.save(SFB_GRACE_DAYS="0")
        again = self.chat(self.late, "ciao da scaduti", "claude")[0]
        self.step("giorni di tolleranza dal pannello: scaduta da tre giorni è ferma con 0, lavora con 7, torna ferma con 0", status == 200 and blocked == 402 and allowed == 200
                  and "ciao da scaduti" in reply and again == 402, (status, out, blocked, why, allowed, reply, again))

    def crediti(self):
        self.title("Crediti")
        _, view, _ = self.http("GET", "/api/credits", token=self.admin)
        self.step("crediti prima: spenti, l'azienda lavora senza conteggio", isinstance(view, dict) and view.get("enabled") is False, view)
        self.save(SFB_CREDITS="1", CREDIT_RATE_CLAUDE="1000", CREDIT_RATE_KIMI="10", CREDIT_RATE_CHATGPT="7", CREDITS_START="111", CREDITS_BUSINESS="2222", CREDITS_COMPLETO="5000",
                  CREDIT_PACK_SIZE="2000", STRIPE_PRICE_CREDITS="price_crediti_1")
        small, medium = self.http("GET", "/api/credits", token=self.late)[1], self.http("GET", "/api/credits", token=self.middle)[1]
        self.step("ogni pacchetto ha i crediti inclusi scritti nel pannello (Start 111, Business 2222)", isinstance(small, dict) and small.get("included") == 111
                  and isinstance(medium, dict) and medium.get("included") == 2222 and {r["model"]: r["per_1000"] for r in medium.get("rates", [])}.get("chatgpt") == 7, (small, medium))
        _, view, _ = self.http("GET", "/api/credits", token=self.admin)
        rates = {r["model"]: r["per_1000"] for r in view.get("rates", [])} if isinstance(view, dict) else {}
        start = view.get("available") if isinstance(view, dict) else None
        self.step("crediti dopo: accesi, con le tariffe e i crediti inclusi scritti nel pannello", isinstance(view, dict) and view.get("enabled") is True and rates.get("claude") == 1000 and rates.get("kimi") == 10
                  and start == 5000 and view.get("pack", {}).get("size") == 2000 and view.get("pack", {}).get("buyable") is True, view)
        status, reply, _ = self.chat(self.admin, "una domanda che consuma", "claude")
        _, view, _ = self.http("GET", "/api/credits", token=self.admin)
        after = view.get("available") if isinstance(view, dict) else None
        self.step("una richiesta scala i crediti secondo la tariffa", status == 200 and isinstance(after, int) and isinstance(start, int) and 0 < start - after < 5000, (status, reply, start, after))
        status, out, _ = self.http("POST", "/api/credits/checkout", {"quantity": 1}, self.admin)
        call = next((s for s in reversed(SEEN["servizi"]) if s["path"] == "/v1/checkout/sessions"), {})
        self.step("la ricarica a pagamento va a Stripe con il prezzo scritto nel pannello", status == 200 and call.get("form", {}).get("line_items[0][price]") == "price_crediti_1"
                  and call.get("form", {}).get("mode") == "payment", (status, out, call.get("form")))
        self.save(CREDITS_COMPLETO="1")
        for _ in range(3):
            status, reply, _ = self.chat(self.admin, "ancora una", "claude")
        self.step("finiti i crediti l'agente lo dice, senza errori interni", status < 500 and "credit" in reply.lower(), (status, reply))
        self.save(SFB_CREDITS="0")
        status, reply, _ = self.chat(self.admin, "di nuovo senza crediti", "claude")
        self.step("spenti di nuovo i crediti, si torna a lavorare", status == 200 and "di nuovo senza crediti" in reply, (status, reply))

    def prezzi_fissi(self):
        self.title("Prezzi creati a mano su Stripe (al posto di quelli del pannello)")
        self.http("POST", "/api/admin/tenants/%d" % self.late_id, {"paid_until": ""}, self.boss)
        for name in ("PRICE_START", "PRICE_BUSINESS", "PRICE_COMPLETO", "PRICE_PER_AGENT"):
            self.save(**{name: ""})
        _, bill, _ = self.http("GET", "/api/billing", token=self.middle)
        self.step("tolti i prezzi dal pannello, la carta torna spenta", isinstance(bill, dict) and bill.get("enabled") is False, bill)
        self.save(STRIPE_PRICE_START="price_start_1", STRIPE_PRICE_BUSINESS="price_business_2", STRIPE_PRICE_COMPLETO="price_completo_3")
        for token, wanted in ((self.late, "price_start_1"), (self.middle, "price_business_2")):
            status, out, _ = self.http("POST", "/api/billing/checkout", {}, token)
            form = next((s for s in reversed(SEEN["servizi"]) if s["path"] == "/v1/checkout/sessions"), {}).get("form", {})
            self.step("con il prezzo fisso «%s» scritto nel pannello, Stripe riceve quello" % wanted, status == 200 and form.get("line_items[0][price]") == wanted
                      and "line_items[0][price_data][unit_amount]" not in form, (status, out, form))
        _, bill, _ = self.http("GET", "/api/billing", token=self.admin)
        self.step("e la carta risulta attiva anche per il pacchetto Completo", isinstance(bill, dict) and bill.get("enabled") is True, bill)

    def riavvio(self):
        self.title("Dopo un riavvio del server")
        self.stop()
        self.step("il server riparte", self.start(), "vedi %s" % (self.work / "server.log"))
        self.boss, self.admin = self.login("gestore@prova.it"), self.login("admin@officina.it")
        _, view, _ = self.http("GET", "/api/admin/settings", token=self.boss)
        fields = {f["key"]: f for f in view["fields"]}
        kept = ["ANTHROPIC_API_KEY", "MOONSHOT_API_KEY", "OPENAI_API_KEY", "BRAVE_API_KEY", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "GOOGLE_CLIENT_SECRET", "SFB_SMTP_PASSWORD"]
        self.step("tutti i segreti scritti nel pannello ci sono ancora", all(fields[k]["set"] and fields[k]["source"] == "pannello" for k in kept), [k for k in kept if not fields[k]["set"]])
        status, reply, _ = self.chat(self.admin, "ciao dopo il riavvio", "claude")
        self.step("gli agenti rispondono ancora con la chiave del pannello", status == 200 and SEEN["llm"][-1]["key"] == "sk-ant-prova-pannello-1", (status, reply))
        _, out = self.search()
        self.step("la ricerca funziona ancora", "Risultato Brave" in out, out)
        _, page, _ = self.http("GET", "/privacy")
        self.step("i dati dell'azienda sono ancora nelle pagine legali", "Alvea Prova S.r.l." in page)
        _, state, _ = self.http("GET", "/api/google", token=self.admin)
        self.step("il collegamento a Google dell'azienda è ancora lì", isinstance(state, dict) and state.get("connected") is True, state)
        code, out = self.manage("controlla-produzione")
        todo = [l for l in out.splitlines() if "DA FARE" in l]
        self.step("il controllo di produzione vede le impostazioni del pannello: non segna più modelli, pagamenti e dati dell'azienda",
                  not [l for l in todo if re.search(r"modell|Stripe|pagament|ragione sociale|dati dell'azienda", l, re.I)], todo)
        self.save(ANTHROPIC_API_KEY="")
        status, reply, _ = self.chat(self.admin, "ciao", "claude")
        self.step("una chiave cancellata dal pannello smette di valere subito", status == 502 and "Manca la chiave" in reply, (status, reply))
        missing = sorted(set(self.fields) - self.touched)
        self.step("ogni campo del pannello (%d) è stato scritto e provato almeno una volta" % len(self.fields), not missing, missing)
        log = (self.work / "server.log").read_text(encoding="utf-8", errors="replace")
        self.save(STRIPE_PRICE_BUSINESS="price_inesistente", SFB_SMTP_HOST="localhost", SFB_SMTP_PORT=str(self.mail.port))
        code, out = self.manage("prova-servizi")
        lines = {l.split(":", 1)[0].split(None, 1)[1]: l.split()[0] for l in out.splitlines() if ":" in l and l.split()}
        self.step("«Prova dei servizi»: posta, Brave, Stripe e i prezzi giusti risultano a posto; il prezzo che su Stripe non esiste viene segnalato",
                  code == 1 and lines.get("Posta di servizio") == "OK" and lines.get("Ricerca con Brave") == "OK" and lines.get("Stripe") == "OK"
                  and lines.get("Stripe, pacchetto Start") == "OK" and lines.get("Stripe, pacchetto Business") == "ERRORE" and lines.get("Stripe, ricarica di crediti") == "OK"
                  and lines.get("Stripe, avvisi") == "OK" and lines.get("Google") == "OK" and "price_inesistente" in out and (self.base + "/api/billing/webhook") in out, out)
        self.save(STRIPE_PRICE_BUSINESS="price_annuale_9", STRIPE_PRICE_START="price_archiviato_8", STRIPE_PRICE_CREDITS="price_mensile_7")
        code, out = self.manage("prova-servizi")
        self.step("«Prova dei servizi» si accorge di un prezzo annuale, di uno archiviato e di una ricarica messa come abbonamento", code == 1 and "non è un abbonamento mensile" in out
                  and "è archiviato" in out and "serve un pagamento singolo" in out, out)
        self.step("in tutta la prova il server non ha mai avuto un errore interno", "Traceback" not in log, log[log.find("Traceback"):][:1500])

    def run(self):
        try:
            self.prepare()
            for part in (self.pannello, self.modelli, self.posta, self.ricerca, self.google, self.registrazione, self.azienda, self.prezzi_e_pagamenti, self.crediti, self.prezzi_fissi, self.riavvio):
                try:
                    part()
                except Exception as e:  # un passaggio che si rompe non deve nascondere gli altri
                    import traceback
                    self.step("%s: la prova stessa si è interrotta" % part.__name__, False, traceback.format_exc()[-900:] or e)
        finally:
            self.stop()
            for s in (self.llm, self.services):
                s.shutdown()
            if not self.failed and not os.environ.get("ALVEA_TIENI"):
                shutil.rmtree(str(self.work), ignore_errors=True)
            else:
                print("\nCartella della prova tenuta: %s" % self.work)
        print("\nProva di configurazione %s: %d controlli, %d falliti." % ("NON superata" if self.failed else "superata", self.count, len(self.failed)))
        return 1 if self.failed else 0


if __name__ == "__main__":
    sys.exit(Prova().run())
