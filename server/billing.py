"""Pagamenti con carta tramite Stripe: il cliente paga dal suo pannello e la scadenza dell'abbonamento si sposta da sola.

Giro: il server crea una sessione di pagamento su Stripe (Checkout, abbonamento mensile) e manda lì il cliente;
Stripe avvisa il server con un «webhook» firmato; a ogni fattura pagata la scadenza diventa la fine del periodo pagato.
Il numero della carta non passa mai da questo server.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from datetime import datetime, timedelta, timezone

from . import audit, catalog, config, connectors, credits, db, google, news, pricing, registration
from .errors import ApiError

TOLERANCE = 300
ASYNC = True  # le email partono in un filo a parte, così Stripe riceve subito la risposta; le prove lo spengono


def _api():
    return config.get("STRIPE_API_BASE", "https://api.stripe.com").rstrip("/")


def price_for(package_id):
    return config.get("STRIPE_PRICE_" + str(package_id).upper())


def status(tenant):
    ready = bool(config.get("STRIPE_SECRET_KEY") and config.get("STRIPE_WEBHOOK_SECRET"))
    return {"enabled": ready and bool(price_for(tenant["package_id"]) or pricing.configured(tenant["package_id"])), "subscribed": bool(tenant.get("stripe_subscription")),
            "paid_until": tenant.get("paid_until") or "", "payment_failed": bool(tenant.get("payment_failed_at")),
            "cancel_at": tenant.get("stripe_cancel_at") or "",
            "portal": bool(config.get("STRIPE_SECRET_KEY") and tenant.get("stripe_customer"))}


def credits_checkout(con, tenant, user, quantity=1):
    """Pagina di Stripe per comprare ricariche di crediti (pagamento singolo). Ritorna {"url"}."""
    size = config.get_int("CREDIT_PACK_SIZE", 0)
    if not (credits.enabled() and config.get("STRIPE_SECRET_KEY") and config.get("STRIPE_WEBHOOK_SECRET") and config.get("STRIPE_PRICE_CREDITS") and size > 0):
        raise ApiError(400, "L'acquisto di crediti non è attivo: il gestore deve impostare la ricarica e il suo prezzo.")
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        quantity = 0
    if not 1 <= quantity <= 20:
        raise ApiError(400, "Puoi comprare da 1 a 20 ricariche alla volta.")
    base = google.public_url()
    form = {"mode": "payment", "line_items[0][price]": config.get("STRIPE_PRICE_CREDITS"), "line_items[0][quantity]": str(quantity),
            "success_url": base + "/#crediti-ok", "cancel_url": base + "/#crediti-annullato", "client_reference_id": str(tenant["id"]),
            "metadata[tenant_id]": str(tenant["id"]), "metadata[kind]": "crediti", "metadata[credits]": str(size * quantity)}
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
    if not url.startswith(("https://checkout.stripe.com/", _api() + "/")):
        raise ApiError(502, "Stripe ha risposto con un indirizzo di pagamento inatteso.")
    audit.record(con, tenant["id"], user["id"], "crediti_acquisto_avviato", {"crediti": size * quantity})
    return {"url": url}


def _day(iso):
    return "/".join(reversed(str(iso).split("-")))


def mail_admins(con, tenant_id, subject, text, wait=False):
    """Scrive agli amministratori attivi dell'azienda, se la posta è configurata. Un invio fallito non ferma niente.
    Ritorna gli indirizzi a cui l'email è partita davvero; se parte in un filo a parte, quelli a cui proverà."""
    if registration.smtp_cfg() is None:
        return []
    to = [r["email"] for r in con.execute("SELECT email FROM users WHERE tenant_id=? AND role='admin' AND active=1 ORDER BY id", (tenant_id,))]

    def deliver():
        done = []
        for address in to:
            try:
                registration.send_mail(address, subject, text)
                done.append(address)
            except Exception as e:
                print("ATTENZIONE: email «%s» non inviata: %s" % (subject, type(e).__name__))
        return done

    if to and ASYNC and not wait:
        threading.Thread(target=deliver, daemon=True).start()
        return to
    return deliver()


