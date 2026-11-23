"""Console del gestore: comandi di manutenzione lanciati dal pannello.

Non è un terminale libero, di proposito: un terminale nel pannello darebbe il server intero a chiunque rubi la password
del gestore. Qui si possono lanciare solo i comandi di questo elenco, senza argomenti scritti a mano; per tutto il resto
c'è l'accesso SSH al server. Ogni comando lanciato finisce nel registro.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time

from . import audit, capacity, config, ops
from .errors import ApiError

COMMANDS = [
    ("capacita", "Quanto regge il server", "Memoria, processore, disco e stima delle persone che possono lavorare insieme.", ["capacita"]),
    ("collaudo", "Collaudo", "Controlla sito, applicazione, agenti, pacchetti e launcher di questo server.", ["collaudo"]),
    ("produzione", "Controllo di produzione", "Dice cosa manca prima di aprire ai clienti.", ["controlla-produzione"]),
    ("registro", "Verifica del registro", "Controlla che nessuna riga del registro sia stata cambiata.", ["verifica-registro"]),
    ("aziende", "Elenco delle aziende", "Aziende, pacchetto e stato.", ["aziende"]),
    ("firma", "Firma dei pacchetti", "Rifirma i pacchetti per i computer dei clienti dopo un aggiornamento.", ["firma-pacchetti"]),
]
TIMEOUT = 180
_busy = threading.Lock()


def listing():
    r = capacity.report()
    return {"commands": [{"id": c[0], "label": c[1], "does": c[2]} for c in COMMANDS],
            "capacity": {"people": r["people"], "lines": [{"state": s, "text": t} for s, t in r["lines"]], "assumptions": r["assumptions"]}}


def run(con, user, command):
    entry = next((c for c in COMMANDS if c[0] == command), None)
    if not entry:
        raise ApiError(400, "Comando non previsto. Dal pannello si lanciano solo i comandi dell'elenco.")
    if not _busy.acquire(blocking=False):
        raise ApiError(409, "Un altro comando è ancora in corso: aspetta che finisca.")
    try:
        audit.record(con, ops.PLATFORM, user["id"], "console_comando", {"comando": command})
        started = time.time()
        try:
            done = subprocess.run([sys.executable, "manage.py"] + entry[3], cwd=str(config.ROOT), capture_output=True, text=True, timeout=TIMEOUT)
            code, output = done.returncode, (done.stdout + ("\n" + done.stderr if done.stderr.strip() else ""))
        except subprocess.TimeoutExpired:
            code, output = 124, "Il comando non ha finito entro %d secondi ed è stato fermato." % TIMEOUT
        return {"command": command, "code": code, "seconds": round(time.time() - started, 1), "output": output[-20000:]}
    finally:
        _busy.release()
