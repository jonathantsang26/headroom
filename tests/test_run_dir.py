"""The map's finished-run source (A33): a run directory is read and checked against
its manifest's sha256s, never re-run. Offline: the run is a fresh spp-synth export."""

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from headroom.app.map_data import (
    GET_FALLBACK,
    GET_UNRESOLVED,
    corridor_segments,
    run_corridor_detail,
    run_segments,
    shown_from_row,
)
from headroom.app.run_dir import RunDirError, list_run_dirs, load_run_dir
from headroom.export import deliver
from headroom.pipeline import run_pipeline
from headroom.provenance.digests import ranking_digest

MANIFEST = "run_manifest.INTERNAL.synthetic.json"


@pytest.fixture(scope="module")
def result():
    return run_pipeline("spp-synth")


@pytest.fixture(scope="module")
def exported(result, tmp_path_factory):
    out = tmp_path_factory.mktemp("runs") / "spp-synth"
    deliver(result, out)
    return out


@pytest.fixture
def run_dir(exported, tmp_path):
    """A private copy each test may tamper with."""
    return Path(shutil.copytree(exported, tmp_path / "spp-synth"))


def _manifest(run_dir: Path) -> dict:
    return json.loads((run_dir / MANIFEST).read_text())


def _write_manifest(run_dir: Path, manifest: dict) -> None:
    (run_dir / MANIFEST).write_text(json.dumps(manifest))


def _rewrite_recorded(run_dir: Path, key: str, blob: bytes) -> None:
    """Replace a recorded file and record its new digest, so only its content is wrong."""
    manifest = _manifest(run_dir)
    (run_dir / manifest["outputs"][key]["path"]).write_bytes(blob)
    manifest["outputs"][key]["sha256"] = hashlib.sha256(blob).hexdigest()
    _write_manifest(run_dir, manifest)


def test_loads_the_run_it_was_written_from(run_dir, result):
    run = load_run_dir(run_dir)
    assert run.mode == "internal-synthetic"
    assert run.n_constraints == result.model.n_constraints == len(run.scores)
    assert len(run.shortlist) == len(result.model.shortlist)
    assert run.shortlist_k == result.model.k
    assert run.output_sha256 == ranking_digest(result)
    assert run.region == "spp-synth"
    # every recorded output was checked, parquet included where DuckDB wrote it
    assert set(run.digests) == set(_manifest(run_dir)["outputs"])
    assert {"scores_json", "shortlist_json", "csv", "quality_report", "notice"} <= set(run.digests)


def test_a_tampered_file_is_refused(run_dir):
    shortlist = run_dir / "shortlist.INTERNAL.synthetic.json"
    rows = json.loads(shortlist.read_text())
    rows[0]["p_top_k"] = 1.0  # still valid JSON, still the right shape
    shortlist.write_text(json.dumps(rows))
    with pytest.raises(RunDirError, match=r"differ from the sha256s.*'shortlist_json'"):
        load_run_dir(run_dir)


@pytest.mark.parametrize("key", ["scores_json", "shortlist_json", "csv", "quality_report",
                                 "notice", "parquet"])
def test_every_recorded_file_is_checked_not_only_the_displayed_ones(run_dir, key):
    entry = _manifest(run_dir)["outputs"].get(key)
    assert entry is not None, f"the export records no {key}"
    with (run_dir / entry["path"]).open("ab") as f:
        f.write(b"\n")
    with pytest.raises(RunDirError, match=rf"differ from the sha256s.*'{key}'"):
        load_run_dir(run_dir)


@pytest.mark.parametrize("blob, match", [
    (b'[{"physical_corridor": ', "shortlist.INTERNAL.synthetic.json is not valid JSON"),
    (b"\xff\xfe", "is not valid JSON"),
    (b'{"rows": []}', "is not a list of rows"),
    (b"[1, 2]", "is not a list of rows"),
])
def test_a_recorded_file_that_is_not_rows_is_refused_though_its_digest_matches(
        run_dir, blob, match):
    _rewrite_recorded(run_dir, "shortlist_json", blob)
    with pytest.raises(RunDirError, match=match):
        load_run_dir(run_dir)


