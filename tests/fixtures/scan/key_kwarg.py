import hashlib
from agents import function_tool, RunContextWrapper
from agents.tool_context import ToolContext
import payments


@function_tool(needs_approval=True)
def charge_ctx_key(ctx: ToolContext[dict], amount: int) -> str:
    key = f"{ctx.context['order_id']}:{ctx.tool_name}"
    return payments.charge(amount, key=key)


@function_tool(needs_approval=True)
def charge_call_key(ctx: ToolContext[dict], amount: int) -> str:
    return payments.charge(amount, key=ctx.tool_call_id)


@function_tool(needs_approval=True)
def charge_hashed_key(ctx: ToolContext[dict], amount: int) -> str:
    key = hashlib.sha256(ctx.context["run_id"].encode()).hexdigest()
    return payments.charge(amount, key=key)


@function_tool(needs_approval=True)
def charge_constant_key(ctx: RunContextWrapper[dict], amount: int) -> str:
    return payments.charge(amount, key=amount)


@function_tool(needs_approval=True)
def charge_none_key(ctx: RunContextWrapper[dict], amount: int) -> str:
    return payments.charge(amount, key=None)
