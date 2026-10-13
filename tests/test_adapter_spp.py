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
MONTHS = range(1, 13)
BC_BYTES = {m: (FIX / f"da_bc_2025{m:02d}_sample.csv.zip").read_bytes() for m in MONTHS}
Q_BYTES = (FIX / "gi_active_queue_sample.csv").read_bytes()
BC_URL = {m: spp.DA_BC_URL.format(year=2025, month=m) for m in MONTHS}
BC_RAW = {m: f"raw:spp_da_bc_2025{m:02d}" for m in MONTHS}
CBC_BYTES = {m: (FIX / f"cbc_2025{m:02d}_sample.csv").read_bytes() for m in MONTHS}
TCR_BYTES = {m: (FIX / f"tcr_2025{m:02d}_sample.csv").read_bytes() for m in MONTHS}
CBC_URL = {m: spp.CBC_URL.format(year=2025, month=m) for m in MONTHS}
TCR_URL = {m: spp.TCR_URL.format(year=2025, month=m) for m in MONTHS}
MONEY_RAW = {f"raw:spp_{k}_2025{m:02d}" for k in ("cbc", "tcr") for m in MONTHS}
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


def _fetch(tmp_path, served, bc=BC_BYTES, queue=Q_BYTES, cbc=CBC_BYTES, tcr=TCR_BYTES):
    snap = tmp_path / "snap"
    update_manifest(snap, snapshot_date="2026-10-12")
    served.update({BC_URL[m]: bc[m] for m in MONTHS})
    served.update({CBC_URL[m]: cbc[m] for m in MONTHS})
    served.update({TCR_URL[m]: tcr[m] for m in MONTHS})
    served[spp.QUEUE_URL] = queue
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
    """The sample rows of all twelve months, parsed here independently of the adapter."""
    rows = []
    for m in MONTHS:
        with zipfile.ZipFile(FIX / f"da_bc_2025{m:02d}_sample.csv.zip") as z:
            text = z.read(f"DA-BC-MONTHLY-2025{m:02d}.csv").decode("utf-8")
        part = list(csv.DictReader(io.StringIO(text)))
        assert " Contingency Name" in part[0]  # SPP's header really has the leading space
        rows += part
    return rows


def _cid(r):
    return f"{r['Constraint Name']}|{r[' Contingency Name']}"


def _sample_queue():
    text = Q_BYTES.decode("utf-8")
    return list(csv.DictReader(io.StringIO(text.split("\r\n", 1)[1], newline="")))


def test_fetch_records_verbatim_raw_files(tmp_path, served):
    snap, result = _fetch(tmp_path, served)
    raw = _manifest(snap)["raw"]
    assert set(raw) == {*BC_RAW.values(), *MONEY_RAW, spp.QUEUE_RAW}
    for m in MONTHS:
        assert raw[BC_RAW[m]]["url"] == BC_URL[m]
        assert raw[BC_RAW[m]]["sha256"] == hashlib.sha256(BC_BYTES[m]).hexdigest()
        assert (snap / raw[BC_RAW[m]]["path"]).read_bytes() == BC_BYTES[m]
    assert raw[spp.QUEUE_RAW]["url"] == spp.QUEUE_URL
    assert raw[spp.QUEUE_RAW]["sha256"] == hashlib.sha256(Q_BYTES).hexdigest()
    assert result["bytes"] == (sum(map(len, BC_BYTES.values())) + len(Q_BYTES)
                               + sum(map(len, CBC_BYTES.values())) + sum(map(len, TCR_BYTES.values())))


def test_fetch_refuses_a_queue_update_that_is_not_pinned(tmp_path, served):
    newer = Q_BYTES.replace(b"10/12/2026", b"10/16/2026", 1)
    with pytest.raises(RuntimeError, match="spp_giq_2026-10-16"):
        _fetch(tmp_path, served, queue=newer)


