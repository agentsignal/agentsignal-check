from langgraph.types import interrupt
import payments, ledgerless
from agents import function_tool


def pay(s):
    payments.charge(s["amount"])
    return interrupt("ok?")


@function_tool(needs_approval=True)
def record(amount: int) -> str:
    return ledgerless.record_sale(amount)
