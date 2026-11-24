"""Che cosa c'è da tradurre e che cosa manca. Raccoglie i testi che una persona può leggere (sito, pagine legali, catalogo, messaggi del
server, interfaccia) e dice quali non hanno ancora una traduzione in `i18n/<lingua>.json`.

Uso:  python3 strumenti/traduzioni.py [en]            elenco di quello che manca, per fonte
      python3 strumenti/traduzioni.py en --json FONTE  lo stesso, pronto da riempire
"""
import ast
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ERRORS = {"ApiError", "ConnectorError", "JobError", "UploadError", "LLMError", "ToolError", "TwoFactorNeeded", "SystemExit"}
CATALOG_KEYS = {"name", "does", "acts", "ext", "run", "station", "tries", "for", "description", "tagline", "label", "note", "short"}


def _const(node):
    """Il testo di un'espressione, se è scritto lì: "…" oppure "…" % qualcosa. Ritorna (testo, ha_pezzi_variabili)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, False
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        left = _const(node.left)
        return (left[0], True) if left else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):  # "…" + qualcosa: non si può sapere com'è fatto il resto
        return None
    if isinstance(node, ast.IfExp):
        return None
    return None


def server_messages():
    """I messaggi che il server manda a chi usa il programma: errori e avvisi. Ritorna {testo: ha_pezzi_variabili}."""
    out = {}
    for f in sorted((ROOT / "server").glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else (node.func.attr if isinstance(node.func, ast.Attribute) else "")
            owner = node.func.value.id if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) else ""
            args = []
            if name in ERRORS and name != "SystemExit":
                args = node.args
            elif owner == "news" and name == "add":
                args = node.args[3:5]
            for a in args:
                for piece in ([a.body, a.orelse] if isinstance(a, ast.IfExp) else [a]):
                    got = _const(piece)
                    if got and re.search(r"[a-zàèéìòù]{3}", got[0]):
                        out[got[0]] = out.get(got[0], False) or got[1] or "%" in got[0]
    return out


def table_texts():
    """Etichette e descrizioni che il server tiene in tabelle e manda all'interfaccia: impostazioni, collegamenti, comandi della console,
    servizi Google, primi passi."""
    os.environ.setdefault("SFB_DB", "/tmp/alvea-traduzioni.db")
    from server import connectors, console, google, i18n, settings
    out = set()

    def walk(o):
        if isinstance(o, str):
            if re.search(r"[a-zàèéìòù]{3}", o) and (" " in o.strip() or o[:1].isupper()) and not re.fullmatch(r"[A-Z0-9_]+|[a-z0-9_\-]+|https?://\S+", o):
                out.add(o)
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple, set)):
            for v in o:
                walk(v)
    walk([(f[0], f[2], re.sub(r" ?\(1 sì, 0 no\)\.?", "", f[2])) for f in settings.FIELDS])
    walk([c[1:3] for c in console.COMMANDS])
    walk({k: {x: y for x, y in v.items() if x != "agent"} for k, v in connectors.DEFS.items()})
    walk([{x: y for x, y in v.items() if x not in ("scopes", "scope")} if isinstance(v, dict) else v for v in google.SERVICES.values()])
    for f in sorted((ROOT / "server").glob("*.py")):  # i dizionari scritti nel codice con una chiave che l'interfaccia legge
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if isinstance(k, ast.Constant) and k.value in i18n.KEYS and k.value not in ("name", "detail", "body", "message"):
                        got = _const(v)
                        if got and not got[1]:
                            walk(got[0])
    return out


def catalog_texts():
    out = set()

    def walk(o, key=None):
        if isinstance(o, str):
            if key in CATALOG_KEYS and re.search(r"[A-Za-zàèéìòù]{2}", o):
                out.add(o)
        elif isinstance(o, dict):
            for k, v in o.items():
                walk(v, k)
        elif isinstance(o, list):
            for v in o:
                walk(v, key)
    walk(json.loads((ROOT / "catalog.json").read_text(encoding="utf-8")))
    return out


def site_texts(lang):
    """Fa costruire al server le pagine pubbliche nella lingua chiesta e ritorna i testi rimasti in italiano."""
    os.environ.setdefault("SFB_DB", "/tmp/alvea-traduzioni.db")
    from server import catalog, i18n, legal, site
    catalog.load()
    i18n.reload()
    i18n.html(site.page().decode("utf-8"), lang)
    for name in legal.PAGES:
        i18n.html(site.legal_page(name).decode("utf-8"), lang)
    return set(i18n.MISSING.get(lang, set()))


def app_texts():
    """Le frasi scritte nell'interfaccia. Quelle intere (tra virgolette, senza pezzi attaccati) si riconoscono da qui;
    quelle composte al momento (testo + numero + testo) le trova la prova nel browser."""
    src = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    out = set()
    for m in re.finditer(r'(?<![\w$])"((?:[^"\\\n]|\\.)*)"', src):
        s = bytes(m.group(1), "utf-8").decode("unicode_escape").encode("latin-1", "ignore").decode("utf-8", "ignore") if "\\" in m.group(1) else m.group(1)
        before, after = src[max(0, m.start() - 2):m.start()].strip(), src[m.end():m.end() + 2].strip()
        glued = before.endswith("+") or after.startswith("+")
        if glued or len(s) < 2 or not re.search(r"[A-Za-zÀ-ú]{2}", s):
            continue
        if re.fullmatch(r"[a-z0-9_.:#/\-\[\]=, ()>*%+]+", s) and " " not in s.strip():  # nomi di classi, indirizzi, chiavi
            continue
        if re.fullmatch(r"[a-z][a-z0-9\-]*( [a-z][a-z0-9\-]*)*", s) and not re.search(r"\b(di|il|la|un|una|per|con|non|che|da|in|del|della|al|e|è|a)\b", s):
            continue  # elenchi di classi CSS
        if s.startswith(("/", "#", ".", "http", "application/", "sfb_", "alvea_", "set_", "reg_", "data-", "aria-", "(max-width")):
            continue
        if re.fullmatch(r"[a-z]+[A-Z]\w*|[A-Z]{3,6}|[\w-]+-Type|X-Lang|[a-z]{2}-[A-Z]{2}|Authorization", s.strip()):  # identificativi, metodi, intestazioni
            continue
        out.add(s)
    return out


def _literals(src):
    """Posizioni (inizio, fine, testo) delle stringhe tra virgolette doppie, saltando commenti ed espressioni regolari semplici."""
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c == "/" and src[i + 1:i + 2] == "/":
            i = src.find("\n", i)
            i = n if i < 0 else i
        elif c == "/" and src[i + 1:i + 2] == "*":
            i = src.find("*/", i) + 2
        elif c == "'":
            j = i + 1
            while j < n and src[j] != "'":
                j += 2 if src[j] == "\\" else 1
            i = j + 1
        elif c == '"':
            j = i + 1
            while j < n and src[j] != '"':
                j += 2 if src[j] == "\\" else 1
            try:
                out.append((i, j + 1, json.loads(src[i:j + 1])))
            except ValueError:
                pass
            i = j + 1
        else:
            i += 1
    return out


def app_patterns():
    """Le frasi che l'interfaccia compone al momento («Usati » + x + « di » + y): per ognuna il modello con i pezzi variabili,
    «Usati {0} di {1}.», così si può tradurre la frase intera e non i suoi frammenti."""
    src = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    lits = _literals(src)
    inside = [False] * (len(src) + 1)
    for a, b, _ in lits:
        for k in range(a, b):
            inside[k] = True
    done, out = set(), set()
    OPEN, CLOSE = "([{", ")]}"
    for a, b, _ in lits:
        if a in done:
            continue
        # indietro fino all'inizio dell'espressione
        i, depth = a - 1, 0
        while i >= 0:
            c = src[i]
            if not inside[i]:
                if c in CLOSE:
                    depth += 1
                elif c in OPEN:
                    if depth == 0:
                        break
                    depth -= 1
                elif depth == 0 and (c in ",;=?:" or src[max(0, i - 6):i + 1].endswith("return")):
                    break
            i -= 1
        start = i + 1
        j, depth = b, 0
        while j < len(src):
            c = src[j]
            if not inside[j]:
                if c in OPEN:
                    depth += 1
                elif c in CLOSE:
                    if depth == 0:
                        break
                    depth -= 1
                elif depth == 0 and c in ",;:?":
                    break
            j += 1
        # divide l'espressione ai «+» di primo livello
        parts, k, depth, last = [], start, 0, start
        while k < j:
            c = src[k]
            if not inside[k]:
                if c in OPEN:
                    depth += 1
                elif c in CLOSE:
                    depth -= 1
                elif c == "+" and depth == 0:
                    parts.append((last, k))
                    last = k + 1
            k += 1
        parts.append((last, j))
        if len(parts) < 2:
            continue
        text, n, holes = "", 0, 0
        for x, y in parts:
            piece = src[x:y].strip()
            lit = [l for l in lits if l[0] >= x and l[1] <= y and src[x:y].strip() == src[l[0]:l[1]]]
            if lit:
                text += lit[0][2]
                done.add(lit[0][0])
            elif piece:
                if not text.endswith("}"):
                    text += "{%d}" % n
                    n += 1
                holes += 1
        if holes and re.search(r"[A-Za-zÀ-ú]{3}", re.sub(r"\{\d+\}", "", text)) and not text.startswith(("/", "#", ".", "set_", "reg_", "Bearer ")) and " " in text.strip():
            if not re.fullmatch(r"[a-z0-9 _\-{}.#]+", text) or re.search(r"\b(di|il|la|un|per|con|non|che|da|in|su|e|è|a)\b", text):
                out.add(text.strip())
    return out


def missing(lang):
    from server import i18n
    i18n.reload()
    have = i18n.table(lang)
    known = lambda s: i18n.text(s, lang, note=False) != s or s.strip() in have["exact"]  # noqa: E731
    report = {}
    server = server_messages()
    raw = json.loads((i18n.FOLDER / (lang + ".json")).read_text(encoding="utf-8"))
    patterns = {it for it, _ in raw.get("patterns", [])}
    def composed(s):  # una frase con pezzi variabili vuole il suo modello; un «%» che è solo un percento («all'80%») è una voce esatta
        s = s.replace("%%", "%")
        return s in patterns or (not i18n.PLACEHOLDER.search(s) and s.strip() in have["exact"])
    report["server"] = sorted(s for s, dyn in server.items() if not (composed(s) if dyn else (s.strip() in have["exact"])))
    report["catalogo"] = sorted(s for s in catalog_texts() if s.strip() not in have["exact"])
    report["tabelle"] = sorted(s for s in table_texts() if not known(s))
    skip = set(raw.get("skip", []))
    report["sito"] = sorted(site_texts(lang))
    report["interfaccia"] = sorted(s for s in app_texts() if not known(s))
    probe = lambda t: re.sub(r"\{\d+\}", lambda m: "x%sx" % m.group(0)[1:-1], t)  # noqa: E731  (un valore qualunque al posto dei pezzi variabili)
    report["composte"] = sorted(t for t in app_patterns() if t not in patterns and t not in skip and i18n.text(probe(t), lang, note=False) == probe(t))
    return report


if __name__ == "__main__":
    lang = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "en"
    rep = missing(lang)
    if "--json" in sys.argv:
        src = sys.argv[sys.argv.index("--json") + 1]
        print(json.dumps(rep[src], ensure_ascii=False, indent=0))
    else:
        for k, v in rep.items():
            print("%-12s mancano %d" % (k, len(v)))
        sys.exit(1 if any(rep.values()) else 0)
