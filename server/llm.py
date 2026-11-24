"""Chiamate ai modelli: Claude (API Messages di Anthropic) e Kimi / ChatGPT (formato Chat Completions).

Formato interno dei messaggi, uguale per tutti i provider:
  {"role": "user", "content": "..."}
  {"role": "assistant", "content": "...", "tool_calls": [{"id","name","input"}], "raw": <messaggio originale>}
  {"role": "tool", "tool_call_id": "...", "name": "...", "content": "..."}
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

from . import config

DEMO_BASE = None  # impostato da app.py quando parte il modello finto
DEMO_SERVER = None  # il modello finto stesso: le prove ne leggono le richieste ricevute


class LLMError(Exception):
    pass


def settings(model_id, tenant_keys=None, allow_platform=True):
    """Ritorna tipo di API, indirizzo, chiave e nome del modello per kimi / claude / chatgpt.
    `allow_platform` falso = vale solo la chiave dell'azienda (aziende registrate da sole)."""
    keys = tenant_keys or {}
    platform = config.get if allow_platform else (lambda name, default="": default)
    if model_id == "claude":
        s = {"kind": "anthropic", "base": config.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
             "key": keys.get("anthropic_api_key") or platform("ANTHROPIC_API_KEY"),
             "model": config.get("CLAUDE_MODEL", "claude-sonnet-5-5"), "key_name": "ANTHROPIC_API_KEY"}
    elif model_id == "kimi":
        s = {"kind": "openai", "base": config.get("KIMI_BASE_URL", "https://api.moonshot.ai/v1"),
             "key": keys.get("moonshot_api_key") or platform("MOONSHOT_API_KEY"),
             "model": config.get("KIMI_MODEL", "kimi-k3"), "key_name": "MOONSHOT_API_KEY",
             # Kimi K3 ragiona sempre, e di suo al massimo: per i lavori d'ufficio il gestore può chiedergli di ragionare meno (più svelto, meno token)
             "effort": config.get("KIMI_REASONING_EFFORT", "").strip().lower()}
    elif model_id == "chatgpt":
        s = {"kind": "openai", "base": config.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
             "key": keys.get("openai_api_key") or platform("OPENAI_API_KEY"),
             "model": config.get("OPENAI_MODEL", ""), "key_name": "OPENAI_API_KEY"}
    elif model_id == "locale":  # Ollama o qualunque servizio compatibile con Chat Completions, accanto al server
        s = {"kind": "openai", "base": platform("OLLAMA_BASE_URL"), "key": "ollama",
             "model": config.get("OLLAMA_MODEL", "llama3.1"), "key_name": "OLLAMA_BASE_URL"}
    else:
        raise LLMError("Modello sconosciuto.")
    if config.demo_mode() and DEMO_BASE:
        s.update(base=DEMO_BASE if s["kind"] == "anthropic" else DEMO_BASE + "/v1", key="demo",
                 model=s["model"] or "demo")
    return s


def configured(model_id, tenant_keys=None, allow_platform=True):
    s = settings(model_id, tenant_keys, allow_platform)
    return bool(s["key"] and s["model"] and s["base"])


def timeout():
    """Secondi di attesa per una risposta: dal pannello o dal file, sempre tra 2 e 900."""
    return max(2, min(900, config.get_int("SFB_LLM_TIMEOUT", 120)))


def _post(url, headers, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers=dict({"Content-Type": "application/json"}, **headers))
    try:
        with urllib.request.urlopen(req, timeout=timeout()) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read(800).decode("utf-8", "replace")
        raise LLMError("Il modello ha risposto con errore %d: %s" % (e.code, detail[:400]))
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        if isinstance(e, socket.timeout) or isinstance(getattr(e, "reason", None), socket.timeout) or "timed out" in str(e):
            raise LLMError("Il modello non ha risposto entro %d secondi. Riprova; se succede spesso con un modello che ragiona a lungo, il gestore può "
                           "alzare l'attesa o abbassare il ragionamento nel pannello, Impostazioni della piattaforma, gruppo «Modelli»." % timeout())
        raise LLMError("Modello non raggiungibile: %s" % getattr(e, "reason", e))
    except ValueError:
        raise LLMError("Risposta del modello non leggibile.")


# ---------- Anthropic Messages ----------

