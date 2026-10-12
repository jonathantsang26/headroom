"""PUDL / FERC Form 1 adapter: schedule 422 transmission-line statistics for the SPP
respondents (latest report year in the pinned PUDL release), joined onto HIFLD line
ids only where the join is unambiguous."""

from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from headroom.ingest.adapters.common import (
    download,
    raw_entry,
    record_raw,
    region_spec,
    registry_pin,
    write_table,
)
from headroom.ingest.snapshot import MANIFEST_NAME, sha256_of

SOURCE = "pudl_ferc_form1"
TABLE = "out_ferc1__yearly_transmission_lines_sched422"  # carries utility_name_ferc1
RAW_NAME = f"raw:{TABLE}"
S3_BASE = "https://s3.us-west-2.amazonaws.com/pudl.catalyst.coop"

FIELDS = [
    "line_id", "voltage_kv", "conductor", "thermal_limit_mw", "cost_usd",
    "record_id", "utility_id_ferc1", "utility_name", "report_year",
    "start_point", "end_point", "length_mi", "standalone_mi", "aggregated_mi",
    "structure", "num_circuits", "voltage_as_reported", "flags", "match", "match_evidence",
]
PARQUET_COLUMNS = (
    "record_id", "utility_id_ferc1", "utility_name_ferc1", "report_year",
    "start_point", "end_point", "operating_voltage_kv",
    "transmission_line_length_miles", "transmission_line_and_structures_length_miles",
    "num_transmission_circuits", "conductor_size_and_material",
    "supporting_structure_type", "capex_total",
)
HIFLD_COLUMNS = ("line_id", "voltage_kv", "sub_1", "sub_2")
DROP_ORDER = ("no_voltage", "no_length", "missing_endpoint")  # first failing check wins
AGGREGATE_FLAGS = ("same_endpoints", "aggregate_name")

# ANSI C84.1 nominal system voltages (kV): a value >= 1000 is read as volts only
# when dividing by 1000 lands exactly on one of these.
NOMINAL_KV = frozenset({
    2.4, 4.16, 4.8, 6.9, 8.32, 12.0, 12.47, 13.2, 13.8, 20.78, 22.86, 23.0, 24.94,
    34.5, 46.0, 69.0, 115.0, 138.0, 161.0, 230.0, 345.0, 500.0, 765.0,
})

# Summary rows that survive the basic filters (a group, total or structure class,
# not one line between two named points). Kept with a flag, never joined.
_AGGREGATE_NAME = re.compile(
    r"^(SUB ?TOTAL|TOTAL|SUMMARY|GROUPED|LESS THAN|LESS |VARIOUS|ALL \d|ROUNDING)"
    r"|\bKV (LINES|SYSTEM)\b"
)

