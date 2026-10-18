"""stdlib http.server plumbing (send_response / wfile.write) is not a side effect; LOCK + status check = dedupe."""
from http.server import BaseHTTPRequestHandler
from threading import Lock
from langgraph.types import Command
from app_graph import graph

LOCK = Lock()


class H(BaseHTTPRequestHandler):
    def _send(self, code, body):
        b = body.encode(); self.send_response(code); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def do_POST(self):
        with LOCK:
            if graph.get_state({}).next == ():
                return self._send(409, "already decided")
            graph.invoke(Command(resume=True), {})
        self._send(200, "ok")
