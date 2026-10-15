"""lore — ask your company's graph who knows what, why, and what's at risk."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from . import brain
from .ingest import snapshot

console = Console()


def day(ts: int | None) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d") if ts else "—"


def graph(args):
    from .db import connect
    return connect(args.graph)


def pick_person(g, q: str) -> dict:
    found = brain.find_person(g, q)
    if not found:
        raise SystemExit(f"No one matching '{q}'. Try `lore stats` to list people.")
    return found[0]


# ---------------------------------------------------------------- commands

def cmd_scan(args):
    s = snapshot(args.repo, use_github=not args.no_github, max_tickets=args.max_tickets, since=args.since, until=args.until)
    t = Table(title=f"Snapshot of {s.repo}", show_header=False)
    for k, v in s.stats().items():
        t.add_row(k.replace("_", " "), f"{v:,}" if isinstance(v, int) else str(v))
    console.print(t)
    return s


def cmd_ingest(args):
    s = cmd_scan(args)
    from .loader import load
    console.print(f"[bold]Loading into graph '{args.graph or 'lore'}'…")
    load(graph(args), s, log=console.print)


def cmd_reset(args):
    g = graph(args)
    try:
        g.delete()
        console.print(f"Deleted graph '{args.graph or 'lore'}'.")
    except Exception as e:
        console.print(f"Nothing to delete ({e}).")


def cmd_refresh(args):
    from .loader import refresh
    refresh(graph(args))
    console.print("Derived knowledge refreshed.")


def cmd_stats(args):
    o = brain.overview(graph(args))
    t = Table(title="Graph")
    t.add_column("node")
    t.add_column("count", justify="right")
    for r in o["counts"]:
        t.add_row(r["label"], f"{r['n']:,}")
    p = Table(title=f"Most active people (as of {day(o['now'])})")
    for c in ("key", "name", "commits", "last active"):
        p.add_column(c)
    for r in o["people"]:
        p.add_row(r["key"], r["name"], str(r["commits"]), day(r["last_active"]))
    console.print(t, p)


def cmd_who(args):
    r = brain.who_knows(graph(args), args.topic)
    if not r["experts"]:
        return console.print("Nothing in the graph about that yet.")
    tree = Tree(f"[bold]Who knows about “{args.topic}”?")
    tree.add("areas: " + ", ".join(f"{m['module']}" for m in r["modules"]))
    for p in r["experts"]:
        badge = "[green]active" if p["active"] else "[red]inactive"
        node = tree.add(f"[bold]{p['name']}[/] ({p['key']}) · knowledge {p['score']} · {badge}")
        node.add("works in: " + ", ".join(dict.fromkeys(p["areas"])))
        for e in p["evidence"]:
            node.add(f"[dim]{day(e['ts'])} {e['sha'][:8]} {e['message'][:70]}")
    if r["owners"]:
        tree.add("CODEOWNERS: " + ", ".join(f"{o['owner']} ({o['files']} files)" for o in r["owners"]))
    console.print(tree)


def cmd_leaves(args):
    g = graph(args)
    p = pick_person(g, args.person)
    r = brain.departure(g, p["key"])
    console.print(Panel(f"[bold]What walks out the door if {p['name']} ({p['key']}) leaves?", style="red"))
    if r["knowledge_at_risk"]:
        t = Table(title="Knowledge at risk")
        for c in ("area", "their share", "commits", "last touched", "other active people", "hand off to"):
            t.add_column(c)
        for m in r["knowledge_at_risk"]:
            succ = "; ".join(f"{s['name']} ({s['via']})" for s in m["successors"]) or "[red]nobody — single point of failure"
            t.add_row(m["module"], f"{m['share']:.0%}", str(m["commits"]), day(m["last"]), str(m["active_people"]), succ)
        console.print(t)
    else:
        console.print("No area depends mostly on them. Nice bus factor.")
    for title, key, fmt in (
        ("Docs only they have ever edited", "sole_author_docs", lambda d: f"{d['doc']} — {d['title']}"),
        ("Decisions they wrote", "decisions", lambda d: f"{d['decision']} [{d['status']}] {d['date'] or ''}"),
        ("Open work in their name", "open_work", lambda d: f"{d['kind']} #{d['number']} {d['title']} {d['url']}"),
    ):
        if r[key]:
            tree = Tree(f"[bold]{title}")
            for d in r[key]:
                tree.add(fmt(d))
            console.print(tree)


def cmd_stale(args):
    docs = brain.stale_docs(graph(args), min_changes=args.min_changes, limit=args.limit)
    if not docs:
        return console.print("No stale docs found. Knowledge is current.")
    for d in docs:
        tree = Tree(f"[bold]{d['doc']}[/] — last edited {day(d['doc_updated'])}")
        for x in d["drift"][:6]:
            tree.add(f"[red]{x['file']} was deleted" if x["deleted"] else f"{x['file']} changed {x['changes']}× since")
        if d["fix_owner"]:
            tree.add("[green]ask: " + ", ".join(f"{p['name']} ({p['changes']} of those changes)" for p in d["fix_owner"]))
        console.print(tree)


def cmd_why(args):
    r = brain.why(graph(args), args.question)
    for d in r["decisions"]:
        tree = Tree(f"[bold]Decision: {d['decision']}[/] [{d['status']}] {d['date'] or ''}")
        tree.add(f"source: {d['source']}")
        if d["people"]:
            tree.add("written by: " + ", ".join(d["people"]))
        if d["affects"]:
            tree.add("affects: " + ", ".join(d["affects"]))
        for url in d["prs"]:
            tree.add(f"PR: {url}")
        console.print(tree)
    if r["discussions"]:
        tree = Tree("[bold]Discussions")
        for t in r["discussions"]:
            fixes = f" → fixes #{', #'.join(map(str, t['fixes']))}" if t["fixes"] else ""
            tree.add(f"{t['kind']} #{t['number']} [{t['state']}] {t['title']} — {t['author']}{fixes}\n[dim]{t['url']}")
        console.print(tree)
    if r["commits"]:
        tree = Tree("[bold]Commits")
        for c in r["commits"]:
            tree.add(f"{day(c['ts'])} {c['sha'][:8]} {c['author']}: {c['message'][:80]}" + (f" ({c['pr']})" if c["pr"] else ""))
        console.print(tree)
    if not any(r.values()):
        console.print("No record of that decision in the graph.")


def cmd_route(args):
    r = brain.route(graph(args), args.text)
    if not r["candidates"]:
        return console.print("Couldn't find an owner for that. It might be new territory.")
    tree = Tree(f"[bold]Route: “{args.text}”[/]  [dim](via {r['via']})")
    tree.add("areas: " + ", ".join(m["module"] for m in r["modules"]))
    for i, c in enumerate(r["candidates"]):
        label = "[green]assign →" if i == 0 else "  backup"
        tree.add(f"{label} [bold]{c['name']}[/] ({c['key']}) fit {c['fit']} · {c['open_load']} open · {', '.join(dict.fromkeys(c['areas']))}")
    console.print(tree)


def cmd_ghosts(args):
    rs = brain.ghosts(graph(args), args.files)
    if not rs:
        return console.print("No hidden coupling for those files.")
    t = Table(title="You changed these. History says you probably also need to change…")
    for c in ("changed", "usually also changes", "how often", "times together"):
        t.add_column(c)
    for r in rs:
        t.add_row(r["changed"], r["usually_also"], f"{r['confidence']:.0%}", str(r["together"]))
    console.print(t)


def cmd_onboard(args):
    r = brain.onboard(graph(args), args.topic)
    tree = Tree(f"[bold]Onboarding plan: {args.topic}")
    tree.add("areas: " + ", ".join(m["module"] for m in r["modules"]))
    for title, key, fmt in (
        ("1. Meet", "people", lambda p: f"{p['name']} ({p['key']})"),
        ("2. Read", "read_first", lambda d: f"{d['doc']} — {d['title']} (updated {day(d['updated'])})"),
        ("3. Understand why", "decisions", lambda d: f"{d['decision']} [{d['status']}]"),
        ("4. Start with these files", "key_files", lambda f: f"{f['file']}"),
    ):
        branch = tree.add(f"[bold]{title}")
        for x in r[key] or []:
            branch.add(fmt(x))
    console.print(tree)


def cmd_ask(args):
    from rich.markdown import Markdown
    from .agent import ask
    r = ask(graph(args), args.question, use_memory=not args.no_memory)
    mem = r["memory"]
    note = {"recalled": f"remembered from {mem.get('asked_before')}, still current",
            "refreshed": f"asked before on {mem.get('asked_before')}, but the evidence changed — re-answered",
            "new": "new question"}[mem["status"]]
    console.print(f"[dim]planner: {r['mode']} · memory: {note}")
    for c in mem.get("changed_since", []):
        console.print(f"[yellow]  changed since: {c['what']} ({c['changes']} commits)")
    for t in r["trace"]:
        console.print(f"[dim]  → {t['tool']}({json.dumps(t['input'])[:100]})")
    console.print(Panel(Markdown(r["answer"]), title=args.question))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lore", description=__doc__)
    ap.add_argument("--graph", help="graph name, one per company (default: $LORE_GRAPH or 'lore')")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name, fn, hlp in (("scan", cmd_scan, "parse a repo and print what was found (no database)"),
                          ("ingest", cmd_ingest, "parse a repo and load it into FalkorDB")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("repo", help="local path, owner/name, or GitHub URL")
        p.add_argument("--no-github", action="store_true", help="skip issues/PRs")
        p.add_argument("--max-tickets", type=int, default=500)
        p.add_argument("--since", help="only history after this date, e.g. 2023-01-01")
        p.add_argument("--until", help="only history up to this date: replay the company at a point in time")
        p.set_defaults(fn=fn)

    sub.add_parser("refresh", help="recompute KNOWS edges and PageRank").set_defaults(fn=cmd_refresh)
    sub.add_parser("reset", help="delete this company's graph").set_defaults(fn=cmd_reset)
    sub.add_parser("stats", help="what's in the graph").set_defaults(fn=cmd_stats)
    p = sub.add_parser("ask", help="ask the agent anything (uses Claude if ANTHROPIC_API_KEY is set)")
    p.add_argument("question")
    p.add_argument("--no-memory", action="store_true")
    p.set_defaults(fn=cmd_ask)
    p = sub.add_parser("who", help="who knows about a topic")
    p.add_argument("topic")
    p.set_defaults(fn=cmd_who)
    p = sub.add_parser("leaves", help="simulate someone leaving")
    p.add_argument("person", help="name or GitHub login (partial ok)")
    p.set_defaults(fn=cmd_leaves)
    p = sub.add_parser("stale", help="docs that no longer match the code")
    p.add_argument("--min-changes", type=int, default=5)
    p.add_argument("--limit", type=int, default=8)
    p.set_defaults(fn=cmd_stale)
    p = sub.add_parser("why", help="trace a decision back to its source")
    p.add_argument("question")
    p.set_defaults(fn=cmd_why)
    p = sub.add_parser("route", help="who should pick up this issue")
    p.add_argument("text")
    p.set_defaults(fn=cmd_route)
    p = sub.add_parser("ghosts", help="files that secretly change together with these")
    p.add_argument("files", nargs="+")
    p.set_defaults(fn=cmd_ghosts)
    p = sub.add_parser("onboard", help="an onboarding plan for a topic or area")
    p.add_argument("topic")
    p.set_defaults(fn=cmd_onboard)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
