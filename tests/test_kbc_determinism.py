"""Board 53 F1 (Tester @ 1b7159c): kill_before_checkpoint was nondeterministic on naive graphs (false PASS in
17/80 runs). Under LangGraph's default async durability, put() runs on a background thread, so how far the run
got before the kill (and whether the effect task's pending writes had landed) was a thread race. The child now
replays the window deterministically (CrashSaver in _lg_child.py, durability="sync"): pending writes saved so
far land, checkpoint #k is dropped, exactly one superstep runs ahead, then a hard kill at the next put()."""
import json, os, tempfile
import pytest

pytest.importorskip("langgraph.checkpoint.sqlite")
from agentsignal_check.detectors import langgraph_crash as lc

EFF = ["tests.fixtures.payments:charge"]
INP = {"amount": 1999}
N = 20


def _nput(graph):
    with tempfile.TemporaryDirectory() as d:
        r = lc._child("full", graph, EFF, f"{d}/b.db", f"{d}/b.ledger", INP, True, put_log=f"{d}/b.puts")
        assert r.returncode == 0, r.stderr
        return sum(1 for _ in open(f"{d}/b.puts"))


def _repeat(graph, n=N):
    nput = _nput(graph)
    out = []
    for _ in range(n):
        with tempfile.TemporaryDirectory() as d:
            out.append(lc.kill_before_checkpoint(graph, EFF, INP, True, nput, d))
    return out


def test_split_naive_kill_before_checkpoint_fails_20_of_20():
    """Repro from the Tester: split_naive_graph:build --effect tests.fixtures.payments:charge --input '{"amount":1999}'."""
    res = _repeat("tests.fixtures.split_naive_graph:build")
    assert [r["status"] for r in res] == ["FAIL"] * N, [r["detail"] for r in res if r["status"] != "FAIL"]
    # same crash point and the same duplicate every time: not just "some FAIL"
    assert len({r["detail"] for r in res}) == 1, {r["detail"] for r in res}
    assert "re-executed" in res[0]["detail"]


def test_keyed_graph_kill_before_checkpoint_passes_20_of_20():
    res = _repeat("tests.fixtures.idem_graph:build")
    assert [r["status"] for r in res] == ["PASS"] * N, [r["detail"] for r in res if r["status"] != "PASS"]


def test_put_crash_child_keeps_pending_writes_and_drops_the_checkpoint(tmp_path, monkeypatch):
    """Child-level: crash at the checkpoint written after `approve` -> the charge runs ahead (one superstep), the
    process dies (137), the resume re-runs `execute` (approve's write landed, the new checkpoint did not)."""
    g = "tests.fixtures.split_naive_graph:build"
    nput = _nput(g)
    seen = set()
    for k in range(1, nput + 1):
        db, led = str(tmp_path / f"p{k}.db"), str(tmp_path / f"p{k}.ledger")
        r1 = lc._child("full", g, EFF, db, led, INP, True, put_crash_at=k)
        if r1.returncode == 0:
            continue
        assert r1.returncode == 137, r1.stderr
        before = sum(1 for _ in open(led)) if os.path.exists(led) else 0
        lc._child("resume", g, EFF, db, led, INP, True)
        after = sum(1 for _ in open(led)) if os.path.exists(led) else 0
        seen.add((k, before, after))
    # exactly one crash point where the effect ran ahead of the lost checkpoint and the resume ran it again
    assert sum(1 for _, b, a in seen if b == 1 and a == 2) == 1, seen
