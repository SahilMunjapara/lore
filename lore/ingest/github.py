"""Public issues and PRs from the GitHub REST API. GITHUB_TOKEN is optional but lifts the 60 req/h limit."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

FIXES = re.compile(r"\b(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?)\s+#(\d+)", re.I)


@dataclass
class Ticket:
    number: int
    kind: str  # "issue" | "pr"
    title: str
    body: str
    state: str
    author: str  # github login
    url: str
    created: int
    closed: int | None
    merged: bool
    labels: list[str] = field(default_factory=list)
    assignees: list[str] = field(default_factory=list)

    @property
    def fixes(self) -> list[int]:
        return sorted({int(n) for n in FIXES.findall(self.body)}) if self.kind == "pr" else []


def _ts(s: str | None) -> int | None:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()) if s else None


CACHE = Path("data/cache")
CACHE_TTL = 24 * 3600


def cached_tickets(slug: str, max_items: int = 500) -> list[Ticket]:
    """fetch_tickets, cached on disk for a day: unauthenticated GitHub allows only 60 requests an hour."""
    f = CACHE / f"{slug.replace('/', '__')}.{max_items}.json"
    if f.exists() and time.time() - f.stat().st_mtime < CACHE_TTL:
        return [Ticket(**t) for t in json.loads(f.read_text())]
    tickets = fetch_tickets(slug, max_items)
    CACHE.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps([asdict(t) for t in tickets]))
    return tickets


def fetch_tickets(slug: str, max_items: int = 500) -> list[Ticket]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "lore-company-brain"}
    if token := os.getenv("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    out: list[Ticket] = []
    page = 1
    while len(out) < max_items:
        url = f"https://api.github.com/repos/{slug}/issues?state=all&per_page=100&sort=updated&page={page}"
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
            batch = json.load(r)
        if not batch:
            break
        for it in batch:
            pr = it.get("pull_request")
            out.append(Ticket(
                number=it["number"],
                kind="pr" if pr else "issue",
                title=it["title"] or "",
                body=(it.get("body") or "")[:4000],
                state=it["state"],
                author=(it.get("user") or {}).get("login", "ghost").lower(),
                url=it["html_url"],
                created=_ts(it["created_at"]),
                closed=_ts(it.get("closed_at")),
                merged=bool(pr and pr.get("merged_at")),
                labels=[lb["name"] for lb in it.get("labels", [])],
                assignees=[a["login"].lower() for a in it.get("assignees", [])],
            ))
        page += 1
    return out[:max_items]
