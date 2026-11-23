"""Server HTTP di Alvea.ai: API JSON e schermata. Solo libreria standard di Python."""
from __future__ import annotations

import json
import re
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import (agents, audit, auth, catalog, config, connectors, db, fake_llm, google, llm, local, media, memory, news, office, ops,
               registration, scheduler, team)
from .errors import ApiError

MAX_BODY = 1000000
CHAT_PER_MINUTE = 30
# Elenco chiuso dei file serviti: niente percorsi costruiti dalla richiesta.
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8"), "/logo.png": ("logo.png", "image/png"), "/icon.png": ("icon.png", "image/png"),
          "/favicon.ico": ("icon.png", "image/png"), "/office.js": ("office.js", "text/javascript; charset=utf-8"),
          "/vendor/mini3d.js": ("vendor/mini3d.js", "text/javascript; charset=utf-8")}
# Tutto viene dallo stesso server: nessun CDN, nessuno script in linea, nessun eval. Il motore 3D è un file locale.
CSP = "; ".join([
    "default-src 'self'", "script-src 'self'", "style-src 'self'", "img-src 'self'", "media-src 'self'", "font-src 'self'",
    "connect-src 'self'", "object-src 'none'", "worker-src 'none'", "frame-src 'none'",
    "frame-ancestors 'none'", "base-uri 'none'", "form-action 'self'"])
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": CSP,
}

_chat_times = {}
_chat_lock = threading.Lock()
_dummy_hash = None


class Req:
    def __init__(self, con, query, body, user, tenant, token, args, cookies=None, ip=None):
        self.con, self.query, self.body, self.user, self.tenant, self.token, self.args = con, query, body, user, tenant, token, args
        self.cookies, self.ip = cookies or {}, ip


class Raw:
    """Risposta non standard: reindirizzamento o JSON con intestazioni in più (cookie)."""

    def __init__(self, status, headers=None, obj=None):
        self.status, self.headers, self.obj = status, headers or [], obj


def _int_arg(value, what="Il numero"):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ApiError(400, "%s non è valido." % what)


def _public_user(u):
    return {"id": u["id"], "name": u["name"], "email": u["email"], "role": u["role"], "active": bool(u.get("active", 1))}


def _throttle_chat(user_id):
    now = time.time()
    with _chat_lock:
        times = [t for t in _chat_times.get(user_id, []) if t > now - 60]
        if len(times) >= CHAT_PER_MINUTE:
            raise ApiError(429, "Troppi messaggi in un minuto. Aspetta qualche secondo.")
        times.append(now)
        _chat_times[user_id] = times


# ---------- rotte ----------

def r_health(q):
    return {"ok": True, "demo": config.demo_mode()}


def r_login(q):
    global _dummy_hash
    email = str(q.body.get("email") or "").strip().lower()
    password = str(q.body.get("password") or "")
    if auth.locked(email):
        raise ApiError(429, "Troppi tentativi sbagliati. Riprova tra qualche minuto.")
    row = q.con.execute("SELECT * FROM users WHERE email=? AND active=1", (email,)).fetchone()
    if row is None:
        if _dummy_hash is None:
            _dummy_hash = auth.hash_password("password-inesistente")
        auth.check_password(password, _dummy_hash)  # stesso tempo di risposta se l'utente non esiste
        ok = False
    else:
        ok = auth.check_password(password, row["pw_hash"])
        if ok and row["tenant_id"] is not None:
            ok = bool(q.con.execute("SELECT 1 FROM tenants WHERE id=? AND active=1", (row["tenant_id"],)).fetchone())
    if not ok:
        auth.note_failure(email)
        raise ApiError(401, "Email o password non corretti.")
    auth.note_success(email)
    if not row["email_verified"]:
        raise ApiError(403, "Conferma prima il tuo indirizzo email: apri il collegamento che ti abbiamo mandato.")
    token = auth.create_session(q.con, row["id"])
    audit.record(q.con, row["tenant_id"] if row["tenant_id"] is not None else ops.PLATFORM, row["id"], "accesso", {})
    return {"token": token}


def r_logout(q):
    auth.drop_session(q.con, q.token)
    return {"ok": True}


