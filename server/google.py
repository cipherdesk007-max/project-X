"""Accesso Google con OAuth 2.0 (codice di autorizzazione + PKCE) e chiamate a Gmail, Calendar e Fogli.

Come funziona:
1. Un amministratore chiede il collegamento: il server crea uno `state` monouso (10 minuti), un
   verificatore PKCE e un segno legato al suo browser (cookie), e restituisce l'indirizzo di Google.
2. Google rimanda il browser a /api/google/callback con un codice. Il server controlla state e cookie,
   scambia il codice con i token e li salva tra i collegamenti dell'azienda (cifrati).
3. Il token di accesso dura circa un'ora: viene rinnovato da solo con il token di rinnovo.

Le credenziali dell'applicazione (GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET) sono della piattaforma e stanno nel .env.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import urllib.parse
from datetime import timedelta
from email.message import EmailMessage

from . import clock, config, connectors
from .connectors import ConnectorError

NAME = "google"
STATE_SECONDS = 600
SCOPE_PREFIX = "https://www.googleapis.com/auth/"
SERVICES = {
    "gmail_lettura": {"label": "Gmail: leggere la posta", "scope": SCOPE_PREFIX + "gmail.readonly",
                      "note": "Permesso che Google classifica «con restrizioni»: per usarlo con i clienti serve una verifica di sicurezza."},
    "gmail_invio": {"label": "Gmail: inviare (dopo approvazione)", "scope": SCOPE_PREFIX + "gmail.send", "note": ""},
    "calendario": {"label": "Calendar: leggere e creare eventi", "scope": SCOPE_PREFIX + "calendar.events", "note": ""},
    "fogli": {"label": "Fogli: leggere", "scope": SCOPE_PREFIX + "spreadsheets.readonly", "note": ""},
}


def _auth_url():
    return config.get("GOOGLE_AUTH_URL", "https://accounts.google.com/o/oauth2/v2/auth")


def _token_url():
    return config.get("GOOGLE_TOKEN_URL", "https://oauth2.googleapis.com/token")


def _revoke_url():
    return config.get("GOOGLE_REVOKE_URL", "https://oauth2.googleapis.com/revoke")


def _gmail():
    return config.get("GOOGLE_GMAIL_BASE", "https://gmail.googleapis.com").rstrip("/")


def _calendar():
    return config.get("GOOGLE_CALENDAR_BASE", "https://www.googleapis.com").rstrip("/")


def _sheets():
    return config.get("GOOGLE_SHEETS_BASE", "https://sheets.googleapis.com").rstrip("/")


def configured():
    return bool(config.get("GOOGLE_CLIENT_ID") and config.get("GOOGLE_CLIENT_SECRET"))


def public_url():
    return config.get("SFB_PUBLIC_URL", "http://127.0.0.1:%d" % config.get_int("SFB_PORT", 8787)).rstrip("/")


def redirect_uri():
    return public_url() + "/api/google/callback"


def storage_ok():
    """I token danno accesso alla posta: di norma si salvano solo cifrati."""
    return connectors.encryption_active() or config.get("SFB_ALLOW_PLAINTEXT_TOKENS") == "1"


def _sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ---------- collegamento ----------

def start(con, tenant_id, user_id, services):
    """Prepara il collegamento. Ritorna (indirizzo di Google, segno per il cookie del browser)."""
    if not configured():
        raise ConnectorError("L'accesso Google non è configurato sulla piattaforma: il gestore inserisce ID client e segreto nel pannello, "
                             "Impostazioni della piattaforma, gruppo «Google» (vedi LEGGIMI).")
    if not storage_ok():
        raise ConnectorError("Per collegare Google le credenziali devono essere cifrate: installa il pacchetto "
                             "'cryptography' e imposta SFB_SECRET_KEY (vedi LEGGIMI).")
    wanted = [s for s in SERVICES if s in (services or [])]
    if not wanted:
        raise ConnectorError("Scegli almeno un servizio Google da collegare.")
    state, verifier, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(64), secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    scopes = ["openid", "email"] + [SERVICES[s]["scope"] for s in wanted]
    con.execute("DELETE FROM oauth_states WHERE expires_at<? OR (tenant_id=? AND user_id=?)", (int(time.time()), tenant_id, user_id))
    con.execute("INSERT INTO oauth_states(state_hash,tenant_id,user_id,verifier,browser_hash,scopes,expires_at) VALUES(?,?,?,?,?,?,?)",
                (_sha(state), tenant_id, user_id, verifier, _sha(browser), " ".join(scopes), int(time.time()) + STATE_SECONDS))
    query = urllib.parse.urlencode({
        "client_id": config.get("GOOGLE_CLIENT_ID"), "redirect_uri": redirect_uri(), "response_type": "code",
        "scope": " ".join(scopes), "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    return _auth_url() + "?" + query, browser


def _email_from_id_token(id_token):
    """Legge l'indirizzo dal token di identità. Arriva da Google su canale cifrato, quindi non serve verificarne la firma."""
    try:
        payload = id_token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode("utf-8"))
        return str(data.get("email") or "")[:200]
    except Exception:
        return ""


