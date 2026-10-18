"""LangGraph rules AS101-AS105 (pure ``ast``)."""
from __future__ import annotations

import ast

from .effects import dotted, last_name, str_value, decorator_names
from .rules_common import (pos, mutually_exclusive, is_ancestor, func_evidence, effect_evidence, effects_in,
                           has_guard_before, has_ledger_decorator, is_handler, has_dedupe_before, finding)

FW = "langgraph"
LOOPS = (ast.While, ast.For, ast.AsyncFor)
START, END = "__start__", "__end__"


def is_interrupt(fctx, call):
    f = call.func
    if isinstance(f, ast.Name):
        full = fctx.expand(f.id)
        return full.startswith("langgraph.") and full.endswith(".interrupt")
    return isinstance(f, ast.Attribute) and f.attr == "interrupt"


def _interrupts(fctx, fn):
    return [c for c in fctx.own_calls(fn) if is_interrupt(fctx, c)]


def _node_key(fctx, expr):
    """Edge endpoint: string constant, START/END names, else None."""
    s = str_value(expr) if not isinstance(expr, ast.JoinedStr) else None
    if s is not None:
        return s
    if isinstance(expr, (ast.Name, ast.Attribute)):
        ln = last_name(expr)
        if ln == "START":
            return START
        if ln == "END":
            return END
    return None


def _resolve_node_fn(fctx, expr, at):
    """A node callable -> list of local function defs (Name, or a call to a local factory returning nested defs)."""
    if isinstance(expr, ast.Name):
        d = fctx.resolve_name(expr.id, at)
        return [d] if d is not None else []
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
        fac = fctx.resolve_name(expr.func.id, at)
        if fac is None:
            return []
        out = []
        for n in fctx.own_nodes(fac):
            if isinstance(n, ast.Return) and isinstance(n.value, ast.Name):
                d = fctx.scope_defs.get(fac, {}).get(n.value.id)
                if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append(d)
        return out
    return []


class Builder:
    def __init__(self):
        self.nodes = {}          # name -> [fn defs] ([] = unresolved / external)
        self.edges = {}          # src -> set(dst) ; "*" = unknown (any node)

    def edge(self, a, b):
        self.edges.setdefault(a, set()).add(b)


def collect_builders(fctx):
    builders = {}
    for fn_scope in [fctx.tree] + list(fctx.funcs):
        for n in (fctx.own_nodes(fn_scope) if fn_scope is not fctx.tree else _module_nodes(fctx)):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
                continue
            meth = n.func.attr
            if meth not in ("add_node", "add_edge", "add_conditional_edges", "add_sequence"):
                continue
            recv = dotted(n.func.value) or "?"
            b = builders.setdefault((id(fn_scope), recv), Builder())
            if meth == "add_node":
                if len(n.args) >= 2:
                    name, fexpr = str_value(n.args[0]), n.args[1]
                elif len(n.args) == 1:
                    name, fexpr = None, n.args[0]
                else:
                    fexpr = next((k.value for k in n.keywords if k.arg in ("action", "node")), None)
                    name = None
                    if fexpr is None:
                        continue
                fns = _resolve_node_fn(fctx, fexpr, n)
                if name is None:
                    name = fns[0].name if fns else (last_name(fexpr) or "?")
                b.nodes.setdefault(name, []).extend(fns)
                for k in n.keywords:
                    if k.arg == "destinations" and isinstance(k.value, (ast.Tuple, ast.List, ast.Set)):
                        for e in k.value.elts:
                            dst = _node_key(fctx, e)
                            b.edge(name, dst if dst else "*")
                    elif k.arg == "destinations" and isinstance(k.value, ast.Dict):
                        for e in k.value.keys:
                            dst = _node_key(fctx, e) if e is not None else None
                            b.edge(name, dst if dst else "*")
            elif meth == "add_edge" and len(n.args) >= 2:
                srcs = n.args[0].elts if isinstance(n.args[0], (ast.List, ast.Tuple)) else [n.args[0]]
                dst = _node_key(fctx, n.args[1]) or "*"
                for s in srcs:
                    b.edge(_node_key(fctx, s) or "*", dst)
            elif meth == "add_conditional_edges" and n.args:
                src = _node_key(fctx, n.args[0]) or "*"
                pm = n.args[2] if len(n.args) >= 3 else next((k.value for k in n.keywords if k.arg == "path_map"), None)
                if isinstance(pm, (ast.List, ast.Tuple, ast.Set)):
                    for e in pm.elts:
                        b.edge(src, _node_key(fctx, e) or "*")
                elif isinstance(pm, ast.Dict):
                    for v in pm.values:
                        b.edge(src, _node_key(fctx, v) or "*")
                else:
                    b.edge(src, "*")
            elif meth == "add_sequence" and n.args and isinstance(n.args[0], (ast.List, ast.Tuple)):
                prev = None
                for e in n.args[0].elts:
                    fns = _resolve_node_fn(fctx, e, n)
                    nm = fns[0].name if fns else (last_name(e) or "?")
                    b.nodes.setdefault(nm, []).extend(fns)
                    if prev:
                        b.edge(prev, nm)
                    prev = nm
    # Command(goto=...) returned from a node is an edge too
    for b in builders.values():
        for name, fns in b.nodes.items():
            for fn in fns:
                for c in fctx.own_calls(fn):
                    if last_name(c.func) == "Command":
                        for k in c.keywords:
                            if k.arg == "goto":
                                vals = k.value.elts if isinstance(k.value, (ast.List, ast.Tuple)) else [k.value]
                                for v in vals:
                                    b.edge(name, _node_key(fctx, v) or "*")
    return list(builders.values())


