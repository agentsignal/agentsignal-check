from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
import mailer


class S(TypedDict, total=False):
    to: str
    approved: bool


def approval(s):
    return {"approved": interrupt({"q": "send?"})}


def send_receipt(s):
    mailer.send_email(s["to"], "receipt")
    return {}


def build(cp):
    g = StateGraph(S)
    g.add_node("approval", approval)
    g.add_node("send_receipt", send_receipt)
    g.add_edge(START, "approval")
    g.add_edge("approval", "send_receipt")
    g.add_edge("send_receipt", END)
    return g.compile(checkpointer=cp)
