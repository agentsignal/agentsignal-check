from langgraph.types import interrupt
import payments


def pay(s):
    cid = payments.charge(s["amount"])  # agentsignal: ignore[AS101]
    ok = interrupt({"q": "confirm?"})
    return {"cid": cid, "ok": ok}


def pay_bare(s):  # agentsignal: ignore
    cid = payments.charge(s["amount"])
    ok = interrupt({"q": "confirm?"})
    return {"cid": cid, "ok": ok}


def pay_other_rule(s):
    cid = payments.charge(s["amount"])  # agentsignal: ignore[AS201]
    ok = interrupt({"q": "confirm?"})
    return {"cid": cid, "ok": ok}
