"""Le altre lingue dell'interfaccia. L'italiano è la lingua in cui il programma è scritto; ogni altra lingua è un dizionario
(`i18n/<lingua>.json`) che traduce i testi già pronti, subito prima che escano: una pagina del sito, la risposta a una richiesta,
un messaggio di errore, un'email. Così il resto del programma non sa niente delle lingue, e un testo nuovo senza traduzione
esce in italiano (e viene segnato tra quelli mancanti) invece di rompere qualcosa.

Il dizionario ha due parti:
- "exact":    {"testo italiano": "English text"}
- "patterns": [["Il pacchetto %s permette al massimo %d documenti.", "The {0} plan allows at most {1} documents."], ...]
  Nei modelli italiani i pezzi variabili si scrivono %s, %d (come nel codice del server) oppure {0}, {1} (come servono
  all'interfaccia); in quelli inglesi sempre {0}, {1}, nell'ordine in cui compaiono in italiano.

Lo stesso file viene letto dal server e scaricato dall'interfaccia (`/i18n/en.json`), che lo usa allo stesso modo.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

LANGS = ("it", "en")
NAMES = {"it": "Italiano", "en": "English"}
FOLDER = Path(__file__).resolve().parents[1] / "i18n"
PLACEHOLDER = re.compile(r"%(?:\d+\$)?[-0]?\d*(?:\.\d+)?[sdf]|\{\d+\}")
# Nelle risposte del server si traducono solo i valori sotto queste chiavi: sono testi scritti da noi (nomi di agenti, descrizioni,
# avvisi). Quello che scrivono le persone (messaggi, nomi dei clienti, note) passa com'è.
KEYS = {"error", "name", "does", "description", "title", "label", "text", "note", "hint", "for", "tries", "message", "notice", "station", "acts",
        "package_name", "reason", "summary", "detail", "body", "status_text", "warning", "info", "ext", "run", "tone", "steps", "action", "what", "why", "help", "schedule", "result"}
MISSING = {}          # lingua -> testi usciti senza traduzione (per le prove e per chi traduce)
_cache, _lock = {}, threading.Lock()


def pick(value):
    """La lingua chiesta, se è una di quelle che ci sono; altrimenti italiano. Accetta anche «en-GB» o un'intestazione Accept-Language."""
    first = str(value or "").split(",")[0].split(";")[0].strip().lower()[:2]
    return first if first in LANGS else "it"


def _regex(pattern):
    """Il modello italiano come espressione regolare: ogni pezzo variabile diventa un gruppo."""
    out, last = "", 0
    for m in PLACEHOLDER.finditer(pattern):
        out += re.escape(pattern[last:m.start()]) + (r"(-?\d+(?:[.,]\d+)?)" if m.group(0)[-1] in "df" else r"(.+?)")
        last = m.end()
    return re.compile("^" + out + re.escape(pattern[last:]) + "$", re.S)


def table(lang):
    """Il dizionario di una lingua: {"exact": {...}, "patterns": [(regex, inglese, inizio fisso), ...]}. Vuoto per l'italiano."""
    if lang == "it" or lang not in LANGS:
        return {"exact": {}, "patterns": []}
    with _lock:
        if lang not in _cache:
            raw = json.loads((FOLDER / (lang + ".json")).read_text(encoding="utf-8"))
            _cache[lang] = {"exact": raw.get("exact", {}),
                            "patterns": [(_regex(it), en, PLACEHOLDER.split(it)[0]) for it, en in raw.get("patterns", [])]}
        return _cache[lang]


def reload():
    with _lock:
        _cache.clear()
    MISSING.clear()