def r_me(q):
    out = {"user": _public_user(q.user), "tenant": None, "catalog": catalog.load(), "entitled": [], "models": [],
           "demo": config.demo_mode(), "secrets_encrypted": connectors.encryption_active()}
    if q.tenant:
        pkg = catalog.package(q.tenant["package_id"])
        try:
            keys = connectors.load(q.con, q.tenant["id"], "modelli")
        except connectors.ConnectorError:
            keys = {}
        own_only = not q.tenant.get("platform_keys", 1)
        out["tenant"] = {"id": q.tenant["id"], "name": q.tenant["name"], "package_id": pkg["id"], "package_name": pkg["name"],
                         "stopped": bool(q.tenant.get("stopped")), "limits": pkg.get("limits") or {}, "own_keys_only": own_only}
        out["entitled"] = sorted(team.active(q.con, q.tenant))  # agenti accesi
        out["ceiling"] = sorted(team.ceiling(q.tenant))         # tetto del pacchetto
        out["agent_status"] = _agent_status(q.con, q.tenant)
        out["models"] = [{"id": m["id"], "name": m["name"], "configured": llm.configured(m["id"], keys, not own_only) or (
                              m["id"] == "locale" and bool(local.online_device(q.con, q.tenant["id"], "modello")))}
                         for m in team.models_for(q.con, q.tenant)]
    return out


def _agent_status(con, tenant):
    """Riga di stato sotto ogni agente incluso: collegato davvero oppure no."""
    tid, entitled, shared = tenant["id"], team.active(con, tenant), bool(tenant.get("platform_keys", 1))
    try:
        keys = connectors.load(con, tid, "modelli")
    except connectors.ConnectorError:
        keys = {}
    ready = {c["name"]: c["ready"] for c in connectors.public_state(con, tid, team.ceiling(tenant))}
    g = google.status(con, tid)
    granted = set(g["granted"]) if g["connected"] and not g["broken"] else set()
    out = {}
    for a in entitled:
        if a == "email":
            out[a] = "Gmail collegato" if granted & {"gmail_lettura", "gmail_invio"} else ("Casella collegata" if ready.get("email") else "Da collegare")
        elif a in ("telegram", "shopify", "whatsapp"):
            out[a] = "Collegato" if ready.get(a) else "Da collegare"
        elif a == "ricerca":
            out[a] = "Collegato" if ready.get("ricerca") or (shared and (config.get("BRAVE_API_KEY") or config.get("SEARXNG_URL"))) else "Da collegare"
        elif a == "calendario":
            out[a] = "Google Calendar" if "calendario" in granted else "Calendario interno"
        elif a == "chatgpt":
            out[a] = "Collegato" if llm.configured("chatgpt", keys, shared) else "Da collegare"
        elif a in ("marketing", "instagram", "linkedin", "voce", "ascolto", "contenuti"):
            out[a] = "Solo bozze"
        else:
            out[a] = "Pronto"
    # Agenti che girano sul computer dell'azienda: contano il launcher e la verifica di quel computer.
    modes, paired = team.modes(con, tenant), None
    for a in entitled:
        spec = local.local_spec(a)
        if spec and (modes.get(a) == "locale" or not catalog.agent(a)["run"]["cloud"]):
            if paired is None:
                paired = bool(con.execute("SELECT 1 FROM devices WHERE tenant_id=? AND revoked=0", (tid,)).fetchone())
            out[a] = "Sul computer" if local.online_device(con, tid, spec["package"]) else ("Computer spento" if paired else "Da collegare")
        elif a == "video":
            out[a] = "Sui nostri server" if local.cloud_video_ready() else "Da collegare"
    return out


def r_summary(q):
    """Numeri per l'intestazione, letti spesso: azioni in attesa, novità, stato."""
    tid = q.tenant["id"]
    sql, params = "SELECT COUNT(*) FROM approvals WHERE tenant_id=? AND status='pending'", [tid]
    if q.user["role"] != "admin":
        sql += " AND requested_by=?"
        params.append(q.user["id"])
    out = news.summary(q.con, tid)
    out.update({
        "pending": q.con.execute(sql, params).fetchone()[0], "stopped": bool(q.tenant.get("stopped")),
        "automations": q.con.execute("SELECT COUNT(*) FROM automations WHERE tenant_id=? AND active=1", (tid,)).fetchone()[0],
        "connections": q.con.execute("SELECT COUNT(*) FROM connectors WHERE tenant_id=? AND name<>'modelli'", (tid,)).fetchone()[0]})
    if (q.query.get("office") or [""])[0] == "1":  # la vista Ufficio 3D chiede anche lo stato di ogni agente acceso
        out["office"] = office.snapshot(q.con, q.tenant, _agent_status(q.con, q.tenant))
    return out


def r_office_agent(q):
    return office.agent_detail(q.con, q.tenant, q.user, q.args[0], _agent_status(q.con, q.tenant))


def r_news(q):
    return {"news": news.recent(q.con, q.tenant["id"], 100)}


def r_news_read(q):
    news.mark_all_read(q.con, q.tenant["id"])
    return {"ok": True}


def r_stop(q):
    ops.set_stopped(q.con, q.tenant, q.user, bool(q.body.get("active")))
    return {"stopped": bool(q.body.get("active"))}


def r_automations(q):
    return scheduler.listing(q.con, q.tenant)


