"""Version-aware scan rules for langgraph bugs fixed in 1.2.16: AS106 (#9315), AS107 (#9322), AS108 (#9314).
High only when the project pins/resolves langgraph < 1.2.16; medium with a pin note when no version is found;
nothing when it requires >= 1.2.16. AS101 stays unconditional."""
import os
import shutil

import pytest

from agentsignal_check.scan import core, versions

HERE = os.path.dirname(os.path.abspath(__file__))
FX = os.path.join(HERE, "fixtures")
V = os.path.join(FX, "scan", "versioned")
NOTE = "fixed in 1.2.16; pin >=1.2.16"


def project(tmp_path, src, files=None):
    """A tiny repo: one graph file + the given project files; .git stops the upward version search."""
    (tmp_path / ".git").mkdir()
    shutil.copy(src, tmp_path / os.path.basename(src))
    for name, body in (files or {}).items():
        (tmp_path / name).write_text(body)
    r = core.scan(str(tmp_path))
    return [(f.rule, f.severity, f.line, f.message) for f in r["_findings"]]


def rules(found, rule):
    return [(sev, line) for r, sev, line, _ in found if r == rule]


SUB = os.path.join(FX, "subgraph_parallel_graph.py")
UV_LOCK = '''version = 1
[[package]]
name = "langgraph"
version = "{v}"
[[package]]
name = "langgraph-checkpoint"
version = "9.9.9"
'''


@pytest.mark.parametrize("files,expected", [
    ({"requirements.txt": "langgraph==1.2.15\n"}, [("high", 18)]),
    ({"requirements.txt": "langgraph-checkpoint==9.0\nlanggraph<1.2.16\n"}, [("high", 18)]),
    ({"uv.lock": UV_LOCK.format(v="1.2.15"), "requirements.txt": "langgraph>=1.2.16\n"}, [("high", 18)]),  # lock wins
    ({"pyproject.toml": '[project]\nname="x"\ndependencies=["langgraph[foo]>=1.1,<1.2.16"]\n'}, [("high", 18)]),
    ({"requirements.txt": "langgraph==1.2.16\n"}, []),
    ({"uv.lock": UV_LOCK.format(v="1.2.17")}, []),
    ({"pyproject.toml": '[project]\nname="x"\ndependencies=["langgraph>=1.2.16"]\n'}, []),
    ({"pyproject.toml": '[project]\nname="x"\ndependencies=["langgraph>=1.0"]\n'}, [("medium", 18)]),   # range: unknown
    ({}, [("medium", 18)]),                                                                            # no version
])
def test_as106_9315_severity_follows_version(tmp_path, files, expected):
    found = project(tmp_path, SUB, files)
    assert rules(found, "AS106") == expected
    for r, sev, _, msg in found:
        if r == "AS106":
            assert "langgraph#9315" in msg and NOTE in msg
            if sev == "high":
                assert "is affected" in msg


def test_no_version_found_medium_note_exact(tmp_path):
    found = project(tmp_path, SUB)
    msg = next(m for r, _, _, m in found if r == "AS106")
    assert msg.endswith(f"{NOTE}.")


def test_as107_9322(tmp_path):
    f = os.path.join(V, "as107_update_twice.py")
    for sub, files, exp in [("a", {"requirements.txt": "langgraph==1.2.15\n"}, [("high", 17)]),
                            ("b", {"requirements.txt": "langgraph==1.2.16\n"}, []),
                            ("c", {}, [("medium", 17)])]:
        (tmp_path / sub).mkdir()
        assert rules(project(tmp_path / sub, f, files), "AS107") == exp, sub


def test_as108_9314_needs_three_parallel_interrupts(tmp_path):
    for sub, name, files, exp in [
        ("a", "as108_durability_exit.py", {"requirements.txt": "langgraph==1.2.10\n"}, [("high", 21)]),
        ("b", "as108_durability_exit.py", {}, [("medium", 21)]),
        ("c", "as108_durability_exit.py", {"requirements.txt": "langgraph>=1.2.16\n"}, []),
        ("d", "as108_two_only.py", {"requirements.txt": "langgraph==1.2.10\n"}, []),
    ]:
        (tmp_path / sub).mkdir()
        assert rules(project(tmp_path / sub, os.path.join(V, name), files), "AS108") == exp, (sub, name)


def test_as101_stays_unconditional_on_fixed_version(tmp_path):
    found = project(tmp_path, os.path.join(FX, "naive_graph.py"), {"requirements.txt": "langgraph==1.2.16\n"})
    assert ("AS101", "high", 9) in [(r, s, l) for r, s, l, _ in found]


def test_repo_fixture_uses_repo_uv_lock():
    lock = os.path.join(os.path.dirname(HERE), "uv.lock")
    v = versions.langgraph_version(SUB)
    assert v.source == lock and v.version
    r = core.scan(os.path.relpath(SUB))
    exp = "high" if versions.classify_exact(v.version) == "affected" else None
    got = [f.severity for f in r["_findings"] if f.rule == "AS106"]
    assert got == ([exp] if exp else [])


