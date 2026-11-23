"""Prezzi dell'abbonamento, tutti decisi dal gestore nel pannello (gruppo «Prezzi»), in euro interi al mese.

Regola: prezzo del pacchetto + prezzo per ogni agente acceso. Con il prezzo per agente a 0 conta solo il pacchetto;
con i prezzi dei pacchetti a 0 e quello per agente sopra zero si paga solo per gli agenti che si usano.
Se nessun prezzo è impostato qui, valgono gli ID dei prezzi creati a mano su Stripe (STRIPE_PRICE_…), come prima.
"""
from __future__ import annotations

from . import catalog, config, team


def package_price(package_id):
    return max(0, config.get_int("PRICE_" + str(package_id).upper(), 0))


def per_agent():
    return max(0, config.get_int("PRICE_PER_AGENT", 0))


def configured(package_id):
    return package_price(package_id) > 0 or per_agent() > 0


def quote(con, tenant):
    """Quanto paga al mese questa azienda con gli agenti accesi adesso. Importi in euro interi; `cents` per Stripe."""
    base, each = package_price(tenant["package_id"]), per_agent()
    agents = len(team.active(con, tenant)) if each else 0
    total = base + each * agents
    return {"configured": configured(tenant["package_id"]), "base": base, "per_agent": each, "agents": agents, "total": total, "cents": total * 100, "currency": "eur"}


def public():
    """Prezzi da mostrare sul sito: solo quelli che il gestore ha impostato."""
    return {"per_agent": per_agent(), "packages": {p["id"]: package_price(p["id"]) for p in catalog.load()["packages"]}}