def test_fetch_refuses_an_archive_for_another_month(tmp_path, served):
    buf = BytesIO()
    with zipfile.ZipFile(BytesIO(BC_BYTES[3])) as src, zipfile.ZipFile(buf, "w") as out:
        out.writestr("DA-BC-MONTHLY-202502.csv", src.read("DA-BC-MONTHLY-202503.csv"))
    with pytest.raises(ValueError, match="DA-BC-MONTHLY-202503.csv"):
        _fetch(tmp_path, served, bc={**BC_BYTES, 3: buf.getvalue()})


def test_normalize_refuses_a_row_outside_its_month(tmp_path, served):
    """March's file carrying a February hour means SPP changed the layout."""
    with zipfile.ZipFile(BytesIO(BC_BYTES[3])) as src:
        text = src.read("DA-BC-MONTHLY-202503.csv").decode("utf-8")
    header, first, rest = text.split("\n", 2)
    moved = first.replace("03/", "02/", 1)
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        out.writestr("DA-BC-MONTHLY-202503.csv", "\n".join([header, moved, rest]))
    snap, _ = _fetch(tmp_path, served, bc={**BC_BYTES, 3: buf.getvalue()})
    with pytest.raises(ValueError, match="another month"):
        spp.normalize(snap)


def test_monthly_archives_carry_rows_the_yearly_file_lacks(snap):
    """TMP452_24498 (LN LIBERAL_ - TEXAS_CO) bound 361 hours in January 2025 and is
    absent from DA-BC-YEARLY-2025 altogether."""
    spp.normalize(snap)
    rows = {r["constraint_id"]: r for r in _table(snap, "constraints")}
    (cid,) = [c for c in rows if c.startswith("TMP452_24498|")]
    assert rows[cid]["monitored_facility"] == "LN LIBERAL_ - TEXAS_CO"
    note = _manifest(snap)["tables"]["constraints"]["note"]
    assert "twelve monthly archives" in note


def test_normalize_refuses_pin_drift(snap, monkeypatch):
    monkeypatch.setattr(common, "registry_pin", lambda source: "spp_da_bc_2024")
    with pytest.raises(RuntimeError, match="spp_binding_constraints"):
        spp.normalize(snap)


def test_constraints_score_lines_transformers_interfaces_and_exclude_phase_shifters(snap):
    spp.normalize(snap)
    rows = {r["constraint_id"]: r for r in _table(snap, "constraints")}
    sample = _sample_bc()
    by_cid = {}
    for r in sample:
        by_cid.setdefault(_cid(r), []).append(r)
    xfmr = {c for c, rs in by_cid.items() if rs[0]["Monitored Facility"].startswith("XFMR")}
    assert {"HUGOPP4|BASE", "T2T2_H_XF@FTSMTH<17246>|17246"} <= xfmr
    shifters = {c for c in by_cid if "PHSHFT" in c}
    assert shifters == {"SOONER_SONR_SONR_PHSHFT_PS|BASE"}
    assert set(rows) == set(by_cid) - shifters
    for cid, r in rows.items():
        mon = by_cid[cid][0]["Monitored Facility"]
        want = ("thermal" if mon.startswith("LN ") else
                "transformer" if mon.startswith("XFMR") else "interface")
        assert r["ctype"] == want
        assert r["monitored_line"] == "" and r["contingency_line"] == ""
    multi = rows["TMP800_29528|BASE"]
    assert multi["monitored_facility_as_reported"] == "Multi-Element Constraint"
    # distinct interfaces must not share one corridor key
    assert multi["monitored_facility"] == "Multi-Element Constraint TMP800_29528"
    note = _manifest(snap)["tables"]["constraints"]["note"]
    assert "Excluded 1 phase-shifter flowgates (SOONER_SONR_SONR_PHSHFT_PS|BASE" in note

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
    assert f"{len(xfmr)} transformer (XFMR)" in entry["note"]
    assert "not resolved to public topology" in entry["note"]


