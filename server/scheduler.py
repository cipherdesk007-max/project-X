"""Lavori automatici: un agente esegue una richiesta salvata, ogni giorno a un'ora o ogni N minuti.

Regole:
- Le azioni verso l'esterno di un lavoro automatico NON partono: finiscono nelle approvazioni, come in chat.
- Il numero di lavori dipende dal pacchetto. Intervallo minimo: 15 minuti.
- Dopo 3 errori di fila il lavoro va in pausa e compare un avviso nelle novità.
- Se il server è stato spento, al riavvio ogni lavoro scaduto parte una volta sola, non una per ogni giro perso.
- Con l'arresto d'emergenza attivo non parte nulla.
"""
from __future__ import annotations

import sys
import threading
import time
import traceback
from datetime import timedelta

from . import agents, audit, catalog, clock, config, db, news, team
from .errors import ApiError

MIN_EVERY = 15
MAX_EVERY = 7 * 24 * 60
MAX_FAILS = 3
DAYS = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]

_stop = threading.Event()
_thread = None


def next_run(kind, hour, minute, weekdays, every_minutes, now=None):
    """Prossima esecuzione, in secondi Unix."""
    now = time.time() if now is None else now
    if kind == "interval":
        return int(now + int(every_minutes) * 60)
    days = {int(d) for d in str(weekdays or "").split(",") if d.strip().isdigit()}
    here = clock.local(now)
    base = here.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    for i in range(9):
        candidate = base + timedelta(days=i)
        if candidate > here and (not days or candidate.weekday() in days):
            return int(candidate.timestamp())
    return int(now + 86400)


def _int(value, lo, hi, what):
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ApiError(400, "%s non valido." % what)
    if not lo <= n <= hi:
        raise ApiError(400, "%s deve essere tra %d e %d." % (what, lo, hi))
    return n


def _clean(con, tenant, body, current=None):
    cur = dict(current or {})
    get = lambda k, d=None: body[k] if k in body else cur.get(k, d)  # noqa: E731
    name = str(get("name") or "").strip()
    prompt = str(get("prompt") or "").strip()
    if not 1 <= len(name) <= 80:
        raise ApiError(400, "Il nome è obbligatorio (massimo 80 caratteri).")
    if not 1 <= len(prompt) <= 4000:
        raise ApiError(400, "La richiesta per l'agente è obbligatoria (massimo 4000 caratteri).")
    agent_id = str(get("agent_id") or agents.CENTRAL)
    if agent_id != agents.CENTRAL and agent_id not in team.active(con, tenant):
        raise ApiError(403, "Questo agente non è attivo nel team dell'azienda.")
    model = str(get("model") or "")
    if model not in [m["id"] for m in team.models_for(con, tenant)]:
        raise ApiError(403, "Questo modello non è disponibile per l'azienda.")
    kind = str(get("kind") or "daily")
    out = {"name": name, "prompt": prompt, "agent_id": agent_id, "model": model, "kind": kind,
           "hour": None, "minute": None, "weekdays": "", "every_minutes": None}
    if kind == "interval":
        out["every_minutes"] = _int(get("every_minutes"), MIN_EVERY, MAX_EVERY, "L'intervallo in minuti")
    elif kind == "daily":
        out["hour"] = _int(get("hour"), 0, 23, "L'ora")
        out["minute"] = _int(get("minute", 0), 0, 59, "Il minuto")
        days = get("weekdays") or []
        if isinstance(days, str):
            days = [d for d in days.split(",") if d.strip()]
        out["weekdays"] = ",".join(str(d) for d in sorted({_int(d, 0, 6, "Il giorno") for d in days}))
    else:
        raise ApiError(400, "Tipo di orario non valido.")
    return out


def describe(row):
    if row["kind"] == "interval":
        return "ogni %d minuti" % row["every_minutes"]
    days = [DAYS[int(d)] for d in str(row["weekdays"] or "").split(",") if d.strip().isdigit()]
    return "%s alle %02d:%02d" % (", ".join(days) if days else "ogni giorno", row["hour"], row["minute"])


def listing(con, tenant):
    rows = db.rows(con.execute("SELECT * FROM automations WHERE tenant_id=? ORDER BY id", (tenant["id"],)))
    for r in rows:
        r["schedule"] = describe(r)
        r["next_run"] = clock.local(r["next_run_at"]).strftime("%Y-%m-%d %H:%M") if r["active"] else None
        r["weekdays"] = [int(d) for d in str(r["weekdays"] or "").split(",") if d.strip().isdigit()]
    return {"automations": rows, "limit": catalog.limit(tenant["package_id"], "automazioni"),
            "timezone": clock.tz_name(), "min_every": MIN_EVERY}


