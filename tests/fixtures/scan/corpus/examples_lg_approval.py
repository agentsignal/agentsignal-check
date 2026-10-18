"""LangGraph 1.2.15: interrupt() approval persisted with SqliteSaver, so any process can resume.

Graph: propose -> approval (interrupt only, nothing before it) -> execute (side effect) | END.
execute uses an idempotency key derived from thread_id, so two workers resuming the same
thread (or a replayed execute step) charge once. No LLM: propose is deterministic.

CLI (used by the crash tests):
  python approval.py start  --db ckpt.db --ledger effects.db --thread order-42
  python approval.py resume --db ckpt.db --ledger effects.db --thread order-42 --decision approve|reject [--worker w1] [--naive]
"""
import argparse, json, sqlite3, time
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import interrupt, Command
import ledger

class S(TypedDict, total=False):
    amount: int
    approved: bool
    reason: str
    result: str

def build(checkpointer, ledger_path, worker="main", idempotent=True, hold=0.0):
    def propose(s):
        return {"amount": 1999}
    def approval(s):
        answer = interrupt({"question": "Approve charge?", "amount": s["amount"]})
        return {"approved": bool(answer.get("approved")), "reason": answer.get("reason", "")}
    def route(s):
        return "execute" if s["approved"] else END
    def execute(s, config):
        time.sleep(hold)  # tests: widen the race window between two workers
        key = f"charge:{config['configurable']['thread_id']}" if idempotent else None
        ran = ledger.perform(ledger_path, f"charge {s['amount']}", key=key, worker=worker)
        return {"result": "charged" if ran else "already charged"}
    b = StateGraph(S)
    b.add_node("propose", propose); b.add_node("approval", approval); b.add_node("execute", execute)
    b.add_edge(START, "propose"); b.add_edge("propose", "approval")
    b.add_conditional_edges("approval", route, ["execute", END]); b.add_edge("execute", END)
    return b.compile(checkpointer=checkpointer)

def saver(path):
    return SqliteSaver(sqlite3.connect(path, check_same_thread=False, timeout=30))

def main():
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["start", "resume"])
    p.add_argument("--db", required=True); p.add_argument("--ledger", required=True)
    p.add_argument("--thread", default="order-42"); p.add_argument("--decision", default="approve")
    p.add_argument("--worker", default="main"); p.add_argument("--naive", action="store_true")
    p.add_argument("--hold", type=float, default=0.0)
    a = p.parse_args()
    g = build(saver(a.db), a.ledger, a.worker, not a.naive, a.hold)
    cfg = {"configurable": {"thread_id": a.thread}}
    if a.cmd == "start":
        out = g.invoke({}, cfg)
        print(json.dumps({"interrupt": out["__interrupt__"][0].value, "next": list(g.get_state(cfg).next)}))
    else:
        out = g.invoke(Command(resume={"approved": a.decision == "approve", "reason": "cli"}), cfg)
        print(json.dumps({"result": out.get("result"), "approved": out.get("approved"),
                          "next": list(g.get_state(cfg).next)}))

if __name__ == "__main__":
    main()
