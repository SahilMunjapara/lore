"""Expose the company brain over MCP, so any agent (Claude Code, Claude Desktop, Cursor…) can use it.

Run: .venv/bin/python -m lore.mcp_server   (stdio transport)
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from . import agent, brain
from .db import connect

mcp = MCPServer("lore", instructions="Lore is a company brain: ask who knows what, why decisions were made, and what knowledge is at risk.")
_graph = None


def g():
    global _graph
    if _graph is None:
        _graph = connect()
    return _graph


@mcp.tool()
def who_knows(topic: str) -> dict:
    """People who know a topic or area today (recency-decayed expertise), with commits as evidence."""
    return brain.who_knows(g(), topic)


@mcp.tool()
def find_person(query: str) -> list[dict]:
    """Look up people by partial name, GitHub login or email."""
    return brain.find_person(g(), query)


@mcp.tool()
def departure(person_key: str) -> dict:
    """What knowledge is at risk if this person leaves, and who should take it over."""
    return brain.departure(g(), person_key)


@mcp.tool()
def stale_docs() -> list[dict]:
    """Docs that no longer match the code they describe, and who should update them."""
    return brain.stale_docs(g())


@mcp.tool()
def why(question: str) -> dict:
    """Trace a decision back to its ADR/RFC, discussions, PRs and commits."""
    return brain.why(g(), question)


@mcp.tool()
def route(issue_text: str) -> dict:
    """Who should pick up a new issue, based on who fixed similar ones and their current load."""
    return brain.route(g(), issue_text)


@mcp.tool()
def ghosts(files: list[str]) -> list[dict]:
    """Files that historically change together with the given files."""
    return brain.ghosts(g(), files)


@mcp.tool()
def ask_lore(question: str) -> dict:
    """Ask the Lore agent a free-form question. Uses its memory of earlier answers when still current."""
    r = agent.ask(g(), question)
    return {"answer": r["answer"], "memory": r["memory"], "tools_used": [t["tool"] for t in r["trace"]]}


if __name__ == "__main__":
    mcp.run()
