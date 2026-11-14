"""Constraint-history panel: cited monthly history, explicit gaps, and the gate.

The committed snapshot under fixtures/spp_synth_panel is fabricated and stamped
``synthetic_fixture``. Tests that exercise the shareable path restamp a copy with a
test-only registered source; no real market data is read or fetched.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from headroom.cli import main
from headroom.ingest.snapshot import MANIFEST_NAME, sha256_of
from headroom.panel import PanelRegion, build_panel
from headroom.panel.export import blocked_sources, deliver_panel, summary_rows
from headroom.provenance.lineage import UNKNOWN_PREFIX
from headroom.provenance.runtime import _SOURCE_DIRS
from headroom.sources import Compliance, SourceRegistry, SourceSpec, load_registry

FIXTURES = Path(__file__).parent / "fixtures"
SNAPSHOT = FIXTURES / "spp_synth_panel"
FOOTPRINT = FIXTURES / "panel_footprint.csv"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Panel tests must never fetch source data")

    monkeypatch.setattr("urllib.request.urlopen", forbidden)


@pytest.fixture(scope="module")
def panel():
    return build_panel("spp-synth", FOOTPRINT)


def _summary(result, cid, market):
    return next(s for s in result.summaries if (s.constraint_id, s.market) == (cid, market))


def _cell(result, cid, market, period):
    return next(
        c for c in result.cells if (c.constraint_id, c.market, c.period) == (cid, market, period)
    )


def _registry(**publishable: bool) -> SourceRegistry:
    specs = [spec.model_copy(deep=True) for spec in load_registry().all()]
    for name, flag in publishable.items():
        specs.append(SourceSpec(
            name=name, bucket="b", tier=1, source_version="test",
            compliance=Compliance(publishable=flag, basis="test-only source"),
        ))
    return SourceRegistry(specs)


def _restamped(tmp_path, **sources: str) -> PanelRegion:
    """Copy the fixture and restamp tables (fabricated values, test-only stamps)."""
    snap = tmp_path / "snapshot"
    shutil.copytree(SNAPSHOT, snap)
    manifest = json.loads((snap / MANIFEST_NAME).read_text())
    for table, source in sources.items():
        manifest["tables"][table]["source"] = source
    (snap / MANIFEST_NAME).write_text(json.dumps(manifest))
    return PanelRegion("panel-test", "SPP", snap)


def test_only_verified_constraints_enter_the_panel(panel):
    assert sorted({s.constraint_id for s in panel.summaries}) == [
        "FLOWGATE_1", "FLOWGATE_3", "FLOWGATE_4", "FLOWGATE_6", "FLOWGATE_9",
    ]
    assert panel.markets == ("DA", "RT")
    assert len(panel.cells) == 5 * 2 * 12
    unresolved = {(m.element_id, m.match_status) for m in panel.footprint.unresolved()}
    assert unresolved == {("SITE_A", "uncertain"), ("NODE_B", "unmatched")}
    assert all(s.constraint_id != "FLOWGATE_2" for s in panel.summaries)  # uncertain
    assert all(s.constraint_id != "FLOWGATE_5" for s in panel.summaries)  # outside footprint


def test_summary_aggregates_observed_months(panel):
    s = _summary(panel, "FLOWGATE_1", "DA")
    assert s.months == 12 and s.months_full == 10 and s.months_partial == 1
    assert s.months_not_covered == ["2025-11"]
    assert s.months_binding == 9
    assert (s.first_binding, s.last_binding) == ("2025-01", "2025-12")
    assert s.values["hours_binding"].value == pytest.approx(362.0)
    assert s.values["shadow_price_max"].value == pytest.approx(310.25)
    assert s.values["congestion_cost_usd"].value == pytest.approx(3_198_000)
    months = [(35.2, 42), (22.4, 18), (41.8, 61), (15.5, 12), (55.0, 77),
              (30.0, 35), (48.6, 88), (19.9, 20), (12.0, 9)]
    expected_mean = sum(m * h for m, h in months) / sum(h for _, h in months)
    assert s.values["shadow_price_mean"].value == pytest.approx(expected_mean)
    assert s.flags == ["partial_coverage", "months_not_covered"]
    assert s.confidence == "medium"
    assert s.reported_facilities == ["SYNTH_SUB_A - SYNTH_SUB_B 345KV / SYNTH_SUB_C - SYNTH_SUB_D 345KV"]


def test_covered_month_without_binding_is_a_cited_zero(panel):
    cell = _cell(panel, "FLOWGATE_1", "DA", "2025-04")
    assert cell.coverage == "full" and cell.binding is False
    zero = cell.values["hours_binding"]
    assert zero.value == 0.0 and zero.confidence == "high"
    assert zero.inputs == ["panel:coverage:DA:2025-04"]
    assert panel.store.source_closure(zero.lineage_id) == {"synthetic_fixture"}
    assert cell.raw_refs == ("raw:da_2025",)


@pytest.mark.parametrize("market,period", [("DA", "2025-11"), ("RT", "2025-12")])
def test_uncovered_month_is_unknown_not_zero(panel, market, period):
    # DA 2025-11 is an explicit zero-day coverage row; RT 2025-12 has no row at all.
    cell = _cell(panel, "FLOWGATE_1", market, period)
    assert cell.coverage == "none" and cell.binding is None
    assert cell.values == {}
    assert cell.missing == ["no source coverage for this month"]
    s = _summary(panel, "FLOWGATE_1", market)
    assert period in s.months_not_covered
    assert "months_not_covered" in s.flags


def test_partial_coverage_is_flagged_as_a_lower_bound(panel):
    binding = _cell(panel, "FLOWGATE_3", "DA", "2025-07")
    assert binding.coverage == "partial" and binding.binding is True
    assert binding.values["hours_binding"].flags == ["partial_coverage"]
    assert binding.values["hours_binding"].confidence == "medium"
    quiet = _cell(panel, "FLOWGATE_4", "DA", "2025-07")
    assert quiet.binding is False
    assert quiet.values["hours_binding"].flags == ["partial_coverage"]
    assert "partial_coverage" in _summary(panel, "FLOWGATE_3", "DA").flags


def test_verified_constraint_absent_from_source_is_low_confidence(panel):
    for market in ("DA", "RT"):
        s = _summary(panel, "FLOWGATE_9", market)
        assert s.seen_in_source is False
        assert "not_seen_in_source" in s.flags
        assert s.confidence == "low"
        assert s.months_binding == 0


def test_unpublished_values_are_reported_missing_not_zero(panel):
    rt = _summary(panel, "FLOWGATE_1", "RT")
    assert rt.values["hours_binding"].value == pytest.approx(283.5)
    assert "congestion_cost_usd" not in rt.values
    assert "congestion_cost_usd: not published for 4 of 4 binding months" in rt.missing
    cell = _cell(panel, "FLOWGATE_1", "RT", "2025-06")
    assert "congestion_cost_usd not published" in cell.missing


def test_summaries_sort_by_observed_hours_without_a_composite(panel):
    order = [(s.market, s.constraint_id) for s in panel.summaries]
    assert order == [
        ("DA", "FLOWGATE_1"), ("DA", "FLOWGATE_3"), ("DA", "FLOWGATE_4"),
        ("DA", "FLOWGATE_6"), ("DA", "FLOWGATE_9"),
        ("RT", "FLOWGATE_1"), ("RT", "FLOWGATE_4"), ("RT", "FLOWGATE_6"),
        ("RT", "FLOWGATE_3"), ("RT", "FLOWGATE_9"),
    ]
    columns = set(summary_rows(panel)[0])
    assert not columns & {"composite", "score", "rank", "p_top_k"}


def test_every_emitted_value_traces_to_stamped_tables(panel):
    facts = [f for c in panel.cells for f in c.values.values()]
    facts += [f for s in panel.summaries for f in s.values.values()]
    assert facts
    for fact in facts:
        closure = panel.store.source_closure(fact.lineage_id)
        assert closure == {"synthetic_fixture"}
        assert not any(source.startswith(UNKNOWN_PREFIX) for source in closure)
    for s in panel.summaries:
        for fact in s.values.values():
            assert fact.inputs and fact.source == "panel.aggregate"


def test_synthetic_panel_is_refused_shareable(panel, tmp_path):
    d = deliver_panel(panel, tmp_path)
    assert d.mode == "internal-synthetic" and d.public_only is False
    assert d.blocked_sources == ["synthetic_fixture"]
    assert all(".INTERNAL.synthetic." in Path(p).name for p in d.paths.values())
    assert not (tmp_path / "panel.json").exists()
    assert d.manifest["blocked_sources"] == ["synthetic_fixture"]


def test_export_carries_citations_gaps_and_unresolved_rows(panel, tmp_path):
    d = deliver_panel(panel, tmp_path)
    doc = json.loads(Path(d.paths["panel_json"]).read_text())
    tables = doc["sources"]["tables"]
    assert set(tables) == {"constraint_history", "constraint_coverage"}
    for entry in tables.values():
        assert entry["source"] == "synthetic_fixture"
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
        assert entry["derived_from"] == ["raw:da_2025", "raw:rt_2025"]
        assert entry["publishable"] is False
    raw = {r["key"]: r for r in doc["sources"]["raw"]}
    assert raw["raw:da_2025"]["url"] == "fixture://spp_synth_panel/raw/da_2025.csv"
    assert raw["raw:da_2025"]["sha256"] == sha256_of(SNAPSHOT / "raw" / "da_2025.csv")

    gaps = [(r["market"], r["period"]) for r in doc["coverage"] if r["coverage"] == "none"]
    assert gaps == [("DA", "2025-11"), ("RT", "2025-12")]
    assert {r["match_status"] for r in doc["unresolved"]} == {"uncertain", "unmatched"}
    assert doc["caveats"]
    april = next(r for r in doc["monthly"] if (r["constraint_id"], r["market"], r["period"])
                 == ("FLOWGATE_1", "DA", "2025-04"))
    assert april["binding"] == "no" and april["hours_binding"] == 0.0
    assert april["raw_refs"] == "raw:da_2025"
    uncovered = next(r for r in doc["monthly"] if (r["constraint_id"], r["market"], r["period"])
                     == ("FLOWGATE_1", "DA", "2025-11"))
    assert uncovered["binding"] == "unknown" and uncovered["hours_binding"] is None

    header = Path(d.paths["unresolved_csv"]).read_text().splitlines()[0]
    assert header.startswith("element_id,element_kind,constraint_id")


def test_manifest_records_inputs_runtime_and_digest(panel, tmp_path):
    d = deliver_panel(panel, tmp_path)
    m = json.loads(Path(d.paths["manifest"]).read_text())
    assert m["schema_version"] == 2 and m["kind"] == "constraint_panel"
    assert m["inputs"] == panel.input_identity
    assert m["runtime"]["source_sha256"] == panel.runtime["source_sha256"]
    assert re.fullmatch(r"[0-9a-f]{64}", m["output_sha256"])
    inputs = m["inputs"]
    assert inputs["footprint"] == {"name": "panel_footprint.csv", "sha256": sha256_of(FOOTPRINT)}
    assert inputs["window"] == {"start": "2025-01", "end": "2025-12"}
    assert set(inputs["raw"]) == {"raw:da_2025", "raw:rt_2025"}
    serialized = json.dumps(inputs)
    assert str(FIXTURES) not in serialized and "/Users/" not in serialized


def test_repeat_builds_produce_identical_digests(tmp_path):
    first = deliver_panel(build_panel("spp-synth", FOOTPRINT), tmp_path / "a")
    second = deliver_panel(build_panel("spp-synth", FOOTPRINT), tmp_path / "b")
    assert first.manifest["output_sha256"] == second.manifest["output_sha256"]


def test_window_limits_months(tmp_path):
    result = build_panel("spp-synth", FOOTPRINT, start="2025-06", end="2025-08")
    assert result.periods == ["2025-06", "2025-07", "2025-08"]
    s = _summary(result, "FLOWGATE_1", "DA")
    assert s.values["hours_binding"].value == pytest.approx(77 + 35 + 88)
    assert s.months_not_covered == []
    with pytest.raises(ValueError, match="after end"):
        build_panel("spp-synth", FOOTPRINT, start="2025-09", end="2025-08")
    with pytest.raises(ValueError, match="YYYY-MM"):
        build_panel("spp-synth", FOOTPRINT, start="2025-9")


def test_publishable_sources_yield_a_shareable_panel(tmp_path):
    region = _restamped(tmp_path, constraint_history="test_public", constraint_coverage="test_public")
    result = build_panel(region, FOOTPRINT, registry=_registry(test_public=True))
    d = deliver_panel(result, tmp_path / "out")
    assert d.mode == "shareable" and d.public_only is True and d.blocked_sources == []
    assert (tmp_path / "out" / "panel.json").exists()
    assert json.loads((tmp_path / "out" / "panel.json").read_text())["mode"] == "shareable"


@pytest.mark.parametrize(
    "stamps,registry,mode,blocked",
    [
        ({"constraint_history": "test_restricted", "constraint_coverage": "test_restricted"},
         {"test_restricted": False}, "internal", ["test_restricted"]),
        ({"constraint_history": "not_registered", "constraint_coverage": "not_registered"},
         {}, "internal", ["not_registered"]),
        ({"constraint_history": "test_public"},
         {"test_public": True}, "internal-synthetic", ["synthetic_fixture"]),
    ],
)
def test_non_publishable_lineage_forces_internal(tmp_path, stamps, registry, mode, blocked):
    region = _restamped(tmp_path, **stamps)
    result = build_panel(region, FOOTPRINT, registry=_registry(**registry))
    assert blocked_sources(result) == blocked
    d = deliver_panel(result, tmp_path / "out")
    assert d.mode == mode and d.public_only is False
    assert not (tmp_path / "out" / "panel.json").exists()


def test_zero_months_inherit_coverage_taint(tmp_path):
    # Publishable history but synthetic coverage: derived zeros carry the coverage source.
    region = _restamped(tmp_path, constraint_history="test_public")
    result = build_panel(region, FOOTPRINT, registry=_registry(test_public=True))
    zero = _cell(result, "FLOWGATE_1", "DA", "2025-04").values["hours_binding"]
    assert result.store.source_closure(zero.lineage_id) == {"synthetic_fixture"}
    binding = _cell(result, "FLOWGATE_1", "DA", "2025-01").values["hours_binding"]
    assert result.store.source_closure(binding.lineage_id) == {"test_public"}


def test_panel_code_is_part_of_runtime_identity():
    assert "panel" in _SOURCE_DIRS


def test_cli_panel_writes_internal_artifacts(tmp_path, capsys):
    out = tmp_path / "panel"
    assert main(["panel", "--region", "spp-synth", "--footprint", str(FOOTPRINT),
                 "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "mode=internal-synthetic" in printed
    assert "shareable export refused" in printed
    assert "DA coverage: 10 full, 1 partial, 1 not covered of 12 months" in printed
    assert (out / "panel.INTERNAL.synthetic.json").exists()
    assert not (out / "panel.json").exists()


def test_cli_panel_rejects_invalid_footprint_without_writing(tmp_path, capsys):
    footprint = tmp_path / "footprint.csv"
    footprint.write_text(FOOTPRINT.read_text().replace(",fixture-reviewer,2026-09-25", ",,", 1))
    out = tmp_path / "panel"
    with pytest.raises(SystemExit) as exc:
        main(["panel", "--region", "spp-synth", "--footprint", str(footprint), "--out", str(out)])
    assert exc.value.code == 1
    assert "verified but lacks reviewed_by, reviewed_on" in capsys.readouterr().err
    assert not out.exists()


def test_cli_panel_rejects_unregistered_region(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["panel", "--region", "spp", "--footprint", str(FOOTPRINT),
              "--out", str(tmp_path / "panel")])
    assert exc.value.code == 1
    assert "Unknown panel region" in capsys.readouterr().err
