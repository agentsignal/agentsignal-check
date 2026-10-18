"""--upload against a local mock HTTP server (no network)."""
import io, json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import pytest
from agentsignal_check import upload as up

from tests import contract

REPORT = {"framework": "langgraph", "results": [{"detector": "baseline", "status": "FAIL", "detail": "x"},
          {"detector": "approval.replay", "status": "WARN", "detail": "y"},
          {"detector": "approval.lost_on_restart", "status": "PASS", "detail": "z"}],
          "env": {"packages": {"langgraph": "1.2.15"}}}
FAKE_KEY = "as_live_" + "T" * 43   # shape-valid, not a real key


class Mock:
    """Contract-checking server. status=None -> behave like the real endpoint (401/413/400/201)."""
    def __init__(self, status=None, body=None, headers=None):
        self.status, self.body, self.headers, self.seen = status, body, headers or {}, []
        mock = self
        class H(BaseHTTPRequestHandler):
            def reply(self, code, obj, extra=None):
                payload = json.dumps(obj).encode()
                self.send_response(code)
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers(); self.wfile.write(payload)
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0)); raw = self.rfile.read(n)
                try:
                    b = json.loads(raw)
                except ValueError:
                    b = None
                errs = contract.validate_run(b) if b is not None else ["invalid_json"]
                mock.seen.append({"path": self.path, "auth": self.headers.get("Authorization"),
                                  "ctype": self.headers.get("Content-Type"), "body": b, "errors": errs})
                if mock.status is not None:
                    return self.reply(mock.status, mock.body if mock.body is not None else {}, mock.headers)
                if not contract.bearer_key(self.headers.get("Authorization")):
                    return self.reply(401, {"error": "invalid_key"})
                if n > contract.MAX_BODY:
                    return self.reply(413, {"error": "payload_too_large", "max_bytes": contract.MAX_BODY})
                if b is None:
                    return self.reply(400, {"error": "invalid_json"})
                if errs:
                    return self.reply(400, {"error": "invalid_body", "errors": errs})
                self.reply(201, {"run_id": "run_123", "fix_links": contract.fix_links(b["report"])})
            def log_message(self, *a): pass
        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}/api/check/runs/"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
    def close(self): self.srv.shutdown(); self.srv.server_close()


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AGENTSIGNAL_API_KEY", FAKE_KEY)
    return monkeypatch


def run_up(mock, env):
    env.setenv("AGENTSIGNAL_API_URL", mock.url)
    out = io.StringIO()
    try:
        res = up.upload(REPORT, out=out, timeout=5)
    finally:
        mock.close()
    return res, out.getvalue()


def test_success_body_matches_contract_and_prints_links(env):
    env.setenv("GITHUB_REPOSITORY", "acme/agent"); env.setenv("GITHUB_SHA", "A" * 40)
    m = Mock()
    (ok, rid), out = run_up(m, env)
    seen = m.seen[0]
    assert ok and rid == "run_123", out
    assert seen["errors"] == [] and seen["path"] == "/api/check/runs/"
    assert seen["auth"] == f"Bearer {FAKE_KEY}" and seen["ctype"] == "application/json"
    b = seen["body"]
    assert set(b) == {"report", "repo", "commit_sha", "framework", "version"}
    assert b["repo"] == "acme/agent" and b["commit_sha"] == "a" * 40
    assert b["framework"] == "langgraph" and b["version"] == "1.2.15"
    assert [r["status"] for r in b["report"]["results"]] == ["FAIL", "FAIL", "PASS"]  # WARN -> FAIL
    # the server mirror (tests/contract.py = prod lib/apikeys.js) still returns the Pro template for baseline;
    # the CLI prints its own free link instead
    assert "baseline  FAIL  https://tryagentsignal.com/guides/langgraph-interrupt-resume-twice/" in out
    assert "idempotent-approval" not in out
    assert "approval.replay  FAIL  https://tryagentsignal.com/templates/langgraph-approval-ui/" in out
    assert "lost_on_restart" not in out and FAKE_KEY not in out


def test_status_mapping():
    assert [up.map_status(s) for s in ["PASS", "FAIL", "ERROR", "SKIP", "WARN", "weird", None]] == \
        ["PASS", "FAIL", "ERROR", "SKIP", "FAIL", "ERROR", "ERROR"]


def test_repo_commit_from_git_when_no_github_env(monkeypatch, tmp_path):
    import subprocess
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False); monkeypatch.delenv("GITHUB_SHA", raising=False)
    g = lambda *a: subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True, text=True).stdout.strip()
    g("init", "-q"); g("remote", "add", "origin", "https://github.com/acme/agent-x.git")
    g("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "--allow-empty", "-m", "x")
    monkeypatch.chdir(tmp_path)
    assert up.detect_repo_commit() == ("acme/agent-x", g("rev-parse", "HEAD"))
    assert up._repo_from_remote("git@github.com:acme/agent.git") == "acme/agent"


def test_no_repo_outside_git(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False); monkeypatch.delenv("GITHUB_SHA", raising=False)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent)); monkeypatch.chdir(tmp_path)
    b = up.build_body(REPORT)
    assert "repo" not in b and "commit_sha" not in b and contract.validate_run(b) == []


