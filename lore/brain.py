"""The company brain: every answer is a graph traversal, and every answer carries the path that produced it.

These functions are also the agent's tools.
"""

from __future__ import annotations

import re
from collections import defaultdict

from .db import rows

DAY = 86400
ACTIVE_WINDOW = 180 * DAY
STOP = set("the and for with that this from what who why how does are was were have has our not but you any can into "
           "about when where which use used using did do we us it its they them get got make made want need should "
           "would could there their then than also just like".split())


def fulltext(q: str) -> str:
    """Free text -> a RediSearch OR-query FalkorDB's full-text index accepts."""
    words = [w for w in re.findall(r"[A-Za-z0-9]{3,}", q.lower()) if w not in STOP]
    return "|".join(dict.fromkeys(words))


def now(g) -> int:
    """'Now' is the latest commit we've seen, so an archived org still reads sensibly."""
    r = rows(g, "MATCH (r:Repo) RETURN max(r.head_ts) AS now")
    return r[0]["now"] if r and r[0]["now"] else 0


# ---------------------------------------------------------------- people

Q_FIND_PERSON = """
MATCH (p:Person)
WHERE toLower(p.key) CONTAINS $q OR toLower(coalesce(p.name, '')) CONTAINS $q
RETURN p.key AS key, p.name AS name, coalesce(p.commits, 0) AS commits, p.last_active AS last_active
ORDER BY commits DESC LIMIT 5"""


def find_person(g, q: str) -> list[dict]:
    return rows(g, Q_FIND_PERSON, q=q.lower().lstrip("@"))


# ---------------------------------------------------------------- who knows about X

Q_TOPIC_MODULES = """
CALL db.idx.fulltext.queryNodes('Commit', $q) YIELD node, score
MATCH (node)-[:TOUCHED]->(:File)-[:IN]->(m:Module)
WHERE m.path <> '(root)'
RETURN m.key AS key, m.path AS module, score
UNION ALL
CALL db.idx.fulltext.queryNodes('Doc', $q) YIELD node, score
MATCH (node)-[:MENTIONS]->(:File)-[:IN]->(m:Module)
WHERE m.path <> '(root)'
RETURN m.key AS key, m.path AS module, score"""

Q_MODULE_BY_PATH = """
MATCH (m:Module) WHERE toLower(m.path) CONTAINS $term
RETURN m.key AS key, m.path AS module"""

Q_EXPERTS = """
MATCH (p:Person)-[k:KNOWS]->(m:Module)
WHERE m.key IN $modules
RETURN p.key AS key, p.name AS name, p.last_active AS last_active,
       m.key AS module_key, m.path AS module, k.score AS score, k.commits AS commits, k.last AS last"""

Q_EVIDENCE = """
MATCH (p:Person {key: $person})-[:AUTHORED]->(c:Commit)-[:TOUCHED]->(:File)-[:IN]->(m:Module)
WHERE m.key IN $modules
RETURN DISTINCT c.sha AS sha, c.message AS message, c.ts AS ts
ORDER BY ts DESC LIMIT 2"""

Q_OWNERS = """
MATCH (o)-[:OWNS]->(:File)-[:IN]->(m:Module)
WHERE m.key IN $modules
RETURN coalesce(o.name, o.key) AS owner, labels(o)[0] AS kind, count(*) AS files
ORDER BY files DESC LIMIT 5"""


def topic_modules(g, topic: str, limit: int = 5) -> list[dict]:
    rel: dict[str, float] = defaultdict(float)
    path: dict[str, str] = {}
    if q := fulltext(topic):
        for r in rows(g, Q_TOPIC_MODULES, q=q):
            rel[r["key"]] += r["score"]
            path[r["key"]] = r["module"]
    for term in re.findall(r"[\w/-]{3,}", topic.lower()):
        if term in STOP:
            continue
        for r in rows(g, Q_MODULE_BY_PATH, term=term):
            rel[r["key"]] += 5.0
            path[r["key"]] = r["module"]
    top = sorted(rel, key=rel.get, reverse=True)[:limit]
    return [{"key": k, "module": path[k], "relevance": round(rel[k], 2)} for k in top]


