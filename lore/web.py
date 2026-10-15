"""Web UI: ask a question, get the answer, the tools the agent used, and the graph path behind it.

Run: .venv/bin/uvicorn lore.web:app --port 8000
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import agent, brain
from .db import connect, rows
from .memory import evidence_keys

app = FastAPI(title="Lore")
STATIC = Path(__file__).parent / "static"
LABELS = ("Person", "Team", "Module", "Doc", "Decision", "Ticket", "File")


class Question(BaseModel):
    question: str
    use_memory: bool = True


def _shas(obj) -> set[str]:
    found: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            found |= {v} if k == "sha" and isinstance(v, str) else _shas(v)
    elif isinstance(obj, list):
        for v in obj:
            found |= _shas(v)
    return found


def subgraph(g, trace: list[dict]) -> dict:
    """The nodes an answer was built from, and every edge between them."""
    results = [t["result"] for t in trace]
    keys, shas = sorted(evidence_keys(results)), sorted(_shas(results))
    nodes = []
    for label in LABELS:
        nodes += rows(g, f"""
            MATCH (n:{label}) WHERE n.key IN $keys
            RETURN id(n) AS id, '{label}' AS label, coalesce(n.name, n.title, n.path, n.key) AS name""", keys=keys)
    nodes += rows(g, """
        MATCH (c:Commit) WHERE c.sha IN $shas
        RETURN id(c) AS id, 'Commit' AS label, substring(c.sha, 0, 8) + ' ' + substring(c.message, 0, 40) AS name""",
        shas=shas)
    ids = [n["id"] for n in nodes]
    edges = rows(g, """
        MATCH (a)-[r]->(b) WHERE id(a) IN $ids AND id(b) IN $ids
        RETURN id(a) AS source, type(r) AS type, id(b) AS target""", ids=ids)
    edges += rows(g, """
        MATCH (c:Commit)-[:TOUCHED]->(:File)-[:IN]->(m:Module) WHERE id(c) IN $ids AND id(m) IN $ids
        RETURN DISTINCT id(c) AS source, 'TOUCHED' AS type, id(m) AS target""", ids=ids)
    return {"nodes": nodes, "edges": edges}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.post("/api/ask")
def ask(q: Question):
    g = connect()
    r = agent.ask(g, q.question, use_memory=q.use_memory)
    r["graph"] = subgraph(g, r["trace"])
    return r


@app.get("/api/overview")
def overview():
    g = connect()
    o = brain.overview(g)
    o["llm"] = agent.llm_available()
    return o
