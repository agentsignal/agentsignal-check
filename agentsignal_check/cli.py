"""agentsignal check — crash-test an agent for double execution / lost approvals."""
import argparse, json, sys
from .detectors import langgraph_crash

def main(argv=None):
    from . import __version__
    p = argparse.ArgumentParser(prog="agentsignal")
    p.add_argument("--version", action="version", version=f"agentsignal-check {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="run detectors against a target agent")
    c.add_argument("--version", action="version", version=f"agentsignal-check {__version__}")
    c.add_argument("--framework", default=None, choices=["auto", "langgraph", "openai-agents"],
                   help="crash test: langgraph (default) or openai-agents; --scan: auto (default) detects per file")
    c.add_argument("--scan", metavar="PATH", help="static scan a file or directory (pure ast, never imports your code); "
                   "with --graph/--agent: scan, then crash test")
    c.add_argument("--graph", help="langgraph: module:factory; factory(checkpointer) -> compiled graph")
    c.add_argument("--agent", help="openai-agents: module:factory; factory() -> agents.Agent with needs_approval tools")
    c.add_argument("--context", default="{}", help="openai-agents: JSON run context (dict) passed to Runner.run")
    c.add_argument("--effect", action="append", help="module:function side effect to watch (repeatable; required for the crash test)")
    c.add_argument("--input", default="{}", help="JSON input for the first invoke")
    c.add_argument("--resume", default="true", help="JSON value sent to every interrupt (auto-approve)")
    c.add_argument("--report", help="write a markdown report to this path")
    c.add_argument("--html", help="write a self-contained HTML report card")
    c.add_argument("--png", help="write a 1200x630 OG card PNG (needs the [png] extra)")
    c.add_argument("--svg", help="write the terminal report card as SVG (rich export)")
    c.add_argument("--badge", help="write a shields-style SVG badge here and print a README snippet")
    c.add_argument("--badge-link", default="report.html", help="URL the badge links to (your latest report)")
    c.add_argument("--json", action="store_true", help="machine-readable output (same as --format json)")
    c.add_argument("--format", choices=["text", "json"], default="text", help="output format")
    c.add_argument("--effect-names", help="--scan: extra side-effect callees, comma separated (e.g. payments.charge,notify)")
    c.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="--scan: skip matching paths (repeatable)")
    c.add_argument("--include-tests", action="store_true", help="--scan: also scan test_*.py / *_test.py")
    c.add_argument("--strict", action="store_true", help="--scan: exit 1 on medium findings too")
    c.add_argument("--verbose", action="store_true", help="--scan: also show low-confidence findings")
    c.add_argument("--upload", action="store_true", help="Pro: upload the JSON report (needs AGENTSIGNAL_API_KEY)")
    c.add_argument("--upload-strict", action="store_true", help="with --upload: exit 3 if the upload fails")
    a = p.parse_args(argv)
    a.json = a.json or a.format == "json"
    scan_code, scan_result = 0, None
    if a.scan:
        import os
        if not os.path.exists(a.scan):
            p.error(f"--scan: no such file or directory: {a.scan}")
        from . import scan as sc
        names = [n.strip() for n in (a.effect_names or "").split(",") if n.strip()]
        fw = "auto" if a.framework in (None, "auto") else a.framework
        scan_result = sc.scan(a.scan, framework=fw, effect_names=names, excludes=a.exclude,
                              include_tests=a.include_tests)
        scan_code = sc.exit_code(scan_result, strict=a.strict)
        if not (a.graph or a.agent):
            print(sc.render_json(scan_result) if a.json else sc.render_text(scan_result, verbose=a.verbose), end="" if not a.json else "\n")
            return scan_code
        if not a.json:
            print(sc.render_text(scan_result, verbose=a.verbose))
        if a.framework in (None, "auto"):
            a.framework = "openai-agents" if a.agent and not a.graph else "langgraph"
    if not a.effect:
        p.error("the following arguments are required: --effect (or use --scan PATH for a static scan)")
    if a.framework == "openai-agents":
        if not a.agent:
            p.error("--framework openai-agents needs --agent module:factory")
        try:
            import agents  # noqa: F401
        except ImportError:
            print("agentsignal: OpenAI Agents SDK support not installed. Run:\n"
                  '  pip install "agentsignal-check[openai-agents]"', file=sys.stderr)
            return 2
        from .detectors import oai_crash
        report = oai_crash.run(a.agent, a.effect, json.loads(a.context))
        a.graph = a.agent
    else:
        if not a.graph:
            p.error("--framework langgraph needs --graph module:factory")
        try:
            import langgraph.checkpoint.sqlite  # noqa: F401
        except ImportError:
            print("agentsignal: LangGraph support not installed. Run:\n"
                  '  pip install "agentsignal-check[langgraph]"', file=sys.stderr)
            return 2
        report = langgraph_crash.run(a.graph, a.effect, json.loads(a.input), json.loads(a.resume))
    from .env import collect
    report["env"] = collect()
    if scan_result is not None:
        import json as _json
        from . import scan as sc
        report["scan"] = _json.loads(sc.render_json(scan_result))["scan"]
    if a.report:
        from .report import render
        open(a.report, "w").write(render(report, a.graph))
    if a.json:
        print(json.dumps(report, indent=2))
    else:
        from .card import print_card
        print_card(report, a.graph)
    from . import card
    if a.html:
        import datetime
        open(a.html, "w").write(card.render_html(report, a.graph))
    if a.svg:
        card.export_svg(report, a.graph, a.svg)
    if a.png:
        card.render_png(report, a.graph, a.png)
    if a.badge:
        svg, md = card.render_badge(report, a.badge_link)
        open(a.badge, "w").write(svg)
        md = md.replace("agentsignal-badge.svg", a.badge)
        print(md, file=sys.stderr if a.json else sys.stdout)
    code = 1 if any(r["status"] == "FAIL" for r in report["results"]) else 0
    code = max(code, scan_code)
    if a.upload or a.upload_strict:
        from .upload import upload
        ok, why = upload(report, out=sys.stderr if a.json else sys.stdout)
        if not ok and a.upload_strict and code == 0:
            return 3
    return code

if __name__ == "__main__":
    sys.exit(main())