def r_automation_create(q):
    return {"id": scheduler.create(q.con, q.tenant, q.user, q.body)}


def r_automation_update(q):
    scheduler.update(q.con, q.tenant, q.user, int(q.args[0]), q.body)
    return {"ok": True}


def r_automation_delete(q):
    scheduler.delete(q.con, q.tenant, q.user, int(q.args[0]))
    return {"ok": True}


def r_automation_run(q):
    agents.check_not_stopped(q.tenant)
    status, output = scheduler.run_now(q.con, q.tenant, int(q.args[0]))
    return {"status": status, "output": output}


def r_memory(q):
    return memory.listing(q.con, q.tenant, include_proposals=q.user["role"] == "admin")


def r_memory_fact(q):
    return {"id": memory.add_fact(q.con, q.tenant, q.user, q.body.get("text"))}


def r_memory_document(q):
    return {"id": memory.add_document(q.con, q.tenant, q.user, q.body.get("title"), q.body.get("text"))}


def r_memory_confirm(q):
    memory.confirm(q.con, q.tenant, q.user, int(q.args[0]))
    return {"ok": True}


def r_memory_delete(q):
    memory.delete(q.con, q.tenant, q.user, int(q.args[0]))
    return {"ok": True}


def r_memory_search(q):
    hits = memory.search(q.con, q.tenant["id"], (q.query.get("q") or [""])[0][:300], k=5)
    return {"hits": [{"title": h["title"], "kind": h["kind"], "text": h["text"][:600]} for h in hits]}


GOOGLE_COOKIE = "sfb_google"


def _google_cookie(value, max_age):
    secure = "; Secure" if google.public_url().startswith("https://") else ""
    return "%s=%s; Path=/api/google; Max-Age=%d; HttpOnly; SameSite=Lax%s" % (GOOGLE_COOKIE, value, max_age, secure)


def r_google(q):
    return google.status(q.con, q.tenant["id"])


def r_google_start(q):
    services = q.body.get("services")
    try:
        url, browser = google.start(q.con, q.tenant["id"], q.user["id"], services if isinstance(services, list) else [])
    except connectors.ConnectorError as e:
        raise ApiError(400, str(e))
    audit.record(q.con, q.tenant["id"], q.user["id"], "google_collegamento_avviato", {})
    return Raw(200, [("Set-Cookie", _google_cookie(browser, google.STATE_SECONDS))], {"url": url})


def r_google_callback(q):
    """Arrivo dal consenso di Google. Non c'è il token dell'app: l'identità viene dallo state monouso e dal cookie."""
    one = lambda k: (q.query.get(k) or [""])[0]  # noqa: E731
    result, tenant_id, user_id = google.finish(q.con, one("state"), one("code"), one("error"), q.cookies.get(GOOGLE_COOKIE))
    if tenant_id is not None:
        audit.record(q.con, tenant_id, user_id, "google_collegato" if result == "ok" else "google_collegamento_fallito", {"esito": result})
    return Raw(302, [("Location", "/#google-" + result), ("Set-Cookie", _google_cookie("", 0))])


def r_google_disconnect(q):
    google.disconnect(q.con, q.tenant["id"])
    audit.record(q.con, q.tenant["id"], q.user["id"], "google_scollegato", {})
    return {"ok": True}


def r_tenant_agents(q):
    return {"agents": team.listing(q.con, q.tenant), "package_name": catalog.package(q.tenant["package_id"])["name"]}


def r_tenant_agents_set(q):
    """Accende, spegne o configura agenti. Corpo: {"agent_id", "is_active", "config"} oppure {"agents": [ ... ]}."""
    changes = q.body.get("agents") if "agents" in q.body else [q.body]
    return {"agents": team.set_agents(q.con, q.tenant, q.user, changes)}


# ---------- computer dei clienti (launcher) e lavori ----------

def _device(q):
    return local.device_from_token(q.con, q.token)


def r_launcher_catalog(q):
    return local.public_catalog()


def r_launcher_pair(q):
    registration.throttle(q.ip, "collega", 30)
    return local.pair(q.con, q.body)


def r_launcher_report(q):
    device, _ = _device(q)
    return local.report(q.con, device, q.body)


def r_launcher_sync(q):
    device, tenant = _device(q)
    return local.sync(q.con, device, tenant)


def r_launcher_package(q):
    registration.throttle(q.ip, "pacchetto", 600)
    return local.package_signed(q.args[0])


def r_launcher_claim(q):
    device, tenant = _device(q)
    return {"job": local.claim(q.con, device, tenant, q.body.get("wait"))}


def r_launcher_progress(q):
    device, _ = _device(q)
    return local.progress(q.con, device, int(q.args[0]), q.body.get("progress"), q.body.get("note"))


