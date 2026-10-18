"""Find the langgraph version a project pins or resolves (uv.lock > requirements*.txt > pyproject.toml).
Pure file parsing: nothing is installed, imported or resolved."""
from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass

FIXED = (1, 2, 16)
VERIFIED_FROM = (1, 1, 0)
FIX_NOTE = "fixed in 1.2.16; pin >=1.2.16"
REQ_RE = re.compile(r"^\s*langgraph\s*(?:\[[^\]]*\])?\s*(?![\w.-])([^;#]*)", re.I)
CLAUSE_RE = re.compile(r"(===|==|~=|!=|>=|<=|>|<)\s*([0-9][0-9A-Za-z.*+!-]*)")


@dataclass
class LgVersion:
    status: str                 # affected | fixed | unknown
    version: str | None = None  # exact version when pinned / resolved
    spec: str | None = None     # requirement spec when only a range is known
    source: str | None = None   # file it came from

    def describe(self):
        what = self.version or self.spec or "?"
        return f"langgraph {what}" + (f" ({os.path.basename(self.source)})" if self.source else "")


def parse_ver(s):
    out = []
    for part in s.split(".")[:3]:
        m = re.match(r"\d+", part)
        if not m:
            break
        out.append(int(m.group()))
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def classify_exact(v: str) -> str:
    t = parse_ver(v)
    if t >= FIXED:
        return "fixed"
    return "affected" if t >= VERIFIED_FROM else "unknown"     # < 1.1.0: not verified -> medium


def classify_spec(spec: str) -> str:
    clauses = CLAUSE_RE.findall(spec or "")
    if not clauses:
        return "unknown"
    lower = upper = None          # (tuple, inclusive)
    for op, v in clauses:
        if op in ("==", "===") and "*" not in v:
            return classify_exact(v)
        t = parse_ver(v.replace(".*", ""))
        if op in (">=", ">") or (op == "==" and "*" in v) or op == "~=":
            if lower is None or t > lower:
                lower = t
        if op == "<":
            upper = (t, False) if upper is None or t < upper[0] else upper
        elif op == "<=":
            upper = (t, True) if upper is None or t < upper[0] else upper
        elif op == "~=":
            parts = [p for p in v.split(".") if p]
            if len(parts) >= 2:
                head = [int(re.match(r"\d+", p).group()) for p in parts[:-1]]
                head[-1] += 1
                ut = tuple((head + [0, 0, 0])[:3])
                upper = (ut, False) if upper is None or ut < upper[0] else upper
    if lower is not None and lower >= FIXED:
        return "fixed"
    if upper is not None and (upper[0] < FIXED or (upper[0] == FIXED and not upper[1])):
        # the resolver picks the newest allowed release, which is below 1.2.16
        return "affected" if upper[0] > VERIFIED_FROM else "unknown"
    return "unknown"


def _from_uv_lock(path):
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        import tomli as tomllib  # type: ignore
    try:
        data = tomllib.load(open(path, "rb"))
    except Exception:
        return None
    for pkg in data.get("package", []) or []:
        if pkg.get("name", "").lower() == "langgraph" and pkg.get("version"):
            v = pkg["version"]
            return LgVersion(classify_exact(v), version=v, source=path)
    return None


def _from_req_lines(lines, path):
    for line in lines:
        m = REQ_RE.match(line)
        if m:
            spec = m.group(1).strip()
            exact = re.fullmatch(r"===?\s*([0-9][0-9A-Za-z.+!-]*)", spec.replace(" ", ""))
            if exact:
                return LgVersion(classify_exact(exact.group(1)), version=exact.group(1), source=path)
            return LgVersion(classify_spec(spec), spec=spec or "any", source=path)
    return None


def _from_requirements(path):
    try:
        return _from_req_lines(open(path, encoding="utf-8", errors="replace").read().splitlines(), path)
    except OSError:
        return None


def _from_pyproject(path):
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        import tomli as tomllib  # type: ignore
    try:
        data = tomllib.load(open(path, "rb"))
    except Exception:
        return None
    proj = data.get("project", {}) or {}
    reqs = list(proj.get("dependencies", []) or [])
    for v in (proj.get("optional-dependencies", {}) or {}).values():
        reqs += list(v or [])
    for v in (data.get("dependency-groups", {}) or {}).values():
        reqs += [x for x in (v or []) if isinstance(x, str)]
    found = _from_req_lines(reqs, path)
    if found:
        return found
    poetry = ((data.get("tool", {}) or {}).get("poetry", {}) or {}).get("dependencies", {}) or {}
    for k, v in poetry.items():
        if k.lower() == "langgraph":
            spec = v if isinstance(v, str) else (v.get("version", "") if isinstance(v, dict) else "")
            spec = spec.strip()
            if re.fullmatch(r"[0-9][0-9A-Za-z.+!-]*", spec):        # poetry bare version = exact
                return LgVersion(classify_exact(spec), version=spec, source=path)
            if spec.startswith("^"):
                spec = f">={spec[1:]}"
            return LgVersion(classify_spec(spec), spec=spec or "any", source=path)
    return None


def _in_dir(d):
    p = os.path.join(d, "uv.lock")
    if os.path.isfile(p):
        r = _from_uv_lock(p)
        if r:
            return r
    for p in sorted(glob.glob(os.path.join(d, "requirements*.txt"))):
        r = _from_requirements(p)
        if r:
            return r
    p = os.path.join(d, "pyproject.toml")
    if os.path.isfile(p):
        return _from_pyproject(p)
    return None


_CACHE: dict = {}


def langgraph_version(file_path: str) -> LgVersion:
    """Nearest project file at or above the scanned file's directory (stops at the repo root / filesystem root)."""
    d = os.path.dirname(os.path.abspath(file_path))
    seen = []
    while True:
        if d in _CACHE:
            res = _CACHE[d]
            break
        seen.append(d)
        res = _in_dir(d)
        if res is not None or os.path.isdir(os.path.join(d, ".git")) or os.path.isfile(os.path.join(d, ".git")):
            res = res or LgVersion("unknown")
            break
        nd = os.path.dirname(d)
        if nd == d:
            res = LgVersion("unknown")
            break
        d = nd
    for s in seen:
        _CACHE[s] = res
    return res


def clear_cache():
    _CACHE.clear()
