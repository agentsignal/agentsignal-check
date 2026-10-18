"""Helpers shared by the LangGraph and OpenAI Agents rules: ordering, guards, handler detection, findings."""
from __future__ import annotations

import ast
import re

from .core import Finding
from .effects import (Effect, dotted, last_name, str_value, key_evidence, has_ledger_decorator,
                      func_mentions_idem_header, decorator_names)
from .. import links

DEDUPE_WORDS = re.compile(r"(ledger|effects|outbox|committed|processed|idempot|dedupe|dedup|seen|handled|delivered)", re.I)
LOOKUP_ATTRS = re.compile(r"(get|exists|lookup|select|fetchone|fetch|contains|has|find|count|sismember|setnx|add)", re.I)
ID_KEY = re.compile(r"(_id|id|_ids|_ref|_key|result|receipt|done|sent|charged|processed|committed|completed)$", re.I)
HANDLER_NAME = re.compile(r"(handler|consumer|on_message)", re.I)


def pos(n):
    return (n.lineno, n.col_offset)


def path_to(fctx, node, stop):
    """[(parent, field, child)] from `stop` down to `node`."""
    out, cur = [], node
    while cur is not None and cur is not stop:
        par = fctx.parent.get(cur)
        if par is None:
            break
        fld = next((f for f, v in ast.iter_fields(par) if v is cur or (isinstance(v, list) and cur in v)), None)
        out.append((par, fld, cur))
        cur = par
    return list(reversed(out))


def mutually_exclusive(fctx, a, b, fn):
    """True if a and b sit in different branches of the same if/elif/else or match statement."""
    pa, pb = path_to(fctx, a, fn), path_to(fctx, b, fn)
    for (p1, f1, c1), (p2, f2, c2) in zip(pa, pb):
        if p1 is not p2:
            return False
        if c1 is not c2:
            if isinstance(p1, ast.If) and {f1, f2} == {"body", "orelse"}:
                return True
            if isinstance(p1, ast.Match) and f1 == f2 == "cases":
                return True
            return False
    return False


def is_ancestor(fctx, anc, node):
    p = fctx.parent.get(node)
    while p is not None:
        if p is anc:
            return True
        p = fctx.parent.get(p)
    return False


def func_evidence(fn):
    """Function-level idempotency evidence: a ledger/once/idempotent decorator or an Idempotency-Key header."""
    return has_ledger_decorator(fn) or func_mentions_idem_header(fn)


def effect_evidence(e: Effect, fi):
    ev = key_evidence(e.call, fi)
    if ev == "ok":
        return "ok"
    if e.inner is not None:
        iev = key_evidence(e.inner.call, None)
        if iev == "ok":
            return "ok"
        ev = ev or iev
    return ev


def effects_in(fctx, clf, fn):
    fi = fctx.funcs.get(fn)
    out = []
    for c in fctx.own_calls(fn):
        e = clf.classify(c, fi)
        if e is not None:
            out.append(e)
    return out


def _test_is_guard(test):
    for n in ast.walk(test):
        if isinstance(n, ast.Call):
            d = dotted(n.func) or ""
            ln = last_name(n.func) or ""
            if ln in ("get", "getattr") and n.args:
                k = str_value(n.args[-1] if ln == "getattr" else n.args[0])
                if k and ID_KEY.search(k):
                    return True
            if DEDUPE_WORDS.search(d) and LOOKUP_ATTRS.search(ln):
                return True
        if isinstance(n, ast.Subscript):
            k = str_value(n.slice)
            if k and ID_KEY.search(k):
                return True
        if isinstance(n, ast.Attribute) and ID_KEY.search(n.attr) and not isinstance(n.ctx, ast.Store):
            if isinstance(n.value, ast.Name) and n.value.id in ("state", "s", "st", "self"):
                return True
        if isinstance(n, ast.Compare) and any(isinstance(o, (ast.In, ast.NotIn)) for o in n.ops):
            for side in [n.left] + n.comparators:
                if DEDUPE_WORDS.search(dotted(side) or ""):
                    return True
    return False


def is_ledger_lookup(call):
    d = dotted(call.func) or ""
    ln = last_name(call.func) or ""
    if DEDUPE_WORDS.search(d) and LOOKUP_ATTRS.search(ln):
        return True
    if ln in ("execute",) and call.args:
        s = str_value(call.args[0]) or ""
        if re.match(r"\s*select\b", s, re.I) and DEDUPE_WORDS.search(s):
            return True
    return False