def finish(con, state, code, error, browser):
    """Conclude il collegamento. Ritorna (esito, tenant_id, user_id): esito 'ok' oppure un codice di errore."""
    row = con.execute("SELECT * FROM oauth_states WHERE state_hash=?", (_sha(state or ""),)).fetchone()
    if not row:
        return "stato", None, None
    con.execute("DELETE FROM oauth_states WHERE state_hash=?", (row["state_hash"],))  # monouso
    tenant_id, user_id = row["tenant_id"], row["user_id"]
    if row["expires_at"] < time.time():
        return "scaduto", tenant_id, user_id
    if not browser or not hmac.compare_digest(_sha(browser), row["browser_hash"]):
        return "browser", tenant_id, user_id
    if error or not code:
        return "negato", tenant_id, user_id
    try:
        res = connectors.http_json("POST", _token_url(), form={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(),
            "client_id": config.get("GOOGLE_CLIENT_ID"), "client_secret": config.get("GOOGLE_CLIENT_SECRET"),
            "code_verifier": row["verifier"]})
    except ConnectorError:
        return "scambio", tenant_id, user_id
    if not res.get("access_token") or not res.get("refresh_token"):
        return "rinnovo", tenant_id, user_id
    granted = set(str(res.get("scope") or row["scopes"]).split())
    services = [s for s, spec in SERVICES.items() if spec["scope"] in granted]
    if not services:
        return "permessi", tenant_id, user_id
    try:
        connectors.store_raw(con, tenant_id, NAME, {
            "access_token": res["access_token"], "refresh_token": res["refresh_token"],
            "expires_at": int(time.time()) + int(res.get("expires_in") or 3600), "services": services,
            "email": _email_from_id_token(res.get("id_token") or ""), "broken": ""})
    except ConnectorError:
        return "cifratura", tenant_id, user_id
    return "ok", tenant_id, user_id


def _load(con, tenant_id):
    try:
        return connectors.load(con, tenant_id, NAME)
    except ConnectorError:
        return {}


def status(con, tenant_id):
    cfg = _load(con, tenant_id)
    return {"configured": configured(), "storage_ok": storage_ok(), "redirect_uri": redirect_uri(),
            "connected": bool(cfg.get("refresh_token")), "email": cfg.get("email", ""), "broken": cfg.get("broken", ""),
            "granted": cfg.get("services", []),
            "services": [{"id": k, "label": v["label"], "note": v["note"]} for k, v in SERVICES.items()]}


def has(con, tenant_id, service):
    cfg = _load(con, tenant_id)
    return bool(cfg.get("refresh_token")) and not cfg.get("broken") and service in cfg.get("services", [])


def disconnect(con, tenant_id):
    cfg = _load(con, tenant_id)
    if cfg.get("refresh_token"):
        try:  # revoca presso Google; se non riesce, i token vengono comunque cancellati qui
            connectors.http_json("POST", _revoke_url(), form={"token": cfg["refresh_token"]}, timeout=10)
        except ConnectorError:
            pass
    connectors.delete(con, tenant_id, NAME)


def _token(con, tenant_id, force=False):
    cfg = connectors.load(con, tenant_id, NAME)
    if not cfg.get("refresh_token"):
        raise ConnectorError("L'account Google non è collegato. Un amministratore lo collega nel Centro di controllo, scheda Collegamenti.")
    if cfg.get("broken"):
        raise ConnectorError(cfg["broken"])
    if not force and cfg.get("expires_at", 0) - 60 > time.time():
        return cfg["access_token"]
    try:
        res = connectors.http_json("POST", _token_url(), form={
            "grant_type": "refresh_token", "refresh_token": cfg["refresh_token"],
            "client_id": config.get("GOOGLE_CLIENT_ID"), "client_secret": config.get("GOOGLE_CLIENT_SECRET")})
    except ConnectorError as e:
        if e.status in (400, 401) and "invalid_grant" in (e.detail or ""):
            cfg["broken"] = ("Il collegamento Google non è più valido (revocato o scaduto): un amministratore deve ricollegarlo. "
                             "Se l'applicazione Google è in stato «Test», i permessi scadono dopo 7 giorni.")
            connectors.store_raw(con, tenant_id, NAME, cfg)
            raise ConnectorError(cfg["broken"])
        raise
    if not res.get("access_token"):
        raise ConnectorError("Google non ha rinnovato l'accesso.")
    cfg["access_token"] = res["access_token"]
    cfg["expires_at"] = int(time.time()) + int(res.get("expires_in") or 3600)
    connectors.store_raw(con, tenant_id, NAME, cfg)
    return cfg["access_token"]


