"""Lineage completeness and the coverage/unobserved path. Snapshots here hold the
fabricated fixture CSVs and keep the `synthetic_fixture` stamp, so every run stays
gate-blocked; what is under test is how missing data is represented."""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pytest

from headroom.export import deliver
from headroom.ingest.region import REGIONS, RegionConfig
from headroom.ingest.snapshot import MANIFEST_NAME, REQUIRED_TABLES, sha256_of
from headroom.model.signals import SIGNAL_NAMES
from headroom.pipeline import run_pipeline
from headroom.sources import load_registry

FIXTURES = Path(__file__).parent / "fixtures" / "spp_synth"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("no network"))


def _snapshot(tmp_path: Path, monkeypatch, *, coverage=None, edit=None) -> str:
    snap = tmp_path / "spp" / "2026-10-01"
    shutil.copytree(FIXTURES, snap / "normalized")
    if edit:
        edit(snap / "normalized")
    tables = {}
    for t in REQUIRED_TABLES:
        path = snap / "normalized" / f"{t}.csv"
        tables[t] = {
            "source": "synthetic_fixture", "source_version": "synthetic",
            "retrieved_at": "2026-10-01T00:00:00+00:00", "sha256": sha256_of(path),
            **(coverage or {}).get(t, {}),
        }
    (snap / MANIFEST_NAME).write_text(json.dumps(
        {"schema_version": 1, "snapshot_date": snap.name, "tables": tables, "raw": {}}))
    region = RegionConfig("spp-coverage-test", "SPP", snap, synthetic=False)
    monkeypatch.setitem(REGIONS, region.name, region)
    return region.name


def test_every_leaf_is_a_registered_source():
    """Derived values never terminate lineage: each node without inputs names a
    source in the registry, so the gate decides from real stamps only."""
    result = run_pipeline("spp-synth")
    registry = load_registry()
    leaves = {n.source for n in result.store._nodes.values() if not n.inputs}
    assert leaves == {"synthetic_fixture"}
    assert all(registry.get(s) for s in leaves)
    for s in result.model.scores:
        assert "data=synthetic_fixture" in s.provenance


def test_unobserved_table_makes_its_signal_unobserved_not_zero_evidence(tmp_path, monkeypatch):
    region = _snapshot(tmp_path, monkeypatch, coverage={
        "planning": {"coverage": "unobserved", "note": "ITP not machine-readable"},
    }, edit=lambda d: (d / "planning.csv").write_text(
        "constraint_id,plan,proposed_fix,assigned_cost_musd\n"))
    result = run_pipeline(region)

    for s in result.model.scores:
        sig = s.signals["planning"]
        assert (sig.lo, sig.expected, sig.hi) == (0.0, 0.0, 0.0)
        assert sig.basis.startswith("unobserved")
        assert s.signal_coverage["planning"] == "unobserved"
    qr = result.quality_report
    assert qr["tables"]["planning"]["coverage"] == "unobserved"
    (affected,) = [a for a in qr["scores_affected"] if a["signal"] == "planning"]
    assert affected["unobserved"] == len(result.model.scores)
    assert any("planning" in g for g in qr["gaps"])


def test_unplaced_monitored_element_is_unobserved_and_kept(tmp_path, monkeypatch):
    def blank_monitored(d: Path):
        rows = list(csv.DictReader((d / "constraints.csv").open()))
        for r in rows:
            if r["constraint_id"] == "FLOWGATE_3":
                r["monitored_line"] = ""
                r["monitored_facility"] = "LN RTO_A - RTO_B"
            else:
                r["monitored_facility"] = ""
        with (d / "constraints.csv").open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=[*rows[0].keys()])
            w.writeheader()
            w.writerows(rows)

    region = _snapshot(tmp_path, monkeypatch, edit=blank_monitored)
    result = run_pipeline(region)
    by_id = {s.constraint_id: s for s in result.model.scores}

    s3 = by_id["FLOWGATE_3"]
    assert s3.physical_corridor == "LN RTO_A - RTO_B"
    assert s3.signal_coverage["loading"] == "unobserved"
    assert s3.signal_coverage["demand_pressure"] == "unobserved"
    assert s3.signal_coverage["rent"] == "observed"
    assert result.bucket_b.screen_status["FLOWGATE_3"] == "unmapped_monitored"
    # no topology and no DA breach record: the rule says so instead of assuming heavy
    assert "heavy=unobserved (no DA breach record)" in s3.get_basis
    assert "contingency=N-1" in s3.get_basis
    assert all(by_id[c].signal_coverage["loading"] == "observed"
               for c in by_id if c != "FLOWGATE_3")
    assert result.quality_report["constraint_coverage"] == {"observed": 9, "unobserved": 1}


def test_coverage_is_exported_and_fabricated_inputs_stay_blocked(tmp_path, monkeypatch):
    region = _snapshot(tmp_path, monkeypatch, coverage={
        "queue": {"coverage": "unobserved", "note": "not obtained"},
    })
    d = deliver(run_pipeline(region), tmp_path / "out")
    assert d.mode == "internal-synthetic"
    assert d.manifest["coverage"]["tables"]["queue"] == "unobserved"
    qr = json.loads(Path(d.paths["quality_report"]).read_text())
    assert qr["signal_coverage"]["demand_pressure"] == {"unobserved": 10}
    rows = list(csv.DictReader(Path(d.paths["csv"]).open()))
    assert {r["cov_demand_pressure"] for r in rows} == {"unobserved"}
    assert set(SIGNAL_NAMES) <= {k[4:] for k in rows[0] if k.startswith("cov_")}


def test_invalid_coverage_value_fails_before_lineage(tmp_path, monkeypatch):
    region = _snapshot(tmp_path, monkeypatch, coverage={"queue": {"coverage": "zero"}})
    with pytest.raises(ValueError, match="coverage"):
        run_pipeline(region)


def test_missing_rating_is_estimated_imputed_and_traced_to_voltage(tmp_path, monkeypatch):
    """A placeable monitored line with no public rating gets a typical-rating
    estimate: flagged imputed, low confidence, derived from the line's voltage."""
    def blank_rating(d: Path):
        rows = list(csv.DictReader((d / "ferc1_lines.csv").open()))
        for r in rows:
            if r["line_id"] == "LINE_5":
                r["thermal_limit_mw"] = ""
        with (d / "ferc1_lines.csv").open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=[*rows[0].keys()])
            w.writeheader()
            w.writerows(rows)

    result = run_pipeline(_snapshot(tmp_path, monkeypatch, edit=blank_rating))
    est = result.store.get("thermal_est:LINE_5")
    assert est is not None and "imputed" in est.flags and est.confidence == "low"
    assert est.value == 1200.0  # typical 345 kV rating
    line = result.bucket_a.lines_by_id["LINE_5"]
    assert line.thermal_limit_mw is est
    assert est.inputs == [line.voltage_kv.lineage_id]
    assert result.store.source_closure(est.lineage_id) == {"synthetic_fixture"}
    # FLOWGATE_3 monitors LINE_5: screened against the estimate, not "no rating"
    assert result.bucket_b.screen_status["FLOWGATE_3"] != "no_thermal_limit"
    assert est.lineage_id in result.store.get("loading:FLOWGATE_3").inputs
