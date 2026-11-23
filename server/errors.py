"""Errore con codice HTTP, mostrato all'utente così com'è."""


class ApiError(Exception):
    def __init__(self, status, message):
        Exception.__init__(self, message)
        self.status = status
        self.message = message
