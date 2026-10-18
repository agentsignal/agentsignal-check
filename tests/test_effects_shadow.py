"""Board 53 F2 (regression from 8c4b2fd): the crash-test children imported `_effects` as a top-level module
(their own directory was sys.path[0]), which shadowed a user's own `_effects.py`: the user graph got ours and
the run exited 2. They now import agentsignal_check.detectors._effects and drop their directory from sys.path."""
import os, sys, textwrap
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _project(tmp_path, monkeypatch):
    (tmp_path / "_effects.py").write_text("def helper():\n    return 'user'\n")
    (tmp_path / "user_graph.py").write_text(textwrap.dedent('''
        import _effects
        from tests.fixtures import idem_graph
        def build(cp):
            assert _effects.helper() == "user", f"user _effects shadowed by {_effects.__file__}"
            return idem_graph.build(cp)
    '''))
    (tmp_path / "user_agent.py").write_text(textwrap.dedent('''
        import _effects
        from tests.fixtures import oai_idem
        def build():
            assert _effects.helper() == "user", f"user _effects shadowed by {_effects.__file__}"
            return oai_idem.build()
    '''))
    monkeypatch.chdir(tmp_path)        # the user's project is the cwd, as with the CLI
    monkeypatch.setenv("PYTHONPATH", ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""))   # tests.fixtures
    monkeypatch.setenv("AS_SCENARIO", str(tmp_path / "s"))


def test_user_effects_module_is_not_shadowed_langgraph(tmp_path, monkeypatch):
    pytest.importorskip("langgraph.checkpoint.sqlite")
    _project(tmp_path, monkeypatch)
    from agentsignal_check.detectors.langgraph_crash import run
    rep = run("user_graph:build", ["tests.fixtures.payments:charge"], {"amount": 5}, True)
    st = {r["detector"]: r["status"] for r in rep["results"]}
    assert set(st.values()) == {"PASS"}, rep["results"]


def test_user_effects_module_is_not_shadowed_openai_agents(tmp_path, monkeypatch):
    pytest.importorskip("agents")
    _project(tmp_path, monkeypatch)
    from agentsignal_check.detectors.oai_crash import run
    rep = run("user_agent:build", ["tests.fixtures.payments:charge"], {"order_id": "order-42"})
    st = {r["detector"]: r["status"] for r in rep["results"]}
    assert set(st.values()) == {"PASS"}, rep["results"]


def test_cli_exit_0_with_user_effects_module(tmp_path, monkeypatch):
    """Real process, like the Tester's repro (qafx/effects_shadow_g.py gave rc 2)."""
    pytest.importorskip("langgraph.checkpoint.sqlite")
    import subprocess
    _project(tmp_path, monkeypatch)
    r = subprocess.run([sys.executable, "-m", "agentsignal_check.cli", "check", "--graph", "user_graph:build",
                        "--effect", "tests.fixtures.payments:charge", "--input", '{"amount":5}', "--json"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]
