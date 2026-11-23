"""Dati per la vista Ufficio 3D: stato vero di ogni agente acceso, e per un agente il suo lavoro recente.

Stati, in ordine di precedenza:
  fermo         arresto d'emergenza attivo per l'azienda
  lavora        in questo momento sta usando uno strumento
  ragiona       in questo momento aspetta la risposta del modello
  attesa        ha azioni in attesa dell'approvazione di un amministratore
  da_collegare  gli manca il collegamento al suo servizio
  pronto        acceso e fermo
  monta         un montaggio video è in corso (sul computer del cliente o sul server), con la percentuale
Lo stato «monta» sta tra «lavora» e «ragiona».
"""
from __future__ import annotations

import json

from . import catalog, db, live, local, media, team, tools
from .errors import ApiError

CENTRAL = "centrale"
# Bozze salvate prima che esistesse la colonna agent_id: l'agente si ricava dal canale.
LEGACY_CHANNEL = {"instagram": "instagram", "linkedin": "linkedin", "campagna": "marketing", "voce": "voce", "verbale": "ascolto"}


def _tool_label(name):
    return str(name or "").replace("_", " ")


def _state(agent_id, stopped, running, pending, status_label, jobs=None):
    if stopped:
        return "fermo", "Fermo: arresto d'emergenza"
    run = running.get(agent_id)
    if run and run["phase"] == "lavora":
        return "lavora", "Sta usando: %s" % _tool_label(run["tool"])
    mine = (jobs or {}).get(agent_id) or {"job": None, "queued": 0}
    if mine["job"]:
        where = "sul computer «%s»" % mine["job"]["device"] if mine["job"]["place"] == "locale" else "sui nostri server"
        if mine["job"]["tool"] == "video_monta":
            return "monta", "Sta montando un video: %d%% %s" % (mine["job"]["progress"], where)
        return "lavora", "Sta usando: %s %s" % (_tool_label(mine["job"]["tool"]), where)
    if run:
        return "ragiona", "Sta ragionando" + (" per un lavoro automatico" if run["source"] == "automazione" else "")
    if pending:
        return "attesa", "%d %s in attesa di approvazione" % (pending, "azione" if pending == 1 else "azioni")
    if status_label == "Da collegare":
        return "da_collegare", "Da collegare al suo servizio"
    if mine["queued"]:
        return "pronto", "%d %s in coda: %s" % (mine["queued"], "lavoro" if mine["queued"] == 1 else "lavori",
                                                 "aspetta il computer" if status_label == "Computer spento" else "parte tra poco")
    return "pronto", status_label or "Pronto"


def snapshot(con, tenant, agent_status):
    """Ritorna {"stopped", "central": {...}, "agents": [{id, name, station, tone, state, detail, pending}]} per gli agenti accesi."""
    tid, stopped = tenant["id"], bool(tenant.get("stopped"))
    running = live.snapshot(tid)
    pending = {r["agent_id"]: r["n"] for r in con.execute(
        "SELECT agent_id, COUNT(*) AS n FROM approvals WHERE tenant_id=? AND status='pending' GROUP BY agent_id", (tid,))}
    live_ids, jobs = team.active(con, tenant), local.running(con, tid)
    agents = []
    for a in catalog.load()["agents"]:
        if a["id"] not in live_ids:
            continue
        state, detail = _state(a["id"], stopped, running, pending.get(a["id"], 0), agent_status.get(a["id"], "Pronto"), jobs)
        agents.append({"id": a["id"], "name": a["name"], "station": a.get("station", "scrivania"), "tone": a["tone"],
                       "state": state, "detail": detail, "pending": pending.get(a["id"], 0)})
    state, detail = _state(CENTRAL, stopped, running, 0, "Coordina gli agenti attivi")
    return {"stopped": stopped, "central": {"id": CENTRAL, "name": "Centrale", "state": state, "detail": detail}, "agents": agents}


