#!/usr/bin/env python3
"""Prova dell'installazione vera su una macchina Linux appena creata (la usa .github/workflows/installazione.yml).

Va lanciata come root dentro /opt/alvea, su una macchina usa e getta con systemd: installa davvero, crea utenti e servizi.
In /srv/origine deve esserci una copia git del repository, che qui fa la parte di GitHub per la prova di aggiornamento.

    bash strumenti/prova_installazione.sh                 # installa come nella guida (su una macchina dove Python può non esserci ancora), poi lancia questa prova
    python3 strumenti/prova_installazione.py riavvio      # dopo aver riavviato la macchina: è ripartito tutto da solo?
"""
import hashlib
import json
import os
import pwd
import socket
import ssl
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request

DIR, ORIGINE, PRIMA = "/opt/alvea", "/srv/origine", "/tmp/prova"  # in PRIMA c'è quello che prova_installazione.sh ha registrato
DOMINIO, EMAIL, PW = "alvea.prova", "gestore@alvea.prova", "password-di-prova-123"
LOCALE = "http://127.0.0.1:8787"
AZIENDA = {"name": "Prima Azienda", "package_id": "business", "admin_email": "admin@prima-azienda.it", "admin_name": "Admin", "admin_password": PW}
falliti = []


def sh(cmd, env=None, cwd=DIR):
    r = subprocess.run(cmd, shell=True, cwd=cwd, env=dict(os.environ, **(env or {})), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return r.returncode, r.stdout


def passo(text):
    print("\n== %s" % text, flush=True)


def atteso(ok, text, detail=""):
    print("%-7s %s" % ("OK" if ok else "ERRORE", text), flush=True)
    if not ok:
        falliti.append(text)
        if detail:
            print("        " + str(detail).strip()[-1500:].replace("\n", "\n        "), flush=True)
    return ok


def http(method, url, body=None, token=None, headers=None, raw=None, insecure=False):
    """Ritorna (stato, intestazioni, corpo): il corpo è un oggetto se è JSON, altrimenti byte."""
    h = dict(headers or {})
    data = raw
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    ctx = ssl._create_unverified_context() if insecure else None

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=ctx)) if ctx else urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=30) as r:
            status, heads, out = r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        status, heads, out = e.code, dict(e.headers), e.read()
    except (urllib.error.URLError, OSError) as e:
        return 0, {}, str(e).encode()
    try:
        return status, heads, json.loads(out) if out[:1] in (b"{", b"[") else out
    except ValueError:
        return status, heads, out


def login(email, password=PW, base=LOCALE):
    status, _, out = http("POST", base + "/api/login", {"email": email, "password": password})
    return out.get("token") if status == 200 and isinstance(out, dict) else None


def aspetta(fn, seconds=40):
    end = time.time() + seconds
    while time.time() < end:
        try:
            if fn():
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def attivo(unit):
    return sh("systemctl is-active %s" % unit)[1].strip() == "active"


def pid():
    return sh("systemctl show alvea -p MainPID --value")[1].strip()


def salute():
    status, _, out = http("GET", LOCALE + "/api/health")
    return out if status == 200 and isinstance(out, dict) else None


def indirizzo_esterno():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # non manda niente: serve solo a sapere con quale indirizzo si esce
        return s.getsockname()[0]
    finally:
        s.close()


def macchina():
    name = dict(l.strip().split("=", 1) for l in open("/etc/os-release") if "=" in l).get("PRETTY_NAME", "?").strip('"')
    print("Macchina: %s | %s | %s" % (name, sh("python3 --version")[1].strip(), sh("caddy version 2>/dev/null | cut -d' ' -f1")[1].strip() or "caddy non ancora installato"), flush=True)


def registrato(name):
    with open(os.path.join(PRIMA, name), encoding="utf-8", errors="replace") as f:
        return f.read().strip()


