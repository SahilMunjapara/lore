"""Parse CODEOWNERS into who-owns-what. Last matching rule wins, as on GitHub."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .git_history import email_key

LOCATIONS = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")


@dataclass
class Rule:
    pattern: str
    owners: list[str]
    regex: re.Pattern


def _glob_to_regex(pattern: str) -> re.Pattern:
    anchored = pattern.startswith("/")
    p = pattern.strip("/")
    out, i = "", 0
    while i < len(p):
        if p.startswith("**", i):
            out += ".*"
            i += 2
        elif p[i] == "*":
            out += "[^/]*"
            i += 1
        elif p[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(p[i])
            i += 1
    # A pattern without a slash in the middle matches at any depth, like .gitignore.
    prefix = "^" if anchored or "/" in p else "^(?:.*/)?"
    return re.compile(prefix + out + "(?:/.*)?$")


def read_rules(repo: Path) -> list[Rule]:
    for loc in LOCATIONS:
        f = repo / loc
        if f.is_file():
            rules = []
            for line in f.read_text(errors="replace").splitlines():
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                pattern, *owners = line.split()
                if owners:
                    rules.append(Rule(pattern, owners, _glob_to_regex(pattern)))
            return rules
    return []


def owners_of(path: str, rules: list[Rule]) -> list[str]:
    for rule in reversed(rules):
        if rule.regex.match(path):
            return rule.owners
    return []


def owner_key(owner: str) -> tuple[str, str]:
    """('team', 'org/team') | ('person', 'gh:login') | ('person', 'git:<hash of email>')."""
    if owner.startswith("@") and "/" in owner:
        return "team", owner[1:]
    if owner.startswith("@"):
        return "person", "gh:" + owner[1:].lower()
    return "person", email_key(owner)
