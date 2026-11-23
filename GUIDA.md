# Guida semplice: installare Alvea.ai sul server e collegarlo al sito

Questa guida è per un server Linux (Ubuntu o Debian) con un indirizzo tipo `app.tuodominio.it`.
Per i dettagli di ogni funzione c'è `LEGGIMI.md`. Per provarlo sul tuo computer senza server: fondo pagina.

## 1. Cosa serve

- Un server Linux raggiungibile da Internet e un nome (`app.tuodominio.it`) che punta al suo indirizzo.
- Almeno una chiave di un modello (Claude, Kimi o ChatGPT), oppure Ollama sul server.

## 2. Installare (10 minuti)

```sh
sudo apt update && sudo apt install -y git python3 python3-cryptography ffmpeg openssl caddy
sudo useradd --system --home /opt/alvea --shell /usr/sbin/nologin alvea
sudo git clone https://github.com/cipherdesk007-max/project-X /opt/alvea
cd /opt/alvea
sudo cp .env.example .env
sudo python3 manage.py init --email tu@tuodominio.it --nome "Il tuo nome"
```

`init` crea il database, la chiave che cifra le credenziali e il tuo utente **gestore**. La password te la chiede lui.

## 3. Impostare il file `.env`

Apri `/opt/alvea/.env` e cambia solo queste righe:

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
sudo chown -R alvea: /opt/alvea/data /opt/alvea/.env
sudo mkdir -p /opt/alvea/storage && sudo chown alvea: /opt/alvea/storage
sudo cp deploy/alvea.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now alvea
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile     # poi cambia app.tuodominio.it dentro il file
sudo systemctl reload caddy
```

Caddy prende da solo il certificato HTTPS. Apri `https://app.tuodominio.it`: deve comparire la pagina di accesso.

## 6. Controllo finale

```sh
sudo -u alvea python3 manage.py controlla-produzione
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

### Impostazioni dal pannello (senza toccare il file)

Entra come gestore: sotto l'elenco delle aziende c'è **Impostazioni della piattaforma**. Lì scrivi, un gruppo alla volta, quello che serve per far girare tutto:

- **Modelli**: le chiavi di Claude, Kimi e ChatGPT, oppure l'indirizzo di Ollama.
- **Posta**: server, porta, utente e password della casella che manda le email di conferma e di recupero password. Il pulsante **Manda un'email di prova** la spedisce al tuo indirizzo: se non arriva, leggi l'errore lì sotto.
- **Ricerca**, **Google**, **Registrazione** (aperta o chiusa, giorni di prova gratuita).
- **Pagamenti**: le chiavi di Stripe (qui sotto).

Quello che salvi vale subito, senza riavviare, e vale più del file `.env`. Chiavi e password non vengono mai rimostrate: vedi solo «impostata». Nel file `.env` restano obbligatorie solo le righe del punto 3 (indirizzo, HTTPS, `SFB_SECRET_KEY`): senza `SFB_SECRET_KEY` il pannello rifiuta di salvare chiavi e password.

### Come farti pagare con la carta (Stripe)

1. Apri un conto su stripe.com e crea **tre prodotti con prezzo mensile ricorrente**, uno per pacchetto. Di ognuno copia il codice del prezzo (comincia con `price_`).
2. In Stripe, in Sviluppatori → Chiavi API, copia la **chiave segreta** (`sk_live_...`).
3. In Stripe, in Sviluppatori → Webhook, aggiungi una destinazione con l'indirizzo che leggi nel pannello sotto Pagamenti (`https://app.tuodominio.it/api/billing/webhook`) e gli eventi `checkout.session.completed`, `invoice.paid`, `invoice.payment_failed`, `customer.subscription.updated`, `customer.subscription.deleted`. Copia il **segreto della firma** (`whsec_...`).
4. Nel pannello, gruppo **Pagamenti**, incolla chiave, segreto e i tre prezzi. Salva.
5. In Stripe attiva il **portale clienti** (Impostazioni → Fatturazione → Portale clienti) e lascia accese «aggiorna metodo di pagamento», «storico fatture» e «annulla abbonamento» alla fine del periodo. Senza questo passo il pulsante «Carta, ricevute e disdetta» dà errore.

Da quel momento ogni azienda vede la scheda **Abbonamento** con il pulsante **Paga con carta**: il numero della carta si scrive sulla pagina di Stripe, mai sul tuo server. A ogni addebito riuscito Stripe avvisa il server e la data «Pagato fino al» si sposta da sola. Se il cliente disdice, resta attivo fino all'ultimo giorno pagato e poi gli agenti vanno in pausa.

Cosa succede da solo, dopo:

- **Carta, ricevute e disdetta**: il cliente le gestisce da sé con un pulsante nella scheda Abbonamento, che lo porta sulla pagina di Stripe.
- **Addebito non riuscito**: il cliente vede un avviso e riceve un'email; Stripe riprova nei giorni seguenti secondo le regole che scegli in Stripe.
- **Giorni di tolleranza** (gruppo Pagamenti, da 0 a 30): per quanti giorni dopo la scadenza gli agenti lavorano ancora. Consiglio 3–5, così un addebito riprovato non ferma nessuno. Vale anche per chi paga con bonifico.
- **Email prima della scadenza**: sette giorni prima parte un'email agli amministratori delle aziende che non si rinnovano da sole (bonifico, prova gratuita, disdetta). Serve la posta configurata.

Prima di usare le chiavi vere fai un giro con quelle di prova (`sk_test_...`) e la carta di prova 4242 4242 4242 4242: è l'unica verifica che qui non ho potuto fare io.

Chi paga con bonifico continua come prima: scrivi a mano **«Pagato fino al»** accanto alla sua azienda.

### Due impostazioni da gestore

- **Tetto di consumo**: nella tua schermata di gestore, accanto a ogni azienda, scrivi quanti token al mese può consumare con i modelli a pagamento (0 = nessun tetto). Serve a non trovarti una spesa che non ti aspetti.
- **Recupero della password**: funziona solo se hai impostato la posta (gruppo Posta delle impostazioni, oppure `SFB_SMTP_HOST` nel file `.env`). Senza posta, la password di un utente la cambia un amministratore della sua azienda.

## 9. Copie di sicurezza

```sh
sudo -u alvea deploy/backup.sh /percorso/delle/copie
```

Da mettere in cron una volta al giorno. La copia contiene dati dei clienti: tienila cifrata e fuori dal server.

## 10. Aggiornare

```sh
cd /opt/alvea && sudo git pull
sudo python3 manage.py firma-pacchetti     # oppure dal computer che tiene la chiave
sudo systemctl restart alvea
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
- Fatture: Stripe incassa e manda la ricevuta, ma la fattura elettronica italiana (SDI) la fai ancora tu o il commercialista.
- Cambio di pacchetto dal pannello del cliente: oggi il pacchetto lo cambi tu, e il prezzo su Stripe va cambiato a mano.
- Prove con servizi veri: fin qui modelli, Google, posta, Telegram, WhatsApp e Shopify sono stati provati solo con servizi finti.
