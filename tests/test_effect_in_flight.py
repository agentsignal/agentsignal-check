"""EffectInFlight raised on resume counts as a clean refusal (board 54 decision G7)."""
import pytest

pytest.importorskip("langgraph.checkpoint.sqlite")


def test_effect_in_flight_on_resume_is_a_clean_refusal():
    from agentsignal_check.detectors.langgraph_crash import run, INFLIGHT_NOTE
    rep = run("tests.fixtures.inflight_graph:build",
              ["tests.fixtures.inflight_graph:charge", "tests.fixtures.inflight_graph:provider_charge"],
              {"amount": 1999}, True)
    by = {r["detector"]: r for r in rep["results"]}
    assert by["baseline"]["status"] == "PASS"
    ke = by["double_execution.kill_resume"]
    assert ke["status"] == "PASS", ke
    # The resume always reaches the leased effect now, also when the killed attempt's next checkpoint was
    # lost under async durability (the child resumes on pending tasks, not only on `next`): clean refusal + note.
    assert "resume failed" not in ke["detail"]
    assert INFLIGHT_NOTE in ke["detail"], ke
    assert INFLIGHT_NOTE == "in flight; retried after lease with the same provider key"
    assert by["double_execution.kill_before_checkpoint"]["status"] == "PASS"


def test_other_resume_errors_still_warn(tmp_path, monkeypatch):
    """Only EffectInFlight is a clean refusal; any other exception on resume stays a WARN."""
    from agentsignal_check.detectors import langgraph_crash as lc
    real = lc._child
    def child(mode, *a, **k):
        r = real(mode, *a, **k)
        if mode == "resume":
            r.returncode, r.stderr = 1, "Traceback...\nValueError: boom"
        return r
    monkeypatch.setattr(lc, "_child", child)
    rep = lc.run("tests.fixtures.idem_graph:build", ["tests.fixtures.payments:charge"], {"amount": 7}, True)
    ke = next(r for r in rep["results"] if r["detector"] == "double_execution.kill_resume")
    assert ke["status"] == "WARN" and "resume failed" in ke["detail"]


def test_effect_in_flight_refusal_deterministic(monkeypatch):
    """Deterministic: the resume child exits non-zero after the watched effect raised EffectInFlight."""
    from agentsignal_check.detectors import langgraph_crash as lc
    real = lc._child
    def child(mode, graph, effects, db, ledger, *a, **k):
        r = real(mode, graph, effects, db, ledger, *a, **k)
        if mode == "resume":
            open(ledger + ".inflight", "a").write('["m:charge", "()", "[]"]\n')
            r.returncode, r.stderr = 1, "Traceback...\ntests.fixtures.inflight_graph.EffectInFlight: order:x in flight"
        return r
    monkeypatch.setattr(lc, "_child", child)
    rep = lc.run("tests.fixtures.idem_graph:build", ["tests.fixtures.payments:charge"], {"amount": 7}, True)
    by = {r["detector"]: r for r in rep["results"]}
    for det in ("double_execution.kill_resume", "double_execution.kill_before_checkpoint"):
        assert by[det]["status"] == "PASS", by[det]
    assert lc.INFLIGHT_NOTE in by["double_execution.kill_resume"]["detail"]
