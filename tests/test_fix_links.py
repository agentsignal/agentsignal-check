import io, os, urllib.request
import pytest
from agentsignal_check.links import FIX_URLS, fix_url
from agentsignal_check import report as md, card

DETS = ["baseline", "double_execution.kill_resume", "double_execution.kill_before_checkpoint",
        "approval.lost_on_restart", "approval.replay"]
def rep(fw):
    return {"framework": fw, "results": [{"detector": d, "status": "FAIL", "detail": "x"} for d in DETS]}

DOUBLE = ["baseline", "double_execution.kill_resume", "double_execution.kill_before_checkpoint"]


def test_no_link_points_at_a_pro_template():
    """Elon 2026-10-18: every FAIL / fix link the CLI prints points at a free page, never a Pro template."""
    from agentsignal_check.links import PRO_TEMPLATES, SCAN_FIX_URL, scan_fix_url
    urls = {u for m in FIX_URLS.values() for u in m.values()} | {SCAN_FIX_URL, scan_fix_url(), fix_url("x.y")}
    for u in urls:
        assert not any(f"/{slug}/" in u for slug in PRO_TEMPLATES), u


def test_double_execution_links_are_the_free_guides():
    """Same targets as Action v1.3.2."""
    lg = "https://tryagentsignal.com/guides/langgraph-interrupt-resume-twice/"
    oai = "https://tryagentsignal.com/guides/duplicate-tool-calls-after-resume/"
    for d in DOUBLE:
        assert fix_url(d, "langgraph") == lg and fix_url(d, None) == lg
        assert fix_url(d, "openai-agents") == oai
    assert fix_url("approval.replay", "openai-agents") == oai


def test_scan_fix_link_is_one_switchable_constant():
    from agentsignal_check import links
    assert links.LEDGER_PAGE == "https://tryagentsignal.com/templates/side-effect-ledger/"
    expected = links.LEDGER_PAGE if links.LEDGER_LIVE else links.LEDGER_FALLBACK
    assert links.LEDGER_URL == expected and links.SCAN_FIX_URL == expected and links.scan_fix_url() == expected
    assert links.LEDGER_LIVE is False      # flip in the patch after board 54 serves 200 (release note)


def test_openai_agents_links_never_point_at_langgraph():
    for d in DETS:
        assert "langgraph" not in fix_url(d, "openai-agents")
    assert fix_url("approval.lost_on_restart", "openai-agents").endswith("/openai-agents-approval-resume/")


def test_langgraph_approval_links_and_default():
    assert fix_url("approval.replay", "langgraph").endswith("/langgraph-approval-ui/")
    assert fix_url("approval.lost_on_restart", "langgraph").endswith("/langgraph-human-in-the-loop/")
    assert fix_url("approval.lost_on_restart", None) == fix_url("approval.lost_on_restart", "langgraph")
    assert fix_url("unknown.detector", "openai-agents").endswith("/templates/")

@pytest.mark.parametrize("fw", ["openai-agents", "langgraph"])
def test_every_renderer_uses_framework_links(fw):
    other = "langgraph-" if fw == "openai-agents" else "openai-agents-"
    out = md.render(rep(fw), "t")
    html = card.render_html(rep(fw), "t")
    buf = io.StringIO(); card.print_card(rep(fw), "t", file=buf)
    for text in (out, html, buf.getvalue()):
        assert other not in text
        assert "idempotent-approval" not in text            # Pro templates
    assert all(fix_url(d, fw) in out and fix_url(d, fw) in html and fix_url(d, fw) in buf.getvalue() for d in DETS)

@pytest.mark.skipif(not os.environ.get("AGENTSIGNAL_NET_TESTS"), reason="set AGENTSIGNAL_NET_TESTS=1 to hit the network")
def test_all_fix_urls_return_200():
    from agentsignal_check.links import SCAN_FIX_URL
    for url in {u for m in FIX_URLS.values() for u in m.values()} | {SCAN_FIX_URL}:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "agentsignal-check-tests"}), timeout=20) as r:
            assert r.status == 200, url
