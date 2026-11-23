"""Archivio dei file di ogni azienda: clip video, immagini, audio.

- I file stanno in `storage/{tenant_id}/` con un nome casuale scelto dal server: il nome dato dall'utente
  non diventa mai un percorso, serve solo per mostrarlo.
- Il caricamento è letto a pezzi e scritto subito su disco: un video grande non passa dalla memoria.
- Si accettano solo i tipi in elenco, e il contenuto deve corrispondere all'estensione (primi byte del file).
- Per vederli la schermata usa collegamenti firmati che scadono: un tag <video> non può mandare l'intestazione
  di accesso, e così il file resta comunque legato all'azienda che l'ha caricato.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import shutil
import time
from email.message import Message
from pathlib import Path

from . import audit, catalog, config, db
from .errors import ApiError

MB = 1024 * 1024
CHUNK = 256 * 1024
LINK_SECONDS = 900
FIELD_LIMIT = 64 * 1024
HEADER_LIMIT = 8192
# estensione -> (tipo mostrato, tipo MIME servito, famiglia riconosciuta dai primi byte)
TYPES = {
    "jpg": ("immagine", "image/jpeg", "jpeg"), "jpeg": ("immagine", "image/jpeg", "jpeg"),
    "png": ("immagine", "image/png", "png"), "gif": ("immagine", "image/gif", "gif"), "webp": ("immagine", "image/webp", "webp"),
    "mp4": ("video", "video/mp4", "iso"), "mov": ("video", "video/quicktime", "iso"), "webm": ("video", "video/webm", "webm"),
    "mp3": ("audio", "audio/mpeg", "mp3"), "wav": ("audio", "audio/wav", "wav"), "m4a": ("audio", "audio/mp4", "iso"),
    "ogg": ("audio", "audio/ogg", "ogg"),
}
STORED_NAME = re.compile(r"[0-9a-f]{32}\.[a-z0-9]{2,5}")
_SECRET = secrets.token_bytes(32)  # firma dei collegamenti: cambia a ogni avvio, i collegamenti durano pochi minuti


class UploadError(ApiError):
    pass


def storage_root():
    return Path(config.get("SFB_STORAGE") or str(config.ROOT / "storage"))  # una riga vuota nel .env vale come assente


def tenant_dir(tenant_id, create=False):
    d = storage_root() / str(int(tenant_id))
    if create:
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(str(d), 0o700)
        except OSError:
            pass
    return d


def max_file_bytes():
    return max(1, config.get_int("SFB_UPLOAD_MAX_MB", 200)) * MB


def quota_bytes(tenant):
    return catalog.limit(tenant["package_id"], "archivio_mb") * MB


def used_bytes(con, tenant_id):
    return con.execute("SELECT COALESCE(SUM(size_bytes),0) FROM media_assets WHERE tenant_id=?", (tenant_id,)).fetchone()[0]


def sniff(head):
    """Famiglia del file dai primi byte, oppure None."""
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip"):
        return "iso"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "mp3"
    return None


def clean_name(filename):
    """Nome da mostrare: niente cartelle, niente caratteri di controllo."""
    name = re.split(r"[\\/]", str(filename or ""))[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip().lstrip(".")
    return name[:150] or "file"


class Body:
    """Legge dal flusso della richiesta al massimo `length` byte."""

    def __init__(self, stream, length):
        self.stream, self.left = stream, length

    def read(self, n):
        n = min(n, self.left)
        if n <= 0:
            return b""
        data = self.stream.read(n)
        self.left -= len(data)
        return data


def parse_multipart(body, boundary, open_sink, max_file, chunk=CHUNK):
    """Legge un corpo multipart/form-data a pezzi. Il primo campo «file» con un nome di file va nel `sink`;
    gli altri campi vengono scartati. Ritorna (nome, dimensione, sha256, primi 64 byte) oppure None."""
    delim = b"\r\n--" + boundary
    buf = b"\r\n" + body.read(chunk)

    def fill():
        nonlocal buf
        data = body.read(chunk)
        buf += data
        return bool(data)

    while delim not in buf:
        if len(buf) > HEADER_LIMIT or not fill():
            raise UploadError(400, "Il caricamento non è nel formato atteso (multipart/form-data).")
    buf = buf[buf.index(delim) + len(delim):]
    result = None
    while True:
        while len(buf) < 2:
            if not fill():
                raise UploadError(400, "Caricamento interrotto.")
        if buf[:2] == b"--":
            return result
        if buf[:2] != b"\r\n":
            raise UploadError(400, "Il caricamento non è nel formato atteso (multipart/form-data).")
        buf = buf[2:]
        while b"\r\n\r\n" not in buf:
            if len(buf) > HEADER_LIMIT or not fill():
                raise UploadError(400, "Caricamento interrotto o intestazioni troppo lunghe.")
        raw, buf = buf.split(b"\r\n\r\n", 1)
        part = Message()
        for line in raw.decode("utf-8", "replace").split("\r\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                part[key.strip()] = value.strip()
        filename = part.get_filename()
        is_file = result is None and filename is not None and part.get_param("name", header="content-disposition") == "file"
        sink = open_sink() if is_file else None
        size, sha, head, limit = 0, hashlib.sha256(), b"", (max_file if is_file else FIELD_LIMIT)
        while True:
            at = buf.find(delim)
            if at >= 0:
                piece, buf, last = buf[:at], buf[at + len(delim):], True
            else:  # tengo in coda quanto basta a non spezzare un delimitatore a cavallo di due letture
                keep = len(delim) - 1
                piece, buf, last = (buf[:-keep], buf[-keep:], False) if len(buf) > keep else (b"", buf, False)
            if piece:
                size += len(piece)
                if size > limit:
                    raise UploadError(413, "Il file supera la dimensione massima di %d MB." % (max_file // MB) if is_file
                                      else "Un campo del modulo è troppo lungo.")
                if is_file:
                    sink.write(piece)
                    sha.update(piece)
                    if len(head) < 64:
                        head += piece[:64 - len(head)]
            if last:
                break
            if not fill():
                raise UploadError(400, "Caricamento interrotto.")
        if is_file:
            result = (filename, size, sha.hexdigest(), head)


def _public(row):
    exp = int(time.time()) + LINK_SECONDS
    return {"id": row["id"], "kind": row["kind"], "name": row["original_name"], "mime": row["mime"], "size_bytes": row["size_bytes"],
            "created_at": row["created_at"], "uploaded_by": row["uploaded_by"],
            "url": "/api/files/%d/content?e=%d&s=%s" % (row["id"], exp, _sign(row["id"], row["tenant_id"], exp))}


def _sign(asset_id, tenant_id, exp):
    return hmac.new(_SECRET, ("%d.%d.%d" % (asset_id, tenant_id, exp)).encode("ascii"), hashlib.sha256).hexdigest()[:40]


def upload(con, tenant, user, body, content_type):
    """Riceve un file e lo registra. `body` è un Body sul flusso della richiesta. Ritorna il file registrato."""
    header = Message()
    header["content-type"] = content_type or ""
    boundary = header.get_param("boundary")
    if header.get_content_type() != "multipart/form-data" or not boundary or len(boundary) > 70:
        raise UploadError(400, "Il caricamento deve essere multipart/form-data con un campo «file».")
    limit, quota, tid = max_file_bytes(), quota_bytes(tenant), tenant["id"]
    if body.left > limit + MB:
        raise UploadError(413, "Il file supera la dimensione massima di %d MB." % (limit // MB))
    used = used_bytes(con, tid)
    if used >= quota or used + body.left > quota + MB:
        raise UploadError(413, "Spazio dell'archivio esaurito: il pacchetto %s prevede %d MB." % (
            catalog.package(tenant["package_id"])["name"], quota // MB))
    folder = tenant_dir(tid, create=True)
    tmp = folder / (".tmp-" + secrets.token_hex(8))
    handle = None

    def open_sink():
        nonlocal handle
        handle = open(str(tmp), "wb")
        return handle

    try:
        try:
            found = parse_multipart(body, boundary.encode("utf-8"), open_sink, limit)
        finally:
            if handle:
                handle.close()
        if not found:
            raise UploadError(400, "Manca il file: va mandato nel campo «file».")
        filename, size, sha, head = found
        name = clean_name(filename)
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if size == 0:
            raise UploadError(400, "Il file è vuoto.")
        if ext not in TYPES:
            raise UploadError(415, "Tipo di file non ammesso. Ammessi: %s." % ", ".join(sorted(TYPES)))
        kind, mime, family = TYPES[ext]
        if sniff(head) != family:
            raise UploadError(415, "Il contenuto del file non corrisponde all'estensione .%s." % ext)
        if used_bytes(con, tid) + size > quota:
            raise UploadError(413, "Spazio dell'archivio esaurito: il pacchetto %s prevede %d MB." % (
                catalog.package(tenant["package_id"])["name"], quota // MB))
        same = con.execute("SELECT * FROM media_assets WHERE tenant_id=? AND sha256=?", (tid, sha)).fetchone()
        if same:  # stesso identico file già in archivio: non lo salvo due volte
            return dict(_public(same), duplicate=True)
        stored = secrets.token_hex(16) + "." + ext
        os.replace(str(tmp), str(folder / stored))
        try:
            os.chmod(str(folder / stored), 0o600)
        except OSError:
            pass
        cur = con.execute("INSERT INTO media_assets(tenant_id,kind,original_name,stored_name,mime,size_bytes,sha256,uploaded_by,created_at) "
                          "VALUES(?,?,?,?,?,?,?,?,?)", (tid, kind, name, stored, mime, size, sha, user["id"], db.now()))
        audit.record(con, tid, user["id"], "file_caricato", {"id": cur.lastrowid, "tipo": kind, "byte": size})
        row = con.execute("SELECT * FROM media_assets WHERE id=? AND tenant_id=?", (cur.lastrowid, tid)).fetchone()
        return dict(_public(row), duplicate=False)
    except OSError:
        raise UploadError(507, "Il server non riesce a salvare il file (spazio su disco o permessi).")
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def store_path(con, tenant, user_id, path, filename):
    """Registra in archivio un file prodotto sul server (per esempio un video montato). Stesse regole dei caricamenti."""
    tid, name = tenant["id"], clean_name(filename)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    size = os.path.getsize(path)
    if ext not in TYPES or size == 0:
        raise UploadError(415, "Il file prodotto non è di un tipo ammesso.")
    kind, mime, family = TYPES[ext]
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        head = f.read(64)
        sha.update(head)
        for chunk in iter(lambda: f.read(CHUNK), b""):
            sha.update(chunk)
    if sniff(head) != family:
        raise UploadError(415, "Il contenuto del file prodotto non corrisponde all'estensione .%s." % ext)
    if size > max_file_bytes() or used_bytes(con, tid) + size > quota_bytes(tenant):
        raise UploadError(413, "Spazio dell'archivio esaurito o file troppo grande.")
    same = con.execute("SELECT * FROM media_assets WHERE tenant_id=? AND sha256=?", (tid, sha.hexdigest())).fetchone()
    if same:
        return dict(_public(same), duplicate=True)
    stored = secrets.token_hex(16) + "." + ext
    shutil.copyfile(path, str(tenant_dir(tid, create=True) / stored))
    cur = con.execute("INSERT INTO media_assets(tenant_id,kind,original_name,stored_name,mime,size_bytes,sha256,uploaded_by,created_at) "
                      "VALUES(?,?,?,?,?,?,?,?,?)", (tid, kind, name, stored, mime, size, sha.hexdigest(), user_id, db.now()))
    audit.record(con, tid, user_id, "file_caricato", {"id": cur.lastrowid, "tipo": kind, "byte": size, "origine": "lavoro"})
    return dict(_public(con.execute("SELECT * FROM media_assets WHERE id=? AND tenant_id=?", (cur.lastrowid, tid)).fetchone()), duplicate=False)


def listing(con, tenant):
    rows = con.execute("SELECT * FROM media_assets WHERE tenant_id=? ORDER BY id DESC LIMIT 500", (tenant["id"],)).fetchall()
    return {"assets": [_public(r) for r in rows], "used_bytes": used_bytes(con, tenant["id"]), "quota_bytes": quota_bytes(tenant),
            "max_file_bytes": max_file_bytes(), "types": sorted(TYPES), "link_seconds": LINK_SECONDS}


def delete(con, tenant, user, asset_id):
    row = con.execute("SELECT * FROM media_assets WHERE id=? AND tenant_id=?", (asset_id, tenant["id"])).fetchone()
    if not row:
        raise ApiError(404, "File non trovato.")
    if user["role"] != "admin" and row["uploaded_by"] != user["id"]:
        raise ApiError(403, "Un operatore può rimuovere solo i file che ha caricato lui.")
    con.execute("DELETE FROM media_assets WHERE id=? AND tenant_id=?", (asset_id, tenant["id"]))
    try:
        (tenant_dir(tenant["id"]) / row["stored_name"]).unlink()
    except OSError:
        pass
    audit.record(con, tenant["id"], user["id"], "file_rimosso", {"id": asset_id})


def resolve_link(con, asset_id, exp, signature):
    """Controlla un collegamento firmato. Ritorna (riga, percorso) oppure alza 403/404."""
    row = con.execute("SELECT m.* FROM media_assets m JOIN tenants t ON t.id=m.tenant_id AND t.active=1 WHERE m.id=?", (asset_id,)).fetchone()
    try:
        exp = int(exp)
    except (TypeError, ValueError):
        exp = 0
    # Stessa risposta per file inesistente, firma sbagliata e collegamento scaduto: non si rivela quali file esistono.
    expected = _sign(asset_id, row["tenant_id"] if row else 0, exp)
    if not row or exp < time.time() or not hmac.compare_digest(expected, str(signature or "")):
        raise ApiError(403, "Collegamento non valido o scaduto.")
    if not STORED_NAME.fullmatch(row["stored_name"]):
        raise ApiError(404, "File non trovato.")
    path = tenant_dir(row["tenant_id"]) / row["stored_name"]
    if not path.is_file():
        raise ApiError(404, "File non trovato sul disco.")
    return dict(row), path
