import logging
from langgraph.types import interrupt
logger = logging.getLogger(__name__)


def node(state):
    logger.info("about to ask")
    print("asking")
    out = {}
    out.update({"asked": True})
    state.update({"x": 1})
    discharge = state.get("discharge")
    return {"ok": interrupt({"q": "ok?", "d": discharge}), **out}
