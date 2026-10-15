"""The brain remembers what it was asked — and knows when a remembered answer has gone stale.

Every answer becomes an (:Answer) node linked by USED edges to the modules, docs, people and tickets it was
built from. When new commits land on that evidence, the answer is outdated, and the graph can say exactly why.
"""

from __future__ import annotations

import re
import time

from . import brain
from .db import rows

LABELS = ("Module", "Doc", "Person", "Ticket")


def norm(question: str) -> str:
    return " ".join(w for w in re.findall(r"[a-z0-9]+", question.lower()) if w not in brain.STOP)


def _similar(a: str, b: str) -> float:
    x, y = set(a.split()), set(b.split())
    return len(x & y) / len(x | y) if x and y else 0.0


Q_RECALL = """
CALL db.idx.fulltext.queryNodes('Answer', $q) YIELD node, score
RETURN node.key AS key, node.question AS question, node.answer AS answer, node.asof AS asof, node.ts AS ts,
       node.asked AS asked, node.trace AS trace
ORDER BY score DESC LIMIT 5"""

Q_MODULES_CHANGED = """
MATCH (a:Answer {key: $key})-[:USED]->(m:Module)<-[:IN]-(:File)<-[:TOUCHED]-(c:Commit)
WHERE c.ts > a.asof
RETURN m.path AS what, count(DISTINCT c) AS changes
ORDER BY changes DESC LIMIT 5"""

Q_DOCS_CHANGED = """
MATCH (a:Answer {key: $key})-[:USED]->(d:Doc)-[:FILE]->(:File)<-[:TOUCHED]-(c:Commit)
WHERE c.ts > a.asof
RETURN d.path AS what, count(DISTINCT c) AS changes
ORDER BY changes DESC LIMIT 5"""


def recall(g, question: str) -> dict | None:
    """A previous answer to (nearly) the same question, with what has changed since it was given."""
    key = norm(question)
    q = brain.fulltext(question)
    if not q:
        return None
    best = None
    for r in rows(g, Q_RECALL, q=q):
        if r["key"] == key or _similar(r["key"], key) >= 0.75:
            best = r
            break
    if not best:
        return None
    best["changed_since"] = [r for q_ in (Q_MODULES_CHANGED, Q_DOCS_CHANGED)
                             for r in rows(g, q_, key=best["key"]) if r["changes"]]
    best["fresh"] = not best["changed_since"]
    return best


def evidence_keys(obj) -> set[str]:
    """Every node key mentioned anywhere in a tool result."""
    found: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("key", "module_key") and isinstance(v, str):
                found.add(v)
            else:
                found |= evidence_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            found |= evidence_keys(v)
    return found


def store(g, question: str, answer: str, trace: list[dict], asof: int, trace_json: str = "") -> int:
    key = norm(question)
    g.query("""
        MERGE (a:Answer {key: $key})
        SET a.question = $question, a.answer = $answer, a.asof = $asof, a.ts = $ts,
            a.asked = coalesce(a.asked, 0) + 1, a.trace = $trace""",
            {"key": key, "question": question, "answer": answer, "asof": asof, "ts": int(time.time()), "trace": trace_json})
    g.query("MATCH (:Answer {key: $key})-[u:USED]->() DELETE u", {"key": key})
    keys = sorted(evidence_keys([t["result"] for t in trace]))
    linked = 0
    for label in LABELS:
        res = g.query(f"""
            MATCH (a:Answer {{key: $key}})
            UNWIND $keys AS k
            MATCH (n:{label} {{key: k}})
            MERGE (a)-[:USED]->(n)
            RETURN count(n)""", {"key": key, "keys": keys})
        linked += res.result_set[0][0] if res.result_set else 0
    return linked
