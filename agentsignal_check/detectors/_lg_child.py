import json, os, sys, sqlite3, threading
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

mode, graph_spec, effects, db, inp, resume = sys.argv[1], sys.argv[2], json.loads(sys.argv[3]), sys.argv[4], json.loads(sys.argv[5]), json.loads(sys.argv[6])
LEDGER, CRASH_AT = os.environ["AS_LEDGER"], int(os.environ.get("AS_CRASH_AT", "0"))
# Run as a script, sys.path[0] is this file's directory. Drop it so the user's own modules (an `_effects.py`,
# `card.py`, ...) are never shadowed by ours, and import the helper by its full package name (board 53 F2).
_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _here]
sys.path.insert(0, os.getcwd())
try:
    from agentsignal_check.detectors._effects import install, load   # awaits async effects
except ImportError:          # running from a source tree that isn't installed: add the package root only for this import
    sys.path.insert(0, os.path.dirname(os.path.dirname(_here)))
    from agentsignal_check.detectors._effects import install, load
    del sys.path[0]

install(effects, LEDGER, CRASH_AT)
gmod, gname = load(graph_spec)
PUT_CRASH_AT = int(os.environ.get("AS_PUT_CRASH_AT", "0"))
PUT_LOG = os.environ.get("AS_PUT_LOG")
puts = 0
_lock = threading.Condition()
_writing = 0            # put_writes calls in progress (kept ones only)
_lost = {"id": None}    # checkpoint id of the put() that was "lost"


def _die():
    """Hard kill once every kept pending write has landed (they were submitted before the lost checkpoint)."""
    with _lock:
        _lock.wait_for(lambda: _writing == 0, timeout=10)
    os._exit(137)


class CrashSaver(SqliteSaver):
    """Counts put() calls. With AS_PUT_CRASH_AT=k it replays LangGraph's async-durability window
    (#8039) deterministically (board 53 F1):

    1. the pending writes of the tasks that ran before checkpoint #k are saved (put_writes always lands);
    2. checkpoint #k itself is never saved (put() returns as if it had been written);
    3. the next superstep runs anyway, as it does while an async checkpoint write is still in flight, and
       its own pending writes (attached to the unsaved checkpoint) are lost too;
    4. the process is hard-killed (os._exit(137)) at the next put(), or when the run pauses/ends.

    The child runs with durability="sync" in this mode, so exactly one superstep runs ahead of the lost
    checkpoint. Under the default async durability how far the run got before the kill was a thread race,
    which made naive graphs pass about 1 run in 5."""

    def put(self, config, checkpoint, metadata, new_versions):
        global puts
        with _lock:
            puts += 1
            n = puts
        if PUT_LOG:
            with open(PUT_LOG, "a") as fh: fh.write("put\n")
        if PUT_CRASH_AT:
            if _lost["id"] is not None:
                _die()
            if n == PUT_CRASH_AT:
                _lost["id"] = checkpoint["id"]
                return {"configurable": {**config["configurable"],
                                         "checkpoint_ns": config["configurable"].get("checkpoint_ns", ""),
                                         "checkpoint_id": checkpoint["id"]}}
        return super().put(config, checkpoint, metadata, new_versions)

    def put_writes(self, config, writes, task_id, task_path=""):
        global _writing
        if _lost["id"] is not None and config["configurable"].get("checkpoint_id") == _lost["id"]:
            return      # writes of the step that ran ahead of the unsaved checkpoint: lost with it
        with _lock:
            _writing += 1
        try:
            return super().put_writes(config, writes, task_id, task_path)
        finally:
            with _lock:
                _writing -= 1
                _lock.notify_all()

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

RUN_KW = {"durability": "sync"} if PUT_CRASH_AT else {}

def drive(first):
    x = first
    for _ in range(50):
        g.invoke(x, cfg, **RUN_KW)
        if _lost["id"] is not None:    # paused or finished right after the lost checkpoint: die now
            _die()
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
