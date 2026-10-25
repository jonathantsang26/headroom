"""Ingest: read the (pinned, immutable) region data and envelope every value at the
boundary. For `spp-synth` the data is the committed fixtures; values become
synthetic-sourced `Fact`s tagged with the real provider they imitate. For a live
region every value carries the stamp of the snapshot table it came from."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from headroom.ingest.region import RegionConfig, get_region
from headroom.ingest.snapshot import REQUIRED_TABLES, Snapshot
from headroom.provenance.envelope import Fact
from headroom.provenance.lineage import LineageStore
from headroom.provenance.real import SourceStamp, real_fact
from headroom.provenance.synthetic import SYNTHETIC_RETRIEVED_AT, synthetic_fact
from headroom.schema.entities import Bus

# Imitated real-source names for triangulation (recorded in each value's provider).
PROVIDER_HIFLD = "hifld_transmission"
PROVIDER_EIA = "eia_860_861"
PROVIDER_FERC1 = "pudl_ferc_form1"

# The real source each fixture-format table imitates (provider of its table Fact).
TABLE_PROVIDER = {
    "buses": PROVIDER_HIFLD,
    "hifld_lines": PROVIDER_HIFLD,
    "eia_lines": PROVIDER_EIA,
    "ferc1_lines": PROVIDER_FERC1,
    "constraints": "spp_binding_constraints",
    "congestion": "spp_binding_constraints",
    "planning": "spp_itp",
    "queue": "spp_queue",
    "load_930": "eia_930",
}


def _parse_csv(content: bytes) -> list[dict]:
    """Parse the same immutable bytes used for the table's input digest."""
    with io.StringIO(content.decode("utf-8"), newline="") as fh:
        return list(csv.DictReader(fh))


