"""The GETs the evidence does not rule out (model/get_match.py get_set), whether it
resolves one, and where the set is shown as the recommendation: flowgate and corridor
rows, the quality report, NOTICE, the map layer and MCP."""

from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from headroom.app.map_data import GET_UNRESOLVED, corridor_detail, corridor_segments, get_color
from headroom.export import deliver
from headroom.ingest.region import REGIONS, RegionConfig
from headroom.mcp.server import top_flowgates_rows
from headroom.model import ScoringConfig
from headroom.model.ev import load_priors
from headroom.model.get_match import get_label, get_set, recommend_get
from headroom.pipeline import run_pipeline
from headroom.provenance.digests import ranking_digest
from headroom.schema.entities import Constraint

ROOT = Path(__file__).resolve().parents[1]
SUBSET = ROOT / "tests" / "fixtures" / "real_samples" / "spp_snapshot" / "2026-10-13.r2"
TEST_PRIORS = yaml.safe_load((ROOT / "tests" / "fixtures" / "ev_priors_test.yaml").read_text())
DLR, APFC, TO, C = ("Dynamic Line Rating", "Advanced Power Flow Control",
                    "Topology Optimization", "Combination")
ALL = [DLR, APFC, TO, C]
N1_BRANCH = {"contingency_name": "N1", "contingency_facility": "LN A - B"}


def _c(ctype="thermal", **kw) -> Constraint:
    return Constraint(constraint_id="F", monitored_line=None, contingency_line=None,
                      ctype=ctype, **kw)


def _rule(c, *, radial=None, observed=False, breached=None, persistence=0.1, loading=0.5):
    """The evidence rule's GET and the set, as run_model computes them."""
    signals = {"persistence": SimpleNamespace(expected=persistence),
               "loading": SimpleNamespace(expected=loading)}
    get = recommend_get(c, radial=bool(radial), signals=signals,
                        loading_observed=observed, breached_hours=breached)
    return get, get_set(c, radial=radial, loading_observed=observed, get=get)


@pytest.mark.parametrize("c, kw, want_get, want_set", [
    (_c(contingency_name="BASE"), {}, DLR, ALL),  # binds on its own rating
    (_c(**N1_BRANCH), {}, APFC, ALL),  # "the outaged branch shares a loop"
    (_c(contingency_name="N1", contingency_facility="UNIT 1"), {}, DLR, ALL),  # not a branch
    (_c(), {}, DLR, ALL),  # contingency unknown
    (_c(**N1_BRANCH), {"breached": 2, "persistence": 0.9}, C, ALL),  # breached, persistent
    (_c("transformer", **N1_BRANCH), {}, APFC, [APFC, TO]),  # DLR rates conductors only
    (_c("transformer", contingency_name="BASE"), {}, APFC, [APFC, TO]),
    (_c("interface", contingency_name="BASE"), {}, APFC, ALL),  # members not public
    (_c("loopflow"), {}, TO, ALL),
    (_c(contingency_name="BASE"), {"radial": True}, DLR, [DLR]),  # no parallel path
])
def test_without_topology_only_the_element_rules_a_get_out(c, kw, want_get, want_set):
    get, gets = _rule(c, **kw)
    assert get == want_get and gets == want_set
    assert get in gets  # the rule's GET is a tie-break among the open GETs


def test_the_rules_own_get_stays_in_the_set():
    # breached and persistent is checked before the transformer branch, so the rule
    # can pick Combination (which includes DLR) on a transformer; the set keeps it
    get, gets = _rule(_c("transformer", **N1_BRANCH), breached=2, persistence=0.9)
    assert get == C and gets == [APFC, TO, C]


@pytest.mark.parametrize("c, kw, want", [
    (_c(), {"radial": True}, DLR),
    (_c(), {"radial": False}, APFC),
    (_c(), {"radial": False, "loading": 1.2, "persistence": 0.9}, C),
    (_c("transformer"), {}, APFC),
    (_c("interface"), {}, APFC),
    (_c("loopflow"), {}, TO),
])
def test_with_topology_observed_the_rule_resolves_one_get(c, kw, want):
    assert _rule(c, observed=True, **kw) == (want, [want])


def test_the_shown_recommendation_names_every_open_get():
    assert get_label([APFC]) == APFC  # resolved: the GET itself
    assert get_label([APFC, TO]) == "APFC or TO"
    assert get_label(ALL) == "DLR, APFC, TO or Combination"


def test_synthetic_regions_where_the_dc_screen_sees_topology_stay_single(tmp_path):
    result = run_pipeline("spp-synth", scoring=replace(ScoringConfig.load(), draws=60))
    d = deliver(result, tmp_path)
    for r in json.loads(Path(d.paths["scores_json"]).read_text()):
        assert r["get_resolved"] is True
        assert r["get_shown"] == r["get_set"] == r["recommended_get"]
    qr = json.loads(Path(d.paths["quality_report"]).read_text())
    assert qr["get_set"]["n_unresolved"] == 0 and qr["get_set"]["sets"] == {}
    assert not any(g.startswith("get_set:") for g in qr["gaps"])
    for seg in corridor_segments(result):
        assert seg["get_resolved"] is True and seg["color"] == get_color(seg["recommended_get"])


