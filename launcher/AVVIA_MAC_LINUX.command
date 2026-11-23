#!/bin/sh
# Doppio clic per aprire il launcher di Alvea (serve Python 3.9 o successivo).
cd "$(dirname "$0")" || exit 1
exec python3 alvea_launcher.py "$@"
