"""Side-effect ledger shared across processes (stands in for a payment/email API).
A UNIQUE idempotency key makes a repeated call a no-op, even from two workers at once."""
import sqlite3

def _db(path):
    c = sqlite3.connect(path, timeout=30, isolation_level=None)
    c.execute("CREATE TABLE IF NOT EXISTS effects (key TEXT UNIQUE, action TEXT, worker TEXT)")
    return c

def perform(path, action, key=None, worker="main"):
    """Record a side effect. With a key, only the first caller wins; returns True if it ran."""
    c = _db(path)
    try:
        cur = c.execute("INSERT OR IGNORE INTO effects VALUES (?,?,?)", (key, action, worker))
        return cur.rowcount == 1
    finally:
        c.close()

def effects(path):
    c = _db(path)
    try:
        return c.execute("SELECT key, action, worker FROM effects").fetchall()
    finally:
        c.close()