def _need(con, tenant_id, service):
    if not has(con, tenant_id, service):
        cfg = _load(con, tenant_id)
        if cfg.get("broken"):
            raise ConnectorError(cfg["broken"])
        raise ConnectorError("Manca il permesso Google «%s». Un amministratore lo aggiunge ricollegando Google "
                             "nei Collegamenti." % SERVICES[service]["label"])


def _api(con, tenant_id, method, url, body=None):
    try:
        return connectors.http_json(method, url, headers={"Authorization": "Bearer " + _token(con, tenant_id)}, body=body)
    except ConnectorError as e:
        if e.status != 401:
            raise
    return connectors.http_json(method, url, headers={"Authorization": "Bearer " + _token(con, tenant_id, force=True)}, body=body)


# ---------- Gmail ----------

def gmail_recent(con, tenant_id, count=5, unseen_only=False):
    _need(con, tenant_id, "gmail_lettura")
    q = urllib.parse.urlencode({"maxResults": count, "q": "in:inbox is:unread" if unseen_only else "in:inbox"})
    listing = _api(con, tenant_id, "GET", "%s/gmail/v1/users/me/messages?%s" % (_gmail(), q))
    out = []
    for item in (listing.get("messages") or [])[:count]:
        mid = str(item.get("id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", mid):
            continue
        msg = _api(con, tenant_id, "GET", "%s/gmail/v1/users/me/messages/%s?format=metadata&metadataHeaders=From"
                                          "&metadataHeaders=Subject&metadataHeaders=Date" % (_gmail(), mid))
        headers = {h.get("name", "").lower(): h.get("value", "") for h in ((msg.get("payload") or {}).get("headers") or [])}
        out.append("Da: %s\nOggetto: %s\nData: %s\nTesto: %s" % (
            headers.get("from", "?"), headers.get("subject", "(senza oggetto)"), headers.get("date", "?"),
            connectors.html_to_text(str(msg.get("snippet") or ""))[:700]))
    return out


def gmail_send(con, tenant_id, to, subject, text):
    _need(con, tenant_id, "gmail_invio")
    connectors.check_recipient(to, subject)
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject[:200]
    msg.set_content(text)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    _api(con, tenant_id, "POST", "%s/gmail/v1/users/me/messages/send" % _gmail(), body={"raw": raw})
    return "Email inviata a %s con Gmail." % to


# ---------- Calendar ----------

def calendar_list(con, tenant_id, start_iso, end_iso):
    _need(con, tenant_id, "calendario")
    q = urllib.parse.urlencode({"timeMin": clock.aware(start_iso).isoformat(), "timeMax": clock.aware(end_iso).isoformat(),
                                "singleEvents": "true", "orderBy": "startTime", "maxResults": 50})
    res = _api(con, tenant_id, "GET", "%s/calendar/v3/calendars/primary/events?%s" % (_calendar(), q))
    out = []
    for e in res.get("items") or []:
        start = (e.get("start") or {}).get("dateTime") or (e.get("start") or {}).get("date") or "?"
        out.append("%s | %s | %s" % (str(start)[:16], str(e.get("summary") or "(senza titolo)")[:150], str(e.get("location") or "-")[:100]))
    return out


def calendar_create(con, tenant_id, title, start_iso, end_iso=None, location="", notes=""):
    _need(con, tenant_id, "calendario")
    start = clock.aware(start_iso)
    end = clock.aware(end_iso) if end_iso else start + timedelta(hours=1)
    if end <= start:
        raise ConnectorError("La fine dell'evento deve essere dopo l'inizio.")
    body = {"summary": title, "start": {"dateTime": start.strftime("%Y-%m-%dT%H:%M:00"), "timeZone": clock.tz_name()},
            "end": {"dateTime": end.strftime("%Y-%m-%dT%H:%M:00"), "timeZone": clock.tz_name()}}
    if location:
        body["location"] = location
    if notes:
        body["description"] = notes
    _api(con, tenant_id, "POST", "%s/calendar/v3/calendars/primary/events" % _calendar(), body=body)
    return "Evento «%s» creato su Google Calendar per %s." % (title, start.strftime("%Y-%m-%d %H:%M"))


# ---------- Fogli ----------

def sheets_read(con, tenant_id, id_or_url, cells="A1:Z200"):
    _need(con, tenant_id, "fogli")
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})", id_or_url) or re.fullmatch(r"([A-Za-z0-9_-]{20,})", id_or_url.strip())
    if not m:
        raise ConnectorError("Indica l'indirizzo del Foglio Google o il suo identificativo.")
    cells = cells or "A1:Z200"
    if not re.fullmatch(r"[\w !:'$.\-àèéìòù]{1,100}", cells):
        raise ConnectorError("Intervallo di celle non valido (es. Foglio1!A1:D50).")
    res = _api(con, tenant_id, "GET", "%s/v4/spreadsheets/%s/values/%s" % (_sheets(), m.group(1), urllib.parse.quote(cells, safe="")))
    rows = res.get("values") or []
    lines = [" | ".join(str(c)[:200] for c in row) for row in rows[:200]]
    return lines, len(rows)
