"""Crash-test fixture for langgraph#9315: parallel interrupts in a subgraph, answered one at a time.
Records the real behaviour of the installed langgraph: duplicates on <= 1.2.15, none on >= 1.2.16."""
import importlib.metadata as md, json, os
import pytest
lg = pytest.importorskip("langgraph")
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from tests.fixtures import subgraph_parallel_graph as fx

def _ver(v): return tuple(int(x) for x in v.split(".")[:3])

def _pending(g, cfg):
    st = g.get_state(cfg, subgraphs=True)
    ints = list(st.interrupts) + [i for t in st.tasks for i in (t.state.interrupts if t.state else ())]
    return {i.value["tool"]: i.id for i in ints}

def test_subgraph_parallel_answers_one_at_a_time(tmp_path, monkeypatch):
    monkeypatch.setenv("AS_SCENARIO", str(tmp_path / "s"))
    g = fx.build(InMemorySaver()); cfg = {"configurable": {"thread_id": "t"}}
    g.invoke({"log": []}, cfg)
    for name in ("a", "b", "c"):
        out = g.invoke(Command(resume={_pending(g, cfg)[name]: True}), cfg)
    assert not g.get_state(cfg).next
    assert len(out["log"]) == 3                      # final state looks right on every version
    db = json.load(open(str(tmp_path / "s") + ".provider.json"))
    charges = db["n"]
    v = md.version("langgraph")
    print(f"langgraph {v}: {charges} charge(s) for 3 approvals")
    if _ver(v) >= (1, 2, 16):
        assert charges == 3, f"langgraph {v}: expected 1 charge per approval, got {charges}"
    else:
        assert charges == 6, f"langgraph {v}: expected the #9315 re-run (3+2+1=6), got {charges}"


def test_keyed_variant_passes_every_detector():
    """Board 64: the same #9315 graph with each charge keyed by a stable id passes the whole crash test
    (on <= 1.2.15 the re-runs still happen but the provider dedupes them; on >= 1.2.16 they don't happen)."""
    pytest.importorskip("langgraph.checkpoint.sqlite")
    from agentsignal_check.detectors.langgraph_crash import run
    rep = run("tests.fixtures.subgraph_parallel_graph:build_keyed", ["tests.fixtures.payments:charge"], {"log": []}, True)
    s = {r["detector"]: r for r in rep["results"]}
    assert {k: v["status"] for k, v in s.items()} == {d: "PASS" for d in s} and len(s) == 5, s
    calls = 3 if _ver(md.version("langgraph")) >= (1, 2, 16) else 6
    assert s["baseline"]["detail"].startswith(f"{calls} effect call(s)"), s["baseline"]
