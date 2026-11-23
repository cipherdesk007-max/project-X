"""Impostazioni della piattaforma che il gestore cambia dal pannello, al posto di scriverle nel file .env.

Quello che si salva qui ha la precedenza sul file .env; un campo lasciato vuoto torna al valore del file.
I segreti (chiavi, password) non vengono mai restituiti: il pannello sa solo se sono impostati.
Restano nel file .env le impostazioni che servono prima che il server parta: indirizzo, porta, chiave di cifratura, demo.
"""
from __future__ import annotations

import json
import os

from . import audit, config, connectors, db
from .errors import ApiError

PLATFORM = 0
# gruppo, chiave, etichetta, segreto, tipo ("text" | "url" | "int" | "bool" | "email")
FIELDS = [
    ("Modelli", "ANTHROPIC_API_KEY", "Chiave API di Claude (Anthropic)", True, "text"),
    ("Modelli", "MOONSHOT_API_KEY", "Chiave API di Kimi (Moonshot)", True, "text"),
    ("Modelli", "OPENAI_API_KEY", "Chiave API di ChatGPT (OpenAI)", True, "text"),
    ("Modelli", "CLAUDE_MODEL", "Nome del modello Claude", False, "text"),
    ("Modelli", "KIMI_MODEL", "Nome del modello Kimi", False, "text"),
    ("Modelli", "OPENAI_MODEL", "Nome del modello ChatGPT", False, "text"),
    ("Modelli", "OLLAMA_BASE_URL", "Indirizzo di Ollama accanto al server (es. http://127.0.0.1:11434/v1)", False, "url"),
    ("Modelli", "OLLAMA_MODEL", "Nome del modello locale", False, "text"),
    ("Posta", "SFB_SMTP_HOST", "Server SMTP", False, "text"),
    ("Posta", "SFB_SMTP_PORT", "Porta (465 oppure 587)", False, "int"),
    ("Posta", "SFB_SMTP_USER", "Utente (di solito l'indirizzo email)", False, "text"),
    ("Posta", "SFB_SMTP_PASSWORD", "Password della casella", True, "text"),
    ("Posta", "SFB_SMTP_FROM_NAME", "Nome del mittente", False, "text"),
    ("Ricerca", "BRAVE_API_KEY", "Chiave di Brave Search", True, "text"),
    ("Ricerca", "SEARXNG_URL", "Indirizzo della tua istanza SearXNG", False, "url"),
    ("Google", "GOOGLE_CLIENT_ID", "ID client OAuth", False, "text"),
    ("Google", "GOOGLE_CLIENT_SECRET", "Segreto client OAuth", True, "text"),
    ("Registrazione", "SFB_REGISTRATION", "Registrazione aperta (1 sì, 0 no)", False, "bool"),
    ("Registrazione", "SFB_TRIAL_DAYS", "Giorni di prova gratuita (0 = nessuna scadenza)", False, "int"),
    ("Registrazione", "SFB_REGISTER_PLATFORM_KEYS", "Chi si registra usa le tue chiavi dei modelli (1 sì, 0 no)", False, "bool"),
    ("Pagamenti", "SFB_GRACE_DAYS", "Giorni di tolleranza dopo la scadenza, da 0 a 30 (0 = pausa dal giorno dopo)", False, "int"),
    ("Pagamenti", "STRIPE_SECRET_KEY", "Chiave segreta di Stripe (sk_…)", True, "text"),
    ("Pagamenti", "STRIPE_WEBHOOK_SECRET", "Segreto del webhook di Stripe (whsec_…)", True, "text"),
    ("Pagamenti", "STRIPE_PRICE_START", "ID del prezzo mensile del pacchetto Start (price_…)", False, "text"),
    ("Pagamenti", "STRIPE_PRICE_BUSINESS", "ID del prezzo mensile del pacchetto Business (price_…)", False, "text"),
    ("Pagamenti", "STRIPE_PRICE_COMPLETO", "ID del prezzo mensile del pacchetto Completo (price_…)", False, "text"),
]
BY_KEY = {f[1]: f for f in FIELDS}


