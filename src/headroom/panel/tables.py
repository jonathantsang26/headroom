"""Normalized constraint-panel tables: the contract a history adapter must emit.

`constraint_history` holds one row per binding constraint, market, and month.
`constraint_coverage` records which market-months the source covered, so an
absent history row means "did not bind" only where coverage exists.
"""

from __future__ import annotations

import calendar
import csv
import io
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from headroom.ingest.snapshot import sha256_of, update_manifest
from headroom.provenance.real import SourceStamp

PANEL_TABLES = ("constraint_history", "constraint_coverage")
MARKETS = ("DA", "RT")
HISTORY_COLUMNS = (
    "constraint_id", "market", "period", "hours_binding", "shadow_price_max",
    "shadow_price_mean", "congestion_cost_usd", "monitored_facility",
    "contingent_facility",
)
COVERAGE_COLUMNS = ("market", "period", "days_expected", "days_covered", "raw_refs")
OPTIONAL_VALUES = ("shadow_price_max", "shadow_price_mean", "congestion_cost_usd")

_PERIOD = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])")
_NUMBER = re.compile(r"-?[0-9]+(\.[0-9]+)?([eE][-+]?[0-9]+)?")
_COUNT = re.compile(r"[0-9]+")
# A covered month may contain the 25-hour market day that ends daylight saving time.
_DST_EXTRA_HOURS = 1.0


def period_days(period: str) -> int:
    year, month = (int(part) for part in period.split("-"))
    return calendar.monthrange(year, month)[1]


def check_period(value: str, where: str) -> str:
    if not isinstance(value, str) or _PERIOD.fullmatch(value) is None:
        raise ValueError(f"{where} period must be YYYY-MM, got {value!r}")
    return value


def iter_periods(start: str, end: str) -> list[str]:
    """Inclusive month range."""
    check_period(start, "window start")
    check_period(end, "window end")
    if start > end:
        raise ValueError(f"Window start {start} is after end {end}")
    year, month = (int(part) for part in start.split("-"))
    periods = []
    while True:
        period = f"{year:04d}-{month:02d}"
        periods.append(period)
        if period == end:
            return periods
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


@dataclass
class HistoryRow:
    constraint_id: str
    market: str
    period: str
    hours_binding: float
    shadow_price_max: float | None
    shadow_price_mean: float | None
    congestion_cost_usd: float | None
    monitored_facility: str
    contingent_facility: str
    as_reported: dict[str, str] = field(default_factory=dict)


@dataclass
class CoverageRow:
    market: str
    period: str
    days_expected: int
    days_covered: int
    raw_refs: tuple[str, ...]

    @property
    def status(self) -> str:
        if self.days_covered == 0:
            return "none"
        return "full" if self.days_covered == self.days_expected else "partial"


def read_csv(content: bytes, table: str, columns: tuple[str, ...]) -> list[dict]:
    """Parse exact bytes; require exactly the contract's columns on every row."""
    reader = csv.DictReader(io.StringIO(content.decode("utf-8"), newline=""))
    names = reader.fieldnames
    if names is None or len(names) != len(set(names)) or set(names) != set(columns):
        raise ValueError(f"{table} must have exactly the columns: {', '.join(columns)}")
    rows = []
    for number, row in enumerate(reader, start=2):
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"{table} line {number} has the wrong number of fields")
        rows.append(row)
    return rows


def _number(text: str, where: str, *, required: bool) -> float | None:
    if text == "":
        if required:
            raise ValueError(f"{where} is required")
        return None
    if _NUMBER.fullmatch(text) is None:
        raise ValueError(f"{where} must be a plain decimal number, got {text!r}")
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"{where} must be finite, got {text!r}")
    return value


def _count(text: str, where: str) -> int:
    if _COUNT.fullmatch(text) is None:
        raise ValueError(f"{where} must be a nonnegative integer, got {text!r}")
    return int(text)


def _market(text: str, where: str) -> str:
    if text not in MARKETS:
        raise ValueError(f"{where} market must be one of {', '.join(MARKETS)}, got {text!r}")
    return text


