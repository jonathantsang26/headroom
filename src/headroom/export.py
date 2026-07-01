"""Phase 4 delivery — the ranked export is the real shareable deliverable.

The publishable gate decides the mode, by construction:
  * every score is checked with assert_publishable(public_only=True);
  * if ANY score's lineage transitively touches a non-publishable source (all
    synthetic runs do), a SHAREABLE artifact is refused and only an INTERNAL,
    clearly-stamped export is written;
  * a real all-public run passes the gate and writes the shareable artifact.

So a fabricated shortlist can never masquerade as publishable — the guard is the
same lineage walk built in Phase 0, not a flag someone can forget.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from headroom.pipeline import PipelineResult
from headroom.provenance.gate import PublishabilityError, assert_publishable
from headroom.provenance.versions import build_run_manifest

# Deterministic stamp (no wall-clock in the reproducible path).
DEFAULT_GENERATED_AT = datetime(2026, 6, 30, tzinfo=timezone.utc)

_SIGNAL_ORDER = (
    "rent", "loading", "persistence", "demand_pressure", "planning",
    "cost_of_inaction",
)


@dataclass
class Delivery:
    mode: str  # "shareable" | "internal-synthetic"
    public_only: bool
    blocked: list[str]  # constraint_ids whose lineage is non-publishable
    paths: dict[str, str] = field(default_factory=dict)
    manifest: dict = field(default_factory=dict)


def scoring_to_manifest(scoring) -> dict:
    return {
        "seed": scoring.seed,
        "draws": scoring.draws,
        "top_fraction": scoring.top_fraction,
        "weights": scoring.weights,
        "references": scoring.references,
    }


def build_score_rows(result: PipelineResult) -> list[dict]:
    """One flat row per flowgate, ranked, with signal expecteds + provenance."""
    rows = []
    for rank, s in enumerate(result.model.scores, start=1):
        row = {
            "rank": rank,
            "constraint_id": s.constraint_id,
            "physical_corridor": s.physical_corridor,
            "p_top_k": round(s.p_top_k, 4),
            "composite_lo": round(s.composite.lo, 4),
            "composite_expected": round(s.composite.expected, 4),
            "composite_hi": round(s.composite.hi, 4),
            "recommended_get": s.recommended_get,
            "screen_status": result.bucket_b.screen_status.get(s.constraint_id, ""),
            "provenance": s.provenance,
        }
        for name in _SIGNAL_ORDER:
            row[f"sig_{name}"] = round(s.signals[name].expected, 4)
        rows.append(row)
    return rows


def _gate_blocked(result: PipelineResult) -> list[str]:
    blocked = []
    for s in result.model.scores:
        try:
            assert_publishable(
                s.composite.lineage_id, result.store, result.registry,
                public_only=True,
            )
        except PublishabilityError:
            blocked.append(s.constraint_id)
    return blocked


def _write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2))


def _materialize_parquet(json_path: Path, parquet_path: Path) -> bool:
    """Write a queryable Parquet of the ranked rows via DuckDB (Decision 2: keep
    outputs cleanly queryable so an MCP server is a 1-day add). Best-effort."""
    try:
        import duckdb
    except ImportError:  # pragma: no cover
        return False
    # COPY ... TO does not accept a bound parameter for the file path; inline the
    # (internally-controlled) paths with quote-escaping.
    jp = str(json_path).replace("'", "''")
    pp = str(parquet_path).replace("'", "''")
    con = duckdb.connect()
    con.execute(f"COPY (SELECT * FROM read_json_auto('{jp}')) TO '{pp}' (FORMAT PARQUET)")
    con.close()
    return True


def deliver(
    result: PipelineResult,
    out_dir: str | Path,
    *,
    generated_at: datetime = DEFAULT_GENERATED_AT,
) -> Delivery:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    blocked = _gate_blocked(result)
    if blocked:
        # Non-public (synthetic) lineage -> shareable export refused by the gate.
        mode, public_only, suffix = "internal-synthetic", False, ".INTERNAL.synthetic"
    else:
        mode, public_only, suffix = "shareable", True, ""

    rows = build_score_rows(result)
    corridors = result.model.shortlist

    rows_json = out / f"scores{suffix}.json"
    _write_json(rows_json, rows)
    shortlist_json = out / f"shortlist{suffix}.json"
    _write_json(shortlist_json, corridors)

    # flat CSV (the forwarded artifact)
    csv_path = out / f"shortlist{suffix}.csv"
    _write_csv(csv_path, rows)

    manifest = build_run_manifest(
        result.registry,
        run_label=f"{result.region.name}-{mode}",
        generated_at=generated_at,
        public_only=public_only,
        scoring=scoring_to_manifest(result.scoring),
        region=result.region.name,
        mode=mode,
    )
    manifest["shortlist_k"] = result.model.k
    manifest["n_constraints"] = result.model.n_constraints
    manifest_path = out / f"run_manifest{suffix}.json"
    _write_json(manifest_path, manifest)

    parquet_path = out / f"scores{suffix}.parquet"
    have_parquet = _materialize_parquet(rows_json, parquet_path)

    paths = {
        "scores_json": str(rows_json),
        "shortlist_json": str(shortlist_json),
        "csv": str(csv_path),
        "manifest": str(manifest_path),
    }
    if have_parquet:
        paths["parquet"] = str(parquet_path)

    return Delivery(
        mode=mode, public_only=public_only, blocked=blocked,
        paths=paths, manifest=manifest,
    )


def _write_csv(path: Path, rows: list[dict]) -> None:
    import csv

    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def export_shareable_or_raise(result: PipelineResult, out_dir: str | Path) -> Delivery:
    """Strict path: refuse to write anything unless the whole shortlist is
    publishable. Used where only a shareable artifact is acceptable."""
    blocked = _gate_blocked(result)
    if blocked:
        raise PublishabilityError(
            f"Shareable export refused: {len(blocked)} scores have non-publishable "
            f"lineage (e.g. {blocked[:3]}). This is a synthetic/non-public run."
        )
    return deliver(result, out_dir)
