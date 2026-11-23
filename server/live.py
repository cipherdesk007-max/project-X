"""Attività in corso degli agenti, in memoria: chi sta ragionando e chi sta usando uno strumento, adesso.

Serve alla vista Ufficio 3D. Non è un registro (quello è `audit`): dura quanto la richiesta in corso e
sparisce al riavvio. Vale per un solo processo del server.
"""
from __future__ import annotations

import itertools
import os
import threading
import time

STALE_SECONDS = 600
_lock = threading.Lock()
_runs = {}  # tenant_id -> {run_id: {...}}
_ids = itertools.count(1)


def begin(tenant_id, agent_id, source="chat"):
    """Un agente inizia a lavorare a una richiesta. Ritorna l'identificativo da passare a phase() e end()."""
    run_id = next(_ids)
    with _lock:
        _runs.setdefault(tenant_id, {})[run_id] = {"agent": agent_id, "phase": "ragiona", "tool": None, "target": None,
                                                    "since": time.time(), "source": source}
    return run_id


def phase(tenant_id, run_id, name, tool=None, target=None):
    """Cambia fase: 'ragiona' (aspetta il modello) oppure 'lavora' (sta usando lo strumento `tool` dell'agente `target`)."""
    with _lock:
        run = _runs.get(tenant_id, {}).get(run_id)
        if run:
            run.update(phase=name, tool=tool, target=target)


def demo_pause():
    """Solo in modalità demo: gli strumenti locali durano un istante, quelli veri (posta, negozio, ricerca) secondi.
    Questa pausa finta, la stessa del modello finto, rende visibile lo stato «al lavoro». Con i modelli veri non fa nulla."""
    if os.environ.get("SFB_DEMO", "0") == "1":
        ms = os.environ.get("SFB_DEMO_DELAY_MS", "900")
        if ms.isdigit() and int(ms):
            time.sleep(min(int(ms), 10000) / 1000.0)


def end(tenant_id, run_id):
    with _lock:
        runs = _runs.get(tenant_id)
        if runs:
            runs.pop(run_id, None)
            if not runs:
                _runs.pop(tenant_id, None)


def snapshot(tenant_id):
    """Stato in corso per agente: {agent_id: {"phase", "tool", "source", "since"}}. 'lavora' prevale su 'ragiona'."""
    now, out = time.time(), {}
    with _lock:
        runs = _runs.get(tenant_id, {})
        for run_id in [k for k, v in runs.items() if v["since"] < now - STALE_SECONDS]:
            runs.pop(run_id, None)  # una richiesta rimasta appesa non lascia un agente «al lavoro» per sempre
        for run in runs.values():
            entries = [(run["agent"], "ragiona", None)]
            if run["phase"] == "lavora" and run["target"]:
                entries.append((run["target"], "lavora", run["tool"]))
            for agent_id, what, tool in entries:
                cur = out.get(agent_id)
                if cur is None or (what == "lavora" and cur["phase"] != "lavora"):
                    out[agent_id] = {"phase": what, "tool": tool, "source": run["source"], "since": run["since"]}
    return out


def reset():
    with _lock:
        _runs.clear()
