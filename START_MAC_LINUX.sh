#!/usr/bin/env bash
# Avvia SalesForB. La prima volta chiede email e password del gestore della piattaforma.
set -euo pipefail
cd "$(dirname "$0")"
python3 manage.py init --se-manca
exec python3 -m server
