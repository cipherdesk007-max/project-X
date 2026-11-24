#!/usr/bin/env bash
# Installa Alvea con un solo comando, dentro la cartella dove hai scaricato il repository (consigliata: /opt/alvea).
#
#   sudo bash deploy/installa.sh --dominio app.tuodominio.it --email tu@tuodominio.it
#   (se sei già root, senza «sudo»)
#
# Fa, in ordine: programmi che servono, file .env, database e utente gestore, firma di TUTTI i pacchetti
# per i computer dei clienti, avvio automatico, copia di sicurezza ogni notte, HTTPS, collaudo.
# Si può rilanciare: non cancella niente. Provato su Ubuntu 22.04, 24.04 e 26.04 e su Debian 12 e 13 (vedi .github/workflows/installazione.yml).
# Con --prova non tocca il sistema (niente apt, utenti, systemd, Caddy): serve a provarlo su un computer qualunque.
set -euo pipefail

DOMINIO=""; EMAIL=""; NOME="Gestore"; PROVA=0; PORTA=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dominio) DOMINIO="$2"; shift 2 ;;
    --email) EMAIL="$2"; shift 2 ;;
    --nome) NOME="$2"; shift 2 ;;
    --porta) PORTA="$2"; shift 2 ;;
    --prova) PROVA=1; shift ;;
    *) echo "Opzione sconosciuta: $1" >&2; exit 2 ;;
  esac
