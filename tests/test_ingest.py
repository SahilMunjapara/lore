import subprocess
from pathlib import Path

from lore.ingest import codeowners, git_history, link_identities, snapshot
from lore.ingest.github import Ticket


def sh(repo: Path, *args: str, env=None):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def commit(repo: Path, who: str, email: str, msg: str, files: dict[str, str]):
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    sh(repo, "add", "-A")
    sh(repo, "-c", f"user.name={who}", "-c", f"user.email={email}", "commit", "-m", msg)


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "acme"
    repo.mkdir()
    sh(repo, "init", "-q")
    for i in range(4):  # billing.py and invoice.html always change together, with no import between them
        commit(repo, "Alice", "alice@acme.dev", f"billing tweak {i} (#{10 + i})",
               {"src/billing/billing.py": f"v{i}", "templates/mail/invoice.html": f"v{i}"})
    commit(repo, "Bob", "123+bobby@users.noreply.github.com", "auth", {"src/auth/login.py": "x"})
    commit(repo, "Alice", "alice@acme.dev", "docs", {
        "docs/adr/0001-use-graphs.md": "# Use a graph for org data\n\nStatus: Accepted\nDate: 2026-01-02\n\nSee `src/billing/billing.py` and src/auth.\n",
        "README.md": "# Acme\nBilling lives in billing.py\n",
        ".github/CODEOWNERS": "* @acme/core\n/src/billing/ @alice-gh\n*.html @acme/design\n",
    })
    return repo


def test_glob_rules():
    rules = [codeowners.Rule(p, [o], codeowners._glob_to_regex(p)) for p, o in
             [("*", "@all"), ("/src/billing/", "@pay"), ("*.html", "@design"), ("docs/**/*.md", "@docs")]]
    assert codeowners.owners_of("src/billing/a/b.py", rules) == ["@pay"]
    assert codeowners.owners_of("templates/x.html", rules) == ["@design"]
    assert codeowners.owners_of("docs/a/b/c.md", rules) == ["@docs"]
    assert codeowners.owners_of("lib/x.py", rules) == ["@all"]


def test_snapshot_end_to_end(tmp_path):
    snap = snapshot(str(make_repo(tmp_path)), use_github=False)
    s = snap.stats()
    assert s["commits"] == 6 and s["decisions"] == 1

    ghost = {(c.a, c.b) for c in snap.cochanges}
    assert ("src/billing/billing.py", "templates/mail/invoice.html") in ghost

    adr = next(d for d in snap.docs if d.decision)
    assert adr.decision == {"title": "Use a graph for org data", "status": "accepted", "date": "2026-01-02"}
    assert "src/billing/billing.py" in adr.mentions_files
    assert "src/auth" in adr.mentions_modules
    readme = next(d for d in snap.docs if d.path == "README.md")
    assert "src/billing/billing.py" in readme.mentions_files  # unique basename

    assert snap.owners["src/billing/billing.py"] == ["@alice-gh"]
    assert snap.owners["templates/mail/invoice.html"] == ["@acme/design"]
    assert snap.person("123+bobby@users.noreply.github.com") == "gh:bobby"
    assert snap.person("alice@acme.dev").startswith("git:") and "@" not in snap.person("alice@acme.dev")


def test_identity_from_squash_merges():
    commits = [git_history.Commit(str(i), "Alice", "alice@acme.dev", i, f"fix (#{i})", []) for i in range(3)]
    tickets = [Ticket(i, "pr", "t", "", "closed", "alice-gh", "", 0, 0, True) for i in range(3)]
    assert link_identities(commits, tickets) == {"alice@acme.dev": "gh:alice-gh"}


def test_identity_skips_bots_and_merges_same_full_name():
    commits = [git_history.Commit("1", "Guy K", "guy@work.dev", 1, "bump x (#1)", []),
               git_history.Commit("2", "Guy Korland", "1+gkorland@users.noreply.github.com", 2, "feat", []),
               git_history.Commit("3", "Guy Korland", "guy@home.dev", 3, "fix", [])]
    tickets = [Ticket(1, "pr", "bump", "", "closed", "dependabot[bot]", "", 0, 0, True)]
    aliases = link_identities(commits, tickets)
    assert "guy@work.dev" not in aliases  # merging a bot's PR doesn't make you the bot
    assert aliases["guy@home.dev"] == "gh:gkorland"
