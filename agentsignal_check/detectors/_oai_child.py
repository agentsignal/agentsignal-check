"""Child process for the OpenAI Agents SDK detector. State crosses process boundaries ONLY as
RunState.to_json() written to a file, exactly like an app persisting a paused run."""
import asyncio, inspect, json, os, sys
mode, agent_spec, effects, state_path, ctx = sys.argv[1], sys.argv[2], json.loads(sys.argv[3]), sys.argv[4], json.loads(sys.argv[5])
LEDGER, CRASH_AT = os.environ["AS_LEDGER"], int(os.environ.get("AS_CRASH_AT", "0"))
from _effects import install, load   # sibling module (this script's dir is sys.path[0]); awaits async effects

sys.path.insert(0, os.getcwd())
install(effects, LEDGER, CRASH_AT)
from agents import Runner, RunConfig, RunState
amod, aname = load(agent_spec)
agent = getattr(amod, aname)()
CFG = RunConfig(tracing_disabled=True)

async def _await(x):
    return await x if inspect.isawaitable(x) else x

def save(st):
    tmp = state_path + ".tmp"; json.dump(st.to_json(), open(tmp, "w")); os.replace(tmp, state_path)

async def load_state():
    return await _await(RunState.from_json(agent, json.load(open(state_path)), context_override=ctx))

async def drive(st, approve=True):
    for _ in range(50):
        r = await Runner.run(agent, st, run_config=CFG)
        if not r.interruptions:
            return r
        st = r.to_state(); save(st)
        if not approve: return r
        for i in r.interruptions: st.approve(i)
    raise SystemExit("too many interrupts")

async def main():
    if mode == "full":          # clean run, approve everything
        r = await Runner.run(agent, "run", context=ctx, run_config=CFG)
        st = r.to_state()
        for i in r.interruptions: st.approve(i)
        if r.interruptions: await drive(st)
    elif mode == "start":       # run until the first approval pause, persist, exit
        r = await Runner.run(agent, "run", context=ctx, run_config=CFG)
        save(r.to_state()); print(json.dumps({"interruptions": len(r.interruptions)}))
    elif mode == "approve_save":  # approve in this process, persist the approved state, exit before running
        st = await load_state()
        for i in st.get_interruptions(): st.approve(i)
        save(st)
    elif mode == "resume":      # fresh process: load persisted state, approve whatever is pending, run to completion
        st = await load_state()
        for i in st.get_interruptions(): st.approve(i)
        await drive(st)
    elif mode == "resume_noapprove":  # fresh process: load approved state and run WITHOUT re-approving
        st = await load_state()
        pending = len(st.get_interruptions())
        r = await Runner.run(agent, st, run_config=CFG)
        print(json.dumps({"pending_on_load": pending, "reinterrupted": len(r.interruptions)}))
    elif mode == "inspect":
        st = await load_state(); print(json.dumps({"pending": len(st.get_interruptions())}))
asyncio.run(main())
