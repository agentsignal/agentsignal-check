from agents import Agent, Runner, RunState
from flask import Flask

app = Flask(__name__)
AGENT = Agent(name="a")


@app.route("/approve", methods=["POST"])
async def approve(saved: str):
    state = await RunState.from_string(AGENT, saved)
    for it in state.get_interruptions():
        state.approve(it)
    return await Runner.run(AGENT, state)


@app.route("/approve-once", methods=["POST"])
async def approve_once(saved: str, run_id: str, processed):
    if run_id in processed:
        return "duplicate"
    state = await RunState.from_string(AGENT, saved)
    for it in state.get_interruptions():
        state.approve(it)
    return await Runner.run(AGENT, state)
