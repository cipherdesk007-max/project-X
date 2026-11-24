"""Pagine legali del sito: informativa sulla privacy, condizioni di servizio, cookie.

I dati di chi vende il servizio (ragione sociale, partita IVA, sede, contatti) si scrivono nel pannello del gestore,
gruppo «Azienda», e finiscono qui e in fondo al sito. Quello che manca compare come «[da compilare: …]».

I testi sono una base di partenza scritta su come funziona davvero la piattaforma: prima di vendere vanno fatti
rivedere da un professionista. Per cambiarli basta modificare le liste qui sotto: ogni sezione è (titolo, righe),
dove una riga è un paragrafo (testo) oppure un elenco puntato (lista di testi). Quando cambi un testo cambia anche
VERSION: è la data che i clienti vedono e che resta nel registro quando accettano le condizioni.
"""
from __future__ import annotations

from . import catalog, config

VERSION = "2026-11-24"
# chiave, nome leggibile, obbligatorio per vendere
FIELDS = [("COMPANY_NAME", "ragione sociale", True), ("COMPANY_VAT", "partita IVA", True), ("COMPANY_ADDRESS", "sede legale", True),
          ("COMPANY_EMAIL", "email di contatto", True), ("COMPANY_PEC", "PEC", False), ("COMPANY_REA", "numero REA", False),
          ("COMPANY_HOSTING", "fornitore e paese del server", True)]
PAGES = {"privacy": "Informativa sulla privacy", "condizioni": "Condizioni di servizio", "cookie": "Cookie"}
MODEL_MAKERS = {"claude": "Anthropic (Stati Uniti)", "kimi": "Moonshot AI (Singapore e Cina)", "chatgpt": "OpenAI (Stati Uniti)"}


def company():
    """I dati dell'azienda come sono impostati adesso, senza segnaposto."""
    return {key: config.get(key, "").strip() for key, _, _ in FIELDS}


def missing():
    """I dati obbligatori che mancano ancora, con il loro nome leggibile."""
    have = company()
    return [label for key, label, required in FIELDS if required and not have[key]]


def footer_line():
    """La riga con i dati dell'azienda in fondo al sito: compare solo con quello che è stato impostato."""
    c = company()
    parts = [c["COMPANY_NAME"], "P. IVA %s" % c["COMPANY_VAT"] if c["COMPANY_VAT"] else "", c["COMPANY_ADDRESS"],
             "REA %s" % c["COMPANY_REA"] if c["COMPANY_REA"] else "", c["COMPANY_EMAIL"], "PEC %s" % c["COMPANY_PEC"] if c["COMPANY_PEC"] else ""]
    return " · ".join(p for p in parts if p)


def _values():
    c = company()
    v = {key: c[key] or "[da compilare: %s]" % label for key, label, _ in FIELDS}
    models = [m for m in catalog.load()["models"] if m["id"] in MODEL_MAKERS]
    return {"nome": v["COMPANY_NAME"], "piva": v["COMPANY_VAT"], "sede": v["COMPANY_ADDRESS"], "email": v["COMPANY_EMAIL"],
            "server": v["COMPANY_HOSTING"], "pec": (" PEC: %s." % c["COMPANY_PEC"]) if c["COMPANY_PEC"] else "",
            "modelli": "; ".join("%s, di %s" % (m["name"], MODEL_MAKERS[m["id"]]) for m in models)}


