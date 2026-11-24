"""Accesso in due passaggi: oltre alla password, un codice a 6 cifre che cambia ogni 30 secondi.

È lo standard TOTP (RFC 6238, con HMAC-SHA1 come lo usano tutte le app): funziona con Google Authenticator,
Microsoft Authenticator, le Password di iPhone, 1Password e simili. Niente SMS e niente servizi esterni.

- Si attiva dal proprio account: prima la password, poi il primo codice dell'app come prova che è stata impostata bene.
- All'attivazione nascono 8 codici di riserva, da usare una volta sola se il telefono non c'è: nel database c'è solo la loro impronta.
- Un codice vale una volta: lo stesso codice letto alle spalle non fa entrare una seconda persona.
- La chiave segreta è cifrata nel database quando la cifratura è attiva (SFB_SECRET_KEY).
- Chi resta chiuso fuori si fa togliere i due passaggi dal server: «python3 manage.py togli-due-passaggi email».
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
import urllib.parse

from . import audit, auth, connectors, db
from .errors import ApiError

STEP, DIGITS, WINDOW = 30, 6, 1  # un codice ogni 30 secondi; si accetta anche quello prima e quello dopo (orologi non perfetti)
RECOVERY = 8
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # senza lettere e cifre che si confondono
PLATFORM = 0


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def code_at(secret, counter):
    """Il codice di un certo intervallo di 30 secondi (RFC 4226, troncamento dinamico)."""
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", int(counter)), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10 ** DIGITS).zfill(DIGITS)


def match(secret, code, now=None, after=0):
    """L'intervallo a cui corrisponde il codice, se è valido adesso e non è già stato usato; altrimenti None."""
    code = "".join(c for c in str(code or "") if c.isdigit())
    if len(code) != DIGITS:
        return None
    current = int((time.time() if now is None else now) // STEP)
    found = None
    for counter in range(current - WINDOW, current + WINDOW + 1):  # sempre tutti i confronti: il tempo di risposta non dice quale ha funzionato
        if hmac.compare_digest(code_at(secret, counter), code) and counter > after:
            found = counter
    return found


def uri(secret, email, issuer="Alvea"):
    """Il collegamento che le app capiscono: toccato dal telefono apre l'app, e contiene le stesse cose di un codice QR."""
    label = urllib.parse.quote("%s:%s" % (issuer, email))
    return "otpauth://totp/%s?secret=%s&issuer=%s&algorithm=SHA1&digits=%d&period=%d" % (label, secret, urllib.parse.quote(issuer), DIGITS, STEP)


def _seal(secret):
    f = connectors._fernet()
    return "f:" + f.encrypt(secret.encode("ascii")).decode("ascii") if f else "p:" + secret


def _open(stored):
    if not stored:
        return ""
    if stored.startswith("p:"):
        return stored[2:]
    f = connectors._fernet()
    if not f:
        raise ApiError(500, "La chiave dei due passaggi è cifrata ma SFB_SECRET_KEY manca nel file .env.")
    try:
        return f.decrypt(stored[2:].encode("ascii")).decode("ascii")
    except Exception:
        raise ApiError(500, "La chiave dei due passaggi non si decifra: SFB_SECRET_KEY è cambiata. Dal server: python3 manage.py togli-due-passaggi <email>.")


def _fresh(con, user):
    return dict(con.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone())


def _hash(code):
    return hashlib.sha256(("riserva:" + "".join(c for c in str(code).lower() if c in ALPHABET)).encode("ascii")).hexdigest()


def _place(user):
    return user["tenant_id"] if user.get("tenant_id") is not None else PLATFORM


def _guard(user, ip):
    """Password o codici sbagliati qui contano come accessi sbagliati: una sessione rubata non serve a indovinarli."""
    stop = auth.locked(user["email"], ip)
    if stop:
        raise ApiError(429, stop)


def status(con, user):
    u = _fresh(con, user)
    return {"on": bool(u["totp_on"]), "recovery_left": len(json.loads(u["totp_codes"] or "[]")) if u["totp_on"] else 0}


def setup(con, user, password, ip=None):
    """Primo passo: con la password giusta nasce una chiave nuova, non ancora attiva. Ritorna la chiave e il collegamento per l'app."""
    u = _fresh(con, user)
    _guard(u, ip)
    if u["totp_on"]:
        raise ApiError(409, "L'accesso in due passaggi è già attivo. Per cambiare telefono disattivalo e riattivalo.")
    if not auth.check_password(str(password or ""), u["pw_hash"]):
        auth.note_failure(u["email"], ip)
        raise ApiError(403, "Password non corretta.")
    secret = new_secret()
    con.execute("UPDATE users SET totp_secret=?, totp_on=0, totp_last=0, totp_codes='' WHERE id=?", (_seal(secret), u["id"]))
    return {"secret": secret, "uri": uri(secret, u["email"])}


def enable(con, user, code, token=None, ip=None):
    """Secondo passo: il primo codice dell'app prova che è impostata bene. Da qui in poi serve a ogni accesso.
    Ritorna i codici di riserva, che si vedono solo adesso."""
    u = _fresh(con, user)
    _guard(u, ip)
    if u["totp_on"]:
        raise ApiError(409, "L'accesso in due passaggi è già attivo.")
    secret = _open(u["totp_secret"])
    if not secret:
        raise ApiError(400, "Prima chiedi la chiave da mettere nell'app.")
    counter = match(secret, code)
    if counter is None:
        auth.note_failure(u["email"], ip)
        raise ApiError(400, "Codice non valido. Controlla di aver scritto il codice che l'app mostra adesso e che l'ora del telefono sia giusta.")
    codes = ["".join(secrets.choice(ALPHABET) for _ in range(8)) for _ in range(RECOVERY)]
    con.execute("UPDATE users SET totp_on=1, totp_last=?, totp_codes=? WHERE id=?", (counter, json.dumps([_hash(c) for c in codes]), u["id"]))
    con.execute("DELETE FROM sessions WHERE user_id=? AND token_hash!=?", (u["id"], auth._token_hash(token or "")))  # gli altri accessi aperti devono rientrare, con il codice
    audit.record(con, _place(u), u["id"], "due_passaggi_attivati", {})
    return {"ok": True, "recovery": [c[:4] + "-" + c[4:] for c in codes]}


def _accept(con, u, code):
    """Controlla un codice dell'app oppure un codice di riserva, e lo consuma. Ritorna "app", "riserva" oppure None."""
    counter = match(_open(u["totp_secret"]), code, after=u["totp_last"])
    if counter is not None:
        # due accessi nello stesso istante con lo stesso codice: ne passa uno solo
        return "app" if con.execute("UPDATE users SET totp_last=? WHERE id=? AND totp_last<?", (counter, u["id"], counter)).rowcount == 1 else None
    left = json.loads(u["totp_codes"] or "[]")
    candidate = _hash(code)
    hit = [h for h in left if hmac.compare_digest(h, candidate)]
    if hit and len("".join(c for c in str(code).lower() if c in ALPHABET)) == 8:
        left.remove(hit[0])
        con.execute("UPDATE users SET totp_codes=? WHERE id=?", (json.dumps(left), u["id"]))
        audit.record(con, _place(u), u["id"], "codice_di_riserva_usato", {"rimasti": len(left)})
        return "riserva"
    return None


def check_login(con, row, code):
    """All'accesso, dopo la password giusta. Senza codice chiede il codice; con un codice sbagliato alza l'errore.
    Ritorna True se il secondo passaggio è superato (o non è attivo)."""
    u = dict(row)
    if not u.get("totp_on"):
        return True
    if code in (None, ""):
        raise TwoFactorNeeded("Scrivi il codice a 6 cifre della tua app di autenticazione.", missing=True)
    if _accept(con, u, code) is None:
        raise TwoFactorNeeded("Codice non valido. Scrivi quello che l'app mostra adesso, oppure un codice di riserva.", missing=False)
    return True


class TwoFactorNeeded(ApiError):
    """La password è giusta ma manca (o è sbagliato) il secondo passaggio: la schermata mostra il campo del codice."""

    def __init__(self, message, missing):
        ApiError.__init__(self, 401, message)
        self.extra = {"two_factor": True}
        self.missing = missing


def disable(con, user, password, code, ip=None):
    """Per toglierli servono tutte e due le cose: password e codice (dell'app o di riserva)."""
    u = _fresh(con, user)
    _guard(u, ip)
    if not u["totp_on"]:
        raise ApiError(409, "L'accesso in due passaggi non è attivo.")
    if not auth.check_password(str(password or ""), u["pw_hash"]) or _accept(con, u, code) is None:
        auth.note_failure(u["email"], ip)
        raise ApiError(403, "Password o codice non corretti.")
    _clear(con, u)
    audit.record(con, _place(u), u["id"], "due_passaggi_disattivati", {})
    return {"ok": True}


def _clear(con, u):
    con.execute("UPDATE users SET totp_secret='', totp_on=0, totp_last=0, totp_codes='' WHERE id=?", (u["id"],))


def reset(con, email):
    """Dal server, per chi ha perso telefono e codici di riserva. Ritorna True se c'era qualcosa da togliere."""
    row = con.execute("SELECT * FROM users WHERE email=?", (str(email or "").strip().lower(),)).fetchone()
    if not row:
        raise ApiError(404, "Nessun utente con questa email.")
    u = dict(row)
    had = bool(u["totp_on"])
    _clear(con, u)
    auth.drop_user_sessions(con, u["id"])
    if had:
        audit.record(con, _place(u), None, "due_passaggi_tolti_dal_server", {"email": u["email"]})
    return had
