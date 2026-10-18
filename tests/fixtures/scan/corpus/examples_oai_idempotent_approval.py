"""OpenAI Agents SDK 0.24.0: keep an approved side effect from running twice.
Real failure modes shown (the SDK itself does NOT re-run an approved tool on a single resume):
  A) duplicate resume: the same saved RunState gets resumed twice (double-click, worker retry, queue redelivery)
  B) whole-run retry: the run crashes AFTER the tool ran (here: the model call fails), app retries from scratch
NAIVE tool: charges every time it's invoked.  FIXED tool: idempotency key from stable run data
(order_id from the run context + tool name), deduped by the (fake) payment API.
MOCK: only the model (fake_model.ScriptedModel, a real agents.Model subclass) and the payment API."""
import asyncio
from dataclasses import dataclass
from agents import Agent, Runner, RunConfig, RunState, RunContextWrapper, function_tool
from agents.tool_context import ToolContext
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))  # shared ../fake_model.py
from fake_model import ScriptedModel

class FakePaymentAPI:
    def __init__(self): self.charges = []; self.by_key = {}
    def charge(self, amount, key=None):
        if key and key in self.by_key: return self.by_key[key]
        cid = f"ch_{len(self.charges)+1}"; self.charges.append((cid, amount))
        if key: self.by_key[key] = cid
        return cid

@dataclass
class Ctx:
    order_id: str
    api: FakePaymentAPI

@function_tool(needs_approval=True)
def charge_naive(ctx: RunContextWrapper[Ctx], amount: int) -> str:
    """Charge the customer."""
    return ctx.context.api.charge(amount)

@function_tool(needs_approval=True)
def charge_fixed(ctx: ToolContext[Ctx], amount: int) -> str:
    """Charge the customer."""
    key = f"{ctx.context.order_id}:{ctx.tool_name}"   # stable across resumes AND fresh retries
    # (ctx.tool_call_id is stable across resumes of one RunState, but a re-generated model call gets a new id)
    return ctx.context.api.charge(amount, key=key)

class FlakyAfterTool(ScriptedModel):
    """Fails on the model call that follows the tool output once (simulates a crash after the side effect)."""
    failed = False
    async def get_response(self, *a, **kw):
        inp = a[1] if len(a) > 1 else kw.get("input")
        if not FlakyAfterTool.failed and any(isinstance(i, dict) and i.get("type") == "function_call_output" for i in (inp or [])):
            FlakyAfterTool.failed = True
            raise ConnectionError("model API 503 (simulated)")
        return await super().get_response(*a, **kw)

CFG = RunConfig(tracing_disabled=True)
def agent(tool, model_cls=ScriptedModel):
    return Agent(name="Billing", instructions="bill", model=model_cls([(tool.name, {"amount": 1999})]), tools=[tool])

async def approve_and_run(a, saved, ctx):
    st = await RunState.from_string(a, saved, context_override=ctx)
    for it in st.get_interruptions(): st.approve(it)
    return await Runner.run(a, st, run_config=CFG)

async def scenario_duplicate_resume(tool):
    api = FakePaymentAPI(); ctx = Ctx("order-42", api); a = agent(tool)
    r = await Runner.run(a, "charge order 42", context=ctx, run_config=CFG)
    saved = r.to_state().to_string()
    print(f"  paused: {[i.tool_name for i in r.interruptions]}, charges={len(api.charges)}")
    await approve_and_run(a, saved, ctx)
    await approve_and_run(a, saved, ctx)    # second worker / double-click resumes the same snapshot
    print(f"  after 2 resumes of same snapshot: charges={api.charges}")

async def scenario_retry(tool):
    api = FakePaymentAPI(); ctx = Ctx("order-42", api); FlakyAfterTool.failed = False
    for attempt in (1, 2):
        a = agent(tool, FlakyAfterTool)
        r = await Runner.run(a, "charge order 42", context=ctx, run_config=CFG)
        try:
            st = r.to_state(); [st.approve(i) for i in r.interruptions]
            out = await Runner.run(a, st, run_config=CFG)
            print(f"  attempt {attempt}: ok -> {out.final_output}")
        except ConnectionError as e:
            print(f"  attempt {attempt}: failed after tool ran ({e}); charges so far={len(api.charges)}; retrying from scratch")
    print(f"  final charges={api.charges}")

async def main():
    for label, tool in [("NAIVE", charge_naive), ("FIXED", charge_fixed)]:
        print(f"== {label} / A) duplicate resume =="); await scenario_duplicate_resume(tool)
        print(f"== {label} / B) retry after crash =="); await scenario_retry(tool)
asyncio.run(main())