def _stored(con):
    row = con.execute("SELECT config, encrypted FROM platform_settings WHERE name='impostazioni'").fetchone()
    if not row:
        return {}
    blob = row["config"]
    if row["encrypted"]:
        f = connectors._fernet()
        if not f:
            raise ApiError(500, "Le impostazioni sono cifrate ma SFB_SECRET_KEY manca nel file .env.")
        try:
            blob = f.decrypt(blob.encode("ascii")).decode("utf-8")
        except Exception:
            raise ApiError(500, "Le impostazioni non si decifrano: SFB_SECRET_KEY è cambiata.")
    return json.loads(blob)


def load(con):
    """Mette in uso le impostazioni salvate. Va chiamata all'avvio e dopo ogni salvataggio."""
    try:
        stored = _stored(con)
    except ApiError as e:
        print("ATTENZIONE: %s" % e.message)
        stored = {}
    config.OVERRIDES.clear()
    config.OVERRIDES.update({k: v for k, v in stored.items() if k in BY_KEY and v != ""})


def view(con):
    """Quello che il pannello mostra: mai il valore di un segreto, solo se c'è e da dove viene."""
    out = []
    for group, key, label, secret, kind in FIELDS:
        panel, env = config.OVERRIDES.get(key, ""), os.environ.get(key, "")
        out.append({"group": group, "key": key, "label": label, "secret": secret, "kind": kind, "set": bool(panel or env),
                    "value": "" if secret else (panel or env), "source": "pannello" if panel else (".env" if env else "")})
    return {"fields": out, "encrypted": bool(connectors._fernet()), "webhook_url": config.get("SFB_PUBLIC_URL", "").rstrip("/") + "/api/billing/webhook"}


def _clean(key, value):
    _, _, label, secret, kind = BY_KEY[key]
    v = str(value if value is not None else "").strip()
    if v == "":
        return ""
    if len(v) > 500 or any(c in v for c in "\r\n\x00"):
        raise ApiError(400, "Valore non valido per «%s»." % label)
    if kind == "int" and not (v.isdigit() and int(v) <= 100000):
        raise ApiError(400, "«%s» deve essere un numero intero." % label)
    if kind == "bool" and v not in ("0", "1"):
        raise ApiError(400, "«%s» deve essere 1 oppure 0." % label)
    if kind == "url" and not v.startswith(("http://", "https://")):
        raise ApiError(400, "«%s» deve iniziare con http:// oppure https://." % label)
    return v


def save(con, user, values):
    if not isinstance(values, dict) or not values:
        raise ApiError(400, "Nessuna impostazione da salvare.")
    unknown = [k for k in values if k not in BY_KEY]
    if unknown:
        raise ApiError(400, "Impostazione sconosciuta: %s." % ", ".join(sorted(map(str, unknown)))[:200])
    clean = {k: _clean(k, v) for k, v in values.items()}
    f = connectors._fernet()
    if not f and config.get("SFB_ALLOW_PLAINTEXT_TOKENS") != "1" and any(BY_KEY[k][3] and v for k, v in clean.items()):
        raise ApiError(400, "Per salvare chiavi e password dal pannello serve la cifratura: metti SFB_SECRET_KEY nel file .env "
                            "(la crea «python3 manage.py init») e installa il pacchetto cryptography.")
    stored = _stored(con)
    changed = sorted(k for k, v in clean.items() if stored.get(k, "") != v)
    for k, v in clean.items():
        if v == "":
            stored.pop(k, None)
        else:
            stored[k] = v
    blob = json.dumps(stored, ensure_ascii=False)
    data, enc = (f.encrypt(blob.encode("utf-8")).decode("ascii"), 1) if f else (blob, 0)
    con.execute("INSERT INTO platform_settings(name,config,encrypted,updated_at) VALUES('impostazioni',?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET config=excluded.config, encrypted=excluded.encrypted, updated_at=excluded.updated_at",
                (data, enc, db.now()))
    if changed:  # nel registro finiscono i nomi delle impostazioni toccate, mai i valori
        audit.record(con, PLATFORM, user["id"], "impostazioni_cambiate", {"chiavi": changed})
    load(con)
    return {"ok": True, "changed": changed}
