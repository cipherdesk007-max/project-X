"""Modello finto per la modalità demo e per i test. Non è intelligenza: sono regole fisse.

Parla i due formati veri (Messages di Anthropic e Chat Completions), così i test
esercitano lo stesso codice che in produzione chiama Claude, Kimi e ChatGPT.
Rifiuta le richieste senza le intestazioni di autenticazione attese.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _after_colon(text):
    return text.split(":", 1)[1].strip() if ":" in text else text.strip()


def decide(text, tool_names):
    """Sceglie uno strumento in base a parole chiave. Ritorna (nome, argomenti) oppure None."""
    low = text.lower()

    def has(name):
        return name in tool_names

    if has("memoria_proponi") and re.search(r"\bricorda(ti)? che\b", low):
        return "memoria_proponi", {"fatto": re.split(r"(?i)ricorda(?:ti)? che", text, maxsplit=1)[1].strip(" .:")}
    if has("memoria_cerca") and re.search(r"(cerca in memoria|cosa sai)", low):
        return "memoria_cerca", {"domanda": _after_colon(text)}
    if has("email_leggi_recenti") and re.search(r"(leggi|controlla|guarda).*(email|posta|mail)", low):
        return "email_leggi_recenti", {"quanti": 5}
    if has("fogli_leggi") and "docs.google.com/spreadsheets" in low:
        return "fogli_leggi", {"foglio": re.search(r"https?://\S+", text).group(0)}
    if has("crm_aggiungi_lead") and re.search(r"(aggiungi|nuovo|crea|salva).*lead", low):
        m = EMAIL_RE.search(text)
        name = re.split(r"(?i)lead", text, maxsplit=1)[1]
        name = EMAIL_RE.sub("", name).strip(" :,.-") or "Senza nome"
        args = {"nome": name}
        if m:
            args["email"] = m.group(0)
        return "crm_aggiungi_lead", args
    if has("crm_elenca_lead") and re.search(r"(elenca|lista|mostra|quali|vedi).*lead", low):
        return "crm_elenca_lead", {}
    if has("telegram_invia_messaggio") and "telegram" in low and re.search(r"(manda|invia|scrivi)", low):
        return "telegram_invia_messaggio", {"testo": _after_colon(text)}
    if has("whatsapp_invia_messaggio") and "whatsapp" in low and re.search(r"(manda|invia|scrivi)", low):
        num = re.search(r"\+?\d[\d ]{7,16}\d", text)
        return "whatsapp_invia_messaggio", {"numero": num.group(0) if num else "", "testo": _after_colon(text)}
    if has("email_invia") and EMAIL_RE.search(text) and re.search(r"(manda|invia|scrivi)", low):
        return "email_invia", {"a": EMAIL_RE.search(text).group(0), "oggetto": "Messaggio da SalesForB",
                               "testo": _after_colon(text)}
    if has("calendario_crea_evento") and re.search(r"(fissa|crea|aggiungi).*(appuntamento|evento|riunione)", low):
        start = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT10:00")
        return "calendario_crea_evento", {"titolo": _after_colon(text)[:120], "inizio": start}
    if has("calendario_elenca_eventi") and re.search(r"(appuntamenti|eventi|agenda)", low):
        return "calendario_elenca_eventi", {}
    if has("contenuti_salva_bozza") and "bozza" in low and "salva" in low:
        return "contenuti_salva_bozza", {"canale": "generico", "titolo": "Bozza demo", "testo": _after_colon(text)}
    if has("report_riepilogo") and re.search(r"(report|riepilogo)", low):
        return "report_riepilogo", {"giorni": 7}
    if has("video_monta") and re.search(r"(monta|crea|fai|prepara).*(video|reel|filmato)", low):
        return "video_monta", {"titolo": _after_colon(text)[:80] if ":" in text else "Video demo", "clip": "tutte",
                               "qualita": "bozza" if "bozza" in low else "alta"}
    if has("video_elenca_materiale") and re.search(r"(materiale|clip|archivio)", low):
        return "video_elenca_materiale", {}
    if has("dev_esegui_comando") and re.search(r"esegui (il comando )?(\w+)", low):
        return "dev_esegui_comando", {"comando": re.search(r"esegui (?:il comando )?(\w+)", low).group(1)}
    if has("dev_scrivi_file") and re.search(r"scrivi (il )?file (\S+):", low):
        return "dev_scrivi_file", {"percorso": re.search(r"(?i)scrivi (?:il )?file (\S+):", text).group(1), "contenuto": _after_colon(text)}
    if has("dev_leggi_file") and re.search(r"leggi (il )?file (\S+)", low):
        return "dev_leggi_file", {"percorso": re.search(r"(?i)leggi (?:il )?file (\S+)", text).group(1)}
    if has("dev_elenca_file") and re.search(r"(elenca|lista|mostra).*(file|cartella)", low):
        return "dev_elenca_file", {}
    if has("team_elenca_utenti") and re.search(r"(utenti|team|ruoli)", low):
        return "team_elenca_utenti", {}
    url = re.search(r"https?://\S+", text)
    if has("ricerca_leggi_pagina") and url:
        return "ricerca_leggi_pagina", {"url": url.group(0)}
    return None


def _plain(text):
    return ("[DEMO] Ho ricevuto: «%s». In modalità demo non c'è un modello vero: rispondo con regole fisse. "
            "Per risposte reali metti SFB_DEMO=0 e una chiave API nel file .env." % text[:200])


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # come Ollama: elenco dei modelli presenti
        data = json.dumps({"object": "list", "data": [{"id": "llama3.1:latest", "object": "model"}]}).encode("utf-8")
        self.send_response(200 if self.path.rstrip("/").endswith("/v1/models") else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8"))
        self.server.requests.append({"path": self.path, "body": req})
        delay = os.environ.get("SFB_DEMO_DELAY_MS", "900")  # un modello vero impiega secondi: in demo si può imitare l'attesa
        if delay.isdigit() and int(delay):
            time.sleep(min(int(delay), 10000) / 1000.0)
        tokens = {"input": len(json.dumps(req)) // 4, "output": 20}
        if self.path.endswith("/v1/messages"):
            if not self.headers.get("x-api-key") or not self.headers.get("anthropic-version"):
                return self._json(401, {"error": "intestazioni mancanti"})
            names = [t["name"] for t in req.get("tools", [])]
            last = req["messages"][-1]
            usage = {"input_tokens": tokens["input"], "output_tokens": tokens["output"]}
            if isinstance(last["content"], list) and last["content"] and last["content"][0].get("type") == "tool_result":
                text = "[DEMO] " + " ".join(str(b.get("content")) for b in last["content"])[:600]
                return self._json(200, {"role": "assistant", "content": [{"type": "text", "text": text}],
                                        "stop_reason": "end_turn", "usage": usage})
            user_text = last["content"] if isinstance(last["content"], str) else ""
            pick = decide(user_text, names)
            if pick:
                return self._json(200, {"role": "assistant", "stop_reason": "tool_use", "usage": usage, "content": [
                    {"type": "tool_use", "id": "toolu_demo_%d" % len(self.server.requests), "name": pick[0], "input": pick[1]}]})
            return self._json(200, {"role": "assistant", "content": [{"type": "text", "text": _plain(user_text)}],
                                    "stop_reason": "end_turn", "usage": usage})
        if self.path.endswith("/chat/completions"):
            if not (self.headers.get("Authorization") or "").startswith("Bearer "):
                return self._json(401, {"error": "intestazioni mancanti"})
            names = [t["function"]["name"] for t in req.get("tools", [])]
            last = req["messages"][-1]
            usage = {"prompt_tokens": tokens["input"], "completion_tokens": tokens["output"]}
            if last["role"] == "tool":
                results = [m["content"] for m in req["messages"] if m["role"] == "tool"]
                msg = {"role": "assistant", "content": "[DEMO] " + " ".join(results[-3:])[:600]}
                return self._json(200, {"choices": [{"index": 0, "message": msg, "finish_reason": "stop"}], "usage": usage})
            pick = decide(last.get("content") or "", names)
            if pick:
                msg = {"role": "assistant", "content": None, "reasoning_content": "demo", "tool_calls": [
                    {"id": "call_demo_%d" % len(self.server.requests), "type": "function",
                     "function": {"name": pick[0], "arguments": json.dumps(pick[1], ensure_ascii=False)}}]}
                return self._json(200, {"choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls"}], "usage": usage})
            msg = {"role": "assistant", "content": _plain(last.get("content") or "")}
            return self._json(200, {"choices": [{"index": 0, "message": msg, "finish_reason": "stop"}], "usage": usage})
        self._json(404, {"error": "percorso sconosciuto"})


def start():
    """Avvia il modello finto su una porta libera di localhost. Ritorna (server, indirizzo base)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.requests = []
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:%d" % server.server_address[1]