def create(con, tenant, user, body):
    limit = catalog.limit(tenant["package_id"], "automazioni")
    have = con.execute("SELECT COUNT(*) FROM automations WHERE tenant_id=?", (tenant["id"],)).fetchone()[0]
    if have >= limit:
        raise ApiError(403, "Il pacchetto %s permette al massimo %d lavori automatici." % (
            catalog.package(tenant["package_id"])["name"], limit))
    c = _clean(con, tenant, body)
    cur = con.execute(
        "INSERT INTO automations(tenant_id,name,agent_id,model,prompt,kind,hour,minute,weekdays,every_minutes,next_run_at,created_by,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tenant["id"], c["name"], c["agent_id"], c["model"], c["prompt"], c["kind"], c["hour"], c["minute"], c["weekdays"],
         c["every_minutes"], next_run(c["kind"], c["hour"], c["minute"], c["weekdays"], c["every_minutes"]), user["id"], db.now()))
    audit.record(con, tenant["id"], user["id"], "automazione_creata", {"id": cur.lastrowid, "nome": c["name"]})
    return cur.lastrowid


def _active(con, tenant_id, but=None):
    return con.execute("SELECT COUNT(*) FROM automations WHERE tenant_id=? AND active=1 AND id!=?", (tenant_id, but or 0)).fetchone()[0]


def enforce_limit(con, tenant_id, package_id):
    """Dopo un cambio di pacchetto: restano attivi i lavori più vecchi fino al limite del pacchetto, gli altri vanno in pausa
    (non vengono cancellati). Ritorna gli id messi in pausa."""
    limit = catalog.limit(package_id, "automazioni")
    extra = [r["id"] for r in con.execute("SELECT id FROM automations WHERE tenant_id=? AND active=1 ORDER BY id", (tenant_id,))][limit:]
    if extra:
        con.execute("UPDATE automations SET active=0 WHERE tenant_id=? AND id IN (%s)" % ",".join("?" * len(extra)), [tenant_id] + extra)
        news.add(con, tenant_id, "avviso", "%d lavori automatici messi in pausa" % len(extra),
                 "Il pacchetto %s permette %d lavori automatici attivi. Quelli in più sono in pausa, non cancellati: "
                 "puoi scegliere quali tenere attivi dalla scheda Automazioni." % (catalog.package(package_id)["name"], limit))
        audit.record(con, tenant_id, None, "automazioni_in_pausa", {"quante": len(extra), "limite": limit})
    return extra


def _get(con, tenant, automation_id):
    row = con.execute("SELECT * FROM automations WHERE id=? AND tenant_id=?", (automation_id, tenant["id"])).fetchone()
    if not row:
        raise ApiError(404, "Lavoro automatico non trovato.")
    return dict(row)


def update(con, tenant, user, automation_id, body):
    row = _get(con, tenant, automation_id)
    c = _clean(con, tenant, body, row)
    active = int(bool(body["active"])) if "active" in body else row["active"]
    limit = catalog.limit(tenant["package_id"], "automazioni")
    if active and not row["active"] and _active(con, tenant["id"], automation_id) >= limit:
        raise ApiError(403, "Il pacchetto %s permette al massimo %d lavori automatici attivi: mettine in pausa un altro prima." % (
            catalog.package(tenant["package_id"])["name"], limit))
    fail_count = 0 if (active and not row["active"]) else row["fail_count"]
    con.execute(
        "UPDATE automations SET name=?, agent_id=?, model=?, prompt=?, kind=?, hour=?, minute=?, weekdays=?, every_minutes=?, "
        "active=?, fail_count=?, next_run_at=? WHERE id=? AND tenant_id=?",
        (c["name"], c["agent_id"], c["model"], c["prompt"], c["kind"], c["hour"], c["minute"], c["weekdays"], c["every_minutes"],
         active, fail_count, next_run(c["kind"], c["hour"], c["minute"], c["weekdays"], c["every_minutes"]), automation_id, tenant["id"]))
    audit.record(con, tenant["id"], user["id"], "automazione_modificata", {"id": automation_id, "attiva": bool(active)})


def delete(con, tenant, user, automation_id):
    _get(con, tenant, automation_id)
    con.execute("DELETE FROM automations WHERE id=? AND tenant_id=?", (automation_id, tenant["id"]))
    audit.record(con, tenant["id"], user["id"], "automazione_rimossa", {"id": automation_id})


def _runner(con, row):
    """Utente a nome del quale gira il lavoro: chi l'ha creato, se è ancora attivo, altrimenti un amministratore attivo."""
    u = con.execute("SELECT * FROM users WHERE id=? AND tenant_id=? AND active=1", (row["created_by"], row["tenant_id"])).fetchone()
    if not u:
        u = con.execute("SELECT * FROM users WHERE tenant_id=? AND role='admin' AND active=1 ORDER BY id LIMIT 1", (row["tenant_id"],)).fetchone()
    return dict(u) if u else None


