"""Minimal approval UI for paused LangGraph runs. Python stdlib http.server only (no web framework).
GET /            HTML list of runs, with Approve/Reject forms for paused ones
POST /runs       start a run (form field: action) -> pauses at interrupt()
POST /runs/<id>/decision  (form: decision=approve|reject, note=...) -> Command(resume=...)
GET /api/runs    JSON state of all runs"""
import html, json, sys, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from typing import TypedDict
from urllib.parse import parse_qs
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import interrupt, Command

class S(TypedDict, total=False):
    action: str
    decision: dict
    result: str

def approval(s):
    return {"decision": interrupt({"question": "Approve this action?", "action": s["action"]})}
def execute(s):
    d = s["decision"]
    return {"result": f"EXECUTED: {s['action']}" if d["approved"] else f"REJECTED: {d.get('note') or 'no reason'}"}
b = StateGraph(S); b.add_node("approval", approval); b.add_node("execute", execute)
b.add_edge(START, "approval"); b.add_edge("approval", "execute"); b.add_edge("execute", END)
graph = b.compile(checkpointer=InMemorySaver())
RUNS: list[str] = []; LOCK = Lock()

def cfg(tid): return {"configurable": {"thread_id": tid}}
def run_info(tid):
    st = graph.get_state(cfg(tid))
    pending = [i.value for t in st.tasks for i in t.interrupts]
    return {"id": tid, "status": "paused" if pending else "done", "pending": pending,
            "action": st.values.get("action"), "result": st.values.get("result")}

PAGE = """<!doctype html><title>Approvals</title><h1>LangGraph approvals</h1>
<form method=post action=/runs><input name=action value="refund order #42 ($19.99)" size=40><button>Start run</button></form><hr>{rows}"""
def render():
    rows = []
    for tid in reversed(RUNS):
        r = run_info(tid); e = html.escape
        if r["status"] == "paused":
            rows.append(f"<div><b>{e(tid)}</b> PAUSED: {e(r['pending'][0]['question'])} <code>{e(r['action'])}</code>"
                        f"<form method=post action=/runs/{tid}/decision><input name=note placeholder=note>"
                        f"<button name=decision value=approve>Approve</button><button name=decision value=reject>Reject</button></form></div>")
        else:
            rows.append(f"<div><b>{e(tid)}</b> DONE: {e(r['result'] or '')}</div>")
    return PAGE.format(rows="\n".join(rows) or "<p>No runs yet.</p>")

class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html"):
        b = body.encode(); self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def _redirect(self): self.send_response(303); self.send_header("Location", "/"); self.end_headers()
    def _form(self):
        n = int(self.headers.get("Content-Length", 0)); q = parse_qs(self.rfile.read(n).decode())
        return {k: v[0] for k, v in q.items()}
    def do_GET(self):
        if self.path == "/": return self._send(200, render())
        if self.path == "/api/runs": return self._send(200, json.dumps([run_info(t) for t in RUNS]), "application/json")
        self._send(404, "not found", "text/plain")
    def do_POST(self):
        f = self._form()
        if self.path == "/runs":
            tid = "run-" + uuid.uuid4().hex[:6]
            with LOCK: graph.invoke({"action": f.get("action", "noop")}, cfg(tid)); RUNS.append(tid)
            return self._redirect()
        parts = self.path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "runs" and parts[2] == "decision" and parts[1] in RUNS:
            tid = parts[1]
            with LOCK:
                if run_info(tid)["status"] != "paused":
                    return self._send(409, "run is not paused (already decided)", "text/plain")
                if f.get("decision") not in ("approve", "reject"):
                    return self._send(400, "decision must be approve or reject", "text/plain")
                graph.invoke(Command(resume={"approved": f["decision"] == "approve", "note": f.get("note", "")}), cfg(tid))
            return self._redirect()
        self._send(404, "not found", "text/plain")
    def log_message(self, fmt, *a): print("  [http]", fmt % a, flush=True)

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"serving on http://127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
