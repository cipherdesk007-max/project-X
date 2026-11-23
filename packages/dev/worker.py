"""Pacchetto locale «dev»: l'Agente Dev lavora in UNA cartella scelta dal cliente sul suo computer.

Regole di sicurezza, applicate qui e non sul server:
- si legge e si scrive solo dentro la cartella scelta (niente percorsi che ne escono, niente collegamenti verso fuori);
- il server non può mandare comandi: può solo chiedere, per nome, uno dei comandi che il cliente ha scritto nel launcher.
"""
import os
import subprocess
from pathlib import Path

SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".DS_Store"}
MAX_READ, MAX_WRITE, MAX_LIST = 60000, 200000, 300


class WorkerError(Exception):
    pass


def _root(env):
    folder = str(env.config.get("cartella") or "")
    if not folder or not Path(folder).is_dir():
        raise WorkerError("La cartella di lavoro dell'Agente Dev non è stata scelta su questo computer (launcher: cartella-dev).")
    return Path(folder).resolve()


def _inside(env, rel):
    root = _root(env)
    rel = str(rel or "").replace("\\", "/").strip()
    if not rel or rel.startswith("/") or "\x00" in rel or (len(rel) > 1 and rel[1] == ":"):
        raise WorkerError("Percorso non valido: indica un percorso relativo alla cartella di lavoro.")
    path = (root / rel).resolve()
    if path != root and root not in path.parents:
        raise WorkerError("Percorso fuori dalla cartella di lavoro: rifiutato.")
    return root, path


def dev_elenca_file(args, env):
    root, out = _root(env), []
    for folder, dirs, files in os.walk(str(root)):
        dirs[:] = sorted(d for d in dirs if d not in SKIP and not d.startswith("."))
        for name in sorted(files):
            p = Path(folder) / name
            if name in SKIP or p.is_symlink():
                continue
            out.append("%s (%d byte)" % (p.relative_to(root).as_posix(), p.stat().st_size))
            if len(out) >= MAX_LIST:
                return {"text": "\n".join(out) + "\n(elenco troncato a %d file)" % MAX_LIST}
    return {"text": "\n".join(out) or "La cartella di lavoro è vuota."}


def dev_leggi_file(args, env):
    _, path = _inside(env, args.get("percorso"))
    if not path.is_file():
        raise WorkerError("File non trovato.")
    data = path.read_bytes()[:MAX_READ + 1]
    if b"\x00" in data:
        raise WorkerError("Il file non è di testo.")
    return {"text": data[:MAX_READ].decode("utf-8", "replace") + ("\n(file troncato)" if len(data) > MAX_READ else "")}


def dev_scrivi_file(args, env):
    root, path = _inside(env, args.get("percorso"))
    text = str(args.get("contenuto") if args.get("contenuto") is not None else "")
    if len(text.encode("utf-8")) > MAX_WRITE:
        raise WorkerError("Contenuto troppo grande (massimo %d KB)." % (MAX_WRITE // 1000))
    if any(part in SKIP or part.startswith(".") for part in path.relative_to(root).parts):
        raise WorkerError("Non scrivo in cartelle o file nascosti.")
    path.parent.mkdir(parents=True, exist_ok=True)
    if (path.parent.resolve() != root and root not in path.parent.resolve().parents) or path.is_symlink():
        raise WorkerError("Percorso fuori dalla cartella di lavoro: rifiutato.")
    tmp = path.with_name(path.name + ".tmp-alvea")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))
    return {"text": "Scritto %s (%d byte)." % (path.relative_to(root).as_posix(), len(text.encode("utf-8")))}


def dev_esegui_comando(args, env):
    root, name = _root(env), str(args.get("comando") or "")
    allowed = env.config.get("comandi") or {}
    command = allowed.get(name)
    if not isinstance(command, list) or not command:
        raise WorkerError("Comando «%s» non permesso su questo computer. Permessi: %s." % (name[:40], ", ".join(sorted(allowed)) or "nessuno"))
    try:
        r = subprocess.run([str(c) for c in command], cwd=str(root), capture_output=True, text=True, timeout=int(env.config.get("timeout") or 120))
    except subprocess.TimeoutExpired:
        raise WorkerError("Il comando «%s» ha superato il tempo massimo." % name)
    except OSError as e:
        raise WorkerError("Il comando «%s» non parte: %s" % (name, e))
    out = (r.stdout + ("\n" + r.stderr if r.stderr else "")).strip()
    return {"text": "Comando «%s» terminato con codice %d.\n%s" % (name, r.returncode, out[-4000:])}


def check(env):
    folder = str(env.config.get("cartella") or "")
    if not folder:
        return True, "da configurare: scegli la cartella di lavoro (launcher: cartella-dev)"
    if not Path(folder).is_dir():
        return False, "la cartella di lavoro scelta non esiste più"
    return True, "cartella di lavoro: %s; comandi permessi: %s" % (folder, ", ".join(sorted(env.config.get("comandi") or {})) or "nessuno")


TOOLS = {"dev_elenca_file": dev_elenca_file, "dev_leggi_file": dev_leggi_file, "dev_scrivi_file": dev_scrivi_file,
         "dev_esegui_comando": dev_esegui_comando}
