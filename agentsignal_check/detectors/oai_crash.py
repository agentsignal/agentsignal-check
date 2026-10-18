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
from .langgraph_crash import _dups, _inflight, INFLIGHT_NOTE

CHILD = os.path.join(os.path.dirname(__file__), "_oai_child.py")

def _child(mode, agent, effects, state, ledger, ctx, crash_at=0):
    env = dict(os.environ, AS_LEDGER=ledger, AS_CRASH_AT=str(crash_at), AS_SCENARIO=state)
    cmd = [sys.executable, CHILD, mode, agent, json.dumps(effects), state, json.dumps(ctx)]
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120)

def _last(r):
    return json.loads(r.stdout.strip().splitlines()[-1])

def _err(r):
    return (r.stderr.strip().splitlines() or ["?"])[-1][-300:]

def run(agent, effects, ctx):
    results = []
    with tempfile.TemporaryDirectory() as d:
        st, led = f"{d}/base.json", f"{d}/base.ledger"
        r = _child("full", agent, effects, st, led, ctx)
        if r.returncode != 0:
            return {"framework": "openai-agents", "results": [{"detector": "baseline", "status": "ERROR", "detail": r.stderr[-800:]}]}
        dups, n = _dups(led)
        results.append({"detector": "baseline", "status": "FAIL" if dups else "PASS",
                        "detail": f"{n} effect call(s) in a clean run" + (f"; duplicates {dups}" if dups else "")})
        worst, notes = [], []
        for k in range(1, n + 1):
            st, led = f"{d}/c{k}.json", f"{d}/c{k}.ledger"
            if _child("start", agent, effects, st, led, ctx).returncode:
                worst.append(f"crash@{k}: start failed"); continue
            r1 = _child("resume", agent, effects, st, led, ctx, crash_at=k)
            if r1.returncode != 137:
                worst.append(f"crash@{k}: child did not crash (rc={r1.returncode})"); continue
            r2 = _child("resume", agent, effects, st, led, ctx)
            dups, _ = _dups(led)
            if dups: worst.append(f"crash after effect #{k} -> re-executed {dups}")
            elif _inflight(led): notes.append(f"crash@{k}: {INFLIGHT_NOTE}")   # clean refusal: no duplicate observed
            elif r2.returncode != 0: worst.append(f"crash@{k}: resume failed: {_err(r2)}")
        results.append({"detector": "double_execution.kill_resume",
                        "status": "FAIL" if any("re-executed" in w for w in worst) else ("PASS" if not worst else "WARN"),
                        "detail": "; ".join(worst + notes) or f"no duplicates across {n} crash point(s) (resumed from RunState.from_json)"})
        # lost approval
        st, led = f"{d}/lost.json", f"{d}/lost.ledger"
        s = _child("start", agent, effects, st, led, ctx)
        if s.returncode:
            results.append({"detector": "approval.lost_on_restart", "status": "ERROR", "detail": _err(s)})
        elif not _last(s)["interruptions"]:
            results.append({"detector": "approval.lost_on_restart", "status": "SKIP", "detail": "agent never paused for approval"})
        else:
            pend = _last(_child("inspect", agent, effects, st, led, ctx))["pending"]
            a = _child("approve_save", agent, effects, st, led, ctx)
            r = _child("resume_noapprove", agent, effects, st, led, ctx)
            if a.returncode or r.returncode:
                results.append({"detector": "approval.lost_on_restart", "status": "ERROR", "detail": _err(a if a.returncode else r)})
            else:
                info, (_, ran) = _last(r), _dups(led)
                ok = pend > 0 and info["reinterrupted"] == 0 and ran > 0
                results.append({"detector": "approval.lost_on_restart", "status": "PASS" if ok else "FAIL",
                                "detail": f"pending after restart={pend}; persisted approval honoured by fresh process "
                                          f"(re-asked={info['reinterrupted']}, effect calls={ran})"})
        # approval replay
        st, led = f"{d}/rep.json", f"{d}/rep.ledger"
        if _child("start", agent, effects, st, led, ctx).returncode:
            results.append({"detector": "approval.replay", "status": "ERROR", "detail": "start failed"})
        else:
            snap = open(st).read()
            r1 = _child("resume", agent, effects, st, led, ctx)
            open(st, "w").write(snap)                     # second worker got the same snapshot
            r2 = _child("resume", agent, effects, st, led, ctx)
            if r1.returncode or r2.returncode:
                results.append({"detector": "approval.replay", "status": "ERROR", "detail": _err(r1 if r1.returncode else r2)})
            else:
                dups, _ = _dups(led)
                results.append({"detector": "approval.replay", "status": "FAIL" if dups else "PASS",
                                "detail": f"same RunState resumed twice -> duplicates {dups}" if dups else "second resume caused no duplicate effects"})
    return {"framework": "openai-agents", "results": results}