def test_a_corridor_row_missing_what_the_map_reads_is_refused(run_dir):
    rows = json.loads((run_dir / "shortlist.INTERNAL.synthetic.json").read_text())
    del rows[1]["provenance"]
    _rewrite_recorded(run_dir, "shortlist_json", json.dumps(rows).encode())
    with pytest.raises(RunDirError, match=r"row 1 has no \['provenance'\]"):
        load_run_dir(run_dir)


def test_a_missing_recorded_file_is_refused(run_dir):
    (run_dir / "scores.INTERNAL.synthetic.json").unlink()
    with pytest.raises(RunDirError, match="scores_json.*missing"):
        load_run_dir(run_dir)


def test_a_manifest_with_no_digests_is_refused(run_dir):
    manifest = _manifest(run_dir)
    del manifest["outputs"]
    _write_manifest(run_dir, manifest)
    with pytest.raises(RunDirError, match="no output digests"):
        load_run_dir(run_dir)


def test_a_manifest_missing_a_displayed_file_is_refused(run_dir):
    manifest = _manifest(run_dir)
    del manifest["outputs"]["shortlist_json"]
    _write_manifest(run_dir, manifest)
    with pytest.raises(RunDirError, match="no digest for.*shortlist_json"):
        load_run_dir(run_dir)


def test_a_mode_its_file_name_contradicts_is_refused(run_dir):
    # a synthetic run relabelled shareable, under the synthetic manifest's name
    manifest = _manifest(run_dir)
    manifest["mode"] = "shareable"
    _write_manifest(run_dir, manifest)
    with pytest.raises(RunDirError, match="records mode 'shareable'"):
        load_run_dir(run_dir)
    # and under the shareable name, with the digests left as they were
    manifest["mode"] = "internal-synthetic"
    _write_manifest(run_dir, manifest)
    os.replace(run_dir / MANIFEST, run_dir / "run_manifest.json")
    with pytest.raises(RunDirError, match="records mode 'internal-synthetic'"):
        load_run_dir(run_dir)


def test_two_manifests_are_refused(run_dir):
    shutil.copy(run_dir / MANIFEST, run_dir / "run_manifest.json")
    with pytest.raises(RunDirError, match="holds 2 run manifests"):
        load_run_dir(run_dir)


@pytest.mark.parametrize("path", ["../outside.json", "/etc/hosts", "sub/scores.json", ""])
def test_a_recorded_path_outside_the_directory_is_refused(run_dir, path):
    manifest = _manifest(run_dir)
    manifest["outputs"]["csv"]["path"] = path
    _write_manifest(run_dir, manifest)
    with pytest.raises(RunDirError, match="not a file in"):
        load_run_dir(run_dir)


def test_a_flowgate_count_the_manifest_contradicts_is_refused(run_dir):
    manifest = _manifest(run_dir)
    manifest["n_constraints"] += 1
    _write_manifest(run_dir, manifest)
    with pytest.raises(RunDirError, match="the manifest records"):
        load_run_dir(run_dir)


def test_list_run_dirs_only_lists_directories_with_a_manifest(run_dir, tmp_path):
    (tmp_path / "empty").mkdir()
    (tmp_path / "loose.json").write_text("{}")
    assert list_run_dirs(tmp_path) == [run_dir]
    assert list_run_dirs(tmp_path / "nope") == []
    assert list_run_dirs(tmp_path / "loose.json") == []


def test_a_runs_folder_that_is_itself_a_run_is_listed_first(run_dir, tmp_path):
    # `headroom run --out data/processed` (the CLI default) writes into the folder itself
    for f in run_dir.iterdir():
        shutil.copy(f, tmp_path / f.name)
    assert list_run_dirs(tmp_path) == [tmp_path, run_dir]
    assert load_run_dir(tmp_path).n_constraints == load_run_dir(run_dir).n_constraints


