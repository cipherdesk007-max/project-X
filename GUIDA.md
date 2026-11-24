# Guida semplice: installare Alvea sul server e collegarlo al sito

Questa guida è per un server Linux (Ubuntu o Debian) con un indirizzo tipo `app.tuodominio.it`.
Per i dettagli di ogni funzione c'è `LEGGIMI.md`. Per provarlo sul tuo computer senza server: fondo pagina.

## 1. Cosa serve

- Un server Linux (VPS) raggiungibile da Internet, con **Ubuntu 22.04, 24.04 o 26.04** oppure **Debian 12 o 13**: sono i cinque sistemi su cui l'installazione viene provata da zero a ogni modifica. Bastano 2 GB di memoria per cominciare; lo script alla fine dice quante persone regge.
- Un nome (`app.tuodominio.it`) che punta all'indirizzo del server: nel pannello di chi ti ha venduto il dominio crea un record **A** con l'indirizzo IP della VPS. Se non l'hai ancora fatto l'installazione va avanti lo stesso e te lo ricorda: l'HTTPS parte da solo appena il nome risponde.
- Le porte 80 e 443 aperte verso il server (se il fornitore della VPS ha un suo firewall nel pannello, aprile lì).
- Almeno una chiave di un modello (Claude, Kimi o ChatGPT), oppure Ollama sul server.

## 2. Installare con un solo comando

```sh
sudo apt update && sudo apt install -y git
sudo git clone https://github.com/cipherdesk007-max/project-X /opt/alvea
cd /opt/alvea
sudo bash deploy/installa.sh --dominio app.tuodominio.it --email tu@tuodominio.it
```

Se sulla VPS entri già come `root` (succede spesso), scrivi gli stessi comandi **senza** `sudo`. Scarica sempre in `/opt/alvea`: se la cartella sta in `/root` o in `/home` lo script si ferma e ti dice come spostarla, perché il servizio per sicurezza non può entrare nelle cartelle personali.

Lo script fa tutto in ordine e ti dice a che punto è: installa i programmi che servono (Python, la cifratura, FFmpeg, Caddy: non devi installare niente prima), crea il file `.env` con la chiave di cifratura, crea il database e il tuo utente **gestore** (la password te la chiede lui), **firma tutti i pacchetti per i computer dei clienti**, accende l'avvio automatico, programma **una copia di sicurezza ogni notte**, apre le porte 80 e 443 se sul server è attivo il firewall `ufw`, configura l'HTTPS e alla fine fa il collaudo. Si può rilanciare quante volte vuoi: non cancella e non riscrive niente di quello che c'è già. Se il collaudo finale non passa lo dice chiaramente e ti indica dove guardare.

Quando ha finito apri `https://app.tuodominio.it`, entra come gestore e scrivi le chiavi dei modelli in **Impostazioni della piattaforma**. I punti 3, 4 e 5 qui sotto servono solo se preferisci fare i passi a mano o vuoi capire cosa ha fatto lo script.

Alla fine lo script dice anche **quanto regge il server**: memoria, processore, disco e circa quante persone possono lavorarci insieme. Lo rivedi quando vuoi con `python3 manage.py capacita` oppure nel pannello del gestore, sezione Server, dove c'è anche la **Console** con i comandi di manutenzione pronti.

Dopo aver scritto la chiave di un modello nelle impostazioni, provala subito: nel pannello, Console → **Prova dei modelli** (oppure `python3 manage.py prova-modelli`). Ti dice se il modello risponde o l'errore esatto, per esempio una chiave sbagliata o un nome di modello che il fornitore non conosce.

Per rifare il collaudo in qualsiasi momento:

```sh
sudo -u alvea python3 manage.py collaudo
```

Controlla che il server risponda, che ci siano tutti i 28 agenti, che i quattro pacchetti siano presenti, firmati e consegnati ai launcher, che il launcher si scarichi. Con `--email` e `--password` di un'azienda e l'opzione `--agenti` scrive anche a ogni agente acceso (con i modelli veri consuma qualche token).

## 3. (A mano) Impostare il file `.env`

Apri `/opt/alvea/.env` e cambia solo queste righe:

```
SFB_DEMO=0
SFB_PUBLIC_URL=https://app.tuodominio.it
SFB_HOST=127.0.0.1
SFB_TRUST_PROXY=1
ANTHROPIC_API_KEY=...        # oppure MOONSHOT_API_KEY, OPENAI_API_KEY, OLLAMA_BASE_URL
```

