"""SPP day-ahead constraint history for the panel (constraint_history and
constraint_coverage), from the raw files `spp.fetch` records.

It reuses spp's readers, flowgate key and duplicate rules, so where the panel and the
pipeline overlap they count the same hours and dollars. Unlike the pipeline it keeps
every flowgate, phase shifters and unrecognized elements included: the panel reports
history, not a GET decision.

Not registered as a panel region and not run by the snapshot builder. SPP's terms allow
non-commercial use with citation and need SPP's written authorization for commercial
use (config/sources.yaml), and a buyer's panel may be commercial. Writing these tables
into an existing snapshot would change its manifest and break replay of the runs pinned
to it, so once the region is registered they belong in a new revision.

The dollars come from `spp._congestion_dollars`, which also reads the TCR files and
does not run `spp._congestion_tripwires`. Run this after `spp.normalize`, whose
tripwires refuse degraded dollar files.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from headroom.ingest.adapters import common, spp
from headroom.panel.tables import period_days, write_panel_table
from headroom.provenance.real import SourceStamp


def _stamp(entry: dict) -> SourceStamp:
    return SourceStamp(entry["source"], entry["source_version"],
                       datetime.fromisoformat(entry["retrieved_at"]))


def normalize(snapshot_dir: Path) -> dict:
    """Write the two panel tables; return counts of what was kept, dropped or flagged."""
    year = spp._data_year()
    bc_keys = [spp._bc_raw_name(year, month) for month in spp.MONTHS]
    cbc_keys = [spp._money_raw_name("cbc", year, month) for month in spp.MONTHS]
    rows, entries = [], []
    for month, key in zip(spp.MONTHS, bc_keys):
        entry = common.raw_entry(snapshot_dir, key)
        version = spp._bc_pin(entry["abspath"], year, month)
        rows += spp._read_bc(entry["abspath"], year, month)
        entries.append(entry)

    # A day counts as covered when the archive has any row for it: a lower bound, since
    # a day on which nothing bound reads as uncovered (never as zero), and a truncated
    # archive shows up as a partial month.
    days: dict[int, set] = defaultdict(set)
    seen: dict[tuple, tuple] = {}
    dupes = conflicting = 0
    acc: dict[tuple[str, int], dict] = defaultdict(
        lambda: {"hours": set(), "abs": [], "mon": Counter(), "cf": Counter()})
    monitored: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        local = datetime.strptime(r["Interval"], spp._TIME) - timedelta(hours=1)
        days[r["_month"]].add(local.date())
        name, cname, mon = r["Constraint Name"], r["Contingency Name"], r["Monitored Facility"]
        if not name or not cname or "|" in name or "|" in cname:
            raise ValueError(f"SPP DA-BC row has an unusable constraint name: {name!r}|{cname!r}")
        hour = datetime.strptime(r["GMTIntervalEnd"], spp._TIME).isoformat()
        value = (r["Shadow Price"], r["State"], r["Contingent Facility"])
        key = (hour, name, mon, cname)
        if key in seen:
            dupes += 1
            conflicting += seen[key] != value
            continue
        seen[key] = value
        cid = f"{name}|{cname}"
        a = acc[(cid, r["_month"])]
        a["hours"].add(hour)
        a["abs"].append(abs(float(r["Shadow Price"])))
        a["mon"][mon] += 1
        a["cf"][r["Contingent Facility"]] += 1
        monitored[cid].add(mon)

    # Dollars join as the pipeline's rent does, except that a negative-rent month is
    # reported as posted rather than left out.
    money = spp._congestion_dollars(snapshot_dir, year)
    history, recycled = [], set()
    negative = 0
    for (cid, month), a in sorted(acc.items()):
        d = money["per"].get(cid)
        cost = None
        if d is not None and d["mon"] and not spp._same_element(d["mon"], monitored[cid]):
            recycled.add(cid)  # the id names another element in the dollars: never joined
        elif d is not None:
            cost = d["months"].get(month, d["negative"].get(month))
            negative += month in d["negative"]
        history.append({
            "constraint_id": cid,
            "market": "DA",
            "period": f"{year}-{month:02d}",
            "hours_binding": len(a["hours"]),
            "shadow_price_max": f"{max(a['abs']):.4f}",
            "shadow_price_mean": f"{math.fsum(a['abs']) / len(a['abs']):.4f}",
            "congestion_cost_usd": None if cost is None else f"{cost:.2f}",
            "monitored_facility": spp._mode(a["mon"]),
            "contingent_facility": spp._mode(a["cf"]),
        })
    unbound = sum(
        1 for cid, d in money["per"].items()
        for month in (*d["months"], *d["negative"]) if (cid, month) not in acc
    )
    coverage = [
        {
            "market": "DA",
            "period": f"{year}-{month:02d}",
            "days_expected": period_days(f"{year}-{month:02d}"),
            "days_covered": len(days[month]),
            "raw_refs": f"{bc};{cbc}",
        }
        for month, bc, cbc in zip(spp.MONTHS, bc_keys, cbc_keys)
    ]

    bc_stamp = SourceStamp("spp_binding_constraints", version,
                           max(datetime.fromisoformat(e["retrieved_at"]) for e in entries))
    cbc_stamp = _stamp(money["cbc_stamp"])
    derived = [*bc_keys, *cbc_keys]
    write_panel_table(snapshot_dir, "constraint_history", history, stamp=bc_stamp,
                      derived_from=derived, contributors=[cbc_stamp],
                      column_sources={"congestion_cost_usd": cbc_stamp})
    write_panel_table(snapshot_dir, "constraint_coverage", coverage, stamp=bc_stamp,
                      derived_from=derived)
    return {
        "flowgates": len({cid for cid, _ in acc}),
        "history_rows": len(history),
        "duplicates": dupes,
        "conflicting_duplicates": conflicting,
        "negative_rent_months": negative,
        "recycled_ids": sorted(recycled),
        "dollar_months_without_binding": unbound,
        "days_covered": {month: len(days[month]) for month in spp.MONTHS},
    }
