"""cost_of_inaction: recurring_rent on the committed real 2026-10-13 SPP subset. The
observed congestion $ outside a flowgate's largest month; binding hours do not enter
it, so they are counted twice (rent, persistence), not three times."""

from __future__ import annotations

import csv
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from headroom.export import deliver
from headroom.ingest.region import REGIONS, RegionConfig
from headroom.model import ScoringConfig
from headroom.pipeline import run_pipeline
from headroom.provenance.reproduce import reproduce_from_manifest

SUBSET = Path(__file__).parent / "fixtures" / "real_samples" / "spp_snapshot" / "2026-10-13.r2"
ROWS = {r["constraint_id"]: r for r in csv.DictReader(
    (SUBSET / "normalized" / "congestion.csv").open(newline=""))}
MULTI = "70BS12WAGS34|25296"
SINGLE = "8673A_LN@MIDLTNTP<3971>|3971"
UNJOINED = "BOIS_TAP_2240_A_LN|BASE"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("no network"))


@pytest.fixture
def region(monkeypatch):
    r = RegionConfig("spp-coi-subset", "SPP", SUBSET, synthetic=False)
    monkeypatch.setitem(REGIONS, r.name, r)
    return r.name


def _scoring():
    return replace(ScoringConfig.load(), draws=200,
                   definitions={"cost_of_inaction": "recurring_rent"})


def test_recurring_rent_is_annual_minus_the_largest_month(region):
    result = run_pipeline(region, scoring=_scoring())
    by_id = {s.constraint_id: s for s in result.model.scores}
    ref = result.scoring.references["coi_ref_musd"]

    row = ROWS[MULTI]
    want = float(row["rent_musd"]) - float(row["max_month_cost_musd"])
    sig = by_id[MULTI].signals["cost_of_inaction"]
    assert want > 0 and sig.expected == pytest.approx(want / ref)
    assert by_id[MULTI].signal_coverage["cost_of_inaction"] == "observed"
    closure = result.store.source_closure(sig.lineage_id)
    assert closure == {"spp_congestion_by_constraint"}  # hours do not enter it

    one = by_id[SINGLE].signals["cost_of_inaction"]  # all of its $ fell in one month
    assert (one.lo, one.expected, one.hi) == (0.0, 0.0, 0.0)
    assert by_id[SINGLE].signal_coverage["cost_of_inaction"] == "observed"

    assert by_id[UNJOINED].signal_coverage["cost_of_inaction"] == "unobserved"


def test_default_definition_is_unchanged(region):
    plain = run_pipeline(region, scoring=replace(ScoringConfig.load(), draws=200))
    s = {x.constraint_id: x for x in plain.model.scores}[MULTI]
    co = plain.bucket_b.congestion[MULTI]
    want = co.rent_musd.expected * co.hours_binding.expected / 8760.0
    assert s.signals["cost_of_inaction"].expected == pytest.approx(
        want / plain.scoring.references["coi_ref_musd"])


def test_definitions_are_recorded_validated_and_replayed(region, tmp_path):
    d = deliver(run_pipeline(region, scoring=_scoring()), tmp_path / "out")
    assert d.manifest["scoring"]["definitions"] == {"cost_of_inaction": "recurring_rent"}
    reproduce_from_manifest(d.manifest, data_dir=SUBSET)
    bad = deepcopy(d.manifest)
    bad["scoring"]["definitions"] = {"cost_of_inaction": "x"}
    with pytest.raises(ValueError, match="scoring"):
        reproduce_from_manifest(bad, data_dir=SUBSET)
    with pytest.raises(ValueError, match="signal definition"):
        replace(ScoringConfig.load(), definitions={"rent": "anything"})
