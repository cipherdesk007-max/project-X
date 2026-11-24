"""Collegamenti verso servizi esterni: credenziali per azienda e chiamate di rete.

Le credenziali sono cifrate (Fernet) se SFB_SECRET_KEY è impostata e il pacchetto
`cryptography` è installato; altrimenti restano in chiaro nel database e la schermata lo segnala.
"""
from __future__ import annotations

import email as email_lib
import email.policy
import html
import imaplib
import http.client
import ipaddress
import json
import re
import smtplib
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr

from . import config, db


class ConnectorError(Exception):
    status = None
    detail = ""


# nome -> etichetta, agente che lo usa (None = per tutti), campi (chiave, etichetta, segreto), obbligatori
DEFS = {
    "email": {
        "label": "Casella email (IMAP e SMTP)", "agent": "email",
        "fields": [("imap_host", "Server IMAP", False), ("imap_port", "Porta IMAP (di solito 993)", False),
                   ("smtp_host", "Server SMTP", False), ("smtp_port", "Porta SMTP (465 oppure 587)", False),
                   ("username", "Indirizzo o nome utente", False),
                   ("password", "Password o password per app", True),
                   ("from_name", "Nome del mittente", False)],
        "required": ["imap_host", "smtp_host", "username", "password"],
    },
    "telegram": {
        "label": "Bot Telegram", "agent": "telegram",
        "fields": [("bot_token", "Token del bot", True), ("default_chat_id", "Chat predefinita (id)", False)],
        "required": ["bot_token"],
    },
    "shopify": {
        "label": "Negozio Shopify", "agent": "shopify",
        "fields": [("shop_domain", "Dominio (nome.myshopify.com)", False),
                   ("access_token", "Token di accesso Admin API", True),
                   ("api_version", "Versione API (es. 2026-10)", False)],
        "required": ["shop_domain", "access_token", "api_version"],
    },
    "whatsapp": {
        "label": "WhatsApp Business (Cloud API)", "agent": "whatsapp",
        "fields": [("phone_number_id", "ID del numero", False), ("access_token", "Token di accesso", True),
                   ("api_version", "Versione Graph API (vuoto = v26.0)", False)],
        "required": ["phone_number_id", "access_token"],
    },
    "ricerca": {
        "label": "Ricerca web (Brave Search API)", "agent": "ricerca",
        "fields": [("brave_api_key", "Chiave API Brave Search", True)],
        "required": ["brave_api_key"],
    },
    "modelli": {
        "label": "Chiavi API dei modelli dell'azienda (facoltative)", "agent": None,
        "fields": [("anthropic_api_key", "Chiave Claude (Anthropic)", True),
                   ("moonshot_api_key", "Chiave Kimi (Moonshot)", True),
                   ("openai_api_key", "Chiave ChatGPT (OpenAI)", True)],
        "required": [],
    },
}


# ---------- archivio credenziali ----------

def _fernet():
    """None se la cifratura non è richiesta. Errore se è richiesta ma non si può fare:
    meglio rifiutare il salvataggio che scrivere un segreto in chiaro senza dirlo."""
    key = config.get("SFB_SECRET_KEY")
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode("ascii"))
    except BaseException as e:  # alcune installazioni rotte di cryptography non alzano ImportError
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        raise ConnectorError("SFB_SECRET_KEY è impostata ma la cifratura non funziona: installa il pacchetto "
                             "'cryptography' (pip3 install cryptography) o controlla la chiave.")


def encryption_active():
    try:
        return _fernet() is not None
    except ConnectorError:
        return False


def load(con, tenant_id, name):
    row = con.execute("SELECT config, encrypted FROM connectors WHERE tenant_id=? AND name=?",
                      (tenant_id, name)).fetchone()
    if not row:
        return {}
    blob = row["config"]
    if row["encrypted"]:
        f = _fernet()
        if not f:
            raise ConnectorError("Credenziali cifrate ma SFB_SECRET_KEY manca.")
        try:
            blob = f.decrypt(blob.encode("ascii")).decode("utf-8")
        except Exception:
            raise ConnectorError("Credenziali non decifrabili: SFB_SECRET_KEY è cambiata.")
    return json.loads(blob)


def save(con, tenant_id, name, values):
    spec = DEFS[name]
    cfg = load(con, tenant_id, name)
    for key, _label, secret in spec["fields"]:
        if key not in values:
            continue
        v = str(values[key] or "").strip()
        if len(v) > 2000 or "\n" in v or "\r" in v:
            raise ConnectorError("Valore non valido per %s." % key)
        if v == "":
            if not secret:
                cfg.pop(key, None)
            continue  # un segreto lasciato vuoto resta quello salvato
        cfg[key] = v
    check_mail_hosts(name, cfg)
    blob = json.dumps(cfg, ensure_ascii=False)
    f = _fernet()
    stored, enc = (f.encrypt(blob.encode("utf-8")).decode("ascii"), 1) if f else (blob, 0)
    con.execute(
        "INSERT INTO connectors(tenant_id,name,config,encrypted,updated_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(tenant_id,name) DO UPDATE SET config=excluded.config, encrypted=excluded.encrypted, "
        "updated_at=excluded.updated_at",
        (tenant_id, name, stored, enc, db.now()),
    )