def r_launcher_result(q):
    device, _ = _device(q)
    return local.result(q.con, device, int(q.args[0]), q.body.get("ok"), q.body.get("text"))


def r_devices(q):
    return {"devices": local.devices(q.con, q.tenant), "jobs": local.listing(q.con, q.tenant, 20), "packages_signed": bool(local.public_key()),
            "cloud_video": local.cloud_video_ready(), "agents": [a for a in team.listing(q.con, q.tenant) if a["in_ceiling"]],
            "launcher_version": local.launcher_version()}


def r_device_code(q):
    return local.new_code(q.con, q.tenant, q.user)


def r_device_revoke(q):
    return local.revoke(q.con, q.tenant, q.user, int(q.args[0]))


def r_register_info(q):
    return registration.public_info()


def r_register(q):
    return registration.register(q.con, q.body, q.ip)


def r_register_resend(q):
    return registration.resend(q.con, q.body, q.ip)


def r_verify(q):
    ok = registration.verify(q.con, (q.query.get("token") or [""])[0])
    return Raw(302, [("Location", "/#verifica-" + ("ok" if ok else "errore"))])


def r_files(q):
    return media.listing(q.con, q.tenant)


def r_file_delete(q):
    media.delete(q.con, q.tenant, q.user, int(q.args[0]))
    return {"ok": True}


def r_chat(q):
    _throttle_chat(q.user["id"])
    try:
        conv = int(q.body["conversation_id"]) if q.body.get("conversation_id") else None
    except (TypeError, ValueError):
        raise ApiError(400, "Conversazione non valida.")
    return agents.run_chat(q.con, q.tenant, q.user, str(q.body.get("agent_id") or agents.CENTRAL),
                           str(q.body.get("model") or ""), conv, str(q.body.get("message") or ""))


def r_conversation(q):
    conv = q.con.execute("SELECT * FROM conversations WHERE id=? AND tenant_id=? AND user_id=?",
                         (int(q.args[0]), q.tenant["id"], q.user["id"])).fetchone()
    if not conv:
        raise ApiError(404, "Conversazione non trovata.")
    msgs = db.rows(q.con.execute("SELECT role, content, meta, created_at FROM messages WHERE tenant_id=? AND conversation_id=? ORDER BY id",
                                 (q.tenant["id"], conv["id"])))
    for m in msgs:
        m["meta"] = json.loads(m["meta"])
    return {"id": conv["id"], "agent_id": conv["agent_id"], "messages": msgs}


def r_approvals(q):
    sql = ("SELECT a.id, a.agent_id, a.tool, a.summary, a.status, a.result, a.created_at, a.decided_at, "
           "u.name AS requested_by_name FROM approvals a JOIN users u ON u.id=a.requested_by WHERE a.tenant_id=?")
    params = [q.tenant["id"]]
    if q.user["role"] != "admin":  # chi non è amministratore vede solo le proprie richieste
        sql += " AND a.requested_by=?"
        params.append(q.user["id"])
    return {"approvals": db.rows(q.con.execute(sql + " ORDER BY a.id DESC LIMIT 100", params))}


def r_approval_decide(q):
    return agents.decide_approval(q.con, q.tenant, q.user, int(q.args[0]), q.args[1] == "approve")


def r_audit(q):
    limit = int((q.query.get("limit") or ["100"])[0]) if (q.query.get("limit") or ["100"])[0].isdigit() else 100
    return {"entries": audit.recent(q.con, q.tenant["id"], limit)}


def r_audit_verify(q):
    ok, count, bad_id, last = audit.verify(q.con, q.tenant["id"])
    return {"ok": ok, "entries": count, "first_broken_id": bad_id, "last_hash": last}


def r_users(q):
    rows = db.rows(q.con.execute("SELECT id, name, email, role, active FROM users WHERE tenant_id=? ORDER BY id", (q.tenant["id"],)))
    return {"users": [_public_user(r) for r in rows],
            "can_add": "team" in team.active(q.con, q.tenant)}


def r_user_create(q):
    if "team" not in team.active(q.con, q.tenant):
        audit.record(q.con, q.tenant["id"], q.user["id"], "agente_bloccato", {"agente": "team"})
        if "team" in team.ceiling(q.tenant):
            raise ApiError(403, "Per aggiungere utenti attiva l'Agente Team nella scheda Agenti.")
        raise ApiError(403, "Più utenti per azienda entrano con il pacchetto %s." % catalog.first_package_with("team")["name"])
    uid = ops.create_user(q.con, q.tenant["id"], q.body.get("email"), q.body.get("name"),
                          str(q.body.get("role") or "member"), q.body.get("password"), q.user["id"])
    return {"id": uid}


