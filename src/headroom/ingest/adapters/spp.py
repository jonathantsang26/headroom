"""SPP adapter: DA binding constraints -> constraints + congestion, the GI active
queue -> queue (POIs mapped onto buses), and planning (no machine-readable ITP
source, so header-only and unobserved)."""

from __future__ import annotations

import csv
import io
import json
import math
import re
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from headroom.ingest.adapters import common
from headroom.ingest.snapshot import MANIFEST_NAME, sha256_of

DA_BC_URL = (
    "https://portal.spp.org/file-browser-api/download/da-binding-constraints"
    "?path=%2F{year}%2FDA-BC-YEARLY-{year}.csv.zip"
)
QUEUE_URL = "https://opsportal.spp.org/Studies/GenerateActiveCSV"
QUEUE_RAW = "raw:spp_gi_active_queue"
MAX_CSV_BYTES = 512 << 20  # uncompressed cap for the zipped DA-BC CSV

BC_COLUMNS = (
    "Interval", "GMTIntervalEnd", "Constraint Name", "Constraint Type", "State",
    "Shadow Price", "Monitored Facility", "Contingent Facility", "Contingency Name",
)
QUEUE_COLUMNS = (
    "Generation Interconnection Number", "State", "TO at POI", "Capacity",
    "Generation Type", "Fuel Type", "Substation or Line", "Date Withdrawn", "Status",
)
CONSTRAINT_FIELDS = [
    "constraint_id", "monitored_line", "contingency_line", "ctype",
    "monitored_facility", "contingency_facility", "contingency_name",
    "spp_constraint_type", "contingency_to", "contingency_kv",
]
CONGESTION_FIELDS = [
    "constraint_id", "monthly_cost_musd", "hours_binding",
    "shadow_price_sum_usd_per_mw", "max_abs_shadow_price", "breached_hours", "period",
]
PLANNING_FIELDS = ["constraint_id", "plan", "proposed_fix", "assigned_cost_musd"]
QUEUE_FIELDS = [
    "request_id", "near_bus", "mw", "fuel", "poi", "state", "to_at_poi", "status", "match",
]

_TIME = "%m/%d/%Y %H:%M:%S"
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_TO_CODES = re.compile(r"[A-Z][A-Z0-9&_]*(?: [A-Z][A-Z0-9&_]*)*")
_POI_STOPWORDS = frozenset({
    "SUBSTATION", "SUBSTA", "SUB", "SWITCHING", "SWITCHINGSTATION", "SWITCH",
    "STATION", "SWITCHYARD", "BUS", "KV",
})


def _data_year() -> int:
    return int(common.region_spec()["spp_data_year"])


def _bc_member(year: int) -> str:
    return f"DA-BC-YEARLY-{year}.csv"


def _bc_raw_name(year: int) -> str:
    return f"raw:spp_da_bc_{year}"


def _bc_pin(path: Path, year: int) -> str:
    """Pin check from the archive itself: exactly one member, the year's CSV."""
    with zipfile.ZipFile(path) as z:
        names = [i.filename for i in z.infolist()]
    if names != [_bc_member(year)]:
        raise ValueError(f"SPP DA-BC archive holds {names}; expected [{_bc_member(year)!r}]")
    return common.require_pin("spp_binding_constraints", f"spp_da_bc_{year}")


def _queue_pin(text: str) -> str:
    """Pin from the CSV's first line: "Last Updated On",M/D/YYYY,"""
    first = next(csv.reader(io.StringIO(text)), [])
    if len(first) < 2 or first[0].strip() != "Last Updated On":
        raise ValueError("SPP GI queue CSV does not start with a 'Last Updated On' line")
    updated = datetime.strptime(first[1].strip(), "%m/%d/%Y").date().isoformat()
    return common.require_pin("spp_queue", f"spp_giq_{updated}")


