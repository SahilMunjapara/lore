# Lore — video script (natural voice, with pauses)

Read it like you're explaining Lore to a friend, not reading an essay. The same text is in each slide's speaker notes.

**How to read the marks**

| Mark | What to do |
|---|---|
| `(pause)` | Stop for about **1 second**. Breathe. |
| `(big pause)` | Stop for **2 seconds**. Let the point land. |
| `…` | Slow down, as if you're thinking. |
| `CAPS` | Say this word a bit stronger. |
| `→ point` | Move your mouse or finger to that part of the slide. |
| `[next]` | Change to the next slide, wait 1 second, *then* start talking. |

**Say these words like this:** FalkorDB = "FAL-kor D-B" · Cypher = "SY-fer" · `src/graph` = "source graph" · `src/record` = "source record" · CODEOWNERS = "code owners" · PRs = "P-Rs" · swilly22 = "swilly twenty-two"

Time: about 4 minutes. For a 3-minute limit, skip slides 6 and 12.

---

## 1 · Cover (15 s)
*(Smile. Look at the camera.)*

Hi, I'm Sahil, from team Campers. (pause)
And this is Lore — (pause) the company brain that knows who knows what. (pause)
We built it on FalkorDB, for Graph Hacks, Track 3. (pause)
Let me show you the problem, how it works, and some real answers it found. (big pause) [next]

## 2 · The problem (25 s)
So, here's something every engineer has felt. (pause)
A lot of what a company knows… isn't written down anywhere. It's in people's heads. (pause)

→ point at card 1. You join a new team, and you ask: who knows this code? (pause) And nobody's really sure. (big pause)

→ card 2. Or someone senior leaves — (pause) and only THEN do you find out they were the only one who understood three systems. (pause)

→ card 3. The docs say one thing… the code does another. (pause)

→ card 4. And when you ask "why did we build it like this?" — the answer is buried in some pull request from two years ago. (big pause) [next]

## 3 · The idea (20 s)
Now, here's the thing — search can't fix this. (pause)
Because the answer isn't in ONE document. It's in how things connect. (pause)

→ point at each box, left to right. A person wrote a commit. (pause) The commit changed a file. (pause) The file lives in a module. (pause) And a doc talks about it. (big pause)

So if you want to know "who knows bulk insert?" — you just start at the doc, and follow the lines back to the people. (pause)
And following lines… is exactly what a graph database is built for. (big pause) [next]

## 4 · Demo company (15 s)
And we didn't want a toy example. (pause)
So our demo company is… FalkorDB itself. (pause)
We loaded its four public repos — about SIX THOUSAND commits, (pause) fourteen hundred issues and pull requests, (pause) two hundred people, (pause) and sixty-two docs. (pause)
All public data — so you can check every answer yourself. (big pause) [next]

## 5 · How it works (20 s)
So how does it work? Four steps. (pause)

→ step 1. One — Read. We read the git history, the code owners file, the docs, and the GitHub issues and P-Rs. (pause)
→ step 2. Two — Load. All of it goes into FalkorDB, as dots and lines. (pause)
→ step 3. Three — Think. This is the fun part. The graph works out new things on its own: who knows what, which files change together, and which files matter most. (pause)
→ step 4. And four — Answer. Every question is just a walk through the graph. (big pause) [next]

## 6 · The graph model (20 s) — skip for 3 minutes
Here's what that graph looks like. (pause)
Dots are things. Lines are how they're connected. (pause)

→ trace the middle row. A person authored a commit… the commit touched a file… the file is in a module. (pause)
→ top row. Up here: docs mention files, decisions are written in docs, and commits link to their pull requests. (pause)

→ orange line. Now — see this orange line? KNOWS. (pause)
We never load that. Lore calculates it, right inside the graph. (pause) And it fades over time. (big pause) [next]

## 7 · Question 1: who knows? (20 s)
Okay — let's ask it something. (pause)
Who knows about bulk insert? (pause)

→ names. Lore says: Avi Avni… then swilly twenty-two, and dvirdukhan. (pause)
And it doesn't just give names — it shows the exact commits that prove it. (pause)

→ chart. But here's the clever bit: knowledge fades. (pause) A commit from today counts fully. Six months ago — half. A year ago — a quarter. (pause)
So you get who knows it NOW. (pause) Not who wrote it back in 2019. (big pause) [next]

## 8 · Question 2: what if someone leaves? (25 s) — your "wow" slide, go slow
Now, this next one is my favourite. (pause)
What happens if someone leaves? (big pause)

