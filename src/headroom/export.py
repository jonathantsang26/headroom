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

from headroom.model.ev import priors_sha256
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
    # any other leaf source in the store that fails the gate, and unresolved input ids
    blocked_sources: list[str] = field(default_factory=list)
    paths: dict[str, str] = field(default_factory=dict)
    manifest: dict = field(default_factory=dict)


def scoring_to_manifest(scoring) -> dict:
    out = {
        "seed": scoring.seed,
        "draws": scoring.draws,
        "top_fraction": scoring.top_fraction,
        "weights": scoring.weights,
        "signal_order": list(scoring.weights),
        "references": scoring.references,
    }
    # Recorded only when set, so composite-ranked manifests keep their earlier shape.
    if scoring.ranking != "composite":
        out["ranking"] = scoring.ranking
    if scoring.get_priors is not None:
        out["get_priors"] = scoring.get_priors
        out["get_priors_sha256"] = priors_sha256(scoring.get_priors)
    if scoring.reference_rule is not None:
        out["reference_rule"] = scoring.reference_rule
    if scoring.definitions:
        out["definitions"] = scoring.definitions
    return out


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
        co = result.bucket_b.congestion.get(s.constraint_id)
        rnd = lambda v, n=4: round(v, n) if v is not None else ""
        # observed quantities, in their own units (what a planner reads first)
        row.update({
            "rent_musd": rnd(co.rent_musd.expected, 6) if co and co.rent_musd else "",
            "value_of_1mw_relief_usd": rnd(co.shadow_price_sum, 2) if co else "",
            "hours_binding": rnd(co.hours_binding.expected, 1) if co else "",
            "breached_hours": rnd(co.breached_hours, 1) if co else "",
            "months_binding": co.months_binding if co and co.months_binding is not None else "",
        })
        row.update({
            "ev_mean_musd": rnd(s.ev_mean, 6),
            "ev_lo_musd": rnd(s.ev.lo, 6) if s.ev else "",
            "ev_expected_musd": rnd(s.ev.expected, 6) if s.ev else "",
            "ev_hi_musd": rnd(s.ev.hi, 6) if s.ev else "",
            "p_top_k_composite": rnd(s.p_top_k_composite) if s.p_top_k_composite is not None else "",
            "get_rule": s.get_rule,
            "get_evidence": s.get_evidence,
            "get_candidates": ";".join(s.get_candidates),
        })
        for option in _GET_OPTIONS:
            p = s.get_probabilities.get(option)
            row[f"p_best_{_slug(option)}"] = round(p, 4) if p is not None else ""
        rows.append(row)
    return rows


_GET_OPTIONS = ("No cost-effective GET", "Dynamic Line Rating", "Advanced Power Flow Control",
                "Topology Optimization", "Combination")


def _slug(name: str) -> str:
    return "none" if name.startswith("No ") else "".join(w[0] for w in name.split()).lower()


def _gate_blocked(result: PipelineResult) -> list[str]:
    """Scores whose exported values (composite, and EV when present) fail the gate."""
    blocked = []
    for s in result.model.scores:
        try:
            for env in (s.composite, s.ev):
                if env is not None:
                    assert_publishable(env.lineage_id, result.store, result.registry,
                                       public_only=True)
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


def _store_blocked(result: PipelineResult) -> list[str]:
    """The whole store, not only what scores and tables reach: every leaf's source
    must pass the gate and every cited input must resolve, so a value minted outside
    the score and table paths cannot reach a shareable artifact unchecked."""
    by_source: dict[str, str] = {}
    for leaf in result.store.leaves():
        by_source.setdefault(leaf.source, leaf.lineage_id)
    blocked = []
    for source, lineage_id in sorted(by_source.items()):
        try:
            assert_publishable(lineage_id, result.store, result.registry, public_only=True)
        except PublishabilityError:
            blocked.append(source)
    dangling = result.store.dangling_inputs()
    if dangling:
        blocked.append(f"unresolved inputs ({len(dangling)}, e.g. {sorted(dangling)[0]})")
    return blocked


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
    blocked_sources = _store_blocked(result)
    if blocked or blocked_tables or blocked_sources:
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
    # Serialize everything before writing anything: a value that cannot be written
    # fails here, not after the artifacts already replaced an earlier run's.
    blobs = {
        "scores_json": _json_bytes(rows),
        "shortlist_json": _json_bytes(corridors),
        "csv": _csv_bytes(rows),
        "quality_report": _json_bytes(qr),
    }
    _json_bytes(manifest)
    names = {"scores_json": rows_json, "shortlist_json": shortlist_json, "csv": csv_path,
             "quality_report": quality_path, "parquet": parquet_path}
    outputs = {key: _atomic_write(names[key], data) for key, data in blobs.items()}
    parquet_sha = _materialize_parquet(rows_json, parquet_path)
    if parquet_sha is not None:
        outputs["parquet"] = parquet_sha
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
        blocked_sources=blocked_sources, paths=paths, manifest=manifest,
    )


def export_shareable_or_raise(result: PipelineResult, out_dir: str | Path) -> Delivery:
    """Strict path: refuse to write anything unless the whole shortlist is
    publishable. Used where only a shareable artifact is acceptable."""
    blocked = _gate_blocked(result)
    blocked_tables = _tables_blocked(result)
    blocked_sources = _store_blocked(result)
    if blocked or blocked_tables or blocked_sources:
        raise PublishabilityError(
            f"Shareable export refused: {len(blocked)} scores have non-publishable "
            f"lineage (e.g. {blocked[:3]}); non-publishable input tables: "
            f"{blocked_tables}; non-publishable sources in lineage: {blocked_sources}. "
            "This is a synthetic/non-public run."
        )
    return deliver(result, out_dir)
