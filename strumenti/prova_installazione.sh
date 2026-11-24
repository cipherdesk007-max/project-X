#!/usr/bin/env bash
# Prova dell'installazione su una macchina Linux appena creata e usa e getta (vedi .github/workflows/installazione.yml).
# Questa parte gira prima che sulla macchina ci sia Python (lo deve installare lo script): fa i passi della guida,
# registra com'è andata in /tmp/prova e poi passa la mano a prova_installazione.py, che controlla tutto il resto.
set -u
[ "$(id -u)" = 0 ] && [ -d /srv/origine ] && [ -d /opt/alvea ] || { echo "Va lanciata come root su una macchina usa e getta, con il repository in /opt/alvea e la sua origine in /srv/origine." >&2; exit 2; }
mkdir -p /tmp/prova
cd /opt/alvea
if command -v python3 >/dev/null; then echo sì > /tmp/prova/python-prima; else echo no > /tmp/prova/python-prima; fi

# 1. Scaricato nel posto sbagliato (la cartella di root): deve fermarsi senza toccare niente.
rm -rf /root/alvea-sbagliata && cp -a /opt/alvea /root/alvea-sbagliata
(cd /root/alvea-sbagliata && bash deploy/installa.sh --dominio alvea.prova --email gestore@alvea.prova) > /tmp/prova/sbagliata.out 2>&1
echo $? > /tmp/prova/sbagliata.code
{ ! id alvea >/dev/null 2>&1 || echo "utente alvea"; [ ! -e /etc/systemd/system/alvea.service ] || echo "servizio"; [ ! -e /root/alvea-sbagliata/.env ] || echo ".env"; } > /tmp/prova/sbagliata.resti
rm -rf /root/alvea-sbagliata

# 2. Il comando della guida.
SECONDS=0
ALVEA_PASSWORD=password-di-prova-123 bash deploy/installa.sh --dominio alvea.prova --email gestore@alvea.prova 2>&1 | tee /tmp/prova/installa.out
echo "${PIPESTATUS[0]}" > /tmp/prova/installa.code
echo "$SECONDS" > /tmp/prova/installa.secondi

command -v python3 >/dev/null || { echo "Dopo l'installazione python3 non c'è: la prova non può continuare." >&2; exit 1; }
exec python3 strumenti/prova_installazione.py installa