## 4. (A mano, e dopo ogni aggiornamento) Firmare i pacchetti per i computer dei clienti

```sh
sudo python3 manage.py firma-pacchetti
```

Va rifatto ogni volta che aggiorni il codice. **Prima di avere clienti veri** sposta `data/firma_privata.pem` su un altro computer e firma da lì con `--chiave`: se resta sul server, chi entra nel server può mandare codice ai computer dei clienti.

## 5. (A mano) HTTPS e avvio automatico

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

## 7. Il sito

Il sito pubblico è già dentro: sta sullo stesso server, all'indirizzo principale. Non c'è niente da collegare.

- `https://app.tuodominio.it/` → il **sito**: presentazione, tutti gli agenti, i tre pacchetti, il launcher da scaricare.
- `https://app.tuodominio.it/app` → l'**applicazione**: accesso, registrazione, ufficio, Centro di controllo.

Agenti e pacchetti del sito vengono dal catalogo (`catalog.json`): se cambi il catalogo, il sito cambia da solo. Se la registrazione è chiusa, i pulsanti «Prova Alvea» diventano «Accedi». Se c'è la prova gratuita, il sito dice quanti giorni dura. Chi apre un collegamento ricevuto per email (conferma, nuova password, ritorno dal pagamento) viene portato da solo all'applicazione.

Il sito mostra i prezzi solo se li hai scritti nel pannello (Impostazioni, gruppo «Prezzi»). Testi e sezioni stanno in `server/site.py`, l'aspetto in `web/sito.css`.

**Pagine legali e dati dell'azienda.** Il sito ha tre pagine: `/privacy`, `/condizioni` e `/cookie`, collegate in fondo a ogni pagina. I tuoi dati (ragione sociale, partita IVA, sede, email, PEC, REA, fornitore del server) li scrivi nel pannello del gestore, Impostazioni, gruppo «Azienda»: compaiono subito nelle pagine e in fondo al sito. Quello che manca si legge come «[da compilare: …]» e `controlla-produzione` lo segnala come «DA FARE». Chi si registra deve spuntare l'accettazione: nel registro dell'azienda resta scritto quale versione ha accettato. I testi stanno in `server/legal.py`, una sezione per riga: sono una base scritta su come funziona la piattaforma, **da far rivedere a un legale prima di vendere**; quando li cambi, cambia anche la data `VERSION` in cima al file.

Se hai già un sito altrove e qui vuoi solo l'applicazione, scrivi `SFB_SITE=0` nel file `.env`: la pagina principale torna a essere l'accesso. Sul tuo sito metti tre collegamenti: Accedi → `/app`, Registrati → `/app#registrati`, Launcher → `/api/launcher/download`.

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

### Prezzi

Nel pannello, gruppo **Prezzi**, scrivi in euro al mese:

- il prezzo di **Start**, **Business** e **Completo**;
- quanto costa **in più ogni agente acceso** (0 se vuoi che conti solo il pacchetto).

Il cliente paga il pacchetto più gli agenti che tiene accesi: se ne accende o ne spegne uno, il prezzo dell'abbonamento cambia da solo su Stripe e la differenza finisce nella fattura successiva. I prezzi compaiono anche sul sito. Con i prezzi scritti qui **non serve creare prodotti e prezzi su Stripe** (i passi 1 e 4 qui sopra, per la parte dei prezzi, si saltano): bastano la chiave segreta e il webhook. Se lasci tutto a 0 valgono, come prima, gli ID dei prezzi creati a mano su Stripe.

### Crediti

Nel pannello, gruppo **Crediti**, decidi tutto tu:

1. **Crediti attivi**: 1. Finché è 0 non cambia niente per nessuno.
2. **Costo di ogni modello** in crediti ogni 1.000 token. Metti di più dove a te costa di più: per esempio Kimi 1, ChatGPT 4, Claude 5. Guarda i listini dei fornitori e aggiungi il tuo margine. Il modello locale non costa mai.
3. **Crediti inclusi ogni mese** in Start, Business e Completo. Si ricaricano il primo del mese; quelli non usati non si sommano.
4. **Ricarica**: quanti crediti contiene e l'ID del suo prezzo su Stripe (un prezzo a **pagamento singolo**, non ricorrente). Se lasci 0 le ricariche non si vendono.

