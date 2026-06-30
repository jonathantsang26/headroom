"""Phase 1: triangulation + invariants + confidence on the SPP-SYNTH base."""

from headroom.ingest import ingest_region
from headroom.provenance.lineage import LineageStore
from headroom.quality import run_bucket_a


def _result():
    store = LineageStore()
    bundle = ingest_region("spp-synth", store)
    return store, run_bucket_a(bundle, store)


def test_base_shape_and_envelopes():
    store, res = _result()
    assert res.quality_report["n_buses"] == 8
    assert res.quality_report["n_lines"] == 11
    # every line's voltage is enveloped with source/version/confidence
    for ln in res.lines:
        assert ln.voltage_kv.source  # provenance present
        assert ln.voltage_kv.source_version
        assert ln.voltage_kv.confidence in {"high", "medium", "low"}


def test_divergence_lowers_confidence_and_flags():
    _, res = _result()
    line6 = res.lines_by_id["LINE_6"]
    # hifld=230, eia=161, ferc1=230 -> majority 230, divergence flagged, low confidence
    assert line6.voltage_kv.value == 230
    assert "source_divergence" in line6.voltage_kv.flags
    assert line6.voltage_kv.confidence == "low"


def test_implausible_reading_flagged_but_not_dropped():
    _, res = _result()
    line1 = res.lines_by_id["LINE_1"]
    # ferc1 reports 350 (implausible); majority keeps 345; the raw reading is flagged.
    assert line1.voltage_kv.value == 345
    report_flags = res.quality_report["flags_by_field"]
    assert report_flags["voltage_kv_raw"].get("voltage_implausible", 0) >= 1


def test_conductor_voltage_invariant():
    _, res = _result()
    line9 = res.lines_by_id["LINE_9"]
    assert line9.voltage_kv.value == 500
    assert "conductor_voltage_mismatch" in line9.conductor.flags
    # voltage confidence downgraded by the mismatch flag
    assert line9.voltage_kv.confidence in {"medium", "low"}


def test_cost_prior_is_high_when_unflagged():
    _, res = _result()
    # audited dollar figures start "high"
    assert res.lines_by_id["LINE_2"].cost_usd.confidence == "high"


def test_quality_report_records_violations():
    _, res = _result()
    assert res.quality_report["n_violations"] >= 2  # implausible voltage + conductor