→ 69%. If swilly twenty-two left FalkorDB… sixty-nine percent of the knowledge of source graph walks out the door with them. (pause)
→ 62%. And sixty-two percent of the execution plan. (big pause)

→ bottom box. Lore also tells you who should take over — Gabriel Gomez and dvirdukhan. (pause)

→ dark card. And it found something nobody was looking for: only ONE person ever touched source record… (pause) and they stopped contributing in 2022. (pause)
That's a hidden single point of failure. (pause) And you'd never spot it without the graph. (big pause) [next]

## 9 · Question 3: stale docs (20 s)
Question three: which docs are out of date? (pause)

→ left box. Here's a real one. This benchmark README still mentions a Dockerfile… (pause) → top box. that was deleted. (pause)
→ bottom box. And it mentions queries.py — which has changed eight times since that doc was written. (pause)

Lore finds this by following the lines from the doc to the files, and checking what happened to them after. (pause)
And it even tells you who should fix it: Avi Avni and dvirdukhan. (big pause) [next]

## 10 · Three more (25 s)
And quickly — three more. (pause)

→ card 1. Why is something built this way? Lore finds the decision record, and the people and P-Rs behind it. (pause)

→ card 2. A new bug comes in? Lore finds similar old bugs, sees who fixed them, and suggests who should take it — without piling everything on the busiest person. (pause)

→ card 3. And a fun one: ghost dependencies. In the Python client, this test file changes in sixty-two percent of the commits that touch graph.py. (pause)
No code says they're linked. (pause) Only history shows it. (big pause) [next]

## 11 · The agent (25 s)
And you don't need to learn any commands. (pause)
You just ask — in plain English. (pause)

→ follow the arrows. The agent picks the right graph tools — using Claude, or a built-in planner if there's no API key. (pause)
It runs read-only queries on FalkorDB. (pause)
And it shows its work: the commits, docs and P-Rs behind every answer. (big pause)

→ orange box. But the best part — it REMEMBERS. (pause)
Every answer is saved in the graph, linked to its evidence. (pause)
We loaded part of the history, asked a question… then loaded the rest, and asked again. (pause)
And Lore said: "My earlier answer is outdated — five new commits landed." (pause) And it answered again. (pause)
That's how the brain stays fresh. (big pause) [next]

## 12 · Why a graph (20 s) — skip for 3 minutes
So — why a graph? (pause)
Because every question here is a path. Person… to commit… to file… to module. (pause)

→ code box. In a normal database, that's a pile of joins, every single time. In FalkorDB… it's one pattern. (pause)

→ right side. And the graph does the real work: expertise, PageRank, memory — and one graph per company. (big pause)

→ bottom line. Honestly? Take FalkorDB away… and nothing works. (big pause) [next]

## 13 · Close (10 s)
*(Look at the camera.)*

So that's Lore. (pause)
The company brain that knows who knows what. (pause)
Public data only. A command line, a web app, and an MCP server — so other AI tools can use it too. (pause)
Built on FalkorDB. (pause)
Thanks for watching!

*(Smile and hold for 2 seconds before you stop recording.)*

---

## Recording tips

- **Layout:** slides full screen, your camera in a small circle in a corner (OBS, Loom or Zoom "share screen + camera" all do this).
- **Present mode:** open the deck, click Present, and use the arrow keys to change slides. Keep this script open on your phone or a second screen.
- **Change the slide first, then speak.** That's what `[next]` is for.
- **Don't read word for word.** Read one line, look at the camera, say it. Small mistakes sound natural — keep going.
- **Smile on the first and last slide.** People can hear a smile.
- **Practise once out loud** with a timer. Then do 2–3 takes and keep the best one, or record each slide separately and join them.

## If judges ask questions

- **Why not a normal database?** Our questions are 4–5 hops (person → commit → file → module → doc). In SQL that's many joins; in a graph it's one pattern.
- **How do you know someone "knows" a system?** We count their commits to it, and each commit's weight halves every 180 days: `sum(exp(-0.693 × age / 180 days))`.
- **How do you find a successor?** First, other active people in the same module. If nobody, people active in modules that change together with it (CO_CHANGES).
- **How does memory know an answer is stale?** Each answer has USED edges to the modules and docs it relied on. If a newer commit touched them, the answer is outdated.
- **Privacy?** Only public repos, issues and PRs. No email addresses stored — people are GitHub logins, or a one-way hash.
- **Did you use AI?** Yes, Claude Code as a coding assistant (disclosed in the README). We designed the graph model and can explain every query.