def store_raw(con, tenant_id, name, cfg):
    """Salva un dizionario così com'è (cifrato se la chiave è impostata). Usato dall'accesso Google."""
    blob = json.dumps(cfg, ensure_ascii=False)
    f = _fernet()
    stored, enc = (f.encrypt(blob.encode("utf-8")).decode("ascii"), 1) if f else (blob, 0)
    con.execute(
        "INSERT INTO connectors(tenant_id,name,config,encrypted,updated_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(tenant_id,name) DO UPDATE SET config=excluded.config, encrypted=excluded.encrypted, "
        "updated_at=excluded.updated_at",
        (tenant_id, name, stored, enc, db.now()),
    )


def delete(con, tenant_id, name):
    con.execute("DELETE FROM connectors WHERE tenant_id=? AND name=?", (tenant_id, name))


def public_state(con, tenant_id, entitled):
    """Stato per la schermata. I segreti non escono mai: solo 'impostato' sì/no."""
    out = []
    for name, spec in DEFS.items():
        available = spec["agent"] is None or spec["agent"] in entitled
        try:
            cfg = load(con, tenant_id, name) if available else {}
            problem = None
        except ConnectorError as e:
            cfg, problem = {}, str(e)
        fields = []
        for key, label, secret in spec["fields"]:
            fields.append({"key": key, "label": label, "secret": secret, "set": bool(cfg.get(key)),
                           "value": "" if secret else cfg.get(key, "")})
        ready = available and all(cfg.get(k) for k in spec["required"]) and (bool(cfg) or not spec["required"])
        out.append({"name": name, "label": spec["label"], "agent": spec["agent"], "available": available,
                    "ready": bool(ready), "problem": problem, "fields": fields})
    return out


def require(con, tenant_id, name):
    cfg = load(con, tenant_id, name)
    missing = [k for k in DEFS[name]["required"] if not cfg.get(k)]
    if missing:
        raise ConnectorError("Il collegamento «%s» non è configurato. Un amministratore lo imposta nel "
                             "Centro di controllo, scheda Collegamenti." % DEFS[name]["label"])
    return cfg


# ---------- rete ----------

def http_json(method, url, headers=None, body=None, timeout=30, form=None):
    """Chiamata HTTP con risposta JSON. `body` viene mandato come JSON, `form` come modulo.
    In caso di errore alza ConnectorError con .status (codice HTTP o None) e .detail (testo della risposta)."""
    data = None
    hdrs = {"Accept": "application/json", "User-Agent": "Alvea/1.0"}
    if form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(2000000)
        return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read(600).decode("utf-8", "replace")
        err = ConnectorError("Il servizio ha risposto %d: %s" % (e.code, detail[:300]))
        err.status, err.detail = e.code, detail
        raise err
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        raise ConnectorError("Servizio non raggiungibile: %s" % getattr(e, "reason", e))
    except ValueError:
        raise ConnectorError("Il servizio ha dato una risposta non leggibile.")


def check_mail_hosts(name, cfg):
    """I server di posta li scrive l'azienda: devono essere nomi veri su Internet, non indirizzi interni di questo server
    o della sua rete (altrimenti il collegamento di posta diventerebbe un modo per esplorarla)."""
    if name != "email":
        return
    for key in ("imap_host", "smtp_host"):
        host = str(cfg.get(key) or "")
        if host:
            if not re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", host):
                raise ConnectorError("«%s» non è il nome di un server di posta (per esempio imap.tuodominio.it)." % host[:80])
            try:
                _public_ip(host)
            except ConnectorError as e:
                raise ConnectorError("Server di posta «%s»: %s" % (host[:80], e))
    for key in ("imap_port", "smtp_port"):
        port = str(cfg.get(key) or "")
        if port and not (port.isdigit() and 0 < int(port) < 65536):
            raise ConnectorError("La porta «%s» non è valida." % port[:10])


def _public_ip(host):
    """Risolve il nome una volta sola e ritorna un indirizzo pubblico. Rifiuta reti interne e riservate."""
    if config.get("SFB_ALLOW_PRIVATE_HOSTS") == "1":  # solo per provare in locale: mai in produzione
        return host
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise ConnectorError("Indirizzo non trovato.")
    ips = [ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos]
    if not ips or any(not ip.is_global for ip in ips):
        raise ConnectorError("Indirizzo non consentito: rete interna o riservata.")
    return str(ips[0])