def _module_nodes(fctx):
    """Module-level statements' nodes (not descending into defs/classes)."""
    stack = list(fctx.tree.body)
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(n))


def _fn_interrupts(fctx, fn):
    """interrupt() in fn, or one level down in a local function it calls."""
    if _interrupts(fctx, fn):
        return True
    fi = fctx.funcs.get(fn)
    for c in fctx.own_calls(fn):
        t = fctx.resolve_local(c, fi)
        if t is not None and t is not fn and _interrupts(fctx, t):
            return True
    return False


def after_interrupt_nodes(fctx, b):
    """Nodes reachable after a node that calls interrupt() (only from resolvable local node functions)."""
    inodes = {name for name, fns in b.nodes.items() if any(_fn_interrupts(fctx, f) for f in fns)}
    if not inodes:
        return set()
    if not b.edges:                       # routing not visible: any other node may follow
        return set(b.nodes) - inodes
    seen, todo = set(), list(inodes)
    while todo:
        cur = todo.pop()
        dsts = b.edges.get(cur, set())
        if "*" in dsts:                       # unknown destination: any node may follow
            dsts = set(b.nodes)
        for d in dsts:
            if d in b.nodes and d not in seen:
                seen.add(d)
                todo.append(d)
    return seen


def _loop_with_interrupt(fctx, fn, call, ints):
    p = fctx.parent.get(call)
    while p is not None and p is not fn:
        if isinstance(p, LOOPS) and any(is_ancestor(fctx, p, i) for i in ints):
            return p
        p = fctx.parent.get(p)
    return None


