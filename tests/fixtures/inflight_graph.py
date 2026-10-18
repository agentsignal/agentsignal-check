"""Leased side-effect ledger (board 54 G2/G7 shape) around a provider that dedupes by key.
A kill between the provider call and the ledger commit leaves the row `pending` with a live lease,
so the resumed node gets EffectInFlight (a clean refusal) instead of charging twice."""
import json, os, time
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt


class EffectInFlight(Exception):
    """Defined locally on purpose: agentsignal-check matches it by class name."""


def _load(suffix, empty):
    p = os.environ.get("AS_SCENARIO", "/tmp/agentsignal") + suffix
    return p, (json.load(open(p)) if os.path.exists(p) else empty)


def provider_charge(amount, key):
    p, db = _load(".provider.json", {"n": 0, "keys": {}})
    if key in db["keys"]:
        return db["keys"][key]
    db["n"] += 1; cid = f"ch_{db['n']}"; db["keys"][key] = cid
    json.dump(db, open(p, "w")); return cid


def charge(amount, key):
    p, led = _load(".ledger.json", {})
    row = led.get(key)
    if row and row["status"] == "committed":
        return row["result"]
    if row and row["status"] == "pending" and row["lease_until"] > time.time():
        raise EffectInFlight(f"{key} is in flight until {row['lease_until']:.0f}")
    led[key] = {"status": "pending", "lease_until": time.time() + 300}
    json.dump(led, open(p, "w"))
    cid = provider_charge(amount, key=key)          # module-global lookup: the checker can watch it
    led[key] = {"status": "committed", "result": cid}
    json.dump(led, open(p, "w"))
    return cid


class S(TypedDict, total=False):
    amount: int; approved: bool; charge_id: str


def approve(s):
    return {"approved": bool(interrupt({"q": "charge?"}))}


def execute(s):
    if not s.get("approved"):
        return {}
    key = f"order:{os.environ.get('AS_SCENARIO', '')}"     # G8: scenario in the key
    return {"charge_id": charge(s["amount"], key=key)}


def build(cp):
    g = StateGraph(S); g.add_node("approve", approve); g.add_node("execute", execute)
    g.add_edge(START, "approve"); g.add_edge("approve", "execute"); g.add_edge("execute", END)
    return g.compile(checkpointer=cp)