# Endpoint-name normalization for the HIFLD join, applied identically to FERC
# start/end points and HIFLD SUB_1/SUB_2, in this order:
#   1. uppercase; drop parentheticals, incl. one left open by truncation ("(WERE)",
#      "(J11-TX;01)", "ONETA ENERGY CENTER (CAL")
#   2. drop a leading circuit designator ("TL90-902 ", "TL133 ", "05 ", "19N ", "31-")
#   3. drop circuit suffixes ("LINE 2", "CKT 1", "CIRCUIT 3", "LN 4")
#   4. punctuation -> space; drop voltage tokens ("345KV", "161 KV")
#   5. drop facility words (SUBSTATION, SUB, SWITCHING, SWITCHYARD, SWYD, STATION,
#      STA, SW, PLANT, POWER PLANT, POWER STATION) and one trailing bare nominal
#      voltage ("CLARKSVILLE 345"); expand CO/CK/CRK/JCT ("POTTER CO" = "POTTER COUNTY")
#      and NW/NE/SE ("NW TEXARKANA"); SW stays a switch abbreviation and is dropped
#   6. compare with spaces removed ("LACYGNE" = "LA CYGNE")
# HIFLD placeholders (TAP<n>, UNKNOWN<n>, NOT AVAILABLE, NONE, blank) never match.
_PAREN = re.compile(r"\([^)]*\)?")
_DESIGNATOR = re.compile(r"^(TL[\d-]+[A-Z]?|\d+[A-Z]?)(\s+|-(?=[A-Z]))")
_CIRCUIT = re.compile(r"\b(LINE|LN|CKT|CIRCUIT)\s*#?\s*\d+[A-Z]?\b")
_PUNCT = re.compile(r"[^A-Z0-9 ]+")
_KV_TOKEN = re.compile(r"\b\d+(\.\d+)?\s*KV\b")
_FACILITY = re.compile(
    r"\b(POWER PLANT|POWER STATION|SUBSTATION|SUB|SWITCHING|SWITCHYARD|SWYD"
    r"|STATION|STA|SW|PLANT)\b"
)
_TRAILING_KV = re.compile(r"\s(69|115|138|161|230|345|500|765)$")
_ABBREV = {
    "CO": "COUNTY", "CK": "CREEK", "CRK": "CREEK", "JCT": "JUNCTION",
    "NW": "NORTHWEST", "NE": "NORTHEAST", "SE": "SOUTHEAST",
}
_PLACEHOLDER = re.compile(r"^((TAP|UNKNOWN)\s*\d*|NOT AVAILABLE|NONE|N/?A|)$")


def release_from_pin(pin: str) -> str:
    """`pudl_v2024.11.0` -> `v2024.11.0`. The parquet carries no version metadata,
    so the versioned S3 path IS the pin; nightly/stable are refused."""
    m = re.fullmatch(r"pudl_(v\d{4}\.\d+\.\d+)", pin)
    if not m:
        raise ValueError(
            f"{SOURCE} pin {pin!r} is not a versioned PUDL release (pudl_vYYYY.N.N); "
            "a snapshot never builds on nightly or stable."
        )
    return m.group(1)


def release_url(release: str) -> str:
    return f"{S3_BASE}/{release}/{TABLE}.parquet"


def fetch(snapshot_dir: Path) -> dict:
    url = release_url(release_from_pin(registry_pin(SOURCE)))
    path = download(url, snapshot_dir / "raw" / f"{TABLE}.parquet")
    return record_raw(snapshot_dir, RAW_NAME, url, path)


def fix_kv(kv: float) -> tuple[float, list[str]]:
    """Voltage in kV and its flags; volts are divided only onto a nominal kV."""
    if kv >= 1000:
        if round(kv / 1000, 3) in NOMINAL_KV:
            return round(kv / 1000, 3), ["kv_in_volts"]
        return kv, ["voltage_implausible"]
    return kv, []


def aggregate_flags(start: str, end: str) -> list[str]:
    s, e = start.upper().strip(), end.upper().strip()
    flags = []
    if s == e:
        flags.append("same_endpoints")
    if _AGGREGATE_NAME.search(s) or _AGGREGATE_NAME.search(e):
        flags.append("aggregate_name")
    return flags


def norm_endpoint(name: str | None) -> str:
    """Join key for one endpoint name ("" when nothing usable is left)."""
    s = _PAREN.sub(" ", (name or "").upper()).strip()
    stripped = _DESIGNATOR.sub("", s, count=1)
    s = stripped if stripped.strip() else s
    s = _CIRCUIT.sub(" ", s)
    s = _KV_TOKEN.sub(" ", _PUNCT.sub(" ", s))
    s = " ".join(_ABBREV.get(t, t) for t in _FACILITY.sub(" ", s).split())
    return _TRAILING_KV.sub("", s)


def join_key(name: str | None) -> str:
    return norm_endpoint(name).replace(" ", "")


def is_placeholder(name: str | None) -> bool:
    return bool(_PLACEHOLDER.match(" ".join((name or "").upper().split())))


def _float(text) -> float | None:
    if text is None or str(text).strip() == "":
        return None
    return float(text)


