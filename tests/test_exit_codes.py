"""Elon 2026-10-18 (P0): any error or FAIL anywhere in a check run exits non-zero -- not only a baseline error.
Covers: a resume that raises, hangs (child timeout), crashes (signal) or exits non-zero; a replay child with no
result line; a kill_before_checkpoint child that errors; the openai-agents inspect child failing; an unhandled
exception in the driver; bad --input; and the v0.3.2 multi-interrupt driver error (scalar Command(resume=...)
with several pending interrupts), which exited 0 on v0.3.2."""
import json, os, subprocess, sys
import pytest
from agentsignal_check.cli import main, exit_code

pytest.importorskip("langgraph.checkpoint.sqlite")
EFF = ["--effect", "tests.fixtures.payments:charge"]
FAULT = ["check", "--graph", "tests.fixtures.fault_graph:build", *EFF, "--input", '{"amount": 5}', "--json"]


def _run(capsys, argv):
    code = main(argv)
    out, err = capsys.readouterr()
    return code, (json.loads(out) if out.strip().startswith("{") else None), err


def by(rep): return {r["detector"]: r for r in rep["results"]}


def test_exit_code_policy_table():
    R = lambda *st: {"results": [{"detector": f"d{i}", "status": s} for i, s in enumerate(st)]}
    assert exit_code(R("PASS", "PASS", "SKIP")) == 0
    assert exit_code(R("PASS", "FAIL")) == 1
    assert exit_code(R("PASS", "FAIL", "ERROR")) == 1
    assert exit_code(R("PASS", "WARN")) == 2
    assert exit_code(R("PASS", "ERROR")) == 2
    assert exit_code(R("PASS", "weird")) == 2
    assert exit_code({"results": []}) == 2


@pytest.mark.parametrize("fault", ["raise", "crash", "exit"])
def test_resume_error_or_crash_exits_non_zero(capsys, monkeypatch, fault):
    monkeypatch.setenv("AS_FAULT", fault)
    code, rep, err = _run(capsys, FAULT)
    s = by(rep)
    assert s["baseline"]["status"] == "PASS"                       # only the resumes misbehave
    assert s["double_execution.kill_resume"]["status"] == "WARN", s
    assert "resume failed" in s["double_execution.kill_resume"]["detail"]
    assert code == 2 and "not a pass" in err


def test_resume_timeout_exits_non_zero(capsys, monkeypatch):
    monkeypatch.setenv("AS_FAULT", "hang")
    monkeypatch.setenv("AGENTSIGNAL_CHILD_TIMEOUT_S", "8")
    code, rep, _ = _run(capsys, FAULT)
    s = by(rep)
    assert "timed out after 8s" in s["double_execution.kill_resume"]["detail"], s
    assert code == 2


def test_baseline_timeout_exits_2(capsys, monkeypatch):
    monkeypatch.setenv("AGENTSIGNAL_CHILD_TIMEOUT_S", "0.01")
    code, rep, err = _run(capsys, ["check", "--graph", "tests.fixtures.idem_graph:build", *EFF, "--json"])
    assert code == 2 and by(rep)["baseline"]["status"] == "ERROR" and "timed out" in err


def test_kill_before_checkpoint_child_error_is_not_skipped(monkeypatch):
    from agentsignal_check.detectors import langgraph_crash as lc
    real = lc._child
    def child(mode, *a, put_crash_at=0, **k):
        r = real(mode, *a, put_crash_at=put_crash_at, **k)
        if put_crash_at:
            r.returncode, r.stderr = 1, "Traceback...\nValueError: boom"
        return r
    monkeypatch.setattr(lc, "_child", child)
    rep = lc.run("tests.fixtures.idem_graph:build", ["tests.fixtures.payments:charge"], {"amount": 7}, True)
    kb = by(rep)["double_execution.kill_before_checkpoint"]
    assert kb["status"] == "WARN" and "child failed" in kb["detail"] and exit_code(rep) == 2


def test_replay_child_without_result_line_is_an_error(monkeypatch):
    from agentsignal_check.detectors import langgraph_crash as lc
    real = lc._child
    def child(mode, *a, **k):
        r = real(mode, *a, **k)
        if mode == "replay":
            r.stdout = "user print, not json\n"
        return r
    monkeypatch.setattr(lc, "_child", child)
    rep = lc.run("tests.fixtures.idem_graph:build", ["tests.fixtures.payments:charge"], {"amount": 7}, True)
    assert by(rep)["approval.replay"]["status"] == "ERROR" and exit_code(rep) == 2


def test_unhandled_driver_exception_exits_2(capsys, monkeypatch):
    from agentsignal_check.detectors import langgraph_crash as lc
    def boom(*a, **k): raise KeyError("boom")
    monkeypatch.setattr(lc, "run", boom)
    code, rep, _ = _run(capsys, ["check", "--graph", "tests.fixtures.idem_graph:build", *EFF, "--json"])
    assert code == 2 and rep["results"] == [{"detector": "driver", "status": "ERROR", "detail": "KeyError: 'boom'"}]


def test_bad_input_json_exits_2(capsys):
    code, rep, _ = _run(capsys, ["check", "--graph", "tests.fixtures.idem_graph:build", *EFF, "--input", "{nope", "--json"])
    assert code == 2 and rep["results"][0]["detector"] == "driver"


def test_v0_3_2_multi_interrupt_driver_error_exits_non_zero(capsys, monkeypatch):
    """v0.3.2 resumed with a scalar Command(resume=value); LangGraph rejects that when several interrupts are
    pending, the baseline errored, and the CLI still exited 0. Reproduced with the legacy 'scalar' driver mode."""
    from agentsignal_check.detectors import langgraph_crash as lc
    real = lc.run
    monkeypatch.setattr(lc, "run", lambda *a, multi="each", **k: real(*a, multi="scalar", **k))
    code, rep, err = _run(capsys, ["check", "--graph", "tests.fixtures.subgraph_parallel_graph:build", *EFF,
                                   "--input", '{"log": []}', "--json"])
    assert by(rep)["baseline"]["status"] == "ERROR", rep
    assert code == 2 and "baseline run failed" in err


def test_openai_agents_inspect_child_failure_is_an_error(monkeypatch):
    pytest.importorskip("agents")
    from agentsignal_check.detectors import oai_crash as oc
    real = oc._child
    def child(mode, *a, **k):
        r = real(mode, *a, **k)
        if mode == "inspect":
            r.returncode, r.stdout, r.stderr = 1, "", "Traceback...\nRuntimeError: inspect broke"
        return r
    monkeypatch.setattr(oc, "_child", child)
    rep = oc.run("tests.fixtures.oai_idem:build", ["tests.fixtures.payments:charge"], {"order_id": "o"})
    lost = by(rep)["approval.lost_on_restart"]
    assert lost["status"] == "ERROR" and "inspect broke" in lost["detail"] and exit_code(rep) == 2


def test_cli_process_exit_status_is_non_zero():
    """End to end through a real process (what CI sees)."""
    env = dict(os.environ, AS_FAULT="raise")
    r = subprocess.run([sys.executable, "-m", "agentsignal_check.cli", *FAULT], capture_output=True, text=True, env=env)
    assert r.returncode == 2, r.stderr
