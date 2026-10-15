# Lore — how to explain it (simple version)

## In one sentence

**Lore is a "company brain": it reads everything a software team leaves behind — code history, docs,
ownership files, issues and pull requests — puts it into a graph, and answers questions like
"who knows this?", "why did we do this?", and "what do we lose if this person leaves?"**

## The problem (say this first)

Every company has knowledge that lives only in people's heads.

- A new engineer asks "who should I talk to about the billing code?" — nobody knows for sure.
- A senior engineer resigns — and only then do people discover that she was the only one who understood three systems.
- The docs say one thing, the code does another — because the code changed and nobody updated the docs.
- Someone asks "why did we build it this way?" — the answer is buried in a two-year-old pull request.

Search tools can't fix this. The answers aren't in one document; they're in **how things connect**:
this person → wrote these commits → which changed these files → which belong to this system → which this doc describes.

## The idea: a graph

A graph is just **dots and lines**.

- The **dots** (nodes) are things: people, teams, files, systems, docs, decisions, issues, pull requests.
- The **lines** (edges) are how they relate: *Alice WROTE this commit*, *this commit CHANGED this file*,
  *this doc TALKS ABOUT this file*, *this pull request FIXES that issue*.

Once everything is connected, a hard question becomes "follow the lines". That's exactly what a graph database
like **FalkorDB** is built for, and it does it fast enough to run on every question.

## Where the data comes from (all public)

| Source | What we learn |
|---|---|
| git history | who changed what, and when |
| CODEOWNERS file | which team officially owns which files |
| docs and decision records (ADRs) | what is documented, and what decisions were made |
| GitHub issues and pull requests | problems, discussions, and who fixed what |

We only use public data, as the rules require. Our demo company is **FalkorDB itself**: we load
FalkorDB's own public repositories, so the judges can check our answers.

## The 5 things it can do (the demo)

1. **"Who knows about X?"** → the people who know it *today*, with the commits that prove it.
   Knowledge fades: someone who touched the code 5 years ago counts less than someone who touched it last month
   (we use a 180-day "half-life", like radioactive decay).
2. **"What happens if this person leaves?"** → the systems where they hold most of the knowledge,
   who should take over each one, the docs only they wrote, and their open work.
3. **"Which docs are out of date?"** → docs that talk about files that changed a lot (or were deleted)
   after the doc was last edited, and who should update them.
4. **"Why do we do it this way?"** → the decision record, the discussion and the pull requests behind it.
5. **"Who should fix this new bug?"** → finds similar old bugs, the pull requests that fixed them, the code they
   touched, and the people who know that code now — and avoids overloading the busiest person.

Bonus: **"ghost dependencies"** — files that always change together even though the code doesn't say so.
Only history (and the graph) can reveal that.

### Real results from our demo company (FalkorDB's 4 public repos)

We loaded ~6,000 commits, ~1,450 issues and pull requests, ~200 people and 62 docs. Some things Lore found:

- **"Who knows about bulk insert?"** → Avi Avni, then swilly22 and dvirdukhan — with the exact commits as proof.
- **"What happens if swilly22 leaves?"** → they hold **69%** of the active knowledge of `src/graph` (140 commits)
  and **62%** of `src/execution_plan`. Lore suggests Gabriel Gomez and dvirdukhan to take over.
- **Jeffrey Lovitz** stopped contributing in 2022, but is still the only person who ever touched `src/record` —
  a real **single point of failure** that nobody would notice without the graph.
- **"Which docs are out of date?"** → e.g. `bench/README.md` mentions a `Dockerfile` that was deleted and a
  `queries.py` that changed 8 times since — and says Avi Avni and dvirdukhan should update it.
- **"Who should fix: crash when deleting a node with an index"** → Avi Avni, found through similar past issues
  and the pull requests that fixed them.
- **Ghost dependency:** in the Python client, `tests/test_graph.py` changes in **62%** of the commits that change
  `falkordb/graph.py`.
