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

SUBSET = Path(__file__).parent / "fixtures" / "real_samples" / "spp_snapshot" / "2026-10-13.r2"
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
        data = s.provenance.split("data=")[1].split(";")[0].split(",")
        assert "spp_itp" not in data and "absent=" in s.provenance  # ITP gave nothing
        if s.constraint_id in rent_rows:
            # observed rent cites only the congestion-by-constraint Fact, under its stamp
            rent = result.bucket_b.congestion[s.constraint_id].rent_musd
            assert result.store.source_closure(rent.lineage_id) == {"spp_congestion_by_constraint"}
            assert rent.inputs == [f"obs:congestion:{s.constraint_id}:rent_musd"]
            assert "rent=spp_congestion_by_constraint" in s.provenance

    d = deliver(result, tmp_path / "out")
    assert d.mode == "shareable" and d.public_only and d.blocked == []
    assert (tmp_path / "out" / "shortlist.csv").exists()
    qr = json.loads(Path(d.paths["quality_report"]).read_text())
    sources = {a["source"]: a for a in qr["attribution"]}
    assert {"spp_congestion_by_constraint", "spp_binding_constraints", "hifld_transmission",
            "spp_tcr_funding", "eia_860_861"} <= set(sources)  # contributors carry terms too
    assert "CC-BY" in sources["eia_860_861"]["basis"]  # via PUDL's redistribution
    consulted = {a["source"] for a in qr["consulted_nothing_obtained"]}
    assert "spp_itp" in consulted and "spp_itp" not in sources  # planning is unobserved
    assert qr["tables"]["congestion"]["coverage"] == "partial"  # some flowgates lack $
    notice = Path(d.paths["notice"]).read_text()
    assert "Mode: **shareable**" in notice and "terms-conditions" in notice
    assert "spp_tcr_funding" in notice and "## Caveats" in notice
    assert d.manifest["outputs"]["notice"]["sha256"]
    assert d.manifest["attribution"] == qr["attribution"]
    assert all("terms-conditions" in a["basis"] for name, a in sources.items() if name.startswith("spp_"))
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


def _copy_with(tmp_path, edit) -> Path:
    snap = tmp_path / "2026-10-13.r2"
    shutil.copytree(SUBSET, snap)
    m = json.loads((snap / MANIFEST_NAME).read_text())
    edit(m)
    (snap / MANIFEST_NAME).write_text(json.dumps(m))
    return snap


def test_a_non_publishable_contributor_blocks_the_shareable_export(tmp_path, monkeypatch, scoring):
    def synthetic_contributor(m):
        m["tables"]["buses"]["contributors"][0].update(
            {"source": "synthetic_fixture", "source_version": "test-v1"})

    snap = _copy_with(tmp_path, synthetic_contributor)
    d = deliver(run_pipeline(_region(monkeypatch, snap), scoring=scoring), tmp_path / "out")
    assert d.mode == "internal-synthetic" and "buses:synthetic_fixture" in d.blocked_tables
    assert "not for sharing" in Path(d.paths["notice"]).read_text()

    # the CLI says so in its exit status: a live run that cannot be shared is a failure
    from headroom import cli

    monkeypatch.setattr(cli, "run_pipeline", lambda *a, **k: run_pipeline(
        _region(monkeypatch, snap), scoring=scoring))
    assert cli.main(["run", "--region", "spp-real-subset", "--out", str(tmp_path / "cli")]) == 2


def test_column_sources_must_be_table_stamps_and_loader_stamped(tmp_path, monkeypatch, scoring):
    def stray(m):  # a column source that is not among the table's stamps
        m["tables"]["congestion"]["column_sources"]["rent_musd"].update(
            {"source": "synthetic_fixture", "source_version": "test-v1"})

    with pytest.raises(ValueError, match="not the table's stamp or a contributor"):
        run_pipeline(_region(monkeypatch, _copy_with(tmp_path / "a", stray)), scoring=scoring)

    def unstamped(m):  # a column the loader would not stamp per column
        m["tables"]["queue"]["column_sources"] = {"mw": dict(m["tables"]["queue"]["contributors"][0])}

    with pytest.raises(ValueError, match="not supported"):
        run_pipeline(_region(monkeypatch, _copy_with(tmp_path / "b", unstamped)), scoring=scoring)


def test_a_non_publishable_leaf_anywhere_in_the_store_blocks_the_export(tmp_path, monkeypatch, scoring):
    """Even a value no score or table reaches (say, a future CEII-backed label)
    keeps the export internal, and so does an input id nothing resolves."""
    from datetime import datetime, timezone

    from headroom.provenance.derived import derived_range
    from headroom.provenance.real import SourceStamp, real_fact

    result = run_pipeline(_region(monkeypatch, SUBSET), scoring=scoring)
    result.store.add(real_fact(
        value=1.0, lineage_id="obs:side_channel", stamp=SourceStamp(
            "synthetic_fixture", "synthetic", datetime(2026, 10, 13, tzinfo=timezone.utc))))
    d = deliver(result, tmp_path / "a")
    assert d.mode == "internal-synthetic" and d.blocked == [] and d.blocked_tables == []
    assert d.blocked_sources == ["synthetic_fixture"]

    clean = run_pipeline(_region(monkeypatch, SUBSET), scoring=scoring)
    clean.store.add(derived_range(lo=0, expected=1, hi=2, basis="test", stage="test",
                                  lineage_id="derived:orphan", inputs=["obs:missing"]))
    d = deliver(clean, tmp_path / "b")
    assert d.mode == "internal-synthetic" and d.blocked_sources[0].startswith("unresolved inputs")


def test_stamps_must_match_todays_pins(tmp_path, monkeypatch, scoring):
    def stale(m):  # e.g. a snapshot built under an earlier DA-BC pin
        m["tables"]["constraints"]["source_version"] = "spp_da_bc_2025"

    with pytest.raises(ValueError, match="differ from the registry pins"):
        run_pipeline(_region(monkeypatch, _copy_with(tmp_path, stale)), scoring=scoring)


def test_a_stamped_derived_value_cannot_launder_its_source(tmp_path, monkeypatch, scoring):
    """A node carrying a registered non-publishable source and public inputs: the taint
    walk follows inputs, so the export checks such stamps itself."""
    from headroom.provenance.envelope import Range

    result = run_pipeline(_region(monkeypatch, SUBSET), scoring=scoring)
    public_leaf = next(n.lineage_id for n in result.store.leaves()
                       if n.source == "hifld_transmission")
    result.store.add(Range(lo=0, expected=1, hi=2, basis="test", source="synthetic_fixture",
                           provider="synthetic_fixture", source_version="synthetic",
                           lineage_id="laundered", inputs=[public_leaf]))
    d = deliver(result, tmp_path / "out")
    assert d.mode == "internal-synthetic" and "synthetic_fixture" in d.blocked_sources
