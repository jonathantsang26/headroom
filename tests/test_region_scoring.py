"""Region scoring: references by a stated rule, and the guard tying them to the
congestion table they were derived from."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from headroom.ingest.region import REGIONS, RegionConfig
from headroom.model import ScoringConfig
from headroom.model.references import check_reference_rule, derive_references
from headroom.pipeline import run_pipeline

REPO = Path(__file__).resolve().parents[1]
SUBSET = REPO / "tests" / "fixtures" / "real_samples" / "spp_snapshot" / "2026-10-13.r2"
SPP_SCORING = REPO / "config" / "regions" / "spp.scoring.yaml"
LIVE = REPO / "data" / "raw" / "spp" / __import__("yaml").safe_load(SPP_SCORING.read_text())[
    "reference_rule"]["derived_from_snapshot"]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("no network"))


def _row(rent, max_month, hours):
    return {"rent_musd": rent, "max_month_cost_musd": max_month, "hours_binding": hours}


def test_rule_is_linear_p99_of_observed_values_to_four_figures():
    rows = [_row(str(i), str(i / 2), str(10 * i)) for i in range(1, 101)] + [_row("", "", "5")]
    d = derive_references(rows)
    rent = [float(i) for i in range(1, 101)]  # blank rent excluded
    assert d["references"]["rent_ref_musd"] == float(f"{np.percentile(rent, 99):.4g}")
    hours = [10.0 * i for i in range(1, 101)] + [5.0]  # every row has hours
    assert d["references"]["persist_ref_hours"] == float(f"{np.percentile(hours, 99):.4g}")
    coi = [i / 2 for i in range(1, 101)]
    assert d["references"]["coi_ref_musd"] == float(f"{np.percentile(coi, 99):.4g}")
    assert (d["n_rent"], d["n_hours"], d["n_coi"]) == (100, 101, 100)


def test_observed_zeros_count_and_an_empty_population_raises():
    zeros = [_row("1.0", "1.0", "3") for _ in range(10)] + [_row("4.0", "1.0", "3")]
    assert derive_references(zeros)["n_coi"] == 11  # one-month episodes are observed zeros
    with pytest.raises(ValueError, match="rent_ref_musd"):
        derive_references([_row("", "", "3")])


def test_guard_refuses_references_from_another_congestion_table(monkeypatch):
    region = RegionConfig("spp-guard-subset", "SPP", SUBSET, synthetic=False)
    monkeypatch.setitem(REGIONS, region.name, region)
    manifest = json.loads((SUBSET / "snapshot_manifest.json").read_text())
    sha = manifest["tables"]["congestion"]["sha256"]
    ok = replace(ScoringConfig.load(), draws=50, reference_rule={
        "derived_from_snapshot": "subset", "congestion_sha256": sha})
    run_pipeline(region.name, scoring=ok)  # matching table: runs
    wrong = replace(ok, reference_rule={"derived_from_snapshot": "other",
                                        "congestion_sha256": "0" * 64})
    with pytest.raises(ValueError, match="derive_scoring_refs"):
        run_pipeline(region.name, scoring=wrong)
    check_reference_rule(replace(ok, reference_rule=None), {})  # no rule: no guard
    with pytest.raises(ValueError, match="records no congestion_sha256"):
        check_reference_rule(replace(ok, reference_rule={"derived_from_snapshot": "x"}), {})


@pytest.mark.skipif(not LIVE.exists(), reason="the pinned live snapshot is not on this machine")
def test_committed_spp_references_match_the_live_snapshot():
    spec = importlib.util.spec_from_file_location("derive", REPO / "scripts" / "derive_scoring_refs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main([str(LIVE), "--check", str(SPP_SCORING)]) == 0


def test_guard_refuses_references_derived_under_another_definition(monkeypatch):
    region = RegionConfig("spp-guard-coi", "SPP", SUBSET, synthetic=False)
    monkeypatch.setitem(REGIONS, region.name, region)
    sha = json.loads((SUBSET / "snapshot_manifest.json").read_text())["tables"]["congestion"]["sha256"]
    rule = {"derived_from_snapshot": "subset", "congestion_sha256": sha,
            "coi_definition": "recurring_rent"}
    ok = replace(ScoringConfig.load(), draws=50, reference_rule=rule,
                 definitions={"cost_of_inaction": "recurring_rent"})
    run_pipeline(region.name, scoring=ok)
    with pytest.raises(ValueError, match="cost_of_inaction=recurring_rent"):
        run_pipeline(region.name, scoring=replace(ok, definitions={}))


def test_check_script_compares_the_definition(tmp_path):
    spec = importlib.util.spec_from_file_location("derive", REPO / "scripts" / "derive_scoring_refs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    got = mod.derive(str(SUBSET), "recurring_rent")
    cfg = {"definitions": {"cost_of_inaction": "recurring_rent"}, **got}
    f = tmp_path / "s.yaml"
    f.write_text(__import__("yaml").safe_dump(cfg))
    assert mod.main([str(SUBSET), "--check", str(f)]) == 0
    cfg["definitions"] = {}  # the pipeline would use rent_x_persistence
    f.write_text(__import__("yaml").safe_dump(cfg))
    assert mod.main([str(SUBSET), "--check", str(f)]) == 1


def test_every_region_scoring_file_loads_from_the_repo():
    """The shipped region defaults load (priors included), so `headroom run --region
    spp` cannot fail on a missing or invalid config file."""
    import subprocess

    for name, region in REGIONS.items():
        if region.scoring_path is None:
            continue
        cfg = ScoringConfig.load(region.scoring_path)
        if cfg.ranking == "ev":
            assert cfg.get_priors and cfg.get_priors["version"]
            assert not cfg.get_priors["version"].startswith("ev_priors_test")
    spp = ScoringConfig.load(REGIONS["spp"].scoring_path)
    assert spp.forward_rent["predictor"] == "months_binding_share"
    if (REPO / ".git").exists():
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "config/get_priors.yaml",
             "config/regions/spp.scoring.yaml"], cwd=REPO, capture_output=True)
        assert tracked.returncode == 0, tracked.stderr.decode()


@pytest.mark.skipif(not LIVE.exists(), reason="the pinned live snapshot is not on this machine")
def test_committed_forward_rent_matches_the_live_snapshot():
    spec = importlib.util.spec_from_file_location("bt", REPO / "scripts" / "backtest_forward_rent.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main([str(LIVE), "--check", str(SPP_SCORING)]) == 0
