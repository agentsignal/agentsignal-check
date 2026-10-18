"""Kill-and-resume detector for LangGraph.

For each side-effect call N (1..K seen in a clean run), run the graph in a
child process with a SQLite checkpointer, hard-kill it (os._exit) right after
effect #N returns, then resume the same thread in a fresh process until done,
auto-approving every interrupt. Any effect call recorded more than once with
identical arguments across the crash => DOUBLE_EXECUTION.
Also: resume the same pending interrupt twice (double-click / retry) and check
for duplicates (APPROVAL_REPLAY), and check a pending approval survives a
process restart (LOST_APPROVAL).
"""
import json, os, subprocess, sys, tempfile, collections

CHILD = os.path.join(os.path.dirname(__file__), "_lg_child.py")

MULTI = {"mode": "each"}

def _child(mode, graph, effects, db, ledger, inp, resume, crash_at=0, put_crash_at=0, put_log=""):
    env = dict(os.environ, AS_LEDGER=ledger, AS_CRASH_AT=str(crash_at), AS_SCENARIO=db,
               AS_PUT_CRASH_AT=str(put_crash_at), AS_PUT_LOG=put_log, AS_MULTI_RESUME=MULTI["mode"])  # AS_SCENARIO: per-scenario scratch key for user stores
    cmd = [sys.executable, CHILD, mode, graph, json.dumps(effects), db, json.dumps(inp), json.dumps(resume)]
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120)

def _dups(ledger):
    if not os.path.exists(ledger):
        return {}, 0
    rows = [json.loads(l) for l in open(ledger) if l.strip()]
    # same call args producing DISTINCT results = the effect really happened twice.
    # same args + same result = deduped retry (e.g. provider idempotency key) -> fine.
    res = collections.defaultdict(set)
    for spec, a, k, r in rows:
        res[f"{spec}{a}{k}"].add(r)
    return {k: len(v) for k, v in res.items() if len(v) > 1}, len(rows)

INFLIGHT_NOTE = "in flight; retried after lease with the same provider key"

def _inflight(ledger):
    """The watched effect raised EffectInFlight (a ledger lease held by the killed attempt) during this resume."""
    p = ledger + ".inflight"
    return os.path.exists(p) and os.path.getsize(p) > 0

def run(graph, effects, inp, resume, multi="each"):
    """multi: how several pending interrupts are answered -- "each" (one at a time, by interrupt id) or "map"."""
    if multi not in ("each", "map"):
        raise ValueError("multi must be 'each' or 'map'")
    MULTI["mode"] = multi
    results = []
    with tempfile.TemporaryDirectory() as d:
        # baseline
        db, led = f"{d}/base.db", f"{d}/base.ledger"
        plog = f"{d}/base.puts"
        r = _child("full", graph, effects, db, led, inp, resume, put_log=plog)
        if r.returncode != 0:
            return {"results": [{"detector": "baseline", "status": "ERROR", "detail": r.stderr[-800:]}]}
        dups, n = _dups(led)
        results.append({"detector": "baseline", "status": "FAIL" if dups else "PASS",
                        "detail": f"{n} effect call(s) in a clean run" + (f"; duplicates {dups}" if dups else "")})
        # kill after each effect, then resume
        worst, notes = [], []
        for k in range(1, n + 1):
            db, led = f"{d}/c{k}.db", f"{d}/c{k}.ledger"
            r1 = _child("full", graph, effects, db, led, inp, resume, crash_at=k)
            if r1.returncode != 137:
                worst.append(f"crash@{k}: child did not crash (rc={r1.returncode})")
                continue
            r2 = _child("resume", graph, effects, db, led, inp, resume)
            dups, _ = _dups(led)
            if dups:
                worst.append(f"crash after effect #{k} -> re-executed {dups}")
            elif _inflight(led):          # clean refusal: no duplicate observed
                notes.append(f"crash@{k}: {INFLIGHT_NOTE}")
            elif r2.returncode != 0:
                worst.append(f"crash@{k}: resume failed: {r2.stderr.strip().splitlines()[-1:]}")
        results.append({"detector": "double_execution.kill_resume",
                        "status": "FAIL" if any("re-executed" in w for w in worst) else ("PASS" if not worst else "WARN"),
                        "detail": "; ".join(worst + notes) or f"no duplicates across {n} crash point(s)"})
        # kill BEFORE each checkpoint write (LangGraph #8039: put_writes vs put ordering)
        nput = sum(1 for _ in open(plog)) if os.path.exists(plog) else 0
        bad, warn, pnotes = [], [], []
        for k in range(1, nput + 1):
            db, led = f"{d}/p{k}.db", f"{d}/p{k}.ledger"
            r1 = _child("full", graph, effects, db, led, inp, resume, put_crash_at=k)
            if r1.returncode != 137:
                continue  # run finished before reaching put #k (e.g. paused) -- nothing to test
            r2 = _child("resume", graph, effects, db, led, inp, resume)
            dups, _ = _dups(led)
            if dups:
                bad.append(f"crash before checkpoint #{k} -> re-executed {dups}")
            elif _inflight(led):          # clean refusal: no duplicate observed
                pnotes.append(f"put#{k}: {INFLIGHT_NOTE}")
            elif r2.returncode != 0:
                warn.append(f"put#{k}: resume failed: {r2.stderr.strip().splitlines()[-1:]}")
        results.append({"detector": "double_execution.kill_before_checkpoint",
                        "status": "FAIL" if bad else ("WARN" if warn else "PASS"),
                        "detail": "; ".join(bad + warn + pnotes) or f"no duplicates across {nput} checkpoint write(s)"})
        # approval replay + lost approval
        db, led = f"{d}/rep.db", f"{d}/rep.ledger"
        r = _child("replay", graph, effects, db, led, inp, resume)
        out = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        if r.returncode != 0:
            results.append({"detector": "approval.replay", "status": "ERROR", "detail": r.stderr[-400:]})
        else:
            info = json.loads(out)
            dups, _ = _dups(led)
            if not info["had_interrupt"]:
                results.append({"detector": "approval.lost_on_restart", "status": "SKIP", "detail": "graph never interrupted"})
            else:
                results.append({"detector": "approval.lost_on_restart",
                                "status": "PASS" if info["pending_after_restart"] else "FAIL",
                                "detail": "pending interrupt visible from a fresh process" if info["pending_after_restart"]
                                else "pending approval not found after process restart"})
                results.append({"detector": "approval.replay", "status": "FAIL" if dups else "PASS",
                                "detail": f"same approval delivered twice -> duplicates {dups}" if dups else "second resume caused no duplicate effects"})
    return {"framework": "langgraph", "results": results}
