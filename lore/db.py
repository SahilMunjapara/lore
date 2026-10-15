"""FalkorDB connection. One graph per company/tenant: `--graph acme` keeps every org isolated in one instance."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from falkordb import FalkorDB

load_dotenv()


def connect(graph: str | None = None):
    db = FalkorDB(
        host=os.getenv("FALKORDB_HOST", "localhost"),
        port=int(os.getenv("FALKORDB_PORT", "6379")),
        username=os.getenv("FALKORDB_USERNAME") or None,
        password=os.getenv("FALKORDB_PASSWORD") or None,
    )
    return db.select_graph(graph or os.getenv("LORE_GRAPH", "lore"))


def rows(graph, cypher: str, **params) -> list[dict]:
    """Run a query and return a list of dicts keyed by the RETURN aliases."""
    res = graph.query(cypher, params)
    cols = [h[1] if isinstance(h, (list, tuple)) else h for h in res.header]
    cols = [c.decode() if isinstance(c, bytes) else c for c in cols]
    return [dict(zip(cols, r)) for r in res.result_set]
