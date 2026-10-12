"""Cross-check the FERC 1 adapter against echo-gridlock's PUDL reading (standalone;
never imported by the pipeline, never an input to a snapshot).

echo-gridlock's metric: latest report year; operating_voltage_kv and
transmission_line_and_structures_length_miles not null and > 0; start/end not null.
Corridors: kV >= 345, capex_total / miles > 0, miles >= 10, kV <= 1000, start != end.
The corrected metric sums both length columns (stand-alone + aggregated pole-miles)
and reads kV reported in volts as kV (the adapter's rule), then applies the same
corridor filter.

    python scripts/crosscheck_echo_gridlock.py PINNED.parquet RELEASE_2026.parquet
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import duckdb
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from headroom.ingest.adapters.pudl_ferc1 import NOMINAL_KV  # noqa: E402

GETS_CSV = Path.home() / "Downloads" / "Grid" / "gets_deployment_prioritization.csv"
SA = "transmission_line_length_miles"
AG = "transmission_line_and_structures_length_miles"
NOMINAL_SQL = ", ".join(str(v) for v in sorted(NOMINAL_KV))
KV_FIXED = (
    f"CASE WHEN kv >= 1000 AND round(kv / 1000, 3) IN ({NOMINAL_SQL}) "
    "THEN round(kv / 1000, 3) ELSE kv END"
)
MILES_BOTH = (
    f"CASE WHEN {SA} IS NULL AND {AG} IS NULL THEN NULL "
    f"ELSE coalesce({SA}, 0) + coalesce({AG}, 0) END"
)


def _load(con, name: str, path: Path) -> None:
    con.execute(
        f"CREATE OR REPLACE TABLE {name} AS SELECT record_id, report_year, utility_id_ferc1, "
        f"utility_name_ferc1, start_point, end_point, operating_voltage_kv::DOUBLE AS kv, "
        f"{SA}::DOUBLE AS {SA}, {AG}::DOUBLE AS {AG}, capex_total::DOUBLE AS capex_total "
        "FROM read_parquet(?)",
        [str(path)],
    )


def _release(con, t: str) -> dict:
    n, y0, y1 = con.execute(
        f"SELECT count(*), min(report_year), max(report_year) FROM {t}"
    ).fetchone()
    return {"rows": n, "report_years": [y0, y1]}


def _metric(con, t: str, *, corrected: bool, ids: list[int] | None = None) -> dict:
    kv = KV_FIXED if corrected else "kv"
    mi = MILES_BOTH if corrected else AG
    ends = ("nullif(trim(start_point), '') IS NOT NULL AND nullif(trim(end_point), '') IS NOT NULL"
            if corrected else "start_point IS NOT NULL AND end_point IS NOT NULL")
    who = "AND utility_id_ferc1 IN (SELECT unnest(?::INTEGER[]))" if ids else ""
    con.execute(
        f"CREATE OR REPLACE TEMP TABLE clean AS SELECT *, {kv} AS kv_m, {mi} AS mi_m FROM {t} "
        f"WHERE report_year = (SELECT max(report_year) FROM {t}) {who}",
        [ids] if ids else [],
    )
    con.execute(
        f"CREATE OR REPLACE TEMP TABLE clean AS SELECT * FROM clean "
        f"WHERE kv_m IS NOT NULL AND kv_m > 0 AND mi_m IS NOT NULL AND mi_m > 0 AND {ends}"
    )
    year, lines, miles, sa, ag, utils = con.execute(
        f"SELECT max(report_year), count(*), sum(mi_m), sum({SA}), sum({AG}), "
        "count(DISTINCT utility_id_ferc1) FROM clean"
    ).fetchone()
    corridors, corridor_mi = con.execute(
        "SELECT count(*), sum(mi_m) FROM clean WHERE kv_m >= 345 AND capex_total / mi_m > 0 "
        "AND mi_m >= 10 AND kv_m <= 1000 AND start_point <> end_point"
    ).fetchone()
    out = {"report_year": year, "lines": lines, "miles": round(miles or 0, 1)}
    if corrected:
        out |= {"standalone_mi": round(sa or 0, 1), "aggregated_mi": round(ag or 0, 1)}
    return out | {"utilities": utils, "corridors": corridors,
                  "corridor_miles": round(corridor_mi or 0, 1)}


def _kv_in_volts(con, t: str, ids: list[int]) -> dict:
    latest = f"report_year = (SELECT max(report_year) FROM {t})"
    nominal = f"round(kv / 1000, 3) IN ({NOMINAL_SQL})"

    def count(where: str, params=()) -> dict:
        ge, ok = con.execute(
            f"SELECT count(*), count(*) FILTER (WHERE {nominal}) FROM {t} "
            f"WHERE kv >= 1000 AND {where}",
            list(params),
        ).fetchone()
        return {"kv_ge_1000": ge, "read_as_volts": ok}

    return {
        "all_years_national": count("TRUE"),
        "latest_year_national": count(latest),
        "latest_year_spp": count(
            f"{latest} AND utility_id_ferc1 IN (SELECT unnest(?::INTEGER[]))", [ids]
        ),
    }


def _gets(con, path: Path, ids: list[int]) -> dict:
    if not path.is_file():
        return {"path": str(path), "present": False}
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    names: dict[str, set[int]] = {}
    for t in ("rel_t", "pin_t"):
        pairs = con.execute(f"SELECT DISTINCT utility_id_ferc1, utility_name_ferc1 FROM {t}")
        for uid, name in pairs.fetchall():
            names.setdefault(name, set()).add(uid)
    spp = []
    for r in rows:
        uids = sorted(names.get(r["Utility"], set()) & set(ids))
        if not uids:
            continue
        hits = con.execute(
            f"SELECT {SA}, {AG} FROM pin_t "
            "WHERE report_year = (SELECT max(report_year) FROM pin_t) "
            "AND utility_id_ferc1 IN (SELECT unnest(?::INTEGER[])) "
            "AND upper(trim(start_point)) = upper(trim(?)) "
            "AND upper(trim(end_point)) = upper(trim(?)) "
            "AND kv = ?",
            [uids, r["From"], r["To"], float(r["kV"])],
        ).fetchall()
        spp.append({
            "From": r["From"], "To": r["To"], "Utility": r["Utility"], "kV": float(r["kV"]),
            "Miles": float(r["Miles"]), "utility_id_ferc1": uids,
            "in_pinned_latest_year": bool(hits),
            "pinned_rows": [{"standalone_mi": None if a is None else round(a, 2),
                             "aggregated_mi": None if b is None else round(b, 2)} for a, b in hits],
        })
    return {"path": str(path), "present": True, "corridors": len(rows),
            "spp_respondent_corridors": spp}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("pinned", type=Path, help="pinned PUDL release parquet (v2024.11.0)")
    ap.add_argument("release", type=Path, help="a 2026 PUDL release parquet (echo-gridlock's data)")
    ap.add_argument("--gets-csv", type=Path, default=GETS_CSV)
    ap.add_argument("--region-spec", type=Path, default=REPO / "config" / "regions" / "spp.yaml")
    args = ap.parse_args(argv)

    ids = sorted(int(k) for k in yaml.safe_load(args.region_spec.read_text())["ferc1_respondents"])
    con = duckdb.connect()
    _load(con, "pin_t", args.pinned)
    _load(con, "rel_t", args.release)
    out = {
        "releases": {"pinned": {"path": str(args.pinned), **_release(con, "pin_t")},
                     "release_2026": {"path": str(args.release), **_release(con, "rel_t")}},
        "echo_gridlock_metric": {"pinned": _metric(con, "pin_t", corrected=False),
                                 "release_2026": _metric(con, "rel_t", corrected=False)},
        "corrected_metric_pinned": {
            "national": _metric(con, "pin_t", corrected=True),
            "spp_respondents": _metric(con, "pin_t", corrected=True, ids=ids),
        },
        "echo_gridlock_metric_pinned_spp_respondents": _metric(
            con, "pin_t", corrected=False, ids=ids,
        ),
        "kv_in_volts_rows_pinned": _kv_in_volts(con, "pin_t", ids),
        "gets_deployment_prioritization": _gets(con, args.gets_csv, ids),
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
