"""Turn a repository (plus its public GitHub issues/PRs) into a Snapshot of plain data, ready to load."""

from __future__ import annotations

import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import codeowners, docs as docs_mod, git_history, github

CLONES = Path("data/repos")


@dataclass
class Snapshot:
    repo: str
    slug: str | None
    head_ts: int
    commits: list[git_history.Commit]
    alive: set[str]
    module_of: dict[str, str]
    cochanges: list[git_history.CoChange]
    docs: list[docs_mod.Doc]
    tickets: list[github.Ticket]
    owners: dict[str, list[str]]  # file -> raw CODEOWNERS owners
    aliases: dict[str, str] = field(default_factory=dict)  # git email -> "gh:login"
    names: dict[str, str] = field(default_factory=dict)  # person key -> display name

    def person(self, email: str) -> str:
        return self.aliases.get(email) or git_history.email_key(email)

    def stats(self) -> dict:
        return {
            "repo": self.repo,
            "commits": len(self.commits),
            "people": len({self.person(c.author_email) for c in self.commits} | {"gh:" + t.author for t in self.tickets}),
            "files": len(self.module_of),
            "alive_files": len(self.alive),
            "modules": len(set(self.module_of.values())),
            "co_change_edges": len(self.cochanges),
            "docs": len(self.docs),
            "decisions": sum(1 for d in self.docs if d.decision),
            "doc_mentions": sum(len(d.mentions_files) + len(d.mentions_modules) for d in self.docs),
            "issues": sum(1 for t in self.tickets if t.kind == "issue"),
            "prs": sum(1 for t in self.tickets if t.kind == "pr"),
            "owned_files": len(self.owners),
            "identities_linked": len(self.aliases),
        }


def resolve(target: str) -> Path:
    """A local path, or a GitHub URL / owner/name that gets cloned (blobless, so it's quick)."""
    p = Path(target).expanduser()
    if p.is_dir():
        return p.resolve()
    m = re.match(r"^(?:https://github\.com/)?([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", target)
    if not m:
        raise SystemExit(f"Not a directory or GitHub repo: {target}")
    dest = CLONES / f"{m.group(1)}__{m.group(2)}"
    if not dest.exists():
        CLONES.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--filter=blob:none", f"https://github.com/{m.group(1)}/{m.group(2)}.git", str(dest)], check=True)
    return dest.resolve()


def link_identities(commits: list[git_history.Commit], tickets: list[github.Ticket]) -> dict[str, str]:
    """Map git emails to GitHub logins, so one person is one node:
    1. noreply addresses (123+login@users.noreply.github.com),
    2. squash-merge commits "(#123)" whose PR author we know — bots excluded, since people merge bot PRs,
    3. the same full name as an email already linked to a login.
    """
    aliases: dict[str, str] = {}
    for c in commits:
        if (m := git_history.NOREPLY.match(c.author_email)) and not git_history.BOT.search(m.group(1)):
            aliases[c.author_email] = "gh:" + m.group(1).lower()
    pr_author = {t.number: t.author for t in tickets if t.kind == "pr" and not git_history.BOT.search(t.author)}
    votes: dict[str, Counter] = defaultdict(Counter)
    for c in commits:
        if c.author_email not in aliases and (n := c.pr_number) in pr_author:
            votes[c.author_email][pr_author[n]] += 1
    for email, counter in votes.items():
        login, n = counter.most_common(1)[0]
        if n / sum(counter.values()) >= 0.6:
            aliases[email] = "gh:" + login

    name_to_login: dict[str, set[str]] = defaultdict(set)
    for c in commits:
        if c.author_email in aliases and " " in c.author_name.strip():
            name_to_login[c.author_name.strip().lower()].add(aliases[c.author_email])
    for c in commits:
        logins = name_to_login.get(c.author_name.strip().lower(), set())
        if c.author_email not in aliases and len(logins) == 1:
            aliases[c.author_email] = next(iter(logins))
    return aliases


def snapshot(target: str, *, use_github: bool = True, max_tickets: int = 500, since: str | None = None,
             until: str | None = None, module_depth: int = 2) -> Snapshot:
    path = resolve(target)
    slug = git_history.repo_slug(path)
    repo = slug or path.name
    commits = git_history.read_commits(path, since=since, until=until)
    alive = git_history.alive_files(path)
    known = alive | {f for c in commits for f in c.files}
    module_of = {f: git_history.module_of(f, module_depth) for f in known}

    tickets: list[github.Ticket] = []
    if use_github and slug:
        try:
            tickets = github.cached_tickets(slug, max_tickets)
        except Exception as e:  # rate limit or offline: the brain still works from git alone
            print(f"! GitHub fetch skipped ({e}). Set GITHUB_TOKEN for more requests.")

    rules = codeowners.read_rules(path)
    owners = {f: o for f in alive if (o := codeowners.owners_of(f, rules))} if rules else {}

    snap = Snapshot(
        repo=repo, slug=slug, head_ts=max((c.ts for c in commits), default=0) or git_history.head_ts(path), commits=commits, alive=alive,
        module_of=module_of, cochanges=git_history.co_changes(commits),
        docs=docs_mod.scan_docs(path, alive, known, set(module_of.values())),
        tickets=tickets, owners=owners, aliases=link_identities(commits, tickets),
    )
    for c in commits:  # most recent name wins (git log is newest-first)
        snap.names.setdefault(snap.person(c.author_email), c.author_name)
    return snap
