"""Computer dei clienti (launcher), pacchetti locali firmati e coda dei lavori.

Tre cose tenute insieme perché sono lo stesso percorso:
- un «computer» è un launcher collegato a un'azienda con un codice usa e getta; da quel momento parla con un suo gettone;
- un «pacchetto» è il codice che il launcher scarica ed esegue: viaggia con una firma RSA che il launcher controlla;
- un «lavoro» è l'esecuzione di uno strumento fuori dal server: sul computer del cliente (`locale`) o in un processo
  a parte sul server (`cloud`, oggi solo il montaggio video).
Le chiavi dei modelli e dei servizi non entrano mai in un lavoro né in un pacchetto.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from . import audit, catalog, config, db, media, news, team
from .errors import ApiError

PKG_DIR = config.ROOT / "packages"
LAUNCHER_DIR = config.ROOT / "launcher"
BASE_PACKAGES = ("modello",)          # installati su ogni computer collegato
ONLINE_SECONDS, CODE_SECONDS, STALE_JOB_SECONDS, MAX_ATTEMPTS = 45, 600, 300, 2
SYNC_WAIT, MODEL_WAIT = 30, 110
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")
_stop = threading.Event()
_thread = None


class JobError(Exception):
    """Un lavoro non può partire o non è riuscito: il messaggio va mostrato a chi l'ha chiesto."""


