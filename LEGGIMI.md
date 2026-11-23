# Alvea.ai — server, schermata e launcher (versione 5)

> Per installarlo su un server e collegarlo al sito segui **`GUIDA.md`**: dieci passi, con il controllo finale `python3 manage.py controlla-produzione`. Questo file descrive ogni funzione nel dettaglio.

Piattaforma di agenti AI per aziende, venduta a pacchetti. Questo archivio contiene tutto il server e la schermata collegata. Gira con il solo Python: non c'è niente da installare.

## Avvio

Serve Python 3.9 o più recente (`python3 --version`).

```bash
./START_MAC_LINUX.sh          # Windows: .\START_WINDOWS.ps1
                              # se dice "permesso negato": bash START_MAC_LINUX.sh
```

La prima volta chiede email e password del **gestore** (tu: crei le aziende e assegni i pacchetti), poi avvia il server su http://127.0.0.1:8787.

Per provare subito i tre pacchetti, in un altro terminale:

```bash
python3 manage.py demo        # crea Demo Start, Demo Business, Demo Completo e stampa le password
```

All'inizio il server è in **modalità demo**: risponde un modello finto a regole fisse, utile per mostrare accessi, blocchi dei pacchetti, approvazioni, lavori automatici e memoria senza spendere. Frasi che il modello finto capisce: `aggiungi lead Mario Rossi mario@rossi.it`, `elenca i lead`, `manda su telegram: testo`, `fissa appuntamento: titolo`, `fammi un riepilogo`, `leggi la posta`, `ricordati che ...`, `cerca in memoria: ...`.

## Aggiornare dalla versione 1, 2, 3 o 4

1. Ferma il server.
2. Estrai questo archivio **sopra** la cartella `alvea` esistente. Il file `.env` e la cartella `data/` non sono nell'archivio, quindi restano i tuoi.
3. Riavvia. Il database si aggiorna da solo all'avvio (tabelle e colonne nuove): aziende, utenti, conversazioni e registro restano. Le aziende esistenti tengono accesi tutti gli agenti del loro pacchetto, come prima.
4. Aggiungi al tuo `.env` le righe nuove che ti servono, copiandole da `.env.example` (registrazione, posta di servizio, archivio, Google, fuso orario).

Prima di aggiornare un'installazione con dati veri, fai una copia di `data/salesforb.db`.

## Novità della versione 9: quello che succede dopo il primo pagamento

