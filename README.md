# SalesForB

Piattaforma di agenti AI per aziende: server, schermata con ufficio 3D e launcher per far girare alcuni agenti sul computer del cliente.

- **Installare sul server e collegare al sito:** [`GUIDA.md`](GUIDA.md)
- **Tutte le funzioni nel dettaglio:** [`LEGGIMI.md`](LEGGIMI.md)

Provarlo subito, senza chiavi e senza installare niente oltre a Python 3.9 o successivo:

```sh
cp .env.example .env
python3 manage.py demo     # crea tre aziende di prova e stampa le password
python3 -m server          # poi apri http://127.0.0.1:8787
```

Prove automatiche: `python3 -m unittest discover -s tests`

Nel repository non ci sono mai: il file `.env`, il database (`data/`), i file dei clienti (`storage/`), le chiavi.
