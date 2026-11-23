"""Il sito pubblico di Alvea, servito dallo stesso server dell'applicazione.

La pagina è costruita dal catalogo: agenti, pacchetti e modelli sono quelli veri, e ogni agente dice da solo
cosa fa e cosa non fa ancora. Niente prezzi, numeri o promesse che il catalogo non contiene.
"""
from __future__ import annotations

from html import escape as e

from . import catalog, config, pricing

STEPS = [("Scegli il pacchetto", "Tre pacchetti, ognuno contiene il precedente. Dentro il tuo pacchetto accendi solo gli agenti che ti servono."),
         ("Collega i tuoi strumenti", "Posta, calendario, negozio, Telegram, WhatsApp: ogni collegamento si fa una volta, dal Centro di controllo."),
         ("Approva e controlla", "Gli agenti preparano il lavoro. Quello che esce verso l'esterno parte solo dopo l'approvazione di un amministratore.")]
SAFETY = [("Niente parte senza di te", "Email, messaggi, comandi: ogni azione verso l'esterno aspetta l'approvazione di un amministratore."),
          ("Registro che non si riscrive", "Ogni azione finisce in un registro concatenato: una riga cambiata dopo si vede."),
          ("Arresto d'emergenza", "Un pulsante ferma subito tutti gli agenti dell'azienda, finché non li riavvii."),
          ("Dati separati per azienda", "Ogni azienda vede solo i propri dati. Le chiavi dei modelli e dei servizi restano sul server.")]


def enabled():
    return config.get("SFB_SITE", "1") == "1"


def _first_package(cat):
    """Per ogni agente, il primo pacchetto che lo contiene."""
    return {agent_id: p["name"] for p in reversed(cat["packages"]) for agent_id in p["adds"]}


def _price(prices, package_id):
    """Riga del prezzo di un pacchetto: compare solo se il gestore ha impostato i prezzi nel pannello."""
    base, each = prices["packages"].get(package_id, 0), prices["per_agent"]
    if not base and not each:
        return ""
    text = ("%d € al mese" % base if base else "") + ((" + " if base else "") + "%d € al mese per ogni agente acceso" % each if each else "")
    return '<p class="price">%s</p>' % e(text)


