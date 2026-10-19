"""Approval in its own node; the effect node is guarded only by a state check (no idempotency key)."""
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from tests.fixtures import payments
class S(TypedDict, total=False):
    amount: int; approved: bool; charge_id: str
_done = {}
def approve(s): return {"approved": bool(interrupt({"q": f"charge {s['amount']}?"}))}
def execute(s):
    if s.get("charge_id") or not s.get("approved"): return {}
    return {"charge_id": payments.charge(s["amount"])}
def build(cp):
    g = StateGraph(S); g.add_node("approve", approve); g.add_node("execute", execute)
    g.add_edge(START, "approve"); g.add_edge("approve", "execute"); g.add_edge("execute", END)
    return g.compile(checkpointer=cp)
