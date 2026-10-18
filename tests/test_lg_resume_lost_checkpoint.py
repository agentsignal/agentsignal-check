"""Board 54 X3 (Tester): naive LangGraph kill_resume was flaky (FAIL/PASS varied between runs).

Cause: under LangGraph's default async durability the hard kill right after the charge sometimes lands after the
approve task's pending writes were saved but before the next checkpoint was. get_state() then reports next=()
although `execute` never committed, and the resume child treated next=() as "finished" and did nothing -> a
random false PASS. The child now resumes whenever the latest checkpoint still has tasks (st.next or st.tasks).
This test builds that exact state deterministically (checkpoint puts after the interrupt are dropped).
"""
import json, os, sqlite3
import pytest

pytest.importorskip("langgraph.checkpoint.sqlite")
GRAPH, EFF = "tests.fixtures.split_naive_graph:build", ["tests.fixtures.payments:charge"]
CFG = {"configurable": {"thread_id": "agentsignal-check"}}


def _lost_checkpoint_db(tmp_path, monkeypatch):
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.types import Command
    from tests.fixtures import split_naive_graph as m
    db = str(tmp_path / "c.db")
    monkeypatch.setenv("AS_SCENARIO", db)              # same provider store the resume child uses

    class DropAfterInterrupt(SqliteSaver):
        n = 0
        def put(self, config, checkpoint, metadata, new_versions):
            DropAfterInterrupt.n += 1
            if DropAfterInterrupt.n >= 3:          # 1 input, 2 start, 3+ lost (as if killed before they landed)
                return {"configurable": {**config["configurable"], "checkpoint_id": checkpoint["id"]}}
            return super().put(config, checkpoint, metadata, new_versions)

    g = m.build(DropAfterInterrupt(sqlite3.connect(db, check_same_thread=False)))
    g.invoke({"amount": 1999}, CFG)
    g.invoke(Command(resume=True), CFG)                 # approve writes saved, charge ch_1, checkpoint lost
    st = m.build(SqliteSaver(sqlite3.connect(db, check_same_thread=False))).get_state(CFG)
    assert st.next == () and [t.name for t in st.tasks] == ["approve"], (st.next, st.tasks)
    return db


def test_resume_continues_when_next_is_empty_but_tasks_pending(tmp_path, monkeypatch):
    from agentsignal_check.detectors import langgraph_crash as lc
    db = _lost_checkpoint_db(tmp_path, monkeypatch)
    led = str(tmp_path / "c.ledger")
    r = lc._child("resume", GRAPH, EFF, db, led, {"amount": 1999}, True)
    assert r.returncode == 0, r.stderr
    rows = [json.loads(l) for l in open(led)] if os.path.exists(led) else []
    assert len(rows) == 1, "resume must re-run `execute` (the charge never committed)"
    # the scenario's provider already charged ch_1 before the crash: the resume charge is a real second one
    assert rows[0][3] == "'ch_2'"


def test_finished_run_is_not_resumed(tmp_path, monkeypatch):
    from agentsignal_check.detectors import langgraph_crash as lc
    monkeypatch.setenv("AS_SCENARIO", str(tmp_path / "s"))
    db, led = str(tmp_path / "f.db"), str(tmp_path / "f.ledger")
    assert lc._child("full", GRAPH, EFF, db, led, {"amount": 1999}, True).returncode == 0
    n = sum(1 for _ in open(led))
    assert lc._child("resume", GRAPH, EFF, db, led, {"amount": 1999}, True).returncode == 0
    assert sum(1 for _ in open(led)) == n


def test_split_naive_kill_resume_fails_every_time():
    from agentsignal_check.detectors.langgraph_crash import run
    for _ in range(3):
        s = {r["detector"]: r["status"] for r in run(GRAPH, EFF, {"amount": 1999}, True)["results"]}
        assert s["double_execution.kill_resume"] == "FAIL", s
