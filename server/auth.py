"""Password, sessioni e blocco dei tentativi di accesso. Solo libreria standard."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

from . import config, db

SESSION_SECONDS = 12 * 3600
MIN_PASSWORD = 10
MAX_FAILS = 5
LOCK_SECONDS = 300

_fails = {}
_fails_lock = threading.Lock()


def hash_password(password):
    iters = config.get_int("SFB_PBKDF2_ITERS", 600000)
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
    return "pbkdf2_sha256$%d$%s$%s" % (iters, salt.hex(), dk.hex())


def check_password(password, stored):
    try:
        algo, iters, salt_hex, dk_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), dk_hex)
    except (ValueError, AttributeError):
        return False


def password_problem(password):
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        return "La password deve avere almeno %d caratteri." % MIN_PASSWORD
    return None


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def locked(email):
    with _fails_lock:
        count, until = _fails.get(email, (0, 0))
        return until > time.time()


def note_failure(email):
    with _fails_lock:
        count, until = _fails.get(email, (0, 0))
        count += 1
        if count >= MAX_FAILS:
            _fails[email] = (0, time.time() + LOCK_SECONDS)
        else:
            _fails[email] = (count, until)


def note_success(email):
    with _fails_lock:
        _fails.pop(email, None)


def create_session(con, user_id):
    token = secrets.token_urlsafe(32)
    con.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
    con.execute(
        "INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)",
        (_token_hash(token), user_id, db.now(), int(time.time()) + SESSION_SECONDS),
    )
    return token


def drop_session(con, token):
    con.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))


def drop_user_sessions(con, user_id):
    con.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))


def user_from_token(con, token):
    """Ritorna (utente, azienda) oppure (None, None). Utente o azienda disattivati non passano."""
    if not token:
        return None, None
    row = con.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id "
        "WHERE s.token_hash=? AND s.expires_at>? AND u.active=1",
        (_token_hash(token), int(time.time())),
    ).fetchone()
    if not row:
        return None, None
    user = dict(row)
    user.pop("pw_hash", None)
    tenant = None
    if user["tenant_id"] is not None:
        t = con.execute("SELECT * FROM tenants WHERE id=? AND active=1", (user["tenant_id"],)).fetchone()
        if not t:
            return None, None
        tenant = dict(t)
    return user, tenant
