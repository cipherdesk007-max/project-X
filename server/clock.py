"""Fuso orario della piattaforma (SFB_TZ, predefinito Europe/Rome) per orari di automazioni e calendario."""
from __future__ import annotations

from datetime import datetime

from . import config


def tz_name():
    return config.get("SFB_TZ", "Europe/Rome")


def tz():
    """Fuso orario richiesto; se il sistema non ha l'archivio dei fusi (capita su Windows), l'ora locale del server."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tz_name())
    except Exception:
        return datetime.now().astimezone().tzinfo


def local(ts=None):
    return datetime.fromtimestamp(ts, tz()) if ts is not None else datetime.now(tz())


def aware(naive_iso):
    """'2026-12-01T10:00' nel fuso della piattaforma -> datetime con fuso."""
    return datetime.fromisoformat(naive_iso).replace(tzinfo=tz())
