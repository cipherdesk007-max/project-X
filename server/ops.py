"""Operazioni su aziende e utenti, usate sia dalle API sia da manage.py."""
from __future__ import annotations

import re
import sqlite3

from . import audit, auth, catalog, db, news, team
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


def update_tenant(con, tenant_id, package_id=None, active=None, actor_id=None, platform_keys=None):
    row = con.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,)).fetchone()
    if not row:
        raise ApiError(404, "Azienda non trovata.")
    if package_id is not None and package_id != row["package_id"]:
        if not catalog.package(package_id):
            raise ApiError(400, "Pacchetto sconosciuto.")
        con.execute("UPDATE tenants SET package_id=? WHERE id=?", (package_id, tenant_id))
        team.on_package_change(con, tenant_id, package_id)
        detail = {"da": row["package_id"], "a": package_id}
        audit.record(con, tenant_id, actor_id, "pacchetto_cambiato", detail)
        audit.record(con, PLATFORM, actor_id, "pacchetto_cambiato", dict(detail, azienda=row["name"]))
    if active is not None and int(bool(active)) != row["active"]:
        con.execute("UPDATE tenants SET active=? WHERE id=?", (int(bool(active)), tenant_id))
        audit.record(con, PLATFORM, actor_id, "azienda_attivata" if active else "azienda_sospesa", {"azienda": row["name"]})
    if platform_keys is not None and int(bool(platform_keys)) != row["platform_keys"]:
        con.execute("UPDATE tenants SET platform_keys=? WHERE id=?", (int(bool(platform_keys)), tenant_id))
        audit.record(con, PLATFORM, actor_id, "chiavi_piattaforma_concesse" if platform_keys else "chiavi_piattaforma_tolte", {"azienda": row["name"]})


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
