"""Terminal report card, HTML page, OG PNG and badge -- all derived ONLY from detector results."""
import html as _h, os, sys

SITE = "https://tryagentsignal.com"
from .links import fix_url
FW = {"langgraph": "LangGraph", "openai-agents": "OpenAI Agents SDK"}
def _fw(report): return FW.get(report.get("framework") or "langgraph", "LangGraph")
TAGLINE = "We kill your LangGraph agent mid-approval, restart it, and count how many times the tool ran."
DOUBLE = ("baseline", "double_execution.kill_resume", "double_execution.kill_before_checkpoint", "approval.replay")
LOST = ("approval.lost_on_restart",)
META = {  # short name, one-line why (what the test does), fix link
    "baseline": ("Clean run", "Approve once, no crash: does the effect run once?", fix_url("baseline")),
    "double_execution.kill_resume": ("Kill after effect", "Hard-kill right after the side effect, resume from checkpoint", fix_url("baseline")),
    "double_execution.kill_before_checkpoint": ("Kill before checkpoint", "Hard-kill before the checkpoint write (langgraph#8039)", fix_url("baseline")),
    "approval.lost_on_restart": ("Restart while paused", "Restart the process mid-approval: is the approval still there?", f"{SITE}/templates/langgraph-human-in-the-loop/"),
    "approval.replay": ("Approval delivered twice", "Double-click / webhook retry / stale checkpoint resume", f"{SITE}/templates/langgraph-approval-ui/"),
}

def summarize(report):
    rs = report["results"]
    passed = sum(r["status"] == "PASS" for r in rs)
    st = {r["detector"]: r["status"] for r in rs}
    def risk(keys):
        vals = [st[k] for k in keys if k in st]
        if any(v == "FAIL" for v in vals): return "HIGH"
        if not vals or any(v in ("ERROR", "WARN", "SKIP") for v in vals): return "UNKNOWN"
        return "LOW"
    return {"passed": passed, "total": len(rs), "all_pass": passed == len(rs) and len(rs) > 0,
            "double_risk": risk(DOUBLE), "lost_risk": risk(LOST)}

# ---------------- terminal ----------------
COL = {"PASS": "bold #3ddc84", "FAIL": "bold #ff5c5c", "WARN": "bold yellow", "SKIP": "dim", "ERROR": "bold magenta"}
RISKCOL = {"HIGH": "bold #000000 on #ff5c5c", "LOW": "bold #000000 on #3ddc84", "UNKNOWN": "bold #000000 on #ffc53d"}
SYM = {"PASS": "✔", "FAIL": "✘", "WARN": "!", "SKIP": "-", "ERROR": "?"}

def build_renderable(report, target):
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich.console import Group
    s = summarize(report)
    head = Text()
    head.append("Approval flow: ", style="bold")
    head.append(f"{s['passed']}/{s['total']}", style="bold #3ddc84" if s["all_pass"] else "bold #ff5c5c")
    head.append(" crash tests survived\n", style="bold")
    head.append(f"target  {target}\n", style="dim")
    head.append("Double-charge risk  ", style="bold"); head.append(f" {s['double_risk']} ", style=RISKCOL[s["double_risk"]])
    head.append("   Lost-approval risk  ", style="bold"); head.append(f" {s['lost_risk']} ", style=RISKCOL[s["lost_risk"]])
    t = Table(box=None, show_header=False, pad_edge=False, expand=True, padding=(0, 1))
    t.add_column(width=6, no_wrap=True); t.add_column(ratio=1)
    fails = []
    for r in report["results"]:
        name, why, _ = META.get(r["detector"], (r["detector"], "", SITE)); url = fix_url(r["detector"], report.get("framework"))
        t.add_row(Text(f"{SYM.get(r['status'],'?')} {r['status']}", style=COL.get(r["status"], "")), Text(name, style="bold"))
        t.add_row("", Text(why, style="dim"))
        if r["status"] != "PASS":
            t.add_row("", Text(_short(r["detail"]), style="#ff9b9b" if r["status"] == "FAIL" else "yellow"))
            t.add_row("", Text(f"fix → {_disp(url)}", style="cyan"))
            fails.append(r)
    from .env import footer
    foot = Text(TAGLINE.replace("LangGraph", _fw(report)), style="dim italic")
    if footer(report.get("env")):
        foot.append("\n" + footer(report["env"]), style="dim")
    return Panel(Group(head, Text(""), t, Text(""), foot), title="[bold]agentsignal check[/]",
                 subtitle=f"[dim]{_fw(report)} · tryagentsignal.com[/]", border_style="#3ddc84" if s["all_pass"] else "#ff5c5c",
                 width=80, padding=(1, 2))

