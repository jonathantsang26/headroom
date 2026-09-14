"""Live-loader mechanics, offline: a tmp snapshot built from fixture CSVs plus a
manifest stamping real source names. Proves the live branch envelopes with TRUE
sources, honors the manifest's retrieved_at, and fails closed without stamps.
(Uses fixture DATA with real STAMPS — fine in a test that never exercises the
export gate; the loader is what's under test.)"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from headroom.ingest.load import _load_live
from headroom.ingest.region import RegionConfig
from headroom.provenance.lineage import LineageStore

FIXTURES = Path(__file__).parent / "fixtures" / "spp_synth"
TABLES = [
    "buses", "hifld_lines", "eia_lines", "ferc1_lines",
    "constraints", "congestion", "planning", "queue", "load_930",
]
STAMP_FOR = {
    "buses": "hifld_transmission",
    "hifld_lines": "hifld_transmission",
    "eia_lines": "eia_860_861",
    "ferc1_lines": "pudl_ferc_form1",
    "constraints": "spp_binding_constraints",
    "congestion": "spp_binding_constraints",
    "planning": "spp_itp",
    "queue": "spp_queue",
    "load_930": "eia_930",
}
RETRIEVED = "2026-07-01T12:00:00+00:00"


def _make_snapshot(tmp_path: Path) -> Path:
    snap = tmp_path / "2026-07-01"
    (snap / "normalized").mkdir(parents=True)
    tables = {}
    for t in TABLES:
        shutil.copy(FIXTURES / f"{t}.csv", snap / "normalized" / f"{t}.csv")
        tables[t] = {
            "source": STAMP_FOR[t],
            "source_version": "test_pin_v1",
            "retrieved_at": RETRIEVED,
        }
    (snap / "snapshot_manifest.json").write_text(
        json.dumps({"schema_version": 1, "snapshot_date": "2026-07-01",
                    "tables": tables, "raw": {}})
    )
    return snap


def _region(data_dir: Path) -> RegionConfig:
    return RegionConfig(name="spp-test", rto="SPP", data_dir=data_dir, synthetic=False)


def test_live_loader_stamps_true_sources(tmp_path):
    snap = _make_snapshot(tmp_path)
    store = LineageStore()
    bundle = _load_live(_region(snap), store)

    some_line = next(iter(bundle.voltage_obs))
    hifld_v = bundle.voltage_obs[some_line][0]
    assert hifld_v.source == "hifld_transmission"
    assert hifld_v.source_version == "test_pin_v1"
    assert hifld_v.retrieved_at == datetime.fromisoformat(RETRIEVED)

    a_thermal = next(iter(bundle.thermal_obs.values()))
    assert a_thermal.source == "pudl_ferc_form1"

    assert len(bundle.table_stamps) == len(TABLES)
    assert bundle.table_stamps["congestion"].source == "spp_binding_constraints"
    assert len(bundle.buses) > 0 and len(bundle.constraints) > 0


def test_live_loader_fails_closed_without_manifest(tmp_path):
    snap = tmp_path / "empty"
    (snap / "normalized").mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        _load_live(_region(snap), LineageStore())


def test_live_loader_fails_closed_on_unstamped_table(tmp_path):
    snap = _make_snapshot(tmp_path)
    m = json.loads((snap / "snapshot_manifest.json").read_text())
    del m["tables"]["ferc1_lines"]
    (snap / "snapshot_manifest.json").write_text(json.dumps(m))
    with pytest.raises(KeyError):
        _load_live(_region(snap), LineageStore())
