"""Phase 4: the export path (the verified deliverable) and the publishable-gate
decision between shareable and internal-synthetic artifacts."""

import json
from pathlib import Path

import pytest

from headroom.export import (
    build_score_rows,
    deliver,
    export_shareable_or_raise,
)
from headroom.pipeline import run_pipeline
from headroom.provenance.gate import PublishabilityError
from headroom.sources import load_registry


def test_synthetic_run_is_refused_shareable_and_written_internal(tmp_path):
    result = run_pipeline("spp-synth")
    d = deliver(result, tmp_path)
    assert d.mode == "internal-synthetic"
    assert d.public_only is False
    assert len(d.blocked) == result.model.n_constraints  # every score is synthetic
    # only the INTERNAL-stamped artifact exists; no bare shareable file
    assert (tmp_path / "shortlist.INTERNAL.synthetic.csv").exists()
    assert not (tmp_path / "shortlist.csv").exists()


def test_strict_shareable_export_raises_on_synthetic(tmp_path):
    result = run_pipeline("spp-synth")
    with pytest.raises(PublishabilityError):
        export_shareable_or_raise(result, tmp_path)


def test_all_public_lineage_clears_the_gate(tmp_path):
    reg = load_registry()
    reg.get("synthetic_fixture").compliance.publishable = True
    result = run_pipeline("spp-synth", registry=reg)
    d = deliver(result, tmp_path)
    assert d.mode == "shareable"
    assert d.blocked == []
    assert (tmp_path / "shortlist.csv").exists()
    assert (tmp_path / "run_manifest.json").exists()


def test_manifest_captures_scoring_for_reproducibility(tmp_path):
    result = run_pipeline("spp-synth")
    d = deliver(result, tmp_path)
    scoring = d.manifest["scoring"]
    assert scoring["seed"] == result.scoring.seed
    assert abs(sum(scoring["weights"].values()) - 1.0) < 1e-9
    assert d.manifest["mode"] == "internal-synthetic"
    assert d.manifest["region"] == "spp-synth"


def test_score_rows_are_ranked_and_complete():
    result = run_pipeline("spp-synth")
    rows = build_score_rows(result)
    assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
    for r in rows:
        assert r["composite_lo"] <= r["composite_expected"] <= r["composite_hi"]
        assert r["recommended_get"]
        assert all(f"sig_{n}" in r for n in ("rent", "loading", "planning"))


def test_mcp_refuses_non_shareable_scores(tmp_path):
    from headroom.mcp.server import build_server

    result = run_pipeline("spp-synth")
    d = deliver(result, tmp_path)  # writes INTERNAL.synthetic artifacts
    pq = d.paths["parquet"]  # scores.INTERNAL.synthetic.parquet
    with pytest.raises(RuntimeError, match="shareable"):
        build_server(pq)


def test_mcp_refuses_when_no_manifest(tmp_path):
    from headroom.mcp.server import build_server

    lonely = tmp_path / "scores.parquet"
    lonely.write_text("")  # a parquet with no sibling manifest -> fail closed
    with pytest.raises(RuntimeError, match="no sibling run manifest"):
        build_server(lonely)


def test_parquet_is_written_and_queryable(tmp_path):
    result = run_pipeline("spp-synth")
    d = deliver(result, tmp_path)
    pq = d.paths.get("parquet")
    assert pq and Path(pq).exists()
    import duckdb

    n = duckdb.sql(f"SELECT count(*) FROM '{pq}'").fetchone()[0]
    assert n == result.model.n_constraints


