"""The replay command exports only after verification succeeds."""

import json
from dataclasses import replace

import pytest

from headroom.cli import main
from headroom.export import deliver
from headroom.model import ScoringConfig
from headroom.pipeline import run_pipeline


def test_replay_command_writes_verified_internal_artifacts(tmp_path, capsys):
    result = run_pipeline("spp-synth", scoring=replace(ScoringConfig.load(), draws=100))
    original = deliver(result, tmp_path / "original")
    out = tmp_path / "replay"

    assert main(["reproduce", "--manifest", original.paths["manifest"], "--out", str(out)]) == 0

    replay_manifest = json.loads((out / "run_manifest.INTERNAL.synthetic.json").read_text())
    assert replay_manifest["output_sha256"] == original.manifest["output_sha256"]
    assert replay_manifest["inputs"] == original.manifest["inputs"]
    assert replay_manifest["public_only"] is False
    assert "Historical replay verified" in capsys.readouterr().out
    assert not (out / "run_manifest.json").exists()


def test_replay_command_rejects_legacy_manifest_without_writing(tmp_path, capsys):
    manifest = tmp_path / "legacy.json"
    manifest.write_text(json.dumps({"region": "spp-synth", "scoring": {"seed": 1}}))
    out = tmp_path / "replay"

    with pytest.raises(SystemExit) as exc:
        main(["reproduce", "--manifest", str(manifest), "--out", str(out)])

    assert exc.value.code == 1
    assert "not reproducible" in capsys.readouterr().err
    assert not out.exists()


def test_replay_command_passes_relocated_input_override(tmp_path, monkeypatch):
    manifest = tmp_path / "run.json"
    archive = tmp_path / "archive"
    out = tmp_path / "replay"

    def reject(path, *, data_dir):
        assert path == str(manifest)
        assert data_dir == str(archive)
        raise ValueError("changed input archive")

    monkeypatch.setattr("headroom.cli.reproduce_from_manifest", reject)
    with pytest.raises(SystemExit) as exc:
        main([
            "reproduce", "--manifest", str(manifest), "--out", str(out),
            "--data-dir", str(archive),
        ])
    assert exc.value.code == 1
    assert not out.exists()


def test_replay_command_rejects_oversized_scoring_number(tmp_path, capsys):
    result = run_pipeline("spp-synth", scoring=replace(ScoringConfig.load(), draws=100))
    original = deliver(result, tmp_path / "original")
    manifest = original.manifest
    manifest["scoring"]["weights"]["rent"] = 10 ** 400
    path = tmp_path / "invalid-scoring.json"
    path.write_text(json.dumps(manifest))
    out = tmp_path / "replay"

    with pytest.raises(SystemExit) as exc:
        main(["reproduce", "--manifest", str(path), "--out", str(out)])

    assert exc.value.code == 1
    assert "invalid scoring" in capsys.readouterr().err
    assert not out.exists()


@pytest.mark.parametrize("value", [0, -1.0])
def test_replay_command_rejects_nonpositive_reference(tmp_path, capsys, value):
    result = run_pipeline("spp-synth", scoring=replace(ScoringConfig.load(), draws=100))
    manifest = deliver(result, tmp_path / "original").manifest
    manifest["scoring"]["references"]["demand_ref_mw"] = value
    path = tmp_path / "zero-reference.json"
    path.write_text(json.dumps(manifest))
    out = tmp_path / "replay"

    with pytest.raises(SystemExit) as exc:
        main(["reproduce", "--manifest", str(path), "--out", str(out)])

    assert exc.value.code == 1
    assert "references must be positive" in capsys.readouterr().err
    assert not out.exists()