def portal(con, tenant, user):
    """Pagina di Stripe dove il cliente cambia carta, scarica le ricevute e disdice. Ritorna {"url"}."""
    if not status(tenant)["portal"]:
        raise ApiError(400, "Non c'è ancora un pagamento con carta da gestire per questa azienda.")
    try:
        session = connectors.http_json("POST", _api() + "/v1/billing_portal/sessions",
                                       headers={"Authorization": "Bearer " + config.get("STRIPE_SECRET_KEY")},
                                       form={"customer": tenant["stripe_customer"], "return_url": google.public_url() + "/#abbonamento"})
    except connectors.ConnectorError as e:
        raise ApiError(502, "Stripe non ha aperto la pagina di gestione: %s" % str(e)[:200])
    url = str(session.get("url") or "")
    if not url.startswith(("https://billing.stripe.com/", _api() + "/")):
        raise ApiError(502, "Stripe ha risposto con un indirizzo inatteso.")
    audit.record(con, tenant["id"], user["id"], "pagamento_gestione_aperta", {})
    return {"url": url}


def remind(con, today=None):
    """Email agli amministratori delle aziende che scadono entro sette giorni e non si rinnovano da sole.
    Una sola per scadenza: se la data viene spostata, l'avviso riparte per la data nuova. Ritorna quante aziende ha avvisato."""
    if registration.smtp_cfg() is None:
        return 0  # senza posta non si segna niente: l'avviso partirà quando la posta c'è
    today = today or datetime.now(timezone.utc).date()
    due = db.rows(con.execute(
        "SELECT * FROM tenants WHERE active=1 AND paid_until!='' AND paid_until>=? AND paid_until<=? AND expiry_notice_for!=paid_until "
        "AND (stripe_subscription='' OR stripe_cancel_at!='')", (today.isoformat(), (today + timedelta(days=7)).isoformat())))
    sent = 0
    for t in due:
        claimed = con.execute("UPDATE tenants SET expiry_notice_for=? WHERE id=? AND expiry_notice_for!=?", (t["paid_until"], t["id"], t["paid_until"]))
        if claimed.rowcount != 1:
            continue  # un altro processo l'ha già presa
        how = ("Per rinnovare con carta apri la scheda Abbonamento: %s/#abbonamento" % google.public_url() if status(t)["enabled"]
               else "Per rinnovare contatta chi ti ha venduto il servizio.")
        if mail_admins(con, t["id"], "Alvea: l'abbonamento scade il %s" % _day(t["paid_until"]),
                       "L'abbonamento di %s scade il %s.\n\nDopo quella data gli agenti vanno in pausa; i dati restano al loro posto.\n\n%s\n"
                       % (t["name"], _day(t["paid_until"]), how), wait=True):
            audit.record(con, t["id"], None, "avviso_scadenza_inviato", {"scadenza": t["paid_until"]})
            sent += 1
        else:  # non è partita a nessuno: si riprova al prossimo giro
            con.execute("UPDATE tenants SET expiry_notice_for=? WHERE id=? AND expiry_notice_for=?", (t["expiry_notice_for"], t["id"], t["paid_until"]))
    return sent