def _num(x: float | None) -> str:
    if x is None:
        return ""
    x = round(x, 6)
    return str(int(x)) if x == int(x) else repr(x)


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _kv_key(kv: float) -> float:
    return round(kv, 1)


def classify_row(r: dict) -> tuple[str | None, dict]:
    """(drop reason or None, contract fields) for one schedule-422 row."""
    kv_raw = _float(r["operating_voltage_kv"])
    sa = _float(r["transmission_line_length_miles"])
    ag = _float(r["transmission_line_and_structures_length_miles"])
    start, end = _text(r["start_point"]), _text(r["end_point"])
    length = None if sa is None and ag is None else round((sa or 0.0) + (ag or 0.0), 6)
    if kv_raw is None or kv_raw <= 0:
        return "no_voltage", {}
    if length is None or length <= 0:
        return "no_length", {}
    if not start or not end:
        return "missing_endpoint", {}
    kv, flags = fix_kv(kv_raw)
    flags += aggregate_flags(start, end)
    if (sa or 0) > 0 and (ag or 0) > 0:
        flags.append("both_length_columns")  # often a 2nd circuit on shared structures
    if (sa or 0) < 0 or (ag or 0) < 0:
        flags.append("negative_length_part")
    capex = _float(r["capex_total"])
    if capex == 0:
        flags.append("capex_zero")  # not itemized on this row; blank, never a $0 cost
    circuits = r["num_transmission_circuits"]
    return None, {
        "line_id": "",
        "voltage_kv": _num(kv),
        "conductor": _text(r["conductor_size_and_material"]),
        "thermal_limit_mw": "",  # Form 1 reports no thermal rating
        "cost_usd": _num(capex) if capex else "",
        "record_id": _text(r["record_id"]),
        "utility_id_ferc1": str(r["utility_id_ferc1"]),
        "utility_name": _text(r["utility_name_ferc1"]),
        "report_year": str(r["report_year"]),
        "start_point": start,
        "end_point": end,
        "length_mi": _num(length),
        "standalone_mi": _num(sa),
        "aggregated_mi": _num(ag),
        "structure": _text(r["supporting_structure_type"]),
        "num_circuits": "" if circuits is None else str(circuits),
        "voltage_as_reported": _num(kv_raw),
        "flags": ";".join(flags),
        "match": "none",
        "match_evidence": "",
    }


def hifld_index(hifld_rows: list[dict]) -> dict[tuple, list[str]]:
    """(sorted normalized SUB pair, kV) -> HIFLD line ids; placeholder ends skipped."""
    index: dict[tuple, list[str]] = defaultdict(list)
    for h in hifld_rows:
        kv = _float(h.get("voltage_kv"))
        a, b = h.get("sub_1"), h.get("sub_2")
        if kv is None or is_placeholder(a) or is_placeholder(b):
            continue
        na, nb = join_key(a), join_key(b)
        if na and nb:
            index[(*sorted((na, nb)), _kv_key(kv))].append(h["line_id"])
    return index


def _ids(ids: list[str], limit: int = 5) -> str:
    shown = " ".join(sorted(ids)[:limit])
    return shown + (f" +{len(ids) - limit} more" if len(ids) > limit else "")


