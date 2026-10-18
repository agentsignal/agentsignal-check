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
