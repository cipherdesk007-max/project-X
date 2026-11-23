#!/usr/bin/env bash
# Installa Alvea con un solo comando, dentro la cartella dove hai scaricato il repository.
#
#   sudo bash deploy/installa.sh --dominio app.tuodominio.it --email tu@tuodominio.it
#
# Fa, in ordine: programmi che servono, file .env, database e utente gestore, firma di TUTTI i pacchetti
# per i computer dei clienti, avvio automatico, HTTPS, collaudo. Si può rilanciare: non cancella niente.
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
[ -n "$EMAIL" ] || { echo "Manca --email (l'indirizzo del gestore)." >&2; exit 2; }
if [ "$PROVA" = 0 ]; then
  [ "$(id -u)" = 0 ] || { echo "Va lanciato con sudo (oppure con --prova per non toccare il sistema)." >&2; exit 2; }
  [ -n "$DOMINIO" ] || { echo "Manca --dominio (per esempio app.tuodominio.it)." >&2; exit 2; }
fi

passo "1/7 Programmi che servono"
if [ "$PROVA" = 0 ] && command -v apt-get >/dev/null; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-cryptography ffmpeg openssl caddy
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
  passo "5/7 Avvio automatico"
  id alvea >/dev/null 2>&1 || useradd --system --home "$DIR" --shell /usr/sbin/nologin alvea
  chown -R alvea: data storage .env
  sed "s|/opt/alvea|$DIR|g" deploy/alvea.service > /etc/systemd/system/alvea.service
  systemctl daemon-reload
  systemctl enable alvea >/dev/null 2>&1
  systemctl restart alvea

  passo "6/7 HTTPS"
  if command -v caddy >/dev/null; then
    [ ! -f /etc/caddy/Caddyfile ] || grep -q "$DOMINIO" /etc/caddy/Caddyfile || cp /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.prima-di-alvea"
    sed "s|app.tuodominio.it|$DOMINIO|" deploy/Caddyfile > /etc/caddy/Caddyfile
    systemctl reload caddy || systemctl restart caddy
    echo "Caddy configurato per $DOMINIO: il certificato arriva da solo se il DNS punta già a questo server."
  else
    echo "Caddy non c'è: configura tu l'HTTPS davanti alla porta 8787."
  fi
else
  passo "5/7 e 6/7 saltati (--prova): niente avvio automatico né HTTPS"
fi

passo "7/7 Collaudo"
if [ "$PROVA" = 0 ]; then
  for _ in $(seq 1 20); do python3 manage.py collaudo >/dev/null 2>&1 && break; sleep 1; done
  sudo -u alvea python3 manage.py collaudo || true
  echo
  sudo -u alvea python3 manage.py controlla-produzione || true
  echo
  echo "Fatto. Apri https://$DOMINIO, entra come $EMAIL e scrivi le chiavi dei modelli in «Impostazioni della piattaforma»."
else
  echo "Avvia il server con «python3 -m server» e poi esegui «python3 manage.py collaudo»."
fi
