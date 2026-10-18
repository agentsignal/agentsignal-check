from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt

TOOLS = ["a", "b", "c"]


def mk(name):
    def node(s):
        return {"answers": [(name, interrupt({"tool": name}))]}
    return node


def build(cp):
    g = StateGraph(dict)
    for n in TOOLS:
        g.add_node(n, mk(n)); g.add_edge(START, n); g.add_edge(n, END)
    return g.compile(checkpointer=cp)


def start(graph, cfg):
    return graph.invoke({"answers": []}, cfg, durability="exit")