def r_user_update(q):
    ops.update_user(q.con, q.tenant["id"], int(q.args[0]), q.user, role=q.body.get("role"),
                    active=q.body.get("active"), password=q.body.get("password"))
    return {"ok": True}


def r_connectors(q):
    return {"connectors": connectors.public_state(q.con, q.tenant["id"], team.ceiling(q.tenant)),
            "secrets_encrypted": connectors.encryption_active()}


def _connector_spec(q):
    spec = connectors.DEFS.get(q.args[0])
    if not spec:
        raise ApiError(404, "Collegamento sconosciuto.")
    if spec["agent"] and spec["agent"] not in team.ceiling(q.tenant):
        raise ApiError(403, "Questo collegamento serve a un agente non incluso nel pacchetto.")
    return spec


def r_connector_save(q):
    _connector_spec(q)
    try:
        connectors.save(q.con, q.tenant["id"], q.args[0], q.body)
    except connectors.ConnectorError as e:
        raise ApiError(400, str(e))
    audit.record(q.con, q.tenant["id"], q.user["id"], "collegamento_salvato", {"collegamento": q.args[0]})
    return {"ok": True}


def r_connector_delete(q):
    _connector_spec(q)
    connectors.delete(q.con, q.tenant["id"], q.args[0])
    audit.record(q.con, q.tenant["id"], q.user["id"], "collegamento_rimosso", {"collegamento": q.args[0]})
    return {"ok": True}


def r_password_forgot(q):
    return registration.forgot(q.con, q.body, q.ip)


def r_password_reset(q):
    return registration.reset(q.con, q.body, q.ip)


def r_usage(q):
    rows = db.rows(q.con.execute(
        "SELECT substr(created_at,1,7) AS month, model, COUNT(*) AS calls, SUM(input_tokens) AS input_tokens, "
        "SUM(output_tokens) AS output_tokens FROM usage WHERE tenant_id=? GROUP BY month, model ORDER BY month DESC, model",
        (q.tenant["id"],)))
    return {"usage": rows, "token_cap": q.tenant.get("monthly_token_cap") or 0, "tokens_month": agents.month_tokens(q.con, q.tenant["id"])}


def r_data(q):
    table, order = {"leads": ("leads", "id DESC"), "events": ("events", "starts_at"), "drafts": ("drafts", "id DESC")}[q.args[0]]
    return {"rows": db.rows(q.con.execute("SELECT * FROM %s WHERE tenant_id=? ORDER BY %s LIMIT 200" % (table, order),
                                          (q.tenant["id"],)))}


def r_admin_tenants(q):
    rows = db.rows(q.con.execute(
        "SELECT t.id, t.name, t.package_id, t.active, t.created_at, t.origin, t.platform_keys, t.monthly_token_cap, "
        "(SELECT COALESCE(SUM(input_tokens+output_tokens),0) FROM usage g WHERE g.tenant_id=t.id AND g.model<>'locale' "
        "AND substr(g.created_at,1,7)=substr(?,1,7)) AS tokens_month, "
        "(SELECT COUNT(*) FROM users u WHERE u.tenant_id=t.id AND u.active=1) AS users, "
        "(SELECT COALESCE(SUM(input_tokens+output_tokens),0) FROM usage g WHERE g.tenant_id=t.id) AS tokens "
        "FROM tenants t ORDER BY t.id", (db.now(),)))
    return {"tenants": rows, "catalog": catalog.load()}


def r_admin_tenant_create(q):
    tid = ops.create_tenant(q.con, q.body.get("name"), str(q.body.get("package_id") or ""), q.body.get("admin_email"),
                            q.body.get("admin_name"), q.body.get("admin_password"), q.user["id"])
    return {"id": tid}


def r_admin_tenant_update(q):
    ops.update_tenant(q.con, int(q.args[0]), package_id=q.body.get("package_id"), active=q.body.get("active"),
                      actor_id=q.user["id"], platform_keys=q.body.get("platform_keys"), token_cap=q.body.get("monthly_token_cap"))
    return {"ok": True}


