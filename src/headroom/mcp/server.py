"""Optional FastMCP server (Decision 2: DEFERRED — Phase 4+ add-on, not v1).

Deliberately a thin stub. The scored outputs are already materialized as queryable
Parquet by headroom.export, so exposing them to an MCP client is ~1 day of work when wanted:
each MCP tool is one parameterized DuckDB query over scores.parquet. Kept out of the
test/import path via a guarded import so a missing `fastmcp` never breaks the build.
"""

from __future__ import annotations

from pathlib import Path


def build_server(scores_parquet: str | Path = "data/processed/scores.parquet"):
    """Construct (but do not run) a FastMCP server exposing read-only query tools
    over the ranked scores. Raises a clear error if fastmcp isn't installed."""
    try:
        from fastmcp import FastMCP  # type: ignore
    except ImportError as e:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "FastMCP is not installed. The MCP server is a deferred Phase-4+ add-on; "
            "install `fastmcp` to enable it."
        ) from e

    import duckdb  # pragma: no cover

    mcp = FastMCP("headroom")  # pragma: no cover

    @mcp.tool()  # pragma: no cover
    def top_corridors(limit: int = 10) -> list[dict]:
        """Return the top-ranked corridors by rank stability."""
        con = duckdb.connect()
        return con.execute(
            "SELECT physical_corridor, p_top_k, recommended_get "
            "FROM read_parquet(?) ORDER BY p_top_k DESC, composite_expected DESC "
            "LIMIT ?",
            [str(scores_parquet), limit],
        ).fetchall()

    return mcp  # pragma: no cover
