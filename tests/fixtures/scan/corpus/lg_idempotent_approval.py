"""Side effects must not repeat when a node re-executes on resume after interrupt().
Shows: (1) NAIVE: side effect before interrupt() in the same node -> runs twice.
       (2) FIXED: split nodes + idempotency key (derived from thread_id) checked against a ledger,
           so even a retried/re-run execute node charges once. No LLM; all LangGraph code real.
The 'payment API' is a fake in-process ledger (stands in for Stripe etc.)."""
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import interrupt, Command

class FakePaymentAPI:
    def __init__(self): self.charges = []; self.by_key = {}
    def charge(self, amount, idempotency_key=None):
        if idempotency_key and idempotency_key in self.by_key:   # server-side dedupe, like Stripe
            return self.by_key[idempotency_key]
        cid = f"ch_{len(self.charges)+1}"; self.charges.append((cid, amount))
        if idempotency_key: self.by_key[idempotency_key] = cid
        return cid

class S(TypedDict, total=False):
    amount: int
    charge_id: str
    approved: bool

def naive_graph(api):
    def node(s):
        api.charge(s["amount"])                       # BAD: before interrupt -> re-runs on resume
        ok = interrupt({"approve_charge": s["amount"]})
        return {"approved": ok}
    b = StateGraph(S); b.add_node("node", node); b.add_edge(START, "node"); b.add_edge("node", END)
    return b.compile(checkpointer=InMemorySaver())

def fixed_graph(api):
    def approval(s):                                   # only interrupt(); nothing before it
        return {"approved": interrupt({"approve_charge": s["amount"]})}
    def route(s): return "execute" if s["approved"] else END
    def execute(s, config):
        key = f"charge:{config['configurable']['thread_id']}"   # stable per run
        return {"charge_id": api.charge(s["amount"], idempotency_key=key)}
    b = StateGraph(S)
    b.add_node("approval", approval); b.add_node("execute", execute)
    b.add_edge(START, "approval"); b.add_conditional_edges("approval", route, ["execute", END]); b.add_edge("execute", END)
    return b.compile(checkpointer=InMemorySaver())

for name, mk in [("NAIVE", naive_graph), ("FIXED", fixed_graph)]:
    api = FakePaymentAPI(); g = mk(api); cfg = {"configurable": {"thread_id": "order-42"}}
    g.invoke({"amount": 1999}, cfg)
    print(f"== {name}: after pause, charges={api.charges}")
    out = g.invoke(Command(resume=True), cfg)
    print(f"   after resume, charges={api.charges} state={out}")
    if name == "FIXED":
        # simulate an at-least-once retry of the execute step (e.g. worker crashed after the API call
        # but before the checkpoint was written): re-run from the checkpoint *before* execute
        hist = list(g.get_state_history(cfg))
        before_exec = next(h for h in hist if h.next == ("execute",))
        g.invoke(None, before_exec.config)
        print(f"   after replaying execute from checkpoint, charges={api.charges}  (still 1 -> idempotent)")