def test_congestion_matches_sums_computed_from_the_raw_rows(snap):
    spp.normalize(snap)
    rows = {r["constraint_id"]: r for r in _table(snap, "congestion")}
    assert set(rows) == {r["constraint_id"] for r in _table(snap, "constraints")}
    by_cid = {}
    for r in _sample_bc():
        if "PHSHFT" not in r["Constraint Name"]:
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
    assert all(_cid(r) in rows for r in last_hour if "PHSHFT" not in r["Constraint Name"])

    # Every row of this flowgate is in the sample: its full-year 2025 DA record.
    assert (rows["TMP363_30990|18875"]["hours_binding"],
            rows["TMP363_30990|18875"]["breached_hours"]) == ("10", "5")
    # The DST fall-back hour repeats local Interval 02:00 with two UTC hours: both count.
    kingfish = by_cid["KINGFISH|BASE"]
    assert len({r["Interval"] for r in kingfish}) < len({r["GMTIntervalEnd"] for r in kingfish})
    assert int(rows["KINGFISH|BASE"]["hours_binding"]) == len({r["GMTIntervalEnd"] for r in kingfish})

    entry = _manifest(snap)["tables"]["congestion"]
    unjoined = [cid for cid, r in rows.items() if not r["rent_musd"]]
    # partial exactly when some flowgate has no observed congestion $
    assert entry["coverage"] == ("partial" if unjoined else "observed")
    assert "Day-Ahead market only" in entry["note"] and "$/MW" in entry["note"]


def test_planning_is_header_only_and_unobserved(snap):
    spp.normalize(snap)
    path = snap / "normalized" / "planning.csv"
    assert path.read_text() == "constraint_id,plan,proposed_fix,assigned_cost_musd\n"
    m = _manifest(snap)
    entry = m["tables"]["planning"]
    assert (entry["coverage"], entry["source"], entry["rows"]) == ("unobserved", "spp_itp", 0)
    assert entry["source_version"] == common.registry_pin("spp_itp")
    assert entry["retrieved_at"] == max(m["raw"][BC_RAW[k]]["retrieved_at"] for k in MONTHS)
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
    assert (entry["source_version"], entry["coverage"]) == ("spp_giq_2026-10-12", "partial")
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
    latest = max(m["raw"][BC_RAW[k]]["retrieved_at"] for k in MONTHS)
    for table in ("constraints", "congestion"):
        assert m["tables"][table]["source_version"] == "spp_da_bc_monthly_2025"
        assert m["tables"][table]["retrieved_at"] == latest
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


def _sample_cbc():
    rows = []
    for m in MONTHS:
        text = CBC_BYTES[m].decode("utf-8")
        rows += [{k.strip(): v.strip() for k, v in r.items()}
                 for r in csv.DictReader(io.StringIO(text))]
    return rows


