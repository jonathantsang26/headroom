"""Phase 5: generalize (region-parameterized) + harden (pinned manifest reproduces
a prior run exactly). Done-when for the whole project."""

import pytest

from headroom.export import deliver
from headroom.pipeline import run_pipeline
from headroom.provenance.reproduce import reproduce_from_manifest, scores_fingerprint


def test_seed_is_recorded_in_manifest(tmp_path):
    result = run_pipeline("spp-synth")
    d = deliver(result, tmp_path)
    assert "seed" in d.manifest["scoring"]  # the reproducibility key is captured


def test_two_runs_are_byte_identical():
    a = run_pipeline("spp-synth")
    b = run_pipeline("spp-synth")
    assert scores_fingerprint(a) == scores_fingerprint(b)


def test_pinned_manifest_reproduces_the_run(tmp_path):
    original = run_pipeline("spp-synth")
    d = deliver(original, tmp_path)
    # reproduce purely from the manifest the run wrote
    replay = reproduce_from_manifest(d.manifest)
    assert scores_fingerprint(replay) == scores_fingerprint(original)


def test_manifest_file_roundtrip_reproduces(tmp_path):
    original = run_pipeline("spp-synth")
    d = deliver(original, tmp_path)
    replay = reproduce_from_manifest(d.paths["manifest"])  # from disk
    assert scores_fingerprint(replay) == scores_fingerprint(original)


def test_changing_seed_changes_the_stability_but_not_catastrophically():
    from headroom.model import ScoringConfig

    base = run_pipeline("spp-synth")
    cfg = base.scoring
    other = ScoringConfig(
        weights=cfg.weights, references=cfg.references, draws=cfg.draws,
        top_fraction=cfg.top_fraction, seed=cfg.seed + 1,
    )
    alt = run_pipeline("spp-synth", scoring=other)
    # the rock-stable leader stays; only the boundary p_top_k jiggles
    assert base.model.scores[0].constraint_id == alt.model.scores[0].constraint_id


def test_pipeline_is_region_parameterized():
    # miso-synth exercises the SAME code path (generalization proof), not a bespoke SPP-only pipeline.
    spp = run_pipeline("spp-synth")
    miso = run_pipeline("miso-synth")
    assert spp.region.rto == "SPP"
    assert miso.region.rto == "MISO"
    assert miso.model.n_constraints == spp.model.n_constraints


def test_coverage_note_present_in_quality_report():
    result = run_pipeline("spp-synth")
    assert "coverage_note" in result.bucket_a.quality_report
    # entities support the non-filer blind-spot flag
    assert all(b.coverage == "observed" for b in result.bucket_a.buses)


def test_reproduce_rejects_manifest_without_scoring():
    # A bare manifest (e.g.
    with pytest.raises(ValueError, match="not reproducible"):
        reproduce_from_manifest({"run_label": "x"})