def has_guard_before(fctx, fn, target):
    """An `if <id/ledger check>` before or around target, or a ledger lookup call before it."""
    tp = pos(target)
    for n in fctx.own_nodes(fn):
        if isinstance(n, ast.If) and _test_is_guard(n.test):
            if pos(n) < tp or is_ancestor(fctx, n, target):
                return True
        if isinstance(n, (ast.While,)) and _test_is_guard(n.test) and pos(n) < tp:
            return True
        if isinstance(n, ast.Call) and pos(n) < tp and is_ledger_lookup(n):
            return True
    return False


HTTP_WRITE_METHODS = {"do_POST", "do_PUT", "do_PATCH", "do_DELETE"}   # http.server.BaseHTTPRequestHandler


def is_handler(fn):
    if HANDLER_NAME.search(fn.name) or fn.name in HTTP_WRITE_METHODS:
        return True
    for d in fn.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        name = last_name(target) or ""
        if name == "post" and isinstance(target, ast.Attribute):
            return True
        if name in ("route", "api_route") and isinstance(d, ast.Call):
            for kw in d.keywords:
                if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple, ast.Set)):
                    if any((str_value(e) or "").upper() == "POST" for e in kw.value.elts):
                        return True
    return False


def has_dedupe_before(fctx, fn, target, allow_get_state=True):
    """Processed-id set/table check, get_state().next check, a lock, or a ledger lookup before target."""
    tp = pos(target)
    saw_state = saw_next = False
    for n in fctx.own_nodes(fn):
        before = pos(n) < tp if hasattr(n, "lineno") else False
        if isinstance(n, (ast.With, ast.AsyncWith)) and (before or is_ancestor(fctx, n, target)):
            for it in n.items:
                d = (dotted(it.context_expr) or "").lower()
                if "lock" in d or "advisory" in d or "for_update" in d:
                    return True
        if not before:
            continue
        if isinstance(n, ast.If) and _test_is_guard(n.test):
            return True
        if isinstance(n, ast.Call):
            ln = last_name(n.func) or ""
            if is_ledger_lookup(n) or ln in ("acquire", "setnx", "sadd"):
                return True
            if any(kw.arg == "nx" for kw in n.keywords):
                return True
            if allow_get_state:
                if ln in ("get_state", "aget_state"):
                    saw_state = True
                else:
                    tgt = fctx.resolve_local(n, fctx.funcs.get(fn))
                    if tgt is not None and any(last_name(c.func) in ("get_state", "aget_state") for c in fctx.own_calls(tgt)):
                        saw_state = saw_next = True
        if isinstance(n, ast.Attribute) and n.attr in ("next", "tasks", "interrupts"):
            saw_next = True
        if isinstance(n, ast.Compare) and any(isinstance(o, (ast.In, ast.NotIn)) for o in n.ops):
            if any(DEDUPE_WORDS.search(dotted(s) or "") for s in [n.left] + n.comparators):
                return True
    return saw_state and saw_next


def finding(fctx, rule, conf, fw, fn, line, col, message, short, group="rerun", effect: Effect | None = None, fix=None):
    return Finding(
        rule=rule, severity=conf, confidence=conf, framework=fw,
        function=getattr(fn, "name", "<module>"), file=fctx.path, line=line, col=col,
        effect_call=(effect.name + "()") if effect else None, effect_line=effect.line if effect else None,
        message=message, fix_url=fix or links.scan_fix_url(rule, fw), short=short, group=group,
        def_line=getattr(fn, "lineno", 0), effect_full=fctx.expand(effect.name) if effect else None,
    )


def is_function_tool(fn, fctx):
    """@function_tool / @function_tool(...) / @agents.function_tool; returns (is_tool, needs_approval)."""
    for d in fn.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        name = last_name(target)
        if name != "function_tool":
            continue
        needs = False
        if isinstance(d, ast.Call):
            for kw in d.keywords:
                if kw.arg == "needs_approval":
                    needs = not (isinstance(kw.value, ast.Constant) and kw.value.value in (False, None))
        return True, needs
    return False, False


__all__ = [n for n in dir() if not n.startswith("_")] + ["decorator_names"]
