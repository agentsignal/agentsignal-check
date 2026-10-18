"""Kill-and-resume detector for the OpenAI Agents SDK (RunState / needs_approval tools).

The agent pauses on an approval-gated tool; the app persists RunState.to_json() and later restores it
with RunState.from_json() in a fresh process. Detectors:
  baseline                       clean run, approve everything: any duplicate effect?
  double_execution.kill_resume   approve + resume, hard-kill after effect #N, restart from the persisted
                                 RunState and resume again (approval re-applied): effect re-executed?
  approval.lost_on_restart       pending approval visible after restart, and an approval persisted via
                                 to_json() is honoured by a fresh process without re-asking
  approval.replay                the same persisted snapshot resumed twice (double-click / redelivery)
A duplicate = same call args producing DISTINCT results (same result = deduped by an idempotency key).
"""
import json, os, subprocess, sys, tempfile
from .langgraph_crash import _dups, _inflight, INFLIGHT_NOTE, run_child, last_json, err_tail, exit_desc, failed

CHILD = os.path.join(os.path.dirname(__file__), "_oai_child.py")

def _child(mode, agent, effects, state, ledger, ctx, crash_at=0):
    env = dict(os.environ, AS_LEDGER=ledger, AS_CRASH_AT=str(crash_at), AS_SCENARIO=state)
    cmd = [sys.executable, CHILD, mode, agent, json.dumps(effects), state, json.dumps(ctx)]
    return run_child(cmd, env)

def _last(r):
    """The child's JSON result line, or None (failed child / no result line)."""
    v = last_json(r) if r.returncode == 0 else None
    return v if isinstance(v, dict) else None

def _err(r):
    return err_tail(r)

def run(agent, effects, ctx):
    results = []
    with tempfile.TemporaryDirectory() as d:
        st, led = f"{d}/base.json", f"{d}/base.ledger"
        r = _child("full", agent, effects, st, led, ctx)
        if r.returncode != 0:
            return {"framework": "openai-agents", "results": [{"detector": "baseline", "status": "ERROR",
                    "detail": (r.stderr or "").strip()[-800:] or failed(r, "baseline run failed")}]}
        dups, n = _dups(led)
        results.append({"detector": "baseline", "status": "FAIL" if dups else "PASS",
                        "detail": f"{n} effect call(s) in a clean run" + (f"; duplicates {dups}" if dups else "")})
        worst, notes = [], []
        for k in range(1, n + 1):
            st, led = f"{d}/c{k}.json", f"{d}/c{k}.ledger"
            s0 = _child("start", agent, effects, st, led, ctx)
            if s0.returncode:
                worst.append(f"crash@{k}: {failed(s0, 'start failed')}"); continue
            r1 = _child("resume", agent, effects, st, led, ctx, crash_at=k)
            if r1.returncode != 137:
                worst.append(f"crash@{k}: child did not crash ({exit_desc(r1.returncode)})"
                             + (f": {_err(r1)}" if r1.returncode else "")); continue
            r2 = _child("resume", agent, effects, st, led, ctx)
            dups, _ = _dups(led)
            if dups: worst.append(f"crash after effect #{k} -> re-executed {dups}")
            elif _inflight(led): notes.append(f"crash@{k}: {INFLIGHT_NOTE}")   # clean refusal: no duplicate observed
            elif r2.returncode != 0: worst.append(f"crash@{k}: {failed(r2, 'resume failed')}")
        results.append({"detector": "double_execution.kill_resume",
                        "status": "FAIL" if any("re-executed" in w for w in worst) else ("PASS" if not worst else "WARN"),
                        "detail": "; ".join(worst + notes) or f"no duplicates across {n} crash point(s) (resumed from RunState.from_json)"})
        # lost approval
        st, led = f"{d}/lost.json", f"{d}/lost.ledger"
        s = _child("start", agent, effects, st, led, ctx)
        started = _last(s)
        ins = None
        if s.returncode or started is None:
            results.append({"detector": "approval.lost_on_restart", "status": "ERROR",
                            "detail": _err(s) if s.returncode else "start child printed no result line"})
        elif not started.get("interruptions"):
            results.append({"detector": "approval.lost_on_restart", "status": "SKIP", "detail": "agent never paused for approval"})
        elif (ins := _last(i := _child("inspect", agent, effects, st, led, ctx))) is None:
            results.append({"detector": "approval.lost_on_restart", "status": "ERROR",
                            "detail": _err(i) if i.returncode else "inspect child printed no result line"})
        else:
            pend = ins.get("pending", 0)
            a = _child("approve_save", agent, effects, st, led, ctx)
            r = _child("resume_noapprove", agent, effects, st, led, ctx)
            info = _last(r)
            if a.returncode or r.returncode or info is None:
                bad = a if a.returncode else r
                results.append({"detector": "approval.lost_on_restart", "status": "ERROR",
                                "detail": _err(bad) if bad.returncode else "resume child printed no result line"})
            else:
                _, ran = _dups(led)
                ok = pend > 0 and info["reinterrupted"] == 0 and ran > 0
                results.append({"detector": "approval.lost_on_restart", "status": "PASS" if ok else "FAIL",
                                "detail": f"pending after restart={pend}; persisted approval honoured by fresh process "
                                          f"(re-asked={info['reinterrupted']}, effect calls={ran})"})
        # approval replay
        st, led = f"{d}/rep.json", f"{d}/rep.ledger"
        s1 = _child("start", agent, effects, st, led, ctx)
        if s1.returncode:
            results.append({"detector": "approval.replay", "status": "ERROR", "detail": failed(s1, "start failed")})
        elif isinstance(_last(s1), dict) and not _last(s1).get("interruptions"):
            results.append({"detector": "approval.replay", "status": "SKIP",
                            "detail": "agent never paused for approval, so there was no approval to deliver twice"})
        else:
            snap = open(st).read()
            r1 = _child("resume", agent, effects, st, led, ctx)
            open(st, "w").write(snap)                     # second worker got the same snapshot
            r2 = _child("resume", agent, effects, st, led, ctx)
            if r1.returncode or r2.returncode:
                results.append({"detector": "approval.replay", "status": "ERROR", "detail": failed(r1 if r1.returncode else r2, "resume failed")})
            else:
                dups, _ = _dups(led)
                results.append({"detector": "approval.replay", "status": "FAIL" if dups else "PASS",
                                "detail": f"same RunState resumed twice -> duplicates {dups}" if dups else "second resume caused no duplicate effects"})
    return {"framework": "openai-agents", "results": results}