def who_knows(g, topic: str, limit: int = 5) -> dict:
    mods = topic_modules(g, topic)
    if not mods:
        return {"topic": topic, "modules": [], "experts": [], "owners": []}
    weight = {m["key"]: m["relevance"] / mods[0]["relevance"] for m in mods}
    active_since = now(g) - ACTIVE_WINDOW
    people: dict[str, dict] = {}
    for r in rows(g, Q_EXPERTS, modules=list(weight)):
        p = people.setdefault(r["key"], {"key": r["key"], "name": r["name"], "score": 0.0, "areas": [],
                                         "active": (r["last_active"] or 0) >= active_since})
        p["score"] += r["score"] * weight[r["module_key"]]
        p["areas"].append(r["module"])
    experts = sorted(people.values(), key=lambda p: (p["active"], p["score"]), reverse=True)[:limit]
    for p in experts:
        p["score"] = round(p["score"], 2)
        p["evidence"] = rows(g, Q_EVIDENCE, person=p["key"], modules=list(weight))
    return {"topic": topic, "modules": mods, "experts": experts, "owners": rows(g, Q_OWNERS, modules=list(weight))}


# ---------------------------------------------------------------- what if someone leaves

Q_DEPARTURE = """
MATCH (p:Person {key: $person})-[k:KNOWS]->(m:Module)
WHERE k.commits >= 2
OPTIONAL MATCH (o:Person)-[k2:KNOWS]->(m)
WHERE o <> p AND k2.last >= $active_since
WITH m, k, sum(coalesce(k2.score, 0)) AS others, count(o) AS active_people
WITH m, k, active_people, k.score / (k.score + others + 0.000001) AS share
WHERE share >= $min_share
RETURN m.key AS key, m.path AS module, share, k.score AS score, k.commits AS commits, k.last AS last, active_people
ORDER BY share * score DESC LIMIT $limit"""

Q_SUCCESSOR_DIRECT = """
MATCH (m:Module {key: $module})<-[k:KNOWS]-(o:Person)
WHERE o.key <> $person AND k.last >= $active_since
RETURN o.key AS key, o.name AS name, k.score AS score, 'works in ' + m.path AS via
ORDER BY score DESC LIMIT 2"""

# Nobody else active in the module? Find people active in modules that change *together* with it.
Q_SUCCESSOR_ADJACENT = """
MATCH (m:Module {key: $module})<-[:IN]-(:File)-[cc:CO_CHANGES]->(:File)-[:IN]->(m2:Module)<-[k:KNOWS]-(o:Person)
WHERE m2 <> m AND o.key <> $person AND k.last >= $active_since
RETURN o.key AS key, o.name AS name, sum(cc.confidence * k.score) AS score, 'co-changes via ' + m2.path AS via
ORDER BY score DESC LIMIT 2"""

Q_SOLE_DOCS = """
MATCH (d:Doc)-[:FILE]->(:File)<-[:TOUCHED]-(:Commit)<-[:AUTHORED]-(a:Person)
WITH d, collect(DISTINCT a.key) AS authors
WHERE size(authors) = 1 AND authors[0] = $person
RETURN d.path AS doc, d.title AS title LIMIT 10"""

Q_THEIR_DECISIONS = """
MATCH (x:Decision)-[:RECORDED_IN]->(:Doc)-[:FILE]->(:File)<-[:TOUCHED]-(:Commit)<-[:AUTHORED]-(:Person {key: $person})
RETURN DISTINCT x.title AS decision, x.status AS status, x.date AS date"""

Q_OPEN_WORK = """
MATCH (:Person {key: $person})-[r:ASSIGNED|OPENED]->(t:Ticket {state: 'open'})
RETURN DISTINCT t.kind AS kind, t.number AS number, t.title AS title, t.url AS url LIMIT 10"""


def departure(g, person: str, min_share: float = 0.5, limit: int = 8) -> dict:
    active_since = now(g) - ACTIVE_WINDOW
    at_risk = rows(g, Q_DEPARTURE, person=person, active_since=active_since, min_share=min_share, limit=limit)
    for m in at_risk:
        m["share"] = round(m["share"], 2)
        succ = rows(g, Q_SUCCESSOR_DIRECT, module=m["key"], person=person, active_since=active_since)
        if not succ:
            succ = rows(g, Q_SUCCESSOR_ADJACENT, module=m["key"], person=person, active_since=active_since)
        m["successors"] = succ
    return {
        "person": person,
        "knowledge_at_risk": at_risk,
        "sole_author_docs": rows(g, Q_SOLE_DOCS, person=person),
        "decisions": rows(g, Q_THEIR_DECISIONS, person=person),
        "open_work": rows(g, Q_OPEN_WORK, person=person),
    }


# ---------------------------------------------------------------- stale knowledge