def checkout(con, tenant, user):
    """Crea la pagina di pagamento su Stripe per il pacchetto dell'azienda. Ritorna {"url"} a cui mandare il cliente."""
    if not status(tenant)["enabled"]:
        raise ApiError(400, "Il pagamento con carta non è attivo: il gestore deve inserire le chiavi di Stripe e il prezzo del tuo pacchetto.")
    if tenant.get("stripe_subscription"):  # un secondo pagamento aprirebbe un secondo abbonamento: il cliente pagherebbe due volte
        raise ApiError(409, "Hai già un abbonamento con carta attivo: per cambiare carta o disdire usa «Carta, ricevute e disdetta».")
    base = google.public_url()
    q = pricing.quote(con, tenant)
    form = {"mode": "subscription", "line_items[0][quantity]": "1",
            "success_url": base + "/#pagamento-ok", "cancel_url": base + "/#pagamento-annullato",
            "client_reference_id": str(tenant["id"]), "metadata[tenant_id]": str(tenant["id"]),
            "subscription_data[metadata][tenant_id]": str(tenant["id"])}
    if q["configured"] and q["cents"] > 0:  # prezzo deciso nel pannello: lo si passa a Stripe così com'è, senza prezzi creati a mano
        form.update({"line_items[0][price_data][currency]": q["currency"], "line_items[0][price_data][unit_amount]": str(q["cents"]),
                     "line_items[0][price_data][recurring][interval]": "month",
                     "line_items[0][price_data][product_data][name]": "Alvea " + catalog.package(tenant["package_id"])["name"]})
    else:
        form["line_items[0][price]"] = price_for(tenant["package_id"])
    if tenant.get("stripe_customer"):
        form["customer"] = tenant["stripe_customer"]
    else:
        form["customer_email"] = user["email"]
    try:
        session = connectors.http_json("POST", _api() + "/v1/checkout/sessions",
                                       headers={"Authorization": "Bearer " + config.get("STRIPE_SECRET_KEY")}, form=form)
    except connectors.ConnectorError as e:
        raise ApiError(502, "Stripe non ha creato il pagamento: %s" % str(e)[:200])
    con.execute("UPDATE tenants SET stripe_amount=? WHERE id=?", (q["cents"] if q["configured"] else 0, tenant["id"]))
    url = str(session.get("url") or "")
    if not url.startswith(("https://checkout.stripe.com/", _api() + "/")):  # si manda il cliente solo su Stripe
        raise ApiError(502, "Stripe ha risposto con un indirizzo di pagamento inatteso.")
    audit.record(con, tenant["id"], user["id"], "pagamento_avviato", {"pacchetto": tenant["package_id"]})
    return {"url": url}


