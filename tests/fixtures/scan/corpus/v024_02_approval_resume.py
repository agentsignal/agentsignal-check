"""Human-in-the-loop: needs_approval -> interruption -> serialize RunState -> restore -> approve/reject -> resume."""
import asyncio
from agents import Agent, Runner, RunConfig, RunState, function_tool
from fake_model import ScriptedModel

@function_tool(needs_approval=True)
def delete_file(path: str) -> str:
    """Delete a file."""
    return f"deleted {path}"

@function_tool(needs_approval=True)
def send_email(to: str) -> str:
    """Send an email."""
    return f"emailed {to}"

def make_agent():
    return Agent(name="Ops", instructions="ops",
                 model=ScriptedModel([("delete_file", {"path": "/tmp/x"}), ("send_email", {"to": "a@b.c"})]),
                 tools=[delete_file, send_email])

async def main():
    cfg = RunConfig(tracing_disabled=True)
    agent = make_agent()
    r = await Runner.run(agent, "clean up and notify", run_config=cfg)
    print("interruptions:", [(i.tool_name, i.arguments) for i in r.interruptions])
    saved = r.to_state().to_string()          # durable checkpoint (e.g. DB)
    print(f"serialized state: {len(saved)} chars")

    # ... later / another process: restore with same agent definition
    agent = make_agent()
    state = await RunState.from_string(agent, saved)
    for item in state.get_interruptions():
        if item.tool_name == "delete_file":
            state.approve(item)
        else:
            state.reject(item, rejection_message="Human declined sending email.")
    r2 = await Runner.run(agent, state, run_config=cfg)
    print("interruptions after resume:", r2.interruptions)
    print("final:", r2.final_output)

asyncio.run(main())
