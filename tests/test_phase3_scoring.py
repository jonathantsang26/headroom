"""Phase 3: signals -> which-GET -> composite -> rank stability -> corridor shortlist."""

import textwrap

import pytest

from headroom.ingest import ingest_region
from headroom.model import ModelResult, ScoringConfig, run_model
from headroom.model.stability import top_k
from headroom.provenance.lineage import LineageStore
from headroom.quality import run_bucket_a
from headroom.reconstruct import run_bucket_b


def _model() -> tuple[LineageStore, ModelResult]:
    store = LineageStore()
    b = ingest_region("spp-synth", store)
    a = run_bucket_a(b, store)
    bb = run_bucket_b(b, a, store)
    return store, run_model(a, bb, store)


def test_every_score_is_well_formed():
    _, m = _model()
    assert m.n_constraints == 10
    for s in m.scores:
        assert 0.0 <= s.p_top_k <= 1.0
        assert s.composite.lo <= s.composite.expected <= s.composite.hi
        assert s.recommended_get in {
            "Dynamic Line Rating", "Topology Optimization",
            "Advanced Power Flow Control", "Combination",
        }
        assert s.provenance  # one-line trail present
        assert len(s.signals) == 6


def test_ranking_has_a_stable_leader_and_a_wobbling_boundary():
    _, m = _model()
    assert m.k == 2
    # scores are sorted by stability desc
    assert m.scores[0].p_top_k == 1.0
    # the shortlist boundary genuinely wobbles under uncertainty
    wobbling = [s for s in m.scores if 0.0 < s.p_top_k < 1.0]
    assert wobbling, "expected at least one constraint with non-trivial rank stability"


def test_composite_band_is_mc_consistent():
    _, m = _model()
    # band is p5..p95 of the same draws -> much tighter than a sum-of-lo/hi band
    for s in m.scores:
        assert s.composite.hi - s.composite.lo < 0.5


def test_get_match_signatures():
    _, m = _model()
    by_id = {s.constraint_id: s for s in m.scores}
    assert by_id["FLOWGATE_6"].recommended_get == "Dynamic Line Rating"  # radial thermal
    assert by_id["FLOWGATE_4"].recommended_get == "Topology Optimization"  # loopflow
    assert by_id["FLOWGATE_7"].recommended_get == "Topology Optimization"  # loopflow
    assert by_id["FLOWGATE_9"].recommended_get == "Advanced Power Flow Control"  # interface


def test_footprint_dedup_to_corridors():
    _, m = _model()
    # LINE_2 carries two flowgates (FLOWGATE_1, FLOWGATE_2) -> one corridor
    line2 = [r for r in m.shortlist if r["physical_corridor"] == "LINE_2"]
    assert len(line2) == 1
    assert line2[0]["n_flowgates"] == 2
    # dedup actually reduced the count
    assert len(m.shortlist) < len(m.scores)


def test_reproducible_under_seed():
    _, m1 = _model()
    _, m2 = _model()
    assert [s.p_top_k for s in m1.scores] == [s.p_top_k for s in m2.scores]


def test_top_k_floor_never_zero():
    assert top_k(5, 0.1) == 1  # would be round(0.5)=0 without the floor
    assert top_k(10, 0.25) == 2


def test_weights_must_sum_to_one(tmp_path):
    bad = tmp_path / "scoring.yaml"
    bad.write_text(textwrap.dedent("""
        weights: {rent: 0.5, loading: 0.9}
        references: {rent_ref_musd: 50, persist_ref_hours: 600, demand_ref_mw: 1000,
                     planning_cost_ref_musd: 200, coi_ref_musd: 2.5}
        stability: {draws: 10, top_fraction: 0.25, seed: 1}
    """))
    with pytest.raises(ValueError):
        ScoringConfig.load(bad)