@pytest.mark.parametrize("spec,status", [
    ("==1.2.15", "affected"), ("==1.2.16", "fixed"), ("==1.0.5", "unknown"), (">=1.2.16", "fixed"),
    (">=1.0", "unknown"), ("<1.2.16", "affected"), ("<=1.2.15", "affected"), ("<=1.2.16", "unknown"),
    (">=1.1,<1.2.16", "affected"), ("~=1.2.10", "unknown"), ("~=1.1", "unknown"), ("==1.2.*", "unknown"),
    ("", "unknown"), (">1.2.15", "unknown"),
])
def test_classify_spec(spec, status):
    assert versions.classify_spec(spec) == status


def test_precision_corpus_unaffected_by_version_rules():
    r = core.scan(os.path.relpath(os.path.join(FX, "scan", "corpus")))
    assert not [f for f in r["_findings"] if f.rule in ("AS106", "AS107", "AS108")]


# ---- header / JSON version = the scanned project's, never the scanner's install (Tester board 53 FAIL) ----
def _scan_out(tmp_path, files):
    from agentsignal_check.scan import core as c
    (tmp_path / ".git").mkdir(exist_ok=True)
    shutil.copy(SUB, tmp_path / "g.py")
    for name, body in files.items():
        (tmp_path / name).write_text(body)
    r = c.scan(str(tmp_path))
    return r, c.render_text(r), c.public(r)["frameworks"]["langgraph"]


def test_header_shows_project_pin(tmp_path):
    r, out, fw = _scan_out(tmp_path, {"requirements.txt": "langgraph==1.2.15\n"})
    head = out.splitlines()[0]
    assert head.endswith("detected: langgraph 1.2.15 (requirements.txt)"), head
    assert fw == {"files": 1, "version": "1.2.15", "spec": None, "source": os.path.relpath(tmp_path / "requirements.txt")}
    # consistent with the version-aware rules' own wording
    assert any("langgraph 1.2.15 (requirements.txt) is affected" in f.message for f in r["_findings"])


def test_project_pinned_1_2_15_scanned_from_a_1_2_16_env(tmp_path):
    """The CI Action case: the scanner's env has langgraph 1.2.16, the project pins 1.2.15. Run through the CLI
    in a subprocess with a real 1.2.16 install when available (uv run --with langgraph==1.2.16), and also fake it
    via importlib.metadata so the test is meaningful on the 1.2.15 lock too."""
    import json as _j, subprocess, sys
    (tmp_path / ".git").mkdir(); shutil.copy(SUB, tmp_path / "g.py")
    (tmp_path / "requirements.txt").write_text("langgraph==1.2.15\n")
    fake = ("import importlib.metadata as m, sys; _v = m.version;"
            "m.version = lambda p: '1.2.16' if p == 'langgraph' else _v(p);"
            "from agentsignal_check.cli import main; sys.exit(main(sys.argv[1:]))")
    for argv in ([sys.executable, "-m", "agentsignal_check.cli"], [sys.executable, "-c", fake]):
        for fmt in ([], ["--json"]):
            r = subprocess.run(argv + ["check", "--scan", ".", *fmt], capture_output=True, text=True, cwd=str(tmp_path))
            if fmt:
                fw = _j.loads(r.stdout)["scan"]["frameworks"]["langgraph"]
                assert fw["version"] == "1.2.15" and fw["source"] == "requirements.txt", fw
                assert "1.2.16" not in _j.dumps(_j.loads(r.stdout)["scan"]["frameworks"])
            else:
                head = r.stdout.splitlines()[0]
                assert "langgraph 1.2.15 (requirements.txt)" in head and "1.2.16" not in head, head


def test_header_range_spec(tmp_path):
    _, out, fw = _scan_out(tmp_path, {"pyproject.toml": '[project]\nname="x"\ndependencies=["langgraph>=1.0"]\n'})
    assert "langgraph >=1.0 (pyproject.toml)" in out.splitlines()[0]
    assert fw["version"] is None and fw["spec"] == ">=1.0" and fw["source"].endswith("pyproject.toml")


def test_header_no_pin_says_unknown_never_installed(tmp_path):
    from importlib.metadata import version
    _, out, fw = _scan_out(tmp_path, {})
    head = out.splitlines()[0]
    assert head.endswith("detected: langgraph version unknown (no pin found)"), head
    assert version("langgraph") not in head
    assert fw == {"files": 1, "version": None, "spec": None, "source": None}


def test_header_lists_each_distinct_project_version(tmp_path):
    from agentsignal_check.scan import core as c
    (tmp_path / ".git").mkdir()
    for sub, pin in (("a", "1.2.15"), ("b", "1.2.16")):
        (tmp_path / sub).mkdir()
        shutil.copy(SUB, tmp_path / sub / "g.py")
        (tmp_path / sub / "requirements.txt").write_text(f"langgraph=={pin}\n")
    r = c.scan(str(tmp_path))
    head = c.render_text(r).splitlines()[0]
    assert "1.2.15 (" in head and "1.2.16 (" in head and "a/requirements.txt" in head and "b/requirements.txt" in head
    assert len(c.public(r)["frameworks"]["langgraph"]["versions"]) == 2
