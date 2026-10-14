"""Snapshot activation never displaces good data with an incomplete build."""

import shutil
from pathlib import Path

import pytest

from headroom.ingest.adapters import build_snapshot as builder
from headroom.ingest.snapshot import REQUIRED_TABLES, sha256_of, update_manifest

FIXTURES = Path(__file__).parent / "fixtures" / "spp_synth"
DAY = "2026-09-23"


def _complete(snapshot):
    (snapshot / "normalized").mkdir(parents=True, exist_ok=True)
    tables = {}
    for table in REQUIRED_TABLES:
        target = snapshot / "normalized" / f"{table}.csv"
        shutil.copy(FIXTURES / f"{table}.csv", target)
        tables[table] = {
            "source": "synthetic_fixture",
            "source_version": "test-v1",
            "retrieved_at": "2026-09-23T00:00:00+00:00",
            "sha256": sha256_of(target),
        }
    update_manifest(snapshot, tables=tables)


@pytest.fixture
def root(tmp_path, monkeypatch):
    root = tmp_path / "spp"
    root.mkdir()
    old = root / "2026-09-22"
    _complete(old)
    (root / "current").symlink_to(old.name)
    monkeypatch.setattr(builder, "SPP_ROOT", root)

    def no_network(*args, **kwargs):
        raise AssertionError("Tests must never fetch real source data")

    monkeypatch.setattr("urllib.request.urlopen", no_network)
    return root


def _set_adapter(monkeypatch, *, fetch=None, normalize=None):
    monkeypatch.setattr(builder, "ADAPTERS", [(
        "offline-test", fetch or (lambda _: {"bytes": 0}),
        normalize or _complete,
    )])


def test_completed_build_is_promoted(root, monkeypatch):
    _set_adapter(monkeypatch)
    assert builder.main(["--date", DAY]) == 0
    assert (root / "current").resolve() == root / DAY
    assert (root / DAY / "snapshot_manifest.json").is_file()
    assert not list(root.glob(f".{DAY}-*"))


@pytest.mark.parametrize("phase", ["fetch", "normalize"])
def test_failed_adapter_preserves_current(root, monkeypatch, phase):
    def fail(_):
        raise RuntimeError("interrupted")

    _set_adapter(monkeypatch, **{phase: fail})
    assert builder.main(["--date", DAY]) == 1
    assert (root / "current").readlink() == Path("2026-09-22")
    assert not (root / DAY).exists()
    assert len(list(root.glob(f".{DAY}-*"))) == 1


def test_unimplemented_normalizer_is_failure(root, monkeypatch):
    def unavailable(_):
        raise NotImplementedError("pending adapter")

    _set_adapter(monkeypatch, normalize=unavailable)
    assert builder.main(["--date", DAY]) == 1
    assert (root / "current").readlink() == Path("2026-09-22")


def test_empty_normalization_is_not_promoted(root, monkeypatch):
    _set_adapter(monkeypatch, normalize=lambda _: None)
    assert builder.main(["--date", DAY]) == 1
    assert (root / "current").readlink() == Path("2026-09-22")


def test_existing_snapshot_is_never_refetched(root, monkeypatch):
    _complete(root / DAY)
    marker = root / DAY / "keep.txt"
    marker.write_text("original")

    def forbidden(_):
        pytest.fail("existing snapshot must not be modified")

    _set_adapter(monkeypatch, fetch=forbidden)
    assert builder.main(["--date", DAY]) == 1
    assert marker.read_text() == "original"
    assert (root / "current").readlink() == Path("2026-09-22")


def test_activate_only_validates_without_fetching(root, monkeypatch):
    _complete(root / DAY)

    def forbidden(_):
        pytest.fail("activation must not download")

    _set_adapter(monkeypatch, fetch=forbidden)
    assert builder.main(["--date", DAY, "--activate-only"]) == 0
    assert (root / "current").readlink() == Path(DAY)


def test_activate_only_rejects_changed_file(root):
    _complete(root / DAY)
    (root / DAY / "normalized" / "congestion.csv").write_text("changed")
    assert builder.main(["--date", DAY, "--activate-only"]) == 1
    assert (root / "current").readlink() == Path("2026-09-22")


@pytest.mark.parametrize("value", ["../escape", "current", "20260923", "2026-02-30"])
def test_invalid_date_has_no_side_effects(root, value):
    before = set(root.iterdir())
    with pytest.raises(SystemExit) as exc:
        builder.main(["--date", value])
    assert exc.value.code == 2
    assert set(root.iterdir()) == before


def test_failed_symlink_replacement_keeps_old_pointer(root, monkeypatch):
    _complete(root / DAY)

    def fail(*_):
        raise OSError("replacement failed")

    monkeypatch.setattr(builder.os, "replace", fail)
    assert builder.main(["--date", DAY, "--activate-only"]) == 1
    assert (root / "current").readlink() == Path("2026-09-22")
    assert not list(root.glob(".current-*"))


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_activation_preserves_non_symlink_current(root, kind):
    _complete(root / DAY)
    current = root / "current"
    current.unlink()
    if kind == "file":
        current.write_text("keep")
    else:
        current.mkdir()
    assert builder.main(["--date", DAY, "--activate-only"]) == 1
    assert not current.is_symlink()
    assert current.exists()


def test_activation_rejects_symlink_snapshot(root):
    (root / DAY).symlink_to("2026-09-22")
    assert builder.main(["--date", DAY, "--activate-only"]) == 1
    assert (root / "current").readlink() == Path("2026-09-22")


