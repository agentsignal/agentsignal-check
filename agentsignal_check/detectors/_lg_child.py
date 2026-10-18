import functools, importlib, json, os, sys, sqlite3
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

mode, graph_spec, effects, db, inp, resume = sys.argv[1], sys.argv[2], json.loads(sys.argv[3]), sys.argv[4], json.loads(sys.argv[5]), json.loads(sys.argv[6])
LEDGER, CRASH_AT = os.environ["AS_LEDGER"], int(os.environ.get("AS_CRASH_AT", "0"))
calls = 0

def load(spec):
    m, f = spec.split(":"); return importlib.import_module(m), f

def wrap(spec):
    mod, name = load(spec); orig = getattr(mod, name)
    @functools.wraps(orig)
    def w(*a, **k):
        global calls
        try:
            res = orig(*a, **k)
        except Exception as e:
            if any(c.__name__ == "EffectInFlight" for c in type(e).__mro__):   # matched by name: no import dependency
                with open(LEDGER + ".inflight", "a") as fh:
                    fh.write(json.dumps([spec, repr(a), repr(sorted(k.items()))]) + "\n"); fh.flush(); os.fsync(fh.fileno())
            raise
        with open(LEDGER, "a") as fh:
            fh.write(json.dumps([spec, repr(a), repr(sorted(k.items())), repr(res)]) + "\n"); fh.flush(); os.fsync(fh.fileno())
        calls += 1
        if CRASH_AT and calls == CRASH_AT:
            os._exit(137)  # hard kill: no finally, no checkpoint flush
        return res
    setattr(mod, name, w)

sys.path.insert(0, os.getcwd())
for e in effects: wrap(e)
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
    drive(resume_cmd() if pending() else None) if st.next else None
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
