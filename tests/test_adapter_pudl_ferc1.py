"""PUDL / FERC Form 1 adapter on a recorded real sample (offline): pinned fetch,
cleaning, kV fixes, aggregate flags and the unambiguous-only HIFLD join."""

import csv
import io
import json
import shutil
import urllib.request
from io import BytesIO
from pathlib import Path

import pytest

from headroom.ingest.adapters import common, pudl_ferc1
from headroom.ingest.snapshot import MANIFEST_NAME, sha256_of, update_manifest

SAMPLE = Path(__file__).parent / "fixtures" / "real_samples" / "pudl_ferc1"
PARQUET = SAMPLE / "transmission_lines_sched422_sample.parquet"
URL = (
    "https://s3.us-west-2.amazonaws.com/pudl.catalyst.coop/v2024.11.0/"
    "out_ferc1__yearly_transmission_lines_sched422.parquet"
)
HIFLD_STAMP = {
    "source": "hifld_transmission",
    "source_version": "hifld_open_2023-09-05",
    "retrieved_at": "2026-10-12T00:04:00+00:00",
}
RID = "transmission_line_statistics_422_2023_"
ROSE_HILL = RID + "c001775_hhd2f7bf012e35dff29e7dd376f3a7b307"
WOODRING = RID + "c001775_hhc068ff0a26d1f4bb5be7a011bb4c5f82"
CLEVELAND = RID + "c001775_hh2e29afa9fe575a86a7c665c60de54b45"
THUNDER_RANCH = RID + "c001775_hhd8dee93108ae2c6d4e997b8fe5ac98d0"
TATONGA = RID + "c001775_hh43aee0a228026be3e9efe07f4108be58"
WOODWARD = RID + "c001775_hhef391b66eecc33cfb00a14195aed8c85"
ONETA_PREFIX = RID + "c000536_tl90902_oneta_sub_riverside_plant"
EMPIRE_345 = RID + "c001130_345_345_wood_h_frame_various"
BASIN_LRS = RID + "c003849_077"


class Response(BytesIO):
    def __init__(self, body: bytes):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("Tests must never fetch real source data")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(common.urllib.request, "urlopen", refuse)


def serve_sample(monkeypatch, seen: list):
    def urlopen(request, *, timeout):
        seen.append(request.full_url)
        return Response(PARQUET.read_bytes())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(common.urllib.request, "urlopen", urlopen)


def add_hifld(snapshot: Path, source: Path = SAMPLE / "hifld_lines.csv") -> None:
    target = snapshot / "normalized" / "hifld_lines.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(source, target)
    update_manifest(snapshot, tables={"hifld_lines": {
        **HIFLD_STAMP, "path": "normalized/hifld_lines.csv", "sha256": sha256_of(target),
        "coverage": "observed", "note": "recorded real sample",
    }})


@pytest.fixture
def fetched(tmp_path, monkeypatch):
    snapshot = tmp_path / "snap"
    update_manifest(snapshot, snapshot_date="2026-10-12")
    serve_sample(monkeypatch, [])
    pudl_ferc1.fetch(snapshot)
    return snapshot


@pytest.fixture
def built(fetched):
    add_hifld(fetched)
    pudl_ferc1.normalize(fetched)
    manifest = json.loads((fetched / MANIFEST_NAME).read_text())
    text = (fetched / "normalized" / "ferc1_lines.csv").read_text()
    rows = list(csv.DictReader(io.StringIO(text)))
    return {
        "snapshot": fetched,
        "header": text.splitlines()[0].split(","),
        "rows": {r["record_id"]: r for r in rows},
        "entry": manifest["tables"]["ferc1_lines"],
        "raw": manifest["raw"][pudl_ferc1.RAW_NAME],
    }


def test_release_comes_from_the_registry_pin():
    assert pudl_ferc1.release_from_pin("pudl_v2024.11.0") == "v2024.11.0"
    assert pudl_ferc1.release_url("v2024.11.0") == URL
    for pin in ("pudl_nightly", "pudl_stable", "v2024.11.0", "pudl_v2024.11"):
        with pytest.raises(ValueError, match="versioned PUDL release"):
            pudl_ferc1.release_from_pin(pin)


def test_fetch_downloads_the_pinned_release(tmp_path, monkeypatch):
    seen = []
    serve_sample(monkeypatch, seen)
    entry = pudl_ferc1.fetch(tmp_path)
    assert seen == [URL]
    assert entry["url"] == URL and entry["bytes"] == PARQUET.stat().st_size
    assert entry["sha256"] == sha256_of(PARQUET)
    manifest = json.loads((tmp_path / MANIFEST_NAME).read_text())
    assert manifest["raw"][pudl_ferc1.RAW_NAME]["path"] == (
        "raw/out_ferc1__yearly_transmission_lines_sched422.parquet"
    )


def test_fetch_refuses_an_unversioned_pin(tmp_path, monkeypatch):
    monkeypatch.setattr(pudl_ferc1, "registry_pin", lambda source: "pudl_nightly")
    with pytest.raises(ValueError, match="never builds on nightly"):
        pudl_ferc1.fetch(tmp_path)
    assert not (tmp_path / "raw").exists()


