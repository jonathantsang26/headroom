"""SPP adapter on recorded real samples (tests/fixtures/real_samples/spp), offline."""

import csv
import hashlib
import io
import json
import math
import urllib.request
import zipfile
from collections import Counter
from io import BytesIO
from pathlib import Path

import pytest

from headroom.ingest.adapters import common, spp
from headroom.ingest.snapshot import MANIFEST_NAME, Snapshot, update_manifest

FIX = Path(__file__).parent / "fixtures" / "real_samples" / "spp"
BC_BYTES = (FIX / "da_bc_2025_sample.csv.zip").read_bytes()
Q_BYTES = (FIX / "gi_active_queue_sample.csv").read_bytes()
BC_URL = spp.DA_BC_URL.format(year=2025)
BC_RAW = "raw:spp_da_bc_2025"
TABLES = ("constraints", "congestion", "planning", "queue")


class Response(BytesIO):
    def __init__(self, body):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))}


@pytest.fixture(autouse=True)
def served(monkeypatch):
    """URL -> bytes served offline; any other request fails the test."""
    served = {}

    def urlopen(request, *args, **kwargs):
        url = getattr(request, "full_url", request)
        if url not in served:
            raise AssertionError(f"unexpected network access: {url}")
        return Response(served[url])

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(common.urllib.request, "urlopen", urlopen)
    return served


def _fetch(tmp_path, served, bc=BC_BYTES, queue=Q_BYTES):
    snap = tmp_path / "snap"
    update_manifest(snap, snapshot_date="2026-10-12")
    served.update({BC_URL: bc, spp.QUEUE_URL: queue})
    result = spp.fetch(snap)
    served.clear()  # normalize must not touch the network
    return snap, result


@pytest.fixture
def snap(tmp_path, served):
    return _fetch(tmp_path, served)[0]


def _manifest(snap):
    return json.loads((snap / MANIFEST_NAME).read_text())


def _table(snap, name):
    with (snap / "normalized" / f"{name}.csv").open(newline="") as fh:
        return list(csv.DictReader(fh))


def _write_buses(snap):
    with (FIX / "buses_sample.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    return rows, common.write_table(
        snap, "buses", rows, list(rows[0]), source="hifld_transmission",
        source_version=common.registry_pin("hifld_transmission"),
        retrieved_at="2026-10-12T00:00:00+00:00", coverage="partial", note="test sample",
    )


def _sample_bc():
    """The sample rows, parsed here independently of the adapter."""
    with zipfile.ZipFile(FIX / "da_bc_2025_sample.csv.zip") as z:
        text = z.read("DA-BC-YEARLY-2025.csv").decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert " Contingency Name" in rows[0]  # SPP's header really has the leading space
    return rows


def _cid(r):
    return f"{r['Constraint Name']}|{r[' Contingency Name']}"


def _sample_queue():
    text = Q_BYTES.decode("utf-8")
    return list(csv.DictReader(io.StringIO(text.split("\r\n", 1)[1], newline="")))


def test_fetch_records_verbatim_raw_files(tmp_path, served):
    snap, result = _fetch(tmp_path, served)
    raw = _manifest(snap)["raw"]
    assert set(raw) == {BC_RAW, spp.QUEUE_RAW}
    assert raw[BC_RAW]["url"] == BC_URL and raw[spp.QUEUE_RAW]["url"] == spp.QUEUE_URL
    assert raw[BC_RAW]["sha256"] == hashlib.sha256(BC_BYTES).hexdigest()
    assert raw[spp.QUEUE_RAW]["sha256"] == hashlib.sha256(Q_BYTES).hexdigest()
    assert (snap / raw[BC_RAW]["path"]).read_bytes() == BC_BYTES
    assert result["bytes"] == len(BC_BYTES) + len(Q_BYTES)


def test_fetch_refuses_a_queue_update_that_is_not_pinned(tmp_path, served):
    newer = Q_BYTES.replace(b"10/9/2026", b"10/16/2026", 1)
    with pytest.raises(RuntimeError, match="spp_giq_2026-10-16"):
        _fetch(tmp_path, served, queue=newer)


def test_fetch_refuses_an_archive_for_another_year(tmp_path, served):
    buf = BytesIO()
    with zipfile.ZipFile(BytesIO(BC_BYTES)) as src, zipfile.ZipFile(buf, "w") as out:
        out.writestr("DA-BC-YEARLY-2024.csv", src.read("DA-BC-YEARLY-2025.csv"))
    with pytest.raises(ValueError, match="DA-BC-YEARLY-2025.csv"):
        _fetch(tmp_path, served, bc=buf.getvalue())


def test_normalize_refuses_pin_drift(snap, monkeypatch):
    monkeypatch.setattr(common, "registry_pin", lambda source: "spp_da_bc_2024")
    with pytest.raises(RuntimeError, match="spp_binding_constraints"):
        spp.normalize(snap)


