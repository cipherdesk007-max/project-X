#!/bin/sh
# Copia di sicurezza di database, file dei clienti e impostazioni. Uso: deploy/backup.sh /percorso/delle/copie
# Da mettere in cron una volta al giorno. La copia contiene dati dei clienti e chiavi: tienila cifrata e fuori dal server.
set -eu
DEST="${1:?indica la cartella delle copie}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"
# copia coerente del database anche mentre il server è acceso
python3 - "$HERE/data/salesforb.db" "$DEST/alvea-$STAMP.db" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
src.backup(dst); dst.close(); src.close()
PY
# solo quello che esiste: su un server appena installato storage/ non c'è ancora
tar -czf "$DEST/alvea-$STAMP-file.tar.gz" -C "$HERE" $(cd "$HERE" && ls -d storage .env data/firma_pubblica.json data/firme_pacchetti.json 2>/dev/null)
echo "Copia fatta: $DEST/alvea-$STAMP.db e $DEST/alvea-$STAMP-file.tar.gz"
