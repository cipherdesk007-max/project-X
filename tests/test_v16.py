"""Prove della versione 22: il sistema attaccato come farebbe un intruso. Permessi su ogni rotta, dati di un'azienda
chiesti da un'altra, salita di grado, sessioni, forza bruta, intestazioni, richieste scritte male, richieste verso la rete interna."""
import http.client
import json
import os
import re
import socket
import unittest
import urllib.parse

from base import Base, PW

from server import app, auth, connectors, db, ops

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c626001000000ffff03000006000557bfabd40000000049454e44ae426082")


def sample(pattern):
    """Un indirizzo vero per una rotta scritta come espressione: numeri, prima alternativa, una parola."""
    p = pattern.strip("^$").replace(r"(\d+)", "999999")
    p = re.sub(r"\(([a-z_]+)\|[^)]*\)", r"\1", p)
    return re.sub(r"\(\[[^\]]+\]\+\)", "video", p).replace("\\", "")


class Sicurezza(Base):
    def setUp(self):
        auth.reset_fails()

    def req(self, method, path, body=None, token=None, headers=None, raw=None):
        """Richiesta senza aiuti: ritorna (stato, intestazioni, byte, oggetto JSON oppure None)."""
        u = urllib.parse.urlsplit(self.base)
        c = http.client.HTTPConnection(u.hostname, u.port, timeout=30)
        h = dict(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
        if body is not None:
            h["Content-Type"] = "application/json"
        if token:
            h["Authorization"] = "Bearer " + token
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        out = r.read()
        c.close()
        try:
            obj = json.loads(out.decode("utf-8"))
        except ValueError:
            obj = None
        return r.status, dict(r.getheaders()), out, obj

    def people(self, name):
        """Un'azienda con amministratore e operatore. Ritorna (gettone admin, gettone operatore, id azienda)."""
        admin, tid = self.new_tenant(name, "completo")
        slug = name.lower().replace(" ", "-")
        con = db.connect()
        ops.create_user(con, tid, "operatore@%s.it" % slug, "Operatore", "member", PW)
        con.close()
        return admin, self.login("operatore@%s.it" % slug), tid

    def test_ogni_rotta_rifiuta_chi_non_ha_il_grado(self):
        admin, member, _ = self.people("Gradi Uno")
        wrong = []
        for method, rx, level, _fn in app.ROUTES:
            path = sample(rx.pattern)
            if path == "/api/logout":
                continue  # farebbe uscire chi sta provando
            for who, token in (("nessuno", None), ("operatore", member), ("amministratore", admin)):
                status = self.req(method, path, {} if method != "GET" else None, token)[0]
                allowed = level == "public" or (who != "nessuno" and level in ("any", "user")) or (who == "amministratore" and level == "admin")
                if status >= 500 or (not allowed and status not in (401, 403)):
                    wrong.append((method, path, level, who, status))
        self.assertEqual(wrong, [])
        self.assertGreater(len(app.ROUTES), 70)
        for path in ("/api/files/upload", "/api/files/1/content", "/api/launcher/jobs/1/file"):
            self.assertIn(self.req("POST" if "content" not in path else "GET", path, raw=b"x")[0], (400, 401, 403), path)

    def test_chi_puo_fare_cosa_e_una_decisione_scritta(self):
        """Questa tabella è la decisione su chi può chiamare cosa. Una rotta nuova, o un permesso allargato, fa fallire la prova:
        va aggiunta qui a ragion veduta. Tutto quello che non è elencato è riservato agli amministratori dell'azienda."""
        expected = {
            "public": ['GET /api/google/callback', 'GET /api/health', 'GET /api/launcher/catalog', 'GET /api/launcher/packages/([a-z]+)', 'GET /api/launcher/sync',
                       'GET /api/register', 'GET /api/verify', 'POST /api/launcher/jobs/(d+)/progress', 'POST /api/launcher/jobs/(d+)/result', 'POST /api/launcher/jobs/claim',
                       'POST /api/launcher/pair', 'POST /api/launcher/report', 'POST /api/login', 'POST /api/password/forgot', 'POST /api/password/reset', 'POST /api/register',
                       'POST /api/register/resend'],
            "any": ['GET /api/2fa', 'GET /api/me', 'POST /api/2fa/disable', 'POST /api/2fa/enable', 'POST /api/2fa/setup', 'POST /api/logout'],
            "user": ['DELETE /api/files/(d+)', 'GET /api/approvals', 'GET /api/billing', 'GET /api/conversations/(d+)', 'GET /api/credits', 'GET /api/data/(leads|events|drafts)',
                     'GET /api/devices', 'GET /api/files', 'GET /api/memory', 'GET /api/memory/search', 'GET /api/news', 'GET /api/office/agents/([a-z]+)', 'GET /api/summary',
                     'GET /api/tenant/agents', 'POST /api/chat', 'POST /api/news/read'],
            "super": ['GET /api/admin/console', 'GET /api/admin/settings', 'GET /api/admin/tenants', 'POST /api/admin/console', 'POST /api/admin/settings',
                      'POST /api/admin/settings/test-email', 'POST /api/admin/tenants', 'POST /api/admin/tenants/(d+)'],
        }
        actual = {}
        for method, rx, level, _fn in app.ROUTES:
            actual.setdefault(level, []).append("%s %s" % (method, rx.pattern.strip("^$").replace("\\", "")))
        for level, routes in expected.items():
            self.assertEqual(sorted(actual.get(level, [])), sorted(routes), level)
        self.assertEqual(sorted(actual), ["admin", "any", "public", "super", "user"])
        self.assertTrue(all(r.split(" ")[1].startswith("/api/admin/") for r in expected["super"]) and not [r for r in actual["admin"] if "/api/admin/" in r],
                        "tutto quello che sta sotto /api/admin è del solo gestore")

    def test_un_azienda_non_legge_e_non_tocca_i_dati_di_un_altra(self):
        a, _, ta = self.people("Vittima Uno")
        b, _, tb = self.people("Intruso Uno")
        conv = self.chat(a, "aggiungi lead Mario Rossi mario@rossi.it", "crm")[1]["conversation_id"]
        self.chat(a, "manda una email a cliente@esempio.it: il segreto della vittima", "email")
        self.chat(a, "ricorda che il fornitore si chiama Segretissimo", "centrale")
        self.assertEqual(self.call("POST", "/api/automations", {"name": "Lavoro della vittima", "agent_id": "email", "model": "claude", "prompt": "x", "kind": "daily", "hour": 8, "minute": 0}, a)[0], 200)
        self.assertEqual(self.upload(a, "segreto.png", PNG)[0], 200)
        code = self.call("POST", "/api/devices/code", {}, a)[1]["code"]
        device = self.call("POST", "/api/launcher/pair", {"code": code, "name": "PC della vittima"})[1]
        code_b = self.call("POST", "/api/devices/code", {}, b)[1]["code"]
        device_b = self.call("POST", "/api/launcher/pair", {"code": code_b, "name": "PC dell'intruso"})[1]
        con = db.connect()
        self.addCleanup(con.close)
        con.execute("INSERT INTO jobs(tenant_id,agent_id,tool,package,place,args,status,device_id,requested_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (ta, "dev", "dev_elenca_file", "dev", "computer", "{}", "in_corso", device["device_id"], 1, db.now()))
        one = lambda table: con.execute("SELECT id FROM %s WHERE tenant_id=? ORDER BY id LIMIT 1" % table, (ta,)).fetchone()[0]
        snapshot = lambda: {t: con.execute("SELECT * FROM %s WHERE tenant_id=? ORDER BY id" % t, (ta,)).fetchall() for t in
                            ("conversations", "approvals", "automations", "media_assets", "users", "leads", "devices", "jobs", "memory")}
        before = snapshot()
        fact = self.call("GET", "/api/memory", token=a)[1]["proposals"][0]["id"]
        user_b = con.execute("SELECT id FROM users WHERE tenant_id=? AND role='member'", (tb,)).fetchone()[0]
        probes = [("GET", "/api/conversations/%d" % conv, None, b), ("POST", "/api/approvals/%d/approve" % one("approvals"), {}, b), ("POST", "/api/approvals/%d/reject" % one("approvals"), {}, b),
                  ("POST", "/api/automations/%d" % one("automations"), {"name": "Rubato", "active": False}, b), ("POST", "/api/automations/%d/run" % one("automations"), {}, b),
                  ("DELETE", "/api/automations/%d" % one("automations"), None, b), ("DELETE", "/api/files/%d" % one("media_assets"), None, b),
                  ("POST", "/api/users/%d" % one("users"), {"role": "member", "active": False, "password": "password-rubata-123"}, b),
                  ("POST", "/api/memory/%d/confirm" % fact, {}, b), ("DELETE", "/api/memory/%d" % fact, None, b), ("DELETE", "/api/devices/%d" % device["device_id"], None, b),
                  ("POST", "/api/launcher/jobs/%d/progress" % one("jobs"), {"progress": 50}, device_b["token"]), ("POST", "/api/launcher/jobs/%d/result" % one("jobs"), {"ok": True, "text": "finto"}, device_b["token"]),
                  ("POST", "/api/admin/tenants/%d" % ta, {"active": False}, b),
                  ("POST", "/api/chat", {"message": "continua", "agent_id": "crm", "model": "claude", "conversation_id": conv}, b)]
        for method, path, body, token in probes:
            status, _, out, _ = self.req(method, path, body, token)
            self.assertIn(status, (403, 404), (method, path, out[:120]))
            for secret in (b"Segret", b"Mario", b"segreto della vittima"):
                self.assertNotIn(secret, out, (method, path))
        tried = {(m, re.sub(r"\d+", "N", p)) for m, p, _, _ in probes}
        with_id = {(m, sample(rx.pattern).replace("999999", "N")) for m, rx, _, _ in app.ROUTES if r"(\d+)" in rx.pattern}
        self.assertEqual(with_id - tried, set(), "ogni rotta che prende un numero deve avere qui la sua prova: aggiungila")
        self.assertEqual(snapshot(), before, "nella vittima non è cambiata nemmeno una riga")
        for table in ("leads", "events", "drafts"):
            self.assertEqual(self.call("GET", "/api/data/" + table, token=b)[1]["rows"], [])
        for path in ("/api/memory", "/api/files", "/api/approvals", "/api/automations", "/api/devices", "/api/news", "/api/audit", "/api/users"):
            out = self.req("GET", path, token=b)[2]
            for secret in (b"Segret", b"Mario", b"vittima"):
                self.assertNotIn(secret, out, path)
        self.assertEqual(self.req("POST", "/api/launcher/jobs/%d/progress" % one("jobs"), {"progress": "abc"}, device["token"])[0], 200, "il computer giusto può, anche con un valore strano")
        url = self.call("GET", "/api/files", token=a)[1]["assets"][0]["url"]
        expires = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["e"][0]
        asset = one("media_assets")
        self.assertEqual(self.fetch(url)[0], 200)
        for forged in (url[:-1] + ("0" if url[-1] != "0" else "1"), url.replace("/%d/" % asset, "/%d/" % (asset + 1)), url.replace("e=" + expires, "e=%d" % (int(expires) + 600)), url.split("?")[0]):
            self.assertEqual(self.fetch(forged)[0], 403, forged)

    def test_nessuno_sale_di_grado(self):
        admin, member, tid = self.people("Gradi Due")
        other, _, other_id = self.people("Gradi Tre")
        con = db.connect()
        self.addCleanup(con.close)
        me = con.execute("SELECT id FROM users WHERE tenant_id=? AND role='member'", (tid,)).fetchone()[0]
        for role in ("superadmin", "gestore", "root", "Admin"):
            self.assertEqual(self.call("POST", "/api/users", {"email": "nuovo-%s@gradi-due.it" % (role or "vuoto"), "name": "X", "role": role, "password": PW}, admin)[0], 400, role)
        self.assertEqual(self.call("POST", "/api/users/%d" % me, {"role": "superadmin"}, admin)[0], 400)
        self.assertEqual(self.call("POST", "/api/users/%d" % me, {"role": "admin"}, member)[0], 403, "un operatore non si promuove da solo")
        self.assertEqual(self.call("POST", "/api/users", {"email": "infiltrato@gradi-due.it", "name": "Y", "role": "admin", "password": PW, "tenant_id": other_id}, admin)[0], 200)
        self.assertEqual(con.execute("SELECT tenant_id FROM users WHERE email='infiltrato@gradi-due.it'").fetchone()[0], tid, "il tenant_id scritto nel corpo non conta")
        self.assertEqual(con.execute("SELECT COUNT(*) FROM users WHERE role='superadmin'").fetchone()[0], 1)
        for body in ({"package_id": "completo"}, {"paid_until": "2099-01-01"}, {"platform_keys": True}, {"monthly_token_cap": 0}):
            self.assertEqual(self.call("POST", "/api/admin/tenants/%d" % tid, body, admin)[0], 403, body)
        self.assertEqual(self.call("POST", "/api/register", {"company": "Gradi Quattro", "name": "T", "email": "t@gradi-quattro.it", "password": PW, "agents": [], "accept": True,
                                                              "role": "superadmin", "package_id": "completo", "platform_keys": 1, "paid_until": "2099-01-01"})[0], 200)
        row = con.execute("SELECT u.role, t.platform_keys, t.paid_until FROM users u JOIN tenants t ON t.id=u.tenant_id WHERE u.email='t@gradi-quattro.it'").fetchone()
        self.assertEqual(tuple(row), ("admin", 0, ""), "i campi in più nella registrazione non contano")

    def test_le_sessioni_muoiono_quando_devono(self):
        admin, member, tid = self.people("Sessioni Uno")
        gestore = self.login("gestore@test.it")
        con = db.connect()
        self.addCleanup(con.close)
        uid = con.execute("SELECT id FROM users WHERE tenant_id=? AND role='member'", (tid,)).fetchone()[0]
        self.assertEqual(len(member), 43)
        self.assertIsNone(con.execute("SELECT 1 FROM sessions WHERE token_hash=?", (member,)).fetchone(), "nel database c'è solo l'impronta del gettone")
        self.call("POST", "/api/logout", {}, member)
        self.assertEqual(self.call("GET", "/api/me", token=member)[0], 401, "dopo l'uscita")
        member = self.login("operatore@sessioni-uno.it")
        self.assertEqual(self.call("POST", "/api/users/%d" % uid, {"password": "password-nuova-456"}, admin)[0], 200)
        self.assertEqual(self.call("GET", "/api/me", token=member)[0], 401, "dopo il cambio della password")
        member = self.login("operatore@sessioni-uno.it", "password-nuova-456")
        self.assertEqual(self.call("POST", "/api/users/%d" % uid, {"active": False}, admin)[0], 200)
        self.assertEqual(self.call("GET", "/api/me", token=member)[0], 401, "utente disattivato")
        self.assertEqual(self.call("POST", "/api/admin/tenants/%d" % tid, {"active": False}, gestore)[0], 200)
        self.assertEqual((self.call("GET", "/api/me", token=admin)[0], self.chat(admin, "ciao")[0]), (401, 401), "azienda sospesa")
        for forged in ("", "x", member[:-1] + "A", "sfbd_" + "a" * 43, "A" * 60000):
            self.assertEqual(self.req("GET", "/api/me", token=forged or None)[0], 401)

    def test_forza_bruta_senza_chiudere_fuori_chi_ha_la_password_giusta(self):
        self.new_tenant("Bruta Uno", "start")
        victim = "admin@bruta-uno.it"
        os.environ.update({"SFB_TRUST_PROXY": "1", "SFB_LOGIN_PER_IP": "8", "SFB_LOGIN_PER_EMAIL": "12"})
        self.addCleanup(lambda: (os.environ.update({"SFB_TRUST_PROXY": "0", "SFB_LOGIN_PER_IP": "100000", "SFB_LOGIN_PER_EMAIL": "100000"}), auth.reset_fails()))
        attempt = lambda email, password, ip: self.req("POST", "/api/login", {"email": email, "password": password}, headers={"X-Forwarded-For": ip})[0]
        self.assertEqual([attempt(victim, "sbagliata-%d" % i, "203.0.113.5") for i in range(7)], [401] * 5 + [429] * 2, "cinque tentativi, poi quell'indirizzo è fermo su quella email")
        self.assertEqual(attempt(victim, PW, "203.0.113.5"), 429, "fermo anche con la password giusta, da quell'indirizzo")
        self.assertEqual(attempt(victim, PW, "198.51.100.7"), 200, "il vero proprietario, dal suo indirizzo, entra lo stesso")
        auth.reset_fails()
        codes = [attempt("persona%d@esempio.it" % i, "password-comune-1", "203.0.113.9") for i in range(12)]
        self.assertEqual(codes, [401] * 8 + [429] * 4, "la stessa password provata su molte email: l'indirizzo viene fermato")
        self.assertEqual(attempt(victim, PW, "203.0.113.9"), 429)
        self.assertEqual(attempt(victim, PW, "198.51.100.7"), 200, "gli altri indirizzi non ne risentono")
        auth.reset_fails()
        codes = [attempt(victim, "sbagliata", "203.0.113.%d" % (20 + i)) for i in range(14)]
        self.assertEqual(codes, [401] * 12 + [429] * 2, "molti indirizzi contro una sola email: a un certo punto si ferma tutto per quella email")
        auth.reset_fails()
        for i in range(50010):
            auth.note_failure("riempimento%d@esempio.it" % i, "203.0.113.200")
        self.assertLess(len(auth._fails), 50010 * 2, "un'ondata di tentativi non riempie la memoria")

    def test_intestazioni_di_sicurezza_su_ogni_risposta(self):
        admin, _, _ = self.people("Intestazioni Uno")
        self.upload(admin, "punto.png", PNG)
        file_url = self.call("GET", "/api/files", token=admin)[1]["assets"][0]["url"]
        wanted = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer", "Cross-Origin-Opener-Policy": "same-origin",
                  "Cross-Origin-Resource-Policy": "same-origin"}
        pages = [("GET", "/", None), ("GET", "/app", None), ("GET", "/app.js", None), ("GET", "/privacy", None), ("GET", "/api/health", None), ("GET", "/api/me", admin),
                 ("GET", file_url, None), ("GET", "/non-esiste", None), ("POST", "/api/login", None), ("TRACE", "/api/me", None), ("OPTIONS", "/api/login", None), ("PATCH", "/", None)]
        for method, path, token in pages:
            status, heads, _, _ = self.req(method, path, token=token)
            for key, value in wanted.items():
                self.assertEqual(heads.get(key), value, (method, path, key))
            self.assertIn("default-src 'self'", heads.get("Content-Security-Policy", ""), path)
            self.assertIn("camera=()", heads.get("Permissions-Policy", ""), path)
            self.assertNotIn("Python", heads.get("Server", ""), "la versione di Python non si dice")
            self.assertFalse([k for k in heads if k.lower().startswith("access-control")], "nessun altro sito può chiamare il server dal browser")
            self.assertNotIn("Strict-Transport-Security", heads, "in HTTP non si promette HTTPS")
            if path != file_url:
                self.assertEqual(heads.get("Cache-Control"), "no-store", path)
        public = os.environ["SFB_PUBLIC_URL"]
        os.environ["SFB_PUBLIC_URL"] = "https://app.esempio.it"
        self.addCleanup(lambda: os.environ.__setitem__("SFB_PUBLIC_URL", public))
        for path in ("/", "/api/health", "/non-esiste"):
            self.assertEqual(self.req("GET", path)[1].get("Strict-Transport-Security"), "max-age=31536000", path)

    def test_richieste_scritte_male_non_danno_mai_un_errore_interno(self):
        admin, _, _ = self.people("Malformate Uno")
        auth_h = {"Authorization": "Bearer " + admin}
        cases = [("POST", "/api/login", b"[" * 100000 + b"]" * 100000, {"Content-Type": "application/json"}), ("POST", "/api/login", b"\xff\xfe{", {}), ("POST", "/api/login", b"", {"Content-Length": "abc"}),
                 ("POST", "/api/login", b"", {"Content-Length": "-5"}), ("POST", "/api/login", b"nullo", {}), ("POST", "/api/login", json.dumps({"email": ["a@b.it"], "password": {"$ne": ""}}).encode(), {}),
                 ("POST", "/api/login", json.dumps({"email": "a\u0000@b.it' OR '1'='1", "password": "x\u0000"}).encode(), {}), ("GET", "/api/conversations/" + "9" * 400, b"", auth_h),
                 ("POST", "/api/chat", json.dumps({"message": "ciao", "agent_id": "centrale", "model": "claude", "conversation_id": 10 ** 40}).encode(), auth_h),
                 ("POST", "/api/automations", json.dumps({"name": "x", "agent_id": "email", "model": "claude", "prompt": "x", "kind": "daily", "hour": 10 ** 30, "minute": -1}).encode(), auth_h),
                 ("GET", "/vendor/../../.env", b"", {}), ("GET", "/vendor/%2e%2e/%2e%2e/.env", b"", {}), ("GET", "//etc/passwd", b"", {}), ("GET", "/.env", b"", {}), ("GET", "/data/salesforb.db", b"", {}),
                 ("GET", "/.git/config", b"", {}), ("GET", "/app.js%00.png", b"", {}), ("GET", "/api/files/1/content?e=abc&s=" + "f" * 5000, b"", {}), ("PUT", "/api/me", b"{}", auth_h), ("TRACE", "/api/me", b"", {})]
        for method, path, body, headers in cases:
            status, _, out, _ = self.req(method, path, raw=body, headers=headers)
            self.assertTrue(400 <= status < 500, (method, path[:60], status, out[:80]))
            for leak in (b"Traceback", b"SFB_SECRET_KEY", b"sqlite", b"root:"):
                self.assertNotIn(leak, out, path[:60])
        u = urllib.parse.urlsplit(self.base)
        for garbage in (b"GARBAGE\r\n\r\n", b"GET / HTTP/9.9\r\n\r\n", b"GET " + b"/a" * 40000 + b" HTTP/1.1\r\n\r\n", b"\x16\x03\x01\x02\x00\x01\x00\x01\xfc\x03\x03"):
            s = socket.create_connection((u.hostname, u.port), timeout=10)
            s.sendall(garbage)
            s.shutdown(socket.SHUT_WR)
            reply = s.recv(4000)
            s.close()
            self.assertNotIn(b"Python", reply)
            self.assertNotIn(b"<html", reply.lower(), "niente pagina d'errore predefinita")
        self.assertEqual(self.req("GET", "/api/health")[0], 200, "dopo tutto questo il server risponde")
        status, heads, out, _ = self.req("HEAD", "/")
        self.assertEqual((status, out, int(heads["Content-Length"]) > 1000), (200, b"", True), "chi controlla se il sito è su chiede solo le intestazioni")
        self.assertEqual((self.req("HEAD", "/api/health")[0], self.req("HEAD", "/api/me", token=admin)[0]), (200, 405))

    def test_le_pagine_web_non_arrivano_mai_alla_rete_interna(self):
        calls, targets = [], []

        def resolve(host, *a, **k):  # un DNS malevolo: prima un indirizzo pubblico, poi il server stesso
            calls.append(host)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34" if len(calls) == 1 else "127.0.0.1", 0))]

        def connect(address, *a, **k):
            targets.append(address)
            raise OSError("collegamento di prova")
        real = socket.getaddrinfo, socket.create_connection
        socket.getaddrinfo, socket.create_connection = resolve, connect
        try:
            with self.assertRaises(connectors.ConnectorError):
                connectors.fetch_page("http://cambia.esempio.it/segreti")
            self.assertEqual((calls, targets), (["cambia.esempio.it"], [("93.184.216.34", 80)]), "ci si collega all'indirizzo controllato, e il nome si risolve una volta sola")
        finally:
            socket.getaddrinfo, socket.create_connection = real
        for url in ("http://127.0.0.1:8787/api/health", "http://localhost/", "http://169.254.169.254/latest/meta-data/", "http://10.0.0.1/", "http://[::1]/", "http://[::ffff:127.0.0.1]/",
                    "http://0.0.0.0/", "http://2130706433/", "http://utente:parola@esempio.it/", "file:///etc/passwd", "gopher://esempio.it/", "http://esempio.it:99999/"):
            with self.assertRaises(connectors.ConnectorError, msg=url):
                connectors.fetch_page(url)

        class Redirect:  # una pagina pubblica che rimanda a un indirizzo interno
            pinned = None

            def __init__(self, host, port=None, timeout=None):
                self.host = host

            def request(self, *a, **k):
                pass

            def getresponse(self):
                class R:
                    status = 302

                    def getheader(self, name):
                        return "http://169.254.169.254/latest/meta-data/"
                return R()

            def close(self):
                pass
        real_class, real_ip = connectors._PinnedHTTP, connectors._public_ip
        seen = []
        connectors._PinnedHTTP = Redirect
        connectors._public_ip = lambda host: "93.184.216.34"
        try:  # il nome è pubblico, ma l'indirizzo porta con sé utente e password: non parte nemmeno il collegamento
            with self.assertRaises(connectors.ConnectorError) as err:
                connectors.fetch_page("http://utente:parola@pubblico.esempio.it/")
            self.assertIn("solo indirizzi http o https", str(err.exception))
        finally:
            connectors._public_ip = real_ip
        connectors._public_ip = lambda host: seen.append(host) or real_ip(host) if host != "pubblico.esempio.it" else "93.184.216.34"
        try:
            with self.assertRaises(connectors.ConnectorError) as err:
                connectors.fetch_page("http://pubblico.esempio.it/")
            self.assertIn("rete interna", str(err.exception))
            self.assertEqual(seen, ["169.254.169.254"], "anche l'indirizzo del rimando viene controllato")
        finally:
            connectors._PinnedHTTP, connectors._public_ip = real_class, real_ip

    def test_i_server_di_posta_di_un_azienda_non_possono_essere_interni(self):
        admin, _, tid = self.people("Posta Interna")
        base = {"username": "info@posta-interna.it", "password": "segreta", "smtp_host": "smtp.esempio.it", "imap_host": "imap.esempio.it"}
        real = socket.getaddrinfo
        socket.getaddrinfo = lambda host, *a, **k: real(host, *a, **k) if re.fullmatch(r"[0-9.]+|localhost", str(host)) else [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        self.addCleanup(lambda: setattr(socket, "getaddrinfo", real))
        for key in ("imap_host", "smtp_host"):
            for host in ("127.0.0.1", "localhost", "169.254.169.254", "10.0.0.5", "192.168.1.1", "0.0.0.0", "imap esempio.it", "http://imap.esempio.it", "imap.esempio.it/percorso", "[::1]"):
                status, out = self.call("PUT", "/api/connectors/email", dict(base, **{key: host}), admin)
                self.assertEqual(status, 400, (key, host, out))
        self.assertEqual(self.call("PUT", "/api/connectors/email", dict(base, imap_port="70000"), admin)[0], 400)
        con = db.connect()
        self.addCleanup(con.close)
        self.assertIsNone(con.execute("SELECT 1 FROM connectors WHERE tenant_id=? AND name='email'", (tid,)).fetchone(), "niente di tutto questo è stato salvato")
        self.assertEqual(self.call("PUT", "/api/connectors/email", base, admin)[0], 200, "un server vero si salva")


class DatiDiUnAzienda(Base):
    def fill(self, name):
        """Un'azienda con qualcosa in ogni angolo. Ritorna (gettone, id)."""
        token, tid = self.new_tenant(name, "completo")
        self.chat(token, "aggiungi lead Cliente di %s cliente@esempio.it" % name, "crm")
        self.chat(token, "manda una email a cliente@esempio.it: preventivo di %s" % name, "email")
        self.chat(token, "ricorda che il fornitore di %s si chiama Rossi" % name, "centrale")
        self.call("POST", "/api/automations", {"name": "Lavoro di " + name, "agent_id": "email", "model": "claude", "prompt": "x", "kind": "daily", "hour": 8, "minute": 0}, token)
        self.assertEqual(self.upload(token, "logo.png", PNG)[0], 200)
        code = self.call("POST", "/api/devices/code", {}, token)[1]["code"]
        self.assertEqual(self.call("POST", "/api/launcher/pair", {"code": code, "name": "PC di " + name})[0], 200)
        self.call("POST", "/api/devices/code", {}, token)
        con = db.connect()
        connectors.save(con, tid, "telegram", {"bot_token": "123456:segreto-del-bot-di-" + name.replace(" ", "-"), "chat_id": "42"})
        con.close()
        return token, tid

    def test_esportazione_completa_senza_segreti_e_cancellazione_senza_resti(self):
        import zipfile
        from server import media, tenantdata
        x, tx = self.fill("Da Cancellare")
        y, ty = self.fill("Che Resta")
        con = db.connect()
        self.addCleanup(con.close)
        users = [r[0] for r in con.execute("SELECT id FROM users WHERE tenant_id=?", (tx,))]
        filled = tenantdata.counts(con, tx)
        for table in ("users", "conversations", "messages", "approvals", "audit", "connectors", "leads", "usage", "automations", "news", "memory", "tenant_agents", "media_assets", "devices", "device_codes"):
            self.assertGreater(filled[table], 0, "la prova deve riempire anche «%s»" % table)
        known = set(tenantdata.tables(con)) | {"tenants", "sessions", "email_tokens", "platform_settings", "billing_events", "sqlite_sequence"}
        everything = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(everything - known, set(), "tabella nuova senza la colonna dell'azienda: decidi se va esportata e cancellata e aggiungila a tenantdata")
        pw_hash = con.execute("SELECT pw_hash FROM users WHERE tenant_id=?", (tx,)).fetchone()[0]
        device_hash = con.execute("SELECT token_hash FROM devices WHERE tenant_id=?", (tx,)).fetchone()[0]
        path = os.path.join(self.enterContext(__import__("tempfile").TemporaryDirectory()), "esportazione.zip")
        out = tenantdata.export(con, tenantdata.find(con, "Da Cancellare"), path)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            blob = b"".join(z.read(n) for n in names)
            leads = json.loads(z.read("dati/leads.json"))
            stored = json.loads(z.read("dati/media_assets.json"))[0]["stored_name"]
            self.assertEqual(z.read("file/" + stored), PNG, "il file caricato è nell'archivio, identico")
        self.assertEqual((out["file"], [l["name"] for l in leads]), (1, ["Cliente di Da Cancellare"]))
        for table in tenantdata.tables(con):
            self.assertEqual("dati/%s.json" % table in names, table not in tenantdata.SKIP_TABLES, table)
        for secret in (pw_hash, device_hash, "segreto-del-bot-di-Da-Cancellare", "Che Resta", "Cliente di Che Resta", "stripe_"):
            self.assertNotIn(secret.encode(), blob, "nell'archivio non deve esserci: %s" % secret[:30])
        snapshot = lambda: {t: [tuple(r) for r in con.execute("SELECT * FROM %s WHERE tenant_id=? ORDER BY rowid" % t, (ty,))] for t in tenantdata.tables(con)}
        before = snapshot()
        removed = tenantdata.erase(con, tenantdata.find(con, tx))
        self.assertGreater(sum(removed.values()), 30)
        self.assertEqual(tenantdata.leftovers(con, tx, users), {})
        for table in tenantdata.tables(con):
            self.assertEqual(con.execute("SELECT COUNT(*) FROM %s WHERE tenant_id=?" % table, (tx,)).fetchone()[0], 0, table)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM sessions WHERE user_id IN (%s)" % ",".join("?" * len(users)), users).fetchone()[0], 0)
        self.assertFalse(media.tenant_dir(tx).exists(), "anche i file sono spariti dal disco")
        self.assertEqual(self.call("GET", "/api/me", token=x)[0], 401, "chi era dentro è fuori")
        self.assertEqual(self.call("POST", "/api/login", {"email": "admin@da-cancellare.it", "password": PW})[0], 401)
        self.assertEqual(snapshot(), before, "l'altra azienda non ha perso nemmeno una riga")
        self.assertEqual(self.call("GET", "/api/me", token=y)[0], 200)
        self.assertEqual(self.fetch(self.call("GET", "/api/files", token=y)[1]["assets"][0]["url"])[2], PNG)
        self.assertTrue(self.call("GET", "/api/audit/verify", token=y)[1]["ok"], "il registro dell'altra azienda è integro")
        last = con.execute("SELECT action, detail FROM audit WHERE tenant_id=0 ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual((last["action"], json.loads(last["detail"])["azienda"]), ("azienda_cancellata", "Da Cancellare"))

    def test_il_comando_non_cancella_per_sbaglio(self):
        import argparse
        import manage
        token, tid = self.fill("Quasi Cancellata")
        con = db.connect()
        self.addCleanup(con.close)
        tmp = self.enterContext(__import__("tempfile").TemporaryDirectory())
        args = lambda **kw: argparse.Namespace(**dict({"azienda": str(tid), "conferma": "", "copia": None, "senza_copia": False}, **kw))
        alive = lambda: self.call("GET", "/api/me", token=token)[0] == 200
        for wrong in (args(), args(senza_copia=True), args(conferma="quasi cancellata", senza_copia=True), args(conferma="Quasi Cancellata "), args(conferma="Quasi Cancellata"), args(azienda="Non Esiste", conferma="Non Esiste", senza_copia=True), args(azienda="0", conferma="", senza_copia=True)):
            self.assertEqual(manage.cmd_cancella(wrong), 1)
            self.assertTrue(alive(), "non è stato cancellato niente")
        con.execute("UPDATE tenants SET stripe_subscription='sub_viva' WHERE id=?", (tid,))
        self.assertEqual(manage.cmd_cancella(args(conferma="Quasi Cancellata", senza_copia=True)), 1, "con un abbonamento attivo si disdice prima")
        self.assertTrue(alive())
        con.execute("UPDATE tenants SET stripe_subscription='' WHERE id=?", (tid,))
        copy = os.path.join(tmp, "prima.zip")
        self.assertEqual(manage.cmd_esporta(argparse.Namespace(azienda="Quasi Cancellata", file=os.path.join(tmp, "solo-copia.zip"))), 0)
        self.assertTrue(alive(), "esportare non cancella")
        self.assertEqual(manage.cmd_cancella(args(azienda="Quasi Cancellata", conferma="Quasi Cancellata", copia=copy)), 0)
        self.assertTrue(os.path.getsize(copy) > 1000 and not alive())
        self.assertIsNone(con.execute("SELECT 1 FROM tenants WHERE id=?", (tid,)).fetchone())


if __name__ == "__main__":
    unittest.main()
