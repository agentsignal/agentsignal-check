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
import json, os, signal, subprocess, sys, tempfile, collections

CHILD = os.path.join(os.path.dirname(__file__), "_lg_child.py")

MULTI = {"mode": "each"}

def child_timeout():
    """Seconds one child process may run (AGENTSIGNAL_CHILD_TIMEOUT_S, default 120)."""
    try:
        return float(os.environ.get("AGENTSIGNAL_CHILD_TIMEOUT_S", "") or 120)
    except ValueError:
        return 120.0


def run_child(cmd, env):
    """subprocess.run that never raises on a hung child: a timeout becomes a failed result (rc 124) whose
    stderr ends with a TimeoutError line, so every detector reports it (ERROR/WARN) and the CLI exits non-zero."""
    t = child_timeout()
    try:
        return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=t)
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        err = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        return subprocess.CompletedProcess(cmd, 124, out, err + f"\nTimeoutError: child process timed out after {t:g}s")


def last_json(r):
    """The last stdout line that parses as JSON (user code may print), else None."""
    for line in reversed((r.stdout or "").strip().splitlines()):
        try:
            return json.loads(line)
        except ValueError:
            continue
    return None


def exit_desc(rc):
    """'rc=3', or 'rc=-11 SIGSEGV' for a child killed by a signal."""
    if rc is not None and rc < 0:
        try:
            return f"rc={rc} {signal.Signals(-rc).name}"
        except ValueError:
            return f"rc={rc} signal {-rc}"
    return f"rc={rc}"


def err_tail(r, n=300):
    """Last non-empty stderr line, or what happened when the child printed nothing (exit code / signal)."""
    lines = [l for l in (r.stderr or "").strip().splitlines() if l.strip()]
    if not lines:
        how = "killed by a signal" if (r.returncode or 0) < 0 else "exited"
        return f"child {how} ({exit_desc(r.returncode)}) with no error output"
    return lines[-1][-n:]


def failed(r, what):
    """'<what> (rc=..): <last error line>' -- never an empty detail (board 53 nit)."""
    return f"{what} ({exit_desc(r.returncode)}): {err_tail(r)}"


def _child(mode, graph, effects, db, ledger, inp, resume, crash_at=0, put_crash_at=0, put_log=""):
    env = dict(os.environ, AS_LEDGER=ledger, AS_CRASH_AT=str(crash_at), AS_SCENARIO=db,
               AS_PUT_CRASH_AT=str(put_crash_at), AS_PUT_LOG=put_log, AS_MULTI_RESUME=MULTI["mode"])  # AS_SCENARIO: per-scenario scratch key for user stores
    cmd = [sys.executable, CHILD, mode, graph, json.dumps(effects), db, json.dumps(inp), json.dumps(resume)]
    return run_child(cmd, env)

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

def kill_before_checkpoint(graph, effects, inp, resume, nput, d, tag="p"):
    """One kill_before_checkpoint pass over checkpoint puts 1..nput (scratch files in directory d).
    Deterministic: for put #k the child saves the pending writes, drops checkpoint #k, lets the next superstep
    run (as it does while an async checkpoint write is in flight) and dies at the next put() -- CrashSaver in
    _lg_child.py. Then a fresh process resumes the thread and the ledger is checked for re-executed effects."""
    bad, warn, pnotes = [], [], []
    for k in range(1, nput + 1):
        db, led = f"{d}/{tag}{k}.db", f"{d}/{tag}{k}.ledger"
        r1 = _child("full", graph, effects, db, led, inp, resume, put_crash_at=k)
        if r1.returncode == 0:
            continue  # run finished before reaching put #k (e.g. paused) -- nothing to test
        if r1.returncode != 137:      # the child errored / crashed / timed out: never silently skip it
            warn.append(f"put#{k}: {failed(r1, 'child failed')}")
            continue
        r2 = _child("resume", graph, effects, db, led, inp, resume)
        dups, _ = _dups(led)
        if dups:
            bad.append(f"crash before checkpoint #{k} -> re-executed {dups}")
        elif _inflight(led):          # clean refusal: no duplicate observed
            pnotes.append(f"put#{k}: {INFLIGHT_NOTE}")
        elif r2.returncode != 0:
            warn.append(f"put#{k}: {failed(r2, 'resume failed')}")
    return {"detector": "double_execution.kill_before_checkpoint",
            "status": "FAIL" if bad else ("WARN" if warn else "PASS"),
            "detail": "; ".join(bad + warn + pnotes) or f"no duplicates across {nput} checkpoint write(s)"}