PRIVACY = [
    ("Chi tratta i tuoi dati", [
        "Il titolare del trattamento è {nome}, partita IVA {piva}, con sede in {sede}. Per qualunque domanda sui tuoi dati scrivi a {email}.{pec}",
        "Questa informativa riguarda il sito e l'applicazione Alvea ed è scritta secondo il Regolamento (UE) 2016/679 (GDPR)."]),
    ("Quali dati raccogliamo", [[
        "Dati dell'account: nome, email, nome dell'azienda. La password non la conserviamo: ne teniamo solo un'impronta non reversibile.",
        "Contenuti che inserisci o che gli agenti preparano per te: messaggi, bozze, contatti commerciali, appuntamenti, documenti e file caricati.",
        "Credenziali dei servizi che colleghi (posta, calendario, negozio, messaggistica): le conserviamo cifrate e le usiamo solo per eseguire le azioni che chiedi.",
        "Dati di pagamento: il pagamento con carta avviene sulle pagine di Stripe. Noi non vediamo e non conserviamo il numero della carta; riceviamo solo l'esito e gli identificativi del cliente e dell'abbonamento.",
        "Dati tecnici: indirizzo IP e orario delle richieste, usati per la sicurezza e per limitare gli abusi, e il registro delle azioni fatte nell'azienda (chi ha fatto cosa e quando)."]]),
    ("Perché li trattiamo", [[
        "Per fornirti il servizio che hai chiesto: esecuzione del contratto (art. 6, par. 1, lett. b GDPR).",
        "Per fatture, contabilità e obblighi fiscali: obbligo di legge (art. 6, par. 1, lett. c).",
        "Per la sicurezza della piattaforma e la prevenzione degli abusi: nostro legittimo interesse (art. 6, par. 1, lett. f)."],
        "Non facciamo profilazione a fini pubblicitari e non vendiamo i tuoi dati."]),
    ("Intelligenza artificiale", [
        "Quando scrivi a un agente, il tuo messaggio e il contesto necessario vengono inviati al fornitore del modello che hai scelto, che genera la risposta. I modelli disponibili sono: {modelli}. Con un modello locale i testi restano sul server o sul tuo computer.",
        "Noi non usiamo i tuoi contenuti per addestrare modelli. Ogni fornitore tratta i dati secondo le proprie condizioni per l'uso tramite API, che ti invitiamo a leggere prima di scegliere il modello.",
        "Le azioni verso l'esterno (inviare un'email o un messaggio, scrivere un file, eseguire un comando) partono solo dopo l'approvazione di un amministratore della tua azienda."]),
    ("Chi riceve i dati", [
        "Per far funzionare il servizio usiamo fornitori che trattano i dati per nostro conto:", [
            "il fornitore del server: {server};",
            "i fornitori dei modelli di intelligenza artificiale indicati sopra, solo per i messaggi inviati al loro modello;",
            "Stripe, per i pagamenti con carta;",
            "il fornitore di posta con cui inviamo le email di servizio (conferma dell'indirizzo, nuova password, avvisi di scadenza);",
            "i servizi che decidi tu di collegare (per esempio Google, Telegram, WhatsApp, Shopify), con cui scambiamo solo i dati necessari a ciò che chiedi."],
        "Alcuni di questi fornitori si trovano fuori dallo Spazio economico europeo. In quei casi il trasferimento avviene con le garanzie previste dagli articoli 44 e seguenti del GDPR, come una decisione di adeguatezza o le clausole contrattuali tipo della Commissione europea."]),
    ("I dati dei tuoi clienti", [
        "Se nell'applicazione inserisci dati di altre persone (i tuoi clienti, contatti, dipendenti), il titolare di quei dati sei tu e noi li trattiamo per tuo conto come responsabile del trattamento, secondo l'articolo 28 del GDPR e le Condizioni di servizio. Sta a te avere una base valida per trattarli e informare gli interessati."]),
    ("Per quanto tempo", [[
        "Dati dell'account e contenuti: per tutta la durata del contratto. Dopo la chiusura li cancelliamo entro 30 giorni, salvo che tu chieda prima una copia.",
        "Dati di fatturazione: 10 anni, come richiede la legge.",
        "Dati tecnici di sicurezza: per il tempo necessario a riconoscere e gestire gli abusi."]]),
    ("I tuoi diritti", [
        "Puoi chiedere in ogni momento di accedere ai tuoi dati, correggerli, cancellarli, limitarne il trattamento, riceverne una copia in formato leggibile o opporti al trattamento (articoli da 15 a 22 del GDPR). Scrivi a {email}: rispondiamo entro un mese.",
        "Se ritieni che i tuoi dati siano trattati in modo scorretto puoi presentare un reclamo al Garante per la protezione dei dati personali (www.garanteprivacy.it)."]),
    ("Sicurezza", [
        "I dati di ogni azienda sono separati da quelli delle altre. Le credenziali dei servizi collegati sono cifrate. Ogni azione finisce in un registro concatenato, in cui una modifica successiva si riconosce. Il collegamento al sito è protetto con HTTPS."]),
    ("Minori", ["Il servizio è pensato per aziende e professionisti e non è rivolto a chi ha meno di 18 anni."]),
    ("Modifiche", ["Se cambiamo questa informativa in modo rilevante te lo comunichiamo per email o nell'applicazione prima che la modifica abbia effetto."]),
]