Il cliente vede tutto nella scheda **Crediti**: quanti ne ha, quando si ricaricano, quanto costa ogni modello, e compra le ricariche con la carta. Tu, nella scheda di ogni azienda, vedi i suoi crediti e puoi aggiungerne o toglierne. Chi usa una propria chiave del modello non consuma crediti.

### Launcher già pronto (exe e dmg)

Oltre alla versione che chiede Python, c'è il launcher già pronto: `Alvea-Launcher.exe` per Windows e `Alvea-Launcher.dmg` per i Mac con processore Apple. Si scaricano da `https://github.com/cipherdesk007-max/project-X/releases/tag/launcher` e vengono ricostruiti e riprovati da soli a ogni modifica del launcher.

Per usarli, accanto al programma va messo il file `server.json` che sta nel pacchetto scaricato dal tuo server (pulsante «Scarica il launcher»): contiene l'indirizzo del server e la chiave che controlla i pacchetti.

Non sono firmati, per scelta: funzionano lo stesso, ma alla prima apertura il cliente deve dare una conferma. Il sito lo spiega sotto i pulsanti di download.

- **Windows**: compare «PC protetto». Si preme «Ulteriori informazioni» e poi «Esegui comunque».
- **Mac**: il sistema dice che non può verificare il programma. Si apre Impostazioni di Sistema → Privacy e sicurezza e si preme «Apri comunque» (sui Mac meno recenti basta clic destro → Apri).
- **Per controllare che il file sia quello vero**: accanto a ogni programma, nel rilascio, c'è un file `.sha256` con la sua impronta. Su Windows `certutil -hashfile Alvea-Launcher.exe SHA256`, su Mac `shasum -a 256 Alvea-Launcher.dmg`: il risultato deve essere uguale.

Due cose che senza firma possono succedere e che non dipendono da noi: qualche antivirus segnala per errore i programmi fatti così, e i computer aziendali con regole strette possono bloccarli del tutto. In quei casi resta la versione con Python, oppure l'agente lavora sui nostri server. Se un giorno vorrai togliere gli avvisi, servono l'iscrizione al programma sviluppatori Apple e un certificato di firma del codice per Windows.

### Agenti sul computer del cliente: prima si paga

Il launcher verifica il computer gratis e senza account. Per collegarlo e far lavorare gli agenti lì serve l'abbonamento pagato: chi è in prova gratuita o è scaduto vede il motivo nella scheda Computer e intanto usa gli agenti sui tuoi server.

Chi paga con bonifico continua come prima: scrivi a mano **«Pagato fino al»** accanto alla sua azienda.

### Due impostazioni da gestore

- **Tetto di consumo**: nella tua schermata di gestore, accanto a ogni azienda, scrivi quanti token al mese può consumare con i modelli a pagamento (0 = nessun tetto). Serve a non trovarti una spesa che non ti aspetti.
- **Recupero della password**: funziona solo se hai impostato la posta (gruppo Posta delle impostazioni, oppure `SFB_SMTP_HOST` nel file `.env`). Senza posta, la password di un utente la cambia un amministratore della sua azienda.

## 9. Copie di sicurezza

L'installazione le programma da sola: **ogni notte verso le 3:30** una copia di database, file dei clienti e impostazioni va in `/var/backups/alvea`, e restano le ultime 14. Se a quell'ora il server era spento, parte alla prima accensione.

```sh
systemctl list-timers alvea-backup.timer      # quando parte la prossima
sudo systemctl start alvea-backup             # farne una subito
sudo ls -l /var/backups/alvea                 # vedere quelle che ci sono
```

**Rimettere una copia** (ferma Alvea, mette da parte i dati di adesso, rimette la copia, riavvia e fa il collaudo):

```sh
cd /opt/alvea
sudo bash deploy/ripristina.sh                                              # l'ultima copia
sudo bash deploy/ripristina.sh /var/backups/alvea/alvea-20261124-033000.db    # una copia precisa
```

Una copia che resta sullo stesso server non ti salva se si rompe il server: una volta alla settimana **portala anche altrove** (un altro server, il tuo computer, uno spazio cloud). Contiene dati dei clienti e chiavi: tienila cifrata. Per metterla in un'altra cartella: `sudo -u alvea deploy/backup.sh /percorso/delle/copie`.

### Dati di un'azienda: esportare e cancellare

Quando un cliente chiede una copia dei suoi dati, o chiude il contratto e vuole che vengano cancellati (lo promettono le pagine legali):

