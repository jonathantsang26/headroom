"""Historical input pins identify the exact CSV bytes parsed by ingestion."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from headroom.ingest.load import _load_live, _load_synthetic, ingest_region
from headroom.ingest.region import RegionConfig, get_region
from headroom.ingest.snapshot import MANIFEST_NAME, REQUIRED_TABLES, Snapshot, sha256_of, update_manifest
from headroom.provenance.lineage import LineageStore

FIXTURES = Path(__file__).parent / "fixtures" / "spp_synth"


@pytest.fixture(params=["synthetic", "snapshot"])
def input_case(request, tmp_path):
    root = tmp_path / "snapshot-v1"
    synthetic = request.param == "synthetic"
    data = root if synthetic else root / "normalized"
    shutil.copytree(FIXTURES, data)
    if not synthetic:
        update_manifest(root, tables={table: {
            # These live-loader mechanics tests use fabricated CSVs and never
            # export them. Keep their source explicitly synthetic nevertheless.
            "source": "synthetic_fixture",
            "source_version": "test_v1",
            "retrieved_at": "2026-07-01T12:00:00+00:00",
            "sha256": sha256_of(data / f"{table}.csv"),
        } for table in REQUIRED_TABLES})
    region = RegionConfig("identity-test", "SPP", root, synthetic)
    loader = _load_synthetic if synthetic else _load_live
    return region, loader


def test_input_identity_contains_all_exact_file_digests_and_no_absolute_paths(input_case):
    region, loader = input_case
    identity = loader(region, LineageStore()).input_identity

    assert identity["schema_version"] == 1
    assert identity["kind"] == ("synthetic" if region.synthetic else "snapshot")
    assert identity["region"] == region.name
    assert identity["rto"] == region.rto
    assert set(identity["tables"]) == set(REQUIRED_TABLES)
    for record in identity["tables"].values():
        assert not Path(record["path"]).is_absolute()
        assert record["sha256"] == sha256_of(region.data_dir / record["path"])
    assert str(region.data_dir) not in json.dumps(identity)
    if not region.synthetic:
        assert identity["snapshot_id"] == region.data_dir.name
        assert identity["snapshot_manifest_sha256"] == sha256_of(region.data_dir / MANIFEST_NAME)


def test_matching_identity_survives_json_roundtrip(input_case):
    region, loader = input_case
    original = loader(region, LineageStore())
    pins = json.loads(json.dumps(original.input_identity))
    replay = loader(region, LineageStore(), expected_inputs=pins)
    assert replay.input_identity == original.input_identity
    assert replay.congestion == original.congestion


@pytest.mark.parametrize("mutation", ["table", "digest", "extra", "schema_bool", "schema_float", "region"])
def test_missing_or_changed_expected_pins_rejected_before_lineage(input_case, mutation):
    region, loader = input_case
    pins = copy.deepcopy(loader(region, LineageStore()).input_identity)
    if mutation == "table":
        del pins["tables"]["load_930"]
    elif mutation == "digest":
        del pins["tables"]["load_930"]["sha256"]
    elif mutation == "extra":
        pins["unexpected"] = "field"
    elif mutation == "schema_bool":
        pins["schema_version"] = True
    elif mutation == "schema_float":
        pins["schema_version"] = 1.0
    else:
        pins["region"] = "another-region"
    store = LineageStore()

    with pytest.raises(ValueError, match="changed inputs or missing pins"):
        loader(region, store, expected_inputs=pins)
    assert len(store) == 0


def test_non_dict_expected_inputs_rejected_before_lineage(input_case):
    region, loader = input_case
    store = LineageStore()
    with pytest.raises(ValueError, match="input identity"):
        loader(region, store, expected_inputs=[])
    assert len(store) == 0


def test_changed_input_rejected_before_lineage_even_with_updated_snapshot_hash(input_case):
    region, loader = input_case
    pins = loader(region, LineageStore()).input_identity
    record = pins["tables"]["load_930"]
    path = region.data_dir / record["path"]
    path.write_bytes(path.read_bytes() + b"\n")
    if not region.synthetic:
        manifest = json.loads((region.data_dir / MANIFEST_NAME).read_text())
        manifest["tables"]["load_930"]["sha256"] = sha256_of(path)
        (region.data_dir / MANIFEST_NAME).write_text(json.dumps(manifest))
    store = LineageStore()

    with pytest.raises(ValueError, match="load_930"):
        loader(region, store, expected_inputs=pins)
    assert len(store) == 0


def test_parser_and_identity_use_the_same_single_read_buffer(input_case, monkeypatch):
    region, loader = input_case
    data = region.data_dir if region.synthetic else region.data_dir / "normalized"
    path = data / "load_930.csv"
    original_bytes = path.read_bytes()
    read_bytes = Path.read_bytes
    reads = 0

    def read_then_change_file(self):
        nonlocal reads
        content = read_bytes(self)
        if self == path:
            reads += 1
            self.write_bytes(b"corrupted after the read")
        return content

    monkeypatch.setattr(Path, "read_bytes", read_then_change_file)
    bundle = loader(region, LineageStore())

    assert reads == 1
    assert bundle.input_identity["tables"]["load_930"]["sha256"] == hashlib.sha256(original_bytes).hexdigest()
    assert bundle.load_930 and "ba" in bundle.load_930[0]


def test_ingest_region_data_override_preserves_registered_config(tmp_path):
    root = tmp_path / "copied-fixtures"
    shutil.copytree(FIXTURES, root)
    registered = get_region("spp-synth")
    bundle = ingest_region("spp-synth", LineageStore(), data_dir=root)
    replay = ingest_region(
        "spp-synth", LineageStore(), data_dir=root,
        expected_inputs=bundle.input_identity,
    )

    assert bundle.region.data_dir == root
    assert get_region("spp-synth") is registered
    assert get_region("spp-synth").data_dir != root
    assert replay.input_identity == bundle.input_identity


def test_synthetic_table_cannot_escape_input_root(tmp_path):
    root = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, root)
    outside = tmp_path / "outside.csv"
    path = root / "load_930.csv"
    path.rename(outside)
    path.symlink_to(outside)
    store = LineageStore()

    with pytest.raises(ValueError, match="escapes"):
        ingest_region("spp-synth", store, data_dir=root)
    assert len(store) == 0


def test_snapshot_digest_identifies_the_exact_manifest_bytes_parsed(tmp_path, monkeypatch):
    root = tmp_path / "snapshot"
    update_manifest(root)
    path = root / MANIFEST_NAME
    original = path.read_bytes()
    read_bytes = Path.read_bytes

    def read_then_change_manifest(self):
        content = read_bytes(self)
        if self == path:
            self.write_bytes(b"not valid JSON")
        return content

    monkeypatch.setattr(Path, "read_bytes", read_then_change_manifest)
    snapshot = Snapshot(root)
    assert snapshot.manifest_sha256 == hashlib.sha256(original).hexdigest()
    assert snapshot.manifest == json.loads(original)
