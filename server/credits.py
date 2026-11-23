"""Crediti: quanto un'azienda può usare i modelli pagati dalla piattaforma.

Regole, tutte decise dal gestore nel pannello (gruppo «Crediti»); finché SFB_CREDITS non è 1 non cambia niente:
- ogni modello ha un costo in crediti ogni 1.000 token (Kimi, Claude, ChatGPT costano diverso; il modello locale non costa);
- ogni pacchetto include dei crediti al mese, che si ricaricano il primo giorno del mese (UTC) e non si accumulano;
- i crediti comprati a parte o regalati dal gestore non scadono e si usano solo dopo aver finito quelli inclusi;
- chi usa una propria chiave del modello non consuma crediti: paga già il suo fornitore.
Ogni movimento è una riga di `credit_ledger`: il saldo si ricalcola sempre dalle righe, non è un numero tenuto a parte.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone

from . import audit, catalog, config, db, news
from .errors import ApiError

OWN_KEY = {"claude": "anthropic_api_key", "kimi": "moonshot_api_key", "chatgpt": "openai_api_key"}


def enabled():
    return config.get("SFB_CREDITS", "0") == "1"


def rate(model_id):
    """Crediti ogni 1.000 token. Il modello locale non costa mai."""
    if model_id == "locale" or model_id not in OWN_KEY:
        return 0
    return max(0, config.get_int("CREDIT_RATE_" + model_id.upper(), 1))


def included(tenant):
    return max(0, config.get_int("CREDITS_" + str(tenant["package_id"]).upper(), 0))


def period(now=None):
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


def resets_on(now=None):
    d = (now or datetime.now(timezone.utc)).date()
    return (date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)).isoformat()


def applies(tenant, model_id, tenant_keys=None):
    """Vero se questa chiamata consuma crediti: sistema acceso, modello a pagamento, chiave della piattaforma."""
    return enabled() and rate(model_id) > 0 and not (tenant_keys or {}).get(OWN_KEY.get(model_id, ""))


def balance(con, tenant, now=None):
    """{"included", "included_left", "extra", "available", "resets_on"}: inclusi nel mese, comprati o regalati, totale."""
    tid, month = tenant["id"], period(now)
    used = con.execute("SELECT COALESCE(SUM(from_included),0) FROM credit_ledger WHERE tenant_id=? AND kind='consumo' AND period=?", (tid, month)).fetchone()[0]
    extra = con.execute("SELECT COALESCE(SUM(CASE WHEN kind='consumo' THEN -from_extra ELSE amount END),0) FROM credit_ledger WHERE tenant_id=?", (tid,)).fetchone()[0]
    inc = included(tenant)
    left = max(0, inc - used)
    return {"included": inc, "included_left": left, "extra": extra, "available": left + extra, "resets_on": resets_on(now)}


def check(con, tenant, model_id, tenant_keys=None):
    if applies(tenant, model_id, tenant_keys) and balance(con, tenant)["available"] <= 0:
        raise ApiError(402, "Crediti finiti: quelli inclusi nel pacchetto si ricaricano il %s. Un amministratore può comprarne altri "
                            "nella scheda Crediti. Il modello «Locale» e le chiavi proprie dell'azienda non consumano crediti."
                       % "/".join(reversed(resets_on().split("-"))))


def cost(model_id, tokens):
    return int(math.ceil(max(0, tokens) * rate(model_id) / 1000.0)) if tokens > 0 else 0


def charge(con, tenant, user_id, model_id, tokens, tenant_keys=None):
    """Scala i crediti di una risposta: prima quelli inclusi nel mese, poi quelli comprati. Ritorna quanti ne ha scalati.
    L'ultima risposta prima dello zero può costare più di quanto restava: la differenza si recupera dal prossimo acquisto."""
    if not applies(tenant, model_id, tenant_keys):
        return 0
    amount = cost(model_id, tokens)
    if amount <= 0:
        return 0
    before = balance(con, tenant)
    from_included = min(amount, before["included_left"])
    con.execute("INSERT INTO credit_ledger(tenant_id,kind,amount,from_included,from_extra,model,note,period,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (tenant["id"], "consumo", amount, from_included, amount - from_included, model_id, "", period(), user_id, db.now()))
    after = before["available"] - amount
    total = before["included"] + max(0, before["extra"])
    if total and before["available"] > 0.2 * total >= after and after > 0:
        news.add(con, tenant["id"], "consumi", "Crediti quasi finiti", "Resta meno di un quinto dei crediti. Quelli inclusi si ricaricano il %s." % "/".join(reversed(before["resets_on"].split("-"))))
    elif before["available"] > 0 >= after:
        news.add(con, tenant["id"], "consumi", "Crediti finiti", "Gli agenti che usano i modelli della piattaforma si fermano finché i crediti non si ricaricano o non ne compri altri.")
    return amount


def grant(con, tenant_id, amount, kind, note="", actor_id=None):
    """Aggiunge crediti che non scadono: 'acquisto' (pagati) oppure 'regalo' (dati dal gestore; negativo per toglierli)."""
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        raise ApiError(400, "I crediti devono essere un numero intero.")
    if amount == 0 or abs(amount) > 100000000 or (kind == "acquisto" and amount < 0):
        raise ApiError(400, "Numero di crediti non valido.")
    if not con.execute("SELECT 1 FROM tenants WHERE id=?", (tenant_id,)).fetchone():
        raise ApiError(404, "Azienda non trovata.")
    con.execute("INSERT INTO credit_ledger(tenant_id,kind,amount,from_included,from_extra,model,note,period,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (tenant_id, kind, amount, 0, 0, "", str(note)[:200], period(), actor_id, db.now()))
    audit.record(con, tenant_id, actor_id, "crediti_" + ("comprati" if kind == "acquisto" else "dal_gestore"), {"crediti": amount})
    return amount


def view(con, tenant):
    """Quello che la scheda Crediti mostra all'azienda."""
    names = {m["id"]: m["name"] for m in catalog.load()["models"]}
    out = balance(con, tenant)
    out.update({
        "enabled": enabled(),
        "rates": [{"model": m, "name": names.get(m, m), "per_1000": rate(m)} for m in ("kimi", "claude", "chatgpt", "locale") if m in names],
        "month": db.rows(con.execute("SELECT model, SUM(amount) AS credits FROM credit_ledger WHERE tenant_id=? AND kind='consumo' AND period=? GROUP BY model ORDER BY credits DESC",
                                     (tenant["id"], period()))),
        "moves": db.rows(con.execute("SELECT kind, amount, note, created_at FROM credit_ledger WHERE tenant_id=? AND kind<>'consumo' ORDER BY id DESC LIMIT 10", (tenant["id"],))),
        "pack": {"size": max(0, config.get_int("CREDIT_PACK_SIZE", 0)),
                 "buyable": bool(config.get("STRIPE_SECRET_KEY") and config.get("STRIPE_WEBHOOK_SECRET") and config.get("STRIPE_PRICE_CREDITS") and config.get_int("CREDIT_PACK_SIZE", 0) > 0)}})
    return out
