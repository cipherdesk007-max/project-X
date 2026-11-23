"""Pagamenti con carta tramite Stripe: il cliente paga dal suo pannello e la scadenza dell'abbonamento si sposta da sola.

Giro: il server crea una sessione di pagamento su Stripe (Checkout, abbonamento mensile) e manda lì il cliente;
Stripe avvisa il server con un «webhook» firmato; a ogni fattura pagata la scadenza diventa la fine del periodo pagato.
Il numero della carta non passa mai da questo server.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

from . import audit, config, connectors, db, google, news
from .errors import ApiError

TOLERANCE = 300


def _api():
    return config.get("STRIPE_API_BASE", "https://api.stripe.com").rstrip("/")


def price_for(package_id):
    return config.get("STRIPE_PRICE_" + str(package_id).upper())


def status(tenant):
    ready = bool(config.get("STRIPE_SECRET_KEY") and config.get("STRIPE_WEBHOOK_SECRET"))
    return {"enabled": ready and bool(price_for(tenant["package_id"])), "subscribed": bool(tenant.get("stripe_subscription")),
            "paid_until": tenant.get("paid_until") or ""}


def checkout(con, tenant, user):
    """Crea la pagina di pagamento su Stripe per il pacchetto dell'azienda. Ritorna {"url"} a cui mandare il cliente."""
    if not status(tenant)["enabled"]:
        raise ApiError(400, "Il pagamento con carta non è attivo: il gestore deve inserire le chiavi di Stripe e il prezzo del tuo pacchetto.")
    base = google.public_url()
    form = {"mode": "subscription", "line_items[0][price]": price_for(tenant["package_id"]), "line_items[0][quantity]": "1",
            "success_url": base + "/#pagamento-ok", "cancel_url": base + "/#pagamento-annullato",
            "client_reference_id": str(tenant["id"]), "metadata[tenant_id]": str(tenant["id"]),
            "subscription_data[metadata][tenant_id]": str(tenant["id"])}
    if tenant.get("stripe_customer"):
        form["customer"] = tenant["stripe_customer"]
    else:
        form["customer_email"] = user["email"]
    try:
        session = connectors.http_json("POST", _api() + "/v1/checkout/sessions",
                                       headers={"Authorization": "Bearer " + config.get("STRIPE_SECRET_KEY")}, form=form)
    except connectors.ConnectorError as e:
        raise ApiError(502, "Stripe non ha creato il pagamento: %s" % str(e)[:200])
    url = str(session.get("url") or "")
    if not url.startswith(("https://checkout.stripe.com/", _api() + "/")):  # si manda il cliente solo su Stripe
        raise ApiError(502, "Stripe ha risposto con un indirizzo di pagamento inatteso.")
    audit.record(con, tenant["id"], user["id"], "pagamento_avviato", {"pacchetto": tenant["package_id"]})
    return {"url": url}


def verify_signature(raw, header, secret, now=None):
    """Firma di Stripe: intestazione «t=<secondi>,v1=<hex>», HMAC-SHA256 su «<t>.<corpo esatto>», valida 5 minuti."""
    parts = [p.split("=", 1) for p in str(header or "").split(",") if "=" in p]
    stamp = next((v for k, v in parts if k.strip() == "t"), "")
    sigs = [v.strip() for k, v in parts if k.strip() == "v1"]
    if not secret or not stamp.isdigit() or not sigs or abs((now or time.time()) - int(stamp)) > TOLERANCE:
        return False
    expected = hmac.new(secret.encode("utf-8"), stamp.encode("ascii") + b"." + raw, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, s) for s in sigs)


def _tenant_for(con, obj):
    ref = str(obj.get("client_reference_id") or (obj.get("metadata") or {}).get("tenant_id") or "")
    if ref.isdigit():
        row = con.execute("SELECT * FROM tenants WHERE id=?", (int(ref),)).fetchone()
        if row:
            return row
    sub = obj.get("subscription") or ((obj.get("parent") or {}).get("subscription_details") or {}).get("subscription")
    for column, value in (("stripe_subscription", sub), ("stripe_customer", obj.get("customer"))):
        if isinstance(value, str) and value:
            row = con.execute("SELECT * FROM tenants WHERE %s=?" % column, (value,)).fetchone()
            if row:
                return row
    return None


def webhook(con, raw, header):
    """Riceve un avviso di Stripe. Ogni evento conta una volta sola, anche se Stripe lo rimanda."""
    secret = config.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise ApiError(503, "Pagamenti non configurati.")
    if not verify_signature(raw, header, secret):
        raise ApiError(400, "Firma non valida.")
    try:
        event = json.loads(raw.decode("utf-8"))
        obj, kind, event_id = event["data"]["object"], str(event["type"]), str(event["id"])[:120]
    except (ValueError, KeyError, TypeError):
        raise ApiError(400, "Evento non leggibile.")
    if con.execute("INSERT OR IGNORE INTO billing_events(id,type,created_at) VALUES(?,?,?)", (event_id, kind[:80], db.now())).rowcount != 1:
        return {"ok": True, "duplicate": True}
    tenant = _tenant_for(con, obj)
    if not tenant:
        return {"ok": True, "ignored": True}
    tid = tenant["id"]
    if kind == "checkout.session.completed":
        con.execute("UPDATE tenants SET stripe_customer=?, stripe_subscription=? WHERE id=?",
                    (str(obj.get("customer") or "")[:120], str(obj.get("subscription") or "")[:120], tid))
        audit.record(con, tid, None, "pagamento_collegato", {})
    elif kind in ("invoice.paid", "invoice.payment_succeeded"):
        ends = [l.get("period", {}).get("end") for l in ((obj.get("lines") or {}).get("data") or []) if isinstance(l, dict)]
        end = max([e for e in ends if isinstance(e, int)] or [0])
        if end:
            until = max(datetime.fromtimestamp(end, timezone.utc).date().isoformat(), tenant["paid_until"] or "")
            con.execute("UPDATE tenants SET paid_until=? WHERE id=?", (until, tid))
            audit.record(con, tid, None, "pagamento_ricevuto", {"fino_al": until, "importo": obj.get("amount_paid"), "valuta": obj.get("currency")})
            news.add(con, tid, "pagamento", "Pagamento ricevuto", "Abbonamento attivo fino al %s." % "/".join(reversed(until.split("-"))))
    elif kind == "customer.subscription.deleted":
        con.execute("UPDATE tenants SET stripe_subscription='' WHERE id=?", (tid,))
        audit.record(con, tid, None, "abbonamento_disdetto", {})
        news.add(con, tid, "pagamento", "Abbonamento con carta disdetto", "Gli agenti restano attivi fino alla scadenza già pagata.")
    return {"ok": True}
