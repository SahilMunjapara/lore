"""Write a Snapshot into FalkorDB, then derive the edges the brain reasons over (KNOWS, PageRank, activity)."""

from __future__ import annotations

import time

from .ingest import Snapshot
from .ingest.codeowners import owner_key

BATCH = 500
HALF_LIFE_DAYS = 180

INDEXES = [
    "CREATE INDEX FOR (r:Repo) ON (r.name)",
    "CREATE INDEX FOR (p:Person) ON (p.key)",
    "CREATE INDEX FOR (t:Team) ON (t.name)",
    "CREATE INDEX FOR (c:Commit) ON (c.sha)",
    "CREATE INDEX FOR (f:File) ON (f.key)",
    "CREATE INDEX FOR (m:Module) ON (m.key)",
    "CREATE INDEX FOR (d:Doc) ON (d.key)",
    "CREATE INDEX FOR (d:Decision) ON (d.key)",
    "CREATE INDEX FOR (t:Ticket) ON (t.key)",
    "CREATE INDEX FOR (a:Answer) ON (a.key)",
    "CALL db.idx.fulltext.createNodeIndex('Answer', 'question')",
    "CALL db.idx.fulltext.createNodeIndex('Doc', 'title', 'text')",
    "CALL db.idx.fulltext.createNodeIndex('Ticket', 'title', 'body')",
    "CALL db.idx.fulltext.createNodeIndex('Commit', 'message')",
]


def _batched(g, cypher: str, rows: list[dict], **params):
    for i in range(0, len(rows), BATCH):
        g.query(cypher, {"rows": rows[i:i + BATCH], **params})


def ensure_schema(g):
    for stmt in INDEXES:
        try:
            g.query(stmt)
        except Exception as e:  # already exists
            if "already" not in str(e).lower():
                raise