def run(graph, effects, inp, resume, multi="each"):
    """multi: how several pending interrupts are answered -- "each" (one at a time, by interrupt id) or "map"."""
    if multi not in ("each", "map", "scalar"):   # "scalar": v0.3.2 behaviour, kept only to regression-test it
        raise ValueError("multi must be 'each' or 'map'")
    MULTI["mode"] = multi
    results = []
    with tempfile.TemporaryDirectory() as d:
        # baseline
        db, led = f"{d}/base.db", f"{d}/base.ledger"
        plog = f"{d}/base.puts"
        r = _child("full", graph, effects, db, led, inp, resume, put_log=plog)
        if r.returncode != 0:
            return {"results": [{"detector": "baseline", "status": "ERROR",
                                 "detail": (r.stderr or "").strip()[-800:] or failed(r, "baseline run failed")}]}
        dups, n = _dups(led)
        results.append({"detector": "baseline", "status": "FAIL" if dups else "PASS",
                        "detail": f"{n} effect call(s) in a clean run" + (f"; duplicates {dups}" if dups else "")})
        # kill after each effect, then resume
        worst, notes = [], []
        for k in range(1, n + 1):
            db, led = f"{d}/c{k}.db", f"{d}/c{k}.ledger"
            r1 = _child("full", graph, effects, db, led, inp, resume, crash_at=k)
            if r1.returncode != 137:
                worst.append(f"crash@{k}: child did not crash ({exit_desc(r1.returncode)})"
                             + (f": {err_tail(r1)}" if r1.returncode else ""))
                continue
            r2 = _child("resume", graph, effects, db, led, inp, resume)
            dups, _ = _dups(led)
            if dups:
                worst.append(f"crash after effect #{k} -> re-executed {dups}")
            elif _inflight(led):          # clean refusal: no duplicate observed
                notes.append(f"crash@{k}: {INFLIGHT_NOTE}")
            elif r2.returncode != 0:
                worst.append(f"crash@{k}: {failed(r2, 'resume failed')}")
        results.append({"detector": "double_execution.kill_resume",
                        "status": "FAIL" if any("re-executed" in w for w in worst) else ("PASS" if not worst else "WARN"),
                        "detail": "; ".join(worst + notes) or f"no duplicates across {n} crash point(s)"})
        # kill BEFORE each checkpoint write (LangGraph #8039: put_writes vs put ordering)
        nput = sum(1 for _ in open(plog)) if os.path.exists(plog) else 0
        results.append(kill_before_checkpoint(graph, effects, inp, resume, nput, d))
        # approval replay + lost approval
        db, led = f"{d}/rep.db", f"{d}/rep.ledger"
        r = _child("replay", graph, effects, db, led, inp, resume)
        info = last_json(r) if r.returncode == 0 else None
        if r.returncode != 0 or not isinstance(info, dict) or "had_interrupt" not in info:
            detail = failed(r, "replay child failed") if r.returncode else "replay child printed no result line"
            # both rows come from this one child: report both, never drop one silently
            results.append({"detector": "approval.lost_on_restart", "status": "ERROR", "detail": f"not tested: {detail}"})
            results.append({"detector": "approval.replay", "status": "ERROR", "detail": detail})
        else:
            dups, _ = _dups(led)
            if not info["had_interrupt"]:
                results.append({"detector": "approval.lost_on_restart", "status": "SKIP", "detail": "graph never interrupted"})
                results.append({"detector": "approval.replay", "status": "SKIP",
                                "detail": "graph never interrupted, so there was no approval to deliver twice"})
            else:
                results.append({"detector": "approval.lost_on_restart",
                                "status": "PASS" if info["pending_after_restart"] else "FAIL",
                                "detail": "pending interrupt visible from a fresh process" if info["pending_after_restart"]
                                else "pending approval not found after process restart"})
                results.append({"detector": "approval.replay", "status": "FAIL" if dups else "PASS",
                                "detail": f"same approval delivered twice -> duplicates {dups}" if dups else "second resume caused no duplicate effects"})
    return {"framework": "langgraph", "results": results}
