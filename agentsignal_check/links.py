"""Per-framework fix links. Every URL here is checked to return 200 (tests/test_fix_links.py)."""
SITE = "https://tryagentsignal.com"
T = f"{SITE}/templates"
FIX_URLS = {
    "langgraph": {
        "baseline": f"{T}/langgraph-idempotent-approval/",
        "double_execution.kill_resume": f"{T}/langgraph-idempotent-approval/",
        "double_execution.kill_before_checkpoint": f"{T}/langgraph-idempotent-approval/",
        "approval.lost_on_restart": f"{T}/langgraph-human-in-the-loop/",
        "approval.replay": f"{T}/langgraph-approval-ui/",
    },
    "openai-agents": {
        "baseline": f"{T}/openai-agents-idempotent-approval/",
        "double_execution.kill_resume": f"{T}/openai-agents-idempotent-approval/",
        "double_execution.kill_before_checkpoint": f"{T}/openai-agents-idempotent-approval/",
        "approval.lost_on_restart": f"{T}/openai-agents-approval-resume/",
        "approval.replay": f"{T}/openai-agents-idempotent-approval/",
    },
}
WHY_OAI = {
    "baseline": "The approved tool's side effect runs more than once in a clean approve/resume.",
    "double_execution.kill_resume": "A crash right after the effect re-runs it when the saved RunState is resumed; key the effect on a stable id.",
    "double_execution.kill_before_checkpoint": "A crash before the RunState is saved re-runs the effect on resume.",
    "approval.lost_on_restart": "Pending approvals don't survive a restart; persist RunState.to_json() and restore with RunState.from_json().",
    "approval.replay": "Resuming the same saved RunState twice (double-click, worker retry, queue redelivery) re-runs the effect.",
}

def _fw(framework):
    return framework if framework in FIX_URLS else "langgraph"

def fix_url(detector, framework=None):
    return FIX_URLS[_fw(framework)].get(detector, f"{T}/")

# Static scan (`agentsignal check --scan`): every finding points at the side-effect ledger fix (board 54).
SCAN_FIX_URL = f"{T}/side-effect-ledger/"

def scan_fix_url(rule=None, framework=None):
    return SCAN_FIX_URL