def _check_public_host(host):
    _public_ip(host)


class _PinnedHTTP(http.client.HTTPConnection):
    """Si collega all'indirizzo già controllato. Senza questo il nome verrebbe risolto una seconda volta al momento del
    collegamento, e chi controlla il DNS potrebbe rispondere prima con un indirizzo pubblico e poi con uno interno."""
    pinned = None

    def connect(self):
        self.sock = socket.create_connection((self.pinned, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    pinned = None

    def connect(self):
        sock = socket.create_connection((self.pinned, self.port), self.timeout)
        self.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)


def fetch_page(url, max_bytes=1000000):
    """Scarica una pagina pubblica. Rifiuta reti interne e ricontrolla ogni reindirizzamento."""
    for _ in range(4):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            raise ConnectorError("Sono ammessi solo indirizzi http o https.")
        try:
            port = parts.port
        except ValueError:
            raise ConnectorError("Indirizzo non valido.")
        pinned = _public_ip(parts.hostname)  # prima il controllo, poi il collegamento
        try:
            conn = (_PinnedHTTPS if parts.scheme == "https" else _PinnedHTTP)("[%s]" % parts.hostname if ":" in parts.hostname else parts.hostname, port, timeout=20)
        except http.client.InvalidURL:
            raise ConnectorError("Indirizzo non valido.")
        conn.pinned = pinned
        try:
            conn.request("GET", (parts.path or "/") + ("?" + parts.query if parts.query else ""),
                         headers={"User-Agent": "Alvea/1.0", "Accept": "text/html,text/plain"})
            r = conn.getresponse()
            location = r.getheader("Location")
            if r.status in (301, 302, 303, 307, 308) and location:
                url = urllib.parse.urljoin(url, location)
                continue
            if r.status >= 400:
                raise ConnectorError("La pagina ha risposto %d." % r.status)
            charset = r.headers.get_content_charset() or "utf-8"
            raw = r.read(max_bytes)
            try:
                return raw.decode(charset, "replace")
            except LookupError:
                return raw.decode("utf-8", "replace")
        except (http.client.HTTPException, socket.timeout, OSError) as e:
            raise ConnectorError("Pagina non raggiungibile: %s" % getattr(e, "reason", e))
        finally:
            conn.close()
    raise ConnectorError("Troppi reindirizzamenti.")


def html_to_text(page):
    page = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", page)
    page = re.sub(r"(?s)<[^>]+>", " ", page)
    return re.sub(r"\s+", " ", html.unescape(page)).strip()


# ---------- servizi ----------

def telegram_send(cfg, text, chat_id=None):
    chat = str(chat_id or cfg.get("default_chat_id") or "").strip()
    if not chat:
        raise ConnectorError("Manca la chat di destinazione: indicala o imposta la chat predefinita.")
    base = config.get("TELEGRAM_API_BASE", "https://api.telegram.org")
    res = http_json("POST", "%s/bot%s/sendMessage" % (base, cfg["bot_token"]), body={"chat_id": chat, "text": text})
    if not res.get("ok"):
        raise ConnectorError("Telegram ha rifiutato il messaggio.")
    return "Messaggio Telegram inviato alla chat %s." % chat


def telegram_updates(cfg, limit=10):
    base = config.get("TELEGRAM_API_BASE", "https://api.telegram.org")
    res = http_json("GET", "%s/bot%s/getUpdates?limit=%d" % (base, cfg["bot_token"], limit))
    out = []
    for u in res.get("result", [])[-limit:]:
        m = u.get("message") or {}
        if m.get("text"):
            who = (m.get("from") or {}).get("first_name", "?")
            out.append("%s (chat %s): %s" % (who, (m.get("chat") or {}).get("id", "?"), m["text"][:500]))
    return out


def shopify_graphql(cfg, query):
    domain = cfg["shop_domain"].replace("https://", "").strip("/")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", domain):
        raise ConnectorError("Il dominio Shopify deve essere del tipo nome.myshopify.com.")
    if not re.fullmatch(r"\d{4}-\d{2}", cfg["api_version"]):
        raise ConnectorError("Versione API Shopify non valida (es. 2026-10).")
    res = http_json("POST", "https://%s/admin/api/%s/graphql.json" % (domain, cfg["api_version"]),
                    headers={"X-Shopify-Access-Token": cfg["access_token"]}, body={"query": query})
    if res.get("errors"):
        raise ConnectorError("Shopify ha risposto con un errore: %s" % json.dumps(res["errors"])[:300])
    return res.get("data") or {}


def whatsapp_send(cfg, number, text):
    number = re.sub(r"[^\d]", "", str(number))
    if not 8 <= len(number) <= 15:
        raise ConnectorError("Numero non valido: serve il formato internazionale, es. 393331234567.")
    version = cfg.get("api_version") or "v26.0"
    if not re.fullmatch(r"v\d{1,3}\.\d", version) or not re.fullmatch(r"\d+", cfg["phone_number_id"]):
        raise ConnectorError("Versione Graph API o ID del numero non validi.")
    base = config.get("WHATSAPP_API_BASE", "https://graph.facebook.com")
    http_json("POST", "%s/%s/%s/messages" % (base, version, cfg["phone_number_id"]),
              headers={"Authorization": "Bearer " + cfg["access_token"]},
              body={"messaging_product": "whatsapp", "recipient_type": "individual", "to": number,
                    "type": "text", "text": {"preview_url": False, "body": text[:4096]}})
    return "Messaggio WhatsApp accettato per il numero %s." % number


def brave_search(api_key, question, count=5):
    base = config.get("BRAVE_API_BASE", "https://api.search.brave.com")
    url = "%s/res/v1/web/search?%s" % (base, urllib.parse.urlencode({"q": question, "count": count}))
    res = http_json("GET", url, headers={"X-Subscription-Token": api_key})
    out = []
    for r in ((res.get("web") or {}).get("results") or [])[:count]:
        out.append({"title": r.get("title", ""), "url": r.get("url", ""), "description": html_to_text(r.get("description", ""))})
    return out


def searxng_search(base, question, count=5):
    """Ricerca su un'istanza SearXNG del gestore. L'istanza deve avere il formato JSON attivo (search.formats in settings.yml)."""
    url = "%s/search?%s" % (base.rstrip("/"), urllib.parse.urlencode({"q": question, "format": "json"}))
    try:
        res = http_json("GET", url)
    except ConnectorError as e:
        if getattr(e, "status", None) == 403:
            raise ConnectorError("SearXNG rifiuta le risposte JSON: va attivato «json» in search.formats nel suo settings.yml.")
        raise
    return [{"title": str(r.get("title") or ""), "url": str(r.get("url") or ""), "description": html_to_text(str(r.get("content") or ""))}
            for r in (res.get("results") or [])[:count]]


def imap_recent(cfg, count=5, unseen_only=False):
    try:
        box = imaplib.IMAP4_SSL(cfg["imap_host"], int(cfg.get("imap_port") or 993),
                                ssl_context=ssl.create_default_context(), timeout=30)
    except (OSError, ValueError) as e:
        raise ConnectorError("Server IMAP non raggiungibile: %s" % e)
    out = []
    try:
        box.login(cfg["username"], cfg["password"])
        box.select("INBOX", readonly=True)
        _typ, data = box.search(None, "UNSEEN" if unseen_only else "ALL")
        ids = data[0].split()[-count:] if data and data[0] else []
        for num in reversed(ids):
            _typ, parts = box.fetch(num, "(BODY.PEEK[]<0.20000>)")
            raw = parts[0][1] if parts and isinstance(parts[0], tuple) else b""
            msg = email_lib.message_from_bytes(raw, policy=email.policy.default)
            try:
                body = msg.get_body(preferencelist=("plain", "html"))
                text = body.get_content() if body else ""
                if body and body.get_content_type() == "text/html":
                    text = html_to_text(text)
            except Exception:
                text = ""
            out.append("Da: %s\nOggetto: %s\nData: %s\nTesto: %s" % (
                msg.get("From", "?"), msg.get("Subject", "(senza oggetto)"), msg.get("Date", "?"),
                re.sub(r"\s+", " ", text)[:700]))
    except imaplib.IMAP4.error as e:
        raise ConnectorError("La casella ha rifiutato l'accesso o la lettura: %s" % e)
    finally:
        try:
            box.logout()
        except Exception:
            pass
    return out


def check_recipient(to, subject):
    if not re.fullmatch(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+", to or ""):
        raise ConnectorError("Indirizzo del destinatario non valido (uno solo per messaggio).")
    if "\n" in subject or "\r" in subject:
        raise ConnectorError("Oggetto non valido.")


def smtp_send(cfg, to, subject, text):
    check_recipient(to, subject)
    msg = EmailMessage()
    msg["From"] = formataddr((cfg.get("from_name") or "", cfg["username"]))
    msg["To"] = to
    msg["Subject"] = subject[:200]
    msg.set_content(text)
    port = int(cfg.get("smtp_port") or 465)
    ctx = ssl.create_default_context()
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(cfg["smtp_host"], port, context=ctx, timeout=30)
        else:
            server = smtplib.SMTP(cfg["smtp_host"], port, timeout=30)
            server.starttls(context=ctx)
        try:
            server.login(cfg["username"], cfg["password"])
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:
                pass
    except (smtplib.SMTPException, OSError, ValueError) as e:
        raise ConnectorError("Invio email non riuscito: %s" % e)
    return "Email inviata a %s." % to