def sync_price(con, tenant_id):
    """Dopo che un'azienda abbonata ha acceso o spento agenti, o ha cambiato pacchetto: porta su Stripe il prezzo nuovo.
    Ritorna il prezzo nuovo in euro, None se non c'era niente da cambiare, oppure una stringa con l'errore.
    Non blocca mai la modifica degli agenti: se Stripe non risponde, il gestore lo vede nel registro."""
    tenant = dict(con.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,)).fetchone())
    q = pricing.quote(con, tenant)
    if not (tenant["stripe_subscription"] and q["configured"] and q["cents"] > 0 and config.get("STRIPE_SECRET_KEY")) or q["cents"] == tenant["stripe_amount"]:
        return None
    auth = {"Authorization": "Bearer " + config.get("STRIPE_SECRET_KEY")}
    try:
        sub = connectors.http_json("GET", _api() + "/v1/subscriptions/" + tenant["stripe_subscription"], headers=auth)
        item = ((sub.get("items") or {}).get("data") or [{}])[0]
        product = ((item.get("price") or {}).get("product")) or ""
        if not (isinstance(item.get("id"), str) and isinstance(product, str) and item["id"] and product):
            raise connectors.ConnectorError("abbonamento senza voce o senza prodotto")
        connectors.http_json("POST", _api() + "/v1/subscription_items/" + item["id"], headers=auth, form={
            "price_data[currency]": q["currency"], "price_data[product]": product, "price_data[unit_amount]": str(q["cents"]),
            "price_data[recurring][interval]": "month"})
    except connectors.ConnectorError as e:
        audit.record(con, tenant_id, None, "prezzo_non_aggiornato", {"da": tenant["stripe_amount"] // 100, "a": q["total"], "errore": str(e)[:200]})
        audit.record(con, 0, None, "prezzo_non_aggiornato", {"azienda": tenant["name"], "a": q["total"]})  # 0 = registro della piattaforma: lo vede il gestore
        return "Stripe non ha aggiornato il prezzo: %s" % str(e)[:160]
    con.execute("UPDATE tenants SET stripe_amount=? WHERE id=?", (q["cents"], tenant_id))
    audit.record(con, tenant_id, None, "prezzo_aggiornato", {"da": tenant["stripe_amount"] // 100, "a": q["total"]})
    news.add(con, tenant_id, "pagamento", "Prezzo dell'abbonamento aggiornato", "Con gli agenti accesi adesso l'abbonamento costa %d € al mese. La differenza la conteggia Stripe nella prossima fattura." % q["total"])
    return q["total"]


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
    if kind == "checkout.session.completed" and obj.get("mode") == "payment":
        # pagamento singolo: una ricarica di crediti. Non tocca l'abbonamento. Conta solo se Stripe dice che è pagato.
        meta = obj.get("metadata") or {}
        if meta.get("kind") == "crediti" and obj.get("payment_status") == "paid" and str(meta.get("credits") or "").isdigit():
            amount = credits.grant(con, tid, int(meta["credits"]), "acquisto", "Ricarica pagata con carta")
            if obj.get("customer") and not tenant["stripe_customer"]:
                con.execute("UPDATE tenants SET stripe_customer=? WHERE id=?", (str(obj["customer"])[:120], tid))
            news.add(con, tid, "pagamento", "Crediti aggiunti", "%d crediti comprati: non scadono e si usano dopo quelli inclusi nel pacchetto." % amount)
    elif kind == "checkout.session.completed":
        con.execute("UPDATE tenants SET stripe_customer=?, stripe_subscription=? WHERE id=?",
                    (str(obj.get("customer") or "")[:120], str(obj.get("subscription") or "")[:120], tid))
        audit.record(con, tid, None, "pagamento_collegato", {})
    elif kind in ("invoice.paid", "invoice.payment_succeeded"):
        ends = [l.get("period", {}).get("end") for l in ((obj.get("lines") or {}).get("data") or []) if isinstance(l, dict)]
        end = max([e for e in ends if isinstance(e, int)] or [0])
        if end:
            until = max(datetime.fromtimestamp(end, timezone.utc).date().isoformat(), tenant["paid_until"] or "")
            con.execute("UPDATE tenants SET paid_until=?, payment_failed_at='', on_trial=0 WHERE id=?", (until, tid))
            audit.record(con, tid, None, "pagamento_ricevuto", {"fino_al": until, "importo": obj.get("amount_paid"), "valuta": obj.get("currency")})
            news.add(con, tid, "pagamento", "Pagamento ricevuto", "Abbonamento attivo fino al %s." % "/".join(reversed(until.split("-"))))
    elif kind == "invoice.payment_failed":
        con.execute("UPDATE tenants SET payment_failed_at=? WHERE id=?", (db.now(), tid))
        audit.record(con, tid, None, "pagamento_non_riuscito", {"importo": obj.get("amount_due"), "valuta": obj.get("currency")})
        text = "L'addebito sulla carta non è riuscito. Stripe riproverà nei prossimi giorni: intanto controlla o cambia la carta dalla scheda Abbonamento."
        news.add(con, tid, "pagamento", "Pagamento non riuscito", text)
        mail_admins(con, tid, "Alvea: pagamento non riuscito", "%s\n\n%s/#abbonamento\n\nSe la scadenza passa senza un pagamento, gli agenti vanno in pausa; i dati restano al loro posto.\n"
                    % (text, google.public_url()))
    elif kind == "customer.subscription.updated":
        ends = [obj.get("cancel_at")] if isinstance(obj.get("cancel_at"), int) else []
        if not ends and obj.get("cancel_at_period_end"):
            ends = [e for e in [obj.get("current_period_end")] + [i.get("current_period_end") for i in ((obj.get("items") or {}).get("data") or []) if isinstance(i, dict)]
                    if isinstance(e, int)]
        day = datetime.fromtimestamp(max(ends), timezone.utc).date().isoformat() if ends else ""
        if day != (tenant["stripe_cancel_at"] or ""):
            con.execute("UPDATE tenants SET stripe_cancel_at=? WHERE id=?", (day, tid))
            audit.record(con, tid, None, "disdetta_registrata" if day else "disdetta_ritirata", {"dal": day} if day else {})
            news.add(con, tid, "pagamento", "Disdetta registrata" if day else "Disdetta ritirata",
                     "La carta non verrà più addebitata. Gli agenti restano attivi fino al %s." % _day(day) if day else "L'abbonamento con carta continua a rinnovarsi.")
    elif kind == "customer.subscription.deleted":
        con.execute("UPDATE tenants SET stripe_subscription='', stripe_cancel_at='', payment_failed_at='' WHERE id=?", (tid,))
        audit.record(con, tid, None, "abbonamento_disdetto", {})
        news.add(con, tid, "pagamento", "Abbonamento con carta disdetto", "Gli agenti restano attivi fino alla scadenza già pagata.")
    return {"ok": True}
