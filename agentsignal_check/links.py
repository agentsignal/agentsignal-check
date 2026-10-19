"""Per-framework fix links. Rule (Elon, 2026-10-18): every FAIL / fix link the CLI prints points at a FREE page,
never a Pro template. Every URL here is checked to return 200 (tests/test_fix_links.py, AGENTSIGNAL_NET_TESTS=1)."""
SITE = "https://tryagentsignal.com"
T = f"{SITE}/templates"

G = f"{SITE}/guides"

# Free guides for double execution (same as Action v1.3.2).
LG_DOUBLE_URL = f"{G}/langgraph-interrupt-resume-twice/"
DUP_TOOL_CALLS_URL = f"{G}/duplicate-tool-calls-after-resume/"

# The scan's closing "Fix" link: the free side-effect ledger (board 54) once it serves 200, until then the free
# idempotency guide (200 today). ONE switch: flip LEDGER_LIVE to True in the patch after board 54 ships
# (tests/test_fix_links.py checks the chosen URL returns 200 with AGENTSIGNAL_NET_TESTS=1).
LEDGER_PAGE = f"{T}/side-effect-ledger/"
LEDGER_FALLBACK = DUP_TOOL_CALLS_URL
LEDGER_LIVE = True
LEDGER_URL = LEDGER_PAGE if LEDGER_LIVE else LEDGER_FALLBACK
SCAN_FIX_LABEL = "Fix (free, copy-paste)" if LEDGER_LIVE else "Fix (free guide)"

# Pro templates: never link to these from the CLI (guarded by tests/test_fix_links.py).
PRO_TEMPLATES = ("langgraph-idempotent-approval", "openai-agents-idempotent-approval")

FIX_URLS = {
    "langgraph": {
        "baseline": LG_DOUBLE_URL,
        "double_execution.kill_resume": LG_DOUBLE_URL,
        "double_execution.kill_before_checkpoint": LG_DOUBLE_URL,
        "approval.lost_on_restart": f"{T}/langgraph-human-in-the-loop/",      # Free template
        "approval.replay": f"{T}/langgraph-approval-ui/",                     # Free template
    },
    "openai-agents": {
        "baseline": DUP_TOOL_CALLS_URL,
        "double_execution.kill_resume": DUP_TOOL_CALLS_URL,
        "double_execution.kill_before_checkpoint": DUP_TOOL_CALLS_URL,
        "approval.lost_on_restart": f"{T}/openai-agents-approval-resume/",    # Free template
        "approval.replay": DUP_TOOL_CALLS_URL,
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
SCAN_FIX_URL = LEDGER_URL

def scan_fix_url(rule=None, framework=None):
    return SCAN_FIX_URL
