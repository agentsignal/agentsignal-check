from langgraph.types import interrupt
import notifier


def review_loop(s):
    while True:
        notifier.send_message(s["user"], "please review")
        ok = interrupt({"q": "approve?"})
        if ok:
            return {"ok": True}