def load(g, s: Snapshot, log=print):
    ensure_schema(g)
    repo = s.repo
    fk = lambda p: f"{repo}:{p}"  # noqa: E731
    t0 = time.time()

    g.query("MERGE (r:Repo {name: $repo}) SET r.slug = $slug, r.head_ts = $head", {"repo": repo, "slug": s.slug, "head": s.head_ts})

    modules = sorted(set(s.module_of.values()))
    _batched(g, """
        UNWIND $rows AS r
        MATCH (repo:Repo {name: $repo})
        MERGE (m:Module {key: r.key}) SET m.path = r.path, m.repo = $repo
        MERGE (m)-[:PART_OF]->(repo)""",
        [{"key": fk(m), "path": m} for m in modules], repo=repo)

    _batched(g, """
        UNWIND $rows AS r
        MATCH (m:Module {key: r.module})
        MERGE (f:File {key: r.key}) SET f.path = r.path, f.repo = $repo, f.alive = r.alive
        MERGE (f)-[:IN]->(m)""",
        [{"key": fk(f), "path": f, "alive": f in s.alive, "module": fk(m)} for f, m in s.module_of.items()], repo=repo)
    log(f"  files & modules   {len(s.module_of):>7,} / {len(modules):,}")

    people = {s.person(c.author_email) for c in s.commits}
    _batched(g, """
        UNWIND $rows AS r
        MERGE (p:Person {key: r.key}) SET p.name = r.name, p.login = r.login""",
        [{"key": k, "name": s.names.get(k, k.removeprefix("gh:")), "login": k[3:] if k.startswith("gh:") else None}
         for k in people | {"gh:" + t.author for t in s.tickets} | {"gh:" + a for t in s.tickets for a in t.assignees}])

    _batched(g, """
        UNWIND $rows AS r
        MATCH (p:Person {key: r.author})
        MERGE (c:Commit {sha: r.sha}) SET c.message = r.message, c.ts = r.ts, c.repo = $repo
        MERGE (p)-[:AUTHORED]->(c)""",
        [{"sha": c.sha, "author": s.person(c.author_email), "message": c.message, "ts": c.ts} for c in s.commits], repo=repo)

    _batched(g, """
        UNWIND $rows AS r
        MATCH (c:Commit {sha: r.sha}), (f:File {key: r.file})
        MERGE (c)-[:TOUCHED]->(f)""",
        [{"sha": c.sha, "file": fk(f)} for c in s.commits for f in set(c.files)])
    log(f"  commits           {len(s.commits):>7,}")

    _batched(g, """
        UNWIND $rows AS r
        MATCH (a:File {key: r.a}), (b:File {key: r.b})
        MERGE (a)-[e:CO_CHANGES]->(b) SET e.count = r.count, e.confidence = r.conf""",
        [{"a": fk(c.a), "b": fk(c.b), "count": c.count, "conf": c.confidence} for c in s.cochanges])
    log(f"  co-change edges   {len(s.cochanges):>7,}")

    team_rows, person_rows = [], []
    for f, owners in s.owners.items():
        for o in owners:
            kind, key = owner_key(o)
            (team_rows if kind == "team" else person_rows).append({"owner": key, "file": fk(f)})
    _batched(g, """
        UNWIND $rows AS r
        MATCH (f:File {key: r.file})
        MERGE (t:Team {name: r.owner})
        MERGE (t)-[:OWNS]->(f)""", team_rows)
    _batched(g, """
        UNWIND $rows AS r
        MATCH (f:File {key: r.file})
        MERGE (p:Person {key: r.owner}) ON CREATE SET p.name = r.owner
        MERGE (p)-[:OWNS]->(f)""", person_rows)
    log(f"  ownership rules   {len(team_rows) + len(person_rows):>7,}")

    _batched(g, """
        UNWIND $rows AS r
        MATCH (f:File {key: r.key})
        MERGE (d:Doc {key: r.key}) SET d.path = r.path, d.title = r.title, d.text = r.text, d.repo = $repo
        MERGE (d)-[:FILE]->(f)""",
        [{"key": fk(d.path), "path": d.path, "title": d.title, "text": d.text} for d in s.docs], repo=repo)
    _batched(g, """
        UNWIND $rows AS r
        MATCH (d:Doc {key: r.doc}), (f:File {key: r.file})
        MERGE (d)-[:MENTIONS]->(f)""",
        [{"doc": fk(d.path), "file": fk(f)} for d in s.docs for f in d.mentions_files])
    _batched(g, """
        UNWIND $rows AS r
        MATCH (d:Doc {key: r.doc}), (m:Module {key: r.module})
        MERGE (d)-[:MENTIONS]->(m)""",
        [{"doc": fk(d.path), "module": fk(m)} for d in s.docs for m in d.mentions_modules])
    decisions = [d for d in s.docs if d.decision]
    _batched(g, """
        UNWIND $rows AS r
        MATCH (d:Doc {key: r.doc})
        MERGE (x:Decision {key: r.doc}) SET x.title = r.title, x.status = r.status, x.date = r.date
        MERGE (x)-[:RECORDED_IN]->(d)
        WITH x, d
        OPTIONAL MATCH (d)-[:MENTIONS]->(:File)-[:IN]->(m:Module)
        WITH x, d, collect(DISTINCT m) AS via_files
        OPTIONAL MATCH (d)-[:MENTIONS]->(m2:Module)
        WITH x, via_files + collect(DISTINCT m2) AS mods
        UNWIND mods AS m
        MERGE (x)-[:AFFECTS]->(m)""",
        [{"doc": fk(d.path), **d.decision} for d in decisions])
    log(f"  docs / decisions  {len(s.docs):>7,} / {len(decisions):,}")

    tk = lambda n: f"{repo}#{n}"  # noqa: E731
    _batched(g, """
        UNWIND $rows AS r
        MATCH (p:Person {key: r.author})
        MERGE (t:Ticket {key: r.key})
        SET t.number = r.number, t.kind = r.kind, t.title = r.title, t.body = r.body, t.state = r.state,
            t.url = r.url, t.created = r.created, t.closed = r.closed, t.merged = r.merged, t.labels = r.labels,
            t.repo = $repo
        MERGE (p)-[:OPENED]->(t)""",
        [{"key": tk(t.number), "author": "gh:" + t.author, **{k: getattr(t, k) for k in
          ("number", "kind", "title", "body", "state", "url", "created", "closed", "merged", "labels")}}
         for t in s.tickets], repo=repo)
    _batched(g, """
        UNWIND $rows AS r
        MATCH (p:Person {key: r.person}), (t:Ticket {key: r.ticket})
        MERGE (p)-[:ASSIGNED]->(t)""",
        [{"person": "gh:" + a, "ticket": tk(t.number)} for t in s.tickets for a in t.assignees])
    _batched(g, """
        UNWIND $rows AS r
        MATCH (pr:Ticket {key: r.pr}), (i:Ticket {key: r.issue})
        MERGE (pr)-[:FIXES]->(i)""",
        [{"pr": tk(t.number), "issue": tk(n)} for t in s.tickets for n in t.fixes])
    _batched(g, """
        UNWIND $rows AS r
        MATCH (c:Commit {sha: r.sha}), (t:Ticket {key: r.ticket})
        MERGE (c)-[:IN_PR]->(t)""",
        [{"sha": c.sha, "ticket": tk(c.pr_number)} for c in s.commits if c.pr_number])
    log(f"  issues / PRs      {len(s.tickets):>7,}")

    merged = merge_people(g)
    if merged:
        log(f"  merged identities {merged:>7,} (same full name across repos)")
    refresh(g)
    log(f"  derived KNOWS + PageRank — done in {time.time() - t0:.1f}s")