Q_STALE_DOCS = """
MATCH (d:Doc)-[:FILE]->(:File)<-[:TOUCHED]-(dc:Commit)
WITH d, max(dc.ts) AS doc_updated
MATCH (d)-[:MENTIONS]->(f:File)
OPTIONAL MATCH (f)<-[:TOUCHED]-(c:Commit)
WHERE c.ts > doc_updated
WITH d, doc_updated, f, count(c) AS changes
WHERE changes >= $min_changes OR f.alive = false
RETURN d.key AS key, d.path AS doc, d.title AS title, doc_updated,
       collect({file: f.path, changes: changes, deleted: NOT f.alive}) AS drift,
       sum(changes) AS total_changes, sum(CASE WHEN f.alive THEN 0 ELSE 1 END) AS deleted_refs
ORDER BY deleted_refs DESC, total_changes DESC LIMIT $limit"""

Q_WHO_SHOULD_UPDATE = """
MATCH (:Doc {key: $doc})-[:MENTIONS]->(:File)<-[:TOUCHED]-(c:Commit)<-[:AUTHORED]-(p:Person)
WHERE c.ts > $since
RETURN p.key AS key, p.name AS name, count(DISTINCT c) AS changes
ORDER BY changes DESC LIMIT 2"""


def stale_docs(g, min_changes: int = 5, limit: int = 10) -> list[dict]:
    out = rows(g, Q_STALE_DOCS, min_changes=min_changes, limit=limit)
    for d in out:
        d["drift"] = sorted(d["drift"], key=lambda x: (not x["deleted"], -x["changes"]))
        d["fix_owner"] = rows(g, Q_WHO_SHOULD_UPDATE, doc=d["key"], since=d["doc_updated"])
    return out


# ---------------------------------------------------------------- why did we do X

Q_WHY_DECISIONS = """
CALL db.idx.fulltext.queryNodes('Doc', $q) YIELD node, score
MATCH (x:Decision)-[:RECORDED_IN]->(node)
OPTIONAL MATCH (x)-[:AFFECTS]->(m:Module)
OPTIONAL MATCH (node)-[:FILE]->(:File)<-[:TOUCHED]-(c:Commit)<-[:AUTHORED]-(p:Person)
OPTIONAL MATCH (c)-[:IN_PR]->(pr:Ticket)
RETURN x.title AS decision, x.status AS status, x.date AS date, node.path AS source, score,
       collect(DISTINCT m.path) AS affects, collect(DISTINCT p.name) AS people, collect(DISTINCT pr.url) AS prs
ORDER BY score DESC LIMIT 3"""

Q_WHY_TICKETS = """
CALL db.idx.fulltext.queryNodes('Ticket', $q) YIELD node, score
OPTIONAL MATCH (a:Person)-[:OPENED]->(node)
OPTIONAL MATCH (node)-[:FIXES]->(i:Ticket)
RETURN node.kind AS kind, node.number AS number, node.title AS title, node.url AS url, node.state AS state,
       a.name AS author, collect(i.number) AS fixes, score
ORDER BY score DESC LIMIT 5"""

Q_WHY_COMMITS = """
CALL db.idx.fulltext.queryNodes('Commit', $q) YIELD node, score
MATCH (p:Person)-[:AUTHORED]->(node)
OPTIONAL MATCH (node)-[:IN_PR]->(pr:Ticket)
RETURN node.sha AS sha, node.message AS message, node.ts AS ts, p.name AS author, pr.url AS pr, score
ORDER BY score DESC LIMIT 5"""


def why(g, question: str) -> dict:
    q = fulltext(question)
    if not q:
        return {"decisions": [], "discussions": [], "commits": []}
    return {
        "decisions": rows(g, Q_WHY_DECISIONS, q=q),
        "discussions": rows(g, Q_WHY_TICKETS, q=q),
        "commits": rows(g, Q_WHY_COMMITS, q=q),
    }


# ---------------------------------------------------------------- route a new issue to the right person

Q_ROUTE_MODULES = """
CALL db.idx.fulltext.queryNodes('Ticket', $q) YIELD node, score
WITH node, score ORDER BY score DESC LIMIT 20
MATCH (node)<-[:FIXES*0..1]-(:Ticket)<-[:IN_PR]-(:Commit)-[:TOUCHED]->(:File)-[:IN]->(m:Module)
WHERE m.path <> '(root)' AND NOT m.path STARTS WITH '.'
RETURN m.key AS key, m.path AS module, sum(score) AS relevance, collect(DISTINCT node.number) AS similar
ORDER BY relevance DESC LIMIT 5"""