import re as _re
def _short(detail, n=66):
    """Human one-liner derived from the real detector detail (counts are copied, never invented)."""
    m = _re.findall(r"'([^'(]+)\(.*?': (\d+)", detail)
    pre = _re.search(r"(crash (?:after effect|before checkpoint) #\d+|same approval delivered twice|clean run)", detail)
    if m:
        fn, k = m[0]; fn = fn.split(".")[-1]
        # k = distinct results for identical args = executions that really happened
        line = f"{fn}() executed {k}× for one approval (expected 1×)"
    else:
        line = detail
    return line if len(line) <= n else line[: n - 1] + "…"

def _disp(url):
    return url.replace("https://", "")

def print_card(report, target, file=None):
    from rich.console import Console
    no_color = bool(os.environ.get("NO_COLOR"))
    f = file or sys.stdout
    if not f.isatty() or no_color:
        # plain, grep-friendly output for pipes/CI/NO_COLOR
        s = summarize(report)
        print(f"agentsignal check: {target}", file=f)
        print(f"Approval flow: {s['passed']}/{s['total']} crash tests survived", file=f)
        print(f"Double-charge risk: {s['double_risk']}  Lost-approval risk: {s['lost_risk']}", file=f)
        from .env import footer
        if footer(report.get("env")): print(footer(report["env"]), file=f)
        for r in report["results"]:
            name, why, _ = META.get(r["detector"], (r["detector"], "", SITE)); url = fix_url(r["detector"], report.get("framework"))
            print(f"[{r['status']}] {r['detector']}: {r['detail']}", file=f)
            if r["status"] != "PASS":
                print(f"    fix: {url}", file=f)
        return
    Console(file=f, width=80).print(build_renderable(report, target))

def export_svg(report, target, path, title="agentsignal check"):
    from rich.console import Console
    c = Console(record=True, width=80, file=open(os.devnull, "w"), force_terminal=True, color_system="truecolor")
    c.print(build_renderable(report, target))
    c.save_svg(path, title=title)

# ---------------- HTML ----------------
CSS = """:root{--bg:#0b0d0c;--fg:#ecf2ee;--dim:#9aa59f;--ok:#3ddc84;--bad:#ff5c5c;--warn:#ffc53d;--line:#26302b;--acc:#7cf3c0}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 "Geist","Inter",ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:860px;margin:48px auto;padding:0 20px}.term{border:1px solid var(--line);border-radius:12px;background:#0f1311;overflow:hidden}
.bar{display:flex;gap:8px;padding:12px 16px;border-bottom:1px solid var(--line)}.bar i{width:12px;height:12px;border-radius:50%;background:#333;display:block}
.bar i:nth-child(1){background:#ff5f57}.bar i:nth-child(2){background:#febc2e}.bar i:nth-child(3){background:#28c840}
.bar span{margin-left:8px;color:var(--dim);font:13px "Geist Mono",ui-monospace,"JetBrains Mono",Menlo,monospace}
.body{padding:28px 32px;font-family:"Geist Mono",ui-monospace,"JetBrains Mono","SFMono-Regular",Menlo,Consolas,monospace}
h1{font:600 30px/1.2 "Geist",ui-sans-serif,system-ui,sans-serif;margin:0 0 6px}.g-ok{color:var(--ok)}.g-bad{color:var(--bad)}
.target{color:var(--dim);font-size:14px}.risks{display:flex;gap:14px;flex-wrap:wrap;margin:18px 0 22px}
.risk{border:1px solid var(--line);border-radius:8px;padding:8px 12px;font-size:14px}.risk b{padding:2px 8px;border-radius:4px;margin-left:8px;color:#000}
.HIGH{background:var(--bad)}.LOW{background:var(--ok)}.UNKNOWN{background:var(--warn)}
.row{display:grid;grid-template-columns:92px 1fr;gap:12px;padding:12px 0;border-top:1px solid var(--line)}
.st{font-weight:700}.PASS{color:var(--ok)}.FAIL{color:var(--bad)}.WARN,.ERROR{color:var(--warn)}.SKIP{color:var(--dim)}
.name{font-weight:600}.why{color:var(--dim);font-size:14px}.detail{color:#ffb4b4;font-size:13px;margin-top:4px;word-break:break-word}
a{color:var(--acc)}.fix{font-size:14px;margin-top:2px}.foot{color:var(--dim);font-size:13px;margin-top:22px}"""

