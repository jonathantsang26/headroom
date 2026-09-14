"""Ingest: read the (pinned, immutable) region data and envelope every value at the
boundary. For `spp-synth` the data is the committed fixtures; values become
synthetic-sourced `Fact`s tagged with the real provider they imitate.

A live adapter (ingest/ferc_form1.py etc.) would replace `_load_csv` with a
RateLimitedClient fetch + raw snapshot, but emit the SAME shapes — so everything
downstream is identical whether data is fixture or live.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from headroom.ingest.region import RegionConfig, get_region
from headroom.ingest.snapshot import Snapshot
from headroom.provenance.envelope import Fact
from headroom.provenance.lineage import LineageStore
from headroom.provenance.real import SourceStamp, real_fact
from headroom.provenance.synthetic import synthetic_fact
from headroom.schema.entities import Bus

# Imitated real-source names for triangulation (recorded in each value's provider).
PROVIDER_HIFLD = "hifld_transmission"
PROVIDER_EIA = "eia_860_861"
PROVIDER_FERC1 = "pudl_ferc_form1"


def _load_csv(path: Path) -> list[dict]:
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


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
    # Per-table provenance stamps (live regions only; empty for fixtures). Prepares
    # L2: reconstruct will stamp its leaf Ranges from these instead of synthetic.
    table_stamps: dict[str, SourceStamp] = field(default_factory=dict)


def ingest_region(region_name: str, store: LineageStore) -> IngestBundle:
    region = get_region(region_name)
    if region.synthetic:
        return _load_synthetic(region, store)
    return _load_live(region, store)


def _load_synthetic(region: RegionConfig, store: LineageStore) -> IngestBundle:
    """Fixture branch: every value becomes a synthetic-sourced Fact (imitated real
    source in `provider`) — structurally never publishable."""
    d = region.data_dir

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
        for r in _load_csv(d / "buses.csv")
    ]

    hifld = {r["line_id"]: r for r in _load_csv(d / "hifld_lines.csv")}
    eia = {r["line_id"]: r for r in _load_csv(d / "eia_lines.csv")}
    ferc1 = {r["line_id"]: r for r in _load_csv(d / "ferc1_lines.csv")}

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
        constraints=_load_csv(d / "constraints.csv"),
        congestion=_load_csv(d / "congestion.csv"),
        planning=_load_csv(d / "planning.csv"),
        queue=_load_csv(d / "queue.csv"),
        load_930=_load_csv(d / "load_930.csv"),
    )


def _load_live(region: RegionConfig, store: LineageStore) -> IngestBundle:
    """Live branch: read a pinned snapshot's normalized tables and envelope every
    value with its TRUE source from the snapshot manifest. Fail-closed twice over:
    no manifest -> Snapshot() raises; table without a stamp -> stamp() raises.
    Same shapes as the fixture branch (the fixture format IS the post-join format),
    so everything downstream is identical.

    L2 NOTE: the five market/planning tables pass through as dicts exactly like the
    fixture branch, and reconstruct still stamps its leaf Ranges synthetic — so a
    live run remains gate-blocked (INTERNAL export) until reconstruct threads
    `bundle.table_stamps`. The airlock opens in stages, failing closed throughout.
    """
    snap = Snapshot(region.data_dir)
    n = snap.normalized
    stamps = {t: snap.stamp(t) for t in snap.manifest["tables"]}
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
        for r in _load_csv(n / "buses.csv")
    ]
    _ = s_b  # buses are structural (geometry exemption); stamp kept for the record

    hifld = {r["line_id"]: r for r in _load_csv(n / "hifld_lines.csv")}
    eia = {r["line_id"]: r for r in _load_csv(n / "eia_lines.csv")}
    ferc1 = {r["line_id"]: r for r in _load_csv(n / "ferc1_lines.csv")}

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
        constraints=_load_csv(n / "constraints.csv"),
        congestion=_load_csv(n / "congestion.csv"),
        planning=_load_csv(n / "planning.csv"),
        queue=_load_csv(n / "queue.csv"),
        load_930=_load_csv(n / "load_930.csv"),
        table_stamps=stamps,
    )
