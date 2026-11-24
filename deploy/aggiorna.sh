#!/usr/bin/env bash
# Aggiorna Alvea all'ultima versione su GitHub, senza toccare dati e impostazioni.
#
#   sudo bash deploy/aggiorna.sh
#
# Fa una copia di sicurezza, scarica la versione nuova, firma i pacchetti (se la chiave è su questo server),
# riavvia e fa il collaudo. Se il collaudo non passa dice come tornare alla versione di prima.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"
[ "$(id -u)" = 0 ] || { echo "Va lanciato come root (con sudo)." >&2; exit 2; }
command -v git >/dev/null && [ -d .git ] || { echo "Questa cartella non è stata scaricata con git: scarica la versione nuova a mano e rilancia deploy/installa.sh." >&2; exit 2; }
PRIMA="$(git rev-parse --short HEAD)"
echo "== Copia di sicurezza prima di aggiornare"
runuser -u alvea -- /bin/sh deploy/backup.sh /var/backups/alvea 14
echo "== Scarico la versione nuova"
git pull --ff-only
DOPO="$(git rev-parse --short HEAD)"
[ "$PRIMA" != "$DOPO" ] || echo "Era già l'ultima versione ($PRIMA): riavvio lo stesso e ricontrollo."
chgrp -R alvea "$DIR" && chmod -R g+rX "$DIR"
echo "== Pacchetti per i computer dei clienti"
if python3 -c 'import sys; from server import config, local; config.load_env(); sys.exit(0 if local.key_path().is_file() else 1)'; then
  python3 manage.py firma-pacchetti | sed -n 1p
else
  echo "La chiave di firma non è su questo server: firma i pacchetti dal computer che la tiene (python3 manage.py firma-pacchetti --chiave …)."
fi
chown -R alvea: data storage .env
chmod 600 .env
# i file del servizio possono essere cambiati con la versione nuova
sed "s|/opt/alvea|$DIR|g" deploy/alvea.service > /etc/systemd/system/alvea.service
sed "s|/opt/alvea|$DIR|g" deploy/alvea-backup.service > /etc/systemd/system/alvea-backup.service
cp deploy/alvea-backup.timer /etc/systemd/system/alvea-backup.timer
systemctl daemon-reload
systemctl enable --now alvea-backup.timer >/dev/null 2>&1 || true
echo "== Riavvio e collaudo"
systemctl restart alvea
for _ in $(seq 1 30); do runuser -u alvea -- python3 manage.py collaudo >/dev/null 2>&1 && break; sleep 1; done
if runuser -u alvea -- python3 manage.py collaudo; then
  echo "Aggiornato da $PRIMA a $DOPO."
else
  echo "ATTENZIONE: dopo l'aggiornamento il collaudo non passa. Per tornare alla versione di prima:" >&2
  echo "  git reset --hard $PRIMA && systemctl restart alvea     (i dati di prima sono nell'ultima copia in /var/backups/alvea: bash deploy/ripristina.sh)" >&2
  exit 1
fi
