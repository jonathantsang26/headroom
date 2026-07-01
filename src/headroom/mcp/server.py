"""Optional FastMCP server (Decision 2: DEFERRED — Phase 4+ add-on, not v1).

Deliberately a thin stub. The scored outputs are already materialized as queryable
Parquet by headroom.export, so exposing them to an MCP client is ~1 day of work when wanted:
each MCP tool is one parameterized DuckDB query over scores.parquet. Kept out of the
test/import path via a guarded import so a missing `fastmcp` never breaks the build.
"""

from __future__ import annotations

import json
from pathlib import Path


def _assert_manifest_shareable(scores_parquet: Path) -> None:
    """Refuse to serve non-publishable scores. The MCP path is a READ boundary too,
    so it must honor the same public-only guarantee as export: a scores parquet may
    only be served if its sibling run manifest says the run was shareable. A synthetic
    or CEII-tainted run (mode != "shareable") is refused here rather than served as if
    it were real. Fail closed when no manifest is found."""
    # export.deliver writes run_manifest<suffix>.json alongside scores<suffix>.parquet
    suffix = scores_parquet.name[len("scores"):-len(".parquet")]
    manifest_path = scores_parquet.with_name(f"run_manifest{suffix}.json")
    if not manifest_path.exists():
        raise RuntimeError(
            f"Refusing to serve {scores_parquet}: no sibling run manifest "
            f"({manifest_path.name}) to confirm the run is shareable. Fail closed."
        )
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("mode") != "shareable" or not manifest.get("public_only", False):
        raise RuntimeError(
            f"Refusing to serve {scores_parquet}: run mode is "
            f"{manifest.get('mode')!r} (public_only={manifest.get('public_only')}). "
            f"Only a shareable, all-public run may be exposed via MCP."
        )


def build_server(scores_parquet: str | Path = "data/processed/scores.parquet"):
    """Construct (but do not run) a FastMCP server exposing read-only query tools
    over the ranked scores. Raises a clear error if fastmcp isn't installed, or if the
    scores parquet is not from a shareable run (the public-only guarantee)."""
    scores_parquet = Path(scores_parquet)
    _assert_manifest_shareable(scores_parquet)

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
        with duckdb.connect() as con:
            return con.execute(
                "SELECT physical_corridor, p_top_k, recommended_get "
                "FROM read_parquet(?) ORDER BY p_top_k DESC, composite_expected DESC "
                "LIMIT ?",
                [str(scores_parquet), limit],
            ).fetchall()

    return mcp  # pragma: no cover