def test_constraints_keep_lines_and_interfaces_and_exclude_transformers(snap):
    spp.normalize(snap)
    rows = {r["constraint_id"]: r for r in _table(snap, "constraints")}
    sample = _sample_bc()
    by_cid = {}
    for r in sample:
        by_cid.setdefault(_cid(r), []).append(r)
    xfmr = {c for c, rs in by_cid.items() if rs[0]["Monitored Facility"].startswith("XFMR")}
    assert {"HUGOPP4|BASE", "T2T2_H_XF@FTSMTH<17246>|17246"} <= xfmr
    assert set(rows) == set(by_cid) - xfmr
    for cid, r in rows.items():
        mon = by_cid[cid][0]["Monitored Facility"]
        assert r["ctype"] == ("thermal" if mon.startswith("LN ") else "interface")
        assert r["monitored_line"] == "" and r["contingency_line"] == ""
    multi = rows["TMP800_29528|BASE"]
    assert multi["monitored_facility_as_reported"] == "Multi-Element Constraint"
    # distinct interfaces must not share one corridor key
    assert multi["monitored_facility"] == "Multi-Element Constraint TMP800_29528"
    phase_shifter = rows["SOONER_SONR_SONR_PHSHFT_PS|BASE"]
    assert phase_shifter["ctype"] == "interface" and phase_shifter["monitored_facility"] == ""

    base = rows["FULTON_T_1961A_A_LN|BASE"]
    assert (base["contingency_facility"], base["contingency_name"]) == ("BASE", "BASE")
    assert base["contingency_to"] == base["contingency_kv"] == ""

    # SPP spells this flowgate's line both ways round: one row, most frequent spelling.
    spellings = Counter(r["Monitored Facility"] for r in by_cid["SUB1399_1500_1_LN|BASE"])
    assert len(spellings) == 2
    sub1399 = rows["SUB1399_1500_1_LN|BASE"]
    assert sub1399["monitored_facility_as_reported"] == spellings.most_common(1)[0][0]
    # the corridor key ignores endpoint order, so both spellings give one key
    keys = {"LN " + " - ".join(sorted(e.strip() for e in m[3:].split(" - "))) for m in spellings}
    assert keys == {sub1399["monitored_facility"]}

    variants = Counter(r["Contingent Facility"] for r in by_cid["TMP272_27635|14756"])
    tmp272 = rows["TMP272_27635|14756"]
    assert len(variants) == 3
    assert tmp272["contingency_facility"] == variants.most_common(1)[0][0]
    assert (tmp272["contingency_to"], tmp272["contingency_kv"]) == ("OKGE", "345")
    assert tmp272["spp_constraint_type"] == "FG"

    entry = _manifest(snap)["tables"]["constraints"]
    assert entry["coverage"] == "partial"
    assert f"Excluded {len(xfmr)} transformer (XFMR) flowgates" in entry["note"]
    assert "not resolved to public topology" in entry["note"]


def test_congestion_matches_sums_computed_from_the_raw_rows(snap):
    spp.normalize(snap)
    rows = {r["constraint_id"]: r for r in _table(snap, "congestion")}
    assert set(rows) == {r["constraint_id"] for r in _table(snap, "constraints")}
    by_cid = {}
    for r in _sample_bc():
        if not r["Monitored Facility"].startswith("XFMR"):
            by_cid.setdefault(_cid(r), []).append(r)
    for cid, rs in by_cid.items():
        got = rows[cid]
        assert int(got["hours_binding"]) == len({r["GMTIntervalEnd"] for r in rs})
        assert int(got["breached_hours"]) == sum(r["State"] == "BREACHED" for r in rs)
        want = round(math.fsum(abs(float(r["Shadow Price"])) for r in rs), 4)
        assert float(got["shadow_price_sum_usd_per_mw"]) == pytest.approx(want, abs=1e-9)
        assert got["monthly_cost_musd"] == "" and got["period"] == "2025"

    # Interval is hour-ending: 01/01/2026 00:00 is 2025's last hour and is kept.
    last_hour = [r for r in _sample_bc() if r["Interval"] == "01/01/2026 00:00:00"]
    assert last_hour
    assert all(_cid(r) in rows for r in last_hour if not r["Monitored Facility"].startswith("XFMR"))

    # Every row of this flowgate is in the sample: its full-year 2025 DA record.
    assert (rows["TMP363_30990|18875"]["hours_binding"],
            rows["TMP363_30990|18875"]["breached_hours"]) == ("10", "5")
    # The DST fall-back hour repeats local Interval 02:00 with two UTC hours: both count.
    kingfish = by_cid["KINGFISH|BASE"]
    assert len({r["Interval"] for r in kingfish}) < len({r["GMTIntervalEnd"] for r in kingfish})
    assert int(rows["KINGFISH|BASE"]["hours_binding"]) == len({r["GMTIntervalEnd"] for r in kingfish})

    entry = _manifest(snap)["tables"]["congestion"]
    assert entry["coverage"] == "observed"
    assert "Day-Ahead market only" in entry["note"] and "$/MW" in entry["note"]


