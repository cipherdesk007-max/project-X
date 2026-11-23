"""Configurazione: legge il file .env e le variabili d'ambiente. Nessuna dipendenza esterna."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"


def load_env(path=None):
    """Carica KEY=VALORE da .env senza sovrascrivere ciò che è già nell'ambiente."""
    p = Path(path) if path else ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get(name, default=""):
    return os.environ.get(name, default)


def get_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def db_path():
    return Path(get("SFB_DB", str(ROOT / "data" / "salesforb.db")))


def demo_mode():
    return get("SFB_DEMO", "0") == "1"