- **Addebito non riuscito** (`invoice.payment_failed`): avviso nella schermata, voce nelle novità ed email agli amministratori dell'azienda; sparisce al primo pagamento riuscito.
- **Giorni di tolleranza** (`SFB_GRACE_DAYS`, da 0 a 30, anche dal pannello): dopo la scadenza gli agenti lavorano ancora per quei giorni, poi vanno in pausa. Con 0 tutto resta come prima.
- **Carta, ricevute e disdetta**: un pulsante porta l'amministratore sul portale clienti di Stripe (`POST /api/billing/portal`). La disdetta arriva come `customer.subscription.updated`: la scheda mostra fino a quando resta attivo.
- **Email prima della scadenza**: una sola per scadenza, sette giorni prima, alle aziende che non si rinnovano da sole; un invio fallito viene riprovato al giro dopo (un giro all'ora).
- Prove in `tests/test_v9.py`.

## Novità della versione 8: impostazioni dal pannello e pagamenti con carta

- **Impostazioni della piattaforma** nella schermata del gestore: chiavi dei modelli, posta, ricerca, Google, registrazione e prova gratuita, Stripe. Valgono subito e hanno la precedenza sul file `.env`; un campo svuotato torna al valore del file. I segreti sono cifrati nel database (`SFB_SECRET_KEY`), non tornano mai al browser e nel registro finiscono solo i nomi delle voci cambiate. Non si possono cambiare dal pannello l'indirizzo pubblico, la chiave di cifratura e la modalità demo.
- **Email di prova** dal pannello, per sapere subito se la posta funziona.
- **Pagamenti con carta con Stripe**: scheda Abbonamento per ogni azienda, pagamento sulla pagina di Stripe (il server non vede mai la carta), avvisi di Stripe accettati solo con firma valida e recente, ogni avviso contato una volta sola, la scadenza non si accorcia mai per un avviso vecchio. File: `server/settings.py`, `server/billing.py`, prove in `tests/test_v8.py`.
- `python3 manage.py controlla-produzione` legge anche le impostazioni del pannello e dice se i pagamenti sono attivi.

## Novità della versione 7: abbonamenti con scadenza, prova gratuita, avviso di consumo

- **Abbonamento con scadenza, gestito dal gestore.** Accanto a ogni azienda c'è «Pagato fino al»: l'ultimo giorno pagato, compreso. Dopo quella data chat, lavori automatici e azioni in attesa si fermano con un messaggio chiaro; gli utenti entrano ancora e i dati restano leggibili. Negli ultimi sette giorni l'azienda vede un avviso in alto. Campo vuoto = nessuna scadenza. Serve a vendere con bonifico o fattura: quando il cliente paga, sposti la data. I giorni si contano sull'orario UTC.
- **Prova gratuita** per chi si registra da solo: `SFB_TRIAL_DAYS=14` nel `.env` fa nascere l'azienda con la scadenza a 14 giorni, e la pagina di registrazione lo dice. Con 0 non c'è scadenza finché non la metti tu.
- **Avviso all'80% del tetto di consumo**: una novità per l'azienda, una volta sola, quando attraversa la soglia.
- **Non c'è ancora il pagamento con carta**: la scadenza si sposta a mano.

## Novità della versione 6: recupero della password e tetto di consumo

- **Recupero della password.** Nella pagina di accesso compare «Password dimenticata?» quando il server ha la posta configurata (`SFB_SMTP_HOST`). L'utente riceve un collegamento che vale un'ora e una volta sola; scelta la nuova password, tutte le sessioni aperte con la vecchia vengono chiuse. La risposta è la stessa che l'indirizzo esista o no, così non si scopre chi è cliente; cinque richieste all'ora per indirizzo di rete. Senza posta configurata resta la strada di prima: la password la cambia un amministratore dell'azienda. Rotte `POST /api/password/forgot` e `POST /api/password/reset`.
- **Tetto mensile di consumo per azienda.** Il gestore, nella sua schermata, scrive quanti token al mese può consumare ogni azienda con i modelli a pagamento (0 = nessun tetto). Raggiunto il tetto, chat e lavori automatici si fermano con un messaggio chiaro fino al mese dopo o finché il gestore non lo alza. Il modello «Locale» non costa: non entra nel conto e resta disponibile. L'azienda vede il suo consumo del mese nella scheda Consumi.
- Il nome del progetto è **Alvea.ai**, con logo e colori del marchio. Le variabili del file `.env` conservano il prefisso `SFB_` e il database il nome `salesforb.db`: sono nomi interni, cambiarli romperebbe gli aggiornamenti.

## Novità della versione 5: launcher, agenti sul computer del cliente, video, modello locale

Gli agenti sono 18: ai 16 di prima si aggiungono **Video Editor** (dal pacchetto Business) e **Dev** (dal pacchetto Completo). Nelle aziende già esistenti i due agenti nuovi partono spenti: li accende l'amministratore.

### Dove gira ogni agente

Ogni agente ha scritto in `catalog.json` (campo `run`) dove può girare. Il server resta l'unico posto in cui stanno le chiavi dei modelli e dei servizi.

| Agente | Nostri server | Computer del cliente | Motore locale | Stato del collegamento locale |
|---|---|---|---|---|
| Video Editor | sì, se il server ha FFmpeg | sì | FFmpeg | **scritto e provato** |
| Dev | no | sì (solo lì) | cartella di lavoro scelta dal cliente | **scritto e provato** |
| Email | sì | sì | IMAP e SMTP, password solo sul computer | **scritto**, provato con server di posta finti |
| Modello linguistico «Locale» | sì, con Ollama accanto al server | sì, con Ollama sul computer | Ollama o compatibile | **scritto**, provato con un modello finto |
| Ascolto | sì (bozze) | solo verifica | faster-whisper | da scrivere |
| Voce | sì (bozze) | solo verifica | kokoro-onnx | da scrivere |
| Ricerca | sì, anche con SearXNG del gestore | solo verifica | SearXNG | lato server scritto; sul computer da scrivere |
| WhatsApp | sì (API di Meta) | solo verifica | WAHA | da scrivere |
| Shopify, Calendario, Contenuti, Marketing, LinkedIn, Instagram, ChatGPT, CRM, Report, Team, Telegram | sì | no | — | usano servizi in rete |

«Solo verifica» vuol dire: il launcher controlla se quel motore è presente sul computer e lo dice al cliente, ma l'agente continua a girare sui server.

### Il launcher

È il programma che il cliente scarica dal sito (pagina di registrazione, oppure Centro di controllo, scheda **Computer**). È un solo file Python, senza dipendenze.

1. **Verifica, senza account.** Controlla sistema, processore, memoria, spazio e i programmi necessari, e per ogni agente scrive `GIRA`, `NON GIRA` con il motivo, oppure `SOLO SUI NOSTRI SERVER`. Per il Video Editor fa una prova vera: monta due secondi di video e dice quanto ci vorrà per un minuto. Il cliente lo fa **prima** di scegliere o pagare.
2. **Collegamento.** L'amministratore crea un codice nella scheda Computer: vale dieci minuti e una volta sola. Il launcher lo scambia con un suo gettone, di cui il server conserva solo l'impronta.
3. **Lavoro.** Il launcher scarica i pacchetti degli agenti impostati su «Computer», ne controlla la firma, ed esegue i lavori che arrivano. Nella scheda Computer si vedono i computer accesi, il verdetto agente per agente e l'elenco dei lavori.

Sicurezza, in breve:
- **Pacchetti firmati.** Il launcher esegue codice scaricato: per questo ogni pacchetto ha una firma RSA che il launcher controlla con la chiave pubblica ricevuta insieme al launcher stesso. Senza firma valida non salva e non esegue niente.
- **Il gettone del computer apre solo le rotte del launcher**: non la chat, non i dati, non l'archivio.
- **Agente Dev**: legge e scrive solo nella cartella scelta dal cliente; il server non può mandare comandi, può solo chiedere *per nome* uno dei comandi che il cliente ha scritto nel launcher. Scrivere un file ed eseguire un comando richiedono l'approvazione di un amministratore.
- **Agente Email in locale**: indirizzo e password della casella restano nel launcher; il server non li riceve.
- **Arresto d'emergenza**: ferma anche la consegna dei lavori ai computer.

**Firma dei pacchetti.** `python3 manage.py firma-pacchetti` crea la chiave (serve `openssl`) e firma. In demo il server lo fa da solo all'avvio e tiene la chiave in `data/firma_privata.pem`. **Prima di avere clienti veri la chiave privata va tolta dal server**: si firma da un altro computer con `--chiave` e si copiano sul server solo `data/firma_pubblica.json` e `data/firme_pacchetti.json`. Se la chiave resta sul server, chi ruba il server può firmare codice per i computer dei clienti.

### Video Editor

Strumenti `video_elenca_materiale` e `video_monta`. Il montaggio prende clip e immagini dell'Archivio Media nell'ordine dato, le porta al formato scelto (verticale 1080×1920, orizzontale, quadrato; «bozza» a metà risoluzione), tiene l'audio delle clip, aggiunge una musica facoltativa a volume basso e salva un MP4 nell'Archivio Media. Parte come lavoro: sul computer del cliente oppure sui server. Nell'Ufficio 3D l'agente passa allo stato **sta montando un video** con la percentuale, e il video finito si guarda dalla sua postazione.

Cosa **non** fa: non guarda dentro le clip per scegliere i momenti migliori, non taglia a ritmo di musica, non mette titoli, sottotitoli o transizioni. Taglia per secondi, come gli viene chiesto.

### Modello «Locale» (Ollama) e ricerca privata (SearXNG)

- `OLLAMA_BASE_URL=http://127.0.0.1:11434/v1` nel `.env`: il modello «Locale» usa un Ollama accanto al server. Senza quella riga, se un computer dell'azienda ha Ollama acceso e il launcher aperto, la richiesta passa da lì. In entrambi i casi niente chiavi e niente costo a consumo. Gli strumenti funzionano solo con modelli che li supportano (per esempio `llama3.1`); il nome si imposta con `OLLAMA_MODEL` oppure, sul computer, con `launcher modello --nome`.
- `SEARXNG_URL=http://127.0.0.1:8080`: l'Agente Ricerca usa un'istanza SearXNG del gestore al posto di Brave. L'istanza deve avere `json` in `search.formats` nel suo `settings.yml`, altrimenti risponde 403.

### I repository indicati come riferimento

Non sono stati clonati né letti: da dove è stato costruito il pacchetto la rete era chiusa. Sono stati usati come **mappa dei motori locali** (quale programma per quale agente) e, per Ollama e SearXNG, è stata controllata la documentazione ufficiale. Nessuna riga di quei progetti è nel pacchetto. Prima di copiarne codice in un prodotto venduto vanno controllate le licenze una per una: diverse sono AGPL o GPL (obbligano a pubblicare il proprio codice) e n8n non è libero per uso commerciale.

## Novità della versione 4: Ufficio 3D

- **Vista «Ufficio 3D».** Una stanza esplorabile (trascina per ruotare, rotella o due dita per lo zoom, Maiusc e trascina per spostare, frecce e + − da tastiera) con **una postazione per ogni agente acceso** dall'azienda e il Centrale davanti. La stanza nasce da `GET /api/me` (chi è acceso, che postazione ha) e gli stati da `GET /api/summary?office=1`, richiesto ogni secondo e mezzo, ogni mezzo secondo mentre una richiesta è in corso.
- **Postazioni attrezzate per tipo**, campo `station` di ogni agente in `catalog.json`: `lavagna` (CRM, Marketing, Report), `posta`, `calendario`, `scrittura`, `ricerca`, `audio` (Voce, Ascolto), `negozio`, `canale` (WhatsApp, Telegram, Instagram, LinkedIn), `terminale` (ChatGPT), `squadra`, e `montaggio` (doppio monitor con timeline) già disegnata per il futuro agente video. Per cambiare postazione a un agente basta cambiare quella parola.
- **Stati veri, in ordine di precedenza:** *fermo* (arresto d'emergenza), *al lavoro* (sta usando uno strumento, con il nome), *sta ragionando* (aspetta il modello), *attende approvazione*, *da collegare*, *pronto*. Ogni personaggio ha un indicatore sopra la testa con colore e movimento diversi, più una posa (mano alzata quando aspetta un'approvazione, mani sulla tastiera quando lavora). Lo stato è scritto anche a parole nell'etichetta e nell'elenco: non dipende dal solo colore.
- **Clic su un agente o sulla sua postazione:** si aprono la chat diretta, le attività in tempo reale (dal registro) e gli elaborati (bozze, lead, appuntamenti), più le azioni in attesa che un amministratore approva o rifiuta lì. Rotta `GET /api/office/agents/{id}`.
- **La vista 2D resta**, con il pulsante «Modalità Compatta / Mobile». Su telefono e dove manca WebGL si parte da quella.
- **Tutto in locale.** Il motore 3D è `web/vendor/mini3d.js`, scritto per questo progetto: WebGL puro, nessuna dipendenza, nessun CDN. **Non è Three.js**: nello spazio in cui è stato costruito il pacchetto Three.js non c'era e non si poteva scaricare. La Content-Security-Policy ora elenca ogni tipo di risorsa e ammette solo lo stesso server (`script-src 'self'`, niente script in linea, niente `eval`).
- **Una conversazione per interlocutore.** Cambiare agente non mescola più i messaggi, in entrambe le viste.

Dalla versione 5 lo stato «sta montando un video» esiste e gli elaborati comprendono i video. In modalità demo il modello finto e gli strumenti aspettano 0,9 secondi (`SFB_DEMO_DELAY_MS`) per rendere visibili «sta ragionando» e «al lavoro»: con i modelli veri quella pausa non esiste e i tempi sono quelli reali.

## Novità della versione 3

- **Agenti scelti dall'azienda.** Il pacchetto non accende più gli agenti: è solo il tetto massimo. Dentro quel tetto ogni amministratore accende e spegne i singoli agenti (scheda Agenti, oppure dalla scheda di ogni agente) e può dare a ognuno istruzioni proprie. Tabella `tenant_agents`, rotte `GET` e `POST /api/tenant/agents`.
- **Registrazione autonoma.** Dalla pagina di accesso una nuova azienda si registra da sola, sceglie i suoi agenti ed entra come amministratore. Rotta pubblica `POST /api/register`.
- **Archivio Media.** Ogni azienda carica clip video, immagini e audio (scheda Archivio Media). Rotta `POST /api/files/upload` (multipart/form-data), file in `storage/{azienda}/`, tabella `media_assets`.

### Registrazione: cosa decidi tu nel `.env`

| Riga | Predefinito | Cosa fa |
|---|---|---|
| `SFB_REGISTRATION` | `aperta` | `chiusa` = le aziende le crea solo il gestore |
| `SFB_REGISTER_PACKAGE` | `completo` | Tetto di agenti e limiti di chi si registra da solo |
| `SFB_REGISTER_PLATFORM_KEYS` | `0` | `0` = chi si registra usa **le proprie** chiavi dei modelli. `1` = usa le tue, cioè spende il tuo credito |
| `SFB_REGISTER_PER_IP_HOUR` | `5` | Tentativi di registrazione all'ora per indirizzo di rete |
| `SFB_REGISTER_PER_HOUR` | `30` | Aziende nuove all'ora in tutto |
| `SFB_SMTP_HOST` e seguenti | vuoto | Se c'è un server di posta, l'account si attiva solo dopo la conferma dell'email |
| `SFB_TRUST_PROXY` | `0` | `1` dietro un proxy tuo: l'indirizzo del visitatore viene letto da `X-Forwarded-For` |

Il gestore può concedere a una singola azienda le chiavi della piattaforma dalla sua schermata. Con `SFB_REGISTER_PLATFORM_KEYS=1` e la registrazione aperta su Internet chiunque può consumare il tuo credito presso i fornitori dei modelli: non c'è ancora un tetto di spesa per azienda.

### Archivio: regole

- Tipi ammessi: jpg, jpeg, png, gif, webp, mp4, mov, webm, mp3, wav, m4a, ogg. Il contenuto deve corrispondere all'estensione. HTML, SVG ed eseguibili sono rifiutati.
- Massimo per file: `SFB_UPLOAD_MAX_MB` (200). Spazio per azienda: dal pacchetto (200, 2.000, 20.000 MB).
- Il nome dato dall'utente non diventa mai un percorso: sul disco il file ha un nome casuale.
- Per mostrare i file la schermata usa collegamenti firmati validi 15 minuti. Chi ha il collegamento apre quel file per quei minuti, anche senza accesso: è così che funzionano i tag video e immagine.
- Lo stesso file caricato due volte non occupa spazio due volte.
- I file non vengono analizzati né ricodificati: nessun controllo antivirus, nessuna miniatura generata.

## Novità della versione 2

- **Lavori automatici.** Un agente esegue una richiesta salvata ogni giorno a un'ora (nei giorni scelti) oppure ogni N minuti (minimo 15). Scheda Automazioni, solo amministratori.
- **Novità.** Esiti dei lavori automatici, azioni in attesa e avvisi, con il conteggio di quelle da leggere nella barra in alto.
- **Accesso Google.** Gmail (lettura e invio), Calendar (lettura e creazione eventi), Fogli (lettura), con il consenso di Google al posto delle password.
- **Memoria.** Fatti sull'azienda e documenti di testo, usati dagli agenti quando rispondono.
- **Arresto d'emergenza.** Un amministratore ferma in un colpo chat, lavori automatici ed esecuzione delle azioni approvate.
- **Centro operativo.** Sotto ogni agente c'è lo stato vero: collegato, da collegare, solo bozze.

## Passare ai modelli veri

Nel file `.env`: metti `SFB_DEMO=0` e le chiavi (`ANTHROPIC_API_KEY`, `MOONSHOT_API_KEY`, `OPENAI_API_KEY` più `OPENAI_MODEL`). Controlla i nomi dei modelli (`CLAUDE_MODEL`, `KIMI_MODEL`) sui siti dei fornitori. Riavvia. In alternativa ogni azienda può mettere le proprie chiavi nel Centro di controllo, scheda Collegamenti.

## Pacchetti

Sono in `catalog.json`, unica fonte per server e schermata. Ogni pacchetto contiene il precedente. Dalla versione 3 il pacchetto è un tetto: quali agenti sono accesi lo decide l'azienda. Per non mettere limiti basta assegnare Completo.

| Pacchetto | Tetto di agenti | Aggiunge | Lavori automatici | Documenti in memoria | Archivio |
|---|---|---|---|---|---|
| Start | 6 | Email, Calendario, Contenuti, Ricerca, Report, Telegram | 2 | 5 | 200 MB |
| Business | 11 | Shopify, CRM Lead, Marketing, ChatGPT (terzo modello), Team (più utenti) | 10 | 50 | 2.000 MB |
| Completo | 16 | WhatsApp, Instagram, LinkedIn, Voce, Ascolto | 50 | 500 | 20.000 MB |

Per spostare un agente, cambiare un limite o aggiungere un quarto pacchetto si cambia solo `catalog.json`. Il pacchetto di un'azienda si cambia dalla schermata del gestore oppure con `python3 manage.py pacchetto ID NOME`.

## Collegare Google

Serve una volta sola, per tutta la piattaforma. Lo fai tu come gestore nella console Google Cloud.

1. Crea un progetto e attiva tre API: Gmail API, Google Calendar API, Google Sheets API.
2. Configura la schermata di consenso OAuth e aggiungi gli ambiti: `gmail.readonly`, `gmail.send`, `calendar.events`, `spreadsheets.readonly`.
3. Crea un client OAuth di tipo «Applicazione web». Come URI di reindirizzamento autorizzato metti l'indirizzo che la schermata mostra nel pannello Google (in locale: `http://127.0.0.1:8787/api/google/callback`).
4. Metti `GOOGLE_CLIENT_ID` e `GOOGLE_CLIENT_SECRET` nel `.env`. Se il server non è su 127.0.0.1:8787, imposta anche `SFB_PUBLIC_URL`.
5. Riavvia. Ogni amministratore collega l'account della sua azienda da Centro di controllo, scheda Collegamenti.

Apri Alvea.ai sempre allo stesso indirizzo di `SFB_PUBLIC_URL` (per esempio `http://127.0.0.1:8787`, non `http://localhost:8787`): altrimenti al ritorno da Google il browser non riconosce la sessione.

Tre limiti di Google da conoscere prima di venderlo (documentazione Google, letta il 23 novembre 2026):

- Finché l'applicazione Google è in stato «Test» la possono usare solo gli utenti di prova che aggiungi tu (massimo 100) e il permesso **scade dopo 7 giorni**: poi va ricollegato. La schermata lo segnala.
- La scadenza dei 7 giorni sparisce mettendo l'applicazione «In produzione»; per togliere il limite degli utenti e la schermata di avviso serve la verifica di Google.
- `gmail.readonly` è un ambito «con restrizioni»: per usarlo in produzione Google chiede anche una valutazione di sicurezza esterna, che ha tempi e costi. `gmail.send`, Calendar e Fogli sono ambiti «sensibili»: verifica sì, valutazione di sicurezza no. Per questo nel pannello i servizi si scelgono uno per uno: puoi partire con invio, Calendar e Fogli e lasciare la lettura della posta a IMAP.

I token di Google si salvano solo cifrati: serve `SFB_SECRET_KEY` e il pacchetto `cryptography` (`pip3 install cryptography`; la chiave la genera `python3 manage.py init` alla prima installazione). Per una prova in locale senza cifratura esiste `SFB_ALLOW_PLAINTEXT_TOKENS=1`: non usarlo con account veri di clienti.

## Cosa è collegato davvero

| Agente | Cosa fa oggi | Cosa manca |
|---|---|---|
| Email | Con Google: legge con Gmail, invia dopo approvazione. Senza Google: IMAP e SMTP | Outlook con accesso Microsoft; allegati |
| Calendario | Con Google: legge Google Calendar, crea eventi dopo approvazione. Senza: calendario interno | Outlook; invitati; modifica e cancellazione eventi |
| Report | Riepilogo dei numeri della piattaforma; legge i Fogli Google | Scrittura sui Fogli; dati di vendita esterni |
| Telegram | Legge i messaggi del bot, invia dopo approvazione | — |
| WhatsApp | Invia testo con la Cloud API dopo approvazione | Messaggi modello (obbligatori per il primo contatto), ricezione |
| Shopify | Legge ordini e prodotti (Admin API GraphQL) | Modifiche al negozio |
| Ricerca | Ricerca web (Brave Search API), lettura di pagine | — |
| CRM Lead | Lead salvati dentro la piattaforma | HubSpot e altri CRM esterni |
| Team | Più utenti con ruoli (operatore, amministratore) | — |
| ChatGPT | Terzo modello selezionabile | — |
| Contenuti, Marketing, Instagram, LinkedIn | Scrivono e salvano bozze | Pubblicazione automatica, account pubblicitari |
| Voce | Prepara copioni di telefonate | Chiamate vere |
| Ascolto | Ricava verbali da una trascrizione incollata | Trascrizione dell'audio |

Tutti gli agenti consultano la memoria dell'azienda.

Rispetto alla schermata del video dimostrativo non ci sono: il riquadro «Attività live» con il browser guidato dall'agente, il comando vocale, il tema chiaro, «Personalizza» e «Intervento manuale».

## Regole che il server applica

- **Team e pacchetti**: un agente spento o fuori dal tetto del pacchetto viene rifiutato dal server, con i suoi strumenti, il suo modello e i suoi lavori automatici; non è solo nascosto nella schermata. Un'azione già in attesa di approvazione non parte se nel frattempo il suo agente è stato spento.
- **Aziende separate**: ogni riga di dati porta l'azienda; ogni query la filtra. Vale anche per memoria, novità, lavori automatici, collegamento Google, agenti scelti e file. Il gestore non entra nei dati delle aziende. Nessuna azienda viene mai cancellata dal database, così i suoi numeri non passano a un'altra.
- **Approvazioni**: email, Telegram, WhatsApp ed eventi su Google Calendar non partono. Creano una richiesta che solo un amministratore approva; un'azione approvata parte una volta sola. Vale anche per i lavori automatici: niente esce senza una persona.
- **Lavori automatici**: dopo 3 errori di fila vanno in pausa con un avviso. Se il server resta spento, al riavvio ogni lavoro scaduto parte una volta, non una per ogni giro perso. Girano a nome di chi li ha creati.
- **Memoria**: un agente non scrive in memoria da solo. Può proporre un fatto; entra solo dopo la conferma di un amministratore. Così un testo malevolo in una email o in una pagina non può lasciare istruzioni che verrebbero rilette in ogni conversazione.
- **Accesso Google**: codice di autorizzazione con PKCE, `state` monouso valido 10 minuti e legato al browser che ha iniziato. I token non escono mai dalle API. Scollegando, viene chiesta la revoca a Google e i token vengono cancellati.
- **Arresto d'emergenza**: ferma chat, lavori automatici ed esecuzione delle azioni approvate. Alla ripartenza i giri saltati non vengono recuperati.
- **Registro**: ogni azione finisce in un registro a catena di hash. Una riga cambiata o cancellata rompe la catena.
- **Accessi**: password con PBKDF2-SHA256 (600.000 giri), sessioni di 12 ore, blocco dopo 5 tentativi sbagliati.
- **Credenziali dei collegamenti**: cifrate se `SFB_SECRET_KEY` è impostata e `cryptography` è installato. Altrimenti restano in chiaro e la schermata lo segnala (i token Google in quel caso non si salvano affatto).
- **Testi esterni**: email, pagine, messaggi e fogli arrivano all'agente marcati come dati non fidati. Gli agenti aprono solo indirizzi scritti dall'utente o trovati dalla ricerca nella stessa richiesta, mai reti interne.

## Limiti da conoscere

- **La ricerca in memoria è per parole, non per significato.** «Garanzia ricambi» trova un testo che contiene quelle parole, non uno che parla di «copertura dei pezzi». Solo testo: i PDF vanno convertiti prima.
- **Il fuso orario è uno solo per tutta la piattaforma** (`SFB_TZ`, predefinito Europe/Rome).
- **I lavori automatici girano dentro il server**: se il server è spento non girano. Uno alla volta: un lavoro lento ritarda i successivi.
- **Le novità lette valgono per tutta l'azienda**, non per singolo utente.
- **Gli stati «sta ragionando» e «al lavoro» vivono nella memoria del server**: valgono per un solo processo e si azzerano al riavvio. Con più processi dietro un bilanciatore servirebbe un archivio condiviso.
- **L'Ufficio 3D interroga il server di continuo** mentre è aperto (una richiesta leggera ogni secondo e mezzo per scheda aperta). Si ferma quando la scheda del browser è nascosta.
- **Il launcher non si aggiorna da solo**: i pacchetti sì, il programma no. La scheda Computer mostra la versione di ogni launcher.
- **Un lavoro alla volta per computer, e uno alla volta sul server.** Un montaggio lungo fa aspettare i successivi.
- **Gli strumenti brevi sul computer (Dev, Email) aspettano la risposta per 30 secondi**; il modello locale per 110. Con il computer spento l'agente lo dice subito.
- I lavori automatici consumano token a ogni giro: la scheda Consumi mostra quanto. Non c'è un tetto di spesa per azienda.

## Cosa non è stato provato

- **Nessuna chiamata a servizi veri.** Claude, Kimi, ChatGPT, Google (consenso, Gmail, Calendar, Fogli), Telegram, WhatsApp, Shopify, Brave, IMAP e SMTP sono scritti secondo la documentazione ma provati solo contro servizi finti locali (modelli, Google, Telegram) o non provati affatto (gli altri). La prima prova con chiavi vere va fatta da te.
- **L'invio vero dell'email di conferma** e i caricamenti oltre i 24 MB. Il lettore dei caricamenti è stato provato anche con il contenuto spezzato in pezzi da 1 byte.
- **Solo Linux**, con Python 3.11 e 3.13. Per Python 3.9 è stata controllata la sintassi, non l'esecuzione. Su macOS e Windows non è stato eseguito.
- **Il launcher è stato eseguito solo su Linux.** Su macOS e Windows è stata controllata solo la sintassi; la lettura della memoria su Windows non è stata provata. Sul computer del cliente serve Python 3.9 o successivo: **non c'è un installatore con doppio clic** (richiede firma del programma per macOS e Windows, che da qui non si può fare).
- **Ollama, SearXNG e un server di posta veri non sono stati usati**: il modello locale è stato provato con il modello finto, la ricerca con un SearXNG finto, la posta con server IMAP e SMTP finti.
- **Video grandi**: provati montaggi fino a 80 secondi in 1080×1920. Non provati file di centinaia di megabyte né il limite di caricamento su connessioni lente.
- **L'Ufficio 3D è stato provato solo in Chrome senza schermo, con il disegno fatto dal processore** (SwiftShader), a 1440×950 e a 390×844 con tocco simulato. Non è stato provato su una scheda grafica vera, né su Safari, Firefox o un telefono reale: lì vanno guardati fluidità e consumo di batteria.
- **Stripe vero non è stato usato**: creazione del pagamento e avvisi sono provati con uno Stripe finto e con firme calcolate secondo la documentazione. Prima di incassare va fatto un giro con le chiavi di prova di Stripe.
- Nessuna prova di carico. SQLite regge bene decine di aziende, non migliaia di utenti contemporanei.

## Come può verificarlo una terza persona

```bash
python3 -m unittest discover -s tests -v     # 130 prove, senza chiavi e senza Internet (13 chiedono FFmpeg e openssl, altrimenti alcune vengono saltate)
python3 manage.py verifica-registro          # ricalcola la catena di ogni azienda
```

Le prove hanno nomi leggibili, per esempio: `test_agente_fuori_pacchetto_rifiutato_dal_server`, `test_azione_esterna_di_un_lavoro_automatico_aspetta_approvazione`, `test_un_agente_puo_solo_proporre_e_serve_la_conferma`, `test_ritorno_falsificato_rifiutato`, `test_due_processi_non_eseguono_lo_stesso_lavoro`. Altre, della versione 3: `test_spegnere_un_agente_lo_blocca_davvero_e_solo_per_quell_azienda`, `test_file_isolati_tra_aziende_su_api_e_su_disco`, `test_la_nuova_azienda_non_vede_niente_delle_altre`, `test_il_nome_del_file_non_diventa_mai_un_percorso`. Le prove sono state a loro volta controllate inserendo difetti nel codice, uno alla volta: 15 per la versione 2 e 26 per la versione 3, tutti scoperti (uno dei 26 solo dopo aver tolto anche la seconda protezione che lo copriva).

Per l'Ufficio 3D, a mano: apri gli strumenti del browser (F12), scheda Rete, e controlla che ogni richiesta vada allo stesso indirizzo del server; scheda Console, e controlla che resti vuota. Poi scrivi al Centrale «aggiungi lead Mario Rossi» e guarda la postazione CRM passare da *pronto* ad *al lavoro* e tornare *pronto*. Le prove `test_ragiona_e_lavora_si_vedono_solo_mentre_succede_e_solo_nella_propria_azienda` e `test_motore_3d_locale_e_nessuna_risorsa_esterna` controllano le stesse due cose senza browser.

A mano: entra con Demo Start, tocca un agente bloccato, e manda `POST /api/chat` con `"agent_id": "shopify"`: la risposta deve essere 403.

## Prima di darlo a un'azienda vera

1. **HTTPS.** Il server non cifra il traffico. Va messo dietro un proxy HTTPS (per esempio Caddy) e solo allora `SFB_HOST` può uscire da 127.0.0.1. Con HTTPS imposta `SFB_PUBLIC_URL` all'indirizzo pubblico.
2. **Copie di sicurezza** della cartella `storage/`, del file `data/salesforb.db` e del file `.env` (senza `SFB_SECRET_KEY` le credenziali cifrate e i collegamenti Google sono persi).
3. **Prova con chiavi vere** di ogni collegamento che vendi.
4. **Verifica Google** dell'applicazione, se vendi l'accesso Google (vedi sopra).
5. **Recupero password**: oggi la cambia un altro amministratore. Non c'è invio per email né secondo fattore. Con la registrazione aperta diventa urgente: chi si registra da solo ed è l'unico amministratore, se dimentica la password resta fuori.
   **Tetto di spesa** per azienda sui modelli: non c'è.
   **Proxy**: i caricamenti grandi richiedono che il proxy HTTPS accetti corpi fino a `SFB_UPLOAD_MAX_MB`.
6. **Privacy**: messaggi, posta letta e documenti in memoria vanno ai fornitori dei modelli e restano in chiaro nel database. Servono informativa e accordi sul trattamento dei dati con i clienti: da far vedere a un legale.
7. **Nome**: «Alvea.ai» è vicino a un marchio noto. Controllo sul marchio prima di venderlo.
8. **Fatturazione**: non c'è. La scheda Consumi dà i token per azienda, da moltiplicare per i listini.

## Rapporto con il SourceKit

Questo server non usa Agno, Hatchet, ToolHive né Activepieces: è scritto da zero con la libreria standard, perché si avvii con un comando e si possa provare per intero. Lavori automatici, accesso Google e memoria della versione 2 sono anch'essi scritti a mano. I sorgenti del kit restano la strada per andare oltre: Agno per memoria per significato, squadre di agenti e più modelli; OpenConnector o Activepieces per gli altri servizi con OAuth (Microsoft, HubSpot, Instagram, LinkedIn). I punti dove innestarli sono `server/agents.py` (ciclo modello-strumenti), `server/tools.py` (elenco degli strumenti), `server/memory.py` (ricerca) e `server/google.py` (modello per un altro OAuth).

## File

```
catalog.json        pacchetti, limiti e agenti
manage.py           comandi: init, demo, aziende, pacchetto, verifica-registro
server/app.py       API e permessi            server/agents.py     motore agenti e approvazioni
server/tools.py     strumenti                 server/connectors.py credenziali e servizi esterni
server/google.py    accesso Google            server/scheduler.py  lavori automatici
server/memory.py    memoria e documenti       server/news.py       novità
server/llm.py       Claude, Kimi, ChatGPT     server/fake_llm.py   modello finto (demo e prove)
server/auth.py      password e sessioni       server/audit.py      registro a catena di hash
server/db.py        database e aggiornamenti  server/ops.py        aziende, utenti, arresto
server/team.py      agenti scelti             server/registration.py registrazione e verifica email
server/media.py     archivio dei file         server/clock.py      fuso orario
server/live.py      chi sta lavorando adesso  server/office.py     dati per l'Ufficio 3D
server/local.py     computer, pacchetti firmati, lavori sul computer e sul server
packages/video      montaggio con FFmpeg      packages/dev         Agente Dev nella cartella di lavoro
packages/email      posta dal computer        packages/modello     modello locale (Ollama)
launcher/           il programma per il computer del cliente (lo stesso che il sito fa scaricare)
web/app.js          schermata                 web/office.js        stanza, postazioni, personaggi
web/vendor/mini3d.js motore 3D locale (WebGL) storage/             file delle aziende (creata al primo caricamento)
tests/              prove (base.py, test_server.py, test_nuove.py, test_v3.py, test_v4.py, test_v5.py)
```