```sh
cd /opt/alvea
sudo -u alvea python3 manage.py aziende                                          # l'elenco, con i numeri
sudo -u alvea python3 manage.py esporta-azienda "Nome Azienda" /tmp/nome-azienda.zip
sudo -u alvea python3 manage.py cancella-azienda "Nome Azienda" --conferma "Nome Azienda" --copia /tmp/nome-azienda.zip
```

L'archivio contiene un file per tabella e i file caricati, senza password né chiavi dei collegamenti. La cancellazione toglie tutto: righe in ogni tabella, accessi aperti, file. Chiede il nome esatto, si rifiuta se c'è ancora un abbonamento con carta attivo (va disdetto prima su Stripe) e alla fine controlla che non sia rimasto niente. Le copie di sicurezza delle ultime notti contengono ancora quei dati: spariscono da sole entro 14 giorni. Questi due comandi esistono solo sul server, di proposito: nel pannello non c'è un pulsante per portare via o distruggere un'azienda.

## 10. Sicurezza del server

Quello che l'installazione fa da sola:

- Alvea gira come utente senza privilegi (`alvea`), chiuso da systemd in un recinto: non vede le cartelle personali, non scrive fuori da `data/` e `storage/`, non ha accesso a dispositivi e impostazioni del sistema. `systemd-analyze security alvea` ne dà il punteggio.
- Il programma ascolta solo in locale: da Internet si entra soltanto da Caddy, in HTTPS, e il browser viene istruito a non usare mai HTTP.
- Gli aggiornamenti di sicurezza del sistema si installano da soli ogni notte.
- Chiavi e password scritte dal pannello sono cifrate nel database; il file `.env` e le copie di sicurezza li legge solo `alvea`.
- Troppi tentativi di accesso sbagliati fermano per qualche minuto quell'indirizzo, senza chiudere fuori il vero proprietario.

Quello che devi fare tu, perché lo script non può deciderlo al posto tuo:

- **Entra sulla VPS con una chiave SSH e spegni l'accesso con password** (`PasswordAuthentication no` in `/etc/ssh/sshd_config`, poi `systemctl restart ssh`). Prima controlla di riuscire a entrare con la chiave, altrimenti resti chiuso fuori. È la porta da cui entrano più spesso.
- Nel pannello del fornitore della VPS apri solo le porte 22, 80 e 443.
- Usa per il gestore una password lunga che non usi altrove: chi entra come gestore vede le impostazioni di tutta la piattaforma.
- Sposta la chiave che firma i pacchetti fuori dal server (`controlla-produzione` te lo ricorda).
- Porta le copie di sicurezza anche fuori dal server.

Le prove automatiche attaccano il sistema a ogni modifica (`tests/test_v16.py`): ogni rotta con ogni grado di utente, i dati di un'azienda chiesti da un'altra, la salita di grado, le sessioni, la forza bruta, le richieste scritte male, le richieste verso la rete interna.

## 11. Aggiornare

```sh
cd /opt/alvea && sudo bash deploy/aggiorna.sh
```

Fa una copia di sicurezza, scarica l'ultima versione da GitHub, firma i pacchetti (se la chiave è su questo server; altrimenti ti ricorda di firmarli dal computer che la tiene), riavvia e fa il collaudo. Se il collaudo non passa ti scrive il comando per tornare alla versione di prima.

Il database si aggiorna da solo all'avvio. `.env`, `data/` e `storage/` non sono nel repository e non vengono toccati.

## Provarlo sul proprio computer, senza server

```sh
git clone https://github.com/cipherdesk007-max/project-X && cd project-X
cp .env.example .env
python3 manage.py demo        # crea tre aziende di prova e stampa le password
python3 -m server             # poi apri http://127.0.0.1:8787
```

## Cosa non è ancora fatto

- Launcher senza firma a pagamento: alla prima apertura Windows e macOS chiedono una conferma (vedi «Launcher già pronto»).
- Pagine legali: i testi ci sono, ma vanno riveduti da un legale e completati con i tuoi dati.
- Collegamento locale per Ascolto, Voce, WhatsApp e Ricerca sul computer del cliente: il launcher li verifica soltanto.
- Fatture: Stripe incassa e manda la ricevuta, ma la fattura elettronica italiana (SDI) la fai ancora tu o il commercialista.
- Prove con servizi veri: fin qui modelli, Google, posta, Telegram, WhatsApp e Shopify sono stati provati solo con servizi finti.