def _log(con, tenant, agent_id, limit=25):
    """Ultime attività dell'agente, ricavate dal registro: cosa ha fatto, non il contenuto dei messaggi."""
    tid, out = tenant["id"], []
    auto_agent = {r["id"]: (r["agent_id"], r["name"]) for r in con.execute("SELECT id, agent_id, name FROM automations WHERE tenant_id=?", (tid,))}
    for r in con.execute("SELECT action, detail, created_at FROM audit WHERE tenant_id=? ORDER BY id DESC LIMIT 400", (tid,)):
        d = json.loads(r["detail"])
        tool = tools.BY_NAME.get(d.get("strumento") or "")
        owner = tool["agent"] if tool else None
        text = None
        if r["action"] == "strumento_eseguito" and owner == agent_id:
            text = "Ha usato: %s" % _tool_label(d["strumento"])
        elif r["action"] == "strumento_errore" and owner == agent_id:
            text = "Errore con %s: %s" % (_tool_label(d["strumento"]), str(d.get("errore") or "")[:140])
        elif r["action"] == "approvazione_richiesta" and owner == agent_id:
            text = "Ha chiesto l'approvazione n. %s (%s)" % (d.get("id"), _tool_label(d["strumento"]))
        elif r["action"] in ("approvazione_eseguita", "approvazione_fallita", "approvazione_rifiutata") and owner == agent_id:
            text = "Azione n. %s %s" % (d.get("id"), {"approvazione_eseguita": "approvata ed eseguita", "approvazione_fallita": "approvata ma fallita",
                                                     "approvazione_rifiutata": "rifiutata"}[r["action"]])
        elif r["action"] == "lavoro_finito" and owner == agent_id:
            text = "Lavoro n. %s %s %s" % (d.get("id"), "finito" if d.get("esito") == "ok" else "non riuscito",
                                           "sul computer dell'azienda" if d.get("dove") == "locale" else "sui nostri server")
        elif r["action"] == "chat" and d.get("agente") == agent_id:
            text = "Ha risposto in chat con %s" % d.get("modello")
        elif r["action"] == "automazione_eseguita" and auto_agent.get(d.get("id"), (None, ""))[0] == agent_id:
            text = "Lavoro automatico «%s»: %s" % (auto_agent[d["id"]][1], "fatto" if d.get("esito") == "ok" else "errore")
        elif r["action"] == "agente_impostato" and d.get("agente") == agent_id:
            text = ("Istruzioni aggiornate" if d.get("attivo") else "Spento, istruzioni aggiornate") if d.get("istruzioni_cambiate") \
                else ("Acceso nel team" if d.get("attivo") else "Spento")
        elif r["action"] == "agente_bloccato" and d.get("agente") == agent_id:
            text = "Richiesta rifiutata: agente non disponibile"
        if text:
            out.append({"when": r["created_at"], "text": text})
            if len(out) >= limit:
                break
    return out


def _outputs(con, tenant, agent_id):
    """Ultimi elaborati dell'agente: bozze di testo, lead, appuntamenti. Gli agenti non producono ancora file multimediali."""
    tid, out = tenant["id"], []
    legacy = [c for c, a in LEGACY_CHANNEL.items() if a == agent_id]
    sql, params = "SELECT * FROM drafts WHERE tenant_id=? AND (agent_id=?", [tid, agent_id]
    if legacy:
        sql += " OR (agent_id IS NULL AND channel IN (%s))" % ",".join("?" * len(legacy))
        params += legacy
    elif agent_id == "contenuti":
        sql += " OR (agent_id IS NULL AND channel NOT IN (%s))" % ",".join("?" * len(LEGACY_CHANNEL))
        params += list(LEGACY_CHANNEL)
    for r in con.execute(sql + ") ORDER BY id DESC LIMIT 5", params):
        out.append({"kind": "bozza", "title": r["title"], "label": r["channel"], "text": r["body"][:600], "when": r["created_at"]})
    for r in con.execute("SELECT m.*, j.result, j.done_at, j.place FROM jobs j JOIN media_assets m ON m.id=j.asset_id AND m.tenant_id=j.tenant_id "
                         "WHERE j.tenant_id=? AND j.agent_id=? AND j.status='fatto' ORDER BY j.id DESC LIMIT 4", (tid, agent_id)):
        out.append({"kind": r["kind"], "title": r["original_name"], "label": "sul computer" if r["place"] == "locale" else "sui nostri server",
                    "text": r["result"][:300], "when": r["done_at"], "url": media._public(r)["url"], "mime": r["mime"]})
    if agent_id == "crm":
        for r in con.execute("SELECT * FROM leads WHERE tenant_id=? ORDER BY id DESC LIMIT 5", (tid,)):
            out.append({"kind": "lead", "title": r["name"], "label": r["status"], "text": " · ".join(x for x in (r["company"], r["email"], r["phone"], r["notes"]) if x)[:300],
                        "when": r["created_at"]})
    if agent_id == "calendario":
        for r in con.execute("SELECT * FROM events WHERE tenant_id=? ORDER BY starts_at DESC LIMIT 5", (tid,)):
            out.append({"kind": "evento", "title": r["title"], "label": r["starts_at"].replace("T", " "), "text": " · ".join(x for x in (r["location"], r["notes"]) if x)[:300],
                        "when": r["created_at"]})
    return out


def agent_detail(con, tenant, user, agent_id, agent_status):
    """Stato, attività recenti, elaborati e azioni in attesa di un agente acceso (o del Centrale)."""
    snap = snapshot(con, tenant, agent_status)
    if agent_id == CENTRAL:
        me = snap["central"]
    else:
        if not catalog.agent(agent_id):
            raise ApiError(404, "Agente sconosciuto.")
        found = [a for a in snap["agents"] if a["id"] == agent_id]
        if not found:
            raise ApiError(404, "L'agente non è attivo nel team dell'azienda.")
        me = found[0]
    sql, params = ("SELECT a.id, a.summary, a.created_at, u.name AS requested_by_name FROM approvals a JOIN users u ON u.id=a.requested_by "
                   "WHERE a.tenant_id=? AND a.status='pending' AND a.agent_id=?"), [tenant["id"], agent_id]
    if user["role"] != "admin":  # come nella scheda Approvazioni: un operatore vede solo le proprie richieste
        sql += " AND a.requested_by=?"
        params.append(user["id"])
    return {"agent": me, "log": _log(con, tenant, agent_id), "outputs": _outputs(con, tenant, agent_id) if agent_id != CENTRAL else [],
            "pending": db.rows(con.execute(sql + " ORDER BY a.id DESC LIMIT 10", params))}
