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

SUBSET = Path(__file__).parent / "fixtures" / "real_samples" / "spp_snapshot" / "2026-10-13"
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
    # ranked by EV rank stability, then EV
    keys = [(s.p_top_k, s.ev.expected if s.ev else float("-inf")) for s in scores]
    assert keys == sorted(keys, reverse=True)
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
