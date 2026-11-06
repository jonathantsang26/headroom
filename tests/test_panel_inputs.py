"""Panel inputs fail closed: stamps, hashes, raw citations, table contract, footprint.

Every snapshot here is a copy of the fabricated fixture; nothing is fetched.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from headroom.ingest.snapshot import MANIFEST_NAME, sha256_of
from headroom.panel.footprint import FOOTPRINT_COLUMNS, parse_footprint
from headroom.panel.load import PanelRegion, load_panel
from headroom.panel.tables import write_panel_table
from headroom.provenance.lineage import LineageStore
from headroom.provenance.real import SourceStamp

FIXTURES = Path(__file__).parent / "fixtures"
SNAPSHOT = FIXTURES / "spp_synth_panel"
FOOTPRINT = FIXTURES / "panel_footprint.csv"
HEADER = ",".join(FOOTPRINT_COLUMNS)
VERIFIED = "SITE_A,site,FLOWGATE_1,COR_1,verified,evidence,ref,reviewer,2026-09-25"


@pytest.fixture
def snap(tmp_path):
    path = tmp_path / "snapshot"
    shutil.copytree(SNAPSHOT, path)
    return path


def _region(path: Path) -> PanelRegion:
    return PanelRegion("panel-test", "SPP", path)


def _rejects(path: Path, match: str) -> None:
    store = LineageStore()
    with pytest.raises((ValueError, KeyError, FileNotFoundError), match=match):
        load_panel(_region(path), store)
    assert len(store) == 0  # nothing entered lineage


def _manifest(path: Path) -> dict:
    return json.loads((path / MANIFEST_NAME).read_text())


def _save(path: Path, manifest: dict) -> None:
    (path / MANIFEST_NAME).write_text(json.dumps(manifest))


def _edit_table(path: Path, table: str, old: str, new: str) -> None:
    """Change a normalized table and re-record its hash, isolating the check under test."""
    csv_path = path / "normalized" / f"{table}.csv"
    text = csv_path.read_text()
    assert old in text
    csv_path.write_text(text.replace(old, new, 1))
    manifest = _manifest(path)
    manifest["tables"][table]["sha256"] = sha256_of(csv_path)
    _save(path, manifest)


def test_fixture_snapshot_loads(snap):
    store = LineageStore()
    inputs = load_panel(_region(snap), store)
    assert len(store) > 0
    assert inputs.raw["raw:da_2025"]["path"] == "raw/da_2025.csv"


@pytest.mark.parametrize("value", [None, [], "raw:da_2025", ["raw:da_2025", "raw:da_2025"], [""]])
def test_tables_must_name_distinct_raw_inputs(snap, value):
    manifest = _manifest(snap)
    if value is None:
        del manifest["tables"]["constraint_coverage"]["derived_from"]
    else:
        manifest["tables"]["constraint_coverage"]["derived_from"] = value
    _save(snap, manifest)
    _rejects(snap, "derived_from")


def test_raw_input_must_be_recorded(snap):
    manifest = _manifest(snap)
    del manifest["raw"]["raw:rt_2025"]
    _save(snap, manifest)
    _rejects(snap, "raw:rt_2025")


def test_raw_bytes_must_match_recorded_hash(snap):
    (snap / "raw" / "da_2025.csv").write_text("tampered\n")
    _rejects(snap, "raw:da_2025")


def test_missing_raw_file_is_rejected(snap):
    (snap / "raw" / "rt_2025.csv").unlink()
    _rejects(snap, "raw:rt_2025")


@pytest.mark.parametrize("path", ["../outside.csv", "/etc/hosts", "", "raw/\x00.csv"])
def test_raw_path_must_stay_inside_snapshot(snap, path):
    manifest = _manifest(snap)
    manifest["raw"]["raw:da_2025"]["path"] = path
    _save(snap, manifest)
    _rejects(snap, "raw:da_2025")


@pytest.mark.parametrize(
    "field,value",
    [("url", ""), ("url", None), ("retrieved_at", "2026-09-25T00:00:00"),
     ("retrieved_at", "yesterday"), ("sha256", "abc"), ("sha256", None)],
)
def test_raw_entry_needs_url_timestamp_and_hash(snap, field, value):
    manifest = _manifest(snap)
    if value is None:
        del manifest["raw"]["raw:da_2025"][field]
    else:
        manifest["raw"]["raw:da_2025"][field] = value
    _save(snap, manifest)
    _rejects(snap, "raw:da_2025")


def test_coverage_rows_may_cite_only_declared_raw_inputs(snap):
    manifest = _manifest(snap)
    manifest["tables"]["constraint_coverage"]["derived_from"] = ["raw:da_2025"]
    manifest["tables"]["constraint_history"]["derived_from"] = ["raw:da_2025"]
    _save(snap, manifest)
    _rejects(snap, "cites raw inputs missing")


def test_history_raw_inputs_must_be_covered(snap):
    manifest = _manifest(snap)
    manifest["tables"]["constraint_coverage"]["derived_from"] = ["raw:rt_2025"]
    _save(snap, manifest)
    _rejects(snap, "does not cover")


def test_table_hash_mismatch_is_rejected(snap):
    table = snap / "normalized" / "constraint_history.csv"
    table.write_text(table.read_text().replace("42.0", "420.0", 1))
    _rejects(snap, "sha256")


def test_table_needs_a_source_stamp(snap):
    manifest = _manifest(snap)
    manifest["tables"]["constraint_history"]["source"] = ""
    _save(snap, manifest)
    _rejects(snap, "nonempty source")


@pytest.mark.parametrize(
    "table,old,new,match",
    [
        ("constraint_history", "FLOWGATE_1,DA,2025-01,42.0", "FLOWGATE_1,DA,2025-11,42.0",
         "no covered days"),
        ("constraint_history", "FLOWGATE_1,DA,2025-01,42.0", "FLOWGATE_1,DA,2025-01,745.5",
         "covered days"),
        ("constraint_history", "FLOWGATE_1,DA,2025-01,42.0", "FLOWGATE_1,DA,2025-02,42.0",
         "repeats"),
        ("constraint_history", "FLOWGATE_1,DA,2025-01,42.0", "FLOWGATE_1,DA,2025-01,0",
         "must be positive"),
        ("constraint_history", "42.0,180.5,35.2", "42.0,180.5,-35.2", "cannot be negative"),
        ("constraint_history", "42.0,180.5,35.2", "42.0,30.5,35.2", "exceeds"),
        ("constraint_history", "42.0,180.5", "1_000,180.5", "plain decimal"),
        ("constraint_history", "42.0,180.5", "nan,180.5", "plain decimal"),
        ("constraint_history", "42.0,180.5", "1e999,180.5", "finite"),
        ("constraint_history", "42.0,180.5", " 42.0,180.5", "plain decimal"),
        ("constraint_history", "FLOWGATE_1,DA,2025-01", "FLOWGATE_1,ID,2025-01", "market"),
        ("constraint_history", "FLOWGATE_1,DA,2025-01", "FLOWGATE_1,DA,2025-1", "YYYY-MM"),
        ("constraint_history", "FLOWGATE_1,DA,2025-01", " FLOWGATE_1,DA,2025-01", "whitespace"),
        ("constraint_history", "hours_binding,", "hours,", "exactly the columns"),
        ("constraint_history", "FLOWGATE_1,DA,2025-01,42.0,", "FLOWGATE_1,DA,2025-01,42.0,,,",
         "wrong number of fields"),
        ("constraint_coverage", "DA,2025-02,28,28", "DA,2025-02,29,29", "days_expected"),
        ("constraint_coverage", "DA,2025-02,28,28", "DA,2025-02,28,29", "exceeds"),
        ("constraint_coverage", "DA,2025-02,28,28,raw:da_2025", "DA,2025-02,28,28,",
         "cites no raw input"),
        ("constraint_coverage", "DA,2025-02,28,28,raw:da_2025",
         "DA,2025-02,28,28,raw:da_2025;raw:da_2025", "distinct"),
        ("constraint_coverage", "DA,2025-02,", "DA,2025-01,", "repeats"),
    ],
)
def test_table_contract_violations_are_rejected(snap, table, old, new, match):
    _edit_table(snap, table, old, new)
    _rejects(snap, match)


def test_writer_validates_before_writing(tmp_path):
    stamp = SourceStamp("synthetic_fixture", "synthetic", datetime(2026, 9, 25, tzinfo=timezone.utc))
    bad = [{"market": "DA", "period": "2025-02", "days_expected": 28, "days_covered": 30,
            "raw_refs": "raw:x"}]
    with pytest.raises(ValueError, match="exceeds"):
        write_panel_table(tmp_path, "constraint_coverage", bad, stamp=stamp, derived_from=["raw:x"])
    assert not (tmp_path / "normalized" / "constraint_coverage.csv").exists()
    with pytest.raises(ValueError, match="raw inputs"):
        write_panel_table(tmp_path, "constraint_coverage", [], stamp=stamp, derived_from=[])
    with pytest.raises(ValueError, match="Unknown panel table"):
        write_panel_table(tmp_path, "congestion", [], stamp=stamp, derived_from=["raw:x"])


def test_failed_write_keeps_the_prior_table(tmp_path, monkeypatch):
    stamp = SourceStamp("synthetic_fixture", "synthetic", datetime(2026, 9, 25, tzinfo=timezone.utc))
    feb = {"market": "DA", "period": "2025-02", "days_expected": 28, "days_covered": 28,
           "raw_refs": "raw:x"}
    mar = {**feb, "period": "2025-03", "days_expected": 31, "days_covered": 31}
    path = write_panel_table(tmp_path, "constraint_coverage", [feb], stamp=stamp,
                             derived_from=["raw:x"])
    before = path.read_bytes()

    def disk_full(self, target):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", disk_full)
    with pytest.raises(OSError, match="disk full"):
        write_panel_table(tmp_path, "constraint_coverage", [feb, mar], stamp=stamp,
                          derived_from=["raw:x"])
    assert path.read_bytes() == before
    assert _manifest(tmp_path)["tables"]["constraint_coverage"]["sha256"] == sha256_of(path)


def _footprint(*rows: str, header: str = HEADER):
    return parse_footprint("\n".join((header, *rows)).encode("utf-8"), name="fp.csv")


def test_footprint_fixture_parses():
    fp = parse_footprint(FOOTPRINT.read_bytes(), name=FOOTPRINT.name)
    assert sorted(fp.verified()) == ["FLOWGATE_1", "FLOWGATE_3", "FLOWGATE_4", "FLOWGATE_6", "FLOWGATE_9"]
    assert len(fp.unresolved()) == 2


def test_footprint_accepts_spreadsheet_bom():
    fp = parse_footprint(("﻿" + HEADER + "\n" + VERIFIED).encode("utf-8"), name="fp.csv")
    assert list(fp.verified()) == ["FLOWGATE_1"]


@pytest.mark.parametrize(
    "row,match",
    [
        ("SITE_A,site,FLOWGATE_1,COR_1,verified,,ref,reviewer,2026-09-25", "lacks evidence"),
        ("SITE_A,site,FLOWGATE_1,COR_1,verified,evidence,ref,,2026-09-25", "lacks reviewed_by"),
        ("SITE_A,site,,COR_1,verified,evidence,ref,reviewer,2026-09-25", "lacks constraint_id"),
        ("SITE_A,site,FLOWGATE_1,COR_1,verified,evidence,ref,reviewer,09/25/2026", "YYYY-MM-DD"),
        ("SITE_A,site,FLOWGATE_1,COR_1,verified,evidence,ref,reviewer,2026-9-25", "YYYY-MM-DD"),
        ("SITE_A,site,FLOWGATE_1,,uncertain,,,,", "uncertain but records no evidence"),
        ("NODE_B,settlement_location,FLOWGATE_1,,unmatched,,,,", "unmatched but names"),
        ("SITE_A,campus,FLOWGATE_1,,uncertain,why,,,", "element_kind"),
        ("SITE_A,site,FLOWGATE_1,,probable,why,,,", "match_status"),
        (",site,FLOWGATE_1,,uncertain,why,,,", "element_id"),
        ("SITE_A,site,FLOWGATE_1,COR_1,verified,evidence,ref,reviewer", "wrong number of fields"),
    ],
)
def test_footprint_rows_fail_closed(row, match):
    with pytest.raises(ValueError, match=match):
        _footprint(row)


def test_footprint_rejects_duplicates_and_conflicting_corridors():
    with pytest.raises(ValueError, match="repeats"):
        _footprint(VERIFIED, VERIFIED)
    with pytest.raises(ValueError, match="another row verified COR_1"):
        _footprint(VERIFIED, "SITE_B,site,FLOWGATE_1,COR_9,verified,evidence,ref,reviewer,2026-09-25")


def test_footprint_requires_contract_columns_and_rows():
    with pytest.raises(ValueError, match="exactly the columns"):
        _footprint(VERIFIED, header=HEADER.replace("reviewed_on", "date"))
    with pytest.raises(ValueError, match="no rows"):
        _footprint()
