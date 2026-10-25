"""Write the constraint panel. The publishable gate decides shareable vs internal."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from headroom.panel.aggregate import PanelResult
from headroom.panel.tables import OPTIONAL_VALUES
from headroom.provenance.digests import canonical_json
from headroom.provenance.envelope import Fact
from headroom.provenance.gate import assert_publishable
from headroom.provenance.versions import build_run_manifest

# Deterministic stamp (no wall-clock in the reproducible path).
DEFAULT_GENERATED_AT = datetime(2026, 6, 30, tzinfo=timezone.utc)
VALUE_FIELDS = ("hours_binding", *OPTIONAL_VALUES)
CAVEATS = (
    "Observed binding history only; not a power-flow study, forecast, or deployment finding.",
    "A month counts as not binding only where the source covered it; uncovered months "
    "are listed and never treated as zero.",
    "Partial-coverage months give lower bounds on binding hours.",
    "Footprint matches are manual. Only verified matches with recorded evidence are "
    "included; all others are listed as unresolved.",
    "Shadow prices and congestion cost appear only where the source publishes them.",
)
MONTHLY_COLUMNS = (
    "constraint_id", "market", "period", "coverage", "days_covered", "days_expected",
    "binding", *VALUE_FIELDS, "missing", "flags", "raw_refs", "hours_lineage_id",
)
SUMMARY_COLUMNS = (
    "constraint_id", "market", "corridor_ids", "footprint_elements", "window_start",
    "window_end", "months", "months_full", "months_partial", "months_not_covered",
    "months_binding", "first_binding", "last_binding", *VALUE_FIELDS, "seen_in_source",
    "confidence", "flags", "missing", "reported_facilities", "hours_lineage_id",
)
UNRESOLVED_COLUMNS = (
    "element_id", "element_kind", "constraint_id", "corridor_id", "match_status",
    "evidence", "evidence_refs",
)
COVERAGE_COLUMNS = ("market", "period", "coverage", "days_covered", "days_expected", "raw_refs")


@dataclass
class PanelDelivery:
    mode: str  # "shareable" | "internal-synthetic" | "internal"
    public_only: bool
    blocked_sources: list[str]
    paths: dict[str, str] = field(default_factory=dict)
    manifest: dict = field(default_factory=dict)


def _value(fact: Fact | None):
    return None if fact is None else fact.value


def _lineage(fact: Fact | None) -> str:
    return "" if fact is None else fact.lineage_id


def monthly_rows(result: PanelResult) -> list[dict]:
    rows = []
    for cell in result.cells:
        row = {
            "constraint_id": cell.constraint_id,
            "market": cell.market,
            "period": cell.period,
            "coverage": cell.coverage,
            "days_covered": cell.days_covered,
            "days_expected": cell.days_expected,
            "binding": {True: "yes", False: "no", None: "unknown"}[cell.binding],
        }
        for name in VALUE_FIELDS:
            row[name] = _value(cell.values.get(name))
        hours = cell.values.get("hours_binding")
        row["missing"] = "; ".join(cell.missing)
        row["flags"] = ";".join(hours.flags) if hours is not None else ""
        row["raw_refs"] = ";".join(cell.raw_refs)
        row["hours_lineage_id"] = _lineage(hours)
        rows.append(row)
    return rows


def summary_rows(result: PanelResult) -> list[dict]:
    rows = []
    for s in result.summaries:
        row = {
            "constraint_id": s.constraint_id,
            "market": s.market,
            "corridor_ids": ";".join(sorted({m.corridor_id for m in s.matches if m.corridor_id})),
            "footprint_elements": ";".join(sorted({m.element_id for m in s.matches})),
            "window_start": result.window[0],
            "window_end": result.window[1],
            "months": s.months,
            "months_full": s.months_full,
            "months_partial": s.months_partial,
            "months_not_covered": ";".join(s.months_not_covered),
            "months_binding": s.months_binding,
            "first_binding": s.first_binding or "",
            "last_binding": s.last_binding or "",
        }
        for name in VALUE_FIELDS:
            row[name] = _value(s.values.get(name))
        row["seen_in_source"] = "yes" if s.seen_in_source else "no"
        row["confidence"] = s.confidence
        row["flags"] = ";".join(s.flags)
        row["missing"] = "; ".join(s.missing)
        row["reported_facilities"] = " | ".join(s.reported_facilities)
        row["hours_lineage_id"] = _lineage(s.values.get("hours_binding"))
        rows.append(row)
    return rows


def unresolved_rows(result: PanelResult) -> list[dict]:
    return [
        {name: getattr(match, name) for name in UNRESOLVED_COLUMNS}
        for match in result.footprint.unresolved()
    ]


def coverage_rows(result: PanelResult) -> list[dict]:
    rows = []
    for market in result.markets:
        for period in result.periods:
            cov = result.inputs.coverage.get((market, period))
            rows.append({
                "market": market,
                "period": period,
                "coverage": cov.status if cov else "none",
                "days_covered": cov.days_covered if cov else 0,
                "days_expected": cov.days_expected if cov else 0,
                "raw_refs": ";".join(cov.raw_refs) if cov else "",
            })
    return rows


def _cited(result: PanelResult, stamp) -> dict:
    spec = result.registry.get(stamp.source)
    return {
        "source": stamp.source,
        "source_version": stamp.source_version,
        "retrieved_at": stamp.retrieved_at.isoformat(),
        "publishable": bool(spec and spec.publishable),
        "basis": spec.compliance.basis if spec else "unregistered source",
    }


def source_citations(result: PanelResult) -> dict:
    """What a brief cites: each table's stamps (its own, contributors, and the source of
    each per-column value) and each raw input's retrieval record."""
    tables = {}
    for table, stamp in result.inputs.stamps.items():
        tables[table] = {
            **_cited(result, stamp),
            "sha256": result.inputs.input_identity["tables"][table]["sha256"],
            "derived_from": list(result.inputs.derived_from[table]),
            "contributors": [_cited(result, c) for c in result.inputs.contributors[table]],
            "column_sources": {
                col: s.source for col, s in sorted(result.inputs.column_sources[table].items())
            },
        }
    raw = [
        {"key": key, "url": c["url"], "retrieved_at": c["retrieved_at"], "sha256": c["sha256"]}
        for key, c in sorted(result.inputs.raw.items())
    ]
    return {"tables": tables, "raw": raw}


