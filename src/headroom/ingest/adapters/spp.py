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

# Monthly archives, not DA-BC-YEARLY: the 2025 yearly file is a strict subset of the
# twelve monthly files (it lacks 6,344 FG rows, 123 constraints, 4.55% of |SP|).
DA_BC_URL = (
    "https://portal.spp.org/file-browser-api/download/da-binding-constraints"
    "?path=%2F{year}%2F{month:02d}%2FDA-BC-MONTHLY-{year}{month:02d}.csv.zip"
)
MONTHS = range(1, 13)
# SPP's per-flowgate DA congestion $ (sum over intervals of flow x shadow price) and
# TCR funding by constraint (settled DA Collected; used only as a calibration total).
CBC_URL = (
    "https://portal.spp.org/file-browser-api/download/congestion-by-constraint"
    "?path=%2F{year}%2FCongestion-Constraint-{year}{month:02d}.csv"
)
TCR_URL = (
    "https://portal.spp.org/file-browser-api/download/monthly-funding-by-constraint"
    "?path=%2F{year}%2FTCR-CONSTRAINT-{year}{month:02d}.csv"
)
CBC_COLUMNS = ("Date", "Constraint Name", "Monitored Facility", "Contingency Name", "Sum")
TCR_COLUMNS = ("Constraint Name", "DA Collected", "Month")
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
    "monitored_facility", "monitored_facility_as_reported", "contingency_facility",
    "contingency_name",
    "spp_constraint_type", "contingency_to", "contingency_kv",
]
CONGESTION_FIELDS = [
    "constraint_id", "monthly_cost_musd", "hours_binding",
    "shadow_price_sum_usd_per_mw", "max_abs_shadow_price", "breached_hours", "period",
    "months_binding", "rent_musd", "max_month_cost_musd", "rent_basis", "flags",
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


def _bc_member(year: int, month: int) -> str:
    return f"DA-BC-MONTHLY-{year}{month:02d}.csv"


def _bc_raw_name(year: int, month: int) -> str:
    return f"raw:spp_da_bc_{year}{month:02d}"


def _bc_pin(path: Path, year: int, month: int) -> str:
    """Pin check from the archive itself: exactly one member, that month's CSV."""
    with zipfile.ZipFile(path) as z:
        names = [i.filename for i in z.infolist()]
    if names != [_bc_member(year, month)]:
        raise ValueError(
            f"SPP DA-BC archive holds {names}; expected [{_bc_member(year, month)!r}]")
    return common.require_pin("spp_binding_constraints", f"spp_da_bc_monthly_{year}")


def _queue_pin(text: str) -> str:
    """Pin from the CSV's first line: "Last Updated On",M/D/YYYY,"""
    first = next(csv.reader(io.StringIO(text)), [])
    if len(first) < 2 or first[0].strip() != "Last Updated On":
        raise ValueError("SPP GI queue CSV does not start with a 'Last Updated On' line")
    updated = datetime.strptime(first[1].strip(), "%m/%d/%Y").date().isoformat()
    return common.require_pin("spp_queue", f"spp_giq_{updated}")


def fetch(snapshot_dir: Path) -> dict:
    """Download the twelve monthly DA-BC zips and the GI active queue CSV verbatim
    into raw/spp/."""
    year = _data_year()
    raw = snapshot_dir / "raw" / "spp"
    entries = {}
    for month in MONTHS:
        url = DA_BC_URL.format(year=year, month=month)
        path = common.download(url, raw / f"{_bc_member(year, month)}.zip", max_mb=100)
        name = _bc_raw_name(year, month)
        entries[name] = common.record_raw(snapshot_dir, name, url, path)
        _bc_pin(path, year, month)
    for kind, url_t in (("cbc", CBC_URL), ("tcr", TCR_URL)):
        for month in MONTHS:
            url = url_t.format(year=year, month=month)
            name = _money_raw_name(kind, year, month)
            path = common.download(url, raw / f"{name.split(':', 1)[1]}.csv", max_mb=20)
            entries[name] = common.record_raw(snapshot_dir, name, url, path)
    _money_pins(year)
    q_path = common.download(QUEUE_URL, raw / "gi_active_queue.csv", max_mb=20)
    entries[QUEUE_RAW] = common.record_raw(snapshot_dir, QUEUE_RAW, QUEUE_URL, q_path)
    _queue_pin(q_path.read_bytes().decode("utf-8-sig"))
    return {"bytes": sum(e["bytes"] for e in entries.values()), "raw": entries}


def _read_bc(path: Path, year: int, month: int) -> list[dict]:
    """Rows of one zipped monthly DA-BC CSV (headers stripped: SPP writes
    ' Contingency Name'). Interval is hour-ending local time, so a row belongs to
    the month of Interval - 1h (the file's last row is the next month's 00:00)."""
    with zipfile.ZipFile(path) as z:
        info = z.getinfo(_bc_member(year, month))
        if info.file_size > MAX_CSV_BYTES:
            raise ValueError(f"{info.filename} is {info.file_size} bytes uncompressed")
        with z.open(info) as fh:  # the header's size is a claim: cap what is read
            data = fh.read(MAX_CSV_BYTES + 1)
        if len(data) > MAX_CSV_BYTES:
            raise ValueError(f"{info.filename} inflates past {MAX_CSV_BYTES} bytes")
        text = data.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text, newline=""))
    header = [h.strip() for h in next(reader)]
    missing = [c for c in BC_COLUMNS if c not in header]
    if missing:
        raise ValueError(f"SPP DA-BC CSV lacks columns {missing}")
    rows = [dict(zip(header, (v.strip() for v in r))) for r in reader if r]
    for r in rows:
        hour = datetime.strptime(r["Interval"], _TIME) - timedelta(hours=1)
        if (hour.year, hour.month) != (year, month):
            raise ValueError(
                f"SPP DA-BC {year}-{month:02d} file has an interval in another month: "
                f"{r['Interval']}")
        r["_month"] = month
    return rows


