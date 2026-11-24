"""Operazioni su aziende e utenti, usate sia dalle API sia da manage.py."""
from __future__ import annotations

import re
import sqlite3

from datetime import date, datetime, timezone

from . import audit, auth, catalog, config, db, news, team
from .errors import ApiError

EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
PLATFORM = 0  # tenant_id usato nel registro per gli eventi della piattaforma


def _clean_email(email):
    email = str(email or "").strip().lower()
    if not EMAIL_RE.fullmatch(email) or len(email) > 200:
        raise ApiError(400, "Indirizzo email non valido.")
    return email


def _clean_name(name, what="Il nome"):
    name = str(name or "").strip()
    if not 1 <= len(name) <= 120:
        raise ApiError(400, "%s è obbligatorio (massimo 120 caratteri)." % what)
    return name


def create_user(con, tenant_id, email, name, role, password, actor_id=None, email_verified=1):
    email, name = _clean_email(email), _clean_name(name)
    if role not in (("superadmin",) if tenant_id is None else ("admin", "member")):
        raise ApiError(400, "Ruolo non valido.")
    problem = auth.password_problem(password)
    if problem:
        raise ApiError(400, problem)
    try:
        cur = con.execute("INSERT INTO users(tenant_id,email,name,role,pw_hash,created_at,email_verified) VALUES(?,?,?,?,?,?,?)",
                          (tenant_id, email, name, role, auth.hash_password(password), db.now(), int(bool(email_verified))))
    except sqlite3.IntegrityError:
        raise ApiError(409, "Esiste già un utente con questa email.")
    audit.record(con, PLATFORM if tenant_id is None else tenant_id, actor_id, "utente_creato", {"email": email, "ruolo": role})
    return cur.lastrowid


def create_tenant(con, name, package_id, admin_email, admin_name, admin_password, actor_id=None,
                  origin="gestore", platform_keys=1, email_verified=1, agents=None):
    """Crea azienda, primo amministratore e team. `agents` None = tutti gli agenti del pacchetto accesi."""
    name = _clean_name(name, "Il nome dell'azienda")
    if not catalog.package(package_id):
        raise ApiError(400, "Pacchetto sconosciuto.")
    # Controlli prima di scrivere, così non resta un'azienda senza amministratore.
    email = _clean_email(admin_email)
    _clean_name(admin_name)
    problem = auth.password_problem(admin_password)
    if problem:
        raise ApiError(400, problem)
    if con.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        raise ApiError(409, "Esiste già un utente con questa email.")
    try:
        cur = con.execute("INSERT INTO tenants(name,package_id,created_at,origin,platform_keys) VALUES(?,?,?,?,?)",
                          (name, package_id, db.now(), origin, int(bool(platform_keys))))
    except sqlite3.IntegrityError:
        raise ApiError(409, "Esiste già un'azienda con questo nome.")
    tenant_id = cur.lastrowid
    create_user(con, tenant_id, email, admin_name, "admin", admin_password, actor_id, email_verified=email_verified)
    team.init_tenant(con, tenant_id, package_id, agents)
    audit.record(con, PLATFORM, actor_id, "azienda_creata", {"azienda": name, "pacchetto": package_id, "origine": origin})
    audit.record(con, tenant_id, actor_id, "azienda_creata", {"pacchetto": package_id})
    return tenant_id


def subscription(tenant):
    """Stato dell'abbonamento: {"paid_until", "days_left", "expired", "grace", "grace_left", "payment_failed"}.
    Senza data non scade mai. I giorni si contano in UTC. Dopo la scadenza ci sono SFB_GRACE_DAYS giorni di tolleranza
    (0 se non impostati) in cui gli agenti lavorano ancora: `expired` diventa vero solo quando sono finiti."""
    tenant = dict(tenant)
    until, failed = tenant.get("paid_until") or "", bool(tenant.get("payment_failed_at"))
    if not until:
        return {"paid_until": "", "days_left": None, "expired": False, "grace": False, "grace_left": 0, "payment_failed": failed}
    grace = max(0, min(30, config.get_int("SFB_GRACE_DAYS", 0)))
    left = (date.fromisoformat(until) - datetime.now(timezone.utc).date()).days
    return {"paid_until": until, "days_left": left, "expired": left < -grace, "grace": -grace <= left < 0,
            "grace_left": max(0, grace + left), "payment_failed": failed}


def local_block(tenant):
    """Perché questa azienda non può ancora far lavorare gli agenti sul proprio computer, oppure "" se può.
    La verifica del computer resta sempre gratuita e senza account; collegarlo, scaricare i pacchetti dell'azienda
    e ricevere lavori richiede un abbonamento pagato: non scaduto, non in prova gratuita."""
    t = dict(tenant)
    if subscription(t)["expired"]:
        return "Abbonamento scaduto: per usare gli agenti sul tuo computer va rinnovato (scheda Abbonamento)."
    if t.get("on_trial"):
        return "Sei in prova gratuita: gli agenti sul tuo computer si attivano dopo il primo pagamento (scheda Abbonamento). Intanto lavorano sui nostri server."
    pkg = str(t.get("package_id") or "").upper()
    card = config.get("STRIPE_SECRET_KEY") and config.get("STRIPE_WEBHOOK_SECRET") and (
        config.get("STRIPE_PRICE_" + pkg) or config.get_int("PRICE_" + pkg, 0) > 0 or config.get_int("PRICE_PER_AGENT", 0) > 0)
    if card and t.get("origin") == "registrazione" and not t.get("paid_until"):
        return "Prima di collegare un computer va attivato l'abbonamento (scheda Abbonamento). Intanto gli agenti lavorano sui nostri server."
    return ""


