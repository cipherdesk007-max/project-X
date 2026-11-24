"""Catalogo dei pacchetti: unica fonte per schermata e server. I pacchetti sono cumulativi."""
from __future__ import annotations

import json

from . import config

_cache = None


def load():
    global _cache
    if _cache is None:
        data = json.loads((config.ROOT / "catalog.json").read_text(encoding="utf-8"))
        validate(data)
        _cache = data
    return _cache


def validate(data):
    agent_ids = [a["id"] for a in data["agents"]]
    assert len(agent_ids) == len(set(agent_ids)), "agenti duplicati nel catalogo"
    placed = [i for p in data["packages"] for i in p["adds"]]
    assert sorted(placed) == sorted(agent_ids), "ogni agente deve entrare in un solo pacchetto"
    assert len({p["id"] for p in data["packages"]}) == len(data["packages"]), "pacchetti duplicati"
    for a in data["agents"]:  # ogni agente ha tre richieste d'esempio, mostrate nella chat solo a chi tocca «Consigli»
        tries = a.get("tries") or []
        assert len(tries) == 3 and len(set(tries)) == 3 and all(isinstance(t, str) and t.strip() for t in tries), "agente %s: servono tre richieste d'esempio diverse" % a["id"]
    seen = set()
    for a in data.get("activities") or []:  # tipi di attività: agenti consigliati, contesto per gli agenti, richieste d'esempio
        assert a["id"] not in seen and a["id"] and a["name"], "attività duplicata o senza nome"
        seen.add(a["id"])
        assert a["for"] in ("azienda", "persona", "tutti"), "attività %s: «for» non valido" % a["id"]
        assert a["agents"] and set(a["agents"]) <= set(agent_ids) and len(set(a["agents"])) == len(a["agents"]), "attività %s: agenti sconosciuti o ripetuti" % a["id"]
        assert len(a["tries"]) == 3 and all(isinstance(t, str) and t.strip() for t in a["tries"]), "attività %s: servono tre richieste d'esempio" % a["id"]
    previous = {}
    for p in data["packages"]:  # i limiti non scendono mai passando a un pacchetto superiore
        for key, value in (p.get("limits") or {}).items():
            assert int(value) >= previous.get(key, 0), "limite %s decrescente" % key
            previous[key] = int(value)


def activities():
    return load().get("activities") or []


def activity(activity_id):
    """Il tipo di attività con questo nome, oppure None."""
    for a in activities():
        if a["id"] == activity_id:
            return a
    return None


def package_ids():
    return [p["id"] for p in load()["packages"]]


def package(package_id):
    for p in load()["packages"]:
        if p["id"] == package_id:
            return p
    return None


def entitled_agents(package_id):
    """Agenti inclusi nel pacchetto: i suoi più quelli di tutti i pacchetti precedenti."""
    out = set()
    for p in load()["packages"]:
        out.update(p["adds"])
        if p["id"] == package_id:
            return out
    return set()


def agent(agent_id):
    for a in load()["agents"]:
        if a["id"] == agent_id:
            return a
    return None


def first_package_with(agent_id):
    for p in load()["packages"]:
        if agent_id in p["adds"]:
            return p
    return None


def limit(package_id, key):
    """Limite numerico del pacchetto (es. automazioni, documenti). 0 se non previsto."""
    p = package(package_id)
    return int(((p or {}).get("limits") or {}).get(key, 0))