# metodo, percorso, livello richiesto, funzione
ROUTES = [(m, re.compile("^" + p + "$"), level, fn) for m, p, level, fn in [
    ("GET", "/api/health", "public", r_health),
    ("POST", "/api/login", "public", r_login),
    ("POST", "/api/logout", "any", r_logout),
    ("GET", "/api/me", "any", r_me),
    ("POST", "/api/chat", "user", r_chat),
    ("GET", r"/api/conversations/(\d+)", "user", r_conversation),
    ("GET", "/api/approvals", "user", r_approvals),
    ("POST", r"/api/approvals/(\d+)/(approve|reject)", "admin", r_approval_decide),
    ("GET", "/api/audit", "admin", r_audit),
    ("GET", "/api/audit/verify", "admin", r_audit_verify),
    ("GET", "/api/users", "admin", r_users),
    ("POST", "/api/users", "admin", r_user_create),
    ("POST", r"/api/users/(\d+)", "admin", r_user_update),
    ("GET", "/api/connectors", "admin", r_connectors),
    ("PUT", r"/api/connectors/([a-z]+)", "admin", r_connector_save),
    ("DELETE", r"/api/connectors/([a-z]+)", "admin", r_connector_delete),
    ("GET", "/api/usage", "admin", r_usage),
    ("GET", r"/api/data/(leads|events|drafts)", "user", r_data),
    ("GET", "/api/register", "public", r_register_info),
    ("POST", "/api/register", "public", r_register),
    ("POST", "/api/register/resend", "public", r_register_resend),
    ("GET", "/api/verify", "public", r_verify),
    ("GET", "/api/tenant/agents", "user", r_tenant_agents),
    ("POST", "/api/tenant/agents", "admin", r_tenant_agents_set),
    ("GET", "/api/files", "user", r_files),
    ("DELETE", r"/api/files/(\d+)", "user", r_file_delete),
    ("GET", "/api/summary", "user", r_summary),
    ("GET", r"/api/office/agents/([a-z]+)", "user", r_office_agent),
    ("POST", "/api/password/forgot", "public", r_password_forgot),
    ("POST", "/api/password/reset", "public", r_password_reset),
    ("GET", "/api/launcher/catalog", "public", r_launcher_catalog),
    ("POST", "/api/launcher/pair", "public", r_launcher_pair),
    # Le rotte del launcher sono «pubbliche» per il controllo utenti: ognuna pretende il gettone del computer.
    ("POST", "/api/launcher/report", "public", r_launcher_report),
    ("GET", "/api/launcher/sync", "public", r_launcher_sync),
    ("GET", r"/api/launcher/packages/([a-z]+)", "public", r_launcher_package),
    ("POST", "/api/launcher/jobs/claim", "public", r_launcher_claim),
    ("POST", r"/api/launcher/jobs/(\d+)/progress", "public", r_launcher_progress),
    ("POST", r"/api/launcher/jobs/(\d+)/result", "public", r_launcher_result),
    ("GET", "/api/devices", "user", r_devices),
    ("POST", "/api/devices/code", "admin", r_device_code),
    ("DELETE", r"/api/devices/(\d+)", "admin", r_device_revoke),
    ("GET", "/api/news", "user", r_news),
    ("POST", "/api/news/read", "user", r_news_read),
    ("POST", "/api/stop", "admin", r_stop),
    ("GET", "/api/automations", "admin", r_automations),
    ("POST", "/api/automations", "admin", r_automation_create),
    ("POST", r"/api/automations/(\d+)", "admin", r_automation_update),
    ("DELETE", r"/api/automations/(\d+)", "admin", r_automation_delete),
    ("POST", r"/api/automations/(\d+)/run", "admin", r_automation_run),
    ("GET", "/api/memory", "user", r_memory),
    ("GET", "/api/memory/search", "user", r_memory_search),
    ("POST", "/api/memory/facts", "admin", r_memory_fact),
    ("POST", "/api/memory/documents", "admin", r_memory_document),
    ("POST", r"/api/memory/(\d+)/confirm", "admin", r_memory_confirm),
    ("DELETE", r"/api/memory/(\d+)", "admin", r_memory_delete),
    ("GET", "/api/google", "admin", r_google),
    ("POST", "/api/google/start", "admin", r_google_start),
    ("GET", "/api/google/callback", "public", r_google_callback),
    ("DELETE", "/api/google", "admin", r_google_disconnect),
    ("GET", "/api/admin/tenants", "super", r_admin_tenants),
    ("POST", "/api/admin/tenants", "super", r_admin_tenant_create),
    ("POST", r"/api/admin/tenants/(\d+)", "super", r_admin_tenant_update),
]]


def _authorize(level, user, tenant):
    if level == "public":
        return
    if not user:
        raise ApiError(401, "Accesso scaduto o mancante. Entra di nuovo.")
    if level == "any":
        return
    if level == "super":
        if user["role"] != "superadmin":
            raise ApiError(403, "Riservato al gestore della piattaforma.")
        return
    if not tenant:
        raise ApiError(403, "Questa funzione è per gli utenti di un'azienda.")
    if level == "admin" and user["role"] != "admin":
        raise ApiError(403, "Riservato agli amministratori dell'azienda.")


