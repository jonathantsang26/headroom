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
SUBSET = REPO / "tests" / "fixtures" / "real_samples" / "spp_snapshot" / "2026-10-13"
LIVE = REPO / "data" / "raw" / "spp" / "2026-10-13"


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


@pytest.mark.skipif(not LIVE.exists(), reason="live 2026-10-13 snapshot not on this machine")
def test_committed_spp_references_match_the_live_snapshot():
    spec = importlib.util.spec_from_file_location("derive", REPO / "scripts" / "derive_scoring_refs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main([str(LIVE), "--check", str(REPO / "config" / "regions" / "spp.scoring.yaml")]) == 0
