"""Read a repository's history with plain `git` — no API, no token, works on any public repo."""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

REC = "\x1e"
FLD = "\x1f"
SQUASH_PR = re.compile(r"\(#(\d+)\)\s*$")
NOREPLY = re.compile(r"^(?:\d+\+)?([^@]+)@users\.noreply\.github\.com$", re.I)
BOT = re.compile(r"\[bot\]|dependabot|renovate|github-actions|snyk|-bot\b|^bot@", re.I)


@dataclass
class Commit:
    sha: str
    author_name: str
    author_email: str
    ts: int
    message: str
    files: list[str] = field(default_factory=list)

    @property
    def pr_number(self) -> int | None:
        m = SQUASH_PR.search(self.message)
        return int(m.group(1)) if m else None

    @property
    def is_bot(self) -> bool:
        return bool(BOT.search(self.author_name) or BOT.search(self.author_email))


def email_key(email: str) -> str:
    """People without a known GitHub login are keyed by a one-way hash: Lore never stores email addresses."""
    return "git:" + hashlib.sha256(email.strip().lower().encode()).hexdigest()[:12]


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, errors="replace")
    if r.returncode:
        raise subprocess.CalledProcessError(r.returncode, ["git", *args], r.stdout, r.stderr.strip()[-500:])
    return r.stdout


def read_commits(repo: Path, since: str | None = None, until: str | None = None,
                 max_commits: int | None = None) -> list[Commit]:
    # --no-renames: rename detection needs file contents, which a blobless clone would fetch one by one.
    args = ["log", "--no-merges", "--no-renames", "--name-only", f"--pretty=format:{REC}%H{FLD}%an{FLD}%ae{FLD}%at{FLD}%s"]
    if since:
        args.append(f"--since={since}")
    if until:
        args.append(f"--until={until}")
    if max_commits:
        args.append(f"--max-count={max_commits}")
    commits = []
    for rec in git(repo, *args).split(REC)[1:]:
        header, _, body = rec.partition("\n")
        sha, name, email, ts, subject = header.split(FLD, 4)
        files = [line for line in body.splitlines() if line.strip()]
        commits.append(Commit(sha, name, email.lower(), int(ts), subject, files))
    return [c for c in commits if not c.is_bot]


def alive_files(repo: Path) -> set[str]:
    return set(git(repo, "ls-files").splitlines())


def head_ts(repo: Path) -> int:
    return int(git(repo, "log", "-1", "--format=%at").strip())


def repo_slug(repo: Path) -> str | None:
    """owner/name from the origin remote, if it points at GitHub."""
    try:
        url = git(repo, "remote", "get-url", "origin").strip()
    except subprocess.CalledProcessError:
        return None
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def module_of(path: str, depth: int = 2) -> str:
    parts = path.split("/")[:-1]
    return "/".join(parts[:depth]) if parts else "(root)"


@dataclass
class CoChange:
    a: str
    b: str
    count: int
    confidence: float  # P(b changes | a changes)


def co_changes(commits: list[Commit], max_files: int = 30, min_count: int = 3, min_conf: float = 0.3) -> list[CoChange]:
    """Files that change together. Directed: a -> b means "when a changes, b usually changes too".

    Huge commits (mass renames, formatting sweeps) are skipped — they say nothing about coupling.
    """
    changes: Counter[str] = Counter()
    pairs: Counter[tuple[str, str]] = Counter()
    for c in commits:
        files = sorted(set(c.files))
        if not files or len(files) > max_files:
            continue
        changes.update(files)
        pairs.update(combinations(files, 2))
    out = []
    for (a, b), n in pairs.items():
        if n < min_count:
            continue
        for x, y in ((a, b), (b, a)):
            conf = n / changes[x]
            if conf >= min_conf:
                out.append(CoChange(x, y, n, round(conf, 3)))
    return out
