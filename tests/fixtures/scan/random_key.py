from uuid import uuid4
from agents import function_tool, RunContextWrapper
import payments


@function_tool(needs_approval=True)
def charge(ctx: RunContextWrapper[dict], amount: int) -> str:
    """Charge with a key that changes on every call (does not dedupe)."""
    return payments.charge(amount, idempotency_key=str(uuid4()))
