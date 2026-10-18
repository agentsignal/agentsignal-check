"""Approval in its own node, then a charge with NO idempotency key in the next node (the common naive shape;
same as Backend's 54 lg_naive control). A kill right after the charge must always be caught on resume."""
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from tests.fixtures import payments
class S(TypedDict, total=False):
    amount: int; approved: bool; charge_id: str
def approve(s): return {"approved": bool(interrupt({"q": f"charge {s['amount']}?"}))}
def execute(s):
    if not s.get("approved"): return {}
    return {"charge_id": payments.charge(s["amount"])}
def build(cp):
    g = StateGraph(S); g.add_node("approve", approve); g.add_node("execute", execute)
    g.add_edge(START, "approve"); g.add_edge("approve", "execute"); g.add_edge("execute", END)
    return g.compile(checkpointer=cp)
