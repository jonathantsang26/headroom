"""Aggregate cited monthly observations into a footprint panel.

Absent data is never read as zero: a month counts as "did not bind" only where
coverage says the source observed it, and that zero is derived from the coverage
record so its lineage stays traceable. No composite score is computed here.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

from headroom.panel.footprint import Footprint, FootprintMatch, load_footprint
from headroom.panel.load import PanelInputs, PanelRegion, get_panel_region, load_panel
from headroom.panel.tables import MARKETS, OPTIONAL_VALUES, iter_periods
from headroom.provenance.envelope import Confidence, Fact, derive_lineage_id
from headroom.provenance.lineage import LineageStore
from headroom.provenance.runtime import runtime_identity
from headroom.sources import SourceRegistry, load_registry

PANEL_STAGE = "panel.aggregate"
PANEL_STAGE_VERSION = "1"


@dataclass
class PanelCell:
    """One constraint, market, and month."""

    constraint_id: str
    market: str
    period: str
    coverage: str  # full | partial | none
    days_covered: int
    days_expected: int
    binding: bool | None  # None when the month was not covered
    values: dict[str, Fact]
    missing: list[str]
    raw_refs: tuple[str, ...]


@dataclass
class PanelSummary:
    """One constraint and market over the window."""

    constraint_id: str
    market: str
    matches: list[FootprintMatch]
    months: int
    months_full: int
    months_partial: int
    months_not_covered: list[str]
    months_binding: int
    first_binding: str | None
    last_binding: str | None
    seen_in_source: bool
    reported_facilities: list[str]
    values: dict[str, Fact]
    missing: list[str]
    flags: list[str]
    confidence: Confidence


@dataclass
class PanelResult:
    region: PanelRegion
    store: LineageStore
    inputs: PanelInputs
    footprint: Footprint
    window: tuple[str, str]
    markets: tuple[str, ...]
    cells: list[PanelCell]
    summaries: list[PanelSummary]
    registry: SourceRegistry
    runtime: dict
    input_identity: dict = field(default_factory=dict)

    @property
    def periods(self) -> list[str]:
        return iter_periods(*self.window)


def _derived(
    store: LineageStore, label: str, value: float, inputs: list[Fact], *,
    unit: str, confidence: Confidence, flags: list[str],
) -> Fact:
    if not inputs:
        raise ValueError(f"Derived panel value {label!r} needs cited inputs")
    ids = [fact.lineage_id for fact in inputs]
    return store.add(Fact(
        value=value,
        unit=unit,
        confidence=confidence,
        flags=sorted(set(flags)),
        source=PANEL_STAGE,
        source_version=PANEL_STAGE_VERSION,
        lineage_id=derive_lineage_id(label, ids),
        inputs=ids,
        retrieved_at=max(fact.retrieved_at for fact in inputs),
    ))


def _cells(
    inputs: PanelInputs, cid: str, market: str, periods: list[str], store: LineageStore,
) -> list[PanelCell]:
    cells = []
    for period in periods:
        cov = inputs.coverage.get((market, period))
        status = cov.status if cov else "none"
        common = dict(
            constraint_id=cid, market=market, period=period, coverage=status,
            days_covered=cov.days_covered if cov else 0,
            days_expected=cov.days_expected if cov else 0,
            raw_refs=cov.raw_refs if cov else (),
        )
        facts = inputs.history_facts.get((cid, market, period))
        if status == "none":
            cells.append(PanelCell(
                **common, binding=None, values={},
                missing=["no source coverage for this month"],
            ))
        elif facts is not None:
            cells.append(PanelCell(
                **common, binding=True, values=dict(facts),
                missing=[f"{name} not published" for name in OPTIONAL_VALUES if name not in facts],
            ))
        else:
            partial = status == "partial"
            zero = _derived(
                store, f"panel.cell:{market}:{period}:{cid}:hours_binding", 0.0,
                [inputs.coverage_facts[(market, period)]], unit="h",
                confidence="medium" if partial else "high",
                flags=["partial_coverage"] if partial else [],
            )
            cells.append(PanelCell(
                **common, binding=False, values={"hours_binding": zero}, missing=[],
            ))
    return cells


def _summary(
    cells: list[PanelCell], *, cid: str, market: str, matches: list[FootprintMatch],
    seen: bool, facilities: list[str], label: str, store: LineageStore,
) -> PanelSummary:
    covered = [c for c in cells if c.coverage != "none"]
    binding = [c for c in cells if c.binding]
    not_covered = [c.period for c in cells if c.coverage == "none"]
    flags = []
    if any(c.coverage == "partial" for c in cells):
        flags.append("partial_coverage")
    if not_covered:
        flags.append("months_not_covered")
    if not seen:
        flags.append("not_seen_in_source")
    confidence: Confidence = "low" if not seen else ("medium" if flags else "high")

    values: dict[str, Fact] = {}
    missing: list[str] = []
    if covered:
        hours = [c.values["hours_binding"] for c in covered]
        values["hours_binding"] = _derived(
            store, f"{label}:hours_binding", sum(f.value for f in hours), hours,
            unit="h", confidence=confidence, flags=flags,
        )
    else:
        missing.append("hours_binding: no covered months in window")

    if binding:
        n = len(binding)
        maxima = [c.values["shadow_price_max"] for c in binding if "shadow_price_max" in c.values]
        if len(maxima) == n:
            values["shadow_price_max"] = _derived(
                store, f"{label}:shadow_price_max", max(f.value for f in maxima), maxima,
                unit="USD/MWh", confidence=confidence, flags=flags,
            )
        else:
            missing.append(f"shadow_price_max: not published for {n - len(maxima)} of {n} binding months")
        weighted = [
            (c.values["shadow_price_mean"], c.values["hours_binding"])
            for c in binding if "shadow_price_mean" in c.values
        ]
        if len(weighted) == n:
            total_hours = sum(h.value for _, h in weighted)
            mean = sum(m.value * h.value for m, h in weighted) / total_hours
            values["shadow_price_mean"] = _derived(
                store, f"{label}:shadow_price_mean", mean,
                [fact for pair in weighted for fact in pair],
                unit="USD/MWh", confidence=confidence, flags=flags,
            )
        else:
            missing.append(f"shadow_price_mean: not published for {n - len(weighted)} of {n} binding months")
        costs = [c.values["congestion_cost_usd"] for c in binding if "congestion_cost_usd" in c.values]
        if len(costs) == n:
            values["congestion_cost_usd"] = _derived(
                store, f"{label}:congestion_cost_usd", sum(f.value for f in costs), costs,
                unit="USD", confidence=confidence, flags=flags,
            )
        else:
            missing.append(f"congestion_cost_usd: not published for {n - len(costs)} of {n} binding months")

    return PanelSummary(
        constraint_id=cid,
        market=market,
        matches=matches,
        months=len(cells),
        months_full=sum(1 for c in cells if c.coverage == "full"),
        months_partial=sum(1 for c in cells if c.coverage == "partial"),
        months_not_covered=not_covered,
        months_binding=len(binding),
        first_binding=binding[0].period if binding else None,
        last_binding=binding[-1].period if binding else None,
        seen_in_source=seen,
        reported_facilities=facilities,
        values=values,
        missing=missing,
        flags=flags,
        confidence=confidence,
    )


def _sort_key(summary: PanelSummary):
    hours = summary.values.get("hours_binding")
    return (
        MARKETS.index(summary.market),
        hours is None,
        -(hours.value if hours is not None else 0.0),
        summary.constraint_id,
    )


def build_panel(
    region: str | PanelRegion,
    footprint: str | Path | Footprint,
    *,
    start: str | None = None,
    end: str | None = None,
    registry: SourceRegistry | None = None,
) -> PanelResult:
    region = get_panel_region(region) if isinstance(region, str) else region
    store = LineageStore()
    registry = deepcopy(registry or load_registry())
    runtime = runtime_identity()
    inputs = load_panel(region, store)
    footprint = footprint if isinstance(footprint, Footprint) else load_footprint(footprint)

    covered_periods = sorted(period for _, period in inputs.coverage)
    if not covered_periods:
        raise ValueError("Panel snapshot records no coverage")
    window = (start or covered_periods[0], end or covered_periods[-1])
    periods = iter_periods(*window)
    markets = tuple(m for m in MARKETS if any(market == m for market, _ in inputs.coverage))

    seen_ids = {row.constraint_id for row in inputs.history}
    facilities: dict[str, list[str]] = {}
    for row in inputs.history:
        text = " / ".join(part for part in (row.monitored_facility, row.contingent_facility) if part)
        if text and text not in facilities.setdefault(row.constraint_id, []):
            facilities[row.constraint_id].append(text)

    cells: list[PanelCell] = []
    summaries: list[PanelSummary] = []
    for cid, matches in sorted(footprint.verified().items()):
        for market in markets:
            constraint_cells = _cells(inputs, cid, market, periods, store)
            cells.extend(constraint_cells)
            summaries.append(_summary(
                constraint_cells, cid=cid, market=market, matches=matches,
                seen=cid in seen_ids, facilities=facilities.get(cid, []),
                label=f"panel.summary:{window[0]}:{window[1]}:{market}:{cid}", store=store,
            ))
    summaries.sort(key=_sort_key)
    order = {(s.constraint_id, s.market): i for i, s in enumerate(summaries)}
    cells.sort(key=lambda c: (order[(c.constraint_id, c.market)], c.period))

    identity = deepcopy(inputs.input_identity)
    identity["footprint"] = {"name": footprint.name, "sha256": footprint.sha256}
    identity["window"] = {"start": window[0], "end": window[1]}
    return PanelResult(
        region=region,
        store=store,
        inputs=inputs,
        footprint=footprint,
        window=window,
        markets=markets,
        cells=cells,
        summaries=summaries,
        registry=registry,
        runtime=runtime,
        input_identity=identity,
    )
