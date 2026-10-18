"""Static scan driver: walk files, parse with ``ast`` (no imports of user code), run framework rules,
apply suppressions, render terminal / JSON output."""
from __future__ import annotations

import ast
import fnmatch
import os
import re
from dataclasses import dataclass, asdict

from .effects import Classifier, FuncInfo, CONTAINER_CTORS, last_name

DEFAULT_SKIP_DIRS = {".venv", "venv", "node_modules", "site-packages", ".git", "build", "dist", "__pycache__",
                     ".tox", ".mypy_cache", ".pytest_cache"}
FRAMEWORKS = ("langgraph", "openai-agents")
FROZEN = {"google.adk": "google-adk", "ai": None}
SUPPRESS_RE = re.compile(r"#\s*agentsignal:\s*ignore(?:\[([A-Za-z0-9_,\s]*)\])?")
RANK = {"high": 3, "medium": 2, "low": 1}


@dataclass
class Finding:
    rule: str
    severity: str
    confidence: str
    framework: str
    function: str
    file: str
    line: int
    col: int
    effect_call: str | None
    effect_line: int | None
    message: str
    fix_url: str
    short: str = ""
    group: str = "rerun"     # rerun | handler | persist
    def_line: int = 0
    effect_full: str | None = None

    def public(self):
        d = asdict(self)
        for k in ("short", "group", "def_line", "effect_full"):
            d.pop(k)
        return d


class FileCtx:
    """Parsed file plus scope / import / function indexes shared by the rules."""

    def __init__(self, tree: ast.Module, src: str, path: str):
        self.tree, self.src, self.path = tree, src, path
        self.lines = src.splitlines()
        self.imports: dict[str, str] = {}      # local alias -> full dotted name
        self.modules: set[str] = set()         # imported modules (full)
        self.parent: dict[ast.AST, ast.AST] = {}
        self.funcs: dict[ast.AST, FuncInfo] = {}
        self.scope_defs: dict[ast.AST, dict[str, ast.AST]] = {}   # scope node -> name -> def
        self._index()

    # ---- indexing ----
    def _index(self):
        for node in ast.walk(self.tree):
            for ch in ast.iter_child_nodes(node):
                self.parent[ch] = node
            if isinstance(node, ast.Import):
                for a in node.names:
                    self.modules.add(a.name)
                    self.imports[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                self.modules.add(node.module)
                for a in node.names:
                    self.imports[a.asname or a.name] = f"{node.module}.{a.name}"
        self._collect_defs(self.tree)
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.funcs[node] = self._func_info(node)

    def _collect_defs(self, scope):
        defs = {}
        stack = list(getattr(scope, "body", []))
        while stack:
            st = stack.pop(0)
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defs[st.name] = st
                self._collect_defs(st)
                continue
            for fld in ("body", "orelse", "finalbody", "handlers"):
                stack.extend(getattr(st, fld, []) or [])
            if isinstance(st, ast.Match):
                for c in st.cases:
                    stack.extend(c.body)
        self.scope_defs[scope] = defs

    def enclosing(self, node, kinds=(ast.FunctionDef, ast.AsyncFunctionDef)):
        p = self.parent.get(node)
        while p is not None and not isinstance(p, kinds):
            p = self.parent.get(p)
        return p

    def scope_chain(self, node):
        """Enclosing function/class/module scopes of `node`, innermost first."""
        out, p = [], self.parent.get(node)
        while p is not None:
            if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
                out.append(p)
            p = self.parent.get(p)
        return out

    def qualname(self, fn):
        parts = [fn.name] + [s.name for s in self.scope_chain(fn) if not isinstance(s, ast.Module)]
        return ".".join(reversed(parts))

    def _func_info(self, fn):
        cls = self.parent.get(fn) if isinstance(self.parent.get(fn), ast.ClassDef) else None
        fi = FuncInfo(fn, self.qualname(fn), cls, self.scope_chain(fn))
        a = fn.args
        for arg in a.posonlyargs + a.args + a.kwonlyargs + ([a.vararg] if a.vararg else []) + ([a.kwarg] if a.kwarg else []):
            fi.params.add(arg.arg)
        for n in self.own_nodes(fn):
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                name = n.targets[0].id
                fi.assigns.setdefault(name, []).append(((n.lineno, n.col_offset), n.value))
                if self._is_container(n.value):
                    fi.bound_containers.add(name)
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value is not None:
                fi.assigns.setdefault(n.target.id, []).append(((n.lineno, n.col_offset), n.value))
                if self._is_container(n.value):
                    fi.bound_containers.add(n.target.id)
            elif isinstance(n, (ast.With, ast.AsyncWith)):
                for it in n.items:
                    if isinstance(it.optional_vars, ast.Name) and self._is_container(it.context_expr):
                        fi.bound_containers.add(it.optional_vars.id)
        return fi

    @staticmethod
    def _is_container(v):
        if isinstance(v, (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp, ast.Tuple)):
            return True
        return isinstance(v, ast.Call) and last_name(v.func) in CONTAINER_CTORS

    # ---- queries ----
    def own_nodes(self, fn):
        """Nodes in fn's body, not descending into nested defs/lambdas/classes or `if TYPE_CHECKING:`."""
        stack = list(fn.body) if hasattr(fn, "body") and isinstance(fn.body, list) else [fn.body]
        for d in getattr(fn, "decorator_list", []):
            pass
        while stack:
            n = stack.pop()
            yield n
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(n, ast.If) and _is_type_checking(n.test):
                stack.extend(n.orelse)
                continue
            stack.extend(ast.iter_child_nodes(n))

    def own_calls(self, fn):
        calls = [n for n in self.own_nodes(fn) if isinstance(n, ast.Call)]
        calls.sort(key=lambda c: (c.lineno, c.col_offset))
        return calls

    def expand(self, d: str) -> str:
        root, _, rest = d.partition(".")
        base = root.split("(")[0]
        if base in self.imports:
            return self.imports[base] + (root[len(base):]) + ("." + rest if rest else "")
        return d

    def resolve_name(self, name, at_node):
        """A local def named `name` visible from `at_node` (enclosing functions first, then module)."""
        for scope in self.scope_chain(at_node) if at_node is not None else [self.tree]:
            if isinstance(scope, ast.ClassDef):
                continue
            d = self.scope_defs.get(scope, {}).get(name)
            if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return d
        return None

    def resolve_local(self, call, fi):
        f = call.func
        if isinstance(f, ast.Name):
            return self.resolve_name(f.id, call)
        if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in ("self", "cls")
                and fi is not None and fi.cls is not None):
            d = self.scope_defs.get(fi.cls, {}).get(f.attr)
            if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return d
        return None

    def suppressed(self, line, rule):
        if not line or line > len(self.lines):
            return False
        m = SUPPRESS_RE.search(self.lines[line - 1])
        if not m:
            return False
        if m.group(1) is None:
            return True
        return rule.upper() in {r.strip().upper() for r in m.group(1).split(",") if r.strip()}