def page():
    cat = catalog.load()
    agents, packs, first = cat["agents"], cat["packages"], _first_package(catalog.load())
    names = {a["id"]: a["name"] for a in agents}
    cards = "".join(
        '<li class="agent"><h3>%s</h3><p>%s</p><p class="acts">%s</p><span class="from">da %s</span></li>'  # niente stili in linea: le regole di sicurezza li vietano
        % (e(a["name"]), e(a["does"]), e(a["acts"]), e(first.get(a["id"], packs[-1]["name"]))) for a in agents)
    total, plans, prices = 0, [], pricing.public()
    for i, p in enumerate(packs):
        total += len(p["adds"])
        limits = p.get("limits") or {}
        plans.append(
            '<li class="plan%s"><h3>%s</h3><p class="count"><b>%d</b> agenti</p>%s<p>%s</p><p class="adds">%s%s</p><ul class="limits">'
            '<li>%s lavori automatici</li><li>%s documenti in memoria</li><li>%s di archivio per foto e video</li></ul>'
            '<a class="btn%s" href="/app#registrati" data-register>Inizia con %s</a></li>'
            % (" featured" if i == 1 else "", e(p["name"]), total, _price(prices, p["id"]), e(p["why"]), "Tutto %s, più: " % e(packs[i - 1]["name"]) if i else "",
               e(", ".join(names.get(x, x) for x in p["adds"])), limits.get("automazioni", 0), limits.get("documenti", 0),
               ("%d GB" % (limits.get("archivio_mb", 0) // 1000) if limits.get("archivio_mb", 0) >= 1000 else "%d MB" % limits.get("archivio_mb", 0)),
               " primary" if i == 1 else "", e(p["name"])))
    steps = "".join('<li><span class="n">%d</span><h3>%s</h3><p>%s</p></li>' % (i + 1, e(t), e(d)) for i, (t, d) in enumerate(STEPS))
    safety = "".join("<li><h3>%s</h3><p>%s</p></li>" % (e(t), e(d)) for t, d in SAFETY)
    models = ", ".join(e(m["name"]) for m in cat["models"])
    local = [a["name"] for a in agents if (a["run"].get("local") or {}).get("ready")]
    html = """<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Alvea — agenti AI per la tua azienda</title>
<meta name="description" content="Alvea mette al lavoro una squadra di {n} agenti AI per la tua azienda: email, calendario, contenuti, clienti, video. Tu approvi quello che esce verso l'esterno.">
<meta property="og:title" content="Alvea — agenti AI per la tua azienda">
<meta property="og:description" content="Una squadra di {n} agenti AI, tre pacchetti, tutto sotto il tuo controllo.">
<meta property="og:image" content="/icon.png">
<meta name="theme-color" content="#0b0f14">
<link rel="icon" href="/icon.png">
<link rel="stylesheet" href="/sito.css">
<script src="/sito.js"></script>
</head>
<body>
<a class="skip" href="#contenuto">Vai al contenuto</a>
<header class="top"><div class="wrap">
<a class="brand" href="/" aria-label="Alvea, pagina iniziale"><img src="/logo-alvea.png" alt="alvea" width="626" height="186"></a>
<nav aria-label="Sezioni"><a href="#agenti">Agenti</a><a href="#pacchetti">Pacchetti</a><a href="#come-funziona">Come funziona</a><a href="#sicurezza">Sicurezza</a></nav>
<div class="cta"><a class="btn" href="/app">Accedi</a><a class="btn primary" href="/app#registrati" data-register>Prova Alvea</a></div>
</div></header>
<main id="contenuto">
<section class="hero"><div class="wrap">
<p class="eyebrow">{n} agenti · un solo sistema</p>
<h1>Una squadra di agenti AI che lavora per la tua azienda.</h1>
<p class="lead">Posta, calendario, contenuti, clienti, video: ogni agente ha un compito preciso. Loro preparano il lavoro, tu decidi cosa esce.</p>
<div class="cta"><a class="btn primary big" href="/app#registrati" data-register>Prova Alvea</a><a class="btn big" href="#launcher">Verifica il tuo computer</a></div>
<p class="note" id="trial" hidden></p>
<div class="hive" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div>
</div></section>
<section id="agenti"><div class="wrap">
<h2>Gli agenti</h2>
<p class="sub">Ognuno dice chiaramente cosa fa oggi. Dove c'è scritto «solo bozze», l'agente prepara e tu pubblichi o invii.</p>
<ul class="agents">{cards}</ul>
</div></section>
<section id="pacchetti" class="alt"><div class="wrap">
<h2>Pacchetti</h2>
<p class="sub">Ogni pacchetto contiene tutto quello del precedente. Puoi salire quando vuoi.</p>
<ul class="plans">{plans}</ul>
</div></section>
<section id="come-funziona"><div class="wrap">
<h2>Come funziona</h2>
<ol class="steps">{steps}</ol>
</div></section>
<section id="launcher" class="alt"><div class="wrap split">
<div><h2>Sui nostri server o sul tuo computer</h2>
<p class="sub">Tutto gira sui nostri server senza installare niente. Se preferisci, alcuni agenti possono lavorare sul tuo computer: {local}.</p>
<p class="sub">Il launcher controlla il tuo computer e ti dice, agente per agente, se può farlo girare. Lo scopri prima di scegliere, senza account. Per farli lavorare sul tuo computer serve poi l'abbonamento attivo.</p>
<div class="cta"><a class="btn primary" href="/api/launcher/download">Scarica il launcher</a></div></div>
<div class="panel"><h3>Modelli</h3><p>{models}. Li scegli tu, conversazione per conversazione. Con un modello locale i testi non lasciano il tuo computer.</p></div>
</div></section>
<section id="sicurezza"><div class="wrap">
<h2>Sicurezza e controllo</h2>
<ul class="safety">{safety}</ul>
</div></section>
<section class="last"><div class="wrap">
<h2>Metti al lavoro la tua squadra.</h2>
<div class="cta"><a class="btn primary big" href="/app#registrati" data-register>Prova Alvea</a><a class="btn big" href="/app">Accedi</a></div>
</div></section>
</main>
<footer><div class="wrap"><img src="/logo-alvea.png" alt="alvea" width="626" height="186"><nav aria-label="Collegamenti"><a href="#agenti">Agenti</a><a href="#pacchetti">Pacchetti</a><a href="/app">Accedi</a><a href="/api/launcher/download">Launcher</a></nav></div></footer>
</body>
</html>
""".format(n=len(agents), cards=cards, plans="".join(plans), steps=steps, safety=safety, models=models, local=e(", ".join(local)))
    return html.encode("utf-8")