def text(value, lang, note=True):
    """Un testo nella lingua chiesta. Se la traduzione manca torna l'italiano."""
    if lang == "it" or not isinstance(value, str) or not value.strip():
        return value
    t = table(lang)
    lead = value[:len(value) - len(value.lstrip())]
    trail = value[len(value.rstrip()):]
    core = value.strip()
    if core in t["exact"]:
        return lead + t["exact"][core] + trail
    for rx, en, start in t["patterns"]:
        if start and not core.startswith(start):
            continue
        m = rx.match(core)
        if m:
            parts = m.groups()

            def fill(k):  # {0} resta com'è (un nome, un testo scritto da qualcuno); {0:t} è un testo nostro e va tradotto anche lui
                n = int(k.group(1))
                if n >= len(parts):
                    return k.group(0)
                return _piece(parts[n], lang) if k.group(2) else parts[n]
            return lead + _SLOT.sub(fill, en) + trail
    if ", " in core:  # un elenco di nomi nostri («Email, Calendario, Ricerca»): tradotto solo se si conoscono tutti
        items = core.split(", ")
        if all(i.strip() in t["exact"] for i in items):
            return lead + ", ".join(t["exact"][i.strip()] for i in items) + trail
    if note and re.search(r"[A-Za-zÀ-ú]{2}", core):
        MISSING.setdefault(lang, set()).add(core)
    return value


_SLOT = re.compile(r"\{(\d+)(:t)?\}")


def _piece(value, lang):
    """Un pezzo variabile dentro un modello: tradotto se è un testo nostro, altrimenti lasciato com'è (un nome, un numero, un indirizzo)."""
    return text(value, lang, note=False) if isinstance(value, str) else value


# Dove stanno i dati scritti dalle persone o dagli agenti: sotto queste chiavi un nome, un titolo o un testo non si traducono mai,
# nemmeno se per caso sono uguali a una voce del dizionario (un cliente che si chiama «Pronto» resta «Pronto»).
PEOPLE = frozenset(("user", "users", "tenant", "tenants", "rows", "devices", "automations", "assets", "documents", "facts", "proposals", "hits",
                    "outputs", "moves", "config", "messages", "leads", "events", "drafts", "members", "specs"))
# Lì dentro restano nostri solo questi campi.
OURS_INSIDE = frozenset(("package_name", "label", "status_text", "hint", "warning", "notice", "reason", "error", "station", "schedule"))


def payload(obj, lang, key=None, people=False):
    """La risposta a una richiesta, con i testi nostri tradotti. I dati delle persone non vengono toccati."""
    if lang == "it":
        return obj
    if isinstance(obj, dict):
        inside = people or key in PEOPLE
        own = obj.get("kind") == "automazione"  # l'avviso di un lavoro automatico: il testo è la risposta dell'agente, non nostra
        return {k: (v if own and k == "body" and isinstance(v, str) else payload(v, lang, k, inside)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [payload(v, lang, key, people or key in PEOPLE) for v in obj]
    if isinstance(obj, str) and key in (OURS_INSIDE if people else KEYS):
        return text(obj, lang, note=False)
    return obj


_TEXT = re.compile(r">([^<>]+)<")
_ATTR = re.compile(r'\b(alt|title|aria-label|placeholder|content)="([^"]*)"')
_SKIP = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)


def html(page, lang, links=None):
    """Una pagina intera nella lingua chiesta: i testi tra i tag e gli attributi che si leggono (alt, title, aria-label, content).
    `links` sono gli indirizzi interni da riscrivere, per esempio {"/privacy": "/en/privacy"}."""
    if lang == "it":
        return page
    from html import escape, unescape
    kept = []

    def hold(m):  # script e stili non sono testo da tradurre
        kept.append(m.group(0))
        return "\x00%d\x00" % (len(kept) - 1)
    page = _SKIP.sub(hold, page)
    page = _TEXT.sub(lambda m: ">" + escape(text(unescape(m.group(1)), lang), quote=False) + "<", page)
    page = _ATTR.sub(lambda m: '%s="%s"' % (m.group(1), escape(text(unescape(m.group(2)), lang, note=m.group(1) != "content"))), page)
    page = re.sub(r'<html lang="it"', '<html lang="%s"' % lang, page, count=1)
    for old, new in (links or {}).items():
        page = page.replace('href="%s"' % old, 'href="%s"' % new).replace('href="%s#' % old, 'href="%s#' % new)
    return re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], page)


def lines(value, lang):
    """Un testo su più righe (un'email): ogni riga per conto suo, così gli indirizzi e i nomi dentro restano com'erano."""
    if lang == "it":
        return value
    return "\n".join(text(line, lang) for line in str(value).split("\n"))