def parse_history(rows: list[dict]) -> list[HistoryRow]:
    out: list[HistoryRow] = []
    seen: set[tuple[str, str, str]] = set()
    for number, r in enumerate(rows, start=2):
        where = f"constraint_history line {number}"
        cid = r["constraint_id"]
        if not cid or cid != cid.strip():
            raise ValueError(f"{where} needs a constraint_id without surrounding whitespace")
        market = _market(r["market"], where)
        period = check_period(r["period"], where)
        key = (cid, market, period)
        if key in seen:
            raise ValueError(f"{where} repeats {cid} {market} {period}")
        seen.add(key)
        hours = _number(r["hours_binding"], f"{where} hours_binding", required=True)
        if hours <= 0:
            raise ValueError(
                f"{where} hours_binding must be positive; omit months without binding"
            )
        sp_max = _number(r["shadow_price_max"], f"{where} shadow_price_max", required=False)
        sp_mean = _number(r["shadow_price_mean"], f"{where} shadow_price_mean", required=False)
        for name, value in (("shadow_price_max", sp_max), ("shadow_price_mean", sp_mean)):
            if value is not None and value < 0:
                raise ValueError(f"{where} {name} is a magnitude and cannot be negative")
        if sp_max is not None and sp_mean is not None and sp_mean > sp_max:
            raise ValueError(f"{where} shadow_price_mean exceeds shadow_price_max")
        cost = _number(r["congestion_cost_usd"], f"{where} congestion_cost_usd", required=False)
        out.append(HistoryRow(
            constraint_id=cid, market=market, period=period, hours_binding=hours,
            shadow_price_max=sp_max, shadow_price_mean=sp_mean, congestion_cost_usd=cost,
            monitored_facility=r["monitored_facility"],
            contingent_facility=r["contingent_facility"],
            as_reported={name: r[name] for name in ("hours_binding", *OPTIONAL_VALUES)},
        ))
    return out


def parse_coverage(rows: list[dict]) -> dict[tuple[str, str], CoverageRow]:
    out: dict[tuple[str, str], CoverageRow] = {}
    for number, r in enumerate(rows, start=2):
        where = f"constraint_coverage line {number}"
        market = _market(r["market"], where)
        period = check_period(r["period"], where)
        if (market, period) in out:
            raise ValueError(f"{where} repeats {market} {period}")
        expected = _count(r["days_expected"], f"{where} days_expected")
        covered = _count(r["days_covered"], f"{where} days_covered")
        if expected != period_days(period):
            raise ValueError(f"{where} days_expected must be {period_days(period)} for {period}")
        if covered > expected:
            raise ValueError(f"{where} days_covered exceeds days_expected")
        refs = tuple(r["raw_refs"].split(";")) if r["raw_refs"] else ()
        if any(not ref or ref != ref.strip() for ref in refs) or len(refs) != len(set(refs)):
            raise ValueError(f"{where} raw_refs must be distinct, nonempty keys separated by ';'")
        if covered and not refs:
            raise ValueError(f"{where} covers {covered} days but cites no raw input")
        out[(market, period)] = CoverageRow(market, period, expected, covered, refs)
    return out


def check_consistency(
    history: list[HistoryRow], coverage: dict[tuple[str, str], CoverageRow],
) -> None:
    """A binding month must sit inside recorded coverage and fit its covered days."""
    for row in history:
        cov = coverage.get((row.market, row.period))
        if cov is None or cov.days_covered == 0:
            raise ValueError(
                f"constraint_history has {row.constraint_id} {row.market} {row.period}, "
                "but constraint_coverage records no covered days for that month"
            )
        if row.hours_binding > 24 * cov.days_covered + _DST_EXTRA_HOURS:
            raise ValueError(
                f"constraint_history {row.constraint_id} {row.market} {row.period} binds "
                f"{row.hours_binding} hours in {cov.days_covered} covered days"
            )


def _cell(value) -> str:
    return "" if value is None else str(value)


def write_panel_table(
    snapshot_dir: Path, table: str, rows: list[dict], *, stamp: SourceStamp,
    derived_from: list[str],
) -> Path:
    """Adapter side of the contract: validate rows, write the normalized CSV, and
    record its stamp, hash, and raw inputs in the snapshot manifest."""
    columns = {"constraint_history": HISTORY_COLUMNS, "constraint_coverage": COVERAGE_COLUMNS}
    if table not in columns:
        raise ValueError(f"Unknown panel table {table!r}")
    if not derived_from:
        raise ValueError(f"{table} must name the raw inputs it was derived from")
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(columns[table]), lineterminator="\n")
    writer.writeheader()
    writer.writerows({name: _cell(row.get(name)) for name in columns[table]} for row in rows)
    content = buffer.getvalue().encode("utf-8")
    parsed = read_csv(content, table, columns[table])
    if table == "constraint_history":
        parse_history(parsed)
    else:
        parse_coverage(parsed)

    path = Path(snapshot_dir) / "normalized" / f"{table}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(content)
    tmp.replace(path)  # readers never see a partly written table
    update_manifest(Path(snapshot_dir), tables={table: {
        "source": stamp.source,
        "source_version": stamp.source_version,
        "retrieved_at": stamp.retrieved_at.isoformat(),
        "path": f"normalized/{table}.csv",
        "sha256": sha256_of(path),
        "derived_from": list(derived_from),
    }})
    return path