def handle_api(method, path, query, body, token, cookies=None, ip=None):
    """Trova la rotta, controlla i permessi, esegue. Ritorna un dizionario o alza ApiError."""
    known_path = False
    for m, rx, level, fn in ROUTES:
        match = rx.match(path)
        if not match:
            continue
        known_path = True
        if m != method:
            continue
        con = db.connect()
        try:
            user, tenant = auth.user_from_token(con, token) if level != "public" else (None, None)
            _authorize(level, user, tenant)
            return fn(Req(con, query, body, user, tenant, token, match.groups(), cookies, ip))
        finally:
            con.close()
    raise ApiError(405 if known_path else 404, "Metodo non ammesso." if known_path else "Risorsa non trovata.")


class Handler(BaseHTTPRequestHandler):
    server_version = "Alvea.ai"
    sys_version = ""
    timeout = 120  # una connessione ferma non tiene occupato un thread per sempre

    def log_message(self, fmt, *args):
        if config.get("SFB_QUIET") != "1":
            line = re.sub(r"([?&](?:s|token|code|state)=)[^&\s\"]+", r"\1...", fmt % args)  # niente firme o codici nei log
            sys.stderr.write("%s %s\n" % (self.log_date_time_string(), line))

    def _ip(self):
        """Indirizzo del cliente. Dietro un proxy fidato (SFB_TRUST_PROXY=1) vale l'ultimo indirizzo aggiunto dal proxy."""
        if config.get("SFB_TRUST_PROXY") == "1":
            forwarded = [x.strip() for x in (self.headers.get("X-Forwarded-For") or "").split(",") if x.strip()]
            if forwarded:
                return forwarded[-1][:64]
        return self.client_address[0]

    def _bearer(self):
        header = self.headers.get("Authorization") or ""
        return header[7:].strip() if header.startswith("Bearer ") else None

    def _upload(self):
        """POST /api/files/upload: accesso e limiti PRIMA di leggere il corpo, poi lettura a pezzi su disco."""
        try:
            length = int(self.headers.get("Content-Length") or -1)
        except ValueError:
            length = -1
        body = media.Body(self.rfile, max(length, 0))
        con = db.connect()
        try:
            user, tenant = auth.user_from_token(con, self._bearer())
            _authorize("user", user, tenant)
            if length <= 0:
                raise ApiError(411, "Manca la dimensione del caricamento (Content-Length).")
            result = media.upload(con, tenant, user, body, self.headers.get("Content-Type"))
            self._json(200, result)
        except ApiError as e:
            # Se resta poco da leggere lo scarto, così il browser riceve la risposta; altrimenti chiudo la connessione.
            if 0 < body.left <= 16 * media.MB:
                while body.read(media.CHUNK):
                    pass
            if body.left:
                self.close_connection = True
            self._json(e.status, {"error": e.message})
        except Exception:
            self.close_connection = True  # corpo letto a metà: la connessione non è più riutilizzabile
            raise
        finally:
            con.close()

    def _job_file(self, job_id):
        """POST /api/launcher/jobs/{id}/file: il computer consegna il file prodotto da un lavoro. Stesse regole dei caricamenti."""
        try:
            length = int(self.headers.get("Content-Length") or -1)
        except ValueError:
            length = -1
        body = media.Body(self.rfile, max(length, 0))
        con = db.connect()
        try:
            device, tenant = local.device_from_token(con, self._bearer())
            local._mine(con, device, job_id)
            if length <= 0:
                raise ApiError(411, "Manca la dimensione del caricamento (Content-Length).")
            asset = media.upload(con, tenant, {"id": device["paired_by"]}, body, self.headers.get("Content-Type"))
            local.attach(con, device, job_id, asset["id"])
            self._json(200, {"asset_id": asset["id"]})
        except ApiError as e:
            if body.left:
                self.close_connection = True
            self._json(e.status, {"error": e.message})
        except Exception:
            self.close_connection = True
            raise
        finally:
            con.close()

    def _file_content(self, asset_id, query):
        """GET /api/files/{id}/content: file servito a pezzi con un collegamento firmato; supporta Range per i video."""
        con = db.connect()
        try:
            row, path = media.resolve_link(con, asset_id, (query.get("e") or [""])[0], (query.get("s") or [""])[0])
        finally:
            con.close()
        size = row["size_bytes"]
        start, end, status = 0, size - 1, 200
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start, end = int(m.group(1)), int(m.group(2)) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            end = min(end, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", row["mime"])
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.send_header("Content-Disposition", "inline; filename*=UTF-8''" + urllib.parse.quote(row["original_name"]))
        self.send_header("Cache-Control", "private, max-age=600")
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        left = end - start + 1
        with open(str(path), "rb") as f:
            f.seek(start)
            while left > 0:
                data = f.read(min(media.CHUNK, left))
                if not data:
                    break
                self.wfile.write(data)
                left -= len(data)

    def _send(self, status, body, ctype, extra=None):
        self.send_response(status)
        for k, v in extra or []:
            self.send_header(k, v)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, obj):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _dispatch(self, method):
        try:
            parts = urllib.parse.urlsplit(self.path)
            if parts.path == "/api/files/upload" and method == "POST":
                return self._upload()
            job_file = re.fullmatch(r"/api/launcher/jobs/(\d+)/file", parts.path)
            if job_file and method == "POST":
                return self._job_file(int(job_file.group(1)))
            if parts.path == "/api/launcher/download" and method == "GET":
                return self._send(200, local.launcher_zip(), "application/zip",
                                  [("Content-Disposition", 'attachment; filename="alvea-launcher.zip"')])
            content = re.fullmatch(r"/api/files/(\d+)/content", parts.path)
            if content and method == "GET":
                return self._file_content(int(content.group(1)), urllib.parse.parse_qs(parts.query))
            if not parts.path.startswith("/api/"):
                entry = STATIC.get(parts.path) if method == "GET" else None
                if not entry:
                    return self._json(404, {"error": "Pagina non trovata."})
                return self._send(200, (config.WEB_DIR / entry[0]).read_bytes(), entry[1])
            body = {}
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                # Se il corpo è poco oltre il limite lo si legge e lo si butta, così chi l'ha mandato riceve la risposta
                # invece di una connessione interrotta. Oltre, si risponde e si chiude: quel che resta non viene mai interpretato.
                if length <= 8 * MAX_BODY:
                    left = length
                    while left > 0:
                        chunk = self.rfile.read(min(left, 65536))
                        if not chunk:
                            break
                        left -= len(chunk)
                self.close_connection = True
                return self._json(413, {"error": "Richiesta troppo grande."})
            if length:
                try:
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                except ValueError:
                    return self._json(400, {"error": "Il corpo della richiesta non è JSON valido."})
                if not isinstance(body, dict):
                    return self._json(400, {"error": "Il corpo della richiesta deve essere un oggetto JSON."})
            token = self._bearer()
            cookies = {}
            for piece in (self.headers.get("Cookie") or "").split(";"):
                if "=" in piece:
                    k, v = piece.split("=", 1)
                    cookies[k.strip()] = v.strip()
            result = handle_api(method, parts.path, urllib.parse.parse_qs(parts.query), body, token, cookies, self._ip())
            if isinstance(result, Raw):
                payload = json.dumps(result.obj, ensure_ascii=False).encode("utf-8") if result.obj is not None else b""
                self._send(result.status, payload, "application/json; charset=utf-8", result.headers)
            else:
                self._json(200, result)
        except ApiError as e:
            self._json(e.status, {"error": e.message})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            try:
                self._json(500, {"error": "Errore interno del server. Il dettaglio è nel terminale del server."})
            except Exception:
                pass

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")