def _emitted(result: PanelResult) -> list[Fact]:
    facts = [f for cell in result.cells for f in cell.values.values()]
    facts += [f for s in result.summaries for f in s.values.values()]
    facts += [
        result.inputs.coverage_facts[(market, period)]
        for market in result.markets for period in result.periods
        if (market, period) in result.inputs.coverage_facts
    ]
    return facts


def blocked_sources(result: PanelResult) -> list[str]:
    """Sources behind any emitted value, or any input table's stamps, that are not
    publishable."""
    inputs = result.inputs
    sources = {stamp.source for stamp in inputs.stamps.values()}
    sources |= {c.source for stamps in inputs.contributors.values() for c in stamps}
    sources |= {s.source for cols in inputs.column_sources.values() for s in cols.values()}
    for fact in _emitted(result):
        sources |= result.store.source_closure(fact.lineage_id)
    return sorted(
        source for source in sources
        if not (result.registry.get(source) and result.registry.get(source).publishable)
    )


def _write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, allow_nan=False) + "\n")


def _write_csv(path: Path, rows: list[dict], columns: tuple[str, ...]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        writer.writerows({k: "" if row[k] is None else row[k] for k in columns} for row in rows)


def deliver_panel(
    result: PanelResult, out_dir: str | Path, *, generated_at: datetime = DEFAULT_GENERATED_AT,
) -> PanelDelivery:
    blocked = blocked_sources(result)
    if not blocked:
        for fact in _emitted(result):  # the lineage gate stays the final authority
            assert_publishable(fact.lineage_id, result.store, result.registry, public_only=True)
        mode, public_only, suffix = "shareable", True, ""
    elif "synthetic_fixture" in blocked:
        mode, public_only, suffix = "internal-synthetic", False, ".INTERNAL.synthetic"
    else:
        mode, public_only, suffix = "internal", False, ".INTERNAL"

    monthly = monthly_rows(result)
    summary = summary_rows(result)
    unresolved = unresolved_rows(result)
    coverage = coverage_rows(result)
    content = {
        "window": {"start": result.window[0], "end": result.window[1]},
        "markets": list(result.markets),
        "summary": summary,
        "monthly": monthly,
        "coverage": coverage,
        "unresolved": unresolved,
    }
    digest = hashlib.sha256(canonical_json(content).encode("utf-8")).hexdigest()

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    panel_json = out / f"panel{suffix}.json"
    _write_json(panel_json, {
        "kind": "constraint_panel",
        "mode": mode,
        "region": result.region.name,
        "rto": result.region.rto,
        "footprint": {
            "name": result.footprint.name,
            "sha256": result.footprint.sha256,
            "verified_constraints": len(result.footprint.verified()),
            "unresolved_rows": len(unresolved),
        },
        **content,
        "sources": source_citations(result),
        "caveats": list(CAVEATS),
    })
    paths = {"panel_json": str(panel_json)}
    for key, rows, columns in (
        ("summary_csv", summary, SUMMARY_COLUMNS),
        ("monthly_csv", monthly, MONTHLY_COLUMNS),
        ("coverage_csv", coverage, COVERAGE_COLUMNS),
        ("unresolved_csv", unresolved, UNRESOLVED_COLUMNS),
    ):
        path = out / f"panel_{key.removesuffix('_csv')}{suffix}.csv"
        _write_csv(path, rows, columns)
        paths[key] = str(path)

    manifest = build_run_manifest(
        result.registry,
        run_label=f"{result.region.name}-panel-{mode}",
        generated_at=generated_at,
        public_only=public_only,
        region=result.region.name,
        mode=mode,
        inputs=result.input_identity,
        runtime=result.runtime,
        output_sha256=digest,
    )
    manifest["kind"] = "constraint_panel"
    manifest["blocked_sources"] = blocked
    manifest_path = out / f"panel_manifest{suffix}.json"
    _write_json(manifest_path, manifest)
    paths["manifest"] = str(manifest_path)
    return PanelDelivery(
        mode=mode, public_only=public_only, blocked_sources=blocked,
        paths=paths, manifest=manifest,
    )
