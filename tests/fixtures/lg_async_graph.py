"""LangGraph: approval node, then a sync node that runs an ASYNC charge (asyncio.run) -- build() through the
async leased ledger (async_ledger.acharge), build_naive() with no key (async_ledger.acharge_naive)."""
import asyncio
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from tests.fixtures import async_ledger
class S(TypedDict, total=False):
    amount: int; approved: bool; charge_id: str
def approve(s): return {"approved": bool(interrupt({"q": f"charge {s['amount']}?"}))}
def _graph(cp, fn):
    def execute(s):
        if not s.get("approved"): return {}
        return {"charge_id": asyncio.run(fn(s["amount"]))}
    g = StateGraph(S); g.add_node("approve", approve); g.add_node("execute", execute)
    g.add_edge(START, "approve"); g.add_edge("approve", "execute"); g.add_edge("execute", END)
    return g.compile(checkpointer=cp)
def build(cp): return _graph(cp, lambda a: async_ledger.acharge(a))
def build_naive(cp): return _graph(cp, lambda a: async_ledger.acharge_naive(a))