Q_ROUTE_CANDIDATES = """
MATCH (p:Person)-[k:KNOWS]->(m:Module)
WHERE m.key IN $modules AND k.last >= $active_since
OPTIONAL MATCH (p)-[:ASSIGNED]->(t:Ticket {state: 'open'})
RETURN p.key AS key, p.name AS name, m.key AS module_key, m.path AS module, k.score AS score, count(t) AS open_load"""


def route(g, text: str, limit: int = 3) -> dict:
    q = fulltext(text)
    mods = rows(g, Q_ROUTE_MODULES, q=q) if q else []
    via = "similar past tickets -> the PRs that fixed them -> files"
    if not mods:
        mods, via = topic_modules(g, text), "commit and doc text -> files"
    if not mods:
        return {"modules": [], "candidates": [], "via": via}
    top = mods[0]["relevance"] or 1
    weight = {m["key"]: m["relevance"] / top for m in mods}
    cands: dict[str, dict] = {}
    for r in rows(g, Q_ROUTE_CANDIDATES, modules=list(weight), active_since=now(g) - ACTIVE_WINDOW):
        c = cands.setdefault(r["key"], {"key": r["key"], "name": r["name"], "fit": 0.0, "areas": [], "open_load": r["open_load"]})
        c["fit"] += r["score"] * weight[r["module_key"]]
        c["areas"].append(r["module"])
    for c in cands.values():  # don't pile everything on the busiest expert
        c["fit"] = round(c["fit"] / (1 + 0.2 * c["open_load"]), 2)
    ranked = sorted(cands.values(), key=lambda c: c["fit"], reverse=True)[:limit]
    return {"modules": mods, "candidates": ranked, "via": via}


# ---------------------------------------------------------------- hidden coupling

Q_GHOSTS = """
MATCH (f:File)-[e:CO_CHANGES]->(other:File)
WHERE f.path IN $paths AND NOT other.path IN $paths AND other.alive
RETURN f.path AS changed, other.path AS usually_also, e.confidence AS confidence, e.count AS together
ORDER BY confidence DESC LIMIT $limit"""


def ghosts(g, paths: list[str], limit: int = 10) -> list[dict]:
    return rows(g, Q_GHOSTS, paths=paths, limit=limit)


# ---------------------------------------------------------------- onboarding a new teammate

Q_ONBOARD_DOCS = """
MATCH (d:Doc)-[:MENTIONS]->(:File)-[:IN]->(m:Module)
WHERE m.key IN $modules
MATCH (d)-[:FILE]->(:File)<-[:TOUCHED]-(c:Commit)
RETURN d.path AS doc, d.title AS title, max(c.ts) AS updated
ORDER BY updated DESC LIMIT 5"""

Q_KEY_FILES = """
MATCH (f:File)-[:IN]->(m:Module)
WHERE m.key IN $modules AND f.alive AND f.rank IS NOT NULL
RETURN f.path AS file, f.rank AS rank
ORDER BY rank DESC LIMIT 5"""

Q_ONBOARD_DECISIONS = """
MATCH (x:Decision)-[:AFFECTS]->(m:Module)
WHERE m.key IN $modules
RETURN DISTINCT x.title AS decision, x.status AS status, x.date AS date"""


def onboard(g, topic: str) -> dict:
    mods = topic_modules(g, topic, limit=3)
    keys = [m["key"] for m in mods]
    return {
        "modules": mods,
        "people": [p for p in who_knows(g, topic, limit=3)["experts"] if p["active"]],
        "read_first": rows(g, Q_ONBOARD_DOCS, modules=keys),
        "key_files": rows(g, Q_KEY_FILES, modules=keys),
        "decisions": rows(g, Q_ONBOARD_DECISIONS, modules=keys),
    }


# ---------------------------------------------------------------- overview

Q_COUNTS = """
MATCH (n) RETURN labels(n)[0] AS label, count(n) AS n ORDER BY n DESC"""

Q_TOP_PEOPLE = """
MATCH (p:Person) WHERE p.commits IS NOT NULL
RETURN p.key AS key, p.name AS name, p.commits AS commits, p.last_active AS last_active
ORDER BY commits DESC LIMIT $limit"""


def overview(g, limit: int = 10) -> dict:
    return {"counts": rows(g, Q_COUNTS), "people": rows(g, Q_TOP_PEOPLE, limit=limit), "now": now(g)}