done
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"
passo() { printf '\n== %s\n' "$1"; }
come_alvea() { runuser -u alvea -- "$@"; }   # «runuser» c'è su ogni Linux; «sudo» su un server appena creato può mancare
[ -n "$EMAIL" ] || { echo "Manca --email (l'indirizzo del gestore)." >&2; exit 2; }
if [ "$PROVA" = 0 ]; then
  [ "$(id -u)" = 0 ] || { echo "Va lanciato con sudo (oppure con --prova per non toccare il sistema)." >&2; exit 2; }
  [ -n "$DOMINIO" ] || { echo "Manca --dominio (per esempio app.tuodominio.it)." >&2; exit 2; }
  case "$DIR" in
    /root|/root/*|/home/*)
      echo "La cartella $DIR è dentro una cartella personale: il servizio gira come utente «alvea» e per sicurezza non può entrarci." >&2
      echo "Spostala e rilancia da lì:  mv \"$DIR\" /opt/alvea && cd /opt/alvea && bash deploy/installa.sh --dominio $DOMINIO --email $EMAIL" >&2
      exit 2 ;;
    *" "*) echo "Il percorso «$DIR» contiene spazi: sposta la cartella in /opt/alvea e rilancia." >&2; exit 2 ;;
  esac
  command -v systemctl >/dev/null || { echo "Su questo sistema manca systemd: lo script non può impostare l'avvio automatico. Usa --prova e avvia il server a mano." >&2; exit 2; }
fi

passo "1/7 Programmi che servono"
if [ "$PROVA" = 0 ]; then
  if command -v apt-get >/dev/null; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq python3 python3-cryptography openssl ca-certificates >/dev/null   # senza questi non si parte
    for extra in ffmpeg git caddy; do   # utili ma non indispensabili: se uno manca nel sistema lo dico e vado avanti
      apt-get install -y -qq "$extra" >/dev/null 2>&1 || echo "ATTENZIONE: non sono riuscito a installare «$extra» con apt."
    done
  else
    echo "Questo sistema non usa apt (non è Ubuntu o Debian): installa a mano python3, il modulo cryptography, openssl, ffmpeg e caddy."
  fi
fi
command -v python3 >/dev/null || { echo "Manca python3." >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || { echo "Serve Python 3.9 o più recente." >&2; exit 1; }
command -v openssl >/dev/null || { echo "Manca openssl: serve per firmare i pacchetti." >&2; exit 1; }
python3 -c 'import cryptography' 2>/dev/null || echo "ATTENZIONE: manca il modulo «cryptography»: le credenziali dei clienti non verrebbero cifrate."
command -v ffmpeg >/dev/null || echo "ATTENZIONE: manca ffmpeg: il Video Editor funzionerà solo sui computer dei clienti."

passo "2/7 File .env"
imposta() {  # imposta CHIAVE VALORE nel file .env, sostituendo la riga se c'è già
  if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else printf '%s=%s\n' "$1" "$2" >> .env; fi
}
if [ ! -f .env ]; then
  python3 -c 'import manage; manage._ensure_env()'   # copia .env.example e ci scrive la chiave che cifra le credenziali
  if [ "$PROVA" = 0 ]; then
    imposta SFB_DEMO 0
    imposta SFB_PUBLIC_URL "https://$DOMINIO"
    imposta SFB_HOST 127.0.0.1
    imposta SFB_TRUST_PROXY 1
  fi
  [ -z "$PORTA" ] || { imposta SFB_PORT "$PORTA"; [ "$PROVA" = 0 ] || imposta SFB_PUBLIC_URL "http://127.0.0.1:$PORTA"; }
  echo "Creato .env"
else
  echo "Il file .env c'è già: non lo tocco."
fi
chmod 600 .env

passo "3/7 Database e utente gestore"
if [ -n "${ALVEA_PASSWORD:-}" ]; then
  python3 manage.py init --se-manca --email "$EMAIL" --nome "$NOME" --password "$ALVEA_PASSWORD"
else
  python3 manage.py init --se-manca --email "$EMAIL" --nome "$NOME"
fi

passo "4/7 Pacchetti per i computer dei clienti"
python3 manage.py firma-pacchetti | sed -n 1p
mkdir -p storage

if [ "$PROVA" = 0 ]; then
  passo "5/7 Avvio automatico e copia di sicurezza ogni notte"
  id alvea >/dev/null 2>&1 || useradd --system --home "$DIR" --shell /usr/sbin/nologin alvea
  chgrp -R alvea "$DIR" && chmod -R g+rX "$DIR"   # il servizio deve poter leggere il programma, comunque sia stato scaricato
  chown -R alvea: data storage .env
  chmod 600 .env
  come_alvea test -r "$DIR/manage.py" || { echo "L'utente «alvea» non riesce a leggere $DIR: controlla i permessi delle cartelle che la contengono (serve almeno «x» per tutti)." >&2; exit 1; }
  sed "s|/opt/alvea|$DIR|g" deploy/alvea.service > /etc/systemd/system/alvea.service
  sed "s|/opt/alvea|$DIR|g" deploy/alvea-backup.service > /etc/systemd/system/alvea-backup.service
  cp deploy/alvea-backup.timer /etc/systemd/system/alvea-backup.timer
  install -d -o alvea -g alvea -m 700 /var/backups/alvea
  systemctl daemon-reload
  systemctl enable alvea >/dev/null 2>&1
  systemctl restart alvea
  systemctl enable --now alvea-backup.timer >/dev/null 2>&1
  echo "Alvea parte da solo a ogni accensione. Ogni notte una copia di database e file va in /var/backups/alvea (restano le ultime 14)."

  passo "6/7 HTTPS"
  if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null && echo "Firewall (ufw): aperte le porte 80 e 443."
  fi
  if command -v caddy >/dev/null; then
    [ ! -f /etc/caddy/Caddyfile ] || grep -q "$DOMINIO" /etc/caddy/Caddyfile || cp /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.prima-di-alvea"
    mkdir -p /etc/caddy
    sed "s|app.tuodominio.it|$DOMINIO|" deploy/Caddyfile > /etc/caddy/Caddyfile
    caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 || echo "ATTENZIONE: Caddy non accetta la configurazione in /etc/caddy/Caddyfile: controlla il nome del dominio."
    systemctl enable caddy >/dev/null 2>&1 || true
    systemctl reload caddy 2>/dev/null || systemctl restart caddy
    echo "Caddy configurato per $DOMINIO."
    if getent hosts "$DOMINIO" >/dev/null 2>&1; then
      echo "Il certificato HTTPS arriva da solo entro un minuto, se il nome punta a questo server."
    else
      echo "ATTENZIONE: il nome $DOMINIO non punta ancora a nessun indirizzo. Crea nel DNS un record A verso l'indirizzo di questo server: Caddy riprova da solo e l'HTTPS parte appena il nome risponde."
    fi
  else
    echo "Caddy non c'è: configura tu l'HTTPS davanti alla porta ${PORTA:-8787}, oppure installa Caddy (caddyserver.com/docs/install) e rilancia questo script."
  fi
else
  passo "5/7 e 6/7 saltati (--prova): niente avvio automatico né HTTPS"
fi

passo "Quanto regge questo server"
if [ "$PROVA" = 0 ]; then come_alvea python3 manage.py capacita || true; else python3 manage.py capacita || true; fi

passo "7/7 Collaudo"
if [ "$PROVA" = 0 ]; then
  for _ in $(seq 1 30); do come_alvea python3 manage.py collaudo >/dev/null 2>&1 && break; sleep 1; done
  RIUSCITO=1
  come_alvea python3 manage.py collaudo || RIUSCITO=0
  echo
  come_alvea python3 manage.py controlla-produzione || true
  echo
  if [ "$RIUSCITO" = 0 ]; then
    echo "ATTENZIONE: il collaudo non è passato. Guarda cosa dice il servizio:  journalctl -u alvea -n 50 --no-pager" >&2
    exit 1
  fi
  echo "Fatto. Il sito è su https://$DOMINIO e l'applicazione su https://$DOMINIO/app: entra come $EMAIL e scrivi le chiavi dei modelli in «Impostazioni della piattaforma»."
  echo "Per aggiornare:  bash deploy/aggiorna.sh      Per rimettere una copia di sicurezza:  bash deploy/ripristina.sh"
else
  echo "Avvia il server con «python3 -m server» e poi esegui «python3 manage.py collaudo»."
fi
