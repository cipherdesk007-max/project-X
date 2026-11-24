"""Team di agenti scelto da ogni azienda.

Il pacchetto non decide più quali agenti sono accesi: è solo il tetto massimo (`ceiling`).
Dentro quel tetto ogni amministratore accende e spegne i singoli agenti. Un agente è utilizzabile
solo se è nel tetto E ha una riga attiva in `tenant_agents`: tutto il server passa da `active()`.
"""
from __future__ import annotations

import json

from . import audit, catalog, db
from .errors import ApiError

MAX_INSTRUCTIONS = 1000
CONFIG_KEYS = {"istruzioni"}
NOTES_BUDGET = 2000


def ceiling(tenant):
    """Agenti che il pacchetto dell'azienda permette, al massimo."""
    return catalog.entitled_agents(tenant["package_id"])


def active(con, tenant):
    """Agenti accesi: scelti dall'azienda e ancora dentro il tetto del pacchetto."""
    rows = con.execute("SELECT agent_id FROM tenant_agents WHERE tenant_id=? AND is_active=1", (tenant["id"],))
    return ceiling(tenant) & {r["agent_id"] for r in rows}


def modes(con, tenant):
    """Dove gira ogni agente dell'azienda: {agent_id: 'cloud' | 'locale'}."""
    return {r["agent_id"]: r["mode"] for r in con.execute("SELECT agent_id, mode FROM tenant_agents WHERE tenant_id=?", (tenant["id"],))}


def models_for(con, tenant):
    have = active(con, tenant)
    return [m for m in catalog.load()["models"] if not m.get("requires_agent") or m["requires_agent"] in have]


def init_tenant(con, tenant_id, package_id, chosen=None):
    """Crea una riga per ogni agente del tetto. `chosen` None = tutti accesi; altrimenti solo quelli indicati."""
    for agent_id in sorted(catalog.entitled_agents(package_id)):
        on = 1 if chosen is None or agent_id in chosen else 0
        con.execute("INSERT OR IGNORE INTO tenant_agents(tenant_id,agent_id,is_active,updated_at) VALUES(?,?,?,?)",
                    (tenant_id, agent_id, on, db.now()))


def on_package_change(con, tenant_id, package_id):
    """Gli agenti che entrano nel tetto per la prima volta partono accesi; le scelte già fatte non si toccano."""
    init_tenant(con, tenant_id, package_id)


def set_activity(con, tenant, user, activity_id, turn_on=False):
    """Cambia il tipo di attività dell'azienda. Con `turn_on` accende anche gli agenti consigliati che stanno nel suo pacchetto
    (non ne spegne nessuno). Ritorna {"activity", "turned_on": [nomi]}."""
    activity_id = str(activity_id or "")
    spec = catalog.activity(activity_id)
    if activity_id and not spec:
        raise ApiError(400, "Tipo di attività sconosciuto.")
    con.execute("UPDATE tenants SET activity=? WHERE id=?", (activity_id, tenant["id"]))
    audit.record(con, tenant["id"], user["id"], "attivita_impostata", {"attivita": activity_id})
    turned = []
    if turn_on and spec:
        have, top = active(con, tenant), ceiling(tenant)
        todo = [a for a in spec["agents"] if a in top and a not in have]
        if todo:
            set_agents(con, tenant, user, [{"agent_id": a, "is_active": True} for a in todo])
            turned = [catalog.agent(a)["name"] for a in todo]
    return {"activity": activity_id, "turned_on": turned}


def backfill(con):
    """Aziende nate prima di questa versione: nessuna riga, quindi tutti gli agenti del loro pacchetto accesi come prima."""
    rows = con.execute("SELECT id, package_id FROM tenants WHERE id NOT IN (SELECT DISTINCT tenant_id FROM tenant_agents)").fetchall()
    for r in rows:
        if catalog.package(r["package_id"]):
            init_tenant(con, r["id"], r["package_id"])


def clean_config(cfg):
    if cfg is None:
        return None
    if not isinstance(cfg, dict) or set(cfg) - CONFIG_KEYS:
        raise ApiError(400, "Configurazione dell'agente non valida. Chiavi ammesse: %s." % ", ".join(sorted(CONFIG_KEYS)))
    text = str(cfg.get("istruzioni") or "").replace("\x00", "").strip()
    if len(text) > MAX_INSTRUCTIONS:
        raise ApiError(400, "Le istruzioni per l'agente sono troppo lunghe (massimo %d caratteri)." % MAX_INSTRUCTIONS)
    return {"istruzioni": text} if text else {}


