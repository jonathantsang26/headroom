"""Phase 4 delivery — the ranked export is the real shareable deliverable."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from headroom.pipeline import PipelineResult
from headroom.provenance.digests import ranking_digest
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
    blocked_tables: list[str] = field(default_factory=list)  # non-publishable input tables
    paths: dict[str, str] = field(default_factory=dict)
    manifest: dict = field(default_factory=dict)


def scoring_to_manifest(scoring) -> dict:
    return {
        "seed": scoring.seed,
        "draws": scoring.draws,
        "top_fraction": scoring.top_fraction,
        "weights": scoring.weights,
        "signal_order": list(scoring.weights),
        "references": scoring.references,
    }


def build_score_rows(result: PipelineResult) -> list[dict]:
    """One flat row per flowgate, ranked, with signal expecteds + provenance."""
    rows = []
    meta = {c.constraint_id: c for c in result.bucket_b.constraints}
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
            "monitored_facility": meta[s.constraint_id].monitored_facility or "",
            "contingency_facility": meta[s.constraint_id].contingency_facility or "",
            "get_basis": s.get_basis,
            "provenance": s.provenance,
        }
        for name in _SIGNAL_ORDER:
            row[f"sig_{name}"] = round(s.signals[name].expected, 4)
        for name in _SIGNAL_ORDER:
            row[f"cov_{name}"] = s.signal_coverage.get(name, "observed")
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


def _tables_blocked(result: PipelineResult) -> list[str]:
    """Input tables whose stamps fail the gate. The quality report summarizes every
    input table, so a shareable run needs every table publishable, not only the
    tables its scores happen to depend on."""
    blocked = []
    for node in result.store._nodes.values():
        if node.lineage_id.startswith("table:") and not node.inputs:
            try:
                assert_publishable(node.lineage_id, result.store, result.registry,
                                   public_only=True)
            except PublishabilityError:
                blocked.append(node.lineage_id.removeprefix("table:"))
    return sorted(blocked)


def _atomic_write(path: Path, data: bytes) -> str:
    """Write beside the destination and rename, so a reader never sees a partial
    artifact; returns the bytes' sha256 for the run manifest."""
    tmp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return hashlib.sha256(data).hexdigest()


def _json_bytes(obj) -> bytes:
    return json.dumps(obj, indent=2).encode("utf-8")


def _csv_bytes(rows: list[dict]) -> bytes:
    if not rows:
        return b""
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _materialize_parquet(json_path: Path, parquet_path: Path) -> str | None:
    """Write a queryable Parquet of the ranked rows via DuckDB (Decision 2: keep
    outputs cleanly queryable so an MCP server is a 1-day add). Best-effort;
    returns the file's sha256, or None when it could not be written."""
    try:
        import duckdb
    except ImportError:  # pragma: no cover
        return None
    tmp = parquet_path.with_name(f".{parquet_path.name}.{uuid4().hex}.tmp")
    jp = str(json_path).replace("'", "''")
    pp = str(tmp).replace("'", "''")
    try:
        with duckdb.connect() as con:
            con.execute(
                f"COPY (SELECT * FROM read_json_auto('{jp}')) "
                f"TO '{pp}' (FORMAT PARQUET)"
            )
        digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
        os.replace(tmp, parquet_path)
        return digest
    except Exception:  # pragma: no cover - depends on filesystem/DuckDB state
        return None
    finally:
        tmp.unlink(missing_ok=True)


def deliver(
    result: PipelineResult,
    out_dir: str | Path,
    *,
    generated_at: datetime = DEFAULT_GENERATED_AT,
) -> Delivery:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    blocked = _gate_blocked(result)
    blocked_tables = _tables_blocked(result)
    if blocked or blocked_tables:
        # Non-public (synthetic) lineage -> shareable export refused by the gate.
        mode, public_only, suffix = "internal-synthetic", False, ".INTERNAL.synthetic"
    else:
        mode, public_only, suffix = "shareable", True, ""

    rows = build_score_rows(result)
    corridors = result.model.shortlist

    rows_json = out / f"scores{suffix}.json"
    shortlist_json = out / f"shortlist{suffix}.json"
    csv_path = out / f"shortlist{suffix}.csv"  # the forwarded artifact
    quality_path = out / f"quality_report{suffix}.json"
    parquet_path = out / f"scores{suffix}.parquet"
    manifest_path = out / f"run_manifest{suffix}.json"

    qr = result.quality_report
    outputs = {
        "scores_json": _atomic_write(rows_json, _json_bytes(rows)),
        "shortlist_json": _atomic_write(shortlist_json, _json_bytes(corridors)),
        "csv": _atomic_write(csv_path, _csv_bytes(rows)),
        "quality_report": _atomic_write(quality_path, _json_bytes(qr)),
    }
    parquet_sha = _materialize_parquet(rows_json, parquet_path)
    if parquet_sha is not None:
        outputs["parquet"] = parquet_sha

    manifest = build_run_manifest(
        result.registry,
        run_label=f"{result.region.name}-{mode}",
        generated_at=generated_at,
        public_only=public_only,
        scoring=scoring_to_manifest(result.scoring),
        region=result.region.name,
        mode=mode,
        inputs=result.input_identity,
        runtime=result.runtime,
        output_sha256=ranking_digest(result),
    )
    manifest["shortlist_k"] = result.model.k
    manifest["n_constraints"] = result.model.n_constraints
    manifest["coverage"] = {
        "tables": {t: e["coverage"] for t, e in qr.get("tables", {}).items()},
        "unobserved_weight_all": qr.get("unobserved_weight_all", 0.0),
    }
    names = {"scores_json": rows_json, "shortlist_json": shortlist_json, "csv": csv_path,
             "quality_report": quality_path, "parquet": parquet_path}
    # Per-file digests of the artifacts written beside this manifest (written last).
    manifest["outputs"] = {
        key: {"path": names[key].name, "sha256": digest} for key, digest in outputs.items()
    }
    _atomic_write(manifest_path, _json_bytes(manifest))

    paths = {
        "scores_json": str(rows_json),
        "shortlist_json": str(shortlist_json),
        "csv": str(csv_path),
        "manifest": str(manifest_path),
        "quality_report": str(quality_path),
    }
    if parquet_sha is not None:
        paths["parquet"] = str(parquet_path)

    return Delivery(
        mode=mode, public_only=public_only, blocked=blocked, blocked_tables=blocked_tables,
        paths=paths, manifest=manifest,
    )


def export_shareable_or_raise(result: PipelineResult, out_dir: str | Path) -> Delivery:
    """Strict path: refuse to write anything unless the whole shortlist is
    publishable. Used where only a shareable artifact is acceptable."""
    blocked = _gate_blocked(result)
    blocked_tables = _tables_blocked(result)
    if blocked or blocked_tables:
        raise PublishabilityError(
            f"Shareable export refused: {len(blocked)} scores have non-publishable "
            f"lineage (e.g. {blocked[:3]}); non-publishable input tables: "
            f"{blocked_tables}. This is a synthetic/non-public run."
        )
    return deliver(result, out_dir)
