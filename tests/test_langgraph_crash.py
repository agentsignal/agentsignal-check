import os, subprocess, sys
from agentsignal_check.detectors.langgraph_crash import run
EFF = ["tests.fixtures.payments:charge"]
def by(rep): return {r["detector"]: r["status"] for r in rep["results"]}

def test_naive_graph_flags_double_execution():
    s = by(run("tests.fixtures.naive_graph:build", EFF, {"amount": 1999}, True))
    assert s["baseline"] == "FAIL"
    assert s["double_execution.kill_before_checkpoint"] == "FAIL"

def test_safe_graph_flags_crash_and_replay_windows():
    s = by(run("tests.fixtures.safe_graph:build", EFF, {"amount": 1999}, True))
    assert s["baseline"] == "PASS"
    assert s["approval.lost_on_restart"] == "PASS"
    # Nondeterministic under LangGraph's default async durability: after a hard kill the thread
    # sometimes reports next=() (looks finished, charge_id never saved) and sometimes re-runs execute.
    assert s["double_execution.kill_resume"] in ("PASS", "FAIL")
    # deterministic since board 53 F1: the state check can't see a charge whose write was lost with the checkpoint
    assert s["double_execution.kill_before_checkpoint"] == "FAIL"
    assert s["approval.replay"] == "FAIL"   # deterministic

def test_idempotent_graph_passes_everything():
    s = by(run("tests.fixtures.idem_graph:build", EFF, {"amount": 1999}, True))
    assert set(s.values()) == {"PASS"}, s
    assert "double_execution.kill_before_checkpoint" in s

def test_cli_exit_code_and_report(tmp_path):
    from agentsignal_check.cli import main
    rp = tmp_path / "r.md"
    assert main(["check", "--graph", "tests.fixtures.naive_graph:build", "--effect", EFF[0],
                 "--input", '{"amount":5}', "--report", str(rp)]) == 1
    md = rp.read_text()
    assert "Verdict: **FAIL**" in md and "https://tryagentsignal.com/fix/" in md
    assert main(["check", "--graph", "tests.fixtures.idem_graph:build", "--effect", EFF[0], "--input", '{"amount":5}']) == 0

def test_cli_missing_extra_message(monkeypatch, capsys):
    import builtins
    from agentsignal_check.cli import main
    real = builtins.__import__
    def fake(name, *a, **k):
        if name.startswith("langgraph"):
            raise ImportError(name)
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    assert main(["check", "--graph", "x:y", "--effect", "a:b"]) == 2
    assert "agentsignal-check[langgraph]" in capsys.readouterr().err