def match_hifld(rows: list[dict], index: dict[tuple, list[str]]) -> Counter:
    """Set line_id/match/match_evidence in place. A row is `exact` only when both
    endpoint names and the voltage name exactly one HIFLD line and no other FERC row
    claims that line; returns the ambiguity causes."""
    causes: Counter = Counter()
    claims: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if any(f in AGGREGATE_FLAGS for f in r["flags"].split(";")):
            r["match_evidence"] = "aggregate row; not joined"
            continue
        a, b = norm_endpoint(r["start_point"]), norm_endpoint(r["end_point"])
        if not a or not b:
            r["match_evidence"] = "endpoint name empty after normalization"
            continue
        kv = _kv_key(float(r["voltage_kv"]))
        key = f"{a}|{b} @ {_num(kv)} kV"
        pair = sorted((a.replace(" ", ""), b.replace(" ", "")))
        candidates = index.get((*pair, kv), [])
        if not candidates:
            r["match_evidence"] = f"no HIFLD line {key}"
        elif len(candidates) > 1:
            r["match"] = "ambiguous"
            r["match_evidence"] = f"{len(candidates)} HIFLD lines {key}: {_ids(candidates)}"
            causes["multiple_hifld_lines"] += 1
        else:
            r["match_evidence"] = key
            claims[candidates[0]].append(r)
    for line_id, claimants in claims.items():
        if len(claimants) == 1:
            claimants[0].update(line_id=line_id, match="exact")
            continue
        for r in claimants:
            r["match"] = "ambiguous"
            r["match_evidence"] = (
                f"{line_id} claimed by {len(claimants)} FERC rows ({r['match_evidence']})"
            )
        causes["shared_hifld_line"] += len(claimants)
    return causes


def _read_hifld(snapshot_dir: Path) -> tuple[list[dict], dict]:
    path = snapshot_dir / "normalized" / "hifld_lines.csv"
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} is missing: the FERC 1 join needs hifld_lines, so hifld.normalize "
            "must run before pudl_ferc1.normalize."
        )
    entry = json.loads((snapshot_dir / MANIFEST_NAME).read_text()).get("tables", {}).get(
        "hifld_lines"
    )
    if not isinstance(entry, dict) or entry.get("sha256") != sha256_of(path):
        raise ValueError(f"{path} does not match its manifest stamp; refusing to join on it")
    with io.StringIO(path.read_text(encoding="utf-8"), newline="") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in HIFLD_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"hifld_lines.csv lacks join column(s) {', '.join(missing)}")
        rows = list(reader)
    stamp = {k: entry[k] for k in ("source", "source_version", "retrieved_at")}
    return rows, stamp


def _read_release(path: Path, respondents: list[int]) -> tuple[int, list[dict]]:
    """(latest report_year in the release, that year's rows for the respondents)."""
    import duckdb

    con = duckdb.connect()
    p = str(path)
    have = {c for c, *_ in con.execute("DESCRIBE SELECT * FROM read_parquet(?)", [p]).fetchall()}
    missing = [c for c in PARQUET_COLUMNS if c not in have]
    if missing:
        raise ValueError(f"{TABLE} parquet lacks column(s) {', '.join(missing)}")
    year = con.execute("SELECT max(report_year) FROM read_parquet(?)", [p]).fetchone()[0]
    if year is None:
        raise ValueError(f"{TABLE} parquet has no report years")
    as_text = ("operating_voltage_kv", "transmission_line_length_miles",
               "transmission_line_and_structures_length_miles", "capex_total")
    cols = ", ".join(f"CAST({c} AS VARCHAR) AS {c}" if c in as_text else c
                     for c in PARQUET_COLUMNS)
    cur = con.execute(
        f"SELECT {cols} FROM read_parquet(?) WHERE report_year = ? "
        "AND utility_id_ferc1 IN (SELECT unnest(?::INTEGER[])) "
        "ORDER BY record_id, start_point, end_point",
        [p, year, respondents],
    )
    names = [d[0] for d in cur.description]
    return int(year), [dict(zip(names, t)) for t in cur.fetchall()]


def _mi(rows: list[dict], col: str) -> float:
    return sum(float(r[col]) for r in rows if r[col])