def installa():
    macchina()
    passo("Cartella sbagliata: dentro /root l'installazione si ferma prima di toccare il sistema")
    atteso(registrato("sbagliata.code") == "2" and "/opt/alvea" in registrato("sbagliata.out"), "Si ferma e dice di spostarla in /opt/alvea", registrato("sbagliata.out"))
    atteso(registrato("sbagliata.resti") == "", "Non ha creato utenti, servizi o file", registrato("sbagliata.resti"))

    passo("Installazione con un comando, come nella guida")
    out = registrato("installa.out")
    atteso(registrato("installa.code") == "0", "L'installazione finisce senza errori (%s secondi; Python c'era già: %s)" % (registrato("installa.secondi"), registrato("python-prima")), out)
    atteso("Collaudo superato" in out, "Il collaudo finale passa")
    atteso("non sono riuscito a installare" not in out, "FFmpeg, git e Caddy installati dal sistema", [l for l in out.splitlines() if "non sono riuscito" in l])
    atteso("non punta ancora a nessun indirizzo" in out, "Avvisa che il nome del dominio non punta ancora al server")
    atteso("manca il modulo «cryptography»" not in out and "manca ffmpeg" not in out, "Cifratura e FFmpeg presenti")
    atteso("Sul sito e nelle pagine legali mancano" in out, "Il controllo di produzione elenca quello che resta da fare")

    passo("Servizio")
    atteso(attivo("alvea") and sh("systemctl is-enabled alvea")[1].strip() == "enabled", "Alvea è acceso e parte da solo all'accensione", sh("systemctl status alvea --no-pager")[1])
    atteso(pwd.getpwuid(os.stat("/proc/%s" % pid()).st_uid).pw_name == "alvea", "Gira come utente «alvea», non come root")
    h = salute()
    atteso(bool(h) and h.get("demo") is False, "Risponde in locale e la modalità demo è spenta", h)
    outside = http("GET", "http://%s:8787/api/health" % indirizzo_esterno())[0]
    atteso(outside == 0, "Dall'esterno la porta 8787 è chiusa: si passa solo da Caddy", outside)
    st = os.stat(".env")
    atteso(stat.S_IMODE(st.st_mode) == 0o600 and pwd.getpwuid(st.st_uid).pw_name == "alvea", "Il file .env lo legge solo «alvea»", oct(st.st_mode))
    atteso(pwd.getpwuid(os.stat("data/salesforb.db").st_uid).pw_name == "alvea", "Il database è di «alvea»")
    env = open(".env").read()
    atteso("SFB_PUBLIC_URL=https://%s\n" % DOMINIO in env and "SFB_DEMO=0\n" in env and "SFB_TRUST_PROXY=1\n" in env and "SFB_SECRET_KEY=\n" not in env,
           "Il file .env ha indirizzo pubblico, proxy e chiave di cifratura")

    passo("Uso: gestore, impostazioni cifrate, un'azienda, un file caricato")
    g = login(EMAIL)
    atteso(bool(g), "Il gestore entra con la password scelta")
    status, _, out = http("GET", LOCALE + "/api/admin/settings", token=g)
    atteso(status == 200 and out.get("encrypted") is True, "La cifratura delle impostazioni è attiva")
    secret = "chiave-segreta-di-prova-9f3a7c"
    status, _, out = http("POST", LOCALE + "/api/admin/settings", {"values": {"BRAVE_API_KEY": secret, "COMPANY_NAME": "Alvea Prova S.r.l."}}, g)
    atteso(status == 200, "Una chiave si salva dal pannello", out)
    leaked = any(secret.encode() in open(os.path.join("data", f), "rb").read() for f in os.listdir("data") if f.startswith("salesforb.db"))
    atteso(not leaked, "Nel database la chiave non è scritta in chiaro")
    status, _, out = http("POST", LOCALE + "/api/admin/tenants", AZIENDA, g)
    atteso(status == 200, "Il gestore crea un'azienda", out)
    a = login(AZIENDA["admin_email"])
    status, _, me = http("GET", LOCALE + "/api/me", token=a)
    atteso(status == 200 and len(me.get("ceiling", [])) == 19, "L'amministratore dell'azienda entra e ha i 19 agenti del pacchetto Business")
    status, _, out = http("POST", LOCALE + "/api/chat", {"message": "Ciao", "agent_id": "centrale", "model": "kimi"}, a)
    atteso(status not in (0, 200, 500) and isinstance(out, dict) and out.get("error"), "Senza chiave di un modello la chat spiega cosa manca (%s)" % (out.get("error", "")[:70] if isinstance(out, dict) else status), out)
    png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c626001000000ffff03000006000557bfabd40000000049454e44ae426082")
    boundary = "----confineDiProva"
    payload = ("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"punto.png\"\r\nContent-Type: image/png\r\n\r\n" % boundary).encode() + png + ("\r\n--%s--\r\n" % boundary).encode()
    status, _, out = http("POST", LOCALE + "/api/files/upload", token=a, raw=payload, headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    atteso(status == 200, "Un file si carica nell'Archivio Media (il servizio può scrivere in storage/)", out)
    status, _, page = http("GET", LOCALE + "/privacy")
    atteso(status == 200 and b"Alvea Prova S.r.l." in page, "Le pagine legali mostrano i dati scritti dal pannello")

    passo("HTTPS con Caddy")
    atteso(attivo("caddy") and sh("systemctl is-enabled caddy")[1].strip() == "enabled", "Caddy è acceso e parte da solo", sh("systemctl status caddy --no-pager")[1])
    atteso(DOMINIO in open("/etc/caddy/Caddyfile").read(), "La configurazione di Caddy ha il dominio giusto")
    code, out = sh("caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile")
    atteso(code == 0, "Caddy accetta la configurazione", out)
    status, heads, _ = http("GET", "http://127.0.0.1/app", headers={"Host": DOMINIO})
    atteso(status in (301, 308) and heads.get("Location", "").startswith("https://%s/" % DOMINIO), "Chi arriva in HTTP viene portato in HTTPS", (status, heads))
    # Un certificato vero non si può avere per un dominio di prova: per provare il passaggio da Caddy al server uso quello interno di Caddy.
    sh("sed -i 's|    encode gzip|    tls internal\\n    encode gzip|' /etc/caddy/Caddyfile && systemctl reload caddy && grep -q '%s' /etc/hosts || echo '127.0.0.1 %s' >> /etc/hosts" % (DOMINIO, DOMINIO))
    ok = aspetta(lambda: http("GET", "https://%s/api/health" % DOMINIO, insecure=True)[0] == 200, 40)
    atteso(ok, "Passando da Caddy in HTTPS il server risponde", http("GET", "https://%s/api/health" % DOMINIO, insecure=True))
    status, heads, page = http("GET", "https://%s/" % DOMINIO, insecure=True)
    atteso(status == 200 and b"data-register" in page and "default-src 'self'" in heads.get("Content-Security-Policy", ""), "Da fuori si vede il sito, con le regole di sicurezza")
    atteso(bool(login(AZIENDA["admin_email"], base="https://" + DOMINIO)), "L'accesso funziona anche passando da Caddy")

    passo("Se il programma cade, riparte da solo")
    before = pid()
    sh("kill -9 %s" % before)
    atteso(aspetta(lambda: attivo("alvea") and pid() not in ("0", before) and salute(), 30), "Dopo un arresto brusco è di nuovo acceso entro 30 secondi", sh("systemctl status alvea --no-pager")[1])

    passo("Copia di sicurezza e ripristino")
    atteso(attivo("alvea-backup.timer") and sh("systemctl is-enabled alvea-backup.timer")[1].strip() == "enabled", "La copia di ogni notte è programmata", sh("systemctl list-timers --all --no-pager")[1])
    code, out = sh("systemctl start alvea-backup.service")
    copies = sorted(os.listdir("/var/backups/alvea")) if os.path.isdir("/var/backups/alvea") else []
    atteso(code == 0 and len([c for c in copies if c.endswith(".db")]) == 1 and len([c for c in copies if c.endswith("-file.tar.gz")]) == 1, "La copia si fa: database e file", (out, copies, sh("journalctl -u alvea-backup -n 20 --no-pager")[1]))
    atteso(all(stat.S_IMODE(os.stat(os.path.join("/var/backups/alvea", c)).st_mode) == 0o600 for c in copies), "Le copie le legge solo «alvea»")
    g = login(EMAIL)
    status, _, out = http("POST", LOCALE + "/api/admin/tenants", dict(AZIENDA, name="Dopo La Copia", admin_email="admin@dopo-la-copia.it"), g)
    atteso(status == 200 and bool(login("admin@dopo-la-copia.it")), "Dopo la copia nasce una seconda azienda")
    code, out = sh("bash deploy/ripristina.sh")
    atteso(code == 0 and "Collaudo superato" in out and "Ripristino fatto" in out, "Il ripristino dell'ultima copia riesce", out)
    atteso(bool(login(AZIENDA["admin_email"])) and bool(login(EMAIL)), "Dopo il ripristino gestore e prima azienda entrano")
    atteso(login("admin@dopo-la-copia.it") is None, "La seconda azienda, nata dopo la copia, non c'è più: i dati sono quelli della copia")
    status, _, files = http("GET", LOCALE + "/api/files", token=login(AZIENDA["admin_email"]))
    atteso(status == 200 and "punto.png" in json.dumps(files), "Il file caricato è tornato", files)
    code, out = sh("bash deploy/ripristina.sh /var/backups/alvea/non-esiste.db")
    atteso(code == 2 and attivo("alvea"), "Con una copia che non esiste non tocca niente")
    sh("head -c 2000 /dev/urandom > /tmp/rotta.db")
    code, out = sh("bash deploy/ripristina.sh /tmp/rotta.db")
    atteso(code == 1 and attivo("alvea") and bool(login(EMAIL)), "Con una copia rovinata non tocca niente", out)

    passo("Rilanciare l'installazione non rompe niente")
    digest = hashlib.sha256(open(".env", "rb").read()).hexdigest()
    code, out = sh("bash deploy/installa.sh --dominio %s --email %s" % (DOMINIO, EMAIL), env={"ALVEA_PASSWORD": "un-altra-password-456"})
    atteso(code == 0 and "Il file .env c'è già" in out and "Collaudo superato" in out, "La seconda volta finisce senza errori", out)
    atteso(hashlib.sha256(open(".env", "rb").read()).hexdigest() == digest, "Il file .env è rimasto identico")
    atteso(bool(login(EMAIL)) and login(EMAIL, "un-altra-password-456") is None and bool(login(AZIENDA["admin_email"])), "Password e dati sono quelli di prima")

    passo("Aggiornamento")
    sh("git config user.email prova@alvea.prova && git config user.name Prova && echo 'riga della versione nuova' >> LEGGIMI.md && git commit -qam 'Versione nuova di prova'", cwd=ORIGINE)
    before, old = pid(), sh("git rev-parse --short HEAD")[1].strip()
    code, out = sh("bash deploy/aggiorna.sh")
    atteso(code == 0 and "Aggiornato da %s a " % old in out and "Collaudo superato" in out, "L'aggiornamento scarica, riavvia e passa il collaudo", out)
    atteso("riga della versione nuova" in open("LEGGIMI.md").read() and pid() != before, "La versione nuova è al suo posto e il servizio è ripartito")
    atteso(bool(login(EMAIL)) and bool(login(AZIENDA["admin_email"])) and hashlib.sha256(open(".env", "rb").read()).hexdigest() == digest, "Dati e impostazioni non sono stati toccati")
    atteso(len([c for c in os.listdir("/var/backups/alvea") if c.endswith(".db")]) == 2, "Prima di aggiornare ha fatto un'altra copia di sicurezza")
    code, out = sh("runuser -u alvea -- python3 manage.py verifica-registro")
    atteso(code == 0, "Il registro delle azioni è integro", out)
    return finale()


def riavvio():
    macchina()
    passo("Dopo il riavvio della macchina")
    sh("grep -q '%s' /etc/hosts || echo '127.0.0.1 %s' >> /etc/hosts" % (DOMINIO, DOMINIO))
    atteso(aspetta(lambda: attivo("alvea") and salute(), 90), "Alvea è ripartito da solo", sh("systemctl status alvea --no-pager")[1])
    atteso(aspetta(lambda: attivo("caddy"), 60), "Caddy è ripartito da solo", sh("systemctl status caddy --no-pager")[1])
    atteso(attivo("alvea-backup.timer"), "La copia di ogni notte è ancora programmata")
    atteso(bool(login(EMAIL)) and bool(login(AZIENDA["admin_email"])), "Gestore e azienda entrano: i dati ci sono")
    atteso(aspetta(lambda: http("GET", "https://%s/api/health" % DOMINIO, insecure=True)[0] == 200, 40), "Passando da Caddy in HTTPS il server risponde")
    return finale()


def finale():
    if falliti:
        print("\nNON SUPERATA: %d controlli falliti" % len(falliti))
        for f in falliti:
            print("  - " + f)
        print(sh("journalctl -u alvea -n 40 --no-pager")[1][-3000:])
        return 1
    print("\nProva superata.")
    return 0


if __name__ == "__main__":
    if os.geteuid() != 0 or not os.path.isdir(ORIGINE):
        sys.exit("Questa prova installa davvero: va lanciata come root su una macchina usa e getta (vedi in cima al file).")
    os.chdir(DIR)
    sys.exit({"installa": installa, "riavvio": riavvio}[sys.argv[1] if len(sys.argv) > 1 else "installa"]())
