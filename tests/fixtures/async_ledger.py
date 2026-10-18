"""Async effects for the checker's own tests (board 54 X2: async effects must be awaited before recording).

acharge        an async, leased side-effect ledger (board 54 G2/G7 shape, file-backed) around the sync,
               key-deduping provider payments.charge. A replay of a committed key returns the stored
               result WITHOUT calling the provider (short-circuit); a pending row inside its lease raises
               EffectInFlight. Watching acharge AND payments:charge must not report a duplicate.
acharge_naive  async, no key: every call is a real new charge (must still be caught).
"""
import asyncio, json, os, time
from tests.fixtures import payments


class EffectInFlight(Exception):
    """Defined locally on purpose: agentsignal-check matches it by class name."""


def _ledger():
    p = os.environ.get("AS_SCENARIO", "/tmp/agentsignal") + ".aledger.json"
    return p, (json.load(open(p)) if os.path.exists(p) else {})


def _save(p, led):
    with open(p, "w") as fh:
        json.dump(led, fh); fh.flush(); os.fsync(fh.fileno())


async def acharge(amount, order_id="order-42"):
    key = f"{os.environ.get('AS_SCENARIO', '')}:{order_id}:charge"     # stable run data (+ scenario, G8)
    p, led = _ledger()
    row = led.get(key)
    if row and row["status"] == "committed":
        await asyncio.sleep(0)
        return row["result"]                                            # replay: short-circuit
    if row and row["status"] == "pending" and row["lease_until"] > time.time():
        raise EffectInFlight(f"{key} is in flight")
    led[key] = {"status": "pending", "lease_until": time.time() + 300}; _save(p, led)
    await asyncio.sleep(0)
    cid = payments.charge(amount, idempotency_key=key)                  # module-global lookup: watchable
    led[key] = {"status": "committed", "result": cid}; _save(p, led)
    return cid


async def acharge_naive(amount):
    await asyncio.sleep(0)
    return payments.charge(amount)


def acharge_via_sync_wrapper(amount):
    """A plain (non-async) callable that hands back a coroutine, like a decorator not marked async."""
    return acharge_naive(amount)