def _h(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------- pacchetti e firma ----------

def package_names():
    return sorted(p.name for p in PKG_DIR.iterdir() if (p / "package.json").is_file()) if PKG_DIR.is_dir() else []


def bundle(name):
    """Contenuto di un pacchetto: {"name", "version", "files": {percorso: testo}} più i byte esatti che vengono firmati."""
    folder = PKG_DIR / name
    if name not in package_names():
        raise ApiError(404, "Pacchetto sconosciuto.")
    meta = json.loads((folder / "package.json").read_text(encoding="utf-8"))
    files = {p.relative_to(folder).as_posix(): p.read_text(encoding="utf-8")
             for p in sorted(folder.rglob("*")) if p.is_file() and p.suffix in (".py", ".json") and "__pycache__" not in p.parts}
    body = {"name": name, "version": meta["version"], "files": files}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return body, raw


def _data_dir():
    return config.db_path().parent


def key_path():
    return Path(config.get("SFB_SIGN_KEY") or str(_data_dir() / "firma_privata.pem"))


def public_key():
    p = _data_dir() / "firma_pubblica.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def _signatures():
    p = _data_dir() / "firme_pacchetti.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def sign_all(key=None):
    """Firma tutti i pacchetti con la chiave privata (creandola se manca). Serve il programma `openssl`.
    Ritorna i nomi firmati. In produzione la chiave privata va tenuta FUORI dal server: vedi LEGGIMI."""
    openssl = shutil.which("openssl")
    if not openssl:
        raise ApiError(503, "Per firmare i pacchetti serve il programma openssl.")
    key = Path(key) if key else key_path()
    _data_dir().mkdir(parents=True, exist_ok=True)
    if not key.is_file():
        subprocess.run([openssl, "genrsa", "-out", str(key), "3072"], check=True, capture_output=True)
        try:
            key.chmod(0o600)
        except OSError:
            pass
    modulus = subprocess.run([openssl, "rsa", "-in", str(key), "-noout", "-modulus"], check=True, capture_output=True, text=True).stdout
    (_data_dir() / "firma_pubblica.json").write_text(json.dumps({"n": modulus.strip().split("=", 1)[1].lower(), "e": 65537}), encoding="utf-8")
    out = {}
    for name in package_names():
        _, raw = bundle(name)
        with tempfile.TemporaryDirectory() as tmp:
            src, sig = Path(tmp) / "pacchetto", Path(tmp) / "firma"
            src.write_bytes(raw)
            subprocess.run([openssl, "dgst", "-sha256", "-sign", str(key), "-out", str(sig), str(src)], check=True, capture_output=True)
            out[name] = {"sha256": hashlib.sha256(raw).hexdigest(), "signature": sig.read_bytes().hex()}
    (_data_dir() / "firme_pacchetti.json").write_text(json.dumps(out), encoding="utf-8")
    return sorted(out)


def verify(public, raw, signature_hex):
    """Controllo RSA PKCS#1 v1.5 con SHA-256, senza librerie esterne. Lo stesso codice è nel launcher."""
    try:
        n, e, sig = int(public["n"], 16), int(public["e"]), bytes.fromhex(signature_hex)
    except (KeyError, ValueError, TypeError):
        return False
    size = (n.bit_length() + 7) // 8
    if len(sig) != size or size < 256:
        return False
    digest = DIGEST_INFO + hashlib.sha256(raw).digest()
    expected = b"\x00\x01" + b"\xff" * (size - len(digest) - 3) + b"\x00" + digest
    return pow(int.from_bytes(sig, "big"), e, n).to_bytes(size, "big") == expected


def signed(name):
    """Firma valida per il contenuto attuale del pacchetto, oppure None."""
    _, raw = bundle(name)
    entry = _signatures().get(name)
    if entry and entry["sha256"] == hashlib.sha256(raw).hexdigest():
        return entry
    return None


def ensure_signed():
    """All'avvio: firma i pacchetti se la chiave è su questo server (demo e prove). Ritorna True se sono tutti firmati."""
    try:
        if all(signed(n) for n in package_names()) and public_key():
            return True
        if config.get("SFB_SIGN_KEY") and not key_path().is_file():
            return False  # chiave tenuta altrove: si firma a mano con manage.py firma-pacchetti
        sign_all()
        return True
    except (ApiError, OSError, subprocess.CalledProcessError, ValueError):
        return False


def launcher_zip():
    """Il launcher da scaricare: i suoi file, l'indirizzo di questo server e la chiave pubblica per controllare i pacchetti."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(LAUNCHER_DIR.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                info = zipfile.ZipInfo("alvea-launcher/" + p.relative_to(LAUNCHER_DIR).as_posix(), date_time=time.localtime()[:6])
                info.external_attr = (0o755 if p.suffix in (".py", ".command", ".sh") else 0o644) << 16
                z.writestr(info, p.read_bytes())
        z.writestr("alvea-launcher/server.json", json.dumps({"server": config.get("SFB_PUBLIC_URL", "http://127.0.0.1:8787").rstrip("/"),
                                                              "public_key": public_key()}, indent=1))
    return out.getvalue()


# ---------- cosa può girare dove ----------

def public_catalog():
    """Requisiti di ogni agente: pubblici, perché il launcher li controlla PRIMA che il cliente scelga o paghi."""
    return {"launcher_version": launcher_version(), "server": config.get("SFB_PUBLIC_URL", "").rstrip("/"),
            "agents": [{"id": a["id"], "name": a["name"], "run": a["run"]} for a in catalog.load()["agents"]],
            "model": catalog.load().get("local_model")}


def launcher_version():
    try:
        text = (LAUNCHER_DIR / "alvea_launcher.py").read_text(encoding="utf-8")
        return text.split('VERSION = "', 1)[1].split('"', 1)[0]
    except (OSError, IndexError):
        return ""


def local_spec(agent_id):
    a = catalog.agent(agent_id)
    spec = (a or {}).get("run", {}).get("local")
    return spec if spec and spec.get("ready") else None


def wanted_packages(con, tenant):
    """Pacchetti che i computer dell'azienda devono avere: quelli degli agenti accesi impostati su «computer»."""
    out = set(BASE_PACKAGES)
    modes = team.modes(con, tenant)
    for agent_id in team.active(con, tenant):
        spec = local_spec(agent_id)
        if spec and (modes.get(agent_id) == "locale" or not catalog.agent(agent_id)["run"]["cloud"]):
            out.add(spec["package"])
    return sorted(out & set(package_names()))


# ---------- computer collegati ----------

def new_code(con, tenant, user):
    """Codice usa e getta, valido dieci minuti, da scrivere nel launcher. Si vede una volta sola."""
    con.execute("DELETE FROM device_codes WHERE expires_at<?", (int(time.time()),))
    raw = "".join(secrets.choice(ALPHABET) for _ in range(8))
    con.execute("INSERT INTO device_codes(code_hash,tenant_id,user_id,expires_at) VALUES(?,?,?,?)",
                (_h(raw), tenant["id"], user["id"], int(time.time()) + CODE_SECONDS))
    audit.record(con, tenant["id"], user["id"], "computer_codice_creato", {})
    return {"code": raw[:4] + "-" + raw[4:], "expires_in": CODE_SECONDS}


def _clean_report(body):
    specs, checks = body.get("specs"), body.get("checks")
    if not isinstance(specs, dict) or not isinstance(checks, dict) or len(json.dumps(body)) > 60000:
        raise ApiError(400, "Verifica del computer non valida.")
    return (json.dumps({str(k)[:40]: (v if isinstance(v, (int, float)) else str(v)[:200]) for k, v in list(specs.items())[:30]}, ensure_ascii=False),
            json.dumps({str(k)[:40]: {"ok": bool((v or {}).get("ok")), "verdict": str((v or {}).get("verdict") or "")[:30],
                                      "why": str((v or {}).get("why") or "")[:400]}
                        for k, v in list(checks.items())[:60] if isinstance(v, dict)}, ensure_ascii=False))


def pair(con, body):
    code = "".join(c for c in str(body.get("code") or "").upper() if c in ALPHABET)
    row = con.execute("SELECT * FROM device_codes WHERE code_hash=? AND expires_at>=?", (_h(code), int(time.time()))).fetchone()
    if not row or len(code) != 8:
        raise ApiError(403, "Codice non valido o scaduto: creane uno nuovo nel Centro di controllo, scheda Computer.")
    if con.execute("DELETE FROM device_codes WHERE code_hash=?", (row["code_hash"],)).rowcount != 1:
        raise ApiError(403, "Codice già usato.")
    tenant = con.execute("SELECT * FROM tenants WHERE id=? AND active=1", (row["tenant_id"],)).fetchone()
    if not tenant:
        raise ApiError(403, "Azienda non attiva.")
    name = " ".join(str(body.get("name") or "Computer").replace("\x00", "").split())[:60] or "Computer"
    token = "sfbd_" + secrets.token_urlsafe(32)
    specs, checks = _clean_report(body) if body.get("specs") is not None else ("{}", "{}")
    cur = con.execute("INSERT INTO devices(tenant_id,name,token_hash,paired_by,specs,checks,launcher_version,last_seen,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                      (tenant["id"], name, _h(token), row["user_id"], specs, checks, str(body.get("launcher_version") or "")[:20], int(time.time()), db.now()))
    audit.record(con, tenant["id"], row["user_id"], "computer_collegato", {"id": cur.lastrowid, "nome": name})
    news.add(con, tenant["id"], "computer", "Computer collegato: %s" % name, "Ora può eseguire gli agenti impostati su «computer».")
    return {"token": token, "device_id": cur.lastrowid, "tenant": tenant["name"], "name": name}


def device_from_token(con, token):
    """Ritorna (computer, azienda) oppure alza 401. Un computer scollegato o di un'azienda sospesa non entra."""
    if not token or not token.startswith("sfbd_"):
        raise ApiError(401, "Questo computer non è collegato: serve un codice nuovo.")
    dev = con.execute("SELECT d.* FROM devices d JOIN tenants t ON t.id=d.tenant_id WHERE d.token_hash=? AND d.revoked=0 AND t.active=1",
                      (_h(token),)).fetchone()
    if not dev:
        raise ApiError(401, "Questo computer non è collegato: serve un codice nuovo.")
    now = int(time.time())
    if now - dev["last_seen"] >= 5:
        con.execute("UPDATE devices SET last_seen=? WHERE id=?", (now, dev["id"]))
    return dict(dev), dict(con.execute("SELECT * FROM tenants WHERE id=?", (dev["tenant_id"],)).fetchone())


def report(con, device, body):
    specs, checks = _clean_report(body)
    con.execute("UPDATE devices SET specs=?, checks=?, launcher_version=? WHERE id=? AND tenant_id=?",
                (specs, checks, str(body.get("launcher_version") or "")[:20], device["id"], device["tenant_id"]))
    return {"ok": True}


def devices(con, tenant):
    now = int(time.time())
    return [{"id": r["id"], "name": r["name"], "online": now - r["last_seen"] <= ONLINE_SECONDS, "last_seen": r["last_seen"],
             "specs": json.loads(r["specs"]), "checks": json.loads(r["checks"]), "launcher_version": r["launcher_version"], "created_at": r["created_at"]}
            for r in con.execute("SELECT * FROM devices WHERE tenant_id=? AND revoked=0 ORDER BY id", (tenant["id"],))]


def revoke(con, tenant, user, device_id):
    if con.execute("UPDATE devices SET revoked=1 WHERE id=? AND tenant_id=? AND revoked=0", (device_id, tenant["id"])).rowcount != 1:
        raise ApiError(404, "Computer non trovato.")
    con.execute("UPDATE jobs SET status='in_coda', device_id=NULL WHERE tenant_id=? AND device_id=? AND status='in_corso'", (tenant["id"], device_id))
    audit.record(con, tenant["id"], user["id"], "computer_scollegato", {"id": device_id})
    return {"ok": True}


def online_device(con, tenant_id, package):
    """Un computer dell'azienda acceso adesso su cui quel pacchetto ha passato la verifica."""
    for r in con.execute("SELECT * FROM devices WHERE tenant_id=? AND revoked=0 AND last_seen>=? ORDER BY last_seen DESC",
                         (tenant_id, int(time.time()) - ONLINE_SECONDS)):
        if (json.loads(r["checks"]).get("pkg:" + package) or {}).get("ok"):
            return dict(r)
    return None


def sync(con, device, tenant):
    out = []
    for name in wanted_packages(con, tenant):
        sig = signed(name)
        if sig:
            out.append({"name": name, "version": bundle(name)[0]["version"], "sha256": sig["sha256"]})
    return {"packages": out, "tenant": tenant["name"], "device": device["name"], "launcher_version": launcher_version()}


def package_signed(name):
    """Il pacchetto con la sua firma. È pubblico: è codice senza segreti, e il launcher lo usa per verificare un computer
    PRIMA che il cliente scelga. Quali lavori arrivano a un computer lo decide comunque il server."""
    sig = signed(name)
    if not sig:
        raise ApiError(503, "Pacchetto non firmato: il gestore deve eseguire «manage.py firma-pacchetti».")
    body, raw = bundle(name)
    return dict(body, sha256=sig["sha256"], signature=sig["signature"])


# ---------- lavori ----------

def place_for(con, tenant, spec):
    """Dove gira uno strumento: None = dentro il server come sempre; 'locale' = computer del cliente; 'cloud' = processo a parte sul server."""
    job = spec.get("job")
    if not job:
        return None
    a = catalog.agent(spec["agent"])
    if not a["run"]["cloud"]:
        return "locale"
    if team.modes(con, tenant).get(spec["agent"]) == "locale" and local_spec(spec["agent"]):
        return "locale"
    return "cloud" if job.get("always") else None


def cloud_video_ready():
    return config.get("SFB_CLOUD_VIDEO", "1") != "0" and bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def dispatch(ctx, spec, args, place):
    """Mette in coda il lavoro. Per gli strumenti brevi aspetta la risposta del computer; per quelli lunghi ritorna subito."""
    job = spec["job"]
    payload = job["prepare"](ctx, args) if job.get("prepare") else args
    name = catalog.agent(spec["agent"])["name"]
    if place == "locale":
        if not local_spec(spec["agent"]):
            raise JobError("L'Agente %s non ha ancora una versione per il computer." % name)
        dev = online_device(ctx.con, ctx.tenant_id, job["package"])
        if not dev and job["kind"] == "sync":
            raise JobError("Nessun computer dell'azienda è acceso e pronto per l'Agente %s. Apri il launcher sul computer "
                           "(Centro di controllo, scheda Computer)." % name)
    elif not cloud_video_ready():
        raise JobError("Il montaggio sui nostri server non è attivo. Imposta l'Agente %s su «computer» nella scheda Computer." % name)
    cur = ctx.con.execute("INSERT INTO jobs(tenant_id,agent_id,tool,package,place,args,requested_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                          (ctx.tenant_id, spec["agent"], spec["name"], job["package"], place, json.dumps(payload, ensure_ascii=False), ctx.user["id"], db.now()))
    job_id = cur.lastrowid
    if job["kind"] == "sync":
        return wait(ctx.con, ctx.tenant_id, job_id, SYNC_WAIT)
    where = "sul computer dell'azienda" if place == "locale" else "sui nostri server"
    hint = "" if place == "cloud" or online_device(ctx.con, ctx.tenant_id, job["package"]) else " Partirà appena un computer dell'azienda apre il launcher."
    return "Lavoro n. %d messo in coda %s.%s Il risultato comparirà nell'Archivio Media e alla postazione dell'agente." % (job_id, where, hint)


def wait(con, tenant_id, job_id, seconds):
    end = time.time() + seconds
    while time.time() < end:
        row = con.execute("SELECT status, result FROM jobs WHERE id=? AND tenant_id=?", (job_id, tenant_id)).fetchone()
        if row["status"] == "fatto":
            return row["result"]
        if row["status"] == "errore":
            raise JobError(row["result"] or "Il lavoro sul computer non è riuscito.")
        time.sleep(0.15)
    con.execute("UPDATE jobs SET status='errore', result=?, done_at=? WHERE id=? AND tenant_id=? AND status IN ('in_coda','in_corso')",
                ("Il computer non ha risposto in tempo.", db.now(), job_id, tenant_id))
    raise JobError("Il computer dell'azienda non ha risposto in tempo.")


def llm_via_device(con, tenant, user, body):
    """Chiede la risposta al modello che gira sul computer del cliente (Ollama). Ritorna la risposta Chat Completions."""
    if not online_device(con, tenant["id"], "modello"):
        raise JobError("Nessun computer dell'azienda ha un modello locale acceso (Ollama). Apri il launcher e controlla la verifica.")
    cur = con.execute("INSERT INTO jobs(tenant_id,agent_id,tool,package,place,args,requested_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                      (tenant["id"], "centrale", "llm_chat", "modello", "locale", json.dumps({"body": body}, ensure_ascii=False), user["id"], db.now()))
    try:
        return json.loads(wait(con, tenant["id"], cur.lastrowid, MODEL_WAIT))
    except ValueError:
        raise JobError("Il modello locale ha dato una risposta non leggibile.")


def _with_links(tenant_id, payload):
    """Per il computer del cliente: ogni file del piano viaggia con un collegamento firmato valido un'ora."""
    def link(asset):
        exp = int(time.time()) + 3600
        return dict(asset, url="/api/files/%d/content?e=%d&s=%s" % (asset["id"], exp, media._sign(asset["id"], tenant_id, exp)))
    plan = payload.get("plan")
    if isinstance(plan, dict):
        plan = dict(plan, items=[dict(i, asset=link(i["asset"])) for i in plan["items"]])
        if plan.get("music"):
            plan["music"] = link(plan["music"])
        return dict(payload, plan=plan)
    return payload


def claim(con, device, tenant, wait_seconds=0):
    """Il launcher chiede lavoro. Aspetta fino a `wait_seconds` così gli strumenti brevi partono subito."""
    tid, end = tenant["id"], time.time() + max(0, min(float(wait_seconds or 0), 20))
    ok = [k[4:] for k, v in json.loads(device["checks"]).items() if k.startswith("pkg:") and v.get("ok")]
    con.execute("UPDATE jobs SET status=CASE WHEN attempts>=? THEN 'errore' ELSE 'in_coda' END, device_id=NULL, "
                "result=CASE WHEN attempts>=? THEN 'Il computer ha smesso di rispondere durante il lavoro.' ELSE result END "
                "WHERE tenant_id=? AND place='locale' AND status='in_corso' AND claimed_at<?", (MAX_ATTEMPTS, MAX_ATTEMPTS, tid, int(time.time()) - STALE_JOB_SECONDS))
    while True:
        con.execute("UPDATE devices SET last_seen=? WHERE id=?", (int(time.time()), device["id"]))
        if tenant.get("stopped") or con.execute("SELECT stopped FROM tenants WHERE id=?", (tid,)).fetchone()["stopped"]:
            return None  # arresto d'emergenza: i lavori restano in coda
        for row in con.execute("SELECT * FROM jobs WHERE tenant_id=? AND place='locale' AND status='in_coda' ORDER BY id LIMIT 20", (tid,)):
            if row["package"] not in ok:
                continue
            if con.execute("UPDATE jobs SET status='in_corso', device_id=?, claimed_at=?, attempts=attempts+1 WHERE id=? AND tenant_id=? AND status='in_coda'",
                           (device["id"], int(time.time()), row["id"], tid)).rowcount == 1:
                return {"id": row["id"], "tool": row["tool"], "package": row["package"], "args": _with_links(tid, json.loads(row["args"]))}
        if time.time() >= end:
            return None
        time.sleep(0.2)


def _mine(con, device, job_id):
    row = con.execute("SELECT * FROM jobs WHERE id=? AND tenant_id=? AND device_id=? AND status='in_corso'",
                      (job_id, device["tenant_id"], device["id"])).fetchone()
    if not row:
        raise ApiError(404, "Lavoro non trovato o non assegnato a questo computer.")
    return row


def progress(con, device, job_id, pct, note=""):
    _mine(con, device, job_id)
    con.execute("UPDATE jobs SET progress=?, note=?, claimed_at=? WHERE id=? AND tenant_id=?",
                (max(0, min(int(pct or 0), 99)), str(note or "")[:120], int(time.time()), job_id, device["tenant_id"]))
    return {"ok": True}


def attach(con, device, job_id, asset_id):
    _mine(con, device, job_id)
    con.execute("UPDATE jobs SET asset_id=? WHERE id=? AND tenant_id=?", (asset_id, job_id, device["tenant_id"]))


def _finish(con, row, ok, text, asset_id=None):
    done = con.execute("UPDATE jobs SET status=?, result=?, progress=?, asset_id=COALESCE(?, asset_id), done_at=? WHERE id=? AND tenant_id=? AND status='in_corso'",
                       ("fatto" if ok else "errore", str(text or "")[:200000 if row["tool"] == "llm_chat" else 6000], 100 if ok else row["progress"],
                        asset_id, db.now(), row["id"], row["tenant_id"])).rowcount
    if done and row["tool"] != "llm_chat":
        audit.record(con, row["tenant_id"], row["requested_by"], "lavoro_finito",
                     {"id": row["id"], "strumento": row["tool"], "dove": row["place"], "esito": "ok" if ok else "errore"})
    if done and row["tool"] == "video_monta":
        news.add(con, row["tenant_id"], "video", "Video pronto (lavoro n. %d)" % row["id"] if ok else "Montaggio non riuscito (lavoro n. %d)" % row["id"],
                 str(text or "")[:400])
    return bool(done)


def result(con, device, job_id, ok, text):
    _finish(con, _mine(con, device, job_id), bool(ok), text)
    return {"ok": True}


def listing(con, tenant, limit=50):
    names = {r["id"]: r["name"] for r in con.execute("SELECT id, name FROM devices WHERE tenant_id=?", (tenant["id"],))}
    return [{"id": r["id"], "agent_id": r["agent_id"], "tool": r["tool"], "place": r["place"], "status": r["status"], "progress": r["progress"],
             "device": names.get(r["device_id"]), "result": r["result"][:600], "asset_id": r["asset_id"], "created_at": r["created_at"], "done_at": r["done_at"]}
            for r in con.execute("SELECT * FROM jobs WHERE tenant_id=? AND tool<>'llm_chat' ORDER BY id DESC LIMIT ?", (tenant["id"], limit))]


def running(con, tenant_id):
    """Lavori in corso o in coda, per agente: serve alla vista Ufficio."""
    out = {}
    for r in con.execute("SELECT j.*, d.name AS device_name FROM jobs j LEFT JOIN devices d ON d.id=j.device_id "
                         "WHERE j.tenant_id=? AND j.status IN ('in_coda','in_corso') AND j.tool<>'llm_chat' ORDER BY j.id", (tenant_id,)):
        cur = out.setdefault(r["agent_id"], {"queued": 0, "job": None})
        if r["status"] == "in_corso" and not cur["job"]:
            cur["job"] = {"id": r["id"], "tool": r["tool"], "progress": r["progress"], "place": r["place"], "device": r["device_name"]}
        elif r["status"] == "in_coda":
            cur["queued"] += 1
    return out


# ---------- lavori sul server («cloud») ----------

class CloudEnv:
    """Quello che un pacchetto vede quando gira sul server: i file dell'azienda si leggono direttamente dall'archivio."""
    config = {}

    def __init__(self, con, row, workdir):
        self.con, self.row, self.workdir = con, row, workdir

    def tool(self, name):
        return shutil.which(name)

    def fetch(self, asset):
        r = self.con.execute("SELECT stored_name FROM media_assets WHERE id=? AND tenant_id=?", (asset["id"], self.row["tenant_id"])).fetchone()
        if not r:
            raise JobError("Il file «%s» non è più in archivio." % asset.get("name"))
        return str(media.tenant_dir(self.row["tenant_id"]) / r["stored_name"])

    def progress(self, pct, note=""):
        self.con.execute("UPDATE jobs SET progress=?, note=?, claimed_at=? WHERE id=? AND tenant_id=?",
                         (max(0, min(int(pct), 99)), str(note)[:120], int(time.time()), self.row["id"], self.row["tenant_id"]))


def load_worker(name):
    spec = importlib.util.spec_from_file_location("sfb_pacchetto_" + name, str(PKG_DIR / name / "worker.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cloud_tick():
    """Esegue un lavoro in coda sul server. Ritorna l'id del lavoro eseguito, oppure None."""
    con = db.connect()
    try:
        con.execute("UPDATE jobs SET status='errore', result='Il server si è fermato durante il lavoro.' WHERE place='cloud' AND status='in_corso' AND claimed_at<?",
                    (int(time.time()) - STALE_JOB_SECONDS,))
        row = con.execute("SELECT j.* FROM jobs j JOIN tenants t ON t.id=j.tenant_id WHERE j.place='cloud' AND j.status='in_coda' AND t.stopped=0 AND t.active=1 "
                          "ORDER BY j.id LIMIT 1").fetchone()
        if not row or con.execute("UPDATE jobs SET status='in_corso', claimed_at=?, attempts=attempts+1 WHERE id=? AND status='in_coda'",
                                  (int(time.time()), row["id"])).rowcount != 1:
            return None
        row = dict(row)
        with tempfile.TemporaryDirectory(prefix="sfb-lavoro-") as tmp:
            try:
                worker = load_worker(row["package"])
                out = worker.TOOLS[row["tool"]](json.loads(row["args"]), CloudEnv(con, row, tmp))
                asset_id = None
                if out.get("file"):
                    tenant = dict(con.execute("SELECT * FROM tenants WHERE id=?", (row["tenant_id"],)).fetchone())
                    asset_id = media.store_path(con, tenant, row["requested_by"], out["file"], out.get("file_name") or "video.mp4")["id"]
                _finish(con, row, True, out.get("text") or "Fatto.", asset_id)
            except Exception as e:  # un lavoro non riuscito non deve fermare la coda
                known = isinstance(e, (JobError, ApiError)) or type(e).__name__ == "WorkerError"
                _finish(con, row, False, str(getattr(e, "message", e))[:600] if known else "Errore imprevisto: %s" % type(e).__name__)
        return row["id"]
    finally:
        con.close()


def start():
    global _thread
    if config.get("SFB_CLOUD_RUNNER", "1") == "0" or (_thread and _thread.is_alive()):
        return False
    _stop.clear()

    def loop():
        while not _stop.is_set():
            try:
                if not cloud_tick():
                    _stop.wait(2)
            except Exception:
                _stop.wait(5)

    _thread = threading.Thread(target=loop, name="sfb-lavori", daemon=True)
    _thread.start()
    return True


def stop():
    _stop.set()