def fetch(snapshot_dir: Path) -> dict:
    """Download the DA-BC zip and the GI active queue CSV verbatim into raw/spp/."""
    year = _data_year()
    raw = snapshot_dir / "raw" / "spp"
    bc_url = DA_BC_URL.format(year=year)
    bc_path = common.download(bc_url, raw / f"{_bc_member(year)}.zip", max_mb=100)
    q_path = common.download(QUEUE_URL, raw / "gi_active_queue.csv", max_mb=20)
    entries = {
        _bc_raw_name(year): common.record_raw(snapshot_dir, _bc_raw_name(year), bc_url, bc_path),
        QUEUE_RAW: common.record_raw(snapshot_dir, QUEUE_RAW, QUEUE_URL, q_path),
    }
    _bc_pin(bc_path, year)
    _queue_pin(q_path.read_bytes().decode("utf-8-sig"))
    return {"bytes": sum(e["bytes"] for e in entries.values()), "raw": entries}


def _read_bc(path: Path, year: int) -> list[dict]:
    """Rows of the zipped DA-BC CSV (headers stripped: SPP writes ' Contingency Name')."""
    with zipfile.ZipFile(path) as z:
        info = z.getinfo(_bc_member(year))
        if info.file_size > MAX_CSV_BYTES:
            raise ValueError(f"{info.filename} is {info.file_size} bytes uncompressed")
        text = z.read(info).decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text, newline=""))
    header = [h.strip() for h in next(reader)]
    missing = [c for c in BC_COLUMNS if c not in header]
    if missing:
        raise ValueError(f"SPP DA-BC CSV lacks columns {missing}")
    return [dict(zip(header, (v.strip() for v in r))) for r in reader if r]


def _mode(counts: Counter) -> str:
    """Most frequent value; ties go to the lexicographically first."""
    return min(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]


def _element(monitored: str) -> str:
    if not monitored:
        return "blank"
    if monitored == "Multi-Element Constraint":
        return "multi"
    head = monitored.split(" ", 1)[0]
    return head.lower() if head in ("LN", "XFMR") else "other"


def _contingency_parts(facility: str) -> tuple[str, str]:
    """TO code(s) before the first colon and kV as the third colon field, when
    the text has that shape (SPP writes many free-form variants)."""
    if facility == "BASE" or ":" not in facility:
        return "", ""
    parts = facility.split(":")
    to = parts[0].strip() if _TO_CODES.fullmatch(parts[0].strip()) else ""
    kv = parts[2].strip() if len(parts) > 2 and _NUMBER.fullmatch(parts[2].strip()) else ""
    return to, kv


def _line_key(monitored: str) -> str:
    """An LN element with endpoint order ignored ('LN A - B' == 'LN B - A')."""
    ends = monitored.split(" ", 1)[1].split(" - ")
    return " - ".join(sorted(e.strip() for e in ends))