def test_contract_columns_and_kept_rows(built):
    assert built["header"][:5] == [
        "line_id", "voltage_kv", "conductor", "thermal_limit_mw", "cost_usd",
    ]
    assert built["header"] == pudl_ferc1.FIELDS
    rows = built["rows"]
    assert len(rows) == 17
    assert {r["report_year"] for r in rows.values()} == {"2023"}
    assert {r["thermal_limit_mw"] for r in rows.values()} == {""}
    # non-SPP respondents (Ameren 222, Con Edison 159) and totals never appear
    assert {r["utility_id_ferc1"] for r in rows.values()} <= {
        str(i) for i in common.region_spec()["ferc1_respondents"]
    }
    assert not any("cahokia" in rid or "pleasant_valley" in rid for rid in rows)
    assert not any(rid.endswith("_total") for rid in rows)


def test_sooner_rose_hill_keeps_stand_alone_miles_and_stays_unmatched(built):
    r = built["rows"][ROSE_HILL]
    # echo-gridlock read only the aggregated column (null here) and dropped this line
    assert (r["standalone_mi"], r["aggregated_mi"], r["length_mi"]) == ("43.87", "", "43.87")
    assert (r["voltage_kv"], r["cost_usd"], r["conductor"]) == ("345", "43932188", "1590ACSR")
    # HIFLD splits it at CHARLES D. LAMB ENERGY CENTER: no single line to claim
    assert (r["line_id"], r["match"]) == ("", "none")
    assert r["match_evidence"] == "no HIFLD line SOONER|ROSE HILL @ 345 kV"


@pytest.mark.parametrize("rid, line_id", [
    (WOODRING, "HIFLD_130857"),
    (CLEVELAND, "HIFLD_113704"),                     # "(GRDA)" parenthetical
    (RID + "c000825_j0601_hitchland_potter_co_sw_sta_hframe_6795_acsr", "HIFLD_310227"),
    (RID + "c001181_lacygne_stilwell_345_345_wdhframe_3078", "HIFLD_143356"),   # LA CYGNE
    (RID + "c000536_tl90914_tulsa_north_sub_wekiwa_sub_2__175_1_22670_acar_0_0", "HIFLD_117710"),
    (RID + "c000772_01_swissvale_sub_lang_sub_hfw_7950_acsr", "HIFLD_123438"),  # reversed
])
def test_exact_matches(built, rid, line_id):
    r = built["rows"][rid]
    assert (r["line_id"], r["match"]) == (line_id, "exact")


def test_one_line_claimed_by_several_rows_is_ambiguous(built):
    oneta = [r for rid, r in built["rows"].items() if rid.startswith(ONETA_PREFIX)]
    assert len(oneta) == 4
    for r in oneta:
        assert (r["line_id"], r["match"]) == ("", "ambiguous")
        assert r["match_evidence"].startswith("HIFLD_140010 claimed by 4 FERC rows")


def test_several_hifld_candidates_is_ambiguous(built):
    r = built["rows"][TATONGA]  # "Mathewson Line 2": the circuit suffix is dropped
    assert (r["line_id"], r["match"]) == ("", "ambiguous")
    assert r["match_evidence"] == (
        "2 HIFLD lines TATONGA|MATHEWSON @ 345 kV: HIFLD_167240 HIFLD_169921"
    )


def test_line_ids_are_unique(built):
    ids = [r["line_id"] for r in built["rows"].values() if r["line_id"]]
    assert len(ids) == len(set(ids)) == 6


def test_kv_in_volts_and_structure_class_row(built):
    r = built["rows"][EMPIRE_345]
    assert (r["voltage_kv"], r["voltage_as_reported"]) == ("345", "345000")
    assert r["flags"] == "kv_in_volts;same_endpoints"
    assert (r["line_id"], r["match"]) == ("", "none")
    assert r["match_evidence"] == "aggregate row; not joined"
    basin = built["rows"][BASIN_LRS]
    assert basin["flags"] == "same_endpoints" and basin["match"] == "none"


def test_double_circuit_sums_both_length_columns(built):
    r = built["rows"][WOODWARD]
    assert (r["standalone_mi"], r["aggregated_mi"], r["length_mi"]) == ("99.3", "99.07", "198.37")
    assert (r["num_circuits"], r["structure"]) == ("2", "(1) Steel")
    assert r["flags"] == "both_length_columns"


def test_zero_capex_is_blank_not_a_cost(built):
    r = built["rows"][THUNDER_RANCH]
    assert (r["cost_usd"], r["flags"]) == ("", "capex_zero")


