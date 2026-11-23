"""Base comune delle prove: server vero su porta libera, database temporaneo, modello finto,
finto Telegram e finto Google. Non servono chiavi né Internet."""
import base64
import hashlib
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.TemporaryDirectory()
os.environ.update({"SFB_DB": str(Path(_tmp.name) / "test.db"), "SFB_DEMO": "1", "SFB_PBKDF2_ITERS": "1000",
                   "SFB_QUIET": "1", "SFB_SECRET_KEY": "", "SFB_SCHEDULER": "0", "SFB_TZ": "Europe/Rome",
                   "GOOGLE_CLIENT_ID": "id-prova", "GOOGLE_CLIENT_SECRET": "segreto-prova",
                   "SFB_ALLOW_PLAINTEXT_TOKENS": "1", "SFB_STORAGE": str(Path(_tmp.name) / "storage"),
                   "SFB_REGISTER_PER_IP_HOUR": "100000", "SFB_REGISTER_PER_HOUR": "100000", "SFB_REGISTRATION": "aperta",
                   "SFB_SMTP_HOST": "", "SFB_REGISTER_PLATFORM_KEYS": "0", "SFB_REGISTER_PACKAGE": "completo",
                   "SFB_DEMO_DELAY_MS": "0", "SFB_CLOUD_RUNNER": "0", "OLLAMA_BASE_URL": "", "SEARXNG_URL": "", "SFB_TRIAL_DAYS": "0"})

from server import app, db, ops  # noqa: E402

PW = "password-di-prova-1"
TELEGRAM_CALLS = []
# Stato del finto Google, azzerabile dalle prove.
GOOGLE = {"challenge": None, "scope": "", "valid": set(), "n": 0, "revoked": False, "sent": [], "events": [],
          "revocations": [], "token_calls": []}


