from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
import mailer


def approval(s):
    return {"approved": interrupt({"q": "send?"})}


def send_receipt(s):
    if s.get("receipt_id"):
        return {}
    return {"receipt_id": mailer.send_email(s["to"], "receipt")}


def send_ledgered(s, ledger):
    if ledger.lookup(s["thread"]):
        return {}
    return {"r": mailer.send_email(s["to"], "receipt")}


def build(cp):
    g = StateGraph(dict)
    g.add_node("approval", approval)
    g.add_node("send_receipt", send_receipt)
    g.add_node("send_ledgered", send_ledgered)
    g.add_edge(START, "approval")
    g.add_edge("approval", "send_receipt")
    g.add_edge("send_receipt", "send_ledgered")
    g.add_edge("send_ledgered", END)
    return g.compile(checkpointer=cp)
