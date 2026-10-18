"""v0.24.0: run-wide RunConfig.tool_error_formatter now receives kind="tool_exception" + error."""
import asyncio
from agents import Agent, Runner, RunConfig, function_tool, ToolErrorFormatterArgs
from fake_model import ScriptedModel

class PaymentGatewayDown(Exception): pass

@function_tool
def charge_card(amount: int) -> str:
    """Charge a card.

    Args:
        amount: amount in cents
    """
    raise PaymentGatewayDown("upstream 503 token=sk-SECRET")   # sensitive detail

@function_tool
def lookup_price(sku: str) -> str:
    """Look up a price."""
    return f"{sku} costs 1999"

def formatter(args: ToolErrorFormatterArgs):
    print(f"  [formatter] kind={args.kind} tool={args.tool_name} error={type(args.error).__name__ if args.error else None}")
    if args.kind == "tool_exception":
        # sanitize: never forward raw exception text (may contain secrets)
        return f"Tool '{args.tool_name}' failed ({type(args.error).__name__}). Try again later."
    return None  # keep SDK default for approval_rejected / tool_not_found

async def main():
    agent = Agent(name="Shop", instructions="help",
                  model=ScriptedModel([("lookup_price", {"sku": "A1"}), ("charge_card", {"amount": 1999})]),
                  tools=[lookup_price, charge_card])
    print("== with run-wide formatter ==")
    r = await Runner.run(agent, "buy A1", run_config=RunConfig(tool_error_formatter=formatter, tracing_disabled=True))
    print("final:", r.final_output)
    agent2 = agent.clone(model=ScriptedModel([("charge_card", {"amount": 1})]))
    print("== without formatter (SDK default) ==")
    r = await Runner.run(agent2, "buy", run_config=RunConfig(tracing_disabled=True))
    print("final:", r.final_output)

asyncio.run(main())