def test_planning_is_header_only_and_unobserved(snap):
    spp.normalize(snap)
    path = snap / "normalized" / "planning.csv"
    assert path.read_text() == "constraint_id,plan,proposed_fix,assigned_cost_musd\n"
    m = _manifest(snap)
    entry = m["tables"]["planning"]
    assert (entry["coverage"], entry["source"], entry["rows"]) == ("unobserved", "spp_itp", 0)
    assert entry["source_version"] == common.registry_pin("spp_itp")
    assert entry["retrieved_at"] == m["raw"][BC_RAW]["retrieved_at"]
    assert "Nothing was retrieved for planning" in entry["note"]


def test_queue_keeps_active_requests_and_maps_unambiguous_pois(snap):
    buses, buses_entry = _write_buses(snap)
    spp.normalize(snap)
    rows = {r["request_id"]: r for r in _table(snap, "queue")}
    sample = _sample_queue()
    active = {r["Generation Interconnection Number"] for r in sample
              if "COMMERCIAL OPERATION" not in r["Status"]}
    assert set(rows) == active and "TI-18-0827" not in rows
    assert rows["ASGI-2024-024"]["status"] == "None"
    capacity = {r["Generation Interconnection Number"]: float(r["Capacity"]) for r in sample}
    assert all(float(r["mw"]) == capacity[rid] for rid, r in rows.items())

    bus_id = {b["name"]: b["bus_id"] for b in buses}
    craig = rows["GI-DISIS-2022-44"]  # "Craig 345 Substation"
    assert (craig["near_bus"], craig["match"]) == (bus_id["CRAIG"], "exact")
    assert (rows["GI-TC-2024-01"]["near_bus"], rows["GI-TC-2024-01"]["match"]) == (
        bus_id["LINCOLN"], "exact")
    # BURLINGTON and BURLINGTON (IA) normalize alike: ambiguous, so no bus.
    assert (rows["GI-TC-2024-10"]["near_bus"], rows["GI-TC-2024-10"]["match"]) == ("", "ambiguous")
    assert (rows["GI-TC-2024-31"]["near_bus"], rows["GI-TC-2024-31"]["match"]) == ("", "none")
    assert rows["GEN-2022-238"]["fuel"] == "Battery/Storage"  # blank Fuel Type
    assert "\n" not in rows["GI-TC-2024-25"]["poi"]

    entry = _manifest(snap)["tables"]["queue"]
    assert (entry["source_version"], entry["coverage"]) == ("spp_giq_2026-10-09", "partial")
    assert entry["contributors"] == [
        {k: buses_entry[k] for k in ("source", "source_version", "retrieved_at")}
    ]


def test_queue_without_buses_leaves_near_bus_blank(snap):
    spp.normalize(snap)
    rows = _table(snap, "queue")
    assert rows and all(r["near_bus"] == "" and r["match"] == "" for r in rows)
    entry = _manifest(snap)["tables"]["queue"]
    assert "buses.csv absent" in entry["note"] and "contributors" not in entry


def test_unstamped_buses_are_refused(snap):
    (snap / "normalized").mkdir(exist_ok=True)
    (snap / "normalized" / "buses.csv").write_bytes((FIX / "buses_sample.csv").read_bytes())
    with pytest.raises(ValueError, match="buses.csv"):
        spp.normalize(snap)


def test_tables_are_stamped_and_deterministic(snap):
    spp.normalize(snap)
    first = {t: e["sha256"] for t, e in _manifest(snap)["tables"].items()}
    spp.normalize(snap)
    m = _manifest(snap)
    assert {t: e["sha256"] for t, e in m["tables"].items()} == first
    for table in ("constraints", "congestion"):
        assert m["tables"][table]["source_version"] == "spp_da_bc_2025"
        assert m["tables"][table]["retrieved_at"] == m["raw"][BC_RAW]["retrieved_at"]
    assert m["tables"]["queue"]["retrieved_at"] == m["raw"][spp.QUEUE_RAW]["retrieved_at"]
    Snapshot(snap).require_tables(TABLES)


@pytest.mark.parametrize("status, withdrawn, active", [
    ("IA FULLY EXECUTED/COMMERCIAL OPERATION", "", False),
    ("WITHDRAWN", "", False),
    ("DISIS STAGE", "3/1/2026", False),
    ("IA FULLY EXECUTED/ON SUSPENSION", "", True),
    ("None", "", True),
])
def test_active_rule(status, withdrawn, active):
    assert spp.is_active({"Status": status, "Date Withdrawn": withdrawn}) is active


@pytest.mark.parametrize("poi, key", [
    ("Landsman Creek 230 SwitchingStation", "LANDSMAN CREEK"),
    ("Milk Creek 345Switching Station", "MILK CREEK"),
    ("Looff 230 Substation (BurlingtonExpansion)", "LOOFF"),
    ("Booneville Station, 12.47 kV bus", "BOONEVILLE"),
    ("Sioux Center North Industrial 69kV Substation", "SIOUX CENTER NORTH INDUSTRIAL"),
    ("Lincoln – Midway 230  Line", "LINCOLN MIDWAY LINE"),
    ("TULSA NORTH (138KV)", "TULSA NORTH"),
    ("RIVERTON 115/34 KV", "RIVERTON"),
])
def test_poi_key_on_real_names(poi, key):
    assert spp.poi_key(poi) == key
