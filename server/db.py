"""Database SQLite. Ogni riga di dati aziendali porta tenant_id e ogni query lo filtra."""
from __future__ import annotations

import contextlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, package_id TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, tenant_id INTEGER REFERENCES tenants(id), email TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('superadmin','admin','member')),
  pw_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL, expires_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS conversations(
  id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
  agent_id TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, conversation_id INTEGER NOT NULL,
  role TEXT NOT NULL, content TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS approvals(
  id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, requested_by INTEGER NOT NULL,
  agent_id TEXT NOT NULL, tool TEXT NOT NULL, args TEXT NOT NULL, summary TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', decided_by INTEGER, decided_at TEXT, result TEXT,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, user_id INTEGER, action TEXT NOT NULL,
  detail TEXT NOT NULL, created_at TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS connectors(
  tenant_id INTEGER NOT NULL, name TEXT NOT NULL, config TEXT NOT NULL,
  encrypted INTEGER NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(tenant_id, name));
CREATE TABLE IF NOT EXISTS leads(
  id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, name TEXT NOT NULL, email TEXT, phone TEXT,
  company TEXT, status TEXT NOT NULL DEFAULT 'nuovo', notes TEXT, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, title TEXT NOT NULL, starts_at TEXT NOT NULL,
  ends_at TEXT, location TEXT, notes TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS drafts(
  id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, channel TEXT NOT NULL, title TEXT NOT NULL,
  body TEXT NOT NULL, created_by INTEGER, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS usage(
  id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, user_id INTEGER NOT NULL, model TEXT NOT NULL,
  input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS credit_ledger(
  id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, kind TEXT NOT NULL, amount INTEGER NOT NULL,
  from_included INTEGER NOT NULL DEFAULT 0, from_extra INTEGER NOT NULL DEFAULT 0, model TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '', period TEXT NOT NULL, created_by INTEGER, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS credit_ledger_tenant ON credit_ledger(tenant_id, period, kind);
CREATE TABLE IF NOT EXISTS automations(
  id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, name TEXT NOT NULL, agent_id TEXT NOT NULL,
  model TEXT NOT NULL, prompt TEXT NOT NULL, kind TEXT NOT NULL, hour INTEGER, minute INTEGER,
  weekdays TEXT NOT NULL DEFAULT '', every_minutes INTEGER, active INTEGER NOT NULL DEFAULT 1,
  next_run_at INTEGER NOT NULL, last_run_at TEXT, last_status TEXT, last_output TEXT,
  fail_count INTEGER NOT NULL DEFAULT 0, created_by INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS news(
  id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '', is_read INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memory(
  id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
  body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', created_by INTEGER,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memory_chunks(
  id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, memory_id INTEGER NOT NULL,
  pos INTEGER NOT NULL, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS oauth_states(
  state_hash TEXT PRIMARY KEY, tenant_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
  verifier TEXT NOT NULL, browser_hash TEXT NOT NULL, scopes TEXT NOT NULL, expires_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS tenant_agents(
  tenant_id INTEGER NOT NULL, agent_id TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1,
  config_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL, PRIMARY KEY(tenant_id, agent_id));
CREATE TABLE IF NOT EXISTS media_assets(
  id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, kind TEXT NOT NULL, original_name TEXT NOT NULL,
  stored_name TEXT NOT NULL, mime TEXT NOT NULL, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
  uploaded_by INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS platform_settings(
  name TEXT PRIMARY KEY, config TEXT NOT NULL, encrypted INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS billing_events(
  id TEXT PRIMARY KEY, type TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS devices(
  id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
  paired_by INTEGER NOT NULL, specs TEXT NOT NULL DEFAULT '{}', checks TEXT NOT NULL DEFAULT '{}', launcher_version TEXT NOT NULL DEFAULT '',
  last_seen INTEGER NOT NULL DEFAULT 0, revoked INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS device_codes(
  code_hash TEXT PRIMARY KEY, tenant_id INTEGER NOT NULL, user_id INTEGER NOT NULL, expires_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, agent_id TEXT NOT NULL, tool TEXT NOT NULL, package TEXT NOT NULL,
  place TEXT NOT NULL, args TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'in_coda', progress INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '',
  device_id INTEGER, result TEXT NOT NULL DEFAULT '', asset_id INTEGER, attempts INTEGER NOT NULL DEFAULT 0, requested_by INTEGER NOT NULL,
  created_at TEXT NOT NULL, claimed_at INTEGER, done_at TEXT);
CREATE INDEX IF NOT EXISTS ix_jobs_tenant ON jobs(tenant_id, status, id);
CREATE INDEX IF NOT EXISTS ix_devices_tenant ON devices(tenant_id, revoked);
CREATE TABLE IF NOT EXISTS email_tokens(
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ix_media_tenant ON media_assets(tenant_id, id);
CREATE INDEX IF NOT EXISTS ix_media_sha ON media_assets(tenant_id, sha256);
CREATE INDEX IF NOT EXISTS ix_automations_due ON automations(active, next_run_at);
CREATE INDEX IF NOT EXISTS ix_news_tenant ON news(tenant_id, id);
CREATE INDEX IF NOT EXISTS ix_memory_tenant ON memory(tenant_id, kind, status);
CREATE INDEX IF NOT EXISTS ix_chunks_tenant ON memory_chunks(tenant_id, memory_id);
CREATE INDEX IF NOT EXISTS ix_audit_tenant ON audit(tenant_id, id);
CREATE INDEX IF NOT EXISTS ix_messages_conv ON messages(tenant_id, conversation_id, id);
CREATE INDEX IF NOT EXISTS ix_approvals_tenant ON approvals(tenant_id, status, id);
CREATE INDEX IF NOT EXISTS ix_leads_tenant ON leads(tenant_id, id);
"""


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def durable(con):
    """Per questa connessione ogni scrittura va sul disco prima di essere data per fatta. Più lento: solo dove serve (i pagamenti)."""
    con.execute("PRAGMA synchronous=FULL")


@contextlib.contextmanager
def transaction(con):
    """Tutto o niente. Dentro il blocco le scritture valgono solo se si arriva in fondo; se qualcosa si rompe (o il server viene
    spento a metà) non ne resta nessuna. Serve dove si conta e poi si scrive (i limiti del pacchetto) e dove più scritture
    stanno in piedi solo insieme (un pagamento ricevuto). Tiene fermo chi vuole scrivere: dentro niente rete e niente attese.
    Se una transazione è già aperta su questa connessione, il blocco ne fa parte."""
    if con.in_transaction:
        yield con
        return
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")


def connect(path=None):
    """Una connessione per richiesta. Autocommit: le transazioni esplicite sono solo nel registro."""
    p = Path(path) if path else config.db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists()
    con = sqlite3.connect(str(p), timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=15000")
    # Con il registro WAL, «NORMAL» è la scelta consigliata da SQLite: il disco viene sincronizzato ai punti di controllo e non a ogni
    # scrittura. Uno spegnimento del programma non perde niente e il database resta sempre integro; solo una mancanza di corrente può
    # perdere le ultimissime scritture. Dove nemmeno quelle si possono perdere (un pagamento confermato a Stripe) si usa durable().
    con.execute("PRAGMA synchronous=NORMAL")
    if new:
        try:
            os.chmod(str(p), 0o600)
        except OSError:
            pass
    return con


# Colonne aggiunte dopo la prima versione: (tabella, colonna, dichiarazione).
MIGRATIONS = [("users", "lang", "TEXT NOT NULL DEFAULT 'it'"),  # la lingua in cui la persona usa l'interfaccia: serve per le email e per gli agenti
              ("tenants", "stopped", "INTEGER NOT NULL DEFAULT 0"),
              ("tenants", "origin", "TEXT NOT NULL DEFAULT 'gestore'"),         # 'gestore' oppure 'registrazione'
              ("tenants", "platform_keys", "INTEGER NOT NULL DEFAULT 1"),       # può usare le chiavi dei modelli della piattaforma
              ("users", "email_verified", "INTEGER NOT NULL DEFAULT 1"),
              ("tenants", "kind", "TEXT NOT NULL DEFAULT 'azienda'"),
              ("tenants", "activity", "TEXT NOT NULL DEFAULT ''"),              # tipo di attività scelto dal catalogo (negozio, studio, ...): dà il contesto agli agenti           # 'azienda' oppure 'persona' (chi si registra solo per sé)
              ("users", "totp_secret", "TEXT NOT NULL DEFAULT ''"),             # accesso in due passaggi: chiave dell'app (cifrata quando si può)
              ("users", "totp_on", "INTEGER NOT NULL DEFAULT 0"),               # 1 da quando il primo codice è stato confermato
              ("users", "totp_last", "INTEGER NOT NULL DEFAULT 0"),             # ultimo intervallo usato: lo stesso codice non vale due volte
              ("users", "totp_codes", "TEXT NOT NULL DEFAULT ''"),              # impronte dei codici di riserva non ancora usati
              ("drafts", "agent_id", "TEXT"),
              ("tenant_agents", "mode", "TEXT NOT NULL DEFAULT 'cloud'"),
              ("email_tokens", "kind", "TEXT NOT NULL DEFAULT 'verifica'"),     # 'verifica' dell'indirizzo oppure nuova 'password'
              ("tenants", "monthly_token_cap", "INTEGER NOT NULL DEFAULT 0"),
              ("tenants", "paid_until", "TEXT NOT NULL DEFAULT ''"),            # ultimo giorno pagato (AAAA-MM-GG); vuoto = nessuna scadenza
              ("tenants", "stripe_customer", "TEXT NOT NULL DEFAULT ''"),
              ("tenants", "stripe_subscription", "TEXT NOT NULL DEFAULT ''"),
              ("tenants", "stripe_amount", "INTEGER NOT NULL DEFAULT 0"),       # centesimi al mese dell'abbonamento in corso, quando il prezzo lo calcola Alvea
              ("tenants", "on_trial", "INTEGER NOT NULL DEFAULT 0"),            # 1 finché l'azienda è in prova gratuita e non ha ancora pagato
              ("tenants", "payment_failed_at", "TEXT NOT NULL DEFAULT ''"),     # ultimo addebito non riuscito; si svuota al primo pagamento
              ("tenants", "stripe_cancel_at", "TEXT NOT NULL DEFAULT ''"),      # disdetta registrata: giorno in cui finisce l'abbonamento con carta
              ("tenants", "expiry_notice_for", "TEXT NOT NULL DEFAULT ''")]     # scadenza per cui l'email di avviso è già partita


def init(con):
    """Crea le tabelle mancanti e aggiunge le colonne nuove. Si può rilanciare: non tocca i dati."""
    con.executescript(SCHEMA)
    for table, column, decl in MIGRATIONS:
        have = [r[1] for r in con.execute("PRAGMA table_info(%s)" % table)]
        if column not in have:
            con.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, decl))
    from . import team  # import qui: team usa questo modulo
    team.backfill(con)


def rows(cur):
    return [dict(r) for r in cur.fetchall()]
