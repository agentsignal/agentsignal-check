"""The crash-test driver answers several pending interrupts by interrupt id (langgraph needs
{interrupt_id: value} there), one at a time (default) or all at once (--multi-interrupt map).
Reproducer: tests/fixtures/subgraph_parallel_graph.py (langgraph#9315, fixed in 1.2.16)."""
import importlib.metadata as md
import pytest

pytest.importorskip("langgraph.checkpoint.sqlite")
from agentsignal_check.detectors.langgraph_crash import run  # noqa: E402

G, EFF = "tests.fixtures.subgraph_parallel_graph:build", ["tests.fixtures.payments:charge"]
FIXED = tuple(int(x) for x in md.version("langgraph").split(".")[:3]) >= (1, 2, 16)


def by(rep):
    return {r["detector"]: r for r in rep["results"]}


def test_one_at_a_time_reports_9315_on_affected_versions():
    s = by(run(G, EFF, {"log": []}, True, multi="each"))
    assert s["baseline"]["status"] != "ERROR", s["baseline"]       # v0.3.2 errored here (scalar resume)
    assert s["approval.lost_on_restart"]["status"] == "PASS"
    if FIXED:   # 1.2.16+: one charge per approval
        assert s["baseline"]["status"] == "PASS" and s["baseline"]["detail"].startswith("3 effect call(s)")
        assert s["approval.replay"]["status"] == "PASS"
    else:       # <=1.2.15: answered siblings re-run on every later answer (3+2+1 = 6 charges)
        assert s["baseline"]["status"] == "FAIL" and s["baseline"]["detail"].startswith("6 effect call(s)")
        assert s["approval.replay"]["status"] == "FAIL"
    # the fixture's charge has no idempotency key, so a kill right after it re-runs it on every version
    assert s["double_execution.kill_resume"]["status"] == "FAIL"


def test_map_answers_all_at_once():
    s = by(run(G, EFF, {"log": []}, True, multi="map"))
    assert s["baseline"]["status"] == "PASS" and s["baseline"]["detail"].startswith("3 effect call(s)")
    assert s["approval.replay"]["status"] == "PASS"


def test_cli_multi_interrupt_flag(capsys):
    from agentsignal_check.cli import main
    code = main(["check", "--graph", G, "--effect", EFF[0], "--input", '{"log": []}', "--multi-interrupt", "map", "--json"])
    assert code == 1 and '"status": "ERROR"' not in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["check", "--graph", G, "--effect", EFF[0], "--multi-interrupt", "bogus"])


def test_bad_multi_value():
    with pytest.raises(ValueError):
        run(G, EFF, {}, True, multi="bogus")