@pytest.fixture
def subset(monkeypatch):
    region = RegionConfig("spp-get-set-subset", "SPP", SUBSET, synthetic=False)
    monkeypatch.setitem(REGIONS, region.name, region)
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("no network"))
    return region.name


def _ev(priors) -> ScoringConfig:
    return replace(ScoringConfig.load(), draws=200, ranking="ev", get_priors=priors)


def test_real_exports_show_the_set_and_flag_it_unresolved(subset, tmp_path):
    result = run_pipeline(subset, scoring=_ev(TEST_PRIORS))
    d = deliver(result, tmp_path)
    assert d.mode == "shareable"  # the set adds no lineage: the gate is unchanged

    with open(d.paths["csv"]) as f:
        header = next(csv.reader(f))
        f.seek(0)
        rows = {r["constraint_id"]: r for r in csv.DictReader(f)}
    i = header.index("recommended_get")
    assert header[i + 1:i + 4] == ["get_shown", "get_resolved", "get_set"]
    sets = result.model.extras["get_set"]
    for s in result.model.scores:
        r, gets = rows[s.constraint_id], sets[s.constraint_id]
        assert r["get_set"] == ";".join(gets) and len(gets) > 1  # topology unobserved
        assert r["get_resolved"] == "False" and r["get_shown"] == get_label(gets)
        assert r["recommended_get"] == s.get_rule and s.recommended_get in gets

    by_cid = {r["constraint_id"]: r for r in json.loads(Path(d.paths["scores_json"]).read_text())}
    assert all(r["get_resolved"] is False for r in by_cid.values())  # a JSON boolean
    for c in json.loads(Path(d.paths["shortlist_json"]).read_text()):
        rep = by_cid[c["representative_flowgate"]]
        assert [c[k] for k in ("get_shown", "get_resolved", "get_set")] == [
            rep[k] for k in ("get_shown", "get_resolved", "get_set")]

    qr = json.loads(Path(d.paths["quality_report"]).read_text())
    n = len(result.model.scores)
    assert qr["get_set"]["n_resolved"] == 0 and qr["get_set"]["n_unresolved"] == n
    assert sum(qr["get_set"]["sets"].values()) == n
    assert any(g.startswith(f"get_set: for {n} of {n} flowgates") for g in qr["gaps"])
    assert "get_set: for" in Path(d.paths["notice"]).read_text()

    top = top_flowgates_rows(d.paths["parquet"], limit=5)
    assert all(t["get_resolved"] is False and t["get_shown"] == by_cid[t["constraint_id"]]["get_shown"]
               for t in top)

    segs = corridor_segments(result)
    assert all(seg["get_resolved"] is False and seg["color"] == GET_UNRESOLVED for seg in segs)
    detail = corridor_detail(result, segs[0]["physical_corridor"])
    assert detail["get_shown"] == segs[0]["get_shown"] and detail["get_resolved"] is False


def test_the_set_leaves_the_ranking_digest_alone(subset):
    """Held in ModelResult.extras, not on Score or the model's corridor rows."""
    result = run_pipeline(subset, scoring=_ev(TEST_PRIORS))
    before = ranking_digest(result)
    result.model.extras["get_set"] = {cid: [DLR] for cid in result.model.extras["get_set"]}
    assert ranking_digest(result) == before
    assert not any(k in row for row in result.model.shortlist
                   for k in ("get_shown", "get_resolved", "get_set"))


def test_the_set_matches_the_ev_candidates_except_where_a_prior_is_zero(subset):
    """The set comes from the evidence, the EV layer's get_candidates from the priors.
    Under the cited priors they agree on every real flowgate. The test priors set DLR
    applicability on interfaces to 0, a judgement the evidence alone does not make."""
    cited = run_pipeline(subset, scoring=_ev(load_priors(ROOT / "config" / "get_priors.yaml")))
    sets = cited.model.extras["get_set"]
    assert all(sets[s.constraint_id] == s.get_candidates for s in cited.model.scores)
    assert cited.quality_report["get_set"]["n_equal_to_ev_candidates"] == len(cited.model.scores)

    test = run_pipeline(subset, scoring=_ev(TEST_PRIORS))
    sets = test.model.extras["get_set"]
    differ = {s.get_evidence for s in test.model.scores if sets[s.constraint_id] != s.get_candidates}
    assert differ == {"interface"}
    for s in test.model.scores:
        if s.get_evidence == "interface":
            assert s.get_candidates == [APFC, TO] and sets[s.constraint_id] == ALL
