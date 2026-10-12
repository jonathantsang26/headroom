"""EIA adapter: EIA-860 plants/generators (via the pinned PUDL release) and EIA-930
hourly balancing-authority demand (Grid Monitor six-month bulk CSVs; no API key)."""

from __future__ import annotations

import csv
import re
from datetime import datetime, timezone
from pathlib import Path

from headroom.ingest.adapters.common import (
    download,
    raw_entry,
    record_raw,
    region_spec,
    registry_pin,
    write_table,
)

PUDL_S3 = "https://s3.us-west-2.amazonaws.com/pudl.catalyst.coop"
PLANTS, GENERATORS = "out_eia__yearly_plants", "out_eia__yearly_generators"
EIA930_BASE = "https://www.eia.gov/electricity/gridmonitor/sixMonthFiles"
EIA930_HALVES = ("Jan_Jun", "Jul_Dec")
# EIA-860 operational_status_code values counted as operating capacity: OP
# (operating) and SB (standby/backup, available for service). OA/OS (out of
# service), RE (retired) and proposed codes (P, L, T, U, V, TS, OT) are excluded.
OPERATING_CODES = ("OP", "SB")
_PIN_860 = re.compile(r"pudl_(v\d{4}\.\d{1,2}\.\d{1,3}):eia860_(\d{4})")
_PIN_930 = re.compile(r"eia930_(\d{4})")

DEMAND_COLS = ("Demand (MW) (Adjusted)", "Demand (MW)")
NETGEN_COLS = ("Net Generation (MW) (Adjusted)", "Net Generation (MW)")
# Jan-Jun 2024 files carry one wind column; from Jul 2024 EIA splits it by
# integrated battery storage, so wind is the sum of the pair.
WIND_COLS = (
    ("Net Generation (MW) from Wind (Adjusted)",),
    ("Net Generation (MW) from Wind without Integrated Battery Storage (Adjusted)",
     "Net Generation (MW) from Wind with Integrated Battery Storage (Adjusted)"),
    ("Net Generation (MW) from Wind",),
    ("Net Generation (MW) from Wind without Integrated Battery Storage",
     "Net Generation (MW) from Wind with Integrated Battery Storage"),
)
LOAD_FIELDS = ["snapshot", "ba", "load_mw", "utc_hour", "basis", "wind_share"]


def pin_860() -> tuple[str, int]:
    """(PUDL release, EIA-860 report year) parsed from the eia_860_861 pin."""
    pin = registry_pin("eia_860_861")
    m = _PIN_860.fullmatch(pin)
    if not m:
        raise ValueError(f"eia_860_861 pin {pin!r} is not pudl_vYYYY.M.P:eia860_YYYY")
    return m.group(1), int(m.group(2))


def year_930() -> int:
    """The EIA-930 year: region spec and registry pin must agree."""
    pin = registry_pin("eia_930")
    m = _PIN_930.fullmatch(pin)
    year = region_spec()["eia930_year"]
    if not m or int(m.group(1)) != year:
        raise ValueError(f"eia_930 pin {pin!r} does not match region eia930_year {year}")
    return year


def pudl_url(table: str) -> str:
    release, _ = pin_860()
    return f"{PUDL_S3}/{release}/{table}.parquet"


def eia930_url(year: int, half: str) -> str:
    return f"{EIA930_BASE}/EIA930_BALANCE_{year}_{half}.csv"


def fetch(snapshot_dir: Path) -> dict:
    """Download the pinned PUDL EIA-860 tables and the year's EIA-930 bulk CSVs."""
    raw = snapshot_dir / "raw"
    total = 0
    for table in (PLANTS, GENERATORS):
        url = pudl_url(table)
        path = download(url, raw / f"{table}.parquet")
        total += record_raw(snapshot_dir, f"raw:{table}", url, path)["bytes"]
    year = year_930()
    for half in EIA930_HALVES:
        url = eia930_url(year, half)
        path = download(url, raw / f"EIA930_BALANCE_{year}_{half}.csv", timeout=300)
        total += record_raw(snapshot_dir, f"raw:eia930_{year}_{half}", url, path)["bytes"]
    return {"bytes": total}


