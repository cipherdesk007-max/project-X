"""Registrazione autonoma di una nuova azienda (rotta pubblica) e verifica facoltativa dell'email.

Difese:
- limite di richieste per indirizzo di rete (in memoria) e tetto globale di aziende nuove all'ora (dal database,
  quindi vale anche dopo un riavvio e dietro un proxy che nasconde gli indirizzi);
- campo trappola per i programmi automatici;
- le aziende registrate da sole non usano le chiavi dei modelli della piattaforma, se non lo decide il gestore:
  altrimenti chiunque potrebbe spendere il credito del gestore;
- se nel .env c'è un server SMTP (SFB_SMTP_HOST), l'account resta bloccato finché l'email non è confermata.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time

from . import audit, auth, catalog, config, connectors, db, google, ops, team
from .errors import ApiError

TOKEN_SECONDS = 24 * 3600
_hits = {}
_lock = threading.Lock()


def enabled():
    return config.get("SFB_REGISTRATION", "aperta") == "aperta"


def package_id():
    pid = config.get("SFB_REGISTER_PACKAGE", "completo")
    return pid if catalog.package(pid) else catalog.package_ids()[-1]


def smtp_cfg():
    host = config.get("SFB_SMTP_HOST")
    if not host:
        return None
    return {"smtp_host": host, "smtp_port": config.get("SFB_SMTP_PORT", "465"), "username": config.get("SFB_SMTP_USER"),
            "password": config.get("SFB_SMTP_PASSWORD"), "from_name": config.get("SFB_SMTP_FROM_NAME", "SalesForB")}


def verification_on():
    return smtp_cfg() is not None


def send_mail(to, subject, text):
    """Punto unico di invio: le prove lo sostituiscono per leggere i messaggi senza un server di posta."""
    connectors.smtp_send(smtp_cfg(), to, subject, text)


def throttle(ip, what, per_hour):
    """Conta ogni tentativo, riuscito o no. Oltre il limite: 429."""
    now, key = time.time(), (what, ip or "?")
    with _lock:
        recent = [t for t in _hits.get(key, []) if t > now - 3600]
        if len(recent) >= per_hour:
            raise ApiError(429, "Troppe richieste da questo indirizzo. Riprova tra un'ora.")
        recent.append(now)
        _hits[key] = recent
        if len(_hits) > 5000:  # non crescere senza fine sotto attacco
            for k in [k for k, v in _hits.items() if not v or v[-1] < now - 3600]:
                _hits.pop(k, None)


def reset_throttle():
    with _lock:
        _hits.clear()


def public_info():
    """Quello che serve al modulo di registrazione, senza accesso."""
    top = catalog.entitled_agents(package_id())
    return {"open": enabled(), "verify_email": verification_on(),
            "agents": [{"id": a["id"], "name": a["name"], "does": a["does"]} for a in catalog.load()["agents"] if a["id"] in top]}


def _hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _send_verification(con, user_id, email):
    token = secrets.token_urlsafe(32)
    con.execute("DELETE FROM email_tokens WHERE user_id=? OR expires_at<?", (user_id, int(time.time())))
    con.execute("INSERT INTO email_tokens(token_hash,user_id,expires_at) VALUES(?,?,?)", (_hash(token), user_id, int(time.time()) + TOKEN_SECONDS))
    link = "%s/api/verify?token=%s" % (google.public_url(), token)
    try:
        send_mail(email, "Conferma il tuo indirizzo per SalesForB",
                  "Per attivare l'account apri questo collegamento entro 24 ore:\n\n%s\n\nSe non hai chiesto tu la registrazione, ignora questo messaggio." % link)
        return True
    except connectors.ConnectorError as e:
        print("ATTENZIONE: email di verifica non inviata: %s" % e)
        return False


def _release_stale(con, user):
    """Una registrazione mai confermata e con il collegamento scaduto non deve bloccare per sempre quell'indirizzo.
    Niente viene cancellato: l'utente e l'azienda vecchi restano come righe spente e l'indirizzo torna libero.
    (Cancellarli farebbe riusare i loro numeri a un'azienda nuova, che erediterebbe ciò che resta legato a quei numeri.)"""
    if user["email_verified"] or user["tenant_id"] is None:
        return False
    tenant = con.execute("SELECT * FROM tenants WHERE id=?", (user["tenant_id"],)).fetchone()
    live = con.execute("SELECT 1 FROM email_tokens WHERE user_id=? AND expires_at>=?", (user["id"], int(time.time()))).fetchone()
    others = con.execute("SELECT COUNT(*) FROM users WHERE tenant_id=?", (user["tenant_id"],)).fetchone()[0]
    if not tenant or tenant["origin"] != "registrazione" or live or others != 1:
        return False
    tag = "%d-%s" % (user["id"], secrets.token_hex(4))
    con.execute("DELETE FROM email_tokens WHERE user_id=?", (user["id"],))
    con.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
    con.execute("UPDATE users SET email=?, active=0 WHERE id=?", ("scaduta-%s@registrazione.invalid" % tag, user["id"]))
    con.execute("UPDATE tenants SET active=0, name=? WHERE id=?", ("%s [registrazione scaduta %s]" % (tenant["name"][:80], tag), tenant["id"]))
    audit.record(con, tenant["id"], None, "registrazione_scaduta", {})
    audit.record(con, ops.PLATFORM, None, "registrazione_scaduta", {"azienda": tenant["name"]})
    return True


def register(con, body, ip):
    if not enabled():
        raise ApiError(403, "La registrazione è chiusa: le aziende vengono create dal gestore.")
    throttle(ip, "registrazione", config.get_int("SFB_REGISTER_PER_IP_HOUR", 5))
    verify = verification_on()
    if str(body.get("website") or "").strip():  # campo trappola: un visitatore vero non lo vede e non lo compila
        return {"ok": True, "verify": True, "sent": True}
    pid = package_id()
    company = str(body.get("company") or "").strip()
    email = str(body.get("email") or "").strip().lower()
    agents = body.get("agents") if body.get("agents") is not None else []
    if not isinstance(agents, list) or any(not isinstance(a, str) for a in agents):
        raise ApiError(400, "L'elenco degli agenti non è valido.")
    top = catalog.entitled_agents(pid)
    for a in agents:
        if not catalog.agent(a):
            raise ApiError(400, "Agente sconosciuto: %s." % a[:40])
        if a not in top:
            raise ApiError(403, "L'Agente %s non è disponibile alla registrazione." % catalog.agent(a)["name"])
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
    recent = con.execute("SELECT COUNT(*) FROM tenants WHERE origin='registrazione' AND created_at>=?", (since,)).fetchone()[0]
    if recent >= config.get_int("SFB_REGISTER_PER_HOUR", 30):
        raise ApiError(429, "Troppe registrazioni in questo momento. Riprova più tardi.")
    existing = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if existing:
        _release_stale(con, dict(existing))
    name = company
    while name and con.execute("SELECT 1 FROM tenants WHERE name=?", (name,)).fetchone():
        name = "%s (%s)" % (company, secrets.token_hex(2))  # i nomi uguali sono ammessi: non si rivela chi è già cliente
    tenant_id = ops.create_tenant(con, name, pid, email, body.get("name"), body.get("password"), actor_id=None,
                                  origin="registrazione", platform_keys=1 if config.get("SFB_REGISTER_PLATFORM_KEYS") == "1" else 0,
                                  email_verified=0 if verify else 1, agents=set(agents))
    user = con.execute("SELECT * FROM users WHERE tenant_id=? ORDER BY id LIMIT 1", (tenant_id,)).fetchone()
    audit.record(con, tenant_id, user["id"], "registrazione", {"agenti": sorted(agents), "verifica_email": verify})
    if verify:
        return {"ok": True, "verify": True, "sent": _send_verification(con, user["id"], email)}
    return {"ok": True, "verify": False, "token": auth.create_session(con, user["id"])}


def verify(con, token):
    """Conferma l'indirizzo. Il collegamento vale una volta sola. Ritorna True se riuscita."""
    row = con.execute("SELECT * FROM email_tokens WHERE token_hash=?", (_hash(token or ""),)).fetchone()
    if not row:
        return False
    con.execute("DELETE FROM email_tokens WHERE token_hash=?", (row["token_hash"],))
    if row["expires_at"] < time.time():
        return False
    user = con.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
    if not user:
        return False
    con.execute("UPDATE users SET email_verified=1 WHERE id=?", (user["id"],))
    con.execute("DELETE FROM email_tokens WHERE user_id=?", (user["id"],))
    audit.record(con, user["tenant_id"] if user["tenant_id"] is not None else ops.PLATFORM, user["id"], "email_confermata", {})
    return True


def resend(con, body, ip):
    """Rimanda il collegamento. La risposta è sempre la stessa, così non si scopre quali indirizzi sono registrati."""
    throttle(ip, "rimanda", config.get_int("SFB_REGISTER_PER_IP_HOUR", 5))
    if verification_on():
        email = str(body.get("email") or "").strip().lower()
        user = con.execute("SELECT * FROM users WHERE email=? AND email_verified=0 AND active=1", (email,)).fetchone()
        if user:
            _send_verification(con, user["id"], email)
    return {"ok": True}
