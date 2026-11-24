#!/usr/bin/env bash
# Rimette al loro posto database e file da una copia di sicurezza fatta con deploy/backup.sh.
#
#   sudo bash deploy/ripristina.sh                      # l'ultima copia in /var/backups/alvea
#   sudo bash deploy/ripristina.sh /percorso/alvea-20261124-033000.db
#
# Ferma Alvea, mette da parte i dati di adesso (in data/prima-del-ripristino-…), rimette la copia, riavvia e fa il collaudo.
# Il file .env di adesso resta com'è, a meno che manchi: così un ripristino non cambia indirizzo o chiavi senza che tu lo sappia.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"
[ "$(id -u)" = 0 ] || { echo "Va lanciato come root (con sudo)." >&2; exit 2; }
DB="${1:-}"
if [ -z "$DB" ]; then
  DB="$(ls -1 /var/backups/alvea/alvea-*.db 2>/dev/null | sort | tail -n 1 || true)"
  [ -n "$DB" ] || { echo "Nessuna copia in /var/backups/alvea: indica il file .db da rimettere." >&2; exit 2; }
fi
[ -f "$DB" ] || { echo "Il file $DB non esiste." >&2; exit 2; }
FILE="${DB%.db}-file.tar.gz"
python3 - "$DB" <<'PY' || { echo "La copia $DB non è un database integro: non tocco niente." >&2; exit 1; }
import sqlite3, sys
con = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0
PY
echo "Rimetto la copia $DB"
systemctl stop alvea
PRIMA="data/prima-del-ripristino-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$PRIMA"
for f in data/salesforb.db data/salesforb.db-wal data/salesforb.db-shm; do [ ! -e "$f" ] || mv "$f" "$PRIMA/"; done
cp "$DB" data/salesforb.db
if [ -f "$FILE" ]; then
  [ ! -d storage ] || mv storage "$PRIMA/storage"
  if [ -f .env ]; then tar -xzf "$FILE" -C "$DIR" --exclude=.env; else tar -xzf "$FILE" -C "$DIR"; fi
  mkdir -p storage
else
  echo "ATTENZIONE: accanto al database non c'è $FILE: rimetto solo il database, i file caricati restano quelli di adesso."
fi
chown -R alvea: data storage .env
chmod 600 .env
systemctl start alvea
for _ in $(seq 1 30); do runuser -u alvea -- python3 manage.py collaudo >/dev/null 2>&1 && break; sleep 1; done
runuser -u alvea -- python3 manage.py collaudo
echo "Ripristino fatto. I dati di prima sono in $DIR/$PRIMA: cancellali quando sei sicuro che non servono."
