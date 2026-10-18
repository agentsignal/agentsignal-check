"""SQL ledger plumbing: INSERT OR IGNORE / SELECT through c.execute is not an unkeyed side effect."""
import sqlite3
from langgraph.types import interrupt


def _db(path):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE IF NOT EXISTS effects (key TEXT UNIQUE)")
    return c


def perform(path, key):
    c = _db(path)
    c.execute("INSERT OR IGNORE INTO effects VALUES (?)", (key,))
    ok = interrupt("continue?")
    return c.execute("SELECT key FROM effects").fetchall(), ok
