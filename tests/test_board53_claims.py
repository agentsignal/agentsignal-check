"""Board 53 claims + nits (Tester @ 1b7159c): C2 version-group wording + pin note, C3 unpinned, C4 /fix/ wording,
C5 Pro key hint, nits: root-relative version source, grouped header, non-empty WARN/ERROR details, replay SKIP."""
import json, os, shutil, subprocess, sys
import pytest

from agentsignal_check.scan import core, versions

HERE = os.path.dirname(os.path.abspath(__file__))
V = os.path.join(HERE, "fixtures", "scan", "versioned")
SUB = os.path.join(HERE, "fixtures", "subgraph_parallel_graph.py")


def _repo(tmp_path, src, files):
    (tmp_path / ".git").mkdir(exist_ok=True)
    shutil.copy(src, tmp_path / os.path.basename(src))
    for name, body in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    r = core.scan(str(tmp_path))
    return r, core.render_text(r)


# ---- C2: "may hit" unless every version finding is high; the pin note is printed in the terminal ----
@pytest.mark.parametrize("files", [{}, {"requirements.txt": "langgraph\n"},
                                   {"pyproject.toml": '[project]\nname="x"\ndependencies=["langgraph>=1.0"]\n'}])
def test_unknown_version_says_may_hit_and_prints_pin_note(tmp_path, files):
    _, out = _repo(tmp_path, os.path.join(V, "as107_update_twice.py"), files)
    assert "for your version" not in out, out
    assert ("1 place may hit a known langgraph bug:" in out) or ("places may hit known langgraph bugs:" in out), out
    assert core.VERSION_NOTE in out and "pin langgraph>=1.2.16" in out


def test_affected_pin_says_hits_for_your_version(tmp_path):
    _, out = _repo(tmp_path, os.path.join(V, "as107_update_twice.py"), {"requirements.txt": "langgraph==1.2.15\n"})
    assert "hits a known langgraph bug for your version:" in out or "hit known langgraph bugs for your version:" in out, out
    assert core.VERSION_NOTE in out


def test_as106_in_rerun_group_also_prints_pin_note(tmp_path):
    _, out = _repo(tmp_path, SUB, {})
    assert "AS106 medium" in out and core.VERSION_NOTE in out


# ---- C3: a bare `langgraph` line is "unpinned", spec null ----
@pytest.mark.parametrize("name,body", [("requirements.txt", "langgraph\n"),
                                       ("requirements.txt", "langgraph[extra]  # no pin\n"),
                                       ("pyproject.toml", '[tool.poetry.dependencies]\nlanggraph = "*"\n')])
def test_bare_langgraph_is_unpinned_spec_null(tmp_path, name, body):
    r, out = _repo(tmp_path, SUB, {name: body})
    head = out.splitlines()[0]
    assert head.endswith(f"detected: langgraph unpinned ({name})"), head
    fw = core.public(r)["frameworks"]["langgraph"]
    assert fw == {"files": 1, "version": None, "spec": None, "source": name}, fw
    assert "any" not in json.dumps(fw)
    assert any(f"langgraph unpinned ({name}) may be affected" in f.message for f in r["_findings"])


def test_unpinned_classifies_unknown():
    v = versions._from_req_lines(["langgraph"], "requirements.txt")
    assert (v.status, v.version, v.spec, v.what) == ("unknown", None, None, "unpinned")


