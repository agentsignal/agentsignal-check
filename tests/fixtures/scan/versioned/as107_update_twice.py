from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command


def review(s):
    a = interrupt({"step": 1})
    b = interrupt({"step": 2})
    return {"answers": [a, b]}


def build(cp):
    g = StateGraph(dict); g.add_node("review", review); g.add_edge(START, "review"); g.add_edge("review", END)
    return g.compile(checkpointer=cp)


def answer(graph, cfg):
    return graph.invoke(Command(resume=True, update={"note": "approved"}), cfg)
