"""OpenAI Agents SDK 0.24.0: human approval that survives restarts and duplicate resumes.

Flow: needs_approval tool -> run pauses with an interruption -> RunState.to_string() is saved
-> any process restores it with RunState.from_string() -> approve/reject -> Runner.run(state).
The side effect uses an idempotency key (order id + tool name), so two workers resuming the
same saved run charge once. MOCK: only the model (fake_model.ScriptedModel).

CLI (used by the crash tests):
  python approval.py start  --state run.json --ledger effects.db
  python approval.py resume --state run.json --ledger effects.db --decision approve|reject [--worker w1] [--naive]
"""
import argparse, asyncio, json
from dataclasses import dataclass
from agents import Agent, Runner, RunConfig, RunState, function_tool
from agents.tool_context import ToolContext
from fake_model import ScriptedModel
import ledger

CFG = RunConfig(tracing_disabled=True)

@dataclass
class Ctx:
    order_id: str
    ledger: str
    worker: str = "main"
    idempotent: bool = True

@function_tool(needs_approval=True)
def charge(ctx: ToolContext[Ctx], amount: int) -> str:
    """Charge the customer."""
    c = ctx.context
    key = f"{c.order_id}:{ctx.tool_name}" if c.idempotent else None
    ran = ledger.perform(c.ledger, f"charge {amount}", key=key, worker=c.worker)
    return f"charged {amount}" if ran else f"already charged {amount}"

def make_agent():
    return Agent(name="Billing", instructions="bill",
                 model=ScriptedModel([("charge", {"amount": 1999})]), tools=[charge])

async def start(ctx):
    r = await Runner.run(make_agent(), "charge order 42", context=ctx, run_config=CFG)
    return r, r.to_state().to_string()

async def resume(saved, ctx, decision):
    agent = make_agent()                              # same definition, fresh objects
    state = await RunState.from_string(agent, saved, context_override=ctx)
    for item in state.get_interruptions():
        if decision == "approve":
            state.approve(item)
        else:
            state.reject(item, rejection_message="Human declined the charge.")
    return await Runner.run(agent, state, run_config=CFG)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["start", "resume"])
    p.add_argument("--state", required=True); p.add_argument("--ledger", required=True)
    p.add_argument("--decision", default="approve"); p.add_argument("--worker", default="main")
    p.add_argument("--naive", action="store_true")
    a = p.parse_args()
    ctx = Ctx("order-42", a.ledger, a.worker, not a.naive)
    if a.cmd == "start":
        r, saved = asyncio.run(start(ctx))
        open(a.state, "w").write(saved)
        print(json.dumps({"interruptions": [i.tool_name for i in r.interruptions]}))
    else:
        r = asyncio.run(resume(open(a.state).read(), ctx, a.decision))
        print(json.dumps({"interruptions": len(r.interruptions), "final": r.final_output}))

if __name__ == "__main__":
    main()
