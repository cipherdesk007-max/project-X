# Guida semplice: installare SalesForB sul server e collegarlo al sito

Questa guida è per un server Linux (Ubuntu o Debian) con un indirizzo tipo `app.tuodominio.it`.
Per i dettagli di ogni funzione c'è `LEGGIMI.md`. Per provarlo sul tuo computer senza server: fondo pagina.

## 1. Cosa serve

- Un server Linux raggiungibile da Internet e un nome (`app.tuodominio.it`) che punta al suo indirizzo.
- Almeno una chiave di un modello (Claude, Kimi o ChatGPT), oppure Ollama sul server.

## 2. Installare (10 minuti)

```sh
sudo apt update && sudo apt install -y git python3 python3-cryptography ffmpeg openssl caddy
sudo useradd --system --home /opt/salesforb --shell /usr/sbin/nologin salesforb
sudo git clone https://github.com/cipherdesk007-max/project-X /opt/salesforb
cd /opt/salesforb
sudo cp .env.example .env
sudo python3 manage.py init --email tu@tuodominio.it --nome "Il tuo nome"
```

`init` crea il database, la chiave che cifra le credenziali e il tuo utente **gestore**. La password te la chiede lui.

## 3. Impostare il file `.env`

Apri `/opt/salesforb/.env` e cambia solo queste righe:

```
SFB_DEMO=0
SFB_PUBLIC_URL=https://app.tuodominio.it
SFB_HOST=127.0.0.1
SFB_TRUST_PROXY=1
ANTHROPIC_API_KEY=...        # oppure MOONSHOT_API_KEY, OPENAI_API_KEY, OLLAMA_BASE_URL
```

## 4. Firmare i pacchetti per i computer dei clienti

```sh
sudo python3 manage.py firma-pacchetti
```

Va rifatto ogni volta che aggiorni il codice. **Prima di avere clienti veri** sposta `data/firma_privata.pem` su un altro computer e firma da lì con `--chiave`: se resta sul server, chi entra nel server può mandare codice ai computer dei clienti.

## 5. HTTPS e avvio automatico

```sh
sudo chown -R salesforb: /opt/salesforb/data /opt/salesforb/.env
sudo mkdir -p /opt/salesforb/storage && sudo chown salesforb: /opt/salesforb/storage
sudo cp deploy/salesforb.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now salesforb
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile     # poi cambia app.tuodominio.it dentro il file
sudo systemctl reload caddy
```

Caddy prende da solo il certificato HTTPS. Apri `https://app.tuodominio.it`: deve comparire la pagina di accesso.

## 6. Controllo finale

```sh
sudo -u salesforb python3 manage.py controlla-produzione
```

Scrive `OK`, `DA FARE` o `CONSIGLIO` per ogni punto. Finché c'è un `DA FARE` non aprire ai clienti.

## 7. Collegarlo al sito

Il sito e l'applicazione restano due cose separate: il sito presenta, l'applicazione lavora. Sul sito bastano tre collegamenti:

| Pulsante sul sito | Indirizzo |
|---|---|
| Accedi | `https://app.tuodominio.it/` |
| Registrati / Prova | `https://app.tuodominio.it/#registrati` |
| Scarica il launcher | `https://app.tuodominio.it/api/launcher/download` |

L'applicazione **non si può mettere dentro una cornice** (iframe) del sito: è una protezione voluta. Se il pannello del sito deve mostrare dati dell'applicazione dentro le sue pagine, serve il sorgente del sito e un lavoro a parte.

## 8. Primo cliente

1. Entra come gestore: vedi le aziende e i loro pacchetti.
2. Il cliente si registra da `#registrati`, sceglie gli agenti e inserisce le sue chiavi dei modelli (chi si registra da solo non usa le tue, a meno che tu non glielo conceda).
3. Se vuole agenti sul suo computer (Video Editor, Email, Dev, modello locale): scarica il launcher, lo apre, e scrive il codice che crea in **Centro di controllo › Computer**. Sul suo computer serve Python 3.9 o successivo.

## 9. Copie di sicurezza

```sh
sudo -u salesforb deploy/backup.sh /percorso/delle/copie
```

Da mettere in cron una volta al giorno. La copia contiene dati dei clienti: tienila cifrata e fuori dal server.

## 10. Aggiornare

```sh
cd /opt/salesforb && sudo git pull
sudo python3 manage.py firma-pacchetti     # oppure dal computer che tiene la chiave
sudo systemctl restart salesforb
```

Il database si aggiorna da solo all'avvio. `.env`, `data/` e `storage/` non sono nel repository e non vengono toccati.

## Provarlo sul proprio computer, senza server

```sh
git clone https://github.com/cipherdesk007-max/project-X && cd project-X
cp .env.example .env
python3 manage.py demo        # crea tre aziende di prova e stampa le password
python3 -m server             # poi apri http://127.0.0.1:8787
```

## Cosa non è ancora fatto

- Installatore del launcher con doppio clic per macOS e Windows (oggi serve Python).
- Collegamento locale per Ascolto, Voce, WhatsApp e Ricerca sul computer del cliente: il launcher li verifica soltanto.
- Pagamenti, recupero password, tetto di spesa per azienda.
- Prove con servizi veri: fin qui modelli, Google, posta, Telegram, WhatsApp e Shopify sono stati provati solo con servizi finti.