def merge_people(g) -> int:
    """One person, one node — across repos too. An email-only Person whose full name matches exactly one
    GitHub-login Person is folded into it: their commits and ownership move over, the duplicate goes."""
    pairs = g.query("""
        MATCH (e:Person) WHERE NOT e.key STARTS WITH 'gh:' AND e.name CONTAINS ' '
        MATCH (p:Person) WHERE p.key STARTS WITH 'gh:' AND toLower(p.name) = toLower(e.name)
        WITH e, collect(p) AS same
        WHERE size(same) = 1
        RETURN e.key, same[0].key""").result_set
    for src, dst in pairs:
        for rel in ("AUTHORED", "OWNS"):
            g.query(f"""
                MATCH (e:Person {{key: $src}})-[r:{rel}]->(x), (p:Person {{key: $dst}})
                MERGE (p)-[:{rel}]->(x)
                DELETE r""", {"src": src, "dst": dst})
        g.query("MATCH (e:Person {key: $src}) DETACH DELETE e", {"src": src})
    return len(pairs)


def refresh(g, now: int | None = None):
    """Recompute derived knowledge. Run after every ingest so the brain stays current.

    KNOWS.score decays with a 180-day half-life: someone who rewrote a module five years ago
    knows less about it today than someone who touched it last month. Files at the repo root
    (README, config) and hidden folders (.github, .vscode) are skipped: they say nothing about expertise.
    """
    if not now:  # measure decay from the newest commit we know, so replaying history stays consistent
        res = g.query("MATCH (r:Repo) RETURN max(r.head_ts)")
        now = (res.result_set[0][0] if res.result_set else None) or int(time.time())
    g.query("""
        MATCH (p:Person)-[:AUTHORED]->(c:Commit)
        WITH p, max(c.ts) AS last, min(c.ts) AS first, count(c) AS n
        SET p.last_active = last, p.first_active = first, p.commits = n""")
    g.query("MATCH (:Person)-[k:KNOWS]->(:Module) DELETE k")
    g.query("""
        MATCH (p:Person)-[:AUTHORED]->(c:Commit)-[:TOUCHED]->(:File)-[:IN]->(m:Module)
        WHERE m.path <> '(root)' AND NOT m.path STARTS WITH '.'
        WITH DISTINCT p, m, c
        WITH p, m, count(c) AS commits, max(c.ts) AS last,
             sum(exp(-0.6931 * ($now - c.ts) / ($half * 86400.0))) AS score
        MERGE (p)-[k:KNOWS]->(m)
        SET k.score = score, k.commits = commits, k.last = last""",
        {"now": now, "half": HALF_LIFE_DAYS})
    # Central files: the ones everything else drags along when it changes.
    g.query("""
        CALL algo.pageRank('File', 'CO_CHANGES') YIELD node, score
        SET node.rank = score""")
