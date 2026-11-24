#!/usr/bin/env python3
"""Comandi di gestione di Alvea.

  python3 manage.py init                 crea database, file .env e il gestore della piattaforma
  python3 manage.py demo                 crea tre aziende di prova, una per pacchetto
  python3 manage.py aziende              elenca le aziende
  python3 manage.py pacchetto ID NOME    cambia il pacchetto di un'azienda (start, business, completo)
  python3 manage.py verifica-registro    ricalcola la catena del registro di ogni azienda
"""
from __future__ import annotations

import argparse
import getpass
import secrets
import shutil
import sys

from server import audit, catalog, config, db, ops
from server.errors import ApiError


def _ensure_env():
    env, example = config.ROOT / ".env", config.ROOT / ".env.example"
    if env.exists() or not example.exists():
        return
    shutil.copy(str(example), str(env))
    try:
        from cryptography.fernet import Fernet
        text = env.read_text(encoding="utf-8").replace("SFB_SECRET_KEY=", "SFB_SECRET_KEY=" + Fernet.generate_key().decode("ascii"), 1)
        env.write_text(text, encoding="utf-8")
        print("Creato .env con una chiave per cifrare le credenziali dei collegamenti.")
    except BaseException as e:
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        print("Creato .env. Il pacchetto 'cryptography' non è installato o non funziona: le credenziali dei collegamenti "
              "resteranno in chiaro nel database finché non lo installi (pip3 install cryptography) e imposti SFB_SECRET_KEY.")
    try:
        env.chmod(0o600)
    except OSError:
        pass


def _con():
    config.load_env()
    con = db.connect()
    db.init(con)
    return con


def cmd_init(args):
    _ensure_env()
    con = _con()
    if con.execute("SELECT 1 FROM users WHERE role='superadmin'").fetchone():
        if not args.se_manca:
            print("Il gestore della piattaforma esiste già.")
        return 0
    print("Creo il gestore della piattaforma: è l'utente che crea le aziende e assegna i pacchetti.")
    email = args.email or input("Email del gestore: ")
    password = args.password or getpass.getpass("Password (almeno 10 caratteri): ")
    ops.create_user(con, None, email, args.nome or "Gestore", "superadmin", password)
    print("Gestore creato: %s" % email.strip().lower())
    return 0


def cmd_demo(args):
    con = _con()
    print("Aziende di prova (segna le password: non vengono salvate in chiaro da nessuna parte):")
    for pkg in catalog.load()["packages"]:
        email, password = "%s@demo.local" % pkg["id"], secrets.token_urlsafe(9)
        try:
            ops.create_tenant(con, "Demo " + pkg["name"], pkg["id"], email, "Admin " + pkg["name"], password)
            print("  pacchetto %-9s  email: %-22s password: %s" % (pkg["name"], email, password))
        except ApiError as e:
            print("  pacchetto %-9s  saltato: %s" % (pkg["name"], e.message))
    return 0


def cmd_aziende(args):
    con = _con()
    for t in con.execute("SELECT * FROM tenants ORDER BY id"):
        print("%3d  %-30s pacchetto: %-9s %s" % (t["id"], t["name"], t["package_id"], "attiva" if t["active"] else "sospesa"))
    return 0


def cmd_pacchetto(args):
    ops.update_tenant(_con(), args.id, package_id=args.pacchetto)
    print("Pacchetto aggiornato.")
    return 0


def cmd_verifica(args):
    con = _con()
    bad = 0
    ids = [ops.PLATFORM] + [r["id"] for r in con.execute("SELECT id FROM tenants ORDER BY id")]
    for tid in ids:
        ok, count, broken, last = audit.verify(con, tid)
        name = "piattaforma" if tid == ops.PLATFORM else "azienda %d" % tid
        if ok:
            print("OK      %-14s righe: %-5d ultimo hash: %s" % (name, count, last))
        else:
            bad += 1
            print("ROTTO   %-14s catena interrotta alla riga con id %s" % (name, broken))
    return 1 if bad else 0