def render_html(report, target, run_at=""):
    from .env import footer
    s = summarize(report); e = _h.escape
    run_at = run_at or footer(report.get("env"))
    grade = f"{s['passed']}/{s['total']}"
    rows = []
    for r in report["results"]:
        name, why, _ = META.get(r["detector"], (r["detector"], "", SITE)); url = fix_url(r["detector"], report.get("framework"))
        extra = "" if r["status"] == "PASS" else f'<div class="detail">{e(_short(r["detail"], 200))}</div><div class="fix"><a href="{url}">fix → {e(_disp(url))}</a></div>'
        rows.append(f'<div class="row"><div class="st {r["status"]}">{SYM.get(r["status"],"?")} {r["status"]}</div>'
                    f'<div><div class="name">{e(name)} <span class="why">· {e(r["detector"])}</span></div><div class="why">{e(why)}</div>{extra}</div></div>')
    title = f"Approval flow: {grade} crash tests survived · agentsignal check"
    desc = f"Double-charge risk {s['double_risk']}, lost-approval risk {s['lost_risk']}. {TAGLINE}"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title><meta name="description" content="{e(desc)}">
<meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(desc)}"><meta property="og:type" content="website">
<meta property="og:image" content="og.png"><meta name="twitter:card" content="summary_large_image"><meta name="twitter:title" content="{e(title)}">
<meta name="twitter:description" content="{e(desc)}"><meta name="twitter:image" content="og.png"><meta name="color-scheme" content="dark">
<style>{CSS}</style></head><body><div class="wrap"><div class="term"><div class="bar"><i></i><i></i><i></i><span>agentsignal check</span></div><div class="body">
<h1>Approval flow: <span class="{'g-ok' if s['all_pass'] else 'g-bad'}">{grade}</span> crash tests survived</h1>
<div class="target">target {e(target)}{(' · ' + e(run_at)) if run_at else ''}</div>
<div class="risks"><div class="risk">Double-charge risk<b class="{s['double_risk']}">{s['double_risk']}</b></div><div class="risk">Lost-approval risk<b class="{s['lost_risk']}">{s['lost_risk']}</b></div></div>
{''.join(rows)}
<div class="foot">{e(TAGLINE)}<br>Generated by <a href="https://github.com/MounirMeziani/agentsignal-check">agentsignal-check</a> · <a href="{SITE}">{SITE}</a></div>
</div></div></div></body></html>"""

# ---------------- OG PNG (Pillow, optional extra) ----------------
FONT_DIRS = ["/usr/share/fonts", os.path.expanduser("~/.fonts"), "/Library/Fonts", "C:/Windows/Fonts"]

def _font(names, size):
    from PIL import ImageFont
    for d in FONT_DIRS:
        for root, _, files in os.walk(d) if os.path.isdir(d) else []:
            for n in names:
                if n in files:
                    try:
                        f = ImageFont.truetype(os.path.join(root, n), size)
                        try: f.set_variation_by_axes([700 if size > 40 else 500])
                        except Exception: pass
                        return f
                    except Exception: pass
    return ImageFont.load_default(size)

def render_png(report, target, path):
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        raise SystemExit('PNG output needs Pillow: pip install "agentsignal-check[png]"')
    s = summarize(report)
    W, H = 1200, 630
    im = Image.new("RGB", (W, H), "#0b0d0c"); d = ImageDraw.Draw(im)
    sans = lambda z: _font(["Geist-VariableFont_wght.ttf", "Inter-VariableFont_opsz,wght.ttf", "DejaVuSans-Bold.ttf"], z)
    mono = lambda z: _font(["GeistMono-VariableFont_wght.ttf", "JetBrainsMono-VariableFont_wght.ttf", "DejaVuSansMono.ttf"], z)
    ok, bad, warn, dim, fg = "#3ddc84", "#ff5c5c", "#ffc53d", "#9aa59f", "#ecf2ee"
    d.rounded_rectangle([40, 40, W - 40, H - 40], 20, fill="#0f1311", outline="#26302b", width=2)
    for i, c in enumerate(["#ff5f57", "#febc2e", "#28c840"]):
        d.ellipse([72 + i * 26, 70, 86 + i * 26, 84], fill=c)
    d.text((160, 66), "agentsignal check", font=mono(22), fill=dim)
    d.text((80, 120), "Approval flow:", font=sans(56), fill=fg)
    gx = 80 + d.textlength("Approval flow: ", font=sans(56))
    grade = f"{s['passed']}/{s['total']}"
    d.text((gx, 120), grade, font=sans(56), fill=ok if s["all_pass"] else bad)
    d.text((80, 190), "crash tests survived", font=sans(56), fill=fg)
    y = 285
    for label, v in (("Double-charge risk", s["double_risk"]), ("Lost-approval risk", s["lost_risk"])):
        d.text((80, y), label, font=mono(26), fill=fg)
        col = {"HIGH": bad, "LOW": ok, "UNKNOWN": warn}[v]
        x = 430; tw = d.textlength(v, font=mono(26))
        d.rounded_rectangle([x - 10, y - 4, x + tw + 10, y + 34], 6, fill=col)
        d.text((x, y), v, font=mono(26), fill="#000")
        y += 52
    y = 290
    for r in report["results"]:
        name = META.get(r["detector"], (r["detector"],))[0]
        col = {"PASS": ok, "FAIL": bad}.get(r["status"], warn)
        d.text((640, y), f"{SYM.get(r['status'],'?')} {r['status']}", font=mono(22), fill=col)
        d.text((760, y), name, font=mono(22), fill=fg)
        y += 40
    d.text((W - 80, H - 80), "tryagentsignal.com", font=mono(22), fill=dim, anchor="rs")
    d.text((80, H - 80), f"{_fw(report)} · {len(report['results'])} crash tests", font=mono(22), fill=dim, anchor="ls")
    im.save(path)

# ---------------- badge ----------------
def _w(t):  # rough Verdana 11px width, like shields.io
    return int(sum(7.2 if c.isupper() else 6.1 for c in t)) + 10

def render_badge(report, link="report.html"):
    s = summarize(report)
    # captions 3a by default; 3b ("passing") only when every detector passed on this run
    if s["all_pass"]:
        label, msg = "Agent Signal crash test", "passing"
    else:
        label, msg = "Crash-tested by Agent Signal", f"{s['passed']}/{s['total']}"
    color = "#3ddc84" if s["all_pass"] else "#dfb317"  # passing only when ALL detectors pass
    lw, mw = _w(label), _w(msg); W = lw + mw
    e = _h.escape
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="20" role="img" aria-label="{e(label)}: {e(msg)}"><title>{e(label)}: {e(msg)}</title>
<linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>
<clipPath id="r"><rect width="{W}" height="20" rx="3" fill="#fff"/></clipPath><g clip-path="url(#r)"><rect width="{lw}" height="20" fill="#0f1311"/>
<rect x="{lw}" width="{mw}" height="20" fill="{color}"/><rect width="{W}" height="20" fill="url(#s)"/></g>
<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11"><text x="{lw/2}" y="14">{e(label)}</text>
<text x="{lw + mw/2}" y="14" fill="#000">{e(msg)}</text></g></svg>"""
    alt = "Agent Signal crash test: passing" if s["all_pass"] else f"Crash-tested by Agent Signal: {s['passed']}/{s['total']} passed"
    md = f"[![{alt}](agentsignal-badge.svg)]({link})"
    return svg, md
