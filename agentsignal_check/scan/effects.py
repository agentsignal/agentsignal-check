"""Shared side-effect classifier (pure ``ast``; never imports user code).

A call is side-effecting if (spec 53, "Side-effect classifier"):
1. its callee's last name matches the verb list (snake_case segment match, case-insensitive),
2. it is a known client call (stripe, requests/httpx, smtplib, boto3, SQL ``execute`` with a write
   literal, subprocess, ``open(..., "w"|"a")``), or
3. it matches a user ``--effect-names`` entry.
Reads (get/list/fetch/read/lookup/search/retrieve, HTTP GET, SELECT) are never effects.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

VERBS = {
    "charge", "refund", "pay", "payout", "transfer", "send", "post", "publish", "notify", "create",
    "insert", "update", "upsert", "delete", "write", "put", "patch", "execute", "submit", "book",
    "provision", "deploy",
}
COMPOUND_VERBS = {"send_email", "send_message", "place_order"}
READS = {"get", "list", "fetch", "read", "lookup", "search", "retrieve"}

# Never effects: logging/printing, stdlib HTTP-server response plumbing, framework plumbing.
LOG_ROOTS = {"logger", "logging", "log", "_logger", "_log", "warnings", "LOGGER", "LOG"}
NEVER_NAMES = {
    "print", "pprint",
    # http.server.BaseHTTPRequestHandler response plumbing (writes to the client socket, not an effect)
    "send_response", "send_response_only", "send_header", "send_error", "end_headers", "flush_headers",
    "log_message", "log_request", "log_error",
    # LangGraph / Agents SDK plumbing whose names hit the verb list
    "update_state", "bulk_update_state", "aupdate_state",
}
WRITE_SINKS = {"wfile", "stdout", "stderr", "buffer", "buf", "sio", "_buf"}
CONTAINER_METHODS = {"update", "insert", "put", "write", "writelines", "setdefault", "put_nowait"}
STATE_NAMES = {"state", "s", "st", "data", "d", "kwargs", "payload", "headers", "params", "config", "cfg",
               "values", "result", "results", "out", "acc", "update", "updates", "row", "rows", "meta",
               "metadata", "context_dict", "merged"}
CONTAINER_CTORS = {"dict", "list", "set", "defaultdict", "OrderedDict", "Counter", "deque", "copy",
                   "deepcopy", "StringIO", "BytesIO", "open"}
FACTORY_RE = re.compile(r"^create_.*_(agent|graph|engine|client|session|app|parser|prompt|model|chain|"
                        r"retriever|tool|tools|executor|logger|connection|pool|table|index)$")
SQLISH_RECEIVER = re.compile(r"^(c|cur|cursor|conn|connection|con|db|session|tx|txn|transaction|engine|"
                             r"_c|_cur|_conn|_db|_session)$")
WRITE_SQL = re.compile(r"^\s*(insert|update|delete|replace|merge|upsert|truncate)\b", re.I)
IDEMPOTENT_SQL = re.compile(r"insert\s+or\s+ignore|on\s+conflict\s*(\([^)]*\))?\s*do\s+nothing", re.I)
HTTP_WRITE = {"post", "put", "patch", "delete"}
STRIPE_WRITE = {"create", "modify", "capture", "refund", "cancel", "confirm", "pay", "delete", "update"}
SUBPROCESS_NAMES = {"run", "call", "check_call", "check_output", "Popen", "getoutput", "getstatusoutput"}

IDEM_KWARGS = {"idempotency_key", "idempotencyKey", "request_id", "dedupe_key", "client_token"}
LEDGER_DECORATORS = {"ledger", "once", "idempotent", "side_effect_ledger"}
RANDOM_CALLS = {"uuid1", "uuid4", "time", "time_ns", "monotonic", "perf_counter", "now", "utcnow",
                "random", "randint", "randrange", "choice", "getrandbits", "token_hex", "token_urlsafe",
                "token_bytes", "urandom", "ulid", "new_id"}
RANDOM_ROOTS = {"random", "secrets", "uuid"}
# identifiers that mark a value as derived from a run / thread / call / tool-call id
ID_IDENT = re.compile(r"(^id$|_id$|Id$|^tool_name$|^configurable$|^context$|^idempotency)")


def dotted(node) -> str | None:
    """`a.b.c` for Name/Attribute chains; calls in the chain render as `x()`, subscripts as `x[]`."""
    parts = []
    while True:
        if isinstance(node, ast.Attribute):
            parts.append(node.attr); node = node.value
        elif isinstance(node, ast.Name):
            parts.append(node.id); break
        elif isinstance(node, ast.Call):
            inner = dotted(node.func)
            if inner is None:
                return None
            parts.append(inner + "()"); break
        elif isinstance(node, ast.Subscript):
            inner = dotted(node.value)
            if inner is None:
                return None
            parts.append(inner + "[]"); break
        else:
            return None
    return ".".join(reversed(parts))


def last_name(func) -> str | None:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def root_name(node) -> str | None:
    while isinstance(node, (ast.Attribute, ast.Call, ast.Subscript)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else None


def str_value(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "{}"
                       for v in node.values)
    return None


def verb_match(name: str) -> bool:
    low = name.lower()
    segs = [s for s in low.split("_") if s]
    if not segs or segs[0] in READS:
        return False
    return low in COMPOUND_VERBS or any(s in VERBS for s in segs)


@dataclass
class Effect:
    name: str            # display callee, e.g. "payments.charge"
    call: ast.Call       # the call in the analysed function
    kind: str            # verb | client | user | local
    inner: "Effect | None" = None

    @property
    def line(self):
        return self.call.lineno


@dataclass
class FuncInfo:
    """Per-function facts used by the classifier and the rules."""
    node: ast.AST
    qualname: str
    cls: ast.ClassDef | None
    scope_chain: list          # enclosing FuncInfo/Module nodes, innermost first (excluding self)
    assigns: dict = field(default_factory=dict)       # name -> [(pos, value)]
    bound_containers: set = field(default_factory=set)
    params: set = field(default_factory=set)


def is_random(expr, fi: FuncInfo | None, depth=0) -> bool:
    for n in ast.walk(expr):
        if isinstance(n, ast.Call):
            ln = last_name(n.func)
            rt = root_name(n.func)
            if ln in RANDOM_CALLS or rt in RANDOM_ROOTS and ln not in ("UUID", "uuid5", "uuid3"):
                return True
        if isinstance(n, ast.Name) and fi is not None and depth < 3:
            for _, val in fi.assigns.get(n.id, []):
                if val is not expr and is_random(val, fi, depth + 1):
                    return True
    return False


def id_derived(expr, fi: FuncInfo | None, depth=0) -> bool:
    for n in ast.walk(expr):
        if isinstance(n, ast.Name) and ID_IDENT.search(n.id):
            return True
        if isinstance(n, ast.Attribute) and ID_IDENT.search(n.attr):
            return True
        if isinstance(n, ast.Subscript):
            s = str_value(n.slice)
            if s and ID_IDENT.search(s):
                return True
        if isinstance(n, ast.Name) and fi is not None and depth < 3:
            for _, val in fi.assigns.get(n.id, []):
                if val is not expr and id_derived(val, fi, depth + 1):
                    return True
    return False


def _is_none(expr) -> bool:
    return isinstance(expr, ast.Constant) and expr.value is None


def key_evidence(call: ast.Call, fi: FuncInfo | None) -> str | None:
    """'ok' (deterministic key), 'random' (key is random per call) or None (no key)."""
    found = None
    for kw in call.keywords:
        if kw.arg is None:          # **kwargs: may carry a key; precision over recall
            return "ok"
        val = kw.value
        if kw.arg in IDEM_KWARGS or kw.arg == "key":
            if _is_none(val):
                continue
            if is_random(val, fi):
                found = "random"; continue
            if kw.arg in IDEM_KWARGS or id_derived(val, fi):
                return "ok"
        if isinstance(val, ast.Dict):
            for k, v in zip(val.keys, val.values):
                if k is not None and (str_value(k) or "").lower() == "idempotency-key":
                    if is_random(v, fi):
                        found = "random"
                    else:
                        return "ok"
    return found


def decorator_names(fn) -> set:
    out = set()
    for d in getattr(fn, "decorator_list", []):
        target = d.func if isinstance(d, ast.Call) else d
        ln = last_name(target)
        if ln:
            out.add(ln)
    return out


def has_ledger_decorator(fn) -> bool:
    return bool(decorator_names(fn) & LEDGER_DECORATORS)


def func_mentions_idem_header(fn) -> bool:
    for n in ast.walk(fn):
        s = str_value(n) if isinstance(n, (ast.Constant, ast.JoinedStr)) else None
        if s and s.lower() == "idempotency-key":
            return True
    return False


class Classifier:
    def __init__(self, fctx, effect_names=()):
        self.f = fctx
        self.user = [n.replace(":", ".").strip() for n in effect_names if n and n.strip()]

    def _user_match(self, d, full, last):
        for n in self.user:
            if "." in n:
                if d == n or d.endswith("." + n) or full == n or full.endswith("." + n):
                    return True
            elif last == n:
                return True
        return False

    def classify(self, call: ast.Call, fi: FuncInfo | None, resolve_local=True) -> Effect | None:
        func = call.func
        d = dotted(func)
        last = last_name(func)
        if d is None or last is None:
            return None
        root = d.split(".")[0]
        full = self.f.expand(d)
        if root in LOG_ROOTS or last in NEVER_NAMES or ".logger." in "." + d or ".log." in "." + d + ".":
            if not self._user_match(d, full, last):
                return None
        if self._user_match(d, full, last):
            return Effect(d, call, "user")
        # one level of local resolution: a call to a function defined in this file is an effect
        # only if its body directly makes one (no deeper, no dynamic dispatch)
        target = self.f.resolve_local(call, fi)
        if target is not None:
            if not resolve_local:
                return None
            tfi = self.f.funcs.get(target)
            for inner in self.f.own_calls(target):
                e = self.classify(inner, tfi, resolve_local=False)
                if e is not None:
                    return Effect(d, call, "local", inner=e)
            return None
        # open(path, "w"|"a"|"x"|"+")
        if isinstance(func, ast.Name) and last == "open" or full in ("io.open", "builtins.open"):
            mode = call.args[1] if len(call.args) > 1 else next(
                (k.value for k in call.keywords if k.arg == "mode"), None)
            m = str_value(mode) if mode is not None else None
            return Effect(d, call, "client") if m and any(c in m for c in "wax+") else None
        # SQL execute: decided by the statement, not by the verb
        if last in ("execute", "executemany", "executescript"):
            sql = self._sql_literal(call, fi)
            if sql is not None:
                if WRITE_SQL.search(sql) and not IDEMPOTENT_SQL.search(sql):
                    return Effect(d, call, "client")
                return None
            recv = d.rsplit(".", 1)[0].split(".")[-1] if "." in d else ""
            if SQLISH_RECEIVER.match(recv.lower()):
                return None
        froot = full.split(".")[0]
        if froot in ("requests", "httpx"):
            if last in HTTP_WRITE:
                return Effect(d, call, "client")
            if last == "request":
                return Effect(d, call, "client") if self._http_method(call) in {"POST", "PUT", "PATCH", "DELETE"} else None
            return None
        if last == "request" and isinstance(func, ast.Attribute):
            return Effect(d, call, "client") if self._http_method(call) in {"POST", "PUT", "PATCH", "DELETE"} else None
        if froot == "stripe" and last in STRIPE_WRITE:
            return Effect(d, call, "client")
        if last.startswith("send") and (froot == "smtplib" or "smtp" in d.lower()):
            return Effect(d, call, "client")
        if froot == "subprocess" or (isinstance(func, ast.Name) and full.startswith("subprocess.")):
            return Effect(d, call, "client")
        if froot == "os" and last == "system":
            return Effect(d, call, "client")
        if last[:1].isupper():          # constructor, not a call to a verb
            return None
        if FACTORY_RE.match(last):
            return None
        if isinstance(func, ast.Attribute):
            recv = func.value
            if last in CONTAINER_METHODS:
                rn = recv.id if isinstance(recv, ast.Name) else (recv.attr if isinstance(recv, ast.Attribute) else None)
                if rn is not None and (rn in STATE_NAMES or (fi is not None and rn in fi.bound_containers)):
                    return None
                if last in ("write", "writelines") and rn in WRITE_SINKS:
                    return None
            if isinstance(recv, (ast.Dict, ast.List, ast.Set, ast.Constant, ast.JoinedStr)):
                return None
        if verb_match(last):
            return Effect(d, call, "verb")
        return None

    def _sql_literal(self, call, fi):
        if not call.args:
            return None
        a = call.args[0]
        if isinstance(a, ast.Call) and last_name(a.func) == "text" and a.args:   # sqlalchemy.text("...")
            a = a.args[0]
        s = str_value(a)
        if s is None and isinstance(a, ast.Name) and fi is not None:
            for _, val in reversed(fi.assigns.get(a.id, [])):
                s = str_value(val)
                if s is not None:
                    break
        return s

    @staticmethod
    def _http_method(call):
        m = call.args[0] if call.args else next((k.value for k in call.keywords if k.arg == "method"), None)
        s = str_value(m) if m is not None else None
        return s.upper() if s else None