def listing(con, tenant):
    top = ceiling(tenant)
    rows = {r["agent_id"]: r for r in con.execute("SELECT * FROM tenant_agents WHERE tenant_id=?", (tenant["id"],))}
    out = []
    for a in catalog.load()["agents"]:
        row = rows.get(a["id"])
        inside = a["id"] in top
        out.append({"agent_id": a["id"], "name": a["name"], "in_ceiling": inside,
                    "is_active": bool(inside and row and row["is_active"]),
                    "config": json.loads(row["config_json"]) if row else {},
                    "mode": "locale" if not a["run"]["cloud"] else (row["mode"] if row else "cloud"), "run": a["run"],
                    "available_from": catalog.first_package_with(a["id"])["name"]})
    return out


def set_agents(con, tenant, user, changes):
    """Applica più modifiche insieme: prima le controlla tutte, poi scrive. Ritorna l'elenco aggiornato."""
    if not isinstance(changes, list) or not changes or len(changes) > 100:
        raise ApiError(400, "Indica almeno un agente da modificare.")
    top, todo = ceiling(tenant), []
    for ch in changes:
        if not isinstance(ch, dict):
            raise ApiError(400, "Modifica non valida.")
        agent_id = str(ch.get("agent_id") or "")
        spec = catalog.agent(agent_id)
        if not spec:
            raise ApiError(404, "Agente sconosciuto: %s." % agent_id[:40])
        if agent_id not in top:
            raise ApiError(403, "L'Agente %s supera il limite del pacchetto dell'azienda: entra con il pacchetto %s." % (
                spec["name"], catalog.first_package_with(agent_id)["name"]))
        if "is_active" not in ch and "config" not in ch and "mode" not in ch:
            raise ApiError(400, "Indica is_active, config oppure mode.")
        if "mode" in ch:
            local = spec["run"].get("local")
            if ch["mode"] not in ("cloud", "locale"):
                raise ApiError(400, "mode deve essere «cloud» oppure «locale».")
            if ch["mode"] == "locale" and not (local and local.get("ready")):
                raise ApiError(400, "L'Agente %s non ha ancora una versione per il computer: gira sui nostri server." % spec["name"])
            if ch["mode"] == "cloud" and not spec["run"]["cloud"]:
                raise ApiError(400, "L'Agente %s gira solo sul computer dell'azienda." % spec["name"])
        if "is_active" in ch and not isinstance(ch["is_active"], bool):
            raise ApiError(400, "is_active deve essere vero o falso.")
        todo.append((agent_id, ch.get("is_active"), clean_config(ch["config"]) if "config" in ch else None, ch.get("mode")))
    for agent_id, is_active, cfg, mode in todo:
        row = con.execute("SELECT * FROM tenant_agents WHERE tenant_id=? AND agent_id=?", (tenant["id"], agent_id)).fetchone()
        new_active = int(is_active) if is_active is not None else (row["is_active"] if row else 0)
        new_cfg = json.dumps(cfg, ensure_ascii=False) if cfg is not None else (row["config_json"] if row else "{}")
        new_mode = mode or (row["mode"] if row else "cloud")
        con.execute("INSERT INTO tenant_agents(tenant_id,agent_id,is_active,config_json,updated_at,mode) VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(tenant_id,agent_id) DO UPDATE SET is_active=excluded.is_active, config_json=excluded.config_json, "
                    "updated_at=excluded.updated_at, mode=excluded.mode", (tenant["id"], agent_id, new_active, new_cfg, db.now(), new_mode))
        audit.record(con, tenant["id"], user["id"], "agente_impostato",
                     {"agente": agent_id, "attivo": bool(new_active), "istruzioni_cambiate": cfg is not None, "dove": new_mode})
    return listing(con, tenant)


def notes(con, tenant, agent_ids):
    """Istruzioni dell'azienda per gli agenti indicati, da aggiungere alle istruzioni del modello."""
    live = active(con, tenant)
    lines, used = [], 0
    for r in con.execute("SELECT agent_id, config_json FROM tenant_agents WHERE tenant_id=? ORDER BY agent_id", (tenant["id"],)):
        if r["agent_id"] not in agent_ids or r["agent_id"] not in live:
            continue
        text = (json.loads(r["config_json"]) or {}).get("istruzioni")
        if not text or used + len(text) > NOTES_BUDGET:
            continue
        lines.append("- Agente %s: %s" % (catalog.agent(r["agent_id"])["name"], text))
        used += len(text)
    return ("Istruzioni dell'azienda per gli agenti, scritte da un amministratore:\n" + "\n".join(lines)) if lines else ""