def test_activation_replaces_dangling_current(root):
    _complete(root / DAY)
    current = root / "current"
    current.unlink()
    current.symlink_to("missing")
    assert builder.main(["--date", DAY, "--activate-only"]) == 0
    assert current.readlink() == Path(DAY)


def test_same_day_rebuild_is_a_revision_and_never_touches_the_day(root, monkeypatch):
    _set_adapter(monkeypatch)
    assert builder.main(["--date", DAY]) == 0
    first = (root / DAY / "snapshot_manifest.json").read_bytes()
    assert builder.main(["--date", DAY, "--revision", "2"]) == 0
    assert (root / f"{DAY}.r2" / "snapshot_manifest.json").is_file()
    assert (root / "current").readlink() == Path(f"{DAY}.r2")
    assert (root / DAY / "snapshot_manifest.json").read_bytes() == first
    import json
    assert json.loads((root / f"{DAY}.r2" / "snapshot_manifest.json").read_text())["revision"] == 2
    assert not list(root.glob(f".{DAY}*"))


def test_existing_day_names_the_next_free_revision(root, monkeypatch, capsys):
    _set_adapter(monkeypatch)
    _complete(root / DAY)
    _complete(root / f"{DAY}.r2")
    assert builder.main(["--date", DAY]) == 1
    assert "--revision 3" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["1", "0", "x"])
def test_invalid_revision_has_no_side_effects(root, value):
    before = set(root.iterdir())
    with pytest.raises(SystemExit) as exc:
        builder.main(["--date", DAY, "--revision", value])
    assert exc.value.code == 2
    assert set(root.iterdir()) == before


def test_no_activate_leaves_current(root, monkeypatch):
    _set_adapter(monkeypatch)
    assert builder.main(["--date", DAY, "--no-activate"]) == 0
    assert (root / DAY).is_dir()
    assert (root / "current").readlink() == Path("2026-09-22")
    assert builder.main(["--date", DAY, "--activate-only"]) == 0
    assert (root / "current").readlink() == Path(DAY)


def _with_raw(snapshot, payload=b"pinned bytes"):
    import json

    raw = snapshot / "raw" / "source.bin"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(payload)
    update_manifest(snapshot, raw={"raw:source": {
        "url": "https://example.invalid/source.bin", "path": "raw/source.bin",
        "sha256": sha256_of(raw), "retrieved_at": "2026-09-22T00:00:00+00:00",
        "bytes": len(payload)}})
    mpath = snapshot / "snapshot_manifest.json"
    m = json.loads(mpath.read_text())
    m["snapshot_date"] = snapshot.name  # a real snapshot's date matches its directory
    mpath.write_text(json.dumps(m))


def test_renormalize_reuses_raw_bytes_into_a_revision(root, monkeypatch):
    _with_raw(root / "2026-09-22")
    seen = []

    def fetch(_):
        raise AssertionError("re-normalize never fetches")

    def normalize(staged):
        seen.append((staged / "raw" / "source.bin").read_bytes())
        _complete(staged)

    _set_adapter(monkeypatch, fetch=fetch, normalize=normalize)
    assert builder.main(["--renormalize-from", "2026-09-22", "--revision", "2"]) == 0
    new = root / "2026-09-22.r2"
    import json

    m = json.loads((new / "snapshot_manifest.json").read_text())
    old = json.loads((root / "2026-09-22" / "snapshot_manifest.json").read_text())
    assert seen == [b"pinned bytes"]
    assert m["revision"] == 2 and m["raw"] == old["raw"]
    assert m["renormalized_from"]["snapshot"] == "2026-09-22"
    assert (root / "current").resolve() == new


def test_renormalize_refuses_changed_raw_bytes_and_needs_a_revision(root, monkeypatch):
    _with_raw(root / "2026-09-22")
    _set_adapter(monkeypatch)
    assert builder.main(["--renormalize-from", "2026-09-22"]) == 1  # no --revision
    (root / "2026-09-22" / "raw" / "source.bin").write_bytes(b"tampered")
    assert builder.main(["--renormalize-from", "2026-09-22", "--revision", "2"]) == 1
    assert not (root / "2026-09-22.r2").exists()
    assert (root / "current").readlink() == Path("2026-09-22")
    assert builder.main(["--renormalize-from", "2026-09-22", "--date", DAY,
                         "--revision", "2"]) == 1  # the date is the source's


def test_an_interrupted_build_removes_its_staging_directory(root, monkeypatch):
    def interrupted(_):
        raise KeyboardInterrupt

    _set_adapter(monkeypatch, fetch=interrupted)
    assert builder.main(["--date", DAY]) == 130
    assert not list(root.glob(f".{DAY}-*")) and not (root / DAY).exists()
    assert (root / "current").readlink() == Path("2026-09-22")


def test_sigterm_removes_staging_and_stale_staging_can_be_cleaned(root, monkeypatch):
    import os
    import signal

    def terminated(_):
        os.kill(os.getpid(), signal.SIGTERM)  # handled by the builder like Ctrl-C

    _set_adapter(monkeypatch, fetch=terminated)
    assert builder.main(["--date", DAY]) == 130
    assert not list(root.glob(f".{DAY}-*"))
    assert signal.getsignal(signal.SIGTERM) is not builder._terminate  # restored

    (root / f".{DAY}-killed").mkdir()  # what SIGKILL leaves
    (root / ".current-tmp").mkdir()  # not a staging directory
    assert builder._stale_staging() == [root / f".{DAY}-killed"]
    assert builder.main(["--clean-staging"]) == 0
    assert not (root / f".{DAY}-killed").exists() and (root / ".current-tmp").exists()
