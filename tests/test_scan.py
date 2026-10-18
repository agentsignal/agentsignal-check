"""Board 53: `agentsignal check --scan` (pure ast static scan)."""
import json
import os
import subprocess
import sys
import time

import pytest

from agentsignal_check import cli
from agentsignal_check.scan import core

HERE = os.path.dirname(os.path.abspath(__file__))
FX = os.path.join(HERE, "fixtures")
SC = os.path.join(FX, "scan")
from agentsignal_check.links import SCAN_FIX_URL as FIX_URL, SCAN_FIX_LABEL as FIX_LABEL


def run(path, *extra):
    r = core.scan(path, **dict(extra))
    return r, [(os.path.basename(f.file), f.line, f.rule, f.severity) for f in r["_findings"]]


def cli_run(capsys, *argv):
    code = cli.main(["check", *argv])
    return code, capsys.readouterr().out


def rel(*p):
    return os.path.relpath(os.path.join(*p))


# ---------------- acceptance fixtures (tests/fixtures + Tester's RESULTS.md) ----------------
def test_naive_graph_as101_high_exit_1(capsys):
    r, f = run(rel(FX, "naive_graph.py"))
    assert f == [("naive_graph.py", 9, "AS101", "high")]
    fd = r["_findings"][0]
    assert fd.function == "pay" and fd.effect_call == "payments.charge()" and fd.effect_line == 9
    assert "interrupt()` at L10" in fd.message
    code, _ = cli_run(capsys, "--scan", rel(FX, "naive_graph.py"))
    assert code == 1


def test_idem_graph_clean_no_as104_noise(capsys):
    _, f = run(rel(FX, "idem_graph.py"))
    assert f == []
    assert cli_run(capsys, "--scan", rel(FX, "idem_graph.py"))[0] == 0


def test_safe_graph_only_as103_medium(capsys):
    _, f = run(rel(FX, "safe_graph.py"))
    assert f == [("safe_graph.py", 12, "AS103", "medium")]
    assert cli_run(capsys, "--scan", rel(FX, "safe_graph.py"))[0] == 0


def test_oai_naive_as201_high_and_oai_idem_clean(capsys):
    _, f = run(rel(FX, "oai_naive.py"))
    assert f == [("oai_naive.py", 9, "AS201", "high")]
    assert run(rel(FX, "oai_idem.py"))[1] == []
    assert cli_run(capsys, "--scan", rel(FX, "oai_naive.py"))[0] == 1
    assert cli_run(capsys, "--scan", rel(FX, "oai_idem.py"))[0] == 0


# precision corpus: verbatim copies of the public templates / agent-approval-examples
CORPUS_EXPECTED = {
    "lg_idempotent_approval.py": [("lg_idempotent_approval.py", 27, "AS101", "high")],      # T1 naive nested node; T2 fixed clean
    "oai_idempotent_approval.py": [("oai_idempotent_approval.py", 30, "AS201", "high")],    # T3 naive; T4 key= fixed clean
    "examples_oai_idempotent_approval.py": [("examples_oai_idempotent_approval.py", 32, "AS201", "high")],  # E3
    "hitl_approval.py": [],             # T6
    "multi_interrupt.py": [],           # T7
    "approval_ui_app.py": [],           # T8: send_response / wfile.write plumbing, LOCK + status check
    "v024_01_tool_error_formatter.py": [],   # T10
    "v024_02_approval_resume.py": [],        # T9
    "examples_lg_approval.py": [],      # E1
    "examples_oai_approval.py": [],     # E2
    "ledger.py": [],                    # E4: c.execute INSERT OR IGNORE / SELECT
}


@pytest.mark.parametrize("name", sorted(CORPUS_EXPECTED))
def test_precision_corpus(name):
    r, f = run(rel(SC, "corpus", name))
    assert f == CORPUS_EXPECTED[name]
    # nothing at all below high either (no medium/low noise on the public templates)
    assert all(x.severity == "high" for x in r["_findings"])


def test_nested_node_function_named(capsys):
    r, _ = run(rel(SC, "corpus", "lg_idempotent_approval.py"))
    assert r["_findings"][0].function == "node" and "interrupt()` at L28" in r["_findings"][0].message


@pytest.mark.skipif(not os.path.isdir("/workspace/agent-signal-templates"), reason="public templates checkout not on this box")
def test_live_public_templates_match_results_md():
    r, f = run("/workspace/agent-signal-templates")
    assert sorted((p, l, ru, s) for p, l, ru, s in f) == [
        ("idempotent_approval.py", 27, "AS101", "high"), ("idempotent_approval.py", 30, "AS201", "high")]
    assert any("google-adk" in s["reason"] for s in r["skipped"])
    if os.path.isdir("/workspace/agent-approval-examples"):
        r, f = run("/workspace/agent-approval-examples")
        assert f == [("idempotent_approval.py", 32, "AS201", "high")]


