"""Snapshot integrity checks and atomic manifest updates, entirely offline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from headroom.ingest.snapshot import MANIFEST_NAME, Snapshot, sha256_of, update_manifest


def _snapshot(root: Path) -> Snapshot:
    path = root / "normalized" / "sample.csv"
    path.parent.mkdir(parents=True)
    path.write_text("id,value\n1,2\n")
    update_manifest(root, tables={"sample": {
        "source": "public_source",
        "source_version": "v1",
        "retrieved_at": "2026-07-01T12:00:00+00:00",
        "sha256": sha256_of(path),
    }})
    return Snapshot(root)


def test_snapshot_validates_matching_hash(tmp_path):
    snap = _snapshot(tmp_path)
    snap.require_tables(["sample"])
    assert snap.verify("sample")


@pytest.mark.parametrize("digest", [None, "", "bad", "z" * 64, 123])
def test_snapshot_rejects_missing_or_malformed_hash(tmp_path, digest):
    snap = _snapshot(tmp_path)
    snap.manifest["tables"]["sample"]["sha256"] = digest
    assert not snap.verify("sample")
    with pytest.raises(ValueError, match="sample.*sha256"):
        snap.require_tables(["sample"])


def test_snapshot_rejects_modified_bytes(tmp_path):
    snap = _snapshot(tmp_path)
    snap.table_path("sample").write_text("id,value\n1,3\n")
    assert not snap.verify("sample")
    with pytest.raises(ValueError, match="sample.*sha256"):
        snap.require_tables(["sample"])


@pytest.mark.parametrize("key,value", [
    ("source", ""), ("source_version", " "), ("source_version", None),
    ("retrieved_at", "2026-07-01"), ("retrieved_at", "2026-07-01T12:00:00"),
    ("retrieved_at", "invalid"),
])
def test_snapshot_rejects_invalid_stamp(tmp_path, key, value):
    snap = _snapshot(tmp_path)
    snap.manifest["tables"]["sample"][key] = value
    with pytest.raises(ValueError, match=key):
        snap.require_tables(["sample"])


def test_snapshot_requires_each_table(tmp_path):
    snap = _snapshot(tmp_path)
    with pytest.raises(KeyError, match="missing"):
        snap.require_tables(["sample", "missing"])


def test_snapshot_rejects_missing_file(tmp_path):
    snap = _snapshot(tmp_path)
    snap.table_path("sample").unlink()
    with pytest.raises(FileNotFoundError, match="sample"):
        snap.require_tables(["sample"])


@pytest.mark.parametrize("path", ["../outside.csv", "/tmp/outside.csv"])
def test_snapshot_rejects_paths_outside_root(tmp_path, path):
    snap = _snapshot(tmp_path)
    snap.manifest["tables"]["sample"]["path"] = path
    with pytest.raises(ValueError, match="path"):
        snap.require_tables(["sample"])


def test_snapshot_rejects_absolute_path_even_inside_root(tmp_path):
    snap = _snapshot(tmp_path)
    snap.manifest["tables"]["sample"]["path"] = str(snap.table_path("sample"))
    with pytest.raises(ValueError, match="relative"):
        snap.require_tables(["sample"])


def test_snapshot_rejects_symlink_escape(tmp_path):
    snap = _snapshot(tmp_path / "snapshot")
    external = tmp_path / "external.csv"
    external.write_text("id,value\n1,2\n")
    path = snap.table_path("sample")
    path.unlink()
    path.symlink_to(external)
    with pytest.raises(ValueError, match="escapes"):
        snap.require_tables(["sample"])


def test_snapshot_pins_current_symlink_at_construction(tmp_path):
    original = _snapshot(tmp_path / "original")
    replacement = _snapshot(tmp_path / "replacement")
    replacement.table_path("sample").write_text("replacement contents")
    current = tmp_path / "current"
    current.symlink_to(original.root, target_is_directory=True)
    pinned = Snapshot(current)
    current.unlink()
    current.symlink_to(replacement.root, target_is_directory=True)

    pinned.require_tables(["sample"])
    assert pinned.table_path("sample").read_text() == "id,value\n1,2\n"


@pytest.mark.parametrize("manifest", [[], {}, {"schema_version": True, "tables": {}},
                                      {"schema_version": 2, "tables": {}},
                                      {"schema_version": 1, "tables": []}])
def test_snapshot_rejects_invalid_manifest_shape(tmp_path, manifest):
    (tmp_path / MANIFEST_NAME).write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        Snapshot(tmp_path)


def test_manifest_update_merges_existing_entries(tmp_path):
    _snapshot(tmp_path)
    update_manifest(tmp_path, raw={"download": {"path": "raw/source.csv"}})
    manifest = json.loads((tmp_path / MANIFEST_NAME).read_text())
    assert "sample" in manifest["tables"]
    assert "download" in manifest["raw"]
    assert list(tmp_path.glob(f".{MANIFEST_NAME}.*.tmp")) == []


def test_failed_manifest_replacement_preserves_original_and_cleans_temp(tmp_path, monkeypatch):
    _snapshot(tmp_path)
    manifest_path = tmp_path / MANIFEST_NAME
    original = manifest_path.read_bytes()

    def fail_replace(self, target):
        # The complete original remains readable until the replacement occurs.
        assert manifest_path.read_bytes() == original
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="replacement failure"):
        update_manifest(tmp_path, raw={"download": {"path": "raw/source.csv"}})
    assert manifest_path.read_bytes() == original
    assert list(tmp_path.glob(f".{MANIFEST_NAME}.*.tmp")) == []
