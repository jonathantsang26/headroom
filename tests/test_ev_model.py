"""The expected-value GET layer (model/ev.py) on the committed subset of the real
2026-10-13 SPP snapshot, with test-only priors (tests/fixtures/ev_priors_test.yaml)."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import yaml

from headroom.export import deliver
from headroom.ingest.region import REGIONS, RegionConfig
from headroom.model import ScoringConfig
from headroom.model.ev import NO_GET, SINGLE_GETS, evidence_class, priors_sha256, validate_priors
from headroom.pipeline import run_pipeline
from headroom.provenance.reproduce import reproduce_from_manifest

SUBSET = Path(__file__).parent / "fixtures" / "real_samples" / "spp_snapshot" / "2026-10-13.r2"
PRIORS = yaml.safe_load((Path(__file__).parent / "fixtures" / "ev_priors_test.yaml").read_text())


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("no network"))


@pytest.fixture
def region(monkeypatch):
    r = RegionConfig("spp-ev-subset", "SPP", SUBSET, synthetic=False)
    monkeypatch.setitem(REGIONS, r.name, r)
    return r.name


def _scoring(priors=PRIORS, ranking="ev", draws=400):
    return replace(ScoringConfig.load(), draws=draws, top_fraction=0.25,
                   ranking=ranking, get_priors=deepcopy(priors))


def test_priors_are_validated():
    validate_priors(PRIORS)
    bad = deepcopy(PRIORS)
    bad["gets"]["Dynamic Line Rating"]["relief_share"]["lo"] = 0.5  # lo > mode
    with pytest.raises(ValueError, match="lo <= mode <= hi"):
        validate_priors(bad)
    missing = deepcopy(PRIORS)
    del missing["gets"]["Topology Optimization"]["applicability"]["loopflow"]
    with pytest.raises(ValueError, match="applicability"):
        validate_priors(missing)
    with pytest.raises(ValueError, match="needs get_priors"):
        replace(ScoringConfig.load(), ranking="ev", get_priors=None)


def test_ev_ranks_the_real_subset_and_stays_shareable(region, tmp_path):
    result = run_pipeline(region, scoring=_scoring())
    scores = result.model.scores
    ranked = [s for s in scores if s.ev is not None]
    assert ranked, "subset flowgates with observed rent get an EV"
    for s in scores:
        assert 0.0 <= s.p_top_k <= 1.0
        if s.ev is None:
            continue
        assert 0.0 <= s.ev.lo <= s.ev.expected <= s.ev.hi
        assert sum(s.get_probabilities.values()) == pytest.approx(1.0)
        assert s.recommended_get in {*s.get_candidates, NO_GET}
        if s.get_evidence.startswith("transformer") or s.get_evidence == "interface":
            assert s.get_probabilities["Dynamic Line Rating"] == 0.0
            assert s.recommended_get != "Dynamic Line Rating"
        assert s.p_top_k_composite is not None and s.get_rule
        # lineage: observed facts only; priors are basis text, never a leaf
        closure = result.store.source_closure(s.ev.lineage_id)
        assert closure <= {"spp_binding_constraints", "spp_congestion_by_constraint"}
        assert "cited assumptions" in s.ev.basis
    # ranked by the mean best-GET value, then EV rank stability
    keys = [(s.ev_mean if s.ev_mean is not None else float("-inf"), s.p_top_k) for s in scores]
    assert keys == sorted(keys, reverse=True)
    # p_top_k is the EV layer's, not the composite's
    assert any(s.p_top_k != s.p_top_k_composite for s in ranked)
    rows = result.model.shortlist
    assert [r["ev_mean"] for r in rows if r.get("ev_mean") is not None] == sorted(
        (r["ev_mean"] for r in rows if r.get("ev_mean") is not None), reverse=True)
    for r in rows:  # the champion is the member with the highest mean value
        members = [s for s in scores if s.physical_corridor == r["physical_corridor"]]
        best = max(members, key=lambda s: s.ev_mean if s.ev_mean is not None else float("-inf"))
        assert r["representative_flowgate"] == best.constraint_id
    d = deliver(result, tmp_path / "out")
    assert d.mode == "shareable"
    assert d.manifest["scoring"]["ranking"] == "ev"
    assert d.manifest["scoring"]["get_priors_sha256"] == priors_sha256(PRIORS)
    qr = json.loads(Path(d.paths["quality_report"]).read_text())
    assert qr["ev"]["priors_version"] == "ev_priors_test"


def test_ev_is_relief_share_times_rent_when_everything_else_is_fixed(region):
    """With only DLR applicable everywhere (p=1), a fixed 10% relief share, no
    haircut and zero cost, EV draws are exactly 10% of the rent draws."""
    p = deepcopy(PRIORS)
    p["haircut"] = {"lo": 1.0, "mode": 1.0, "hi": 1.0}
    for g in SINGLE_GETS:
        p["gets"][g]["annual_cost_musd"] = {"lo": 0.0, "mode": 0.0, "hi": 0.0}
        p["gets"][g]["applicability"] = {k: (1.0 if g == "Dynamic Line Rating" else 0.0)
                                         for k in p["gets"][g]["applicability"]}
    p["gets"]["Dynamic Line Rating"]["relief_share"] = {"lo": 0.1, "mode": 0.1, "hi": 0.1}
    result = run_pipeline(region, scoring=_scoring(p, draws=4000))
    co = result.bucket_b.congestion
    for s in result.model.scores:
        if s.ev is None:
            continue
        rent = co[s.constraint_id].rent_musd
        if rent.hi > rent.lo:
            rng = np.random.default_rng(0)
            draws = rng.triangular(rent.lo, rent.expected, rent.hi, 200000)
            want = np.percentile(draws, 50) * 0.1
        else:  # a degenerate band: every draw is the point value
            want = rent.expected * 0.1
        assert s.ev.expected == pytest.approx(want, rel=0.05, abs=1e-12)
        assert 0.1 * rent.lo - 1e-9 <= s.ev.lo and s.ev.hi <= 0.1 * rent.hi + 1e-9
        # zero rent ties DLR with doing nothing; ties go to "no cost-effective GET"
        want_get = "Dynamic Line Rating" if rent.lo > 0 else NO_GET
        assert s.recommended_get == want_get


def test_cost_above_any_relief_means_no_get(region):
    p = deepcopy(PRIORS)
    for g in SINGLE_GETS:
        p["gets"][g]["annual_cost_musd"] = {"lo": 1e6, "mode": 1e6, "hi": 1e6}
    result = run_pipeline(region, scoring=_scoring(p))
    for s in result.model.scores:
        if s.ev is not None:
            assert s.recommended_get == NO_GET and s.ev.hi == 0.0
        # every draw's best is doing nothing (EV 0): zero ties never enter the top k
        assert s.p_top_k == 0.0


def test_composite_ranking_keeps_ev_as_columns_only(region):
    composite = run_pipeline(region, scoring=_scoring(ranking="composite"))
    plain = run_pipeline(region, scoring=replace(ScoringConfig.load(), draws=400, top_fraction=0.25))
    assert [s.constraint_id for s in composite.model.scores] == [s.constraint_id for s in plain.model.scores]
    assert [s.p_top_k for s in composite.model.scores] == [s.p_top_k for s in plain.model.scores]
    assert all(s.recommended_get == s.get_rule for s in composite.model.scores)


def test_ev_run_replays_and_refuses_tampered_priors(region, tmp_path):
    d = deliver(run_pipeline(region, scoring=_scoring()), tmp_path / "out")
    replay = reproduce_from_manifest(d.manifest, data_dir=SUBSET)
    assert replay.model.scores[0].constraint_id
    tampered = deepcopy(d.manifest)
    tampered["scoring"]["get_priors"]["haircut"]["mode"] = 0.8
    with pytest.raises(ValueError, match="GET priors"):
        reproduce_from_manifest(tampered, data_dir=SUBSET)


def test_evidence_classes():
    from headroom.schema.entities import Constraint

    line = lambda **kw: Constraint(constraint_id="x", monitored_line=None, contingency_line=None,
                                   ctype="thermal", **kw)
    assert evidence_class(line(contingency_name="BASE"), radial=None, months_binding=5) == "line_base"
    assert evidence_class(line(contingency_name="123"), radial=None, months_binding=5) == "line_n1"
    assert evidence_class(line(contingency_name="123"), radial=None, months_binding=1) == "line_n1_episodic"
    assert evidence_class(line(contingency_name="123"), radial=True, months_binding=1) == "line_radial"
    xf = Constraint(constraint_id="y", monitored_line=None, contingency_line=None,
                    ctype="transformer", contingency_name="BASE")
    assert evidence_class(xf, radial=None, months_binding=3) == "transformer_base"


def _fixed(x):
    return {"lo": x, "mode": x, "hi": x}


def _ev_direct(result, priors, *, seed=7, draws=2000, point_rent=False):
    """run_ev on a pipeline result's flowgates, optionally with every rent band
    collapsed to its expected value (so only the priors vary between draws)."""
    from types import SimpleNamespace

    from headroom.model.ev import run_ev

    bb = result.bucket_b
    congestion = bb.congestion
    if point_rent:
        congestion = {
            cid: co.model_copy(update={"rent_musd": co.rent_musd.model_copy(
                update={"lo": co.rent_musd.expected, "hi": co.rent_musd.expected})})
            if co.rent_musd is not None else co
            for cid, co in congestion.items()
        }
    radial = {c.constraint_id: None for c in bb.constraints}
    return run_ev(bb.constraints, SimpleNamespace(congestion=congestion), radial, priors,
                  result.store, draws=draws, top_fraction=0.25, seed=seed)


def _only(get, p_app, **fixed):
    p = deepcopy(PRIORS)
    p["haircut"] = _fixed(fixed.get("h", 1.0))
    for g in SINGLE_GETS:
        p["gets"][g]["annual_cost_musd"] = _fixed(0.0)
        p["gets"][g]["applicability"] = {k: (p_app if g == get else 0.0)
                                         for k in p["gets"][g]["applicability"]}
    return p


def test_priors_are_shared_across_flowgates_within_a_draw(region):
    """With point rents, zero cost and DLR always applicable, a draw scales every
    flowgate's value by the same relief share, so the top k is the same set in every
    draw: p_top_k is exactly 0 or 1."""
    result = run_pipeline(region, scoring=_scoring())
    p = _only("Dynamic Line Rating", 1.0)
    p["gets"]["Dynamic Line Rating"]["relief_share"] = {"lo": 0.01, "mode": 0.2, "hi": 0.6}
    res = _ev_direct(result, p, point_rent=True)
    assert set(res.p_top_k.values()) <= {0.0, 1.0}
    assert sum(res.p_top_k.values()) == res.k  # rents are distinct: no ties at the cut


def test_applicability_is_drawn_once_per_element(region):
    """Flowgates on one physical element share each draw's applicability, so with one
    applicability probability everywhere, every member of a corridor has the same
    share of draws in which no GET applies; different elements are drawn apart."""
    result = run_pipeline(region, scoring=_scoring())
    res = _ev_direct(result, _only("Dynamic Line Rating", 0.5), draws=4000)
    co = result.bucket_b.congestion
    by_corridor = {}
    for c in result.bucket_b.constraints:
        rent = co.get(c.constraint_id)
        if rent is not None and rent.rent_musd is not None and rent.rent_musd.lo > 0:
            by_corridor.setdefault(c.corridor, []).append(res.p_best[c.constraint_id][NO_GET])
    shared = [v for v in by_corridor.values() if len(v) > 1]
    assert shared, "the subset has corridors with several flowgates"
    assert all(len(set(v)) == 1 for v in shared)
    assert len({v[0] for v in by_corridor.values()}) > 1  # elements are independent


def test_haircut_cost_and_combination_enter_the_value(region):
    """DLR (u 0.1) and APFC (u 0.2) both always applicable, haircut 0.5, costs 0.01
    and 0.02: per draw the options are 0.05R-0.01, 0.10R-0.02 and the combination
    (1-0.9x0.8)x0.5 R - 0.03 = 0.14R-0.03. With point rents the best value is exact."""
    result = run_pipeline(region, scoring=_scoring())
    p = _only("Dynamic Line Rating", 1.0, h=0.5)
    p["gets"]["Advanced Power Flow Control"]["applicability"] = dict(
        p["gets"]["Dynamic Line Rating"]["applicability"])
    p["gets"]["Dynamic Line Rating"]["relief_share"] = _fixed(0.1)
    p["gets"]["Advanced Power Flow Control"]["relief_share"] = _fixed(0.2)
    p["gets"]["Dynamic Line Rating"]["annual_cost_musd"] = _fixed(0.01)
    p["gets"]["Advanced Power Flow Control"]["annual_cost_musd"] = _fixed(0.02)
    res = _ev_direct(result, p, point_rent=True, draws=200)
    co = result.bucket_b.congestion
    for cid, ev in res.ev.items():
        r = co[cid].rent_musd.expected
        options = {NO_GET: 0.0, "Dynamic Line Rating": 0.05 * r - 0.01,
                   "Advanced Power Flow Control": 0.10 * r - 0.02, "Combination": 0.14 * r - 0.03}
        want = max(options.values())
        assert ev.lo == pytest.approx(want) and ev.hi == pytest.approx(want)
        assert res.ev_mean[cid] == pytest.approx(want)
        if want > 0 and len({round(v, 12) for v in options.values()}) == 4:
            assert res.recommended[cid] == max(options, key=options.get)


def test_ev_order_does_not_depend_on_the_seed_at_the_top(region):
    """The leaders' order follows the mean value, not Monte Carlo noise in p_top_k."""
    result = run_pipeline(region, scoring=_scoring())
    tops = []
    for seed in (1, 2, 3):
        res = _ev_direct(result, PRIORS, seed=seed, draws=4000)
        tops.append(sorted(res.ev_mean, key=res.ev_mean.get, reverse=True)[:3])
    assert tops[0] == tops[1] == tops[2]


def test_priors_with_non_json_values_are_refused_at_load():
    import datetime

    bad = deepcopy(PRIORS)
    bad["accessed"] = datetime.date(2026, 10, 13)  # an unquoted YAML date
    with pytest.raises(ValueError, match="JSON values only"):
        validate_priors(bad)
