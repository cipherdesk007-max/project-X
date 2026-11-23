"""Registro delle azioni, a catena di hash: ogni riga contiene l'hash della precedente.

Cambiare o cancellare una riga in mezzo rompe la catena e `verify` lo segnala.
Limite dichiarato: chi ha accesso al file del database può riscrivere l'intera catena.
Per una prova verso terzi va salvato altrove, a intervalli, l'ultimo hash (`last_hash`).
"""
from __future__ import annotations

import hashlib
import json

from . import db

ZERO = "0" * 64


def _hash(prev, tenant_id, user_id, action, detail_json, created_at):
    blob = "\x1f".join([prev, str(tenant_id), str(user_id), action, detail_json, created_at])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def record(con, tenant_id, user_id, action, detail=None):
    detail_json = json.dumps(detail or {}, ensure_ascii=False, sort_keys=True)
    created_at = db.now()
    con.execute("BEGIN IMMEDIATE")
    try:
        row = con.execute(
            "SELECT hash FROM audit WHERE tenant_id=? ORDER BY id DESC LIMIT 1", (tenant_id,)
        ).fetchone()
        prev = row["hash"] if row else ZERO
        h = _hash(prev, tenant_id, user_id, action, detail_json, created_at)
        con.execute(
            "INSERT INTO audit(tenant_id,user_id,action,detail,created_at,prev_hash,hash) VALUES(?,?,?,?,?,?,?)",
            (tenant_id, user_id, action, detail_json, created_at, prev, h),
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return h


def verify(con, tenant_id):
    """Ricalcola la catena. Ritorna (ok, righe_controllate, id_prima_riga_rotta, ultimo_hash)."""
    prev = ZERO
    count = 0
    for r in con.execute("SELECT * FROM audit WHERE tenant_id=? ORDER BY id", (tenant_id,)):
        expected = _hash(prev, r["tenant_id"], r["user_id"], r["action"], r["detail"], r["created_at"])
        if r["prev_hash"] != prev or r["hash"] != expected:
            return False, count, r["id"], prev
        prev = r["hash"]
        count += 1
    return True, count, None, prev


def recent(con, tenant_id, limit=100):
    cur = con.execute(
        "SELECT a.id, a.action, a.detail, a.created_at, a.hash, u.email AS user_email "
        "FROM audit a LEFT JOIN users u ON u.id=a.user_id WHERE a.tenant_id=? ORDER BY a.id DESC LIMIT ?",
        (tenant_id, max(1, min(int(limit), 500))),
    )
    out = []
    for r in cur.fetchall():
        d = dict(r)
        d["detail"] = json.loads(d["detail"])
        out.append(d)
    return out