def test_rent_is_spp_congestion_dollars_joined_exactly_with_its_own_stamp(snap):
    spp.normalize(snap)
    rows = {r["constraint_id"]: r for r in _table(snap, "congestion")}
    sample = _sample_cbc()
    # expected, computed independently: dedupe (month, name, contingency), rent = -Sum,
    # positive-Sum months flagged and left out
    seen, want, negative = set(), {}, Counter()
    for r in sample:
        key = (r["Date"], r["Constraint Name"], r["Contingency Name"])
        if key in seen:
            continue
        seen.add(key)
        cid = f"{r['Constraint Name']}|{r['Contingency Name']}"
        usd = -float(r["Sum"])
        if usd < 0:
            negative[cid] += 1
            continue
        want.setdefault(cid, []).append(usd)
    assert len(seen) < len(sample)  # the sample carries a duplicate key
    joined = {c: v for c, v in want.items() if c in rows}
    assert joined
    for cid, vals in joined.items():
        got = rows[cid]
        assert float(got["rent_musd"]) == pytest.approx(math.fsum(vals) / 1e6, abs=1e-6)
        assert float(got["max_month_cost_musd"]) == pytest.approx(max(vals) / 1e6, abs=1e-6)
        assert got["rent_basis"] == "spp_congestion_by_constraint"
    for cid, row in rows.items():
        if cid not in want:
            assert row["rent_musd"] == "" and row["rent_basis"] == ""  # unobserved, not 0
    # the one negative-rent month in the sample belongs to the excluded phase shifter
    assert set(negative) == {"SOONER_SONR_SONR_PHSHFT_PS|BASE"}

    entry = _manifest(snap)["tables"]["congestion"]
    cbc = entry["column_sources"]["rent_musd"]
    assert (cbc["source"], cbc["source_version"]) == ("spp_congestion_by_constraint", "spp_cbc_2025")
    assert entry["column_sources"]["max_month_cost_musd"] == cbc
    assert {c["source"] for c in entry["contributors"]} == {
        "spp_congestion_by_constraint", "spp_tcr_funding"}
    assert "duplicate congestion keys removed: 1" in entry["note"]
    assert "TCR funding by constraint, DA Collected" in entry["note"]
    Snapshot(snap).require_tables(TABLES)


def test_months_binding_counts_months_with_any_binding_hour(snap):
    spp.normalize(snap)
    rows = {r["constraint_id"]: r for r in _table(snap, "congestion")}
    from datetime import datetime, timedelta
    months = {}
    for r in _sample_bc():
        hour = datetime.strptime(r["Interval"], "%m/%d/%Y %H:%M:%S") - timedelta(hours=1)
        months.setdefault(_cid(r), set()).add(hour.month)  # Interval is hour-ending
    for cid, row in rows.items():
        assert int(row["months_binding"]) == len(months[cid])


def test_a_recycled_id_naming_another_element_is_not_joined(tmp_path, served):
    text = CBC_BYTES[1].decode("utf-8")
    lines = text.split("\n")
    target = next(ln for ln in lines[1:] if ",70BS12WAGS34," in ln)
    swapped = target.replace("LN 70_BLUF - SUB1214", "LN ELSEWHERE - NOWHERE")
    cbc = {m: CBC_BYTES[m] for m in MONTHS}
    for m in MONTHS:
        cbc[m] = CBC_BYTES[m].decode("utf-8").replace(
            "LN 70_BLUF - SUB1214", "LN ELSEWHERE - NOWHERE").encode("utf-8")
    snap, _ = _fetch(tmp_path, served, cbc=cbc)
    spp.normalize(snap)
    rows = {r["constraint_id"]: r for r in _table(snap, "congestion")}
    (cid,) = [c for c in rows if c.startswith("70BS12WAGS34|")]
    assert rows[cid]["rent_musd"] == "" and "cbc_monitored_mismatch" in rows[cid]["flags"]
    assert swapped != target


def test_a_congestion_file_for_another_month_is_refused(tmp_path, served):
    bad = CBC_BYTES[2].decode("utf-8").replace("\n202502,", "\n202503,", 1).encode("utf-8")
    snap, _ = _fetch(tmp_path, served, cbc={**CBC_BYTES, 2: bad})
    with pytest.raises(ValueError, match="congestion-by-constraint 2025-02"):
        spp.normalize(snap)


def test_a_conflicting_congestion_duplicate_fails_the_build(tmp_path, served):
    text = CBC_BYTES[1].decode("utf-8")
    line = next(ln for ln in text.split("\n")[1:] if ",70BS12WAGS34," in ln)
    head, _, value = line.rpartition(",")
    bad = (text.rstrip("\n") + "\n" + f"{head},{float(value) - 1:.2f}" + "\n").encode("utf-8")
    snap, _ = _fetch(tmp_path, served, cbc={**CBC_BYTES, 1: bad})
    with pytest.raises(ValueError, match="conflicting rows"):
        spp.normalize(snap)
