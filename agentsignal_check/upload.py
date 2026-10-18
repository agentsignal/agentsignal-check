"""--upload: POST the --json report to Agent Signal (Pro). Stdlib only."""
import json, os, re, subprocess, sys, urllib.request, urllib.error

DEFAULT_URL = "https://tryagentsignal.com/api/check/runs/"
MAX_BYTES = 256 * 1024
NO_KEY_HINT = ("agentsignal: --upload skipped: AGENTSIGNAL_API_KEY is not set "
               "(create a key at https://tryagentsignal.com/pro/keys)")


STATUSES = ("PASS", "FAIL", "ERROR", "SKIP")
_STATUS_MAP = {"WARN": "FAIL"}  # anything else unknown -> ERROR
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def map_status(s):
    s = str(s or "").upper()
    return s if s in STATUSES else _STATUS_MAP.get(s, "ERROR")


def _git(*args):
    try:
        r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _repo_from_remote(url):
    m = re.search(r"[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$", url or "")
    return m.group(1) if m else ""


def detect_repo_commit():
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip() or _repo_from_remote(_git("remote", "get-url", "origin"))
    sha = os.environ.get("GITHUB_SHA", "").strip().lower() or _git("rev-parse", "HEAD").lower()
    return (repo if _REPO_RE.match(repo) and len(repo) <= 200 else None,
            sha if _SHA_RE.match(sha) else None)


def build_body(report):
    """Body for POST /api/check/runs/: {report:{framework, results, env}, repo?, commit_sha?, framework?, version?}."""
    results = []
    for r in report.get("results", [])[:100]:
        item = {"detector": str(r.get("detector", ""))[:100], "status": map_status(r.get("status"))}
        if r.get("detail") is not None:
            item["detail"] = str(r["detail"])
        results.append(item)
    fw = report.get("framework")
    rep = {"results": results}
    if fw:
        rep["framework"] = str(fw)[:64]
    if report.get("env") is not None:
        rep["env"] = report["env"]
    body = {"report": rep}
    if fw:
        body["framework"] = str(fw)[:64]
        pkgs = (report.get("env") or {}).get("packages") or {}
        ver = pkgs.get(fw) if isinstance(pkgs, dict) else None
        if isinstance(ver, str) and ver:
            body["version"] = ver[:64]
    repo, sha = detect_repo_commit()
    if repo:
        body["repo"] = repo
    if sha:
        body["commit_sha"] = sha
    return body


def upload(report, out=sys.stderr, timeout=15):
    """Return (ok, message). Never raises; never prints the key."""
    key = os.environ.get("AGENTSIGNAL_API_KEY", "").strip()
    if not key:
        print(NO_KEY_HINT, file=out)
        return False, "no-key"
    url = os.environ.get("AGENTSIGNAL_API_URL", "").strip() or DEFAULT_URL
    body = json.dumps(build_body(report)).encode()
    if len(body) > MAX_BYTES:
        print(f"agentsignal: upload failed: report is {len(body)} bytes, limit is {MAX_BYTES}", file=out)
        return False, "too-large"
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "User-Agent": "agentsignal-check"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read() or b"{}")
        except ValueError:
            err = {}
        code_name = err.get("error", "") if isinstance(err, dict) else ""
        msgs = {401: "invalid or revoked API key (401 invalid_key). Check AGENTSIGNAL_API_KEY.",
                403: "this key's account has no active Signal Pro plan (403 pro_required).",
                413: f"report too large for the server (413 payload_too_large, limit {MAX_BYTES} bytes).",
                429: "rate limit hit (429 rate_limited, 60 uploads/hour per key)."}
        msg = msgs.get(e.code, f"server returned HTTP {e.code}{' ' + code_name if code_name else ''}.")
        if e.code == 429:
            ra = e.headers.get("Retry-After")
            if ra:
                msg += f" Retry after {ra}s."
        if e.code == 400:
            errs = err.get("errors") if isinstance(err, dict) else None
            msg = f"server rejected the report (400 {code_name or 'bad request'})"
            msg += (": " + "; ".join(map(str, errs[:5]))) if errs else "."
        print(f"agentsignal: upload failed: {msg}", file=out)
        return False, f"http-{e.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        print(f"agentsignal: upload failed: network error reaching {url}: {reason}", file=out)
        return False, "network"
    except ValueError:
        print("agentsignal: upload failed: server response was not JSON", file=out)
        return False, "bad-response"
    print(f"agentsignal: uploaded run {data.get('run_id', '?')}", file=out)
    # The printed link always comes from links.py (free pages only, Elon 2026-10-18), not the server's `url`:
    # the server table can lag the CLI (prod still maps double execution to Pro templates).
    from .links import fix_url
    fw = report.get("framework") if isinstance(report, dict) else None
    for l in data.get("fix_links") or []:
        if isinstance(l, dict):
            det = l.get("detector", "?")
            link = fix_url(det, fw) if isinstance(det, str) and det != "?" else ""
            print(f"  {det}  {l.get('status', '?')}  {link}".rstrip(), file=out)
    return True, data.get("run_id")
