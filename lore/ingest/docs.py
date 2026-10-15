"""Find docs, what they talk about, and which of them record decisions (ADRs / RFCs)."""

from __future__ import annotations

import posixpath
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

DOC_EXT = (".md", ".mdx", ".rst", ".adoc")
SKIP = re.compile(r"(^|/)(node_modules|vendor|third_party|\.github/ISSUE_TEMPLATE)/")
# Changelogs and release notes describe the past on purpose; they are never "stale".
HISTORY = re.compile(r"(^|/)(changelog|changes|history|release[-_ ]?notes|news)[^/]*$", re.I)
DECISION_DIR = re.compile(r"(^|/)(adrs?|decisions?|rfcs?|design-docs?|proposals?)/", re.I)
STATUS = re.compile(r"^\W*status\W*[:\-]\s*\**\s*([A-Za-z][\w -]{1,30})", re.I | re.M)
DATE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")
TOKEN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]{2,}")
TEXT_LIMIT = 6000


@dataclass
class Doc:
    path: str
    title: str
    text: str
    mentions_files: set[str] = field(default_factory=set)
    mentions_modules: set[str] = field(default_factory=set)
    decision: dict | None = None


def _title(text: str, path: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()[:200]
        if line.startswith("title:"):
            return line[6:].strip().strip("\"'")[:200]
    return posixpath.basename(path)


def scan_docs(repo: Path, alive: set[str], known_files: set[str], modules: set[str]) -> list[Doc]:
    basenames = Counter(posixpath.basename(f) for f in known_files)
    unique_base = {posixpath.basename(f): f for f in known_files if basenames[posixpath.basename(f)] == 1}
    # Only multi-segment module paths: "src" alone is too generic to count as a mention.
    named_modules = {m for m in modules if "/" in m}

    docs = []
    for path in sorted(alive):
        if not path.lower().endswith(DOC_EXT) or SKIP.search(path) or HISTORY.search(path):
            continue
        try:
            text = (repo / path).read_text(errors="replace")
        except OSError:
            continue
        doc = Doc(path, _title(text, path), text[:TEXT_LIMIT])
        here = posixpath.dirname(path)
        for raw in set(TOKEN.findall(text)):
            tok = raw.rstrip(".,:;)").removeprefix("./")
            rel = posixpath.normpath(posixpath.join(here, tok))
            if rel in alive and rel != tok:  # a path relative to the doc wins over a same-named file at the root
                doc.mentions_files.add(rel)
            elif tok in known_files and ("." in tok or "/" in tok or len(tok) >= 6):
                doc.mentions_files.add(tok)
            elif rel in known_files:
                doc.mentions_files.add(rel)
            elif "." in tok and len(tok) >= 6 and tok in unique_base:
                doc.mentions_files.add(unique_base[tok])
            elif tok.strip("/") in named_modules:
                doc.mentions_modules.add(tok.strip("/"))
        doc.mentions_files.discard(path)

        name = posixpath.basename(path)
        if DECISION_DIR.search(path) and not name.lower().startswith(("readme", "index", "template")):
            status = STATUS.search(text)
            date = DATE.search(text)
            doc.decision = {
                "title": doc.title,
                "status": status.group(1).strip().lower() if status else "unknown",
                "date": date.group(1) if date else None,
            }
        docs.append(doc)
    return docs