TERMS = [
    ("Chi fornisce il servizio", [
        "Alvea è fornito da {nome}, partita IVA {piva}, con sede in {sede} (di seguito «noi»). Contatti: {email}.{pec}",
        "Creando un account accetti queste condizioni. Se le accetti per conto di un'azienda dichiari di avere il potere di farlo."]),
    ("Il servizio", [
        "Alvea mette a disposizione agenti basati sull'intelligenza artificiale che preparano e svolgono attività per la tua azienda. Gli agenti e i limiti di ogni pacchetto sono quelli descritti nel sito al momento dell'acquisto.",
        "Alcuni agenti sono indicati come «solo bozze»: preparano il materiale, ma pubblicare o inviare resta a te."]),
    ("Account", [
        "I dati che inserisci alla registrazione devono essere veri. Sei responsabile della custodia delle tue credenziali e di ciò che fanno gli utenti che aggiungi alla tua azienda. Se sospetti un accesso non autorizzato avvisaci subito."]),
    ("Prezzi e pagamento", [[
        "Il prezzo è quello mostrato prima del pagamento: la quota del pacchetto più, se prevista, una quota per ogni agente acceso. I prezzi si intendono al netto dell'IVA, se dovuta.",
        "L'abbonamento è mensile, si paga in anticipo con carta tramite Stripe e si rinnova ogni mese finché non lo disdici.",
        "Se accendi o spegni agenti o cambi pacchetto, il prezzo dell'abbonamento si aggiorna di conseguenza e la differenza viene calcolata in proporzione ai giorni.",
        "Se un pagamento non va a buon fine ti avvisiamo. Trascorsi gli eventuali giorni di tolleranza il servizio va in pausa finché il pagamento non è regolare; i tuoi dati restano conservati."]]),
    ("Prova gratuita", [
        "Se alla registrazione è indicata una prova gratuita, dura i giorni indicati e non richiede la carta. Alla fine il servizio va in pausa finché non attivi l'abbonamento. Gli agenti che lavorano sul tuo computer richiedono l'abbonamento attivo."]),
    ("Crediti", [
        "Dove sono previsti, i crediti misurano l'uso dei modelli messi a disposizione da noi. Quelli inclusi nel pacchetto si rinnovano all'inizio di ogni mese e non si accumulano. Quelli comprati a parte non scadono finché il contratto è attivo. I crediti non sono convertibili in denaro. Se usi una tua chiave del modello i crediti non vengono scalati."]),
    ("Disdetta e recesso", [
        "Puoi disdire in qualsiasi momento dal tuo pannello. La disdetta vale dalla fine del periodo già pagato: fino ad allora continui a usare il servizio, e il periodo iniziato non viene rimborsato, salvo quanto previsto dalla legge.",
        "Se sei un consumatore hai 14 giorni dalla conclusione del contratto per recedere senza dare motivazioni, scrivendo a {email} (articoli 52 e seguenti del Codice del consumo). Se hai chiesto di iniziare a usare il servizio subito, in caso di recesso paghi solo la parte corrispondente ai giorni di servizio già forniti."]),
    ("Uso dell'intelligenza artificiale", [[
        "I testi e le proposte degli agenti sono generati da modelli di intelligenza artificiale e possono contenere errori o imprecisioni: vanno sempre controllati prima di usarli.",
        "Le azioni verso l'esterno partono solo dopo l'approvazione di un amministratore della tua azienda, che ne resta responsabile.",
        "Gli agenti non sostituiscono un professionista: bozze di contratti, preventivi, solleciti e testi amministrativi vanno verificati da chi ne ha la competenza."]]),
    ("Uso consentito", [
        "Non puoi usare Alvea per attività illecite, per inviare comunicazioni non richieste, per violare diritti di altri, per trattare dati di persone senza averne titolo, per aggirare i limiti del tuo pacchetto o per compromettere la sicurezza della piattaforma. In questi casi possiamo sospendere l'account."]),
    ("Contenuti e proprietà", [
        "I contenuti che inserisci e quelli che gli agenti producono per te restano tuoi. Ci autorizzi a trattarli solo per fornirti il servizio. Il software, il marchio e il sito di Alvea restano nostri."]),
    ("Servizi di terzi", [
        "Se colleghi servizi di altri fornitori (per esempio Google, Telegram, WhatsApp, Shopify, o una tua chiave di un modello) valgono anche le loro condizioni. Non rispondiamo dei loro malfunzionamenti o delle loro modifiche."]),
    ("Programma per il tuo computer", [
        "Il launcher è un programma facoltativo che permette ad alcuni agenti di lavorare sul tuo computer. Esegue solo i pacchetti firmati da noi e, per l'agente che lavora sul codice, solo i comandi che hai permesso tu. Lo installi e lo usi sotto la tua responsabilità sul computer che scegli."]),
    ("Disponibilità e modifiche", [
        "Facciamo il possibile perché il servizio sia sempre raggiungibile, ma non garantiamo che funzioni senza interruzioni: possono esserci manutenzioni e guasti, anche dei fornitori dei modelli.",
        "Possiamo aggiornare funzioni e agenti. Se cambiamo queste condizioni o i prezzi te lo comunichiamo con almeno 30 giorni di anticipo: se non sei d'accordo puoi disdire prima che la modifica abbia effetto."]),
    ("Responsabilità", [
        "Nei limiti consentiti dalla legge, la nostra responsabilità per danni derivanti dal servizio non supera quanto hai pagato nei dodici mesi precedenti. Questo limite non vale in caso di dolo o colpa grave e non riduce i diritti che la legge riconosce ai consumatori."]),
    ("Sospensione e chiusura", [
        "Possiamo sospendere o chiudere un account in caso di violazione grave di queste condizioni o di mancato pagamento prolungato, avvisandoti quando possibile. Alla chiusura puoi chiedere una copia dei tuoi dati entro 30 giorni; poi vengono cancellati, salvo quelli che la legge ci impone di conservare."]),
    ("Dati personali", [
        "I tuoi dati sono trattati come descritto nell'Informativa sulla privacy. Per i dati di altre persone che inserisci nell'applicazione tu sei il titolare del trattamento e noi il responsabile: li trattiamo solo secondo le tue istruzioni, per fornire il servizio, con misure di sicurezza adeguate e con i fornitori indicati nell'informativa (articolo 28 del GDPR)."]),
    ("Legge applicabile e foro", [
        "Queste condizioni sono regolate dalla legge italiana. Se sei un consumatore è competente il giudice del luogo in cui risiedi o hai il domicilio. Negli altri casi è competente in via esclusiva il foro della nostra sede."]),
]

