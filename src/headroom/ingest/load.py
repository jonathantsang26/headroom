"""Ingest: read the (pinned, immutable) region data and envelope every value at the
boundary. For `spp-synth` the data is the committed fixtures; values become
synthetic-sourced `Fact`s tagged with the real provider they imitate."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass, field, replace
from pathlib import Path

from headroom.ingest.region import RegionConfig, get_region
from headroom.ingest.snapshot import REQUIRED_TABLES, Snapshot
from headroom.provenance.envelope import Fact
from headroom.provenance.lineage import LineageStore
from headroom.provenance.real import SourceStamp, real_fact
from headroom.provenance.synthetic import synthetic_fact
from headroom.schema.entities import Bus

# Imitated real-source names for triangulation (recorded in each value's provider).
PROVIDER_HIFLD = "hifld_transmission"
PROVIDER_EIA = "eia_860_861"
PROVIDER_FERC1 = "pudl_ferc_form1"


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
    constraints: list[dict] = field(default_factory=list)
    congestion: list[dict] = field(default_factory=list)
    planning: list[dict] = field(default_factory=list)
    queue: list[dict] = field(default_factory=list)
    load_930: list[dict] = field(default_factory=list)
    # Per-table provenance stamps (live regions only; empty for fixtures).
    table_stamps: dict[str, SourceStamp] = field(default_factory=dict)
    input_identity: dict = field(default_factory=dict)


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

    hifld = {r["line_id"]: r for r in tables["hifld_lines"]}
    eia = {r["line_id"]: r for r in tables["eia_lines"]}
    ferc1 = {r["line_id"]: r for r in tables["ferc1_lines"]}

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
        obs: list[Fact] = []
        # voltage observed independently by three providers
        obs.append(
            store.add(
                synthetic_fact(
                    value=float(h["voltage_kv"]),
                    provider=PROVIDER_HIFLD,
                    unit="kV",
                    as_reported=h["voltage_kv"],
                    lineage_id=f"obs:{PROVIDER_HIFLD}:{line_id}:voltage",
                )
            )
        )
        if line_id in eia:
            obs.append(
                store.add(
                    synthetic_fact(
                        value=float(eia[line_id]["voltage_kv"]),
                        provider=PROVIDER_EIA,
                        unit="kV",
                        as_reported=eia[line_id]["voltage_kv"],
                        lineage_id=f"obs:{PROVIDER_EIA}:{line_id}:voltage",
                    )
                )
            )
        if line_id in ferc1:
            f = ferc1[line_id]
            obs.append(
                store.add(
                    synthetic_fact(
                        value=float(f["voltage_kv"]),
                        provider=PROVIDER_FERC1,
                        unit="kV",
                        as_reported=f["voltage_kv"],
                        lineage_id=f"obs:{PROVIDER_FERC1}:{line_id}:voltage",
                    )
                )
            )
            conductor_obs[line_id] = store.add(
                synthetic_fact(
                    value=f["conductor"],
                    provider=PROVIDER_FERC1,
                    as_reported=f["conductor"],
                    lineage_id=f"obs:{PROVIDER_FERC1}:{line_id}:conductor",
                )
            )
            thermal_obs[line_id] = store.add(
                synthetic_fact(
                    value=float(f["thermal_limit_mw"]),
                    provider=PROVIDER_FERC1,
                    unit="MW",
                    as_reported=f["thermal_limit_mw"],
                    lineage_id=f"obs:{PROVIDER_FERC1}:{line_id}:thermal",
                )
            )
            cost_obs[line_id] = store.add(
                synthetic_fact(
                    value=float(f["cost_usd"]),
                    provider=PROVIDER_FERC1,
                    unit="USD",
                    as_reported=f["cost_usd"],
                    lineage_id=f"obs:{PROVIDER_FERC1}:{line_id}:cost",
                )
            )
        voltage_obs[line_id] = obs

    return IngestBundle(
        region=region,
        buses=buses,
        line_skeleton=line_skeleton,
        voltage_obs=voltage_obs,
        conductor_obs=conductor_obs,
        thermal_obs=thermal_obs,
        cost_obs=cost_obs,
        constraints=tables["constraints"],
        congestion=tables["congestion"],
        planning=tables["planning"],
        queue=tables["queue"],
        load_930=tables["load_930"],
        input_identity=identity,
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
    s_h = stamps["hifld_lines"]
    s_e = stamps["eia_lines"]
    s_f = stamps["ferc1_lines"]
    s_b = stamps["buses"]

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
    _ = s_b  # buses are structural (geometry exemption); stamp kept for the record

    hifld = {r["line_id"]: r for r in tables["hifld_lines"]}
    eia = {r["line_id"]: r for r in tables["eia_lines"]}
    ferc1 = {r["line_id"]: r for r in tables["ferc1_lines"]}

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
        obs: list[Fact] = []
        obs.append(
            store.add(
                real_fact(
                    value=float(h["voltage_kv"]),
                    stamp=s_h,
                    provider=PROVIDER_HIFLD,
                    unit="kV",
                    as_reported=h["voltage_kv"],
                    lineage_id=f"obs:{PROVIDER_HIFLD}:{line_id}:voltage",
                )
            )
        )
        if line_id in eia:
            obs.append(
                store.add(
                    real_fact(
                        value=float(eia[line_id]["voltage_kv"]),
                        stamp=s_e,
                        provider=PROVIDER_EIA,
                        unit="kV",
                        as_reported=eia[line_id]["voltage_kv"],
                        lineage_id=f"obs:{PROVIDER_EIA}:{line_id}:voltage",
                    )
                )
            )
        if line_id in ferc1:
            f = ferc1[line_id]
            obs.append(
                store.add(
                    real_fact(
                        value=float(f["voltage_kv"]),
                        stamp=s_f,
                        provider=PROVIDER_FERC1,
                        unit="kV",
                        as_reported=f["voltage_kv"],
                        lineage_id=f"obs:{PROVIDER_FERC1}:{line_id}:voltage",
                    )
                )
            )
            conductor_obs[line_id] = store.add(
                real_fact(
                    value=f["conductor"],
                    stamp=s_f,
                    provider=PROVIDER_FERC1,
                    as_reported=f["conductor"],
                    lineage_id=f"obs:{PROVIDER_FERC1}:{line_id}:conductor",
                )
            )
            thermal_obs[line_id] = store.add(
                real_fact(
                    value=float(f["thermal_limit_mw"]),
                    stamp=s_f,
                    provider=PROVIDER_FERC1,
                    unit="MW",
                    as_reported=f["thermal_limit_mw"],
                    lineage_id=f"obs:{PROVIDER_FERC1}:{line_id}:thermal",
                )
            )
            cost_obs[line_id] = store.add(
                real_fact(
                    value=float(f["cost_usd"]),
                    stamp=s_f,
                    provider=PROVIDER_FERC1,
                    unit="USD",
                    as_reported=f["cost_usd"],
                    lineage_id=f"obs:{PROVIDER_FERC1}:{line_id}:cost",
                )
            )
        voltage_obs[line_id] = obs

    return IngestBundle(
        region=region,
        buses=buses,
        line_skeleton=line_skeleton,
        voltage_obs=voltage_obs,
        conductor_obs=conductor_obs,
        thermal_obs=thermal_obs,
        cost_obs=cost_obs,
        constraints=tables["constraints"],
        congestion=tables["congestion"],
        planning=tables["planning"],
        queue=tables["queue"],
        load_930=tables["load_930"],
        table_stamps=stamps,
        input_identity=identity,
    )
