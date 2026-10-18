"""No interrupt() at all, keyed charge: lost_on_restart and replay are SKIP (nothing to approve), the rest PASS."""
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from tests.fixtures import payments
class S(TypedDict, total=False):
    amount: int; charge_id: str
def execute(s): return {"charge_id": payments.charge(s["amount"], idempotency_key=f"order-{s['amount']}")}
def build(cp):
    g = StateGraph(S); g.add_node("execute", execute); g.add_edge(START, "execute"); g.add_edge("execute", END)
    return g.compile(checkpointer=cp)