def _money_raw_name(kind: str, year: int, month: int) -> str:
    return f"raw:spp_{kind}_{year}{month:02d}"


def _money_pins(year: int) -> tuple[str, str]:
    return (common.require_pin("spp_congestion_by_constraint", f"spp_cbc_{year}"),
            common.require_pin("spp_tcr_funding", f"spp_tcr_funding_{year}"))


def _read_money(path: Path, columns: tuple[str, ...], label: str) -> list[dict]:
    """A congestion-$ CSV (SPP writes headers with leading spaces)."""
    text = path.read_bytes().decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text, newline=""))
    header = [h.strip() for h in next(reader)]
    missing = [c for c in columns if c not in header]
    if missing:
        raise ValueError(f"SPP {label} CSV lacks columns {missing}")
    return [dict(zip(header, (v.strip() for v in r))) for r in reader if any(r)]


def _congestion_dollars(snapshot_dir: Path, year: int) -> dict:
    """Observed DA congestion $ per flowgate from congestion-by-constraint, plus the
    settled TCR DA Collected total as a calibration figure. Every month's rows must
    carry that month; duplicate keys are removed (counted, conflicts counted)."""
    per: dict[str, dict] = defaultdict(lambda: {"months": {}, "positive": 0, "mon": set()})
    seen: dict[tuple, str] = {}
    dupes = 0
    entries = {}
    for month in MONTHS:
        e = common.raw_entry(snapshot_dir, _money_raw_name("cbc", year, month))
        entries[month] = e
        for r in _read_money(e["abspath"], CBC_COLUMNS, "congestion-by-constraint"):
            if r["Date"] != f"{year}{month:02d}":
                raise ValueError(
                    f"SPP congestion-by-constraint {year}-{month:02d} has a row for {r['Date']}")
            key = (r["Date"], r["Constraint Name"], r["Contingency Name"])
            if key in seen:
                if seen[key] != r["Sum"]:
                    raise ValueError(
                        f"SPP congestion-by-constraint {r['Date']} has conflicting rows for "
                        f"{key[1]}|{key[2]}: Sum {seen[key]} vs {r['Sum']}")
                dupes += 1
                continue
            seen[key] = r["Sum"]
            cid = f"{r['Constraint Name']}|{r['Contingency Name']}"
            usd = -float(r["Sum"])  # SPP posts congestion as a negative Sum
            d = per[cid]
            d["mon"].add(r["Monitored Facility"])
            if usd < 0:
                d["positive"] += 1  # a month of negative rent: flagged, never netted
                continue
            d["months"][month] = d["months"].get(month, 0.0) + usd
    tcr_total = 0.0
    tcr_entries = {}
    for month in MONTHS:
        e = common.raw_entry(snapshot_dir, _money_raw_name("tcr", year, month))
        tcr_entries[month] = e
        for r in _read_money(e["abspath"], TCR_COLUMNS, "TCR funding"):
            if r["Month"] != f"{month:02d}/{year}":
                raise ValueError(f"SPP TCR funding {year}-{month:02d} has a row for {r['Month']}")
            tcr_total += float(r["DA Collected"])
    cbc_version, tcr_version = _money_pins(year)
    latest = lambda es: max(x["retrieved_at"] for x in es.values())
    return {
        "per": per, "dupes": dupes, "tcr_total": tcr_total,
        "cbc_stamp": {"source": "spp_congestion_by_constraint", "source_version": cbc_version,
                      "retrieved_at": latest(entries)},
        "tcr_stamp": {"source": "spp_tcr_funding", "source_version": tcr_version,
                      "retrieved_at": latest(tcr_entries)},
    }


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


