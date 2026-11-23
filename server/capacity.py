"""Quanto regge questo server: memoria, processore, disco e una stima di quante persone possono lavorarci insieme.

I numeri di base sono misurati, non supposti (24 novembre 2026, 2 core, modello finto che risponde subito):
240 chat partite nello stesso istante, tutte riuscite, circa 125 chat al secondo, 26 MB a riposo e circa 0,3 MB
in più per ogni chat in corso. La stima usa un quarto di quella velocità, per stare larghi.
Quello che la misura NON contiene: i limiti del fornitore del modello (richieste al minuto della tua chiave),
che di solito arrivano molto prima dei limiti di questo server.
"""
from __future__ import annotations

import os
import shutil

from . import catalog, config

CHATS_PER_SECOND = 30      # un quarto dei 125 misurati
MB_PER_CHAT = 0.3          # memoria di una chat in attesa della risposta del modello
BASE_MB = 120              # programma, database aperto, margine
SECONDS_BETWEEN_MESSAGES = 30   # una persona che lavora fitto manda un messaggio ogni mezzo minuto
MODEL_SECONDS = 10         # quanto ci mette in media un modello vero a rispondere
VIDEO_SECONDS_PER_MINUTE = 24   # misurato: un minuto di video verticale su 2 core


def machine():
    try:
        ram = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1e9
    except (ValueError, OSError, AttributeError):
        ram = 0.0
    try:
        free = shutil.disk_usage(str(config.db_path().parent if config.db_path().parent.exists() else config.ROOT)).free / 1e9
    except OSError:
        free = 0.0
    return {"cores": os.cpu_count() or 1, "ram_gb": round(ram, 1), "disk_free_gb": round(free, 1),
            "ffmpeg": bool(shutil.which("ffmpeg")), "ollama": bool(config.get("OLLAMA_BASE_URL"))}


def report(m=None):
    """Ritorna {"machine", "people", "chats", "videos", "companies_full", "lines": [(stato, testo)], "assumptions": [...]}.
    Stato: 'OK', 'ATTENZIONE' (funziona ma stretto) oppure 'NON REGGE'."""
    m = m or machine()
    lines = []
    usable_mb = max(0.0, m["ram_gb"] * 1000 * 0.5 - BASE_MB)  # metà della memoria al programma, il resto al sistema e a FFmpeg
    chats_ram = int(usable_mb / MB_PER_CHAT)
    chats_cpu = int(CHATS_PER_SECOND * MODEL_SECONDS * (1 if m["cores"] >= 2 else 0.5))
    chats = max(0, min(chats_ram, chats_cpu)) if m["ram_gb"] else chats_cpu
    people = int(chats * SECONDS_BETWEEN_MESSAGES / MODEL_SECONDS)
    lines.append(("OK" if m["ram_gb"] >= 2 else ("ATTENZIONE" if m["ram_gb"] >= 1 else "NON REGGE"),
                  "Memoria: %.1f GB. %s" % (m["ram_gb"], "Basta per le chat e per un montaggio video alla volta." if m["ram_gb"] >= 2
                                            else ("Basta per le chat; il montaggio video sul server è a rischio." if m["ram_gb"] >= 1
                                                  else "Troppo poca: serve almeno 1 GB, meglio 2."))))
    lines.append(("OK" if m["cores"] >= 2 else "ATTENZIONE", "Processore: %d core.%s" % (m["cores"], "" if m["cores"] >= 2 else " Con un solo core un montaggio video rallenta tutte le chat.")))
    lines.append(("OK" if m["disk_free_gb"] >= 20 else ("ATTENZIONE" if m["disk_free_gb"] >= 5 else "NON REGGE"),
                  "Disco: %.0f GB liberi.%s" % (m["disk_free_gb"], "" if m["disk_free_gb"] >= 20 else " Foto e video dei clienti lo riempiono in fretta.")))
    lines.append(("OK", "Chat: circa %d risposte in corso insieme, cioè circa %d persone che lavorano nello stesso momento." % (chats, people)))
    videos = 0
    if not m["ffmpeg"]:
        lines.append(("NON REGGE", "Montaggio video sul server: manca FFmpeg. Il Video Editor funziona solo sui computer dei clienti."))
    else:
        videos = max(1, m["cores"] // 2) if m["ram_gb"] >= 1 or not m["ram_gb"] else 0
        speed = VIDEO_SECONDS_PER_MINUTE * 2.0 / max(1, min(m["cores"], 8))
        lines.append(("OK" if videos and m["ram_gb"] >= 2 else "ATTENZIONE",
                      "Montaggio video sul server: %d alla volta, circa %d secondi per ogni minuto di video; gli altri aspettano in coda." % (max(videos, 1), round(speed))))
    need = catalog.load().get("local_model") or {}
    if m["ollama"]:
        enough = m["ram_gb"] >= need.get("ram_gb", 8) and m["cores"] >= need.get("cores", 4)
        lines.append(("OK" if enough else "NON REGGE", "Modello locale sul server (Ollama): servono %d GB di memoria e %d core%s. Risponde a una persona alla volta." % (
            need.get("ram_gb", 8), need.get("cores", 4), "" if enough else ", qui non ci sono")))
    top = max((p.get("limits") or {}).get("archivio_mb", 0) for p in catalog.load()["packages"]) / 1000 or 1
    full = int(m["disk_free_gb"] * 0.8 / top)
    lines.append(("OK" if full >= 5 else "ATTENZIONE", "Archivio: lo spazio libero basta per %d %s tutti i %d GB del pacchetto più grande." % (full, "azienda che riempie" if full == 1 else "aziende che riempiono", top)))
    return {"machine": m, "people": people, "chats": chats, "videos": videos, "companies_full": full, "lines": lines,
            "assumptions": ["Stima, non garanzia: misurata con un modello finto che risponde subito.",
                            "Una persona che lavora manda un messaggio ogni %d secondi; un modello vero risponde in circa %d secondi." % (SECONDS_BETWEEN_MESSAGES, MODEL_SECONDS),
                            "Non sono compresi i limiti del fornitore del modello (richieste al minuto della tua chiave): di solito arrivano prima.",
                            "Le persone iscritte possono essere molte di più: contano quelle che scrivono nello stesso momento."]}


def text(r=None):
    r = r or report()
    return "\n".join(["%-10s %s" % line for line in r["lines"]] + [""] + ["- " + a for a in r["assumptions"]])
