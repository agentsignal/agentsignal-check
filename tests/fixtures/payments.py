"""Fake payment provider persisted per scenario; honours idempotency keys like Stripe."""
import json, os
def _db():
    p = os.environ.get("AS_SCENARIO", "/tmp/agentsignal") + ".provider.json"
    return p, (json.load(open(p)) if os.path.exists(p) else {"n": 0, "keys": {}})
def charge(amount, idempotency_key=None):
    p, db = _db()
    if idempotency_key and idempotency_key in db["keys"]:
        return db["keys"][idempotency_key]
    db["n"] += 1; cid = f"ch_{db['n']}"
    if idempotency_key: db["keys"][idempotency_key] = cid
    tmp = f"{p}.{os.getpid()}.tmp"           # atomic: a hard kill mid-write never leaves a torn file
    with open(tmp, "w") as fh:
        json.dump(db, fh); fh.flush(); os.fsync(fh.fileno())
    os.replace(tmp, p); return cid