def test_real_server_style_errors(env):
    env.setenv("AGENTSIGNAL_API_KEY", "not_a_real_key")
    (ok, why), out = run_up(Mock(), env)
    assert why == "http-401" and "invalid_key" in out


def test_400_invalid_body_lists_errors(env):
    m = Mock(400, {"error": "invalid_body", "errors": ["report.results must be an array of 1-100 items"]})
    (ok, why), out = run_up(m, env)
    assert why == "http-400" and "invalid_body" in out and "1-100 items" in out


@pytest.mark.parametrize("code,needle", [(401, "invalid_key"), (403, "pro_required"), (413, "payload_too_large"),
                                         (429, "rate_limited"), (500, "HTTP 500")])
def test_http_errors(env, code, needle):
    m = Mock(code, {"error": "x"}, {"Retry-After": "120"} if code == 429 else None)
    (ok, why), out = run_up(m, env)
    assert not ok and why == f"http-{code}" and needle in out and FAKE_KEY not in out
    if code == 429:
        assert "Retry after 120s" in out


def test_network_error(env):
    env.setenv("AGENTSIGNAL_API_URL", "http://127.0.0.1:9/api/check/runs")  # discard port, refused
    out = io.StringIO()
    ok, why = up.upload(REPORT, out=out, timeout=3)
    assert not ok and why == "network" and "network error" in out.getvalue()


def test_no_key_hint(monkeypatch):
    monkeypatch.delenv("AGENTSIGNAL_API_KEY", raising=False)
    out = io.StringIO()
    assert up.upload(REPORT, out=out) == (False, "no-key")
    assert out.getvalue().strip() == up.NO_KEY_HINT and out.getvalue().count("\n") == 1


def test_client_side_size_cap(env):
    env.setenv("AGENTSIGNAL_API_URL", "http://127.0.0.1:9/x")
    out = io.StringIO()
    ok, why = up.upload({"framework": "langgraph", "results": [{"detector": "baseline", "status": "FAIL", "detail": "a" * (300 * 1024)}]}, out=out)
    assert not ok and why == "too-large"


# ---- CLI wiring (needs the langgraph extra; skipped otherwise via importorskip) ----
EFF = "tests.fixtures.payments:charge"

def _cli(args, capsys):
    pytest.importorskip("langgraph.checkpoint.sqlite")
    from agentsignal_check.cli import main
    code = main(["check", "--effect", EFF, "--input", '{"amount":5}', "--json"] + args)
    return code, capsys.readouterr()


def test_cli_no_key_without_upload_is_unchanged(monkeypatch, capsys):
    monkeypatch.delenv("AGENTSIGNAL_API_KEY", raising=False)
    code, cap = _cli(["--graph", "tests.fixtures.idem_graph:build"], capsys)
    assert code == 0 and cap.err == "" and json.loads(cap.out)["results"]


def test_cli_no_key_with_upload_one_line_hint(monkeypatch, capsys):
    monkeypatch.delenv("AGENTSIGNAL_API_KEY", raising=False)
    code, cap = _cli(["--graph", "tests.fixtures.idem_graph:build", "--upload"], capsys)
    assert code == 0 and cap.err == up.NO_KEY_HINT + "\n"
    json.loads(cap.out)  # stdout stays pure JSON


def test_cli_upload_failure_keeps_exit_code(env, capsys):
    m = Mock(401, {"error": "bad key"}); env.setenv("AGENTSIGNAL_API_URL", m.url)
    try:
        code, cap = _cli(["--graph", "tests.fixtures.idem_graph:build", "--upload"], capsys)
        code_n, _ = _cli(["--graph", "tests.fixtures.naive_graph:build", "--upload"], capsys)
    finally:
        m.close()
    assert code == 0 and code_n == 1 and "401" in cap.err
    json.loads(cap.out)


def test_cli_upload_strict_fails_on_upload_error(env, capsys):
    m = Mock(429, {}); env.setenv("AGENTSIGNAL_API_URL", m.url)
    try:
        code, cap = _cli(["--graph", "tests.fixtures.idem_graph:build", "--upload", "--upload-strict"], capsys)
    finally:
        m.close()
    assert code == 3 and "rate limit" in cap.err


def test_cli_upload_success_posts_full_json(env, capsys):
    m = Mock(); env.setenv("AGENTSIGNAL_API_URL", m.url)
    try:
        code, cap = _cli(["--graph", "tests.fixtures.naive_graph:build", "--upload"], capsys)
    finally:
        m.close()
    assert code == 1 and "run_123" in cap.err, cap.err
    rep = json.loads(cap.out)
    assert m.seen[0]["errors"] == [] and m.seen[0]["body"]["report"]["results"] == rep["results"]
    assert m.seen[0]["body"]["report"]["env"] == rep["env"]
    assert "double_execution.kill_resume  FAIL  https://" in cap.err