# ---- nit: version source relative to the scan root, not the cwd ----
def test_version_source_is_relative_to_scan_root(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir()
    (proj / "app").mkdir()
    shutil.copy(SUB, proj / "app" / "g.py")
    (proj / "app" / "requirements.txt").write_text("langgraph==1.2.15\n")
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    r = core.scan(str(proj))
    fw = core.public(r)["frameworks"]["langgraph"]
    assert fw["source"] == "app/requirements.txt", fw
    r2 = core.scan(str(proj / "app" / "g.py"))       # a single file: relative to its directory
    assert core.public(r2)["frameworks"]["langgraph"]["source"] == "requirements.txt"


# ---- nit: identical versions in several pin files are listed once ----
def test_header_groups_identical_versions(tmp_path):
    (tmp_path / ".git").mkdir()
    for sub, pin in (("a", "1.2.15"), ("b", "1.2.15"), ("c", "1.2.15"), ("d", "1.2.15"), ("e", "1.2.16")):
        (tmp_path / sub).mkdir()
        shutil.copy(SUB, tmp_path / sub / "g.py")
        (tmp_path / sub / "requirements.txt").write_text(f"langgraph=={pin}\n")
    r = core.scan(str(tmp_path))
    head = core.render_text(r).splitlines()[0]
    assert head.count("1.2.15") == 1 and head.count("1.2.16") == 1, head
    assert "1.2.15 (a/requirements.txt, b/requirements.txt, c/requirements.txt, +1 more)" in head, head
    assert "1.2.16 (e/requirements.txt)" in head, head
    assert len(core.public(r)["frameworks"]["langgraph"]["versions"]) == 5     # JSON keeps one entry per pin file


# ---- C4: the markdown report promises only what /fix/ promises ----
def test_report_fix_line_matches_fix_page():
    from agentsignal_check.report import render
    md = render({"framework": "langgraph", "results": [{"detector": "baseline", "status": "FAIL", "detail": "x"}]}, "t")
    assert "re-run this check" not in md
    assert "a tested patch for your approval flow within 48 hours, with a short test log, or a full refund" in md


# ---- C5: the --upload key hint says it's Pro ----
def test_upload_key_hint_is_labelled_pro():
    from agentsignal_check import upload
    assert "Upload needs a Signal Pro API key: https://tryagentsignal.com/pro/keys" in upload.NO_KEY_HINT


def test_fail_fix_links_are_never_pro_pages():
    from agentsignal_check import links
    for fw in (None, "langgraph", "openai-agents"):
        for det in ("baseline", "double_execution.kill_resume", "double_execution.kill_before_checkpoint",
                    "approval.lost_on_restart", "approval.replay"):
            assert "/pro/" not in links.fix_url(det, fw)


# ---- nit: WARN/ERROR details are never empty ----
def test_signal_and_exit_details_say_what_happened():
    from agentsignal_check.detectors import langgraph_crash as lc
    sig = subprocess.CompletedProcess([], -11, "", "")
    ext = subprocess.CompletedProcess([], 3, "", "")
    assert lc.failed(sig, "resume failed") == "resume failed (rc=-11 SIGSEGV): child killed by a signal (rc=-11 SIGSEGV) with no error output"
    assert lc.failed(ext, "resume failed") == "resume failed (rc=3): child exited (rc=3) with no error output"
    assert lc.failed(subprocess.CompletedProcess([], 1, "", "Traceback\nValueError: boom\n"), "x") == "x (rc=1): ValueError: boom"


def test_resume_killed_by_signal_warn_detail(monkeypatch):
    pytest.importorskip("langgraph.checkpoint.sqlite")
    from agentsignal_check.detectors import langgraph_crash as lc
    real = lc._child
    def child(mode, *a, **k):
        r = real(mode, *a, **k)
        if mode == "resume":
            r.returncode, r.stderr = -11, ""
        if mode == "replay":
            r.returncode, r.stdout, r.stderr = 3, "", ""
        return r
    monkeypatch.setattr(lc, "_child", child)
    rep = lc.run("tests.fixtures.idem_graph:build", ["tests.fixtures.payments:charge"], {"amount": 7}, True)
    by = {r["detector"]: r for r in rep["results"]}
    ke = by["double_execution.kill_resume"]
    assert ke["status"] == "WARN" and "resume failed (rc=-11 SIGSEGV)" in ke["detail"] and "[]" not in ke["detail"], ke
    assert by["approval.replay"]["status"] == "ERROR" and "rc=3" in by["approval.replay"]["detail"]
    # both rows from the replay child are reported, never dropped
    assert by["approval.lost_on_restart"]["status"] == "ERROR" and "rc=3" in by["approval.lost_on_restart"]["detail"]


# ---- nit: approval.replay is SKIP with a reason when the graph never interrupts ----
def test_no_interrupt_graph_reports_replay_skip():
    pytest.importorskip("langgraph.checkpoint.sqlite")
    from agentsignal_check.detectors.langgraph_crash import run
    from agentsignal_check.cli import exit_code
    rep = run("tests.fixtures.noint_graph:build", ["tests.fixtures.payments:charge"], {"amount": 5}, True)
    by = {r["detector"]: r for r in rep["results"]}
    assert by["approval.replay"]["status"] == "SKIP" and "never interrupted" in by["approval.replay"]["detail"]
    assert by["approval.lost_on_restart"]["status"] == "SKIP"
    assert {by[d]["status"] for d in ("baseline", "double_execution.kill_resume",
                                      "double_execution.kill_before_checkpoint")} == {"PASS"}, rep
    assert exit_code(rep) == 0


def test_skip_rows_print_no_fix_link():
    from agentsignal_check import card
    import io
    rep = {"framework": "langgraph", "results": [{"detector": "approval.replay", "status": "SKIP", "detail": "graph never interrupted"}]}
    f = io.StringIO()
    card.print_card(rep, "t", file=f)
    assert "[SKIP] approval.replay" in f.getvalue() and "fix:" not in f.getvalue()
    assert "fix →" not in card.render_html(rep, "t")


def test_version_is_0_4_0():
    import agentsignal_check
    assert agentsignal_check.__version__ == "0.4.0"
