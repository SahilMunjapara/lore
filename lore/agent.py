"""The Lore agent: answers free-form questions about the company by choosing graph tools, then cites its path.

Two planners share the same tools, memory and trace:
- Claude (when ANTHROPIC_API_KEY is set): picks and chains tools itself.
- A built-in keyword planner: no API key needed, so the demo always runs.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from . import brain, memory

MODEL = "claude-opus-5-5"
MAX_STEPS = 8
RESULT_LIMIT = 12000
WRITE_WORDS = re.compile(r"\b(CREATE|MERGE|SET|DELETE|REMOVE|DROP|DETACH|LOAD)\b|createNodeIndex|algo\.", re.I)

SYSTEM = """You are Lore, a company brain. You answer questions about how a software organization works — who \
knows what, who owns what, why decisions were made, what knowledge is at risk, and who should do a piece of work — \
using ONLY the tools, which query a FalkorDB graph built from the org's git history, CODEOWNERS, docs, ADRs, issues \
and PRs.

How to work:
- Call tools before answering. Chain them when the question needs it (e.g. find_person, then departure).
- Every claim must be backed by something a tool returned: name the commit (short sha), doc path, PR/issue URL or \
module that supports it.
- "Knowledge" scores decay with a 180-day half-life, so prefer people marked active.
- If the graph has no evidence, say so plainly. Never invent people, files or decisions.
- Answer in short Markdown: the direct answer first, then a "How I know" list of the graph paths you used."""


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


_text = {"type": "string"}
TOOLS = [
    {"name": "who_knows", "description": "People who know a topic or area today, ranked by recency-decayed expertise, "
     "with commits as evidence, plus CODEOWNERS owners.", "input_schema": _schema({"topic": _text}, ["topic"])},
    {"name": "find_person", "description": "Look up people by partial name or GitHub login. Returns their key.",
     "input_schema": _schema({"query": _text}, ["query"])},
    {"name": "departure", "description": "Simulate a person leaving: areas where they hold most of the active knowledge, "
     "suggested successors, docs only they wrote, decisions they authored, their open work. Takes a person key from "
     "find_person.", "input_schema": _schema({"person_key": _text}, ["person_key"])},
    {"name": "stale_docs", "description": "Docs whose referenced files changed many times or were deleted since the doc "
     "was last edited, with who should update them.", "input_schema": _schema({}, [])},
    {"name": "why", "description": "Trace why something is the way it is: matching decision records (ADRs/RFCs), "
     "issue/PR discussions, and commits.", "input_schema": _schema({"question": _text}, ["question"])},
    {"name": "route", "description": "Who should pick up a new issue: similar past tickets -> PRs that fixed them -> "
     "files -> active experts, balanced by open workload.", "input_schema": _schema({"issue_text": _text}, ["issue_text"])},
    {"name": "ghosts", "description": "Hidden coupling: files that historically change together with the given files "
     "even without an import between them.",
     "input_schema": _schema({"files": {"type": "array", "items": _text}}, ["files"])},
    {"name": "onboard", "description": "Onboarding plan for a topic: people to meet, docs to read, decisions, key files.",
     "input_schema": _schema({"topic": _text}, ["topic"])},
    {"name": "overview", "description": "Counts of everything in the graph and the most active people.",
     "input_schema": _schema({}, [])},
    {"name": "cypher", "description": "Run a READ-ONLY Cypher query for anything the other tools don't cover. Labels: "
     "Person, Team, Repo, Module, File, Commit, Doc, Decision, Ticket, Answer. Relationships: AUTHORED, TOUCHED, IN, "
     "PART_OF, CO_CHANGES{count,confidence}, OWNS, KNOWS{score,commits,last}, FILE, MENTIONS, RECORDED_IN, AFFECTS, "
     "OPENED, ASSIGNED, FIXES, IN_PR, USED. Always add a LIMIT.", "input_schema": _schema({"query": _text}, ["query"])},
]
for _t in TOOLS:
    _t["strict"] = True


def run_tool(g, name: str, args: dict):
    if name == "who_knows":
        return brain.who_knows(g, args["topic"])
    if name == "find_person":
        return brain.find_person(g, args["query"])
    if name == "departure":
        return brain.departure(g, args["person_key"])
    if name == "stale_docs":
        return brain.stale_docs(g)
    if name == "why":
        return brain.why(g, args["question"])
    if name == "route":
        return brain.route(g, args["issue_text"])
    if name == "ghosts":
        return brain.ghosts(g, args["files"])
    if name == "onboard":
        return brain.onboard(g, args["topic"])
    if name == "overview":
        return brain.overview(g)
    if name == "cypher":
        q = args["query"]
        if WRITE_WORDS.search(q):
            raise ValueError("Only read-only queries are allowed.")
        res = g.ro_query(q)
        cols = [h[1] for h in res.header]
        return [dict(zip(cols, map(_plain, r))) for r in res.result_set[:50]]
    raise ValueError(f"Unknown tool {name}")


def _plain(v):
    if hasattr(v, "properties"):  # Node / Edge
        return v.properties
    return v


def _json(obj) -> str:
    s = json.dumps(obj, default=str)
    return s if len(s) <= RESULT_LIMIT else s[:RESULT_LIMIT] + "… (truncated)"


# ---------------------------------------------------------------- Claude planner

def llm_available() -> bool:
    if os.getenv("LORE_LLM", "").lower() == "off":
        return False
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


def plan_with_claude(g, question: str, trace: list[dict]) -> str:
    import anthropic

    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": question}]
    for _ in range(MAX_STEPS):
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            tools=TOOLS,
            messages=messages,
            output_config={"effort": "medium"},
            # On a policy decline, the API retries on a fallback model inside the same call.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            return "I can't help with that request."
        messages.append({"role": "assistant", "content": response.content})
        calls = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason != "tool_use" or not calls:
            return "".join(b.text for b in response.content if b.type == "text").strip()
        results = []
        for call in calls:
            try:
                out = run_tool(g, call.name, call.input)
                trace.append({"tool": call.name, "input": call.input, "result": out})
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": _json(out)})
            except Exception as e:
                trace.append({"tool": call.name, "input": call.input, "result": {"error": str(e)}})
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": f"Error: {e}", "is_error": True})
        messages.append({"role": "user", "content": results})
    return "I ran out of steps before finishing. Try a narrower question."


# ---------------------------------------------------------------- keyword planner (no API key)

INTENTS = [
    ("departure", r"\b(leave|leaves|leaving|left|quit|quits|bus factor|hit by a bus|walks? out|vacation)\b"),
    ("stale_docs", r"\b(stale|outdated|out of date|old docs?|docs? (are|is) wrong|drift)\b"),
    ("route", r"\b(assign|route|triage|who should (fix|take|handle|pick|look)|new (bug|issue))\b"),
    ("ghosts", r"\b(ghost\w*|changes? (together|with)|coupl\w*|also (need to )?change|forgot to change)\b"),
    ("onboard", r"\b(onboard\w*|new (hire|joiner|teammate|engineer)|get started|ramp up)\b"),
    ("why", r"\b(why|decid\w*|decision|reason|rationale|adr|rfc)\b"),
    ("overview", r"\b(overview|stats|summary of the (company|org)|how big)\b"),
]


def _guess_person(g, question: str) -> dict | None:
    best = None
    for tok in re.findall(r"@?[A-Za-z][\w.-]{2,}", question):
        if tok.lower().lstrip("@") in brain.STOP or tok.lower() in {"leave", "leaves", "leaving", "quit", "what", "happens", "tomorrow"}:
            continue
        for p in brain.find_person(g, tok)[:1]:
            if not best or p["commits"] > best["commits"]:
                best = p
    return best


LEAD = set("who whom what where how is are knows know about owns own understands an expert experts on in for tell "
           "me can help with to onboard ramp up get started".split())


def _topic(question: str) -> str:
    words = question.strip(" ?.!").split()
    while words and words[0].lower().strip(",:") in LEAD:
        words.pop(0)
    return " ".join(words) or question


def plan_with_keywords(g, question: str, trace: list[dict]) -> str:
    intent = next((name for name, rx in INTENTS if re.search(rx, question, re.I)), "who_knows")
    if intent == "departure":
        person = _guess_person(g, question)
        trace.append({"tool": "find_person", "input": {"query": question}, "result": [person] if person else []})
        if not person:
            return "I couldn't tell who you mean. Try their name or GitHub login."
        args = {"person_key": person["key"]}
    elif intent == "ghosts":
        files = re.findall(r"[\w./-]+\.\w+", question)
        if not files:
            return "Name one or more files, e.g. `src/graph_core.rs`."
        args = {"files": files}
    elif intent in ("stale_docs", "overview"):
        args = {}
    elif intent == "why":
        args = {"question": question}
    elif intent == "route":
        args = {"issue_text": re.sub(r"^.*?:\s*", "", question) if ":" in question else question}
    else:
        args = {"topic": _topic(question)}
    out = run_tool(g, intent, args)
    trace.append({"tool": intent, "input": args, "result": out})
    return render(intent, out)


def day(ts) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d") if ts else "—"


def render(tool: str, r) -> str:
    """Plain-Markdown answers for the keyword planner."""
    L: list[str] = []
    if tool == "who_knows":
        if not r["experts"]:
            return "The graph has nothing on that topic yet."
        top = r["experts"][0]
        L.append(f"**Ask {top['name']}** — they know {', '.join(dict.fromkeys(top['areas']))} best right now.\n")
        L.append("**How I know**")
        for p in r["experts"]:
            ev = "; ".join(f"`{e['sha'][:8]}` {e['message'][:60]}" for e in p["evidence"])
            L.append(f"- {p['name']} ({'active' if p['active'] else 'inactive'}, knowledge {p['score']}): {ev}")
        if r["owners"]:
            L.append("- CODEOWNERS: " + ", ".join(o["owner"] for o in r["owners"]))
    elif tool == "departure":
        risk = r["knowledge_at_risk"]
        if not risk:
            L.append("Nothing depends mostly on this person. Good bus factor.")
        else:
            L.append(f"**{len(risk)} area(s) would lose most of their active knowledge.**\n")
            for m in risk:
                succ = ", ".join(f"{s['name']} ({s['via']})" for s in m["successors"]) or "**nobody — single point of failure**"
                L.append(f"- `{m['module']}`: they hold {m['share']:.0%} ({m['commits']} commits, last {day(m['last'])}). Hand off to: {succ}")
        for d in r["sole_author_docs"]:
            L.append(f"- Only they have edited `{d['doc']}`")
        for d in r["decisions"]:
            L.append(f"- They wrote the decision “{d['decision']}” [{d['status']}]")
        for t in r["open_work"]:
            L.append(f"- Open {t['kind']} #{t['number']}: {t['title']} {t['url']}")
    elif tool == "stale_docs":
        if not r:
            return "No stale docs found."
        L.append(f"**{len(r)} doc(s) no longer match the code.**\n")
        for d in r:
            drift = "; ".join(f"`{x['file']}` deleted" if x["deleted"] else f"`{x['file']}` changed {x['changes']}×" for x in d["drift"][:3])
            who = ", ".join(p["name"] for p in d["fix_owner"])
            L.append(f"- `{d['doc']}` (last edited {day(d['doc_updated'])}): {drift}" + (f" → ask {who}" if who else ""))
    elif tool == "why":
        if not any(r.values()):
            return "There's no record of that decision in the graph."
        for d in r["decisions"]:
            L.append(f"- **Decision:** {d['decision']} [{d['status']}] — `{d['source']}`, by {', '.join(d['people']) or 'unknown'}")
        for t in r["discussions"]:
            L.append(f"- {t['kind']} #{t['number']} “{t['title']}” by {t['author']} — {t['url']}")
        for c in r["commits"]:
            L.append(f"- `{c['sha'][:8]}` {day(c['ts'])} {c['author']}: {c['message'][:80]}")
    elif tool == "route":
        if not r["candidates"]:
            return "I couldn't find an owner for that. It may be new territory."
        top = r["candidates"][0]
        L.append(f"**Assign to {top['name']}** (fit {top['fit']}, {top['open_load']} open items).\n")
        L.append(f"**How I know** — {r['via']}: " + ", ".join(f"`{m['module']}`" for m in r["modules"]))
        for c in r["candidates"][1:]:
            L.append(f"- Backup: {c['name']} (fit {c['fit']})")
    elif tool == "ghosts":
        if not r:
            return "No hidden coupling found for those files."
        for x in r:
            L.append(f"- Changing `{x['changed']}`? `{x['usually_also']}` changed with it {x['confidence']:.0%} of the time ({x['together']}×).")
    elif tool == "onboard":
        L.append("**Meet:** " + (", ".join(p["name"] for p in r["people"]) or "—"))
        L.append("**Read:** " + (", ".join(f"`{d['doc']}`" for d in r["read_first"]) or "—"))
        L.append("**Why it's built this way:** " + (", ".join(d["decision"] for d in r["decisions"]) or "—"))
        L.append("**Start with:** " + (", ".join(f"`{f['file']}`" for f in r["key_files"]) or "—"))
    elif tool == "overview":
        L.append(", ".join(f"{c['n']:,} {c['label']}" for c in r["counts"]))
        L.append("Most active: " + ", ".join(p["name"] for p in r["people"][:5]))
    return "\n".join(L)


# ---------------------------------------------------------------- entry point

def ask(g, question: str, use_memory: bool = True) -> dict:
    """Answer a question. Returns the answer, the tool trace, and what memory said."""
    remembered = memory.recall(g, question) if use_memory else None
    if remembered and remembered["fresh"]:
        try:
            old_trace = json.loads(remembered["trace"] or "[]")
        except ValueError:
            old_trace = []
        return {"answer": remembered["answer"], "trace": old_trace,
                "memory": {"status": "recalled", "asked_before": day(remembered["ts"])}, "mode": "memory"}

    trace: list[dict] = []
    mode = "claude" if llm_available() else "keywords"
    answer = plan_with_claude(g, question, trace) if mode == "claude" else plan_with_keywords(g, question, trace)

    mem = {"status": "new"}
    if remembered:  # we answered this before, but the evidence has moved on
        mem = {"status": "refreshed", "asked_before": day(remembered["ts"]), "changed_since": remembered["changed_since"]}
    if use_memory:
        mem["linked_evidence"] = memory.store(g, question, answer, trace, asof=brain.now(g), trace_json=json.dumps(trace, default=str))
    return {"answer": answer, "trace": trace, "memory": mem, "mode": mode}
