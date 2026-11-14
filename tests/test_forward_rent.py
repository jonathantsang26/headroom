"""Forward rent for the EV layer (model/forward.py) and its backtest."""

import hashlib
import json
import shutil
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import yaml

from headroom.ingest.region import REGIONS, RegionConfig
from headroom.model import ScoringConfig
from headroom.model.ev import SINGLE_GETS
from headroom.model.forward import (backtest, cbc_raw_sha256, check_forward_rent_rule,
                                    predict, ratio_mean, ratio_quantiles, ratio_sampler,
                                    share, validate_forward)
from headroom.pipeline import run_pipeline

SUBSET = Path(__file__).parent / "fixtures" / "real_samples" / "spp_snapshot" / "2026-10-13.r2"
PRIORS = yaml.safe_load((Path(__file__).parent / "fixtures" / "ev_priors_test.yaml").read_text())
FLAT = {"predictor": "months_binding_share", "ratio_quantiles": {"0": 1.0, "1": 1.0}}


def test_spec_is_validated_and_sampled_by_interpolation():
    spec = {"predictor": "months_binding_share",
            "ratio_quantiles": {"0": 0.0, "0.5": 1.0, "1": 3.0}}
    validate_forward(spec)
    eps = ratio_sampler(spec)
    assert eps(np.array([0.0, 0.25, 0.5, 0.75, 1.0])).tolist() == [0.0, 0.5, 1.0, 2.0, 3.0]
    assert ratio_mean(spec) == pytest.approx(0.25 * 1 + 0.5 * 2.0)  # trapezoids: 0.25 + 1.0
    for bad in ({"predictor": "x", "ratio_quantiles": spec["ratio_quantiles"]},
                {"predictor": "annual", "ratio_quantiles": {"0.1": 0, "1": 1}},
                {"predictor": "annual", "ratio_quantiles": {"0": 2, "1": 1}}):
        with pytest.raises(ValueError):
            validate_forward(bad)
    assert share("months_binding_share", 3) == 0.25 and share("annual", 3) == 1.0


def test_backtest_prefers_persistent_binders_when_spikes_do_not_recur():
    rng = np.random.default_rng(0)
    steady = rng.uniform(1, 2, size=(40, 12))           # binds every month
    spikes = np.zeros((40, 12))
    spikes[np.arange(40), rng.integers(0, 12, 40)] = 15  # one big month, never again
    bt = backtest(np.vstack([steady, spikes]), top_fraction=0.25)
    assert bt["months_binding_share"]["mean_capture"] > bt["annual"]["mean_capture"]
    q = ratio_quantiles(bt["months_binding_share"]["ratios"])
    assert q["0"] <= q["0.5"] <= q["1"] and q["1"] == q["0.99"]
    assert predict("months_binding_share", np.array([[2.0, 0, 0, 0, 0, 0]])).tolist() == [2.0 / 6]


def test_ev_draws_use_forward_rent(monkeypatch):
    region = RegionConfig("spp-forward-subset", "SPP", SUBSET, synthetic=False)
    monkeypatch.setitem(REGIONS, region.name, region)
    p = deepcopy(PRIORS)
    p["haircut"] = {"lo": 1.0, "mode": 1.0, "hi": 1.0}
    for g in SINGLE_GETS:
        p["gets"][g]["annual_cost_musd"] = {"lo": 0.0, "mode": 0.0, "hi": 0.0}
        p["gets"][g]["applicability"] = {k: (1.0 if g == "Dynamic Line Rating" else 0.0)
                                         for k in p["gets"][g]["applicability"]}
    p["gets"]["Dynamic Line Rating"]["relief_share"] = {"lo": 0.1, "mode": 0.1, "hi": 0.1}
    sc = replace(ScoringConfig.load(), draws=200, top_fraction=0.25, ranking="ev",
                 get_priors=p, forward_rent=FLAT)
    result = run_pipeline(region.name, scoring=sc)
    co = result.bucket_b.congestion
    for s in result.model.scores:
        if s.ev_mean is None:
            continue
        o = co[s.constraint_id]
        want = 0.1 * o.rent_musd.expected * o.months_binding / 12
        assert s.ev_mean == pytest.approx(want) and s.ev.lo == pytest.approx(want)
        assert "forward rent" in s.ev.basis
    assert result.quality_report["ev"]["forward_rent"]["predictor"] == "months_binding_share"


def _cbc_raw(year: str = "2025") -> dict:
    return {f"raw:spp_cbc_{year}{m:02d}": {
        "path": f"raw/spp/spp_cbc_{year}{m:02d}.csv", "sha256": f"{m:064x}",
        "retrieved_at": "2026-10-13T08:38:09+00:00", "url": f"https://example.test/{m}",
    } for m in range(1, 13)}


def test_forward_rent_rule_ties_a_derived_spec_to_its_cbc_files():
    raw = _cbc_raw()
    # the digest backtest_forward_rent.py records: the monthly sha256s, in month order
    want = hashlib.sha256("".join(f"{m:064x}" for m in range(1, 13)).encode()).hexdigest()
    assert cbc_raw_sha256(raw) == want
    derived = {**FLAT, "derived_from_snapshot": "s", "cbc_raw_sha256": want}
    check_forward_rent_rule(derived, raw)
    check_forward_rent_rule(None, {})  # no calibration: nothing to tie
    check_forward_rent_rule(FLAT, {})  # hand-specified: no derivation to tie
    with pytest.raises(ValueError, match="backtest_forward_rent"):
        check_forward_rent_rule({**derived, "cbc_raw_sha256": "0" * 64}, raw)
    with pytest.raises(ValueError, match="records no cbc_raw_sha256"):
        check_forward_rent_rule({**FLAT, "derived_from_snapshot": "s"}, raw)
    del raw["raw:spp_cbc_202507"]
    with pytest.raises(ValueError, match="cannot be tied.*spp_cbc_202507"):
        check_forward_rent_rule(derived, raw)
    with pytest.raises(ValueError, match="exactly one year"):
        check_forward_rent_rule(derived, {**_cbc_raw(), **_cbc_raw("2024")})


def test_pipeline_refuses_a_forward_rent_derived_from_other_cbc_files(tmp_path, monkeypatch):
    snap = tmp_path / "snap"
    shutil.copytree(SUBSET, snap)
    manifest = json.loads((snap / "snapshot_manifest.json").read_text())
    manifest["raw"] = {**manifest.get("raw", {}), **_cbc_raw()}
    (snap / "snapshot_manifest.json").write_text(json.dumps(manifest))
    for name, root in (("spp-forward-cbc", snap), ("spp-forward-no-cbc", SUBSET)):
        monkeypatch.setitem(REGIONS, name, RegionConfig(name, "SPP", root, synthetic=False))
    derived = {**FLAT, "derived_from_snapshot": "snap", "cbc_raw_sha256": cbc_raw_sha256(_cbc_raw())}
    sc = replace(ScoringConfig.load(), draws=50, forward_rent=derived)

    run_pipeline("spp-forward-cbc", scoring=sc)  # the files it was derived from: runs
    with pytest.raises(ValueError, match="backtest_forward_rent"):
        run_pipeline("spp-forward-cbc",
                     scoring=replace(sc, forward_rent={**derived, "cbc_raw_sha256": "0" * 64}))
    with pytest.raises(ValueError, match="cannot be tied"):
        run_pipeline("spp-forward-no-cbc", scoring=sc)  # snapshot records no CBC files
