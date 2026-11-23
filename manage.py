#!/usr/bin/env python3
"""Comandi di gestione di SalesForB.

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
    add(bool(shutil.which("openssl")), "openssl presente.", "openssl assente: non potrai firmare i pacchetti da questo computer.", block=False)
    return out


def cmd_produzione(args):
    report = production_report()
    for state, text in report:
        print("%-10s %s" % (state, text))
    todo = sum(1 for s, _ in report if s == "DA FARE")
    print("\n%s" % ("Pronto per i clienti." if not todo else "%d cose da fare prima di aprire ai clienti." % todo))
    return 1 if todo else 0


def cmd_firma(args):
    from server import local
    names = local.sign_all(args.chiave)
    print("Pacchetti firmati: %s" % ", ".join(names))
    print("Chiave privata: %s" % (args.chiave or local.key_path()))
    print("In produzione tieni la chiave privata FUORI dal server e firma da un altro computer: chi ruba il server non deve poter firmare.")
    return 0


def main():
    p = argparse.ArgumentParser(description="Gestione di SalesForB")
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
