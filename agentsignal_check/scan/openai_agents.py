"""OpenAI Agents SDK rules AS201-AS204 (pure ``ast``)."""
from __future__ import annotations

import ast

from .effects import dotted, last_name
from .rules_common import (func_evidence, effect_evidence, effects_in, is_handler, has_dedupe_before, finding,
                           is_function_tool, pos)

FW = "openai-agents"


def run(fctx, clf):
    out = []
    has_approval_tools = False
    # AS201 / AS202: tool effect without an idempotency key
    for fn in fctx.funcs:
        is_tool, needs = is_function_tool(fn, fctx)
        if not is_tool:
            continue
        has_approval_tools |= needs
        if func_evidence(fn):
            continue
        fi = fctx.funcs.get(fn)
        for e in effects_in(fctx, clf, fn):
            ev = effect_evidence(e, fi)
            if ev == "ok":
                continue
            rule, sev = ("AS201", "high") if needs else ("AS202", "medium")
            what = "approved tool" if needs else "tool"
            if ev == "random":
                msg = (f"{what} `{fn.name}()` calls `{e.name}()` with an idempotency key that is random per call; "
                       f"a resumed or retried run repeats it.")
                short = f"{what}: key is random per call"
            else:
                msg = (f"{what} `{fn.name}()` calls `{e.name}()` with no idempotency key; a duplicate resume of the "
                       f"saved RunState or a whole-run retry repeats it.")
                short = f"{what} has no idempotency key"
            out.append(finding(fctx, rule, sev, FW, fn, e.line, e.call.col_offset, msg, short, effect=e))
    # AS203: RunState restored + approved/run inside a web handler / queue consumer without dedupe
    for fn in fctx.funcs:
        if not is_handler(fn):
            continue
        calls = fctx.own_calls(fn)
        restore = next((c for c in calls if last_name(c.func) in ("from_json", "from_string")
                        and "RunState" in (fctx.expand(dotted(c.func) or ""))), None)
        if restore is None:
            continue
        resumed = any(pos(c) > pos(restore) and (last_name(c.func) == "approve" or
                      (last_name(c.func) in ("run", "run_sync", "run_streamed") and "Runner" in (dotted(c.func) or "")))
                      for c in calls)
        if resumed and not has_dedupe_before(fctx, fn, restore, allow_get_state=False):
            out.append(finding(
                fctx, "AS203", "medium", FW, fn, restore.lineno, restore.col_offset,
                f"`{fn.name}()` restores a RunState and resumes it with no processed-id or ledger check first; "
                f"a double-click or redelivery resumes the same run twice.",
                "RunState resumed with no processed-id check", group="handler"))
    # AS204: needs_approval tools + interruptions handled, but the state is never serialized
    if has_approval_tools:
        handled = [n for n in fctx.nodes if (isinstance(n, ast.Attribute) and n.attr == "interruptions")
                   or (isinstance(n, ast.Call) and last_name(n.func) == "get_interruptions")]
        persisted = any(isinstance(n, ast.Call) and last_name(n.func) in ("to_json", "to_string")
                        for n in fctx.nodes)
        if handled and not persisted:
            first = min(handled, key=pos)
            fn = fctx.enclosing(first) or fctx.tree
            out.append(finding(
                fctx, "AS204", "low", FW, fn, first.lineno, first.col_offset,
                "approval-gated tools pause the run, but the RunState is never saved with to_json()/to_string(); "
                "a restart loses the pending approval.",
                "pending approval never persisted", group="persist"))
    return out
