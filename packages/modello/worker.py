"""Pacchetto locale «modello»: gira le richieste del server a un modello che vive su questo computer.

Parla con qualunque servizio compatibile con l'API Chat Completions (Ollama: http://127.0.0.1:11434/v1).
Si collega solo a indirizzi di questo stesso computer.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL, DEFAULT_MODEL = "http://127.0.0.1:11434/v1", "llama3.1"


class WorkerError(Exception):
    pass


def _base(env):
    url = str(env.config.get("indirizzo") or DEFAULT_URL).rstrip("/")
    host = urllib.parse.urlsplit(url).hostname or ""
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise WorkerError("Il modello locale deve stare su questo computer (indirizzo 127.0.0.1).")
    return url


def llm_chat(args, env):
    body = dict(args.get("body") or {}, model=str(env.config.get("modello") or DEFAULT_MODEL), stream=False)
    req = urllib.request.Request(_base(env) + "/chat/completions", data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer ollama"})
    try:
        with urllib.request.urlopen(req, timeout=int(env.config.get("timeout") or 100)) as r:
            return {"text": r.read(2000000).decode("utf-8")}
    except urllib.error.HTTPError as e:
        raise WorkerError("Il modello locale ha risposto con errore %d: %s" % (e.code, e.read(300).decode("utf-8", "replace")))
    except OSError as e:
        raise WorkerError("Il modello locale non risponde: %s" % getattr(e, "reason", e))


def check(env):
    try:
        with urllib.request.urlopen(_base(env) + "/models", timeout=4) as r:
            names = [m.get("id") for m in (json.loads(r.read(200000).decode("utf-8")).get("data") or [])]
    except WorkerError as e:
        return False, str(e)
    except (OSError, ValueError):
        return False, "nessun modello locale in ascolto (Ollama non è avviato su questo computer)"
    wanted = str(env.config.get("modello") or DEFAULT_MODEL)
    if not any(str(n or "").split(":")[0] == wanted.split(":")[0] for n in names):
        return False, "Ollama risponde ma il modello «%s» non è scaricato (presenti: %s)" % (wanted, ", ".join(str(n) for n in names[:6]) or "nessuno")
    return True, "modello «%s» pronto" % wanted


TOOLS = {"llm_chat": llm_chat}