def test_manifest_records_each_output_digest_and_leaves_no_temp_files(tmp_path):
    import hashlib

    d = deliver(run_pipeline("spp-synth"), tmp_path)
    outputs = d.manifest["outputs"]
    assert {"scores_json", "shortlist_json", "csv", "quality_report"} <= set(outputs)
    for entry in outputs.values():
        data = (tmp_path / entry["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    on_disk = json.loads(Path(d.paths["manifest"]).read_text())
    assert on_disk["outputs"] == outputs
    assert not list(tmp_path.glob(".*.tmp"))


def test_csv_cells_cannot_start_a_spreadsheet_formula():
    from headroom.export import _csv_bytes

    text = _csv_bytes([{"name": "=HYPERLINK(\"x\")", "n": -1.5, "ok": "LN A - B"}]).decode()
    assert "'=HYPERLINK" in text and ",-1.5," in text and "LN A - B" in text


def test_an_output_directory_holds_one_run(tmp_path):
    from dataclasses import replace

    from headroom.export import deliver
    from headroom.model import ScoringConfig
    from headroom.pipeline import run_pipeline

    a = run_pipeline("spp-synth", scoring=replace(ScoringConfig.load(), draws=60))
    b = run_pipeline("spp-synth", scoring=replace(ScoringConfig.load(), draws=70, seed=3))
    deliver(a, tmp_path)
    deliver(a, tmp_path)  # the same run again: idempotent
    with pytest.raises(FileExistsError, match="different run"):
        deliver(b, tmp_path)
    assert deliver(b, tmp_path, overwrite=True).manifest["output_sha256"] != ""


def test_overwrite_removes_the_previous_runs_files_under_either_mode(tmp_path):
    from dataclasses import replace

    from headroom.export import deliver
    from headroom.model import ScoringConfig
    from headroom.pipeline import run_pipeline

    (tmp_path / "scores.parquet").write_bytes(b"stale shareable parquet")
    (tmp_path / "shortlist.csv").write_text("stale")
    run = run_pipeline("spp-synth", scoring=replace(ScoringConfig.load(), draws=60))
    deliver(run, tmp_path, overwrite=True)  # synthetic: INTERNAL names
    assert not (tmp_path / "scores.parquet").exists()
    assert not (tmp_path / "shortlist.csv").exists()
    assert (tmp_path / "shortlist.INTERNAL.synthetic.csv").exists()


def test_missing_numbers_are_null_so_columns_stay_numeric(tmp_path):
    import json
    from dataclasses import replace

    from headroom.export import deliver
    from headroom.model import ScoringConfig
    from headroom.pipeline import run_pipeline

    d = deliver(run_pipeline("spp-synth", scoring=replace(ScoringConfig.load(), draws=60)), tmp_path)
    rows = json.loads(open(d.paths["scores_json"]).read())
    assert all(r["ev_mean_musd"] is None for r in rows)  # no EV layer on the fixture
    assert all(not isinstance(r["rent_musd"], str) for r in rows)


def test_mcp_top_flowgates_returns_dicts_in_rank_order(tmp_path):
    from dataclasses import replace

    from headroom.export import deliver
    from headroom.mcp.server import top_flowgates_rows
    from headroom.model import ScoringConfig
    from headroom.pipeline import run_pipeline

    d = deliver(run_pipeline("spp-synth", scoring=replace(ScoringConfig.load(), draws=60)), tmp_path)
    rows = top_flowgates_rows(d.paths["parquet"], limit=3)
    assert [r["rank"] for r in rows] == [1, 2, 3]
    assert all(isinstance(r, dict) and r["constraint_id"] for r in rows)


def test_a_crash_while_writing_leaves_the_previous_run_whole(tmp_path, monkeypatch):
    """--overwrite touches nothing of the earlier run until the whole new set is written."""
    from dataclasses import replace

    from headroom import export
    from headroom.model import ScoringConfig
    from headroom.pipeline import run_pipeline

    first = export.deliver(run_pipeline("spp-synth", scoring=replace(ScoringConfig.load(), draws=60)), tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}

    def crash(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(export, "_materialize_parquet", crash)
    second = run_pipeline("spp-synth", scoring=replace(ScoringConfig.load(), draws=70, seed=3))
    with pytest.raises(OSError, match="disk full"):
        export.deliver(second, tmp_path, overwrite=True)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before  # no staging left
    assert first.manifest["output_sha256"]


def test_a_refused_run_on_real_data_is_labelled_internal_not_synthetic():
    from types import SimpleNamespace

    from headroom.export import _export_mode

    real = SimpleNamespace(region=SimpleNamespace(synthetic=False))
    fixture = SimpleNamespace(region=SimpleNamespace(synthetic=True))
    assert _export_mode(real, refused=True) == ("internal", False, ".INTERNAL")
    assert _export_mode(fixture, refused=True) == ("internal-synthetic", False, ".INTERNAL.synthetic")
    assert _export_mode(real, refused=False) == ("shareable", True, "")


def test_an_interrupted_move_keeps_the_new_run_in_staging_and_says_where(tmp_path, monkeypatch):
    import os
    from dataclasses import replace

    from headroom import export
    from headroom.model import ScoringConfig
    from headroom.pipeline import run_pipeline

    export.deliver(run_pipeline("spp-synth", scoring=replace(ScoringConfig.load(), draws=60)), tmp_path)
    real_replace, moves = os.replace, []

    def flaky(src, dst):
        if Path(dst).parent == tmp_path:  # a move into the out dir (not a staging write)
            moves.append(dst)
            if len(moves) == 2:
                raise OSError("interrupted")
        return real_replace(src, dst)

    monkeypatch.setattr(export.os, "replace", flaky)
    second = run_pipeline("spp-synth", scoring=replace(ScoringConfig.load(), draws=70, seed=3))
    with pytest.raises(RuntimeError, match="kept in .*staging") as exc:
        export.deliver(second, tmp_path, overwrite=True)
    staging = Path(str(exc.value).rsplit("kept in ", 1)[1])
    assert staging.is_dir() and any(p.name.startswith("run_manifest") for p in staging.iterdir())
    assert not list(tmp_path.glob("run_manifest*.json"))  # no manifest over a mixed set


def test_artifacts_without_a_manifest_are_not_silently_joined(tmp_path):
    from dataclasses import replace

    from headroom.export import deliver
    from headroom.model import ScoringConfig
    from headroom.pipeline import run_pipeline

    (tmp_path / "scores.INTERNAL.parquet").write_bytes(b"left over")
    run = run_pipeline("spp-synth", scoring=replace(ScoringConfig.load(), draws=60))
    with pytest.raises(FileExistsError, match="no manifest"):
        deliver(run, tmp_path)
    deliver(run, tmp_path, overwrite=True)
    assert not (tmp_path / "scores.INTERNAL.parquet").exists()
