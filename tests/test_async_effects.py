"""Board 54 X2 (Tester): the effect watcher must await async effects before recording them.

Before the fix the child wrappers called an `async def` effect without awaiting it: the ledger row held a
`<coroutine object ... at 0x...>` repr (a new value on every call, so a ledger short-circuit on replay looked
like a duplicate) and the crash-at-N kill landed before the effect had run.
"""
import asyncio, json, os, subprocess, sys
import pytest

from agentsignal_check.detectors import _effects
from agentsignal_check.detectors.langgraph_crash import _dups

LEDGER_FN = "tests.fixtures.async_ledger:acharge"
PROVIDER = "tests.fixtures.payments:charge"
NAIVE_FN = "tests.fixtures.async_ledger:acharge_naive"
CTX = {"order_id": "order-42"}


@pytest.fixture
def watch(tmp_path, monkeypatch):
    """install() in-process, restoring the patched module attributes afterwards."""
    monkeypatch.setenv("AS_SCENARIO", str(tmp_path / "s"))
    led = str(tmp_path / "ledger")
    def go(*specs):
        for spec in specs:
            mod, name = _effects.load(spec)
            monkeypatch.setattr(mod, name, getattr(mod, name))   # registers the restore
        _effects.install(list(specs), led, 0)
        return led
    monkeypatch.setitem(_effects._state, "calls", 0)
    return go


def rows(led):
    return [json.loads(l) for l in open(led)] if os.path.exists(led) else []


# --- unit: the wrapper itself -------------------------------------------------------------------------

def test_async_effect_counted_once_on_clean_run(watch):
    from tests.fixtures import async_ledger
    led = watch(NAIVE_FN)
    assert asyncio.iscoroutinefunction(async_ledger.acharge_naive)      # still awaitable for frameworks
    assert asyncio.run(async_ledger.acharge_naive(5)) == "ch_1"
    r = rows(led)
    assert len(r) == 1 and r[0][3] == "'ch_1'", r                         # real result, not a coroutine repr
    assert _dups(led) == ({}, 1)


def test_sync_callable_returning_coroutine_is_awaited(watch):
    from tests.fixtures import async_ledger
    led = watch("tests.fixtures.async_ledger:acharge_via_sync_wrapper")
    assert asyncio.run(async_ledger.acharge_via_sync_wrapper(5)) == "ch_1"
    assert [x[3] for x in rows(led)] == ["'ch_1'"]


def test_sync_effect_unchanged(watch):
    from tests.fixtures import payments
    led = watch(PROVIDER)
    assert not asyncio.iscoroutinefunction(payments.charge)
    assert payments.charge(5) == "ch_1" and payments.charge(5) == "ch_2"
    assert [x[3] for x in rows(led)] == ["'ch_1'", "'ch_2'"]
    assert list(_dups(led)[0].values()) == [2]                            # two real charges, same args


def test_async_ledger_short_circuit_on_replay_is_not_a_duplicate(watch):
    from tests.fixtures import async_ledger
    led = watch(LEDGER_FN, PROVIDER)
    first = asyncio.run(async_ledger.acharge(1999))
    replay = asyncio.run(async_ledger.acharge(1999))                     # committed: no provider call
    assert first == replay == "ch_1"
    assert [r[0] for r in rows(led)] == [PROVIDER, LEDGER_FN, LEDGER_FN]
    assert _dups(led)[0] == {}


def test_async_naive_effect_twice_is_a_duplicate(watch):
    from tests.fixtures import async_ledger
    led = watch(NAIVE_FN)
    asyncio.run(async_ledger.acharge_naive(1999)); asyncio.run(async_ledger.acharge_naive(1999))
    assert list(_dups(led)[0].values()) == [2]


def test_async_effect_in_flight_is_logged_not_recorded(watch, tmp_path):
    from tests.fixtures import async_ledger
    led = watch(LEDGER_FN)
    p = str(tmp_path / "s") + ".aledger.json"
    json.dump({f"{tmp_path / 's'}:order-42:charge": {"status": "pending", "lease_until": 9e12}}, open(p, "w"))
    with pytest.raises(async_ledger.EffectInFlight):
        asyncio.run(async_ledger.acharge(1999))
    assert rows(led) == [] and os.path.getsize(led + ".inflight") > 0


def test_crash_at_kills_after_the_async_effect_ran(tmp_path):
    """The hard kill must land AFTER the awaited effect (provider charged, row recorded), not before it."""
    led, scen = str(tmp_path / "ledger"), str(tmp_path / "s")
    code = ("import asyncio, sys; sys.path.insert(0, '.');"
            "from agentsignal_check.detectors._effects import install;"
            f"install([{NAIVE_FN!r}], {led!r}, 1);"
            "from tests.fixtures import async_ledger; asyncio.run(async_ledger.acharge_naive(5)); print('survived')")
    r = subprocess.run([sys.executable, "-c", code], env=dict(os.environ, AS_SCENARIO=scen),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 137 and "survived" not in r.stdout, r.stderr
    assert json.load(open(scen + ".provider.json"))["n"] == 1
    assert [x[3] for x in rows(led)] == ["'ch_1'"]


# --- end to end: the detectors ------------------------------------------------------------------------

def by(rep): return {r["detector"]: r for r in rep["results"]}


def test_oai_async_ledger_both_targets_no_false_duplicate():
    pytest.importorskip("agents")
    from agentsignal_check.detectors.oai_crash import run
    s = by(run("tests.fixtures.oai_async_ledger:build", [LEDGER_FN, PROVIDER], CTX))
    assert {k: v["status"] for k, v in s.items()} == {
        "baseline": "PASS", "double_execution.kill_resume": "PASS",
        "approval.lost_on_restart": "PASS", "approval.replay": "PASS"}, s
    assert "re-executed" not in s["double_execution.kill_resume"]["detail"]


def test_oai_async_naive_still_double_executes():
    pytest.importorskip("agents")
    from agentsignal_check.detectors.oai_crash import run
    s = by(run("tests.fixtures.oai_async_naive:build", [NAIVE_FN], CTX))
    assert s["double_execution.kill_resume"]["status"] == "FAIL", s
    assert "re-executed" in s["double_execution.kill_resume"]["detail"]
    assert s["approval.replay"]["status"] == "FAIL"


def test_lg_async_ledger_both_targets_no_false_duplicate():
    pytest.importorskip("langgraph.checkpoint.sqlite")
    from agentsignal_check.detectors.langgraph_crash import run
    s = by(run("tests.fixtures.lg_async_graph:build", [LEDGER_FN, PROVIDER], {"amount": 1999}, True))
    assert all(v["status"] == "PASS" for v in s.values()), s


def test_lg_async_naive_still_double_executes():
    pytest.importorskip("langgraph.checkpoint.sqlite")
    from agentsignal_check.detectors.langgraph_crash import run
    s = by(run("tests.fixtures.lg_async_graph:build_naive", [NAIVE_FN], {"amount": 1999}, True))
    assert s["double_execution.kill_resume"]["status"] == "FAIL", s