def _constraints_and_congestion(snapshot_dir: Path) -> dict:
    year = _data_year()
    entry = common.raw_entry(snapshot_dir, _bc_raw_name(year))
    version = _bc_pin(entry["abspath"], year)
    rows = _read_bc(entry["abspath"], year)

    seen: dict[tuple, tuple] = {}
    dupes = conflicting = 0
    total_abs = []
    acc: dict[str, dict] = defaultdict(lambda: {
        "hours": set(), "breached": set(), "abs": [], "mon": Counter(), "cf": Counter(),
        "stype": Counter(), "cname": "",
    })
    for r in rows:
        name, cname, mon = r["Constraint Name"], r["Contingency Name"], r["Monitored Facility"]
        if not name or not cname or "|" in name or "|" in cname:
            raise ValueError(f"SPP DA-BC row has an unusable constraint name: {name!r}|{cname!r}")
        # Interval is hour-ending local time: 01/01/<year+1> 00:00 is <year>'s last hour.
        if (datetime.strptime(r["Interval"], _TIME) - timedelta(hours=1)).year != year:
            raise ValueError(f"SPP DA-BC {year} file has an interval in another year: {r['Interval']}")
        hour = datetime.strptime(r["GMTIntervalEnd"], _TIME).isoformat()
        value = (r["Shadow Price"], r["State"], r["Contingent Facility"])
        key = (hour, name, mon, cname)
        if key in seen:
            dupes += 1
            conflicting += seen[key] != value
            continue
        seen[key] = value
        sp = abs(float(r["Shadow Price"]))
        total_abs.append(sp)
        a = acc[f"{name}|{cname}"]
        a["hours"].add(hour)
        if r["State"] == "BREACHED":
            a["breached"].add(hour)
        a["abs"].append(sp)
        a["mon"][mon] += 1
        a["cf"][r["Contingent Facility"]] += 1
        a["stype"][r["Constraint Type"]] += 1
        a["cname"] = cname

    constraints, congestion = [], []
    excluded = Counter()
    excl_hours = Counter()
    excl_abs: dict[str, list] = defaultdict(list)
    kinds = Counter()
    multi_mon, mixed, multi_cf, reversed_only, lines = [], 0, 0, 0, set()
    blank_names = []
    for cid in sorted(acc):
        a = acc[cid]
        monitored = _mode(a["mon"])
        element = _element(monitored)
        mixed += len({_element(m) for m in a["mon"]}) > 1
        if element in ("xfmr", "other"):
            excluded[element] += 1
            excl_hours[element] += len(a["hours"])
            excl_abs[element].extend(a["abs"])
            continue
        if len(a["mon"]) > 1:
            multi_mon.append(cid)
            reversed_only += element == "ln" and len({_line_key(m) for m in a["mon"]}) == 1
        multi_cf += len(a["cf"]) > 1
        kinds[element] += 1
        if element == "ln":
            lines.add(_line_key(monitored))
        if element == "blank":
            blank_names.append(cid)
        facility = _mode(a["cf"])
        to, kv = _contingency_parts(facility)
        constraints.append({
            "constraint_id": cid, "monitored_line": "", "contingency_line": "",
            "ctype": "thermal" if element == "ln" else "interface",
            "monitored_facility": monitored, "contingency_facility": facility,
            "contingency_name": a["cname"], "spp_constraint_type": _mode(a["stype"]),
            "contingency_to": to, "contingency_kv": kv,
        })
        congestion.append({
            "constraint_id": cid, "monthly_cost_musd": "",
            "hours_binding": len(a["hours"]),
            "shadow_price_sum_usd_per_mw": f"{round(math.fsum(a['abs']), 4):.4f}",
            "max_abs_shadow_price": f"{round(max(a['abs']), 4):.4f}",
            "breached_hours": len(a["breached"]), "period": str(year),
        })

    total = math.fsum(total_abs)
    n_base = sum(1 for c in constraints if c["contingency_name"] == "BASE")
    xfmr_share = math.fsum(excl_abs["xfmr"]) / total if total else 0.0
    other = (
        f" Excluded {excluded['other']} flowgates with an unrecognized monitored-element "
        f"prefix ({excl_hours['other']} constraint-hours)." if excluded["other"] else ""
    )
    stamp = {"source": "spp_binding_constraints", "source_version": version,
             "retrieved_at": entry["retrieved_at"]}
    common.write_table(
        snapshot_dir, "constraints", constraints, CONSTRAINT_FIELDS, **stamp,
        coverage="partial",
        note=(
            f"SPP DA binding constraints {year}: {len(acc)} flowgates "
            f"(Constraint Name|Contingency Name) in {len(rows)} hourly rows. Kept "
            f"{len(constraints)}: {kinds['ln']} thermal (LN monitored element) and "
            f"{kinds['multi'] + kinds['blank']} interface ({kinds['multi']} Multi-Element, "
            f"{kinds['blank']} with a blank monitored element: {', '.join(blank_names) or 'none'}; "
            "the blank ones are named as phase-shifter (PHSHFT_PS) constraints and are "
            "classed interface per the table contract). Excluded "
            f"{excluded['xfmr']} transformer (XFMR) flowgates: {excl_hours['xfmr']} "
            f"constraint-hours, {xfmr_share:.1%} of total |shadow price| over all "
            f"flowgates.{other} {len(lines)} distinct monitored lines (endpoint order "
            f"ignored). {len(multi_mon)} flowgates carry >1 monitored-facility spelling, "
            f"{reversed_only} of them only reversed endpoints ({', '.join(multi_mon) or 'none'}); "
            f"{multi_cf} carry >1 "
            "contingent-facility spelling; each is one row with the most frequent spelling "
            f"(ties: first sorted). {mixed} flowgates mix element types (classed by the "
            "most frequent). Monitored elements not resolved to public topology: "
            "monitored_line and contingency_line are blank for every row (SPP names are "
            "market-model bus names; the bus-location model is CEII); contingency_line is "
            f"also inherently blank for the {n_base} BASE (no-contingency) flowgates."
        ),
    )
    common.write_table(
        snapshot_dir, "congestion", congestion, CONGESTION_FIELDS, **stamp,
        coverage="observed",
        note=(
            f"SPP Day-Ahead market only (no real-time), {year}, one row per constraints "
            "row. hours_binding = distinct UTC hours (GMTIntervalEnd) the flowgate was "
            "BINDING or BREACHED; breached_hours counts BREACHED. Local Interval labels "
            "repeat on the DST fall-back hour; those are distinct UTC hours with their own "
            "shadow prices and are kept. Exact duplicate rows "
            "(GMTIntervalEnd, Constraint Name, Monitored Facility, Contingency Name) "
            f"removed: {dupes} ({conflicting} with differing values; first kept). "
            "shadow_price_sum_usd_per_mw = sum of |shadow price| over those hours, in $/MW "
            "(not $): SPP posts no $ congestion figure and the ratings needed for rent are "
            "not public, so monthly_cost_musd is blank."
        ),
    )
    return entry


