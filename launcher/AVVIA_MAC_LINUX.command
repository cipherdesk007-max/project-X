#!/bin/sh
# Doppio clic per aprire il launcher di SalesForB (serve Python 3.9 o successivo).
cd "$(dirname "$0")" || exit 1
exec python3 salesforb_launcher.py "$@"
