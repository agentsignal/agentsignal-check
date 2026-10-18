"""Fault injection for the exit-code tests (Elon 2026-10-18: any error or FAIL exits non-zero).
Approval node, then `execute`. The FIRST execute in a scenario charges normally (so the baseline passes);
any later execute (a resume or a replay) misbehaves as AS_FAULT says, before charging:
  raise  -> RuntimeError          hang -> sleeps (child timeout)
  crash  -> SIGSEGV (child crash) exit -> sys.exit(3)
"""
import json, os, signal, sys, time
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from tests.fixtures import payments
class S(TypedDict, total=False):
    amount: int; approved: bool; charge_id: str
def approve(s): return {"approved": bool(interrupt({"q": f"charge {s['amount']}?"}))}
def _charged_before():
    p = os.environ.get("AS_SCENARIO", "/tmp/agentsignal") + ".provider.json"
    return os.path.exists(p) and json.load(open(p)).get("n", 0) > 0
def execute(s):
    if not s.get("approved"): return {}
    if _charged_before():
        f = os.environ.get("AS_FAULT", "raise")
        if f == "hang": time.sleep(3600)
        if f == "crash": os.kill(os.getpid(), signal.SIGSEGV)
        if f == "exit": sys.exit(3)
        raise RuntimeError("injected fault on resume")
    return {"charge_id": payments.charge(s["amount"], idempotency_key=f"{os.environ.get('AS_SCENARIO')}:order")}
def build(cp):
    g = StateGraph(S); g.add_node("approve", approve); g.add_node("execute", execute)
    g.add_edge(START, "approve"); g.add_edge("approve", "execute"); g.add_edge("execute", END)
    return g.compile(checkpointer=cp)
