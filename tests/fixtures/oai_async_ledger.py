"""OpenAI Agents SDK: approval-gated ASYNC tool charging through an async leased ledger (board 54 oai_fixed shape)."""
from agents import Agent, function_tool
from agents.tool_context import ToolContext
from tests.fixtures import async_ledger
from tests.fixtures.oai_model import ScriptedModel

@function_tool(needs_approval=True)
async def charge(ctx: ToolContext[dict], amount: int) -> str:
    """Charge the customer."""
    return await async_ledger.acharge(amount, order_id=(ctx.context or {}).get("order_id", "order-42"))

def build():
    return Agent(name="Billing", instructions="bill", model=ScriptedModel([("charge", {"amount": 1999})]), tools=[charge])