def run_one(con, row, manual=False):
    """Esegue un lavoro. Ritorna (stato, testo)."""
    tid = row["tenant_id"]
    tenant = con.execute("SELECT * FROM tenants WHERE id=? AND active=1", (tid,)).fetchone()
    user = _runner(con, row)
    try:
        if not tenant or not user:
            raise ApiError(409, "Azienda sospesa o senza utenti attivi.")
        res = agents.run_chat(con, dict(tenant), user, row["agent_id"], row["model"], None, row["prompt"], source="automazione")
        status, output = "ok", res["reply"]
        waiting = [e for e in res["events"] if e.get("approval_id")]
        if waiting:
            output += "\n\n%d azione/i in attesa di approvazione." % len(waiting)
    except ApiError as e:
        status, output = "errore", e.message
    except Exception as e:
        traceback.print_exc()
        status, output = "errore", "Errore imprevisto: %s" % type(e).__name__
    fails = 0 if status == "ok" else row["fail_count"] + 1
    paused = status != "ok" and fails >= MAX_FAILS and not manual
    con.execute("UPDATE automations SET last_run_at=?, last_status=?, last_output=?, fail_count=?, active=CASE WHEN ? THEN 0 ELSE active END "
                "WHERE id=? AND tenant_id=?", (db.now(), status, output[:2000], fails, 1 if paused else 0, row["id"], tid))
    news.add(con, tid, "automazione", "%s: %s" % (row["name"], "fatto" if status == "ok" else "errore"), output[:2000])
    if paused:
        news.add(con, tid, "avviso", "Lavoro automatico «%s» messo in pausa" % row["name"],
                 "Ha dato errore %d volte di fila. Ultimo errore: %s" % (MAX_FAILS, output[:500]))
    audit.record(con, tid, user["id"] if user else None, "automazione_eseguita",
                 {"id": row["id"], "esito": status, "manuale": bool(manual), "messa_in_pausa": paused})
    return status, output


def run_now(con, tenant, automation_id):
    return run_one(con, _get(con, tenant, automation_id), manual=True)


def tick(now=None, _between=None):
    """Esegue i lavori scaduti. Ritorna quanti ne ha eseguiti. Si può chiamare da più processi: ogni lavoro
    viene preso una volta sola. `_between` serve solo alle prove: gira tra la lettura e la prenotazione."""
    now = time.time() if now is None else now
    done = 0
    con = db.connect()
    try:
        due = db.rows(con.execute(
            "SELECT a.* FROM automations a JOIN tenants t ON t.id=a.tenant_id "
            "WHERE a.active=1 AND a.next_run_at<=? AND t.active=1 AND t.stopped=0 ORDER BY a.next_run_at LIMIT 20", (int(now),)))
        if _between:
            _between()
        for row in due:
            nxt = next_run(row["kind"], row["hour"], row["minute"], row["weekdays"], row["every_minutes"], now)
            claimed = con.execute("UPDATE automations SET next_run_at=? WHERE id=? AND next_run_at=? AND active=1",
                                  (nxt, row["id"], row["next_run_at"]))
            if claimed.rowcount != 1:
                continue
            run_one(con, row)
            done += 1
    finally:
        con.close()
    return done


def reschedule_all(con, tenant_id):
    """Dopo un arresto d'emergenza: riparte dal prossimo orario, senza recuperare i giri saltati."""
    for row in db.rows(con.execute("SELECT * FROM automations WHERE tenant_id=? AND active=1", (tenant_id,))):
        con.execute("UPDATE automations SET next_run_at=? WHERE id=? AND tenant_id=?",
                    (next_run(row["kind"], row["hour"], row["minute"], row["weekdays"], row["every_minutes"]), row["id"], tenant_id))


def reminders():
    """Avvisi di scadenza per email. Basta un giro all'ora: `billing.remind` manda una sola email per scadenza."""
    from . import billing
    con = db.connect()
    try:
        return billing.remind(con)
    finally:
        con.close()


def _loop(poll):
    last = 0.0
    while not _stop.wait(poll):
        try:
            tick()
            if time.time() - last >= 3600:
                last = time.time()
                reminders()
        except Exception:
            traceback.print_exc(file=sys.stderr)


def start():
    """Avvia il controllo periodico in un thread. SFB_SCHEDULER=0 lo disattiva."""
    global _thread
    if config.get("SFB_SCHEDULER", "1") != "1" or (_thread and _thread.is_alive()):
        return False
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(max(2, config.get_int("SFB_SCHEDULER_POLL", 20)),), daemon=True)
    _thread.start()
    return True


def stop():
    _stop.set()
