"""Load a pinned panel snapshot and envelope each observation with its true source."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from headroom.ingest.snapshot import Snapshot
from headroom.panel.tables import (
    COVERAGE_COLUMNS,
    HISTORY_COLUMNS,
    OPTIONAL_VALUES,
    PANEL_TABLES,
    CoverageRow,
    HistoryRow,
    check_column_sources,
    check_consistency,
    parse_coverage,
    parse_history,
    read_csv,
)
from headroom.provenance.envelope import Fact
from headroom.provenance.lineage import LineageStore
from headroom.provenance.real import SourceStamp, real_fact

_REPO_ROOT = Path(__file__).resolve().parents[3]
UNITS = {
    "hours_binding": "h",
    "shadow_price_max": "USD/MWh",
    "shadow_price_mean": "USD/MWh",
    "congestion_cost_usd": "USD",
}


@dataclass(frozen=True)
class PanelRegion:
    name: str
    rto: str
    data_dir: Path


# Only the fabricated fixture is registered. Register a live region only after its
# source's retrieval, retention, and commercial-report rights are documented.
PANEL_REGIONS: dict[str, PanelRegion] = {
    "spp-synth": PanelRegion(
        "spp-synth", "SPP", _REPO_ROOT / "tests" / "fixtures" / "spp_synth_panel",
    ),
}


def get_panel_region(name: str) -> PanelRegion:
    try:
        return PANEL_REGIONS[name]
    except KeyError:
        raise KeyError(f"Unknown panel region {name!r}. Known: {sorted(PANEL_REGIONS)}") from None


@dataclass
class PanelInputs:
    region: PanelRegion
    history: list[HistoryRow]
    coverage: dict[tuple[str, str], CoverageRow]
    stamps: dict[str, SourceStamp]
    contributors: dict[str, list[SourceStamp]]
    column_sources: dict[str, dict[str, SourceStamp]]
    derived_from: dict[str, tuple[str, ...]]
    raw: dict[str, dict]  # key -> verified {url, path, sha256, retrieved_at}
    history_facts: dict[tuple[str, str, str], dict[str, Fact]]
    coverage_facts: dict[tuple[str, str], Fact]
    input_identity: dict


def load_panel(region: PanelRegion, store: LineageStore) -> PanelInputs:
    """Validate stamps, table hashes, and raw citations before creating lineage."""
    snap = Snapshot(region.data_dir)
    raw_tables = snap.read_tables(PANEL_TABLES)
    stamps = {table: snap.stamp(table) for table in PANEL_TABLES}
    contributors = {table: snap.contributors(table) for table in PANEL_TABLES}
    column_sources = {table: snap.column_sources(table) for table in PANEL_TABLES}
    for table, cols in column_sources.items():
        check_column_sources(table, cols)
    derived = {table: snap.derived_from(table) for table in PANEL_TABLES}
    if not set(derived["constraint_history"]) <= set(derived["constraint_coverage"]):
        raise ValueError(
            "constraint_history cites raw inputs that constraint_coverage does not cover"
        )
    raw = {key: snap.verify_raw(key) for key in sorted(set().union(*derived.values()))}

    history = parse_history(
        read_csv(raw_tables["constraint_history"][1], "constraint_history", HISTORY_COLUMNS)
    )
    coverage = parse_coverage(
        read_csv(raw_tables["constraint_coverage"][1], "constraint_coverage", COVERAGE_COLUMNS)
    )
    cited = set(derived["constraint_coverage"])
    for cov in coverage.values():
        uncited = sorted(set(cov.raw_refs) - cited)
        if uncited:
            raise ValueError(
                f"constraint_coverage {cov.market} {cov.period} cites raw inputs missing "
                f"from its manifest entry: {uncited}"
            )
    check_consistency(history, coverage)

    identity = {
        "schema_version": 1,
        "kind": "panel_snapshot",
        "region": region.name,
        "rto": region.rto,
        "snapshot_id": snap.root.name,
        "snapshot_manifest_sha256": snap.manifest_sha256,
        "tables": {
            table: {
                "path": path.relative_to(snap.root).as_posix(),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for table, (path, content) in raw_tables.items()
        },
        "raw": {key: {"path": c["path"], "sha256": c["sha256"]} for key, c in raw.items()},
    }

    coverage_facts: dict[tuple[str, str], Fact] = {}
    for key, cov in sorted(coverage.items()):
        coverage_facts[key] = store.add(real_fact(
            value=float(cov.days_covered),
            stamp=stamps["constraint_coverage"],
            unit="days",
            confidence="high",
            as_reported=str(cov.days_covered),
            flags=[] if cov.status == "full" else [f"coverage_{cov.status}"],
            lineage_id=f"panel:coverage:{cov.market}:{cov.period}",
        ))

    history_facts: dict[tuple[str, str, str], dict[str, Fact]] = {}
    history_cols = column_sources["constraint_history"]
    for row in history:
        partial = coverage[(row.market, row.period)].status == "partial"
        base = f"panel:{row.market}:{row.period}:{row.constraint_id}"
        facts: dict[str, Fact] = {}
        for name in ("hours_binding", *OPTIONAL_VALUES):
            value = getattr(row, name)
            if value is None:
                continue
            facts[name] = store.add(real_fact(
                value=value,
                stamp=history_cols.get(name, stamps["constraint_history"]),
                unit=UNITS[name],
                confidence="medium" if partial else "high",
                as_reported=row.as_reported[name],
                flags=["partial_coverage"] if partial else [],
                lineage_id=f"{base}:{name}",
            ))
        history_facts[(row.constraint_id, row.market, row.period)] = facts

    return PanelInputs(
        region=region,
        history=history,
        coverage=coverage,
        stamps=stamps,
        contributors=contributors,
        column_sources=column_sources,
        derived_from=derived,
        raw=raw,
        history_facts=history_facts,
        coverage_facts=coverage_facts,
        input_identity=identity,
    )