def normalize(snapshot_dir: Path) -> None:
    raw = raw_entry(snapshot_dir, RAW_NAME)
    hifld_rows, hifld_stamp = _read_hifld(snapshot_dir)
    ids = sorted(int(k) for k in region_spec()["ferc1_respondents"])
    year, source_rows = _read_release(raw["abspath"], ids)

    kept: list[dict] = []
    dropped: Counter = Counter()
    filed: Counter = Counter()
    for r in source_rows:
        filed[r["utility_id_ferc1"]] += 1
        reason, row = classify_row(r)
        if reason:
            dropped[reason] += 1
        else:
            kept.append(row)
    causes = match_hifld(kept, hifld_index(hifld_rows))

    flag_counts = Counter(f for r in kept for f in r["flags"].split(";") if f)
    lines = [r for r in kept if not any(f in AGGREGATE_FLAGS for f in r["flags"].split(";"))]
    exact = [r for r in kept if r["match"] == "exact"]
    match_counts = Counter(r["match"] for r in kept)
    per_resp = Counter(int(r["utility_id_ferc1"]) for r in kept)
    line_mi = _mi(lines, "length_mi")
    exact_mi = _mi(exact, "length_mi")
    share = exact_mi / line_mi if line_mi else 0.0
    no_rows = [i for i in ids if i not in filed]
    no_usable = [i for i in ids if i in filed and not per_resp[i]]

    note = (
        f"FERC Form 1 schedule 422 via PUDL {release_from_pin(registry_pin(SOURCE))} "
        f"{TABLE} (version = versioned S3 path; the parquet carries none), "
        f"report_year {year} (latest in the release). "
        f"Respondents: {len(ids)} configured; kept rows by utility_id_ferc1: "
        + ", ".join(f"{i}={per_resp[i]}" for i in ids if per_resp[i])
        + (f"; filed but no usable rows: {', '.join(map(str, no_usable))}" if no_usable else "")
        + (f"; no {year} rows: {', '.join(map(str, no_rows))}" if no_rows else "")
        + f". Source rows {len(source_rows)}; kept {len(kept)}; dropped "
        f"{sum(dropped.values())} ("
        + ", ".join(f"{k} {dropped[k]}" for k in DROP_ORDER)
        + "; first failing check in that order). "
        f"Flags on kept rows: "
        + (", ".join(f"{k} {v}" for k, v in sorted(flag_counts.items())) or "none")
        + ". same_endpoints/aggregate_name rows are group or structure-class summaries: "
        "kept for audit, never joined. kv_in_volts: kV reported in volts divided by 1000 "
        "(voltage_as_reported keeps the filing). length_mi = stand-alone + aggregated "
        f"pole-miles: {_mi(kept, 'standalone_mi'):,.1f} + {_mi(kept, 'aggregated_mi'):,.1f} "
        f"= {_mi(kept, 'length_mi'):,.1f} mi on kept rows, {line_mi:,.1f} mi excluding "
        "summary rows; both_length_columns rows (often a second circuit on shared "
        "structures) count both columns. cost_usd = capex_total (float32 in PUDL; blank "
        "when null or 0, flag capex_zero). thermal_limit_mw blank: Form 1 reports no rating. "
        f"HIFLD join (both normalized endpoint names, either order, + voltage, exactly one "
        f"line, one FERC row per line): exact {match_counts['exact']} rows, "
        f"{exact_mi:,.1f} mi = {share:.1%} of non-summary miles; ambiguous "
        f"{match_counts['ambiguous']} ({causes['multiple_hifld_lines']} with several HIFLD "
        f"candidates, {causes['shared_hifld_line']} sharing one HIFLD line with another "
        f"FERC row, e.g. one line filed as structure/state segments or by two "
        f"respondents); none {match_counts['none']}. Unmatched rows keep a blank line_id. "
        "Respondent filings include lines outside SPP (e.g. NorthWestern Montana, Basin, "
        "SPS New Mexico); only the HIFLD join places a row in the footprint. "
        "Non-filers (NPPD, OPPD, LES, WFEC, GRDA, Sunflower, Midwest Energy, WAPA, "
        "munis/co-ops) file no schedule 422: unobserved, never zero."
    )
    write_table(
        snapshot_dir, "ferc1_lines", kept, FIELDS,
        source=SOURCE, source_version=registry_pin(SOURCE),
        retrieved_at=raw["retrieved_at"], coverage="partial", note=note,
        contributors=[hifld_stamp],
    )