def make_server(host, port):
    """Prepara database, catalogo e, in demo, il modello finto. Ritorna il server pronto."""
    catalog.load()
    con = db.connect()
    db.init(con)
    con.close()
    if config.demo_mode() and not llm.DEMO_BASE:
        llm.DEMO_SERVER, llm.DEMO_BASE = fake_llm.start()
    local.ensure_signed()
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def main():
    config.load_env()
    host, port = config.get("SFB_HOST", "127.0.0.1"), config.get_int("SFB_PORT", 8787)
    server = make_server(host, port)
    con = db.connect()
    has_owner = con.execute("SELECT 1 FROM users WHERE role='superadmin'").fetchone()
    con.close()
    print("Alvea.ai in ascolto su http://%s:%d" % (host, port))
    if not has_owner:
        print("ATTENZIONE: nessun gestore creato. Esegui prima: python3 manage.py init")
    if config.demo_mode():
        print("MODALITÀ DEMO: risponde un modello finto a regole fisse. Per i modelli veri: SFB_DEMO=0 e le chiavi nel file .env")
    if not connectors.encryption_active():
        print("ATTENZIONE: le credenziali dei collegamenti sono salvate in chiaro (manca SFB_SECRET_KEY o il pacchetto cryptography).")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("ATTENZIONE: il server non cifra il traffico. Davanti va messo un proxy HTTPS prima di esporlo in rete.")
    if local.start():
        print("Lavori sul server attivi (montaggio video: %s)." % ("sì" if local.cloud_video_ready() else "no, manca FFmpeg"))
    if not local.public_key():
        print("ATTENZIONE: pacchetti locali non firmati (manca openssl): i computer dei clienti non potranno installarli.")
    if scheduler.start():
        print("Lavori automatici attivi (controllo ogni %d secondi, fuso orario %s)." % (
            max(2, config.get_int("SFB_SCHEDULER_POLL", 20)), config.get("SFB_TZ", "Europe/Rome")))
    if not google.configured():
        print("Accesso Google non configurato: mancano GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET nel file .env.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        scheduler.stop()
        print("\nServer fermato.")


if __name__ == "__main__":
    main()
