"""Historical replay stays pinned when data, configuration, or current moves.

All snapshots here contain fabricated fixture CSVs and retain the
``synthetic_fixture`` source stamp, including when exercising the live loader.
"""

from __future__ import annotations

import json
import shutil
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from headroom.export import Delivery, deliver
from headroom.ingest.region import REGIONS, RegionConfig
from headroom.ingest.snapshot import MANIFEST_NAME, REQUIRED_TABLES, sha256_of
from headroom.model import ScoringConfig
from headroom.pipeline import PipelineResult, run_pipeline
from headroom.provenance.digests import ranking_digest
from headroom.provenance.gate import PublishabilityError, assert_publishable
from headroom.provenance.reproduce import reproduce_from_manifest, scores_fingerprint
from headroom.provenance.versions import PUDL_RELEASE
from headroom.sources import load_registry


FIXTURES = Path(__file__).parent / "fixtures" / "spp_synth"


@dataclass
class RecordedRun:
    result: PipelineResult
    delivery: Delivery
    data_dir: Path


@pytest.fixture
def scoring():
    return replace(ScoringConfig.load(), draws=100)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Historical replay tests must never fetch source data")

    monkeypatch.setattr("urllib.request.urlopen", forbidden)


def _make_snapshot(path: Path) -> Path:
    shutil.copytree(FIXTURES, path / "normalized")
    source_version = load_registry()["synthetic_fixture"].source_version
    tables = {
        table: {
            "path": f"normalized/{table}.csv",
            "source": "synthetic_fixture",
            "source_version": source_version,
            "retrieved_at": "2026-09-22T00:00:00+00:00",
            "sha256": sha256_of(path / "normalized" / f"{table}.csv"),
        }
        for table in REQUIRED_TABLES
    }
    (path / MANIFEST_NAME).write_text(json.dumps({
        "schema_version": 1,
        "snapshot_date": path.name,
        "tables": tables,
        "raw": {},
    }, sort_keys=True))
    return path


def _change_table(snapshot: Path) -> None:
    """Change valid values and update the snapshot's own integrity hash."""
    table = snapshot / "normalized" / "congestion.csv"
    before = table.read_text()
    after = before.replace("FLOWGATE_1,2.4,380", "FLOWGATE_1,24.0,380")
    assert before != after
    table.write_text(after)
    manifest_path = snapshot / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest["tables"]["congestion"]["sha256"] = sha256_of(table)
    manifest_path.write_text(json.dumps(manifest, sort_keys=True))


