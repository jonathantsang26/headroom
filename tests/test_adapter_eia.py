"""EIA adapter on recorded real samples (PUDL EIA-860 extract around Mandan ND and
four days of EIA-930 SWPP/MISO rows), served offline. See fixtures/real_samples/eia."""

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from headroom.ingest.adapters import common, eia
from headroom.ingest.snapshot import update_manifest

SAMPLE = Path(__file__).parent / "fixtures" / "real_samples" / "eia"
CSV_930 = {h: SAMPLE / f"EIA930_BALANCE_2024_{h}.csv" for h in eia.EIA930_HALVES}


class Response(io.BytesIO):
    def __init__(self, body: bytes):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))}


def serve(request, timeout=None):
    url = request.full_url if hasattr(request, "full_url") else request
    for table in (eia.PLANTS, eia.GENERATORS):
        if url == eia.pudl_url(table):
            return Response((SAMPLE / f"{table}.parquet").read_bytes())
    for half, path in CSV_930.items():
        if url == eia.eia930_url(2024, half):
            return Response(path.read_bytes())
    raise AssertionError(f"unexpected network access: {url}")


@pytest.fixture
def snap(tmp_path, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", serve)
    monkeypatch.setattr(common.urllib.request, "urlopen", serve)
    snap = tmp_path / "snap"
    (snap / "raw").mkdir(parents=True)
    update_manifest(snap, snapshot_date="2026-10-12")
    return snap


def _manifest(snap: Path) -> dict:
    return json.loads((snap / "snapshot_manifest.json").read_text())


def _rows(snap: Path, table: str) -> list[dict]:
    return list(csv.DictReader((snap / "normalized" / f"{table}.csv").open(newline="")))


def test_pins_drive_the_urls():
    assert eia.pin_860() == ("v2024.11.0", 2024)
    assert eia.pudl_url(eia.PLANTS) == (
        "https://s3.us-west-2.amazonaws.com/pudl.catalyst.coop/v2024.11.0/"
        "out_eia__yearly_plants.parquet"
    )
    assert eia.year_930() == 2024
    assert eia.eia930_url(2024, "Jul_Dec").endswith(
        "/sixMonthFiles/EIA930_BALANCE_2024_Jul_Dec.csv"
    )


@pytest.mark.parametrize("source, bad", [
    ("eia_860_861", "pudl_nightly:eia860_2024"),
    ("eia_860_861", "pudl_v2024.11.0"),
    ("eia_930", "eia930_2023"),
])
def test_pin_mismatch_raises(monkeypatch, source, bad):
    real = common.registry_pin
    monkeypatch.setattr(eia, "registry_pin", lambda s: bad if s == source else real(s))
    with pytest.raises(ValueError):
        eia.pin_860() if source == "eia_860_861" else eia.year_930()


def test_fetch_records_every_raw_file(snap):
    out = eia.fetch(snap)
    raw = _manifest(snap)["raw"]
    names = {
        f"raw:{eia.PLANTS}", f"raw:{eia.GENERATORS}",
        "raw:eia930_2024_Jan_Jun", "raw:eia930_2024_Jul_Dec",
    }
    assert set(raw) == names
    assert out["bytes"] == sum(raw[n]["bytes"] for n in names)
    assert raw["raw:eia930_2024_Jan_Jun"]["url"] == eia.eia930_url(2024, "Jan_Jun")


def test_load_plants_operating_capacity(snap):
    eia.fetch(snap)
    plants = {p["plant_id_eia"]: p for p in eia.load_plants(snap)}
    # R M Heskett 2024: units 3 (88.0) and 4 (88.4) OP; units 1-2 retired. Unit 4 was
    # status V (proposed) in 2023, so the report-year filter matters.
    heskett = plants[2790]
    assert heskett["ba"] == "MISO" and heskett["n_generators"] == 2
    assert heskett["capacity_mw"] == pytest.approx(176.4, abs=1e-3)
    # Mandan Refinery: GEN1 + GEN2 OP at 2.5 MW; GEN3 OA (out of service) excluded.
    refinery = plants[52133]
    assert (refinery["ba"], refinery["capacity_mw"], refinery["n_generators"]) == ("SWPP", 5.0, 2)
    assert refinery["lat"] == pytest.approx(46.8492, abs=1e-4)
    assert list(plants) == sorted(plants)


def _sample_swpp() -> list[dict]:
    rows = []
    for path in CSV_930.values():
        with path.open(newline="") as fh:
            rows += [r for r in csv.DictReader(fh) if r["Balancing Authority"] == "SWPP"]
    return rows


def test_load_930_snapshots_from_real_hours(snap):
    eia.fetch(snap)
    out = eia.normalize(snap)
    rows = {r["snapshot"]: r for r in _rows(snap, "load_930")}
    assert list(rows) == ["peak", "offpeak", "shoulder", "windhigh"]
    assert {r["ba"] for r in rows.values()} == {"SWPP"}
    sample = _sample_swpp()
    demand = sorted(int(r["Demand (MW) (Adjusted)"]) for r in sample)
    assert out["hours"] == len(sample) == 96  # MISO rows in the sample are ignored
    # peak = max demand in the sample: 54,247 MW at 2024-07-15 22:00 UTC (the 2024 SWPP peak).
    assert int(rows["peak"]["load_mw"]) == demand[-1] == 54247
    assert rows["peak"]["utc_hour"] == "2024-07-15T22:00:00+00:00"
    assert int(rows["offpeak"]["load_mw"]) == demand[0]
    assert int(rows["shoulder"]["load_mw"]) == demand[(len(demand) - 1) // 2]
    assert rows["peak"]["basis"].endswith("Demand (MW) (Adjusted)")
    # windhigh: wind / net generation (adjusted) is maximal at 2024-04-07 10:00 UTC.
    best = max(
        sample,
        key=lambda r: float(r.get("Net Generation (MW) from Wind (Adjusted)") or 0)
        / float(r["Net Generation (MW) (Adjusted)"]),
    )
    utc = datetime.strptime(best["UTC Time at End of Hour"], "%m/%d/%Y %I:%M:%S %p")
    assert rows["windhigh"]["utc_hour"] == utc.replace(tzinfo=timezone.utc).isoformat()
    assert rows["windhigh"]["utc_hour"] == "2024-04-07T10:00:00+00:00"
    assert rows["windhigh"]["load_mw"] == best["Demand (MW) (Adjusted)"]


def test_jul_dec_wind_columns_are_split(snap):
    eia.fetch(snap)
    with CSV_930["Jul_Dec"].open(newline="") as fh:
        header = next(csv.reader(fh))
    assert "Net Generation (MW) from Wind (Adjusted)" not in header
    hours = eia.load_hours(snap, "SWPP", 2024)
    july = [h for h in hours if h["utc"].month == 7]
    assert len(july) == 48 and all(h["wind_share"] is not None for h in july)
    row = next(
        r for r in _sample_swpp() if r["UTC Time at End of Hour"] == "07/15/2024 10:00:00 PM"
    )
    col = "Net Generation (MW) from Wind {} Integrated Battery Storage (Adjusted)"
    wind = float(row[col.format("without")]) + float(row[col.format("with")] or 0)
    netgen = float(row["Net Generation (MW) (Adjusted)"])
    peak_hour = next(h for h in july if h["utc"] == datetime(2024, 7, 15, 22, tzinfo=timezone.utc))
    assert peak_hour["wind_share"] == pytest.approx(wind / netgen)


def test_eia_lines_header_only_unobserved(snap):
    eia.fetch(snap)
    eia.normalize(snap)
    assert (snap / "normalized" / "eia_lines.csv").read_bytes() == b"line_id,voltage_kv\n"
    m = _manifest(snap)
    entry = m["tables"]["eia_lines"]
    assert (entry["coverage"], entry["rows"], entry["source"]) == ("unobserved", 0, "eia_860_861")
    assert entry["source_version"] == "pudl_v2024.11.0:eia860_2024"
    assert "no line-level voltages" in entry["note"]
    assert entry["retrieved_at"] == max(
        m["raw"][f"raw:{t}"]["retrieved_at"] for t in (eia.PLANTS, eia.GENERATORS)
    )


def test_load_930_stamp(snap):
    eia.fetch(snap)
    eia.normalize(snap)
    m = _manifest(snap)
    entry = m["tables"]["load_930"]
    stamp = (entry["source"], entry["source_version"], entry["coverage"])
    assert stamp == ("eia_930", "eia930_2024", "observed")
    assert entry["retrieved_at"] == max(
        m["raw"][f"raw:eia930_2024_{h}"]["retrieved_at"] for h in eia.EIA930_HALVES
    )
    assert "no API key" in entry["note"]
