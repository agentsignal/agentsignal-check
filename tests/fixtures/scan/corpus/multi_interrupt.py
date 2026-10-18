"""Multiple interrupts in ONE node, plus parallel interrupts resumed by interrupt id. No LLM; all LangGraph real."""
import operator
from typing import TypedDict, Annotated
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import interrupt, Command

class S(TypedDict, total=False):
    answers: Annotated[list, operator.add]

calls = {"n": 0}
def review(s):
    calls["n"] += 1
    print(f"  [review] execution #{calls['n']}")
    a = interrupt({"step": 1, "q": "Approve deleting 1200 rows?"})
    b = interrupt({"step": 2, "q": "Also notify the customer?"})
    c = interrupt({"step": 3, "q": "Reason for audit log?"})
    return {"answers": [a, b, c]}

def part1():
    print("== Part 1: three sequential interrupt() calls in one node ==")
    b = StateGraph(S); b.add_node("review", review); b.add_edge(START, "review"); b.add_edge("review", END)
    g = b.compile(checkpointer=InMemorySaver()); cfg = {"configurable": {"thread_id": "seq"}}
    out = g.invoke({"answers": []}, cfg)
    for resume in [True, False, "cleanup ticket #881"]:
        print("  paused on:", out["__interrupt__"][0].value, "-> resume", repr(resume))
        out = g.invoke(Command(resume=resume), cfg)
    print("  final:", out)

def part2():
    print("== Part 2: two parallel nodes each interrupt; resume both at once keyed by interrupt id ==")
    def mk(name):
        def node(s):
            return {"answers": [(name, interrupt({"node": name, "q": f"approve {name}?"}))]}
        return node
    b = StateGraph(S)
    for n in ("tool_a", "tool_b"):
        b.add_node(n, mk(n)); b.add_edge(START, n); b.add_edge(n, END)
    g = b.compile(checkpointer=InMemorySaver()); cfg = {"configurable": {"thread_id": "par"}}
    out = g.invoke({"answers": []}, cfg)
    intrs = out["__interrupt__"]
    print("  pending:", [(i.id[:8], i.value["node"]) for i in intrs])
    decisions = {i.id: (i.value["node"] == "tool_a") for i in intrs}  # approve a, reject b
    out = g.invoke(Command(resume=decisions), cfg)
    print("  final:", sorted(out["answers"]))

part1(); calls["n"] = 0; part2()
