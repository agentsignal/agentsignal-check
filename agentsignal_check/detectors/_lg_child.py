import json, os, sys, sqlite3
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

mode, graph_spec, effects, db, inp, resume = sys.argv[1], sys.argv[2], json.loads(sys.argv[3]), sys.argv[4], json.loads(sys.argv[5]), json.loads(sys.argv[6])
LEDGER, CRASH_AT = os.environ["AS_LEDGER"], int(os.environ.get("AS_CRASH_AT", "0"))
from _effects import install, load   # sibling module (this script's dir is sys.path[0]); awaits async effects

sys.path.insert(0, os.getcwd())
install(effects, LEDGER, CRASH_AT)
gmod, gname = load(graph_spec)
PUT_CRASH_AT = int(os.environ.get("AS_PUT_CRASH_AT", "0"))
PUT_LOG = os.environ.get("AS_PUT_LOG")
puts = 0

class CrashSaver(SqliteSaver):
    """Counts put() calls; optionally hard-kills BEFORE the Nth checkpoint is written
    (pending writes from put_writes are already persisted) -- LangGraph #8039 window."""
    def put(self, *a, **k):
        global puts
        puts += 1
        if PUT_LOG:
            with open(PUT_LOG, "a") as fh: fh.write("put\n")
        if PUT_CRASH_AT and puts == PUT_CRASH_AT:
            os._exit(137)
        return super().put(*a, **k)

conn = sqlite3.connect(db, check_same_thread=False)
g = getattr(gmod, gname)(CrashSaver(conn))
cfg = {"configurable": {"thread_id": "agentsignal-check"}}

MULTI = os.environ.get("AS_MULTI_RESUME", "each")   # several pending interrupts: "each" (one at a time) | "map" (all at once)

def pending():
    """Pending interrupts across the graph and its subgraphs, de-duplicated by interrupt id."""
    st = g.get_state(cfg, subgraphs=True)
    seen = {}
    for i in list(st.interrupts) + [i for t in st.tasks for i in (getattr(t.state, "interrupts", None) or ())]:
        seen.setdefault(getattr(i, "id", None) or repr(i.value), i)
    return list(seen.values())

def resume_cmd():
    """One pending interrupt: Command(resume=value). Several: LangGraph needs {interrupt_id: value}."""
    ints = pending()
    if MULTI == "scalar":                     # v0.3.2 behaviour (regression tests only): LangGraph rejects it
        return Command(resume=resume)         # when several interrupts are pending
    if len(ints) <= 1 or not all(getattr(i, "id", None) for i in ints):
        return Command(resume=resume)
    if MULTI == "map":
        return Command(resume={i.id: resume for i in ints})
    return Command(resume={ints[0].id: resume})          # answer them one at a time, like an approval UI

def drive(first):
    x = first
    for _ in range(50):
        g.invoke(x, cfg)
        if not g.get_state(cfg).next: return
        x = resume_cmd()
    raise SystemExit("too many interrupts")

if mode == "full":
    drive(inp)
elif mode == "resume":
    st = g.get_state(cfg)
    # Unfinished = there is a next node OR the latest checkpoint still has tasks. Under LangGraph's default
    # async durability a hard kill can land after a task's pending writes were saved but before the next
    # checkpoint was: get_state() then reports next=() although the run never finished (the following node,
    # e.g. the charge, never committed). invoke(None) applies those writes and continues, like any app
    # resuming the thread. Checking `next` alone skipped the resume -> a random false PASS (board 54 X3).
    if st.next or st.tasks:
        drive(resume_cmd() if pending() else None)
elif mode == "replay":
    g.invoke(inp, cfg)
    had = bool(g.get_state(cfg).interrupts)
    conn.close()
    conn = sqlite3.connect(db, check_same_thread=False)  # simulate restart
    g = getattr(gmod, gname)(CrashSaver(conn))
    pending_after = bool(g.get_state(cfg).interrupts)
    if had:
        snap = g.get_state(cfg).config
        cmd = resume_cmd()
        g.invoke(cmd, cfg)
        g.invoke(cmd, snap)  # duplicate delivery of the same approval
    print(json.dumps({"had_interrupt": had, "pending_after_restart": pending_after}))