def _is_type_checking(test):
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")


def detect_frameworks(fctx: FileCtx) -> set:
    fw = set()
    for m in fctx.modules:
        top = m.split(".")[0]
        if top == "langgraph":
            fw.add("langgraph")
        elif top == "agents":
            fw.add("openai-agents")
    return fw


def frozen_framework(fctx: FileCtx):
    for m in fctx.modules:
        if m == "google.adk" or m.startswith("google.adk."):
            return "google-adk"
    return None


# ---------------- config / walking ----------------
def load_config(root):
    """[tool.agentsignal.scan] from the nearest pyproject.toml at or above the scan root."""
    d = os.path.abspath(root if os.path.isdir(root) else os.path.dirname(root) or ".")
    while True:
        p = os.path.join(d, "pyproject.toml")
        if os.path.isfile(p):
            try:
                try:
                    import tomllib  # py>=3.11
                except ImportError:  # pragma: no cover
                    import tomli as tomllib  # type: ignore
                with open(p, "rb") as fh:
                    data = tomllib.load(fh)
            except Exception:
                return {}, p
            return (data.get("tool", {}).get("agentsignal", {}).get("scan", {}) or {}), p
        nd = os.path.dirname(d)
        if nd == d:
            return {}, None
        d = nd


def is_test_file(name):
    return fnmatch.fnmatch(name, "test_*.py") or fnmatch.fnmatch(name, "*_test.py")