def production_report():
    """Controlli prima di aprire ai clienti. Ritorna [(stato, testo)] con stato 'OK', 'DA FARE' (blocca) o 'CONSIGLIO'."""
    import shutil
    from server import config, local
    config.load_env()  # gli stessi valori che legge il server all'avvio
    if config.db_path().is_file():  # e quelli salvati dal gestore nel pannello, che valgono più del file
        from server import db, settings
        con = db.connect()
        try:
            settings.load(con)
        except Exception:
            pass  # database di una versione precedente: le impostazioni del pannello non ci sono ancora
        con.close()
    out, url = [], config.get("SFB_PUBLIC_URL", "")
    def add(ok, good, bad, block=True):
        out.append(("OK", good) if ok else ("DA FARE" if block else "CONSIGLIO", bad))
    add(not config.demo_mode(), "Modalità demo spenta: rispondono i modelli veri.", "SFB_DEMO=1: risponde il modello finto. Metti SFB_DEMO=0 nel file .env.")
    add(url.startswith("https://"), "Indirizzo pubblico in HTTPS: %s" % url, "SFB_PUBLIC_URL deve essere l'indirizzo https del server (ora: %s). Vedi deploy/Caddyfile." % (url or "vuoto"))
    add(config.get("SFB_HOST", "127.0.0.1") == "127.0.0.1" and config.get("SFB_TRUST_PROXY", "0") == "1",
        "Il server ascolta solo in locale, dietro il proxy.", "Con il proxy davanti servono SFB_HOST=127.0.0.1 e SFB_TRUST_PROXY=1, altrimenti i limiti per indirizzo non funzionano.")
    add(any(config.get(k) for k in ("ANTHROPIC_API_KEY", "MOONSHOT_API_KEY", "OPENAI_API_KEY", "OLLAMA_BASE_URL")),
        "Almeno un modello è configurato.", "Nessun modello: serve una chiave (ANTHROPIC_API_KEY, MOONSHOT_API_KEY, OPENAI_API_KEY) oppure OLLAMA_BASE_URL.")
    add(bool(config.get("SFB_SECRET_KEY")), "Le credenziali dei collegamenti sono cifrate.", "Manca SFB_SECRET_KEY: le credenziali dei clienti resterebbero in chiaro nel database.")
    add(bool(local.public_key()) and all(local.signed(n) for n in local.package_names()), "Pacchetti locali firmati.",
        "Pacchetti locali non firmati: esegui «python3 manage.py firma-pacchetti» (serve openssl).")
    add(not local.key_path().is_file(), "La chiave privata di firma non è su questo server.",
        "La chiave privata di firma è su questo server (%s): spostala su un altro computer e firma da lì con --chiave." % local.key_path())
    add(config.db_path().is_file(), "Database presente.", "Database assente: esegui «python3 manage.py init».")
    add(local.cloud_video_ready(), "FFmpeg presente: il montaggio video sui server è attivo.", "FFmpeg assente: il Video Editor funzionerà solo sui computer dei clienti.", block=False)
    add(bool(config.get("SFB_SMTP_HOST")), "Posta di servizio configurata: chi si registra conferma l'email.", "Senza SFB_SMTP_HOST chi si registra non conferma l'email.", block=False)
    stripe = [k for k in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET") if not config.get(k)]
    prices = [p for p in ("START", "BUSINESS", "COMPLETO") if config.get("STRIPE_PRICE_" + p)]
    add(not stripe and bool(prices), "Pagamenti con carta attivi per: %s." % ", ".join(p.capitalize() for p in prices),
        "Pagamenti con carta non attivi (mancano %s): la scadenza si sposta a mano dal pannello del gestore." % (", ".join(stripe) or "i prezzi dei pacchetti"), block=False)
    add(bool(shutil.which("openssl")), "openssl presente.", "openssl assente: non potrai firmare i pacchetti da questo computer.", block=False)
    return out


def cmd_produzione(args):
    report = production_report()
    for state, text in report:
        print("%-10s %s" % (state, text))
    todo = sum(1 for s, _ in report if s == "DA FARE")
    print("\n%s" % ("Pronto per i clienti." if not todo else "%d cose da fare prima di aprire ai clienti." % todo))
    return 1 if todo else 0


def acceptance(url, email=None, password=None, agents=False):
    """Collaudo di un server acceso: quello che deve funzionare subito dopo l'installazione. Ritorna [(riuscito, testo)].
    Senza credenziali controlla solo le parti pubbliche. Con `agents` scrive a ogni agente attivo (in produzione consuma token)."""
    import io
    import json
    import time
    import urllib.error
    import urllib.request
    import zipfile
    from server import local
    url, out = url.rstrip("/"), []

    def call(method, path, body=None, token=None, raw=False):
        headers = {"Content-Type": "application/json"} if body is not None else {}
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(url + path, json.dumps(body).encode() if body is not None else None, headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
                return r.status, dict(r.headers), data if raw else (json.loads(data) if data[:1] in (b"{", b"[") else data)
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()
        except (urllib.error.URLError, OSError) as e:
            return 0, {}, str(e).encode()

    def add(ok, text):
        out.append((bool(ok), text))

    status, _, health = call("GET", "/api/health")
    add(status == 200, "Il server risponde su %s%s" % (url, " (modalità demo)" if status == 200 and health.get("demo") else ""))
    if status != 200:
        return out
    status, headers, _ = call("GET", "/", raw=True)
    csp = headers.get("Content-Security-Policy", "")
    add(status == 200 and "default-src 'self'" in csp and "http" not in csp, "Pagina servita con regole di sicurezza che ammettono solo file di questo server")
    missing = [p for p in ("/app", "/sito.css", "/sito.js", "/app.js", "/app.css", "/office.js", "/vendor/mini3d.js", "/logo.png", "/logo-alvea.png", "/icon.png") if call("GET", p, raw=True)[0] != 200]
    add(not missing, "Sito, applicazione e marchio presenti" if not missing else "File della schermata mancanti: %s" % ", ".join(missing))
    agents_all = catalog.load()["agents"]
    status, _, cat = call("GET", "/api/launcher/catalog")
    add(status == 200 and len(cat.get("agents", [])) == len(agents_all), "Catalogo pubblico con tutti i %d agenti" % len(agents_all))
    names = local.package_names()
    unsigned = [n for n in names if not local.signed(n)]
    add(names and not unsigned and local.public_key(), "Pacchetti locali presenti e firmati: %s" % ", ".join(names) if not unsigned
        else "Pacchetti non firmati: %s (esegui «python3 manage.py firma-pacchetti»)" % ", ".join(unsigned))
    need = sorted({a["run"]["local"]["package"] for a in agents_all if (a["run"].get("local") or {}).get("ready")} | {catalog.load()["local_model"]["package"]})
    lost = [n for n in need if n not in names]
    add(not lost, "Ogni agente che può girare sul computer del cliente ha il suo pacchetto" if not lost else "Pacchetti mancanti nella cartella packages/: %s" % ", ".join(lost))
    served = [n for n in names if call("GET", "/api/launcher/packages/" + n)[0] != 200]
    add(not served, "Il server consegna i pacchetti firmati ai launcher" if not served else "Pacchetti che il server non consegna: %s" % ", ".join(served))
    status, _, data = call("GET", "/api/launcher/download", raw=True)
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        inside = json.loads(z.read("alvea-launcher/server.json"))
        ok = status == 200 and "alvea-launcher/alvea_launcher.py" in z.namelist() and bool(inside.get("public_key"))
        where = inside.get("server", "")
    except (zipfile.BadZipFile, KeyError, ValueError):
        ok, where = False, ""
    add(ok, "Launcher scaricabile, con la chiave per controllare i pacchetti (si collegherà a %s)" % where if ok else "Il launcher non si scarica o è incompleto")
    add(call("GET", "/api/me")[0] == 401, "Senza accesso i dati non si leggono")
    add(call("POST", "/api/billing/webhook", {"id": "evt_collaudo", "type": "invoice.paid", "data": {"object": {}}})[0] in (400, 503),
        "Un avviso di pagamento senza firma viene rifiutato")
    if email and password:
        status, _, login = call("POST", "/api/login", {"email": email, "password": password})
        add(status == 200, "Accesso riuscito come %s" % email if status == 200 else "Accesso non riuscito come %s" % email)
        if status == 200:
            token = login["token"]
            me = call("GET", "/api/me", token=token)[2]
            if not me.get("tenant"):
                status, _, sets = call("GET", "/api/admin/settings", token=token)
                groups = sorted({f["group"] for f in sets.get("fields", [])}) if status == 200 else []
                add(len(groups) >= 6, "Impostazioni della piattaforma raggiungibili: %s" % ", ".join(groups))
            elif agents:
                names = {a["id"]: a["name"] for a in agents_all}
                model = next((m["id"] for m in me.get("models", []) if m.get("configured")), "claude")
                failed = []
                for agent_id in me.get("entitled", []):
                    status = call("POST", "/api/chat", {"message": "Collaudo: rispondi solo «ok».", "agent_id": agent_id, "model": model}, token)[0]
                    if status == 429:  # limite di messaggi al minuto: si aspetta e si riprova una volta
                        time.sleep(61)
                        status = call("POST", "/api/chat", {"message": "Collaudo: rispondi solo «ok».", "agent_id": agent_id, "model": model}, token)[0]
                    if status != 200:
                        failed.append("%s (%s)" % (names.get(agent_id, agent_id), status))
                add(me.get("entitled") and not failed, "Tutti i %d agenti accesi rispondono con il modello «%s»" % (len(me.get("entitled", [])), model) if not failed
                    else "Agenti che non rispondono: %s" % ", ".join(failed))
    return out


def cmd_collaudo(args):
    config.load_env()
    url = args.url or "http://127.0.0.1:%s" % config.get("SFB_PORT", "8787")
    report = acceptance(url, args.email, args.password, args.agenti)
    for ok, text in report:
        print("%-7s %s" % ("OK" if ok else "ERRORE", text))
    bad = sum(1 for ok, _ in report if not ok)
    print("\nCollaudo superato: %d controlli." % len(report) if not bad else "\nCollaudo NON superato: %d controlli falliti su %d." % (bad, len(report)))
    return 1 if bad else 0


def model_check():
    """Manda una domanda vera a ogni modello che ha una chiave: dice se risponde, in quanto tempo e con quanti token.
    Ritorna [(stato, testo)] con stato 'OK', 'ERRORE' oppure '-' (non configurato). Consuma pochi token per modello."""
    import time
    from server import llm, settings
    config.load_env()
    if config.db_path().is_file():
        con = db.connect()
        try:
            settings.load(con)  # le chiavi scritte nel pannello valgono più del file
        except Exception:
            pass
        con.close()
    if config.demo_mode() and not llm.DEMO_BASE:
        from server import fake_llm
        llm.DEMO_SERVER, llm.DEMO_BASE = fake_llm.start()
    out = []
    for m in catalog.load()["models"]:
        s = llm.settings(m["id"], None, True)
        if not llm.configured(m["id"], None, True):
            out.append(("-", "%s: non configurato (manca %s)." % (m["name"], s["key_name"])))
            continue
        started = time.time()
        try:
            res = llm.chat(m["id"], "Sei un controllo tecnico. Rispondi con una sola parola.", [{"role": "user", "content": "Scrivi soltanto: ok"}], [], None, True)
            tokens = sum(res.get("usage") or (0, 0))
            out.append(("OK", "%s (%s): ha risposto «%s» in %.1f secondi%s." % (m["name"], s["model"], " ".join(str(res.get("text") or "").split())[:60], time.time() - started,
                                                                            ", %d token" % tokens if tokens else "")))
        except llm.LLMError as e:
            out.append(("ERRORE", "%s (%s): %s" % (m["name"], s["model"] or "nessun nome di modello", str(e)[:300])))
    return out


def cmd_modelli(args):
    report = model_check()
    for state, text in report:
        print("%-7s %s" % (state, text))
    if config.demo_mode():
        print("\nATTENZIONE: SFB_DEMO=1, ha risposto il modello finto. Per provare i modelli veri metti SFB_DEMO=0 nel file .env.")
    return 1 if any(s == "ERRORE" for s, _ in report) else 0


def cmd_capacita(args):
    config.load_env()
    from server import capacity
    print(capacity.text())
    return 0


def cmd_firma(args):
    from server import local
    names = local.sign_all(args.chiave)
    print("Pacchetti firmati: %s" % ", ".join(names))
    print("Chiave privata: %s" % (args.chiave or local.key_path()))
    print("In produzione tieni la chiave privata FUORI dal server e firma da un altro computer: chi ruba il server non deve poter firmare.")
    return 0


def main():
    p = argparse.ArgumentParser(description="Gestione di Alvea")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init")
    s.add_argument("--email")
    s.add_argument("--password")
    s.add_argument("--nome")
    s.add_argument("--se-manca", action="store_true", help="non fare nulla se il gestore esiste già")
    s.set_defaults(fn=cmd_init)
    sub.add_parser("demo").set_defaults(fn=cmd_demo)
    sub.add_parser("aziende").set_defaults(fn=cmd_aziende)
    s = sub.add_parser("pacchetto")
    s.add_argument("id", type=int)
    s.add_argument("pacchetto", choices=catalog.package_ids())
    s.set_defaults(fn=cmd_pacchetto)
    sub.add_parser("verifica-registro").set_defaults(fn=cmd_verifica)
    sub.add_parser("controlla-produzione", help="dice cosa manca prima di aprire il server ai clienti").set_defaults(fn=cmd_produzione)
    sub.add_parser("prova-modelli", help="manda una domanda vera a ogni modello configurato e dice se risponde").set_defaults(fn=cmd_modelli)
    sub.add_parser("capacita", help="memoria, processore, disco e quante persone regge questo server").set_defaults(fn=cmd_capacita)
    s = sub.add_parser("collaudo", help="prova un server acceso: pagina, agenti, pacchetti, launcher")
    s.add_argument("--url", help="indirizzo del server (se manca: quello locale)")
    s.add_argument("--email")
    s.add_argument("--password")
    s.add_argument("--agenti", action="store_true", help="scrive a ogni agente attivo dell'azienda: in produzione consuma token")
    s.set_defaults(fn=cmd_collaudo)
    s = sub.add_parser("firma-pacchetti", help="firma i pacchetti locali che i launcher dei clienti scaricano")
    s.add_argument("--chiave", help="percorso della chiave privata (tienila fuori dal server); se manca viene creata")
    s.set_defaults(fn=cmd_firma)
    args = p.parse_args()
    try:
        return args.fn(args)
    except ApiError as e:
        print("Errore: %s" % e.message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