# ---------------- one test per rule ----------------
def test_as102_effect_in_loop_with_interrupt():
    r, f = run(rel(SC, "as102_loop.py"))
    assert f == [("as102_loop.py", 7, "AS102", "high")]


def test_as103_and_as104_post_interrupt_node():
    _, f = run(rel(SC, "as103_as104_node.py"))
    assert f == [("as103_as104_node.py", 17, "AS103", "medium"), ("as103_as104_node.py", 17, "AS104", "medium")]


def test_as104_guard_and_ledger_lookup_suppress_as104_only():
    _, f = run(rel(SC, "as104_guarded.py"))
    assert f == [("as104_guarded.py", 13, "AS103", "medium"), ("as104_guarded.py", 19, "AS103", "medium")]


def test_as105_handler_without_dedupe():
    r, f = run(rel(SC, "as105_handler.py"))
    assert f == [("as105_handler.py", 11, "AS105", "medium")]   # approve_once / approve_checked are deduped
    assert r["_findings"][0].group == "handler"


def test_as202_plain_tool_and_reads_ignored():
    _, f = run(rel(SC, "as202_tool.py"))
    assert f == [("as202_tool.py", 8, "AS202", "medium")]


def test_as203_runstate_resumed_in_handler():
    _, f = run(rel(SC, "as203_handler.py"))
    assert f == [("as203_handler.py", 10, "AS203", "medium")]


def test_as204_low_only_in_json_and_verbose(capsys):
    r, f = run(rel(SC, "as204_not_persisted.py"))
    assert f == [("as204_not_persisted.py", 13, "AS204", "low")]
    code, out = cli_run(capsys, "--scan", rel(SC, "as204_not_persisted.py"))
    assert code == 0 and "AS204" not in out and "1 low-confidence finding hidden" in out
    _, out = cli_run(capsys, "--scan", rel(SC, "as204_not_persisted.py"), "--verbose")
    assert "AS204 low" in out
    _, out = cli_run(capsys, "--scan", rel(SC, "as204_not_persisted.py"), "--format", "json")
    assert json.loads(out)["scan"]["findings"][0]["rule"] == "AS204"


def test_random_key_flagged():
    r, f = run(rel(SC, "random_key.py"))
    assert f == [("random_key.py", 9, "AS201", "high")]
    assert "random per call" in r["_findings"][0].message and "key is random per call" in r["_findings"][0].short


def test_key_kwarg_counts_when_id_derived():
    _, f = run(rel(SC, "key_kwarg.py"))
    # ctx.context[...] + tool_name, tool_call_id and a hash of run_id count; a constant / None key does not
    assert f == [("key_kwarg.py", 26, "AS201", "high"), ("key_kwarg.py", 31, "AS201", "high")]


# ---------------- false-positive guards ----------------
def test_fp_plumbing_ledger_logging_state():
    r, f = run(rel(SC, "fp"))
    assert f == [] and r["files"] == 3


def test_effect_classifier_segments():
    from agentsignal_check.scan.effects import verb_match
    assert verb_match("charge_card") and verb_match("send_email") and verb_match("place_order")
    assert not verb_match("discharge") and not verb_match("get_charge") and not verb_match("lookup_price")


# ---------------- suppression, config, skipping, no-import, speed ----------------
def test_inline_suppression_counted(capsys):
    r, f = run(rel(SC, "suppressed.py"))
    assert f == [("suppressed.py", 18, "AS101", "high")] and r["suppressed"] == 2
    _, out = cli_run(capsys, "--scan", rel(SC, "suppressed.py"))
    assert "2 suppressed" in out


def test_pyproject_ignore_and_effect_names():
    r, f = run(rel(SC, "cfg"))
    assert f == [("agent.py", 13, "AS201", "high")]          # record_sale only via effect_names
    assert r["suppressed"] == 1                               # AS101 ignored by config


def test_effect_names_flag(capsys):
    code, out = cli_run(capsys, "--scan", rel(SC, "as204_not_persisted.py"), "--format", "json")
    assert json.loads(out)["scan"]["findings"][0]["rule"] == "AS204"
    _, f = run(rel(SC, "cfg", "agent.py"), ("effect_names", ["payments.charge"]))
    assert ("agent.py", 7, "AS101", "high") not in f          # config ignore still applies


def test_unparseable_listed_scan_continues(capsys):
    r, f = run(rel(SC, "broken"))
    assert f == [("good.py", 6, "AS101", "high")]
    assert r["skipped"] and "parse error" in r["skipped"][0]["reason"]
    code, out = cli_run(capsys, "--scan", rel(SC, "broken"))
    assert code == 1 and "skipped " in out and "bad_syntax.py" in out