def run(fctx, clf):
    out = []
    reported = set()          # id(effect call) already reported by AS101/AS102
    any_interrupt = False

    # AS101 / AS102: effect before interrupt() / effect in a loop with interrupt()
    for fn in fctx.funcs:
        ints = _interrupts(fctx, fn)
        if not ints:
            continue
        any_interrupt = True
        if func_evidence(fn):
            continue
        fi = fctx.funcs.get(fn)
        for e in effects_in(fctx, clf, fn):
            if effect_evidence(e, fi) == "ok":
                continue
            loop = _loop_with_interrupt(fctx, fn, e.call, ints)
            if loop is not None:
                i = next(i for i in ints if is_ancestor(fctx, loop, i))
                kind = "`while` loop" if isinstance(loop, ast.While) else "`for` loop"
                out.append(finding(
                    fctx, "AS102", "high", FW, fn, e.line, e.call.col_offset,
                    f"`{e.name}()` (L{e.line}) and `interrupt()` (L{i.lineno}) share a {kind} in `{fn.name}()`; "
                    f"on resume LangGraph re-runs the node from the top, so `{e.name}` runs again.",
                    f"{e.name}() in a loop with interrupt() at :{i.lineno}", effect=e))
                reported.add(id(e.call))
                continue
            later = [i for i in ints if pos(i) > pos(e.call) and not mutually_exclusive(fctx, e.call, i, fn)]
            if not later:
                continue
            i = later[0]
            out.append(finding(
                fctx, "AS101", "high", FW, fn, e.line, e.call.col_offset,
                f"`{fn.name}()` calls `{e.name}()` before `interrupt()` at L{i.lineno}; LangGraph re-runs the whole "
                f"node on resume, so `{e.name}` (L{e.line}) runs again.",
                f"{e.name}() runs before interrupt() at :{i.lineno}", effect=e))
            reported.add(id(e.call))

    # AS103 / AS104: node (or tool) reachable after an interrupt node, effect without key / guard
    candidates = []           # (fn, min_pos or None)
    for b in collect_builders(fctx):
        after = after_interrupt_nodes(fctx, b)
        for name, fns in b.nodes.items():
            for fn in fns:
                ints = _interrupts(fctx, fn)
                if name in after:
                    candidates.append((fn, None))
                elif ints:
                    candidates.append((fn, pos(ints[0])))     # effects after interrupt() in the same node
    if any_interrupt:
        for fn in fctx.funcs:
            if "tool" in decorator_names(fn):
                candidates.append((fn, None))
        for n in ast.walk(fctx.tree):
            if isinstance(n, ast.Call) and last_name(n.func) == "ToolNode" and n.args and isinstance(n.args[0], (ast.List, ast.Tuple)):
                for el in n.args[0].elts:
                    for fn in _resolve_node_fn(fctx, el, n):
                        candidates.append((fn, None))
    done = set()
    for fn, after_pos in candidates:
        if (id(fn), after_pos) in done:
            continue
        done.add((id(fn), after_pos))
        if func_evidence(fn):
            continue
        fi = fctx.funcs.get(fn)
        for e in effects_in(fctx, clf, fn):
            if id(e.call) in reported or (after_pos is not None and pos(e.call) <= after_pos):
                continue
            ev = effect_evidence(e, fi)
            if ev == "ok":
                continue
            reported.add(id(e.call))
            if ev == "random":
                msg = f"`{e.name}()` in `{fn.name}()` runs after an approval; its idempotency key is random per call, so a re-run is not deduplicated."
                short = f"{e.name}(): key is random per call"
            else:
                msg = f"`{e.name}()` in `{fn.name}()` runs after an approval with no idempotency key; a retried or replayed node repeats it."
                short = f"{e.name}() has no idempotency key"
            out.append(finding(fctx, "AS103", "medium", FW, fn, e.line, e.call.col_offset, msg, short, effect=e))
            if not has_ledger_decorator(fn) and not has_guard_before(fctx, fn, e.call):
                out.append(finding(
                    fctx, "AS104", "medium", FW, fn, e.line, e.call.col_offset,
                    f"`{fn.name}()` runs on the resume path and calls `{e.name}()` with no ledger lookup or "
                    f"`if state.get(<id>)` guard before it.",
                    f"no ledger lookup before {e.name}() on resume path", effect=e))

    # AS105: Command(resume=...) inside a web handler / queue consumer with no dedupe evidence
    for fn in fctx.funcs:
        if not is_handler(fn):
            continue
        for c in fctx.own_calls(fn):
            if last_name(c.func) == "Command" and any(k.arg == "resume" for k in c.keywords):
                if not has_dedupe_before(fctx, fn, c):
                    out.append(finding(
                        fctx, "AS105", "medium", FW, fn, c.lineno, c.col_offset,
                        f"`{fn.name}()` resumes with `Command(resume=...)` and no processed-id, state or lock check "
                        f"first; a double-click or redelivery resumes twice.",
                        "Command(resume=...) with no processed-id check", group="handler"))
                break
    return out
