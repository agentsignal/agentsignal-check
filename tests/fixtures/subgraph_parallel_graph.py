"""Three parallel approvals INSIDE a subgraph, answered one at a time; each charges AFTER its interrupt().
langgraph <= 1.2.15 replays the subgraph on every resume, so already-answered siblings charge again
(a: 3x, b: 2x, c: 1x). Fixed in 1.2.16 by langgraph-ai/langgraph#9315. The parent graph's final state
looks correct either way; only the side-effect ledger shows the duplicates."""
import operator, threading
from typing import Annotated, TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from tests.fixtures import payments
_LOCK = threading.Lock()   # siblings run in parallel threads; payments.py is a plain JSON file
AMOUNTS = {"a": 100, "b": 200, "c": 300}
class S(TypedDict, total=False):
    log: Annotated[list, operator.add]
def _node(name):
    def f(s):
        ok = interrupt({"q": f"charge {AMOUNTS[name]} for {name}?", "tool": name})
        with _LOCK:
            cid = payments.charge(AMOUNTS[name]) if ok else None
        return {"log": [f"{name}:{cid}"]}
    return f
def build(cp):
    sb = StateGraph(S)
    for n in AMOUNTS:
        sb.add_node(n, _node(n)); sb.add_edge(START, n)
    p = StateGraph(S); p.add_node("approvals", sb.compile()); p.add_edge(START, "approvals"); p.add_edge("approvals", END)
    return p.compile(checkpointer=cp)