class FakeTelegram(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8"))
        TELEGRAM_CALLS.append({"path": self.path, "body": body})
        out = json.dumps({"ok": True, "result": {"message_id": 1}}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class FakeGoogle(BaseHTTPRequestHandler):
    """Imita il server dei token e le API di Gmail, Calendar e Fogli quanto basta a provare il nostro codice."""

    def log_message(self, *a):
        pass

    def _out(self, status, obj):
        out = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _authorized(self):
        return (self.headers.get("Authorization") or "")[7:] in GOOGLE["valid"]

    def _new_token(self):
        GOOGLE["n"] += 1
        token = "at-%d" % GOOGLE["n"]
        GOOGLE["valid"].add(token)
        return token

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8")
        path = urllib.parse.urlsplit(self.path).path
        if path == "/token":
            form = {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}
            GOOGLE["token_calls"].append(form)
            if form.get("client_id") != "id-prova" or form.get("client_secret") != "segreto-prova":
                return self._out(401, {"error": "invalid_client"})
            if form.get("grant_type") == "authorization_code":
                digest = hashlib.sha256(form.get("code_verifier", "").encode("ascii")).digest()
                challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
                if form.get("code") != "codice-buono" or challenge != GOOGLE["challenge"] \
                        or not form.get("redirect_uri", "").endswith("/api/google/callback"):
                    return self._out(400, {"error": "invalid_grant"})
                payload = base64.urlsafe_b64encode(json.dumps({"email": "azienda@example.com"}).encode()).decode().rstrip("=")
                return self._out(200, {"access_token": self._new_token(), "refresh_token": "rt-1", "expires_in": 3600,
                                       "scope": GOOGLE["scope"], "token_type": "Bearer", "id_token": "x.%s.y" % payload})
            if form.get("grant_type") == "refresh_token":
                if GOOGLE["revoked"] or form.get("refresh_token") != "rt-1":
                    return self._out(400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
                return self._out(200, {"access_token": self._new_token(), "expires_in": 3600, "token_type": "Bearer"})
            return self._out(400, {"error": "unsupported_grant_type"})
        if path == "/revoke":
            GOOGLE["revocations"].append(urllib.parse.parse_qs(raw).get("token", [""])[0])
            return self._out(200, {})
        if not self._authorized():
            return self._out(401, {"error": {"code": 401, "status": "UNAUTHENTICATED"}})
        body = json.loads(raw or "{}")
        if path == "/gmail/v1/users/me/messages/send":
            GOOGLE["sent"].append(base64.urlsafe_b64decode(body["raw"]).decode("utf-8"))
            return self._out(200, {"id": "inviato-1"})
        if path == "/calendar/v3/calendars/primary/events":
            GOOGLE["events"].append(body)
            return self._out(200, {"id": "evento-1"})
        self._out(404, {"error": "sconosciuto"})

    def do_GET(self):
        parts = urllib.parse.urlsplit(self.path)
        if not self._authorized():
            return self._out(401, {"error": {"code": 401, "status": "UNAUTHENTICATED"}})
        if parts.path == "/gmail/v1/users/me/messages":
            return self._out(200, {"messages": [{"id": "m1"}, {"id": "m2"}]})
        if parts.path.startswith("/gmail/v1/users/me/messages/"):
            mid = parts.path.rsplit("/", 1)[1]
            return self._out(200, {"id": mid, "snippet": "Vorrei un preventivo per %s" % mid, "payload": {"headers": [
                {"name": "From", "value": "Cliente %s <cliente@example.org>" % mid},
                {"name": "Subject", "value": "Richiesta preventivo %s" % mid}, {"name": "Date", "value": "Mon, 23 Nov 2026 09:00:00 +0100"}]}})
        if parts.path == "/calendar/v3/calendars/primary/events":
            q = urllib.parse.parse_qs(parts.query)
            GOOGLE["last_calendar_query"] = {k: v[0] for k, v in q.items()}
            return self._out(200, {"items": [{"summary": "Riunione fornitori", "start": {"dateTime": "2026-11-24T15:00:00+01:00"}, "location": "Sede"}]})
        if parts.path.startswith("/v4/spreadsheets/"):
            GOOGLE["last_sheet_path"] = parts.path
            return self._out(200, {"values": [["Mese", "Vendite"], ["Ottobre", "12400"], ["Novembre", "15150"]]})
        self._out(404, {"error": "sconosciuto"})


def serve(server):
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d" % server.server_address[1]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.telegram = ThreadingHTTPServer(("127.0.0.1", 0), FakeTelegram)
        os.environ["TELEGRAM_API_BASE"] = serve(cls.telegram)
        cls.google = ThreadingHTTPServer(("127.0.0.1", 0), FakeGoogle)
        g = serve(cls.google)
        os.environ.update({"GOOGLE_AUTH_URL": g + "/auth", "GOOGLE_TOKEN_URL": g + "/token", "GOOGLE_REVOKE_URL": g + "/revoke",
                           "GOOGLE_GMAIL_BASE": g, "GOOGLE_CALENDAR_BASE": g, "GOOGLE_SHEETS_BASE": g})
        cls.server = app.make_server("127.0.0.1", 0)
        cls.base = serve(cls.server)
        os.environ["SFB_PUBLIC_URL"] = cls.base
        con = db.connect()
        if not con.execute("SELECT 1 FROM users").fetchone():
            ops.create_user(con, None, "gestore@test.it", "Gestore", "superadmin", PW)
            for pkg in ("start", "business", "completo"):
                ops.create_tenant(con, "Azienda " + pkg, pkg, "admin@%s.it" % pkg, "Admin " + pkg, PW)
        con.close()

    @classmethod
    def tearDownClass(cls):
        for s in (cls.server, cls.telegram, cls.google):
            s.shutdown()
            s.server_close()

    def call(self, method, path, body=None, token=None):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode("utf-8") if body is not None else None)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def raw(self, method, path, body=None, token=None, cookie=None):
        """Come `call`, ma non segue i reindirizzamenti e restituisce anche le intestazioni."""
        host = urllib.parse.urlsplit(self.base)
        con = http.client.HTTPConnection(host.hostname, host.port, timeout=30)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if cookie:
            headers["Cookie"] = cookie
        con.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        r = con.getresponse()
        data = r.read().decode("utf-8")
        out = (r.status, dict(r.getheaders()), json.loads(data) if data else None)
        con.close()
        return out

    def upload(self, token, filename, data, field="file", extra_fields=None, boundary="----confineDiProva7MA4YWxk"):
        """Carica un file come farebbe un browser (multipart/form-data). Ritorna (stato, risposta)."""
        parts = []
        for key, value in (extra_fields or {}).items():
            parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (boundary, key, value)).encode("utf-8"))
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\nContent-Type: application/octet-stream\r\n\r\n"
                      % (boundary, field, filename)).encode("utf-8") + data + b"\r\n")
        parts.append(("--%s--\r\n" % boundary).encode("utf-8"))
        return self.post_raw("/api/files/upload", b"".join(parts), "multipart/form-data; boundary=" + boundary, token)

    def post_raw(self, path, payload, content_type, token=None):
        host = urllib.parse.urlsplit(self.base)
        con = http.client.HTTPConnection(host.hostname, host.port, timeout=60)
        headers = {"Content-Type": content_type, "Content-Length": str(len(payload))}
        if token:
            headers["Authorization"] = "Bearer " + token
        con.request("POST", path, body=payload, headers=headers)
        r = con.getresponse()
        out = (r.status, json.loads(r.read().decode("utf-8")))
        con.close()
        return out

    def fetch(self, path, headers=None):
        """GET senza accesso (come un tag <img> o <video>). Ritorna (stato, intestazioni, byte)."""
        host = urllib.parse.urlsplit(self.base)
        con = http.client.HTTPConnection(host.hostname, host.port, timeout=60)
        con.request("GET", path, headers=headers or {})
        r = con.getresponse()
        out = (r.status, dict(r.getheaders()), r.read())
        con.close()
        return out

    def login(self, email, password=PW):
        status, out = self.call("POST", "/api/login", {"email": email, "password": password})
        self.assertEqual(status, 200, out)
        return out["token"]

    def chat(self, token, message, agent_id="centrale", model="claude", conversation_id=None):
        return self.call("POST", "/api/chat", {"message": message, "agent_id": agent_id, "model": model,
                                               "conversation_id": conversation_id}, token)

    def new_tenant(self, name, package_id):
        """Azienda nuova e isolata per una prova. Ritorna (token dell'amministratore, id azienda)."""
        email = "admin@%s.it" % name.lower().replace(" ", "-")
        con = db.connect()
        tenant_id = ops.create_tenant(con, name, package_id, email, "Admin " + name, PW)
        con.close()
        return self.login(email), tenant_id
