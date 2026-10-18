"""A baseline run that errors (setup, import or graph failure) exits 2 instead of passing CI."""
import pytest
from agentsignal_check.cli import main

pytest.importorskip("langgraph.checkpoint.sqlite")

def test_baseline_error_exits_2_langgraph(capsys):
    code = main(["check", "--graph", "tests.fixtures.broken_graph:build", "--effect", "tests.fixtures.payments:charge"])
    err = capsys.readouterr().err
    assert code == 2
    assert "baseline run failed" in err and "DATABASE_URL not set" in err


def test_baseline_import_error_exits_2(capsys):
    code = main(["check", "--graph", "tests.fixtures.no_such_module:build", "--effect", "tests.fixtures.payments:charge",
                 "--json"])
    out, err = capsys.readouterr()
    assert code == 2 and '"status": "ERROR"' in out and "ModuleNotFoundError" in err


def test_baseline_error_exits_2_openai_agents(capsys):
    pytest.importorskip("agents")
    code = main(["check", "--framework", "openai-agents", "--agent", "tests.fixtures.broken_agent:build",
                 "--effect", "tests.fixtures.payments:charge"])
    assert code == 2 and "missing tool config" in capsys.readouterr().err


def test_passing_and_failing_runs_keep_their_exit_codes():
    eff = ["--effect", "tests.fixtures.payments:charge", "--input", '{"amount":5}']
    assert main(["check", "--graph", "tests.fixtures.idem_graph:build", *eff]) == 0
    assert main(["check", "--graph", "tests.fixtures.naive_graph:build", *eff]) == 1