def test_manifest_stamp_and_note(built):
    e = built["entry"]
    assert (e["source"], e["source_version"]) == ("pudl_ferc_form1", "pudl_v2024.11.0")
    assert e["retrieved_at"] == built["raw"]["retrieved_at"]
    assert (e["coverage"], e["rows"]) == ("partial", 17)
    assert e["contributors"] == [HIFLD_STAMP]
    note = e["note"]
    for fragment in (
        "PUDL v2024.11.0", "report_year 2023 (latest in the release)",
        "filed but no usable rows: 196",
        "Source rows 22; kept 17; dropped 5 (no_voltage 3, no_length 1, missing_endpoint 1",
        "both_length_columns 2, capex_zero 1, kv_in_volts 1, same_endpoints 2",
        "exact 6 rows", "ambiguous 5 (1 with several HIFLD candidates, 4 sharing",
        "none 6", "thermal_limit_mw blank", "unobserved, never zero",
    ):
        assert fragment in note


def test_normalize_is_deterministic(built):
    first = built["entry"]["sha256"]
    pudl_ferc1.normalize(built["snapshot"])
    again = json.loads((built["snapshot"] / MANIFEST_NAME).read_text())["tables"]["ferc1_lines"]
    assert again["sha256"] == first


def test_missing_hifld_lines_is_an_error(fetched):
    with pytest.raises(FileNotFoundError, match="hifld.normalize must run before"):
        pudl_ferc1.normalize(fetched)


def test_hifld_lines_without_sub_names_is_an_error(fetched, tmp_path):
    stripped = tmp_path / "hifld_lines.csv"
    with (SAMPLE / "hifld_lines.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    with stripped.open("w", newline="") as fh:
        w = csv.DictWriter(fh, ["line_id", "from_bus", "to_bus", "voltage_kv", "length_mi"],
                           extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    add_hifld(fetched, stripped)
    with pytest.raises(ValueError, match="sub_1, sub_2"):
        pudl_ferc1.normalize(fetched)


def test_unstamped_hifld_lines_is_an_error(fetched):
    add_hifld(fetched)
    (fetched / "normalized" / "hifld_lines.csv").write_text("line_id,voltage_kv,sub_1,sub_2\n")
    with pytest.raises(ValueError, match="does not match its manifest stamp"):
        pudl_ferc1.normalize(fetched)


def test_hifld_placeholders_never_enter_the_index():
    with (SAMPLE / "hifld_lines.csv").open(newline="") as fh:
        index = pudl_ferc1.hifld_index(list(csv.DictReader(fh)))
    indexed = {i for ids in index.values() for i in ids}
    assert not indexed & {"HIFLD_143680", "HIFLD_113620", "HIFLD_104695", "HIFLD_313410"}
    for name in ("TAP148969", "UNKNOWN118254", "NOT AVAILABLE", "NONE", "", None):
        assert pudl_ferc1.is_placeholder(name)
    for name in ("TAPPAN", "SOONER", "NONESUCH"):
        assert not pudl_ferc1.is_placeholder(name)


@pytest.mark.parametrize("raw, expected", [
    (161000.0, (161.0, ["kv_in_volts"])),
    (34500.0, (34.5, ["kv_in_volts"])),
    (1000.0, (1000.0, ["voltage_implausible"])),   # never 1 kV
    (138138.0, (138138.0, ["voltage_implausible"])),
    (345.0, (345.0, [])),
])
def test_fix_kv(raw, expected):
    assert pudl_ferc1.fix_kv(raw) == expected


@pytest.mark.parametrize("start, end, flags", [
    ("Total 345 kV Lines (All)", "x", ["aggregate_name"]),
    ("SUMMARY OF 115 KV SYSTEM", "x", ["aggregate_name"]),
    ("115 kV LINES", "x", ["aggregate_name"]),
    ("Various 69 Kv", "x", ["aggregate_name"]),
    ("Less non-NWE 345kV partial ownership miles", "x", ["aggregate_name"]),
    ("345", "345", ["same_endpoints"]),
    ("Miles City-Bowman", "Bowman- New Und", []),
    ("Sooner", "Woodring", []),
])
def test_aggregate_flags(start, end, flags):
    assert pudl_ferc1.aggregate_flags(start, end) == flags


@pytest.mark.parametrize("raw, normalized", [
    ("TL90-902 ONETA SUB", "ONETA"),
    ("(J11-TX;01) BORDER", "BORDER"),
    ("31-Rose Hill Sub", "ROSE HILL"),
    ("19N Str 4", "STR 4"),
    ("Rose Hill (WERE)", "ROSE HILL"),
    ("ONETA ENERGY CENTER (CAL", "ONETA ENERGY CENTER"),
    ("Mathewson Line 2", "MATHEWSON"),
    ("Welsh Ckt 1", "WELSH"),
    ("HOLCOMB POWER PLANT 345KV", "HOLCOMB"),
    ("NORTHEASTERN POWER STATION", "NORTHEASTERN"),
    ("Clarksville 345", "CLARKSVILLE"),
    ("TULSA NORTH (345KV)", "TULSA NORTH"),
    ("POTTER CO SW STA", "POTTER COUNTY"),
    ("NW Texarkana (TX Portion)", "NORTHWEST TEXARKANA"),
    ("Muskogee 161 KV Tie", "MUSKOGEE TIE"),
    ("345", "345"),
])
def test_norm_endpoint(raw, normalized):
    assert pudl_ferc1.norm_endpoint(raw) == normalized