def test_an_unreadable_folder_is_skipped_not_raised(run_dir, tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        assert list_run_dirs(tmp_path) == [run_dir]
    finally:
        locked.chmod(0o755)


def test_run_segments_match_the_in_process_segments(run_dir, result):
    run = load_run_dir(run_dir)
    keys = ("rank", "physical_corridor", "recommended_get", "get_shown", "get_resolved",
            "get_set", "p_top_k", "n_flowgates", "color", "width")
    from_files = [{k: s[k] for k in keys} for s in run_segments(run.shortlist)]
    in_process = [{k: s[k] for k in keys} for s in corridor_segments(result)]
    assert from_files == in_process
    assert not any(s["has_geometry"] for s in run_segments(run.shortlist))


def test_unresolved_rows_are_purple_and_old_rows_are_not_called_resolved():
    row = {"recommended_get": "Advanced Power Flow Control", "get_shown": "APFC or TO",
           "get_resolved": False,
           "get_set": "Advanced Power Flow Control;Topology Optimization"}
    assert shown_from_row(row)["get_set"] == ["Advanced Power Flow Control",
                                              "Topology Optimization"]
    seg = run_segments([{**row, "physical_corridor": "X", "representative_flowgate": "X|1",
                         "p_top_k": 0.5, "n_flowgates": 1, "composite_lo": 0,
                         "composite_expected": 0, "composite_hi": 0, "provenance": ""}])[0]
    assert seg["color"] == GET_UNRESOLVED
    # a run exported before get_shown existed: the set is unknown, not resolved, so the
    # rule's pick is labelled as such and drawn grey, not in a resolved GET's color
    old = {"recommended_get": "Dynamic Line Rating", "physical_corridor": "Y",
           "representative_flowgate": "Y|1", "p_top_k": 0.5, "n_flowgates": 1,
           "composite_lo": 0, "composite_expected": 0, "composite_hi": 0, "provenance": ""}
    shown = shown_from_row(old)
    assert shown == {"get_shown": "Dynamic Line Rating (rule's pick; GET set not recorded)",
                     "get_resolved": None, "get_set": None}
    assert run_segments([old])[0]["color"] == GET_FALLBACK


def test_run_detail_joins_the_representative_flowgate_row(run_dir):
    run = load_run_dir(run_dir)
    corridor = run.shortlist[0]
    d = run_corridor_detail(run.shortlist, run.scores, corridor["physical_corridor"])
    fg = next(s for s in run.scores if s["constraint_id"] == corridor["representative_flowgate"])
    assert d["get_basis"] == fg["get_basis"]
    assert list(d["signals"]) == ["rent", "loading", "persistence", "demand_pressure",
                                  "planning", "cost_of_inaction"]
    assert all(lo is None and hi is None for lo, _, hi in d["signals"].values())
    assert d["signals"]["rent"][1] == fg["sig_rent"]
    assert d["coverage"]["rent"] == fg["cov_rent"]
    assert run_corridor_detail(run.shortlist, run.scores, "NOPE") is None


# The recorded real SPP run (README), where `headroom run --region spp` wrote it.
REAL_RUN = Path(__file__).resolve().parents[1] / "data" / "processed" / "spp-2026-10-13-candidates"


@pytest.mark.skipif(not REAL_RUN.is_dir(), reason="the recorded real SPP run is not on this machine")
def test_the_recorded_real_spp_run_loads():
    run = load_run_dir(REAL_RUN)
    assert run.mode == "shareable"
    assert (run.n_constraints, len(run.shortlist), run.shortlist_k) == (2072, 892, 103)
    assert run.snapshot_id == "2026-10-13.r2"
    assert run.output_sha256.startswith("8bdd46ec")
    segs = run_segments(run.shortlist)
    assert not any(s["has_geometry"] for s in segs)
    assert all(s["get_resolved"] is False and s["color"] == GET_UNRESOLVED for s in segs)
    d = run_corridor_detail(run.shortlist, run.scores, segs[0]["physical_corridor"])
    assert d["ev_get"] and d["ev"] is not None
    assert abs(sum(r["p_best"] for r in d["decision"]) - 1) < 1e-3
