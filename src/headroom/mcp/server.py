from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

# export.deliver writes run_manifest<suffix>.json alongside scores<suffix>.parquet
_SCORES_PARQUET = re.compile(r"scores((?:\.INTERNAL(?:\.synthetic)?)?)\.parquet")


def _assert_manifest_shareable(scores_parquet: Path) -> None:
    """Refuse to serve non-publishable scores. The MCP path is a READ boundary too,
    so it must honor the same public-only guarantee as export: a scores parquet may
    only be served if its sibling run manifest says the run was shareable. A synthetic
    or CEII-tainted run (mode != "shareable") is refused here rather than served as if
    it were real. Fail closed when no manifest is found, when the file is not named as
    an export's scores parquet, or when its bytes are not the ones the manifest
    records (A14)."""
    match = _SCORES_PARQUET.fullmatch(scores_parquet.name)
    if match is None:
        raise RuntimeError(
            f"Refusing to serve {scores_parquet}: not an export's scores parquet "
            "(scores[.INTERNAL[.synthetic]].parquet). Fail closed."
        )
    manifest_path = scores_parquet.with_name(f"run_manifest{match.group(1)}.json")
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
    recorded = ((manifest.get("outputs") or {}).get("parquet") or {}).get("sha256")
    if not recorded:
        raise RuntimeError(
            f"Refusing to serve {scores_parquet}: its run manifest records no parquet "
            "digest to check it against. Fail closed."
        )
    actual = hashlib.sha256(scores_parquet.read_bytes()).hexdigest()
    if actual != recorded:
        raise RuntimeError(
            f"Refusing to serve {scores_parquet}: its sha256 {actual[:12]}... is not the "
            f"{recorded[:12]}... its run manifest records. Fail closed."
        )


def top_flowgates_rows(scores_parquet: str | Path, limit: int = 10) -> list[dict]:
    """The top flowgates in the run's own rank order (the export's `rank`, which
    follows the run's ranking: composite or EV), one dict per row. get_shown is the
    recommendation to show: where get_resolved is false the evidence leaves every GET
    in get_set open, and recommended_get is only the rule's tie-break among them."""
    import duckdb

    with duckdb.connect() as con:
        cur = con.execute(
            "SELECT rank, constraint_id, physical_corridor, p_top_k, get_shown, "
            "get_resolved, get_set, recommended_get, ev_recommended_get, ev_mean_musd "
            "FROM read_parquet(?) ORDER BY rank LIMIT ?",
            [str(scores_parquet), int(limit)],
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


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

    mcp = FastMCP("headroom")  # pragma: no cover

    @mcp.tool()  # pragma: no cover
    def top_flowgates(limit: int = 10) -> list[dict]:
        """Return the top flowgates in the run's own rank order (the export's `rank`,
        which follows the run's ranking: composite or EV). Show get_shown as the
        recommended GET: where get_resolved is false, public evidence cannot tell the
        GETs in get_set apart."""
        _assert_manifest_shareable(scores_parquet)  # the file may have changed since
        return top_flowgates_rows(scores_parquet, limit)

    return mcp  # pragma: no cover