def update_tenant(con, tenant_id, package_id=None, active=None, actor_id=None, platform_keys=None, token_cap=None, paid_until=None):
    row = con.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,)).fetchone()
    if not row:
        raise ApiError(404, "Azienda non trovata.")
    if package_id is not None and package_id != row["package_id"]:
        if not catalog.package(package_id):
            raise ApiError(400, "Pacchetto sconosciuto.")
        con.execute("UPDATE tenants SET package_id=? WHERE id=?", (package_id, tenant_id))
        team.on_package_change(con, tenant_id, package_id)
        from . import scheduler  # qui dentro: scheduler usa il motore, che usa questo modulo
        scheduler.enforce_limit(con, tenant_id, package_id)
        detail = {"da": row["package_id"], "a": package_id}
        audit.record(con, tenant_id, actor_id, "pacchetto_cambiato", detail)
        audit.record(con, PLATFORM, actor_id, "pacchetto_cambiato", dict(detail, azienda=row["name"]))
    if active is not None and int(bool(active)) != row["active"]:
        con.execute("UPDATE tenants SET active=? WHERE id=?", (int(bool(active)), tenant_id))
        audit.record(con, PLATFORM, actor_id, "azienda_attivata" if active else "azienda_sospesa", {"azienda": row["name"]})
    if platform_keys is not None and int(bool(platform_keys)) != row["platform_keys"]:
        con.execute("UPDATE tenants SET platform_keys=? WHERE id=?", (int(bool(platform_keys)), tenant_id))
        audit.record(con, PLATFORM, actor_id, "chiavi_piattaforma_concesse" if platform_keys else "chiavi_piattaforma_tolte", {"azienda": row["name"]})
    if paid_until is not None:
        try:
            clean = date.fromisoformat(paid_until).isoformat() if paid_until != "" else ""
        except (TypeError, ValueError):
            raise ApiError(400, "La scadenza è una data nel formato AAAA-MM-GG, oppure vuota per nessuna scadenza.")
        if clean != row["paid_until"]:
            con.execute("UPDATE tenants SET paid_until=?, on_trial=0 WHERE id=?", (clean, tenant_id))  # la data messa dal gestore vale come pagamento
            audit.record(con, PLATFORM, actor_id, "scadenza_cambiata", {"azienda": row["name"], "da": row["paid_until"], "a": clean})
            audit.record(con, tenant_id, actor_id, "scadenza_cambiata", {"da": row["paid_until"], "a": clean})
    if token_cap is not None:
        if isinstance(token_cap, bool) or not isinstance(token_cap, int) or not 0 <= token_cap <= 10 ** 13:
            raise ApiError(400, "Il tetto mensile è un numero intero di token, 0 per nessun tetto.")
        if token_cap != row["monthly_token_cap"]:
            con.execute("UPDATE tenants SET monthly_token_cap=? WHERE id=?", (token_cap, tenant_id))
            audit.record(con, PLATFORM, actor_id, "tetto_cambiato", {"azienda": row["name"], "da": row["monthly_token_cap"], "a": token_cap})
            audit.record(con, tenant_id, actor_id, "tetto_cambiato", {"da": row["monthly_token_cap"], "a": token_cap})


def update_user(con, tenant_id, user_id, actor, role=None, active=None, password=None):
    row = con.execute("SELECT * FROM users WHERE id=? AND tenant_id=?", (user_id, tenant_id)).fetchone()
    if not row:
        raise ApiError(404, "Utente non trovato.")
    new_role = row["role"] if role is None else role
    new_active = row["active"] if active is None else int(bool(active))
    if new_role not in ("admin", "member"):
        raise ApiError(400, "Ruolo non valido.")
    loses_admin = row["role"] == "admin" and row["active"] and (new_role != "admin" or not new_active)
    if loses_admin:
        others = con.execute("SELECT COUNT(*) FROM users WHERE tenant_id=? AND role='admin' AND active=1 AND id<>?",
                             (tenant_id, user_id)).fetchone()[0]
        if others == 0:
            raise ApiError(400, "Deve restare almeno un amministratore attivo.")
    if password is not None:
        problem = auth.password_problem(password)
        if problem:
            raise ApiError(400, problem)
        con.execute("UPDATE users SET pw_hash=? WHERE id=? AND tenant_id=?", (auth.hash_password(password), user_id, tenant_id))
    con.execute("UPDATE users SET role=?, active=? WHERE id=? AND tenant_id=?", (new_role, new_active, user_id, tenant_id))
    if password is not None or not new_active or new_role != row["role"]:
        auth.drop_user_sessions(con, user_id)  # ruolo o password cambiati: deve rientrare
    audit.record(con, tenant_id, actor["id"], "utente_modificato",
                 {"email": row["email"], "ruolo": new_role, "attivo": bool(new_active), "password_cambiata": password is not None})


def set_stopped(con, tenant, user, stopped):
    """Arresto d'emergenza: ferma chat, lavori automatici ed esecuzione delle azioni approvate per l'azienda."""
    stopped = int(bool(stopped))
    if stopped == int(tenant.get("stopped") or 0):
        return
    con.execute("UPDATE tenants SET stopped=? WHERE id=?", (stopped, tenant["id"]))
    if not stopped:
        from . import scheduler  # import qui per evitare un ciclo tra moduli
        scheduler.reschedule_all(con, tenant["id"])
    audit.record(con, tenant["id"], user["id"], "arresto_emergenza_attivato" if stopped else "arresto_emergenza_tolto", {})
    news.add(con, tenant["id"], "avviso", "Arresto d'emergenza attivato" if stopped else "Agenti riavviati",
             "Da %s." % user["name"])