def stamp_860(snapshot_dir: Path) -> dict:
    """The eia_860_861 stamp: pinned version, freshest retrieval of its two raw files."""
    entries = [raw_entry(snapshot_dir, f"raw:{t}") for t in (PLANTS, GENERATORS)]
    return {
        "source": "eia_860_861",
        "source_version": registry_pin("eia_860_861"),
        "retrieved_at": max(e["retrieved_at"] for e in entries),
    }


def load_plants(snapshot_dir: Path) -> list[dict]:
    """Operating EIA-860 plants for the pinned report year, sorted by plant id.

    A plant is operating when it has at least one generator with an
    OPERATING_CODES status that year; capacity_mw sums those generators'
    nameplate. lat/lon/ba come from the plants table and may be None."""
    import duckdb

    _, year = pin_860()
    plants = raw_entry(snapshot_dir, f"raw:{PLANTS}")["abspath"]
    gens = raw_entry(snapshot_dir, f"raw:{GENERATORS}")["abspath"]
    con = duckdb.connect()
    rows = con.execute(
        """
        WITH g AS (
            SELECT plant_id_eia, SUM(CAST(capacity_mw AS DOUBLE)) AS mw, COUNT(*) AS n
            FROM read_parquet($gens)
            WHERE report_date = make_date($year, 1, 1)
              AND list_contains($codes, operational_status_code)
              AND capacity_mw IS NOT NULL
            GROUP BY plant_id_eia
        ),
        p AS (
            SELECT plant_id_eia, plant_name_eia, CAST(latitude AS DOUBLE) AS lat,
                   CAST(longitude AS DOUBLE) AS lon, balancing_authority_code_eia AS ba
            FROM read_parquet($plants)
            WHERE report_date = make_date($year, 1, 1)
        )
        SELECT g.plant_id_eia, p.plant_name_eia, p.lat, p.lon, p.ba, g.mw, g.n
        FROM g LEFT JOIN p USING (plant_id_eia)
        WHERE g.mw > 0
        ORDER BY g.plant_id_eia
        """,
        {"gens": str(gens), "plants": str(plants), "year": year, "codes": list(OPERATING_CODES)},
    ).fetchall()
    con.close()
    if not rows:
        raise ValueError(f"EIA-860 has no operating generators for report year {year}")
    return [
        {
            "plant_id_eia": int(pid), "plant_name": name, "lat": lat, "lon": lon,
            "ba": (ba or "").strip() or None, "capacity_mw": round(mw, 3),
            "n_generators": int(n),
        }
        for pid, name, lat, lon, ba, mw, n in rows
    ]


def _num(row: dict, cols: tuple[str, ...]) -> tuple[float, str, str] | None:
    """First non-blank numeric column: (value, as-reported text, column name)."""
    for col in cols:
        text = (row.get(col) or "").replace(",", "").strip()
        if text:
            try:
                return float(text), text, col
            except ValueError:
                continue
    return None


def _wind(row: dict, header: list[str]) -> float | None:
    for group in WIND_COLS:
        if not all(c in header for c in group):
            continue
        vals = [(row.get(c) or "").replace(",", "").strip() for c in group]
        if any(vals):
            try:
                return sum(float(v) for v in vals if v)
            except ValueError:
                return None
    return None


def load_hours(snapshot_dir: Path, ba: str, year: int) -> list[dict]:
    """The BA's hourly rows for the year from both six-month files, by UTC hour."""
    hours: dict[datetime, dict] = {}
    for half in EIA930_HALVES:
        path = raw_entry(snapshot_dir, f"raw:eia930_{year}_{half}")["abspath"]
        with path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            header = list(reader.fieldnames or [])
            required = (
                "Balancing Authority", "UTC Time at End of Hour", "Data Date", "Demand (MW)",
            )
            for col in required:
                if col not in header:
                    raise ValueError(f"EIA-930 {half} file lacks column {col!r}")
            for row in reader:
                if (row.get("Balancing Authority") or "").strip() != ba:
                    continue
                if datetime.strptime(row["Data Date"].strip(), "%m/%d/%Y").year != year:
                    continue
                utc = datetime.strptime(
                    row["UTC Time at End of Hour"].strip(), "%m/%d/%Y %I:%M:%S %p",
                ).replace(tzinfo=timezone.utc)
                if utc in hours:
                    raise ValueError(f"EIA-930 {ba} has duplicate UTC hour {utc.isoformat()}")
                demand = _num(row, DEMAND_COLS)
                netgen = _num(row, NETGEN_COLS)
                wind = _wind(row, header)
                share = wind / netgen[0] if wind is not None and netgen and netgen[0] > 0 else None
                hours[utc] = {"utc": utc, "demand": demand, "wind_share": share}
    return [hours[k] for k in sorted(hours)]


