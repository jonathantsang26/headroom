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
    assert d.mode == "internal"
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


@pytest.mark.parametrize("facility, branch", [
    ("SPS:CARPENTR HITCH:345:3:12", True),       # two stations
    ("GRDA CSWS:CATSAGR5: 161 138 2", False),    # one station, transformer windings
    ("LN ATOKA - CENTRAL", True),
    ("GEN:UNIT1:13", False),
])
def test_branch_outage_decides_whether_n1_implies_a_parallel_path(facility, branch):
    from headroom.model.get_match import get_basis, recommend_get
    from headroom.provenance.envelope import Range
    from headroom.schema.entities import Constraint

    c = Constraint(constraint_id="x", monitored_line=None, contingency_line=None,
                   ctype="thermal", contingency_name="123", contingency_facility=facility)
    assert c.branch_outage is branch
    zero = Range(lo=0, expected=0, hi=0, basis="t", source="s", source_version="v", lineage_id="z")
    sig = {"loading": zero, "persistence": zero, "rent": zero}
    get = recommend_get(c, radial=False, signals=sig, loading_observed=False, breached_hours=0)
    assert get == ("Advanced Power Flow Control" if branch else "Dynamic Line Rating")
    basis = get_basis(c, radial=None, loading_observed=False, breached_hours=0, get=get)
    assert "treated meshed" not in basis
    assert ("branch outage" if branch else "not a branch") in basis


def _shadow_price_congestion(d: Path, *, with_rent_column: bool):
    """Rewrite the fixture congestion table in the live SPP shape: no monthly $,
    summed |shadow price| instead; optionally an observed-$ column, blank for one row."""
    rows = list(csv.DictReader((d / "congestion.csv").open()))
    fields = ["constraint_id", "monthly_cost_musd", "hours_binding",
              "shadow_price_sum_usd_per_mw"] + (["rent_musd"] if with_rent_column else [])
    out = []
    for i, r in enumerate(rows):
        row = {"constraint_id": r["constraint_id"], "monthly_cost_musd": "",
               "hours_binding": r["hours_binding"],
               "shadow_price_sum_usd_per_mw": f"{float(r['monthly_cost_musd']) * 1e5:.1f}"}
        if with_rent_column:
            row["rent_musd"] = "" if i == 0 else f"{float(r['monthly_cost_musd']) * 12:.3f}"
        out.append(row)
    with (d / "congestion.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(out)


def test_snapshot_without_observed_dollars_uses_the_stated_rating_band(tmp_path, monkeypatch):
    region = _snapshot(tmp_path, monkeypatch,
                       edit=lambda d: _shadow_price_congestion(d, with_rent_column=False))
    result = run_pipeline(region)
    for co in result.bucket_b.congestion.values():
        assert "unobserved rating" in co.rent_musd.basis and "100-1200 MW" in co.rent_musd.basis
    assert all(s.signal_coverage["rent"] == "observed" for s in result.model.scores)


def test_a_blank_observed_dollar_is_unobserved_never_the_band(tmp_path, monkeypatch):
    region = _snapshot(tmp_path, monkeypatch,
                       edit=lambda d: _shadow_price_congestion(d, with_rent_column=True))
    result = run_pipeline(region)
    first = next(iter(result.bucket_b.congestion))
    co = result.bucket_b.congestion[first]
    assert co.rent_musd is None
    score = {s.constraint_id: s for s in result.model.scores}[first]
    assert score.signal_coverage["rent"] == "unobserved"
    assert score.signal_coverage["cost_of_inaction"] == "unobserved"
    for cid, other in result.bucket_b.congestion.items():
        if cid != first:
            assert other.rent_musd is not None and "Observed DA congestion" in other.rent_musd.basis


def test_same_station_endpoints_get_a_caution():
    from headroom.model.get_match import _same_station_ends

    assert _same_station_ends("LN FT_CAL - FT_CAL")
    assert not _same_station_ends("LN OSAGE_OG - WEBBTAP4")
    assert not _same_station_ends("XFMR EDWV - EDWV") and not _same_station_ends(None)