def _forbid_model(monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("Input validation must reject drift before running the model")

    monkeypatch.setattr("headroom.pipeline.run_model", forbidden)


def _forbid_pipeline(monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("Replay must reject invalid pins before running the pipeline")

    monkeypatch.setattr("headroom.provenance.reproduce.run_pipeline", forbidden)


@pytest.fixture
def synthetic_run(tmp_path, monkeypatch, scoring):
    data_dir = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, data_dir)
    region = RegionConfig("spp-replay-synth", "SPP", data_dir, synthetic=True)
    monkeypatch.setitem(REGIONS, region.name, region)
    result = run_pipeline(region.name, scoring=scoring)
    return RecordedRun(result, deliver(result, tmp_path / "delivery"), data_dir)


@pytest.fixture
def snapshot_run(tmp_path, monkeypatch, scoring):
    snapshot = _make_snapshot(tmp_path / "spp" / "2026-09-22")
    current = snapshot.parent / "current"
    current.symlink_to(snapshot.name)
    region = RegionConfig("spp-replay-test", "SPP", current, synthetic=False)
    monkeypatch.setitem(REGIONS, region.name, region)
    result = run_pipeline(region.name, scoring=scoring)
    return RecordedRun(result, deliver(result, tmp_path / "delivery"), snapshot)


def test_delivery_pins_rootless_fixture_bytes_runtime_and_full_output(synthetic_run):
    result = synthetic_run.result
    manifest = synthetic_run.delivery.manifest
    assert manifest["schema_version"] == 2
    assert manifest["inputs"] == result.input_identity
    assert manifest["runtime"] == result.runtime
    assert manifest["pinned"]["pudl_release"] == PUDL_RELEASE
    assert manifest["output_sha256"] == ranking_digest(result)
    assert len(manifest["output_sha256"]) == 64
    assert manifest["sources"]["synthetic_fixture"]["publishable"] is False
    assert result.input_identity == {
        "schema_version": 1,
        "kind": "synthetic",
        "region": result.region.name,
        "rto": "SPP",
        "tables": {
            table: {
                "path": f"{table}.csv",
                "sha256": sha256_of(synthetic_run.data_dir / f"{table}.csv"),
            }
            for table in REQUIRED_TABLES
        },
    }
    assert str(synthetic_run.data_dir) not in json.dumps(manifest["inputs"])


def test_snapshot_identity_pins_manifest_bytes_and_resolved_snapshot(snapshot_run):
    identity = snapshot_run.result.input_identity
    assert identity == {
        "schema_version": 1,
        "kind": "snapshot",
        "region": snapshot_run.result.region.name,
        "rto": "SPP",
        "snapshot_id": snapshot_run.data_dir.name,
        "snapshot_manifest_sha256": sha256_of(snapshot_run.data_dir / MANIFEST_NAME),
        "tables": {
            table: {
                "path": f"normalized/{table}.csv",
                "sha256": sha256_of(snapshot_run.data_dir / "normalized" / f"{table}.csv"),
            }
            for table in REQUIRED_TABLES
        },
    }
    assert str(snapshot_run.data_dir) not in json.dumps(identity)


@pytest.mark.parametrize("from_disk", [False, True])
def test_fixture_replay_accepts_mapping_and_json_path(synthetic_run, from_disk):
    delivery = synthetic_run.delivery
    manifest = Path(delivery.paths["manifest"]) if from_disk else delivery.manifest
    replay = reproduce_from_manifest(manifest)
    assert ranking_digest(replay) == delivery.manifest["output_sha256"]
    assert replay.input_identity == synthetic_run.result.input_identity


def test_sorted_json_roundtrip_preserves_recorded_signal_order(synthetic_run, tmp_path):
    manifest_path = tmp_path / "sorted-manifest.json"
    manifest_path.write_text(json.dumps(synthetic_run.delivery.manifest, sort_keys=True))

    replay = reproduce_from_manifest(manifest_path)
    assert list(replay.scoring.weights) == list(synthetic_run.result.scoring.weights)
    assert ranking_digest(replay) == synthetic_run.delivery.manifest["output_sha256"]


def test_snapshot_replay_uses_recorded_archive_after_current_moves(snapshot_run, scoring):
    old = snapshot_run.data_dir
    new = _make_snapshot(old.parent / "2026-09-23")
    _change_table(new)
    current = old.parent / "current"
    current.unlink()
    current.symlink_to(new.name)

    newest = run_pipeline(snapshot_run.result.region.name, scoring=scoring)
    assert scores_fingerprint(newest) != scores_fingerprint(snapshot_run.result)
    replay = reproduce_from_manifest(snapshot_run.delivery.manifest)
    assert replay.input_identity == snapshot_run.result.input_identity
    assert ranking_digest(replay) == snapshot_run.delivery.manifest["output_sha256"]
    assert current.resolve() == new


def test_missing_archive_never_falls_back_to_current(snapshot_run, monkeypatch):
    old = snapshot_run.data_dir
    new = _make_snapshot(old.parent / "2026-09-23")
    current = old.parent / "current"
    current.unlink()
    current.symlink_to(new.name)
    shutil.rmtree(old)
    _forbid_model(monkeypatch)

    with pytest.raises((FileNotFoundError, ValueError)):
        reproduce_from_manifest(snapshot_run.delivery.manifest)
    assert current.resolve() == new


def test_table_change_with_updated_snapshot_hash_still_refuses_replay(snapshot_run, monkeypatch):
    _change_table(snapshot_run.data_dir)
    _forbid_model(monkeypatch)

    with pytest.raises(ValueError, match="[Ii]nput|[Mm]ismatch|[Cc]hanged"):
        reproduce_from_manifest(snapshot_run.delivery.manifest)


def test_snapshot_metadata_only_change_refuses_replay(snapshot_run, monkeypatch):
    manifest_path = snapshot_run.data_dir / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest["tables"]["congestion"]["retrieved_at"] = "2026-09-23T00:00:00+00:00"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True))
    _forbid_model(monkeypatch)

    with pytest.raises(ValueError, match="[Ii]nput|[Mm]ismatch|[Cc]hanged"):
        reproduce_from_manifest(snapshot_run.delivery.manifest)


def test_archive_relocation_override_reproduces_with_original_basename(snapshot_run, tmp_path):
    original = snapshot_run.data_dir
    moved = tmp_path / "moved-archives" / original.name
    moved.parent.mkdir()
    shutil.move(str(original), moved)

    replay = reproduce_from_manifest(snapshot_run.delivery.manifest, data_dir=moved)
    assert replay.input_identity == snapshot_run.result.input_identity
    assert ranking_digest(replay) == snapshot_run.delivery.manifest["output_sha256"]


def test_archive_relocation_override_rejects_changed_basename(snapshot_run, tmp_path, monkeypatch):
    moved = tmp_path / "renamed-snapshot"
    shutil.copytree(snapshot_run.data_dir, moved)
    _forbid_model(monkeypatch)

    with pytest.raises(ValueError):
        reproduce_from_manifest(snapshot_run.delivery.manifest, data_dir=moved)