def test_scan_never_imports_user_code():
    # raises_on_import.py raises at import time; a subprocess scan must still succeed and report it
    r = subprocess.run([sys.executable, "-m", "agentsignal_check.cli", "check", "--scan",
                        rel(SC, "raises_on_import.py"), "--format", "json"], capture_output=True, text=True)
    assert r.returncode == 1, r.stderr
    assert "imported user code" not in r.stderr
    assert json.loads(r.stdout)["scan"]["findings"][0]["rule"] == "AS101"
    assert "tests.fixtures.scan.raises_on_import" not in sys.modules


def test_test_files_skipped_unless_include_tests(tmp_path):
    (tmp_path / "test_agent.py").write_text(open(os.path.join(FX, "naive_graph.py")).read())
    assert run(str(tmp_path))[1] == []
    assert run(str(tmp_path), ("include_tests", True))[1] == [("test_agent.py", 9, "AS101", "high")]


def test_exclude_and_default_skip_dirs(tmp_path):
    src = open(os.path.join(FX, "naive_graph.py")).read()
    for d in (".venv", "node_modules", "pkg"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "g.py").write_text(src)
    assert [x[0] for x in run(str(tmp_path))[1]] == ["g.py"]
    assert run(str(tmp_path), ("excludes", ["pkg"]))[1] == []


def test_2k_file_repo_under_3_5s(tmp_path):
    naive = open(os.path.join(FX, "naive_graph.py")).read()
    plain = "import os\n\ndef f(x):\n    return os.path.join(x, 'y')\n" * 20
    for i in range(2000):
        d = tmp_path / f"m{i // 100}"
        d.mkdir(exist_ok=True)
        (d / f"f{i}.py").write_text(naive if i % 20 == 0 else plain)
    t = time.perf_counter()
    r = core.scan(str(tmp_path))
    dt = time.perf_counter() - t
    assert r["files"] == 2000 and len(r["findings"]) == 100
    assert dt < 3.5, dt


def test_2k_file_repo_of_real_framework_files_under_3_5s(tmp_path):
    """Heavier than the synthetic one (Tester's setup): every file is a copy of a real fixture/corpus/scanner
    file, so most of them import langgraph / agents and are fully parsed and checked."""
    import glob, shutil
    srcs = sorted(p for p in glob.glob(os.path.join(FX, "**", "*.py"), recursive=True)
                  if "broken" not in p and "bad_syntax" not in p)
    srcs += sorted(glob.glob(os.path.join(os.path.dirname(core.__file__), "..", "**", "*.py"), recursive=True))
    (tmp_path / ".git").mkdir()
    for i in range(2000):
        d = tmp_path / f"pkg{i // 100}"
        d.mkdir(exist_ok=True)
        shutil.copy(srcs[i % len(srcs)], d / f"m{i}.py")
    t = time.perf_counter()
    r = core.scan(str(tmp_path))
    dt = time.perf_counter() - t
    assert r["files"] == 2000 and r["findings"]
    assert dt < 3.5, dt


# ---------------- CLI contract ----------------
def test_effect_still_required_without_scan(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["check", "--graph", "tests.fixtures.naive_graph:build"])
    assert e.value.code == 2 and "--effect" in capsys.readouterr().err


def test_scan_usage_error_missing_path(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["check", "--scan", rel(SC, "does_not_exist")])
    assert e.value.code == 2


def test_scan_needs_no_effect_or_graph_and_json_alias(capsys):
    code, out = cli_run(capsys, "--scan", rel(FX, "naive_graph.py"), "--json")
    d = json.loads(out)["scan"]
    assert code == 1 and d["rerun_tools"] == 1 and d["fix_url"] == FIX_URL
    assert set(d["findings"][0]) == {"rule", "severity", "confidence", "framework", "function", "file", "line", "col",
                                     "effect_call", "effect_line", "message", "fix_url"}


def test_strict_makes_medium_fail(capsys):
    assert cli_run(capsys, "--scan", rel(FX, "safe_graph.py"), "--strict")[0] == 1


def test_every_result_ends_with_count_and_fix_link(capsys):
    for p in (rel(FX, "naive_graph.py"), rel(SC, "corpus")):
        _, out = cli_run(capsys, "--scan", p)
        last = out.rstrip("\n").splitlines()[-1]
        assert last.endswith(f"will re-run on resume. {FIX_LABEL}: {FIX_URL}"), last
    _, out = cli_run(capsys, "--scan", rel(SC, "corpus"))
    assert "These 3 tools will re-run on resume:" in out and "3 tools will re-run on resume. Fix" in out