def iter_files(root, excludes=(), include_tests=False):
    """Yield (abs_path, rel_posix) for .py files under root, skipping default dirs, excludes, tests."""
    def excluded(rel):
        base = rel.rsplit("/", 1)[-1]
        return any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(base, g) or
                   any(fnmatch.fnmatch(part, g) for part in rel.split("/")[:-1]) for g in excludes)
    if os.path.isfile(root):
        name = os.path.basename(root)
        if not excluded(name):
            yield os.path.abspath(root), name
        return
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in DEFAULT_SKIP_DIRS and not d.endswith(".egg-info"))
        for fn in sorted(fns):
            if not fn.endswith(".py"):
                continue
            ap = os.path.join(dp, fn)
            rel = os.path.relpath(ap, root).replace(os.sep, "/")
            if (not include_tests and is_test_file(fn)) or excluded(rel):
                continue
            yield ap, rel


def display_path(root_arg, rel):
    """Path shown to the user: the scan root as typed + the file's path under it (clickable from cwd)."""
    r = root_arg.replace(os.sep, "/")
    if os.path.isfile(root_arg):
        return r[2:] if r.startswith("./") else r
    r = r.rstrip("/")
    if r.startswith("./"):
        r = r[2:]
    return rel if r in ("", ".") else f"{r}/{rel}"


def framework_versions():
    from importlib.metadata import version, PackageNotFoundError
    out = {}
    for fw, pkg in (("langgraph", "langgraph"), ("openai-agents", "openai-agents")):
        try:
            out[fw] = version(pkg)
        except PackageNotFoundError:
            out[fw] = None
    return out


def scan(root, framework="auto", effect_names=(), excludes=(), include_tests=False, ignore_rules=()):
    """Run the static scan. Returns the `scan` dict (JSON shape) plus private keys used by the renderer."""
    from . import langgraph as lg, openai_agents as oai
    cfg, cfg_path = load_config(root)
    effect_names = list(effect_names) + list(cfg.get("effect_names", []) or [])
    excludes = list(excludes) + list(cfg.get("exclude", []) or [])
    ignore = {r.upper() for r in list(ignore_rules) + list(cfg.get("ignore", []) or [])}
    findings, skipped, fw_files = [], [], {}
    suppressed = {"inline": 0, "config": 0}
    nfiles = 0
    for ap, rel in iter_files(root, excludes, include_tests):
        nfiles += 1
        disp = display_path(root, rel)
        try:
            with open(ap, "rb") as fh:
                raw = fh.read()
            if b"langgraph" not in raw and b"agents" not in raw and b"google.adk" not in raw:
                continue            # cannot import a supported framework: nothing to scan (keeps CI scans fast)
            src = raw.decode("utf-8-sig", errors="replace")
            tree = ast.parse(src, filename=disp)
        except (SyntaxError, ValueError, RecursionError) as e:
            line = getattr(e, "lineno", None)
            skipped.append({"file": disp, "reason": f"parse error: {e.__class__.__name__}" + (f" at line {line}" if line else "")})
            continue
        except OSError as e:
            skipped.append({"file": disp, "reason": f"unreadable: {e.__class__.__name__}"})
            continue
        fctx = FileCtx(tree, src, disp)
        fws = detect_frameworks(fctx)
        if not fws:
            fz = frozen_framework(fctx)
            if fz:
                skipped.append({"file": disp, "reason": f"{fz}: frozen, not scanned"})
            continue
        if framework not in (None, "auto"):
            fws &= {framework}
        clf = Classifier(fctx, effect_names)
        for fw in sorted(fws):
            fw_files[fw] = fw_files.get(fw, 0) + 1
            mod = lg if fw == "langgraph" else oai
            for f in mod.run(fctx, clf):
                if f.rule in ignore:
                    suppressed["config"] += 1
                    continue
                if any(fctx.suppressed(ln, f.rule) for ln in {f.line, f.effect_line, f.def_line} if ln):
                    suppressed["inline"] += 1
                    continue
                findings.append(f)
    findings.sort(key=lambda f: (f.file, f.line, f.col, f.rule))
    vers = framework_versions()
    return {
        "files": nfiles,
        "frameworks": {fw: {"files": n, "version": vers.get(fw)} for fw, n in sorted(fw_files.items())},
        "findings": [f.public() for f in findings],
        "suppressed": suppressed["inline"] + suppressed["config"],
        "skipped": skipped,
        "config": os.path.relpath(cfg_path) if cfg else None,
        "_findings": findings,
        "_root": root,
    }


