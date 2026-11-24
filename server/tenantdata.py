"""Tutti i dati di un'azienda: esportarli in un archivio e cancellarli davvero.

Serve quando un cliente chiede una copia dei suoi dati o chiude il contratto (lo promettono le pagine legali).
Le tabelle non sono scritte a mano: si prendono dal database tutte quelle che hanno una colonna `tenant_id`,
così una tabella aggiunta domani viene esportata e cancellata senza doversene ricordare qui.
Si usa dal server, con `python3 manage.py esporta-azienda` e `cancella-azienda`: di proposito non c'è un pulsante
nel pannello, perché chi rubasse l'accesso del gestore non deve poter portare via o distruggere un'azienda con un clic.
"""
from __future__ import annotations

import json
import os
import shutil
import zipfile

from . import audit, db, media
from .errors import ApiError

PLATFORM = 0
# Quello che non esce mai in un'esportazione: segreti che servono solo al server.
SECRET_COLUMNS = {"users": {"pw_hash"}, "devices": {"token_hash"}, "connectors": {"config"}}
SKIP_TABLES = {"oauth_states", "device_codes"}  # codici a scadenza breve: non sono dati del cliente
BY_USER = ("sessions", "email_tokens")          # tabelle legate all'utente, senza colonna dell'azienda


def tables(con):
    """Le tabelle con i dati delle aziende, in ordine alfabetico."""
    names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return [n for n in names if n != "tenants" and "tenant_id" in [c[1] for c in con.execute("PRAGMA table_info(%s)" % n)]]


def find(con, ref):
    """L'azienda per numero oppure per nome esatto."""
    ref = str(ref).strip()
    row = con.execute("SELECT * FROM tenants WHERE id=?", (int(ref),)).fetchone() if ref.isdigit() and len(ref) < 12 else None
    row = row or con.execute("SELECT * FROM tenants WHERE name=?", (ref,)).fetchone()
    if not row or row["id"] == PLATFORM:
        raise ApiError(404, "Azienda non trovata: «%s». L'elenco è in «python3 manage.py aziende»." % ref[:80])
    return dict(row)


def counts(con, tenant_id):
    return {t: con.execute("SELECT COUNT(*) FROM %s WHERE tenant_id=?" % t, (tenant_id,)).fetchone()[0] for t in tables(con)}


def export(con, tenant, path):
    """Scrive un archivio .zip con tutti i dati dell'azienda (un file JSON per tabella) e i file che ha caricato.
    Ritorna {"tabelle": {nome: righe}, "file": quanti}."""
    tid, done, files = tenant["id"], {}, 0
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # contiene dati dei clienti: lo legge solo chi lo crea
    with os.fdopen(fd, "wb") as raw, zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("azienda.json", json.dumps({k: tenant[k] for k in tenant if not k.startswith("stripe_")}, ensure_ascii=False, indent=1))
        for table in tables(con):
            if table in SKIP_TABLES:
                continue
            hidden = SECRET_COLUMNS.get(table, set())
            rows = [{k: r[k] for k in r.keys() if k not in hidden} for r in con.execute("SELECT * FROM %s WHERE tenant_id=? ORDER BY rowid" % table, (tid,))]
            done[table] = len(rows)
            z.writestr("dati/%s.json" % table, json.dumps(rows, ensure_ascii=False, indent=1))
        folder = media.tenant_dir(tid)
        for row in con.execute("SELECT stored_name FROM media_assets WHERE tenant_id=? ORDER BY id", (tid,)):
            src = folder / row["stored_name"]
            if media.STORED_NAME.fullmatch(row["stored_name"]) and src.is_file():
                z.write(str(src), "file/" + row["stored_name"])  # il nome originale di ogni file è in dati/media_assets.json
                files += 1
        z.writestr("LEGGIMI.txt", "Dati dell'azienda «%s» esportati da Alvea il %s.\n"
                   "dati/: un file per tabella, in formato JSON. file/: i file caricati; il nome originale di ognuno è in dati/media_assets.json.\n"
                   "Non ci sono password, chiavi dei collegamenti né gettoni dei computer: servono solo al server.\n" % (tenant["name"], db.now()))
    return {"tabelle": done, "file": files}


def erase(con, tenant, actor_id=None):
    """Cancella l'azienda e tutto quello che le appartiene: righe in ogni tabella, sessioni, file. Non si torna indietro.
    Ritorna quante righe ha tolto per tabella."""
    tid = tenant["id"]
    try:
        from . import google
        google.disconnect(con, tid)  # se c'è un account Google collegato, il permesso viene ritirato anche da Google
    except Exception:
        pass
    users = [r[0] for r in con.execute("SELECT id FROM users WHERE tenant_id=?", (tid,))]
    removed = {}
    con.execute("BEGIN IMMEDIATE")
    try:
        for table in BY_USER:
            if users:
                removed[table] = con.execute("DELETE FROM %s WHERE user_id IN (%s)" % (table, ",".join("?" * len(users))), users).rowcount
        for table in tables(con):
            removed[table] = con.execute("DELETE FROM %s WHERE tenant_id=?" % table, (tid,)).rowcount
        con.execute("DELETE FROM tenants WHERE id=?", (tid,))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    shutil.rmtree(str(media.tenant_dir(tid)), ignore_errors=True)
    audit.record(con, PLATFORM, actor_id, "azienda_cancellata", {"azienda": tenant["name"], "id": tid, "righe": sum(removed.values())})
    return {k: v for k, v in removed.items() if v}


def leftovers(con, tenant_id, user_ids=()):
    """Quello che è rimasto di un'azienda dopo la cancellazione: deve essere vuoto. {tabella: righe}."""
    left = {t: n for t, n in counts(con, tenant_id).items() if n}
    for table in BY_USER:
        if user_ids:
            n = con.execute("SELECT COUNT(*) FROM %s WHERE user_id IN (%s)" % (table, ",".join("?" * len(user_ids))), list(user_ids)).fetchone()[0]
            if n:
                left[table] = n
    if con.execute("SELECT 1 FROM tenants WHERE id=?", (tenant_id,)).fetchone():
        left["tenants"] = 1
    if media.tenant_dir(tenant_id).exists():
        left["file"] = 1
    return left