def _facility_key(element: str, monitored: str, cid: str) -> str:
    """The corridor key: one physical element, one key, as far as public names allow.
    Lines ignore endpoint order (SPP spells some both ways round); SPP names every
    multi-element interface "Multi-Element Constraint", so those are keyed by their
    constraint name; a blank element stays blank (the corridor falls back to the
    constraint id). Transformers are keyed by SPP's monitored text, which names the
    substation, not the bank ("XFMR GAVINS - GAVINS" for GAVINS1, 2 and 3): every
    transformer at a substation shares one key, since public data cannot tell the
    banks apart."""
    if element == "ln":
        return f"LN {_line_key(monitored)}"
    if element == "multi":
        return f"{monitored} {cid.split('|', 1)[0]}"
    return monitored


def _constraints_and_congestion(snapshot_dir: Path) -> dict:
    year = _data_year()
    rows, entries = [], []
    for month in MONTHS:
        entry = common.raw_entry(snapshot_dir, _bc_raw_name(year, month))
        version = _bc_pin(entry["abspath"], year, month)
        rows += _read_bc(entry["abspath"], year, month)
        entries.append(entry)
    # The stamp's retrieval time is the latest of the twelve downloads.
    entry = max(entries, key=lambda e: e["retrieved_at"])

    seen: dict[tuple, tuple] = {}
    dupes = conflicting = 0
    total_abs = []
    acc: dict[str, dict] = defaultdict(lambda: {
        "hours": set(), "breached": set(), "abs": [], "mon": Counter(), "cf": Counter(),
        "stype": Counter(), "cname": "", "months": set(),
    })
    for r in rows:
        name, cname, mon = r["Constraint Name"], r["Contingency Name"], r["Monitored Facility"]
        if not name or not cname or "|" in name or "|" in cname:
            raise ValueError(f"SPP DA-BC row has an unusable constraint name: {name!r}|{cname!r}")
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
        a["months"].add(r["_month"])
        if r["State"] == "BREACHED":
            a["breached"].add(hour)
        a["abs"].append(sp)
        a["mon"][mon] += 1
        a["cf"][r["Contingent Facility"]] += 1
        a["stype"][r["Constraint Type"]] += 1
        a["cname"] = cname

    money = _congestion_dollars(snapshot_dir, year)
    constraints, congestion = [], []
    excluded = Counter()
    excl_hours = Counter()
    excl_abs: dict[str, list] = defaultdict(list)
    excluded_ids: dict[str, list] = defaultdict(list)
    kinds = Counter()
    multi_mon, mixed, multi_cf, reversed_only, lines = [], 0, 0, 0, set()
    joined = mismatched = flagged = 0
    joined_abs = 0.0
    for cid in sorted(acc):
        a = acc[cid]
        monitored = _mode(a["mon"])
        element = _element(monitored)
        mixed += len({_element(m) for m in a["mon"]}) > 1
        # A phase shifter's own angle limit: the device is itself a flow controller,
        # so there is no GET to recommend and its congestion is not a GET opportunity.
        if element == "blank" and "PHSHFT" in cid.split("|", 1)[0]:
            element = "phase_shifter"
        if element in ("other", "phase_shifter"):
            excluded[element] += 1
            excl_hours[element] += len(a["hours"])
            excl_abs[element].extend(a["abs"])
            excluded_ids[element].append(cid)
            continue
        if len(a["mon"]) > 1:
            multi_mon.append(cid)
            reversed_only += element == "ln" and len({_line_key(m) for m in a["mon"]}) == 1
        multi_cf += len(a["cf"]) > 1
        kinds[element] += 1
        if element == "ln":
            lines.add(_line_key(monitored))
        facility = _mode(a["cf"])
        to, kv = _contingency_parts(facility)
        constraints.append({
            "constraint_id": cid, "monitored_line": "", "contingency_line": "",
            "ctype": {"ln": "thermal", "xfmr": "transformer"}.get(element, "interface"),
            "monitored_facility": _facility_key(element, monitored, cid),
            "monitored_facility_as_reported": monitored, "contingency_facility": facility,
            "contingency_name": a["cname"], "spp_constraint_type": _mode(a["stype"]),
            "contingency_to": to, "contingency_kv": kv,
        })
        sp_sum = math.fsum(a["abs"])
        row = {
            "constraint_id": cid, "monthly_cost_musd": "",
            "hours_binding": len(a["hours"]),
            "shadow_price_sum_usd_per_mw": f"{round(sp_sum, 4):.4f}",
            "max_abs_shadow_price": f"{round(max(a['abs']), 4):.4f}",
            "breached_hours": len(a["breached"]), "period": str(year),
            "months_binding": len(a["months"]), "rent_musd": "", "max_month_cost_musd": "",
            "rent_basis": "", "flags": "",
        }
        d = money["per"].get(cid)
        flags = []
        if d is not None and d["mon"] and not _same_element(d["mon"], set(a["mon"])):
            mismatched += 1  # a recycled id naming another element: never joined
            flags.append("cbc_monitored_mismatch")
        elif d is not None and d["months"]:
            months = d["months"]
            row["rent_musd"] = f"{math.fsum(months.values()) / 1e6:.6f}"
            row["max_month_cost_musd"] = f"{max(months.values()) / 1e6:.6f}"
            row["rent_basis"] = "spp_congestion_by_constraint"
            joined += 1
            joined_abs += sp_sum
        if d is not None and d["positive"]:
            flagged += 1
            flags.append(f"cbc_negative_rent_months={d['positive']}")
        row["flags"] = ";".join(flags)
        congestion.append(row)

    total = math.fsum(total_abs)
    kept_abs = math.fsum(math.fsum(acc[c["constraint_id"]]["abs"]) for c in constraints)
    n_base = sum(1 for c in constraints if c["contingency_name"] == "BASE")
    share = lambda k: math.fsum(excl_abs[k]) / total if total else 0.0
    other = (
        f" Excluded {excluded['other']} flowgates with an unrecognized monitored-element "
        f"prefix ({excl_hours['other']} constraint-hours)." if excluded["other"] else ""
    )
    cbc_total = math.fsum(
        float(r["rent_musd"]) for r in congestion if r["rent_musd"]) * 1e6
    stamp = {"source": "spp_binding_constraints", "source_version": version,
             "retrieved_at": entry["retrieved_at"]}
    common.write_table(
        snapshot_dir, "constraints", constraints, CONSTRAINT_FIELDS, **stamp,
        coverage="partial",
        note=(
            f"SPP DA binding constraints {year} from the twelve monthly archives "
            f"(DA-BC-MONTHLY; the yearly file omits rows): {len(acc)} flowgates "
            f"(Constraint Name|Contingency Name) in {len(rows)} hourly rows. Kept "
            f"{len(constraints)}: {kinds['ln']} thermal (LN monitored element), "
            f"{kinds['xfmr']} transformer (XFMR) and {kinds['multi'] + kinds['blank']} "
            f"interface ({kinds['multi']} Multi-Element, {kinds['blank']} other blank "
            f"elements). Excluded {excluded['phase_shifter']} phase-shifter flowgates "
            f"({', '.join(excluded_ids['phase_shifter']) or 'none'}; "
            f"{excl_hours['phase_shifter']} constraint-hours, {share('phase_shifter'):.2%} "
            "of |shadow price|): the binding limit is the phase shifter's own, and the "
            f"device is itself a power-flow controller.{other} Kept flowgates carry "
            f"{kept_abs / total if total else 0:.2%} of total |shadow price|. "
            f"{len(lines)} distinct monitored lines (endpoint order "
            f"ignored). {len(multi_mon)} flowgates carry >1 monitored-facility spelling, "
            f"{reversed_only} of them only reversed endpoints ({', '.join(multi_mon) or 'none'}); "
            f"{multi_cf} carry >1 "
            "contingent-facility spelling; each is one row with the most frequent spelling "
            f"(ties: first sorted). {mixed} flowgates mix element types (classed by the "
            "most frequent). monitored_facility is the corridor key: lines with endpoints "
            "sorted, multi-element interfaces suffixed with their constraint name; SPP's "
            "spelling is kept in monitored_facility_as_reported. Monitored elements not "
            "resolved to public topology: monitored_line and contingency_line are blank for "
            "every row (SPP names are market-model bus names; the bus-location model is "
            f"CEII); contingency_line is also inherently blank for the {n_base} BASE "
            "(no-contingency) flowgates."
        ),
    )
    unjoined = sum(1 for r in congestion if not r["rent_musd"])
    common.write_table(
        snapshot_dir, "congestion", congestion, CONGESTION_FIELDS, **stamp,
        coverage="partial" if unjoined else "observed",
        contributors=[money["cbc_stamp"], money["tcr_stamp"]],
        column_sources={"rent_musd": money["cbc_stamp"],
                        "max_month_cost_musd": money["cbc_stamp"]},
        note=(
            f"SPP Day-Ahead market only (no real-time), {year}, one row per constraints "
            "row. hours_binding = distinct UTC hours (GMTIntervalEnd) the flowgate was "
            "BINDING or BREACHED; breached_hours counts BREACHED; months_binding counts "
            "months with any binding hour. Local Interval labels repeat on the DST "
            "fall-back hour; those are distinct UTC hours with their own shadow prices and "
            "are kept. Exact duplicate rows (GMTIntervalEnd, Constraint Name, Monitored "
            f"Facility, Contingency Name) removed: {dupes} ({conflicting} with differing "
            "values; first kept). shadow_price_sum_usd_per_mw = sum of |shadow price| "
            "over those hours ($/MW-yr: the value of 1 MW of relief, first order). "
            "rent_musd = SPP's congestion by constraint (sum over intervals of flow x "
            "shadow price; posted as a negative Sum), summed over the months it is "
            "positive; months where SPP shows negative rent are counted in flags, never "
            f"netted. Joined on Constraint Name|Contingency Name: {joined} of "
            f"{len(congestion)} flowgates ({joined_abs / kept_abs if kept_abs else 0:.3%} "
            f"of kept |shadow price|); {mismatched} not joined because the monitored "
            f"element differs (recycled ids); {flagged} flagged with negative-rent months; "
            f"{unjoined} have no observed $ and their rent is unobserved (blank). Identical "
            f"duplicate congestion keys removed: {money['dupes']} (a conflicting duplicate "
            "fails the build). "
            f"Calibration: congestion-by-constraint total on kept flowgates "
            f"${cbc_total / 1e6:,.1f}M; SPP TCR funding by constraint, DA Collected over "
            f"all constraints, ${money['tcr_total'] / 1e6:,.1f}M (settled; the two "
            "products differ by definition and coverage)."
        ),
    )
    return entry


def _same_element(cbc: set[str], dabc: set[str]) -> bool:
    """Whether congestion-by-constraint names the same monitored element(s) as the
    DA binding record (lines compare with endpoint order ignored)."""
    norm = lambda m: f"LN {_line_key(m)}" if m.startswith("LN ") else m
    return bool({norm(m) for m in cbc} & {norm(m) for m in dabc})


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