def test_clean_scan_ends_with_no_risks_and_no_fix_link(capsys):
    """Tester nit 1: a clean (or low-only) scan ends with the spec's line and carries no fix link."""
    _, out = cli_run(capsys, "--scan", rel(FX, "oai_idem.py"))
    lines = out.rstrip().splitlines()
    assert lines[-1] == "No re-run risks found by static scan."
    assert "1 file scanned." in out and FIX_URL not in out and "re-run on resume" not in out
    assert "safe" not in out.lower()
    d = json.loads(cli_run(capsys, "--scan", rel(FX, "oai_idem.py"), "--json")[1])["scan"]
    assert d["summary"] == "No re-run risks found by static scan." and d["fix_url"] is None and d["rerun_tools"] == 0


def test_low_only_scan_is_clean(capsys):
    _, out = cli_run(capsys, "--scan", rel(SC, "as204_not_persisted.py"))
    assert out.rstrip().splitlines()[-1] == "No re-run risks found by static scan."
    assert "low-confidence finding hidden" in out and FIX_URL not in out


def test_medium_only_says_may(capsys):
    """Tester nit 1: medium-only results say 'may re-run', not 'will'."""
    code, out = cli_run(capsys, "--scan", rel(FX, "safe_graph.py"))
    assert code == 0
    assert "1 tool may re-run on resume:" in out
    assert out.rstrip().splitlines()[-1] == f"1 tool may re-run on resume. {FIX_LABEL}: {FIX_URL}"
    assert " will re-run" not in out
    d = json.loads(cli_run(capsys, "--scan", rel(FX, "safe_graph.py"), "--json")[1])["scan"]
    assert d["summary"].startswith("1 tool may re-run on resume.") and d["fix_url"] == FIX_URL


def test_prove_it_hint_only_names_build_when_it_exists(capsys):
    """Tester nit 2: suggest <module>:build only when that file defines build()."""
    _, out = cli_run(capsys, "--scan", rel(FX, "naive_graph.py"))
    assert "--graph tests.fixtures.naive_graph:build " in out
    for f, flag in (("random_key.py", "--agent"), ("suppressed.py", None), ("raises_on_import.py", None)):
        _, out = cli_run(capsys, "--scan", rel(SC, f))
        hint = [l for l in out.splitlines() if l.startswith("Prove it")]
        if not hint:
            continue
        assert ":build" not in hint[0], hint[0]
        assert "<module:function that returns your" in hint[0], hint[0]
        if flag:
            assert flag in hint[0]


def test_long_paths_do_not_run_into_the_message(capsys):
    """Tester nit 3: a path longer than its column goes on its own line; the message is wrapped below it."""
    _, out = cli_run(capsys, "--scan", rel(SC, "corpus"))
    rows = [l for l in out.splitlines() if l.startswith("  ") and ".py:" in l]
    assert rows
    for l in rows:
        path = next(t for t in l.split() if ".py:" in t)
        after = l.split(path, 1)[1]
        assert after == "" or after.startswith(" "), l           # never glued to the message
    lines = out.splitlines()
    i = next(n for n, l in enumerate(lines) if "lg_idempotent_approval.py:27" in l)
    assert lines[i].rstrip().endswith("lg_idempotent_approval.py:27")
    assert "api.charge() runs before interrupt()" in lines[i + 1] and lines[i + 1].startswith(" " * 10)


def test_golden_terminal_and_json(capsys):
    p = rel(FX, "naive_graph.py")
    _, out = cli_run(capsys, "--scan", p)
    from agentsignal_check.scan import versions
    lg = versions.langgraph_version(p)        # this repo's own pin (uv.lock), the same lookup as AS106-108
    sub = lambda t: (t.replace("{path}", p).replace("{lgver}", lg.version).replace("{fix}", FIX_URL)
                     .replace("{fixlabel}", FIX_LABEL)
                     .replace("{lgsrc}", os.path.relpath(lg.source, os.path.dirname(os.path.abspath(p))).replace(os.sep, "/")))
    assert out == sub(open(os.path.join(SC, "golden", "naive_graph.txt")).read())
    _, out = cli_run(capsys, "--scan", p, "--format", "json")
    golden = json.loads(sub(open(os.path.join(SC, "golden", "naive_graph.json")).read()))
    assert json.loads(out) == golden


def test_scan_plus_crash_test_merges_report(capsys, tmp_path, monkeypatch):
    pytest.importorskip("langgraph.checkpoint.sqlite")
    monkeypatch.setenv("AS_SCENARIO", str(tmp_path / "s"))
    code, out = cli_run(capsys, "--scan", rel(FX, "naive_graph.py"), "--graph", "tests.fixtures.naive_graph:build",
                        "--effect", "tests.fixtures.payments:charge", "--input", '{"amount": 5}', "--json")
    d = json.loads(out)
    assert code == 1 and d["scan"]["findings"][0]["rule"] == "AS101" and d["results"]
