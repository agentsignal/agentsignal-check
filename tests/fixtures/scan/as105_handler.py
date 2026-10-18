from fastapi import FastAPI
from langgraph.types import Command
from app_graph import graph

app = FastAPI()
PROCESSED = set()


@app.post("/runs/{tid}/approve")
def approve(tid: str):
    return graph.invoke(Command(resume=True), {"configurable": {"thread_id": tid}})


@app.post("/runs/{tid}/approve-once")
def approve_once(tid: str, decision_id: str):
    if decision_id in PROCESSED:
        return {"status": "duplicate"}
    PROCESSED.add(decision_id)
    return graph.invoke(Command(resume=True), {"configurable": {"thread_id": tid}})


@app.post("/runs/{tid}/approve-checked")
def approve_checked(tid: str):
    cfg = {"configurable": {"thread_id": tid}}
    if not graph.get_state(cfg).next:
        return {"status": "already decided"}
    return graph.invoke(Command(resume=True), cfg)