def _planning(snapshot_dir: Path, bc_entry: dict) -> None:
    common.write_table(
        snapshot_dir, "planning", [], PLANNING_FIELDS,
        source="spp_itp", source_version=common.registry_pin("spp_itp"),
        retrieved_at=bc_entry["retrieved_at"], coverage="unobserved",
        note=(
            "No machine-readable SPP ITP source resolved: ITP named constraints, proposed "
            "fixes and assigned costs are published as PDF only, and the spp.org document "
            "path probed (documents/75192) returned 404. Nothing was retrieved for "
            "planning; retrieved_at is this build's DA binding-constraint retrieval time, "
            "not a planning retrieval. Header-only: unobserved, never zero."
        ),
    )


def poi_key(name: str) -> str:
    """Comparable substation name: uppercase, parentheticals, punctuation, voltage
    tokens (345 kV, 115, 69kV) and words like SUBSTATION/SWITCHING/STATION removed."""
    s = re.sub(r"\([^)]*\)", " ", name.upper())
    s = re.sub(r"(\d)(KV|LINE|SUBSTATION|SWITCHING|STATION|SUB|BUS)\b", r"\1 \2", s)
    s = re.sub(r"[^A-Z0-9.]+", " ", s)
    s = re.sub(r"\b\d+(?:\.\d+)?\s*KV\b", " ", s)
    tokens = (t.strip(".") for t in s.split())
    return " ".join(
        t for t in tokens if t and t not in _POI_STOPWORDS and not _NUMBER.fullmatch(t)
    )


def is_active(row: dict) -> bool:
    """Active = not withdrawn and not already in commercial operation."""
    status = row.get("Status", "").upper()
    return not (
        "COMMERCIAL OPERATION" in status or "WITHDRAW" in status or row.get("Date Withdrawn")
    )


