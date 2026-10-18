"""Importing this file fails loudly: proves the scan never imports user code."""
from langgraph.types import interrupt
import payments

raise RuntimeError("agentsignal scan imported user code")


def pay(s):
    payments.charge(s["amount"])
    return interrupt("ok?")
