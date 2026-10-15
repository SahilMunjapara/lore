import re

from lore import agent, memory


def intent(q: str) -> str:
    return next((name for name, rx in agent.INTENTS if re.search(rx, q, re.I)), "who_knows")


def test_intents():
    assert intent("What happens if Alice leaves?") == "departure"
    assert intent("Which docs are out of date?") == "stale_docs"
    assert intent("Who should fix: crash on bulk insert") == "route"
    assert intent("Why do we use Rust?") == "why"
    assert intent("Onboard me to the query planner") == "onboard"
    assert intent("What else changes together with src/a.rs?") == "ghosts"
    assert intent("Who knows about bulk insert?") == "who_knows"


def test_topic_extraction():
    assert agent._topic("Who knows about bulk insert?") == "bulk insert"
    assert agent._topic("Who owns the query planner") == "the query planner"


def test_cypher_tool_is_read_only():
    for q in ("MATCH (n) DETACH DELETE n", "CREATE (:X)", "MATCH (n) SET n.x = 1", "CALL algo.pageRank('a','b')"):
        assert agent.WRITE_WORDS.search(q)
    assert not agent.WRITE_WORDS.search("MATCH (p:Person) RETURN p.name LIMIT 5")


def test_evidence_keys_and_memory_key():
    result = {"modules": [{"key": "r:src/a", "module": "src/a"}], "experts": [{"key": "gh:alice", "areas": []}],
              "rows": [{"module_key": "r:src/b"}]}
    assert memory.evidence_keys(result) == {"r:src/a", "gh:alice", "r:src/b"}
    assert memory.norm("Who knows about Bulk-Insert?") == memory.norm("who knows bulk insert")


def test_render_every_tool():
    who = {"experts": [{"key": "gh:a", "name": "Alice", "score": 3.2, "active": True, "areas": ["src/a"],
                        "evidence": [{"sha": "abcdef1234", "message": "fix", "ts": 0}]}], "owners": [{"owner": "org/core"}]}
    assert "Ask Alice" in agent.render("who_knows", who)
    dep = {"knowledge_at_risk": [{"module": "src/a", "share": 0.9, "commits": 12, "last": 0, "successors": []}],
           "sole_author_docs": [{"doc": "docs/a.md", "title": "A"}], "decisions": [], "open_work": []}
    out = agent.render("departure", dep)
    assert "single point of failure" in out and "90%" in out
    stale = [{"doc": "README.md", "doc_updated": 0, "drift": [{"file": "x.py", "changes": 0, "deleted": True}],
              "fix_owner": [{"name": "Bob"}]}]
    assert "deleted" in agent.render("stale_docs", stale)
    route = {"candidates": [{"name": "Bob", "fit": 2.0, "open_load": 1}], "modules": [{"module": "src/a"}], "via": "x"}
    assert "Assign to Bob" in agent.render("route", route)
    assert "80%" in agent.render("ghosts", [{"changed": "a", "usually_also": "b", "confidence": 0.8, "together": 4}])
    assert "no record" in agent.render("why", {"decisions": [], "discussions": [], "commits": []})


def test_claude_loop_runs_tools_and_returns_text(monkeypatch):
    """Simulate the API: first turn asks for a tool, second turn answers."""
    from types import SimpleNamespace as NS
    import anthropic

    calls = []
    turns = [
        NS(stop_reason="tool_use", content=[NS(type="tool_use", id="t1", name="find_person", input={"query": "alice"})]),
        NS(stop_reason="end_turn", content=[NS(type="text", text="Alice knows it.")]),
    ]

    class FakeMessages:
        def create(self, **kw):
            calls.append(kw)
            return turns[len(calls) - 1]

    class FakeClient:
        beta = NS(messages=FakeMessages())

    monkeypatch.setattr(anthropic, "Anthropic", lambda: FakeClient())
    monkeypatch.setattr(agent.brain, "find_person", lambda g, q: [{"key": "gh:alice", "name": "Alice"}])
    trace = []
    assert agent.plan_with_claude(None, "who is alice?", trace) == "Alice knows it."
    assert trace[0]["tool"] == "find_person" and trace[0]["result"][0]["key"] == "gh:alice"
    first = calls[0]
    assert first["model"] == "claude-opus-5-5" and first["fallbacks"] == "default"
    assert all(t["strict"] for t in first["tools"])
    messages = calls[1]["messages"]  # the same list, so it also holds the final assistant turn
    tool_result = messages[2]["content"][0]
    assert tool_result["type"] == "tool_result" and tool_result["tool_use_id"] == "t1"
