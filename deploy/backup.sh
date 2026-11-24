#!/bin/sh
# Copia di sicurezza di database, file dei clienti e impostazioni.
#   deploy/backup.sh /percorso/delle/copie [giorni]
# Con «giorni» cancella dalla cartella le copie più vecchie di quel numero di giorni (l'installazione usa 14).
# L'installazione la lancia da sola ogni notte in /var/backups/alvea. Una copia che resta sullo stesso server non basta:
# portala anche altrove (un altro server, un disco, uno spazio cloud). Contiene dati dei clienti e chiavi: tienila cifrata.
set -eu
umask 077
DEST="${1:?indica la cartella delle copie}"
GIORNI="${2:-0}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"
# copia coerente del database anche mentre il server è acceso
python3 - "$HERE/data/salesforb.db" "$DEST/alvea-$STAMP.db" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
assert dst.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "la copia del database non è integra"
dst.close(); src.close()
PY
# solo quello che esiste: su un server appena installato storage/ non c'è ancora
tar -czf "$DEST/alvea-$STAMP-file.tar.gz" -C "$HERE" $(cd "$HERE" && ls -d storage .env data/firma_pubblica.json data/firme_pacchetti.json 2>/dev/null)
case "$GIORNI" in ''|*[!0-9]*|0) ;; *) find "$DEST" -maxdepth 1 -name 'alvea-*' -type f -mtime "+$GIORNI" -delete ;; esac
echo "Copia fatta: $DEST/alvea-$STAMP.db e $DEST/alvea-$STAMP-file.tar.gz"
