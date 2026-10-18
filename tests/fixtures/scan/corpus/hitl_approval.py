"""LangGraph HITL: interrupt() pauses a node, checkpointer persists, Command(resume=...) continues.
No LLM involved: the 'proposal' node is deterministic (stands in for a model). All LangGraph code is real."""
from typing import TypedDict
from pydantic import BaseModel
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import interrupt, Command

class Decision(BaseModel):
    """Shape of a human's answer. A resume value that doesn't match raises pydantic.ValidationError
    (langgraph >= 1.2.15 marks it, see langgraph.errors.is_invalid_resume); nothing is saved and the
    interrupt stays pending, so a malformed answer can never be read as an approval."""
    approved: bool
    reason: str | None = None

class S(TypedDict, total=False):
    request: str
    action: dict
    decision: str
    result: str

def propose(state: S):
    print("  [propose] runs")
    return {"action": {"tool": "delete_records", "table": state["request"], "rows": 1200}}

def approval(state: S):
    print("  [approval] node (re)starts")  # NOTE: node re-executes from the top on resume
    answer = interrupt({"question": "Approve this action?", "action": state["action"]}, response_schema=Decision)
    if answer.approved:
        return Command(goto="execute", update={"decision": "approved"})
    return Command(goto=END, update={"decision": "rejected", "result": f"rejected: {answer.reason}"})

def execute(state: S):
    a = state["action"]
    return {"result": f"executed {a['tool']} on {a['table']} ({a['rows']} rows)"}

b = StateGraph(S)
b.add_node("propose", propose)
b.add_node("approval", approval, destinations=("execute", END))
b.add_node("execute", execute)
b.add_edge(START, "propose"); b.add_edge("propose", "approval"); b.add_edge("execute", END)
graph = b.compile(checkpointer=InMemorySaver())

def demo_invalid_resume():
    """A malformed answer fails the resume; the run stays paused and a valid answer still works."""
    from langgraph.errors import is_invalid_resume
    cfg = {"configurable": {"thread_id": "t-invalid"}}
    print("== thread t-invalid ==")
    graph.invoke({"request": "customers"}, cfg)
    for bad in ["yes", {"approved": "maybe"}]:
        try:
            graph.invoke(Command(resume=bad), cfg)
            print("  invalid resume", repr(bad), "ACCEPTED")
        except Exception as e:
            print("  invalid resume", repr(bad), "raised", type(e).__name__, "is_invalid_resume=%s" % is_invalid_resume(e))
        print("  still paused at:", graph.get_state(cfg).next)
    out = graph.invoke(Command(resume={"approved": False, "reason": "retry after bad input"}), cfg)
    print("  final:", {k: out[k] for k in ("decision", "result")})

for tid, resume in [("t-approve", {"approved": True}), ("t-reject", {"approved": False, "reason": "too many rows"})]:
    cfg = {"configurable": {"thread_id": tid}}
    print(f"== thread {tid} ==")
    out = graph.invoke({"request": "customers"}, cfg)
    intr = out["__interrupt__"][0]
    print("  paused, interrupt payload:", intr.value)
    snap = graph.get_state(cfg)
    print("  checkpoint next node:", snap.next)
    print("  -> human resumes with", resume)
    out = graph.invoke(Command(resume=resume), cfg)
    print("  final:", {k: out[k] for k in ("decision", "result")})

demo_invalid_resume()