- **Memory:** we loaded the history only up to June 2026 and asked a question, then loaded the rest.
  Asking again, Lore said its earlier answer was outdated because "5 new commits landed in `falkordb/asyncio`" — and re-answered.

## What makes it an *agent* (not just a search box)

- You ask in plain English. The agent **decides which graph tools to use**, can chain them
  (first find the person, then simulate them leaving), and **shows its path**: every answer lists
  the tools it ran and highlights the nodes and lines in the graph it used.
- It **remembers**. Every answer is saved in the graph, linked to the evidence it used.
  Ask again later: if nothing changed, it answers from memory instantly. If new commits touched that evidence,
  it says *"my earlier answer is outdated — 14 commits changed `src/graph` since"* and re-answers.
  That's how the brain stays current.
- It works with **Claude** (when an API key is set) or with a built-in planner (no key needed).
- Other AI tools can use it too, through **MCP** (a standard plug for AI assistants).

## How it works (architecture in 4 steps)

1. **Read** — Python reads the git history, CODEOWNERS, docs and GitHub issues of a repo.
2. **Load** — it writes everything into FalkorDB as nodes and edges (Cypher `MERGE`).
3. **Think** — it computes extra knowledge inside the graph: who `KNOWS` which system (with decay),
   which files `CO_CHANGE`, and which files are most central (PageRank).
4. **Answer** — every question is a Cypher query that walks the graph; the agent picks the right ones.

## Questions judges may ask — and short answers

**Why a graph and not a normal database?**
Our questions are about connections several steps away (person → commit → file → system → doc).
In a normal database that's many slow joins; in a graph it's one pattern, e.g.
`(p:Person)-[:AUTHORED]->(:Commit)-[:TOUCHED]->(:File)-[:IN]->(m:Module)`.

**Is the graph doing real work?**
Yes. Every answer is a graph traversal. Expertise (`KNOWS`) is computed by a Cypher query, central files by
FalkorDB's PageRank algorithm, and memory staleness by following `USED` edges to new commits.
Remove FalkorDB and nothing works.

**How do you know someone "knows" a system?**
We count their commits to it, but each commit's weight halves every 180 days.
So recent work counts most. Formula: `sum(exp(-0.693 × age / 180 days))`.

**How do you find a successor when someone leaves?**
First: other active people in the same system. If nobody: people active in systems that
*change together* with it (`CO_CHANGES` edges) — they're the closest experts.

**How does memory know an answer is stale?**
Each saved answer has `USED` edges to the systems and docs it relied on and the date it was made.
If any commit after that date touched those systems, the answer is outdated.

**What about privacy?**
Only public repositories, issues and pull requests. No private chats. We never store email addresses:
people are identified by their public GitHub login, or by a one-way hash when no login is known.

**What did AI help with?**
We used Claude Code as a coding assistant (disclosed in the README, as the rules require).
We designed the graph model, reviewed and ran every query, and can explain all of it.

## 3-minute demo video script

| Time | Show | Say |
|---|---|---|
| 0:00 | Title | "Every company loses knowledge when people leave. Lore is a company brain built on FalkorDB." |
| 0:15 | `lore ingest FalkorDB/FalkorDB` | "We load a real company — FalkorDB's own public repos — into a graph: people, commits, files, docs, decisions, issues." |
| 0:35 | FalkorDB Browser at localhost:3000 | "Here's the graph. Dots are things, lines are relationships." |
| 0:50 | Web UI: "Who knows about bulk insert?" | "It finds who knows this *today* — and shows the commits that prove it." |
| 1:15 | "What happens if *X* leaves?" | "Here's the knowledge that walks out the door, and who should take over each part." |
| 1:45 | "Which docs are out of date?" | "These docs describe code that changed 20 times since. And here's who should fix them." |
| 2:10 | Ask the first question again | "It remembers — and knows when its own answer has gone stale." |
| 2:35 | Graph panel | "Every answer shows its path through the graph. No guessing." |
| 2:50 | Closing | "Lore: the company brain that knows who knows what. Built on FalkorDB." |