def public(result):
    return {k: v for k, v in result.items() if not k.startswith("_")}


def exit_code(result, strict=False):
    """1 if any high finding (or, with --strict, any medium/high finding); else 0.
    Unparseable files are listed under `skipped` and never fail the scan."""
    fs = result["_findings"]
    if any(f.confidence == "high" for f in fs) or (strict and any(f.confidence != "low" for f in fs)):
        return 1
    return 0


def rerun_count(result):
    """Distinct functions (not findings) in the 're-run on resume' group, low confidence excluded."""
    return len({(f.file, f.def_line, f.function) for f in result["_findings"]
                if f.group == "rerun" and f.confidence != "low"})


def summary_line(result):
    from .. import links
    n = rerun_count(result)
    return f"{n} tool{'' if n == 1 else 's'} will re-run on resume. Fix (free, copy-paste): {links.scan_fix_url()}"


def render_json(result):
    import json
    from .. import links
    d = public(result)
    d["rerun_tools"] = rerun_count(result)
    d["summary"] = summary_line(result)
    d["fix_url"] = links.scan_fix_url()
    return json.dumps({"scan": d}, indent=2)


# ---------------- terminal rendering ----------------
GROUPS = [
    ("rerun", ("tool will re-run on resume", "tools will re-run on resume")),
    ("handler", ("approval handler can be delivered twice", "approval handlers can be delivered twice")),
    ("persist", ("approval may be lost on restart", "approvals may be lost on restart")),
]


def _module_of(path):
    p = path[:-3] if path.endswith(".py") else path
    return p.replace("/", ".")


def render_text(result, verbose=False):
    fs = [f for f in result["_findings"] if verbose or f.confidence != "low"]
    fws = result["frameworks"]
    det = ", ".join(f"{fw} {v['version']}" if v.get("version") else fw for fw, v in fws.items())
    out = [f"agentsignal scan  {result['_root']}  ({det + ' detected' if det else 'no supported framework detected'})", ""]
    if not fs:
        out.append(f"No re-run risks found by static scan ({result['files']} file{'s' if result['files'] != 1 else ''}). "
                   "Static scan can't see runtime behavior; run the crash test to prove it.")
    n = 0
    for gid, (one, many) in GROUPS:
        gfs = [f for f in fs if f.group == gid]
        if not gfs:
            continue
        k = len({(f.file, f.def_line, f.function) for f in gfs})
        out.append(f"{k} {one}:" if k == 1 else f"These {k} {many}:")
        for f in gfs:
            n += 1
            loc = f"{f.file}:{f.line}"
            out.append(f"  {n}. {f.function:<14} {loc:<22} {f.short:<52} {f.rule} {f.confidence}".rstrip())
        out.append("")
    hidden = len(result["_findings"]) - len(fs)
    tail = []
    if hidden:
        tail.append(f"{hidden} low-confidence finding{'s' if hidden != 1 else ''} hidden (use --verbose or --json).")
    if result["suppressed"]:
        tail.append(f"{result['suppressed']} suppressed")
    for s in result["skipped"]:
        tail.append(f"skipped {s['file']}: {s['reason']}")
    if tail:
        if out[-1] != "":
            out.append("")
        out.extend(tail)
        out.append("")
    if out[-1] != "":
        out.append("")
    if fs:
        first = fs[0]
        mod = _module_of(first.file)
        eff = (first.effect_full or first.effect_call or "").replace("()", "")
        concrete = (all(p.isidentifier() for p in mod.split(".")) and "." in eff
                    and first.effect_full and first.effect_full != (first.effect_call or "").replace("()", ""))
        target = f"{mod}:build" if all(p.isidentifier() for p in mod.split(".")) else "your_module:build"
        effect = (eff.rsplit(".", 1)[0] + ":" + eff.rsplit(".", 1)[1]) if concrete else "your_module:effect_fn"
        flag = "--framework openai-agents --agent" if first.framework == "openai-agents" else "--graph"
        out.append(f"Prove it on your code (60s): agentsignal check {flag} {target} --effect {effect}")
    out.append(summary_line(result))
    return "\n".join(out) + "\n"
