"""End to end on a committed subset of the real 2026-10-13 SPP snapshot (real rows,
real stamps): a run clears the unchanged publishable gate and writes a shareable
export, its gaps are reported, and any synthetic-stamped table still blocks it."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from headroom.export import deliver
from headroom.ingest.region import REGIONS, RegionConfig
from headroom.ingest.snapshot import MANIFEST_NAME, Snapshot
from headroom.model import ScoringConfig
from headroom.pipeline import run_pipeline
from headroom.provenance.gate import assert_publishable
from headroom.provenance.reproduce import reproduce_from_manifest

SUBSET = Path(__file__).parent / "fixtures" / "real_samples" / "spp_snapshot" / "2026-10-13"
PUBLIC = {"hifld_transmission", "eia_860_861", "eia_930", "pudl_ferc_form1",
          "spp_binding_constraints", "spp_congestion_by_constraint", "spp_tcr_funding",
          "spp_itp", "spp_queue"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("no network"))


@pytest.fixture
def scoring():
    return ScoringConfig.load().__class__(**{**ScoringConfig.load().__dict__, "draws": 400})


def _region(monkeypatch, data_dir: Path) -> str:
    region = RegionConfig("spp-real-subset", "SPP", data_dir, synthetic=False)
    monkeypatch.setitem(REGIONS, region.name, region)
    return region.name


def test_subset_is_a_valid_stamped_snapshot():
    snap = Snapshot(SUBSET)
    snap.require_tables()
    assert {snap.stamp(t).source for t in snap.manifest["tables"]} <= PUBLIC
    assert snap.coverage("planning")["coverage"] == "unobserved"
    assert snap.coverage("eia_lines")["coverage"] == "unobserved"
    assert [c.source for c in snap.contributors("buses")] == ["eia_860_861"]
    rent = snap.column_sources("congestion")["rent_musd"]
    assert (rent.source, rent.source_version) == ("spp_congestion_by_constraint", "spp_cbc_2025")


def test_real_subset_clears_the_gate_and_reports_its_gaps(tmp_path, monkeypatch, scoring):
    result = run_pipeline(_region(monkeypatch, SUBSET), scoring=scoring)
    leaves = {n.source for n in result.store._nodes.values() if not n.inputs}
    assert leaves <= PUBLIC and "synthetic_fixture" not in leaves
    rent_rows = {cid for cid, co in result.bucket_b.congestion.items() if co.rent_musd}
    assert rent_rows and len(rent_rows) < len(result.model.scores)  # some unobserved
    for s in result.model.scores:
        assert_publishable(s.composite.lineage_id, result.store, result.registry)
        assert "synthetic_fixture" not in s.provenance
        want = "observed" if s.constraint_id in rent_rows else "unobserved"
        assert s.signal_coverage["rent"] == want  # no congestion $ row: unobserved, not 0
        assert s.signal_coverage["loading"] == "unobserved"  # SPP elements are not placeable
        assert s.signal_coverage["planning"] == "unobserved"
        if s.constraint_id in rent_rows:
            # observed rent cites the congestion-by-constraint Facts, under their stamp
            rent = result.bucket_b.congestion[s.constraint_id].rent_musd
            assert "spp_congestion_by_constraint" in result.store.source_closure(rent.lineage_id)

    d = deliver(result, tmp_path / "out")
    assert d.mode == "shareable" and d.public_only and d.blocked == []
    assert (tmp_path / "out" / "shortlist.csv").exists()
    qr = json.loads(Path(d.paths["quality_report"]).read_text())
    assert qr["dc_screen"]["status"].startswith("not_run")
    assert qr["unobserved_weight_all"] == pytest.approx(0.5)  # loading+demand+planning
    affected = {a["signal"]: a for a in qr["scores_affected"]}
    assert {"loading", "demand_pressure", "planning"} <= set(affected)
    assert affected["rent"]["unobserved"] == len(result.model.scores) - len(rent_rows)
    corridors = [c["physical_corridor"] for c in result.model.shortlist]
    assert len(corridors) == len(set(corridors))
    assert not any(c == "Multi-Element Constraint" for c in corridors)

    replay = reproduce_from_manifest(d.manifest, data_dir=SUBSET)
    assert [s.constraint_id for s in replay.model.scores] == [s.constraint_id for s in result.model.scores]


@pytest.mark.parametrize("table", ["congestion", "buses"])
def test_one_synthetic_stamped_table_blocks_the_shareable_export(tmp_path, monkeypatch, scoring, table):
    snap = tmp_path / "2026-10-13"
    shutil.copytree(SUBSET, snap)
    mpath = snap / MANIFEST_NAME
    manifest = json.loads(mpath.read_text())
    manifest["tables"][table].update(source="synthetic_fixture", source_version="synthetic")
    mpath.write_text(json.dumps(manifest))
    result = run_pipeline(_region(monkeypatch, snap), scoring=scoring)
    d = deliver(result, tmp_path / "out")
    assert d.mode == "internal-synthetic"
    assert table in d.blocked_tables  # even when no score depends on the table
    assert not (tmp_path / "out" / "shortlist.csv").exists()