def _anthropic_messages(messages):
    out = []
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            if m.get("raw") is not None:
                content = m["raw"]  # blocchi originali, da rimandare invariati
            else:
                content = [{"type": "text", "text": m["content"]}] if m.get("content") else []
                for tc in m.get("tool_calls") or []:
                    content.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["input"]})
            out.append({"role": "assistant", "content": content or [{"type": "text", "text": "."}]})
        else:  # risultati degli strumenti: tutti nello stesso messaggio utente
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) \
                    and out[-1]["content"] and out[-1]["content"][0].get("type") == "tool_result":
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return out


def _call_anthropic(s, system, messages, tools):
    body = {"model": s["model"], "max_tokens": config.get_int("SFB_MAX_TOKENS", 2048), "system": system,
            "messages": _anthropic_messages(messages)}
    if tools:
        body["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["schema"]} for t in tools]
    res = _post(s["base"].rstrip("/") + "/v1/messages",
                {"x-api-key": s["key"], "anthropic-version": "2023-06-01"}, body)
    content = res.get("content") or []
    text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
    calls = [{"id": b["id"], "name": b["name"], "input": b.get("input") or {}} for b in content if b.get("type") == "tool_use"]
    usage = res.get("usage") or {}
    return {"text": text, "tool_calls": calls, "raw": content,
            "usage": (int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0))}


# ---------- Chat Completions (Kimi, ChatGPT) ----------

def _openai_messages(system, messages):
    out = [{"role": "system", "content": system}]
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            if m.get("raw") is not None:
                out.append(m["raw"])  # invariato: alcuni modelli richiedono i loro campi di ragionamento
            else:
                msg = {"role": "assistant", "content": m.get("content") or ""}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [{"id": tc["id"], "type": "function",
                                          "function": {"name": tc["name"], "arguments": json.dumps(tc["input"], ensure_ascii=False)}}
                                         for tc in m["tool_calls"]]
                out.append(msg)
        else:
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
    return out


def _call_openai(s, system, messages, tools, post=None):
    body = {"model": s["model"], "messages": _openai_messages(system, messages)}
    if s.get("effort") in ("low", "high", "max"):
        body["reasoning_effort"] = s["effort"]
    if tools:
        body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                         "parameters": t["schema"]}} for t in tools]
    res = (post or _post)(s["base"].rstrip("/") + "/chat/completions", {"Authorization": "Bearer " + s["key"]}, body)
    try:
        msg = res["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise LLMError("Risposta del modello senza contenuto.")
    calls = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {"_argomenti_non_validi": str(fn.get("arguments"))[:500]}
        calls.append({"id": tc.get("id") or "call", "name": fn.get("name") or "", "input": args})
    usage = res.get("usage") or {}
    return {"text": msg.get("content") or "", "tool_calls": calls, "raw": msg,
            "usage": (int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0))}


def chat(model_id, system, messages, tools, tenant_keys=None, allow_platform=True, post=None):
    """`post`: funzione che recapita la richiesta al posto della chiamata diretta (modello sul computer del cliente)."""
    s = settings(model_id, tenant_keys, allow_platform)
    if model_id == "locale" and not s["base"] and not post:
        raise LLMError("Nessun modello locale disponibile: serve Ollama accanto al server (il gestore ne scrive l'indirizzo nel pannello, "
                       "Impostazioni della piattaforma, gruppo «Modelli») oppure sul computer dell'azienda, con il launcher aperto.")
    if not s["key"]:
        if not allow_platform:
            raise LLMError("Manca la chiave API di questo modello: un amministratore la inserisce nel Centro di controllo, "
                           "scheda Collegamenti, alla voce «Chiavi API dei modelli».")
        raise LLMError("Manca la chiave API per questo modello (%s): il gestore la inserisce nel pannello, Impostazioni della piattaforma, "
                       "gruppo «Modelli»; oppure un amministratore mette la chiave dell'azienda nei Collegamenti." % s["key_name"])
    if not s["model"]:
        raise LLMError("Manca il nome del modello ChatGPT: il gestore lo scrive nel pannello, Impostazioni della piattaforma, "
                       "gruppo «Modelli» (i nomi cambiano spesso: va copiato dal sito di OpenAI).")
    if s["kind"] == "anthropic":
        return _call_anthropic(s, system, messages, tools)
    return _call_openai(s, system, messages, tools, post)