def test_archive_relocation_override_rejects_changed_bytes(snapshot_run, tmp_path, monkeypatch):
    moved = tmp_path / "moved-archives" / snapshot_run.data_dir.name
    shutil.copytree(snapshot_run.data_dir, moved)
    _change_table(moved)
    _forbid_model(monkeypatch)

    with pytest.raises(ValueError):
        reproduce_from_manifest(snapshot_run.delivery.manifest, data_dir=moved)


def test_fixture_drift_is_rejected_before_model(synthetic_run, monkeypatch):
    path = synthetic_run.data_dir / "load_930.csv"
    path.write_bytes(path.read_bytes() + b"\n")
    _forbid_model(monkeypatch)

    with pytest.raises(ValueError, match="[Ii]nput|[Mm]ismatch|[Cc]hanged"):
        reproduce_from_manifest(synthetic_run.delivery.manifest)


def test_pipeline_expected_inputs_guards_direct_reruns(synthetic_run, monkeypatch, scoring):
    path = synthetic_run.data_dir / "congestion.csv"
    path.write_bytes(path.read_bytes() + b"\n")
    _forbid_model(monkeypatch)

    with pytest.raises(ValueError, match="[Ii]nput|[Mm]ismatch|[Cc]hanged"):
        run_pipeline(
            synthetic_run.result.region.name,
            scoring=scoring,
            expected_inputs=synthetic_run.result.input_identity,
        )


def test_legacy_manifest_is_rejected_before_pipeline(synthetic_run, monkeypatch):
    manifest = synthetic_run.delivery.manifest
    legacy = {"region": manifest["region"], "scoring": manifest["scoring"]}
    _forbid_pipeline(monkeypatch)

    with pytest.raises(ValueError):
        reproduce_from_manifest(legacy)


@pytest.mark.parametrize("key", [
    "schema_version", "inputs", "runtime", "sources", "pinned", "output_sha256",
])
def test_missing_replay_pin_is_rejected_before_pipeline(synthetic_run, monkeypatch, key):
    manifest = deepcopy(synthetic_run.delivery.manifest)
    del manifest[key]
    _forbid_pipeline(monkeypatch)

    with pytest.raises(ValueError):
        reproduce_from_manifest(manifest)


@pytest.mark.parametrize("pin", ["source_version", "publishable", "pudl_release", "runtime"])
def test_altered_source_or_runtime_pin_is_rejected_before_pipeline(synthetic_run, monkeypatch, pin):
    manifest = deepcopy(synthetic_run.delivery.manifest)
    if pin == "source_version":
        manifest["sources"]["synthetic_fixture"][pin] = "different-release"
    elif pin == "publishable":
        manifest["sources"]["synthetic_fixture"][pin] = True
    elif pin == "pudl_release":
        manifest["pinned"][pin] = "different-release"
    else:
        manifest["runtime"]["python"]["version"] = "0.0.0"
    _forbid_pipeline(monkeypatch)

    with pytest.raises(ValueError):
        reproduce_from_manifest(manifest)


def test_changed_registry_is_rejected_before_pipeline(synthetic_run, monkeypatch):
    registry = load_registry()
    registry["synthetic_fixture"].source_version = "different-release"
    _forbid_pipeline(monkeypatch)

    with pytest.raises(ValueError):
        reproduce_from_manifest(synthetic_run.delivery.manifest, registry=registry)


def test_tampered_output_digest_is_rejected(synthetic_run):
    manifest = deepcopy(synthetic_run.delivery.manifest)
    manifest["output_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="[Oo]utput|[Dd]igest|[Mm]ismatch"):
        reproduce_from_manifest(manifest)


@pytest.mark.parametrize("fixture_name", ["synthetic_run", "snapshot_run"])
def test_replayed_fabricated_inputs_remain_blocked_for_public_export(request, fixture_name, tmp_path):
    recorded = request.getfixturevalue(fixture_name)
    replay = reproduce_from_manifest(recorded.delivery.manifest)
    delivery = deliver(replay, tmp_path / "replayed-delivery")

    # a synthetic region is labelled synthetic; a live snapshot with fabricated
    # tables is real-region data the gate refused: internal, not "synthetic"
    want = "internal-synthetic" if replay.region.synthetic else "internal"
    assert delivery.mode == want
    assert delivery.public_only is False
    assert set(delivery.blocked) == {score.constraint_id for score in replay.model.scores}
    label = ["INTERNAL", "synthetic"] if replay.region.synthetic else ["INTERNAL"]
    assert all(Path(path).name.split(".")[1:-1] == label for path in delivery.paths.values())
    for score in replay.model.scores:
        with pytest.raises(PublishabilityError):
            assert_publishable(score.composite.lineage_id, replay.store, replay.registry)
