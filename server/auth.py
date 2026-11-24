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
IP_WINDOW = 600
EMAIL_WINDOW = 900

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


def _limit(name, default):
    return max(1, config.get_int(name, default))


def _recent(key, window, now):
    kept = [t for t in _fails.get(key, ()) if t > now - window]
    if kept:
        _fails[key] = kept
    else:
        _fails.pop(key, None)
    return len(kept)


def locked(email, ip=None):
    """Perché questo accesso è fermo per qualche minuto, oppure "" se può provare.
    Tre conti separati, così chi sbaglia apposta la password di un altro non lo chiude fuori dal suo computer:
    stessa email dallo stesso indirizzo (5 in 5 minuti), un indirizzo su qualunque email (30 in 10 minuti),
    una email da qualunque indirizzo (50 in 15 minuti)."""
    now = time.time()
    with _fails_lock:
        if _recent((email, ip), LOCK_SECONDS, now) >= MAX_FAILS or _recent(("email", email), EMAIL_WINDOW, now) >= _limit("SFB_LOGIN_PER_EMAIL", 50):
            return "Troppi tentativi sbagliati. Riprova tra qualche minuto."
        if _recent(("ip", ip), IP_WINDOW, now) >= _limit("SFB_LOGIN_PER_IP", 30):
            return "Troppi tentativi sbagliati da questo indirizzo. Riprova tra qualche minuto."
    return ""


def note_failure(email, ip=None):
    now = time.time()
    with _fails_lock:
        if len(_fails) > 50000:  # un'ondata di tentativi non deve riempire la memoria: si buttano i conti scaduti
            for key in [k for k, v in _fails.items() if not v or v[-1] < now - EMAIL_WINDOW]:
                del _fails[key]
            if len(_fails) > 50000:
                _fails.clear()
        for key in ((email, ip), ("ip", ip), ("email", email)):
            _fails.setdefault(key, []).append(now)


def note_success(email, ip=None):
    with _fails_lock:
        _fails.pop((email, ip), None)


def reset_fails():
    with _fails_lock:
        _fails.clear()


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