def _buses(snapshot_dir: Path) -> tuple[dict[str, list[str]], dict] | None:
    """Bus ids by poi_key(name), from a stamped normalized/buses.csv (if present)."""
    path = snapshot_dir / "normalized" / "buses.csv"
    if not path.is_file():
        return None
    entry = json.loads((snapshot_dir / MANIFEST_NAME).read_text())["tables"].get("buses")
    if not isinstance(entry, dict) or entry.get("sha256") != sha256_of(path):
        raise ValueError("normalized/buses.csv is not stamped in the manifest or fails its sha256")
    index: dict[str, list[str]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as fh:
        for b in csv.DictReader(fh):
            key = poi_key(b["name"] or "")
            if key:
                index[key].append(b["bus_id"])
    stamp = {k: entry[k] for k in ("source", "source_version", "retrieved_at")}
    return index, stamp


def _queue(snapshot_dir: Path) -> None:
    entry = common.raw_entry(snapshot_dir, QUEUE_RAW)
    text = entry["abspath"].read_bytes().decode("utf-8-sig")
    version = _queue_pin(text)
    reader = csv.reader(io.StringIO(text, newline=""))
    next(reader)  # "Last Updated On" line, checked by _queue_pin
    header = [h.strip() for h in next(reader)]
    missing = [c for c in QUEUE_COLUMNS if c not in header]
    if missing:
        raise ValueError(f"SPP GI queue CSV lacks columns {missing}")
    records = [dict(zip(header, (v.strip() for v in r))) for r in reader if any(r)]

    buses = _buses(snapshot_dir)
    out, ids = [], set()
    n_commercial = n_withdrawn = n_nocap = n_fallback = n_nofuel = 0
    kept_status, match_n, match_mw = Counter(), Counter(), Counter()
    for r in records:
        if not is_active(r):
            n_commercial += "COMMERCIAL OPERATION" in r["Status"].upper()
            n_withdrawn += "COMMERCIAL OPERATION" not in r["Status"].upper()
            continue
        try:
            mw = float(r["Capacity"].replace(",", ""))
        except ValueError:
            n_nocap += 1
            continue
        rid = r["Generation Interconnection Number"]
        if not rid or rid in ids:
            raise ValueError(f"SPP GI queue has a blank or repeated request id: {rid!r}")
        ids.add(rid)
        fuel = r["Fuel Type"] or r["Generation Type"]
        n_fallback += not r["Fuel Type"] and bool(r["Generation Type"])
        n_nofuel += not fuel
        poi = " ".join(r["Substation or Line"].split())
        near_bus = match = ""
        if buses is not None:
            hits = buses[0].get(poi_key(poi), []) if poi_key(poi) else []
            match = "exact" if len(hits) == 1 else "ambiguous" if hits else "none"
            near_bus = hits[0] if len(hits) == 1 else ""
            match_n[match] += 1
            match_mw[match] += mw
        kept_status[r["Status"]] += 1
        out.append({
            "request_id": rid, "near_bus": near_bus, "mw": mw, "fuel": fuel, "poi": poi,
            "state": r["State"], "to_at_poi": r["TO at POI"], "status": r["Status"],
            "match": match,
        })
    out.sort(key=lambda q: q["request_id"])

    total_mw = math.fsum(q["mw"] for q in out)
    if buses is None:
        matching = ("normalized/buses.csv absent: near_bus not attempted (blank for every "
                    "row, match blank).")
    else:
        share = match_mw["exact"] / total_mw if total_mw else 0.0
        matching = (
            "near_bus set only when the POI's normalized name (uppercase; parentheticals, "
            "punctuation, voltage tokens and SUBSTATION/SWITCHING/STATION/SUB/SWITCHYARD/"
            "BUS words removed) equals exactly one bus name normalized the same way: "
            f"exact {match_n['exact']} ({share:.1%} of MW), ambiguous {match_n['ambiguous']}, "
            f"none {match_n['none']} (line and tap POIs never match a bus)."
        )
    statuses = ", ".join(f"{s or '(blank)'} {n}" for s, n in sorted(kept_status.items()))
    common.write_table(
        snapshot_dir, "queue", out, QUEUE_FIELDS,
        source="spp_queue", source_version=version, retrieved_at=entry["retrieved_at"],
        coverage="partial",
        contributors=[buses[1]] if buses is not None else None,
        note=(
            f"SPP GI active queue CSV ({version}): {len(records)} requests. Excluded "
            f"{n_commercial} whose Status contains COMMERCIAL OPERATION (IA FULLY "
            f"EXECUTED/COMMERCIAL OPERATION: already in service) and {n_withdrawn} withdrawn "
            "(Status contains WITHDRAW or Date Withdrawn set; the active export already "
            "omits them). Kept "
            f"{len(out)} active requests, {total_mw:.1f} MW; {n_nocap} skipped for a "
            f"missing Capacity. Kept statuses: {statuses}. fuel = Fuel Type, else "
            f"Generation Type ({n_fallback} rows); blank for {n_nofuel}. {matching}"
        ),
    )


def normalize(snapshot_dir: Path) -> None:
    """Write constraints, congestion, planning and queue from the recorded raw files."""
    bc_entry = _constraints_and_congestion(snapshot_dir)
    _planning(snapshot_dir, bc_entry)
    _queue(snapshot_dir)
