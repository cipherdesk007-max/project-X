"""Novità: ciò che è successo mentre nessuno guardava (automazioni, azioni in attesa, avvisi)."""
from __future__ import annotations

from . import db

KEEP = 500


def add(con, tenant_id, kind, title, body=""):
    con.execute("INSERT INTO news(tenant_id,kind,title,body,created_at) VALUES(?,?,?,?,?)",
                (tenant_id, kind, str(title)[:200], str(body)[:4000], db.now()))
    con.execute("DELETE FROM news WHERE tenant_id=? AND id NOT IN "
                "(SELECT id FROM news WHERE tenant_id=? ORDER BY id DESC LIMIT ?)", (tenant_id, tenant_id, KEEP))


def recent(con, tenant_id, limit=100):
    return db.rows(con.execute("SELECT id, kind, title, body, is_read, created_at FROM news WHERE tenant_id=? "
                               "ORDER BY id DESC LIMIT ?", (tenant_id, max(1, min(int(limit), KEEP)))))


def mark_all_read(con, tenant_id):
    con.execute("UPDATE news SET is_read=1 WHERE tenant_id=? AND is_read=0", (tenant_id,))


def summary(con, tenant_id):
    unread = con.execute("SELECT COUNT(*) FROM news WHERE tenant_id=? AND is_read=0", (tenant_id,)).fetchone()[0]
    last = con.execute("SELECT title, created_at FROM news WHERE tenant_id=? ORDER BY id DESC LIMIT 1", (tenant_id,)).fetchone()
    return {"unread": unread, "latest": dict(last) if last else None}
