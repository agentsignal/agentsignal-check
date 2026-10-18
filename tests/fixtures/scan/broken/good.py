from langgraph.types import interrupt
import payments


def pay(s):
    payments.charge(s["amount"])
    return interrupt("ok?")