def _load_rows(hours: list[dict], ba: str) -> tuple[list[dict], dict]:
    with_demand = [h for h in hours if h["demand"] is not None]
    if not with_demand:
        raise ValueError(f"EIA-930 has no {ba} demand rows")
    by_demand = sorted(with_demand, key=lambda h: (h["demand"][0], h["utc"]))
    peak = max(with_demand, key=lambda h: (h["demand"][0], -h["utc"].timestamp()))
    picks = {
        "peak": (peak, "max demand hour"),
        "offpeak": (by_demand[0], "min demand hour"),
        "shoulder": (by_demand[(len(by_demand) - 1) // 2], "median demand hour (lower median)"),
    }
    windy = [h for h in with_demand if h["wind_share"] is not None]
    if windy:
        top = max(windy, key=lambda h: (h["wind_share"], -h["utc"].timestamp()))
        picks["windhigh"] = (top, "max wind share of net generation")
    rows = []
    for snap, (h, why) in picks.items():
        value, text, col = h["demand"]
        rows.append({
            "snapshot": snap, "ba": ba, "load_mw": text,
            "utc_hour": h["utc"].isoformat(), "basis": f"{why}; {col}",
            "wind_share": "" if h["wind_share"] is None else f"{h['wind_share']:.4f}",
        })
    stats = {
        "hours": len(hours), "hours_with_demand": len(with_demand),
        "hours_with_wind_share": len(windy),
        "adjusted_demand_hours": sum(1 for h in with_demand if h["demand"][2] == DEMAND_COLS[0]),
    }
    return rows, stats


def normalize(snapshot_dir: Path) -> dict:
    """Write eia_lines (header-only, unobserved) and load_930 (BA snapshots)."""
    s860 = stamp_860(snapshot_dir)
    write_table(
        snapshot_dir, "eia_lines", [], ["line_id", "voltage_kv"],
        source=s860["source"], source_version=s860["source_version"],
        retrieved_at=s860["retrieved_at"], coverage="unobserved",
        note="EIA-860/861 publish no line-level voltages; nothing to corroborate HIFLD kV.",
    )

    ba, year = region_spec()["eia_ba_code"], year_930()
    entries = [raw_entry(snapshot_dir, f"raw:eia930_{year}_{h}") for h in EIA930_HALVES]
    rows, stats = _load_rows(load_hours(snapshot_dir, ba, year), ba)
    coverage = "observed" if len(rows) == 4 else "partial"
    write_table(
        snapshot_dir, "load_930", rows, LOAD_FIELDS,
        source="eia_930", source_version=registry_pin("eia_930"),
        retrieved_at=max(e["retrieved_at"] for e in entries), coverage=coverage,
        note=(
            f"{ba} {year} hourly demand from the EIA-930 six-month bulk CSVs (no API key): "
            f"{stats['hours_with_demand']} of {stats['hours']} hours have demand. load_mw is "
            "'Demand (MW) (Adjusted)' when present, else 'Demand (MW)' "
            f"({stats['adjusted_demand_hours']} adjusted hours). peak/offpeak = max/min "
            "demand hour, shoulder = lower-median demand hour, windhigh = max wind share "
            "of adjusted net generation (Jul-Dec wind = with + without integrated battery "
            "storage); ties go to the earliest UTC hour."
            + ("" if coverage == "observed" else " No hour had a wind share: windhigh missing.")
        ),
    )
    return {"load_930": rows, **stats}