COOKIES = [
    ("In breve", [
        "Alvea non usa cookie di profilazione né strumenti di statistica di terzi. Il sito non carica caratteri, script o immagini da altri siti. Per questo non vedi un banner dei cookie: per i soli strumenti tecnici la legge non richiede il consenso."]),
    ("Che cosa usiamo", [[
        "Accesso: quando entri nell'applicazione, il tuo accesso viene tenuto nella memoria della scheda del browser (sessionStorage). Sparisce quando chiudi la scheda o esci.",
        "Collegamento di Google: se un amministratore collega l'account Google dell'azienda, per circa dieci minuti viene usato un cookie tecnico che serve a completare il collegamento in sicurezza. Poi viene cancellato."]]),
    ("Pagamenti", [
        "Il pagamento con carta avviene sulle pagine di Stripe, che usa i propri cookie secondo la propria informativa."]),
    ("Come gestirli", [
        "Puoi cancellare o bloccare cookie e memoria del sito dalle impostazioni del tuo browser. Se blocchi la memoria della scheda non riuscirai a restare collegato all'applicazione."]),
]

TEXTS = {"privacy": PRIVACY, "condizioni": TERMS, "cookie": COOKIES}


def sections(page):
    """Le sezioni di una pagina con i dati dell'azienda già inseriti: [(titolo, [paragrafo | [voci]])]."""
    v = _values()
    fill = lambda text: text.format(**v)
    return [(title, [[fill(i) for i in row] if isinstance(row, list) else fill(row) for row in rows]) for title, rows in TEXTS[page]]
