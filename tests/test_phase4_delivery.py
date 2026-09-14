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