def _input_identity(
    region: RegionConfig, root: Path, raw_tables: dict[str, tuple[Path, bytes]],
    *, snapshot: Snapshot | None = None,
) -> dict:
    identity = {
        "schema_version": 1,
        "kind": "synthetic" if region.synthetic else "snapshot",
        "region": region.name,
        "rto": region.rto,
        "tables": {
            table: {
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for table, (path, content) in raw_tables.items()
        },
    }
    if snapshot is not None:
        identity["snapshot_id"] = snapshot.root.name
        identity["snapshot_manifest_sha256"] = snapshot.manifest_sha256
    return identity


def _require_expected_inputs(identity: dict, expected_inputs: dict | None) -> None:
    if expected_inputs is None:
        return
    if not isinstance(expected_inputs, dict):
        raise ValueError("Expected input identity must be a dictionary of pinned inputs")
    try:
        expected_json = json.dumps(expected_inputs, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as e:
        raise ValueError("Expected input identity contains invalid pins") from e
    # Canonical JSON comparison also distinguishes schema version 1 from True
    # or 1.0, which ordinary Python dictionary equality would treat as equal.
    if expected_json != json.dumps(identity, sort_keys=True, allow_nan=False):
        expected_tables = expected_inputs.get("tables")
        changed = (
            [table for table in REQUIRED_TABLES
             if expected_tables.get(table) != identity["tables"][table]]
            if isinstance(expected_tables, dict) else list(REQUIRED_TABLES)
        )
        detail = f"tables {', '.join(changed)}" if changed else "snapshot or region metadata"
        raise ValueError(f"Input identity mismatch: changed inputs or missing pins ({detail})")


@dataclass
class IngestBundle:
    region: RegionConfig
    buses: list[Bus]
    line_skeleton: dict[str, dict]  # line_id -> {from_bus, to_bus, length_mi}
    voltage_obs: dict[str, list[Fact]]  # line_id -> [hifld, eia, ferc1] voltage Facts
    conductor_obs: dict[str, Fact]
    thermal_obs: dict[str, Fact]
    cost_obs: dict[str, Fact]
    # Operational rows; each carries "_lineage": the Fact ids its values came from.
    constraints: list[dict] = field(default_factory=list)
    congestion: list[dict] = field(default_factory=list)
    planning: list[dict] = field(default_factory=list)
    queue: list[dict] = field(default_factory=list)
    load_930: list[dict] = field(default_factory=list)
    # Per-table provenance stamps (live regions only; empty for fixtures).
    table_stamps: dict[str, SourceStamp] = field(default_factory=dict)
    input_identity: dict = field(default_factory=dict)
    # The snapshot manifest's raw-input records (live regions only). Not part of the
    # input identity, which pins the manifest that holds them.
    raw_inputs: dict = field(default_factory=dict)
    # table -> Fact ids standing for "this whole table" (used by values derived
    # from a table's content or from a row's absence in it).
    table_lineage: dict[str, list[str]] = field(default_factory=dict)
    # table -> {"coverage": observed|partial|unobserved, "note": str}
    table_coverage: dict[str, dict] = field(default_factory=dict)
    # every source stamp a live table carries (own, contributors, column sources)
    table_sources: dict[str, list[SourceStamp]] = field(default_factory=dict)


def ingest_region(
    region_name: str, store: LineageStore, *, data_dir: Path | None = None,
    expected_inputs: dict | None = None,
) -> IngestBundle:
    region = get_region(region_name)
    if data_dir is not None:
        region = replace(region, data_dir=Path(data_dir))
    if region.synthetic:
        return _load_synthetic(region, store, expected_inputs=expected_inputs)
    return _load_live(region, store, expected_inputs=expected_inputs)


def _load_synthetic(
    region: RegionConfig, store: LineageStore, *, expected_inputs: dict | None = None,
) -> IngestBundle:
    """Fixture branch: every value becomes a synthetic-sourced Fact (imitated real
    source in `provider`) — structurally never publishable."""
    d = region.data_dir.resolve()
    raw_tables = {}
    for table in REQUIRED_TABLES:
        path = (d / f"{table}.csv").resolve()
        if not path.is_relative_to(d):
            raise ValueError(f"Synthetic table {table!r} path escapes the input directory")
        raw_tables[table] = (path, path.read_bytes())
    identity = _input_identity(region, d, raw_tables)
    _require_expected_inputs(identity, expected_inputs)
    tables = {table: _parse_csv(content) for table, (_, content) in raw_tables.items()}
    coverage = {t: {"coverage": "observed", "note": "synthetic fixture"} for t in REQUIRED_TABLES}
    return _envelope(
        region, store, tables, identity,
        stamps={t: None for t in REQUIRED_TABLES}, contributors={}, coverage=coverage,
    )


def _load_live(
    region: RegionConfig, store: LineageStore, *, expected_inputs: dict | None = None,
) -> IngestBundle:
    """Live branch: read a pinned snapshot's normalized tables and envelope every
    value with its TRUE source from the snapshot manifest. All required table
    stamps, paths and hashes are validated before any values enter lineage.
    Same shapes as the fixture branch (the fixture format IS the post-join format),
    so everything downstream is identical."""
    snap = Snapshot(region.data_dir)
    raw_tables = snap.read_tables()
    identity = _input_identity(region, snap.root, raw_tables, snapshot=snap)
    _require_expected_inputs(identity, expected_inputs)
    tables = {table: _parse_csv(content) for table, (_, content) in raw_tables.items()}
    stamps = {t: snap.stamp(t) for t in REQUIRED_TABLES}
    contributors = {t: snap.contributors(t) for t in REQUIRED_TABLES}
    column_sources = {t: snap.column_sources(t) for t in REQUIRED_TABLES}
    coverage = {t: snap.coverage(t) for t in REQUIRED_TABLES}
    bundle = _envelope(
        region, store, tables, identity,
        stamps=stamps, contributors=contributors, coverage=coverage,
        column_sources=column_sources,
    )
    bundle.table_stamps = stamps
    bundle.raw_inputs = snap.manifest.get("raw") or {}
    bundle.table_sources = {
        t: [stamps[t], *contributors[t], *column_sources[t].values()] for t in REQUIRED_TABLES
    }
    return bundle


# Columns whose row Facts take their own source from `column_sources`.
PER_COLUMN_STAMPED = {
    "congestion": {"monthly_cost_musd", "hours_binding", "shadow_price_sum_usd_per_mw",
                   "breached_hours", "months_binding", "rent_musd", "max_month_cost_musd"},
}


def _leaf(stamp: SourceStamp | None, *, value, provider: str, lineage_id: str,
          unit: str | None = None, as_reported: str | None = None) -> Fact:
    """A raw observation: real-stamped on a live run, synthetic on fixtures."""
    if stamp is None:
        return synthetic_fact(
            value=value, provider=provider, unit=unit, as_reported=as_reported,
            lineage_id=lineage_id,
        )
    return real_fact(
        value=value, stamp=stamp, provider=provider, unit=unit,
        as_reported=as_reported, lineage_id=lineage_id,
    )


def _text(row: dict, key: str) -> str:
    return (row.get(key) or "").strip()


def _unique(rows: list[dict], key, table: str) -> None:
    seen = set()
    for r in rows:
        k = key(r)
        if k in seen:
            raise ValueError(f"Table {table!r} has duplicate key {k!r}")
        seen.add(k)


def _envelope(
    region: RegionConfig,
    store: LineageStore,
    tables: dict[str, list[dict]],
    identity: dict,
    *,
    stamps: dict[str, SourceStamp | None],
    contributors: dict[str, list[SourceStamp]],
    coverage: dict[str, dict],
    column_sources: dict[str, dict[str, SourceStamp]] | None = None,
) -> IngestBundle:
    column_sources = column_sources or {}
    for t, cols in column_sources.items():
        unsupported = set(cols) - PER_COLUMN_STAMPED.get(t, set())
        if unsupported:  # fail closed: the loader would stamp them with the table's source
            raise ValueError(f"column_sources for {t}.{sorted(unsupported)} are not supported: "
                             "the loader stamps per column only " +
                             str(sorted(PER_COLUMN_STAMPED.get(t, set()))))
    # One Fact per table: its sha256, under the table's stamp (and one per
    # contributing source), so absence-derived values have lineage too.
    table_lineage: dict[str, list[str]] = {}
    for t in REQUIRED_TABLES:
        digest = identity["tables"][t]["sha256"]
        n = f"{len(tables[t])} rows"
        ids = [store.add(_leaf(
            stamps[t], value=digest, provider=TABLE_PROVIDER[t],
            lineage_id=f"table:{t}", as_reported=n,
        )).lineage_id]
        for c in contributors.get(t, []):
            ids.append(store.add(_leaf(
                c, value=digest, provider=c.source,
                lineage_id=f"table:{t}:{c.source}", as_reported=n,
            )).lineage_id)
        table_lineage[t] = ids

    buses = [
        Bus(
            bus_id=r["bus_id"],
            name=r["name"],
            ba=r["ba"],
            lat=float(r["lat"]),
            lon=float(r["lon"]),
            role=r["role"],
            weight=float(r["weight"]),
        )
        for r in tables["buses"]
    ]

    s_h, s_e, s_f = stamps["hifld_lines"], stamps["eia_lines"], stamps["ferc1_lines"]
    hifld = {r["line_id"]: r for r in tables["hifld_lines"]}
    eia = {r["line_id"]: r for r in tables["eia_lines"] if _text(r, "line_id")}
    ferc1 = {r["line_id"]: r for r in tables["ferc1_lines"] if _text(r, "line_id")}

    line_skeleton: dict[str, dict] = {}
    voltage_obs: dict[str, list[Fact]] = {}
    conductor_obs: dict[str, Fact] = {}
    thermal_obs: dict[str, Fact] = {}
    cost_obs: dict[str, Fact] = {}

    for line_id, h in hifld.items():
        line_skeleton[line_id] = {
            "from_bus": h["from_bus"],
            "to_bus": h["to_bus"],
            "length_mi": float(h["length_mi"]),
        }
        # voltage observed independently by up to three providers
        obs: list[Fact] = [store.add(_leaf(
            s_h, value=float(h["voltage_kv"]), provider=PROVIDER_HIFLD, unit="kV",
            as_reported=h["voltage_kv"], lineage_id=f"obs:{PROVIDER_HIFLD}:{line_id}:voltage",
        ))]
        if line_id in eia and _text(eia[line_id], "voltage_kv"):
            obs.append(store.add(_leaf(
                s_e, value=float(eia[line_id]["voltage_kv"]), provider=PROVIDER_EIA,
                unit="kV", as_reported=eia[line_id]["voltage_kv"],
                lineage_id=f"obs:{PROVIDER_EIA}:{line_id}:voltage",
            )))
        if line_id in ferc1:
            f = ferc1[line_id]
            if _text(f, "voltage_kv"):
                obs.append(store.add(_leaf(
                    s_f, value=float(f["voltage_kv"]), provider=PROVIDER_FERC1,
                    unit="kV", as_reported=f["voltage_kv"],
                    lineage_id=f"obs:{PROVIDER_FERC1}:{line_id}:voltage",
                )))
            # FERC Form 1 reports no thermal rating; a live table leaves it blank.
            if _text(f, "conductor"):
                conductor_obs[line_id] = store.add(_leaf(
                    s_f, value=f["conductor"], provider=PROVIDER_FERC1,
                    as_reported=f["conductor"],
                    lineage_id=f"obs:{PROVIDER_FERC1}:{line_id}:conductor",
                ))
            if _text(f, "thermal_limit_mw"):
                thermal_obs[line_id] = store.add(_leaf(
                    s_f, value=float(f["thermal_limit_mw"]), provider=PROVIDER_FERC1,
                    unit="MW", as_reported=f["thermal_limit_mw"],
                    lineage_id=f"obs:{PROVIDER_FERC1}:{line_id}:thermal",
                ))
            if _text(f, "cost_usd"):
                cost_obs[line_id] = store.add(_leaf(
                    s_f, value=float(f["cost_usd"]), provider=PROVIDER_FERC1,
                    unit="USD", as_reported=f["cost_usd"],
                    lineage_id=f"obs:{PROVIDER_FERC1}:{line_id}:cost",
                ))
        voltage_obs[line_id] = obs

    # Operational rows: one Fact per scored field, ids attached to the row.
    constraints = tables["constraints"]
    _unique(constraints, lambda r: r["constraint_id"], "constraints")
    for r in constraints:
        cid = r["constraint_id"]
        r["_lineage"] = [store.add(_leaf(
            stamps["constraints"], value=r["ctype"], provider=TABLE_PROVIDER["constraints"],
            as_reported=" | ".join(
                _text(r, k) for k in ("monitored_facility", "monitored_line",
                                      "contingency_facility", "contingency_line")
                if _text(r, k)
            ) or None,
            lineage_id=f"obs:constraints:{cid}",
        )).lineage_id]

    congestion = tables["congestion"]
    _unique(congestion, lambda r: r["constraint_id"], "congestion")
    for r in congestion:
        prefix = f"obs:congestion:{r['constraint_id']}"
        r["_lineage"] = _row_facts(
            store, stamps["congestion"], TABLE_PROVIDER["congestion"], r, prefix,
            ("monthly_cost_musd", "hours_binding", "shadow_price_sum_usd_per_mw",
             "breached_hours", "months_binding"),
            column_sources=column_sources.get("congestion", {}),
        )
        # observed $ (its own source when stamped by column) kept separately so a
        # rent value cites only the facts it was computed from
        cols = column_sources.get("congestion", {})
        r["_rent_lineage"] = _row_facts(
            store, stamps["congestion"], TABLE_PROVIDER["congestion"], r, prefix,
            ("rent_musd",), column_sources=cols,
        )
        r["_max_month_lineage"] = _row_facts(
            store, stamps["congestion"], TABLE_PROVIDER["congestion"], r, prefix,
            ("max_month_cost_musd",), column_sources=cols,
        )
        r["_rent_provider"] = (cols["rent_musd"].source if "rent_musd" in cols
                               else TABLE_PROVIDER["congestion"])

    planning = tables["planning"]
    for i, r in enumerate(planning):
        r["_lineage"] = _row_facts(
            store, stamps["planning"], TABLE_PROVIDER["planning"], r,
            f"obs:planning:{r['constraint_id']}:{i}", ("assigned_cost_musd",),
        )

    queue = tables["queue"]
    _unique(queue, lambda r: r["request_id"], "queue")
    for r in queue:
        r["_lineage"] = _row_facts(
            store, stamps["queue"], TABLE_PROVIDER["queue"], r,
            f"obs:queue:{r['request_id']}", ("mw",),
        )

    load_930 = tables["load_930"]
    _unique(load_930, lambda r: (r["snapshot"], r["ba"]), "load_930")
    for r in load_930:
        r["_lineage"] = _row_facts(
            store, stamps["load_930"], TABLE_PROVIDER["load_930"], r,
            f"obs:load_930:{r['snapshot']}:{r['ba']}", ("load_mw",),
        )

    return IngestBundle(
        region=region,
        buses=buses,
        line_skeleton=line_skeleton,
        voltage_obs=voltage_obs,
        conductor_obs=conductor_obs,
        thermal_obs=thermal_obs,
        cost_obs=cost_obs,
        constraints=constraints,
        congestion=congestion,
        planning=planning,
        queue=queue,
        load_930=load_930,
        input_identity=identity,
        table_lineage=table_lineage,
        table_coverage=coverage,
    )


def _row_facts(store: LineageStore, stamp: SourceStamp | None, provider: str,
               row: dict, prefix: str, fields: tuple[str, ...], *,
               column_sources: dict[str, SourceStamp] | None = None) -> list[str]:
    ids = []
    for name in fields:
        raw = _text(row, name)
        if not raw:
            continue
        col = (column_sources or {}).get(name)
        ids.append(store.add(_leaf(
            col or stamp, value=float(raw), provider=col.source if col else provider,
            as_reported=raw, lineage_id=f"{prefix}:{name}",
        )).lineage_id)
    return ids


def retrieved_at_of(facts: list[Fact]) -> datetime:
    """Latest retrieval time among observations (a derived value is as fresh as its
    freshest input)."""
    return max((f.retrieved_at for f in facts), default=SYNTHETIC_RETRIEVED_AT)
