"""7.1 Signals — each a `Range`, kept explainable (visible, never folded into one
opaque composite). Raw quantities are normalized to ~0-1 by fixed references so the
weighted composite is interpretable.

Coverage: a signal is `observed` when its source table covers the constraint — a
constraint absent from a complete table is an observed zero. When the source could
not be obtained (table unobserved) or the constraint cannot be placed on public
topology, the signal is `unobserved`: a degenerate zero Range that adds nothing to
any constraint's composite and says so in its basis. Unobserved is never evidence."""

from __future__ import annotations

from headroom.provenance.derived import derived_range
from headroom.provenance.envelope import Range
from headroom.provenance.lineage import LineageStore
from headroom.reconstruct.bucket_b import BucketBResult
from headroom.schema.entities import Constraint, Line

SIGNAL_NAMES = (
    "rent",
    "loading",
    "persistence",
    "demand_pressure",
    "planning",
    "cost_of_inaction",
)
STAGE = "model.signals"


def _scaled(rng: Range, denom: float, *, cid: str, name: str,
            inputs: list[str] | None = None) -> Range:
    """Normalize a Range by a fixed reference (preserves the distribution shape).
    `inputs` chains this signal to the upstream Range it was derived from, so the
    publishable gate's taint walk reaches that Range's source."""
    d = denom or 1.0
    return derived_range(
        lo=rng.lo / d,
        expected=rng.expected / d,
        hi=rng.hi / d,
        basis=f"{name} normalized by reference {d}.",
        stage=STAGE,
        provider=rng.provider or rng.source,
        lineage_id=f"signal:{name}:{cid}",
        inputs=inputs or [rng.lineage_id],
    )


def _const(value: float, *, cid: str, name: str, spread: float = 0.0,
           inputs: list[str]) -> Range:
    return derived_range(
        lo=max(0.0, value - spread),
        expected=value,
        hi=value + spread,
        basis=f"{name} (point with small uncertainty band).",
        stage=STAGE,
        provider=STAGE,
        lineage_id=f"signal:{name}:{cid}",
        inputs=inputs,
    )


def _unobserved(*, cid: str, name: str, why: str, inputs: list[str]) -> Range:
    return derived_range(
        lo=0.0,
        expected=0.0,
        hi=0.0,
        basis=f"unobserved: {why}. Contributes nothing to the ranking; not zero evidence.",
        stage=STAGE,
        provider=STAGE,
        lineage_id=f"signal:{name}:{cid}",
        inputs=inputs,
    )


def _bus_degree(lines: list[Line]) -> dict[str, int]:
    deg: dict[str, int] = {}
    for ln in lines:
        deg[ln.from_bus] = deg.get(ln.from_bus, 0) + 1
        deg[ln.to_bus] = deg.get(ln.to_bus, 0) + 1
    return deg


def is_radial(line: Line, degree: dict[str, int]) -> bool:
    """A monitored line is radial if either endpoint is a stub (degree 1)."""
    return degree.get(line.from_bus, 0) <= 1 or degree.get(line.to_bus, 0) <= 1


def _queue_near(line: Line, bb: BucketBResult,
                adjacency: dict[str, set[str]]) -> tuple[float, list[str]]:
    near = {line.from_bus, line.to_bus}
    near |= adjacency.get(line.from_bus, set()) | adjacency.get(line.to_bus, set())
    items = [q for q in bb.queue if q.near_bus in near]
    return sum(q.mw for q in items), [i for q in items for i in q.lineage_ids]


def queue_mw_near(constraint: Constraint, line: Line, bb: BucketBResult,
                  adjacency: dict[str, set[str]]) -> float:
    """Queue MW at the monitored line's endpoints and their 1-hop neighbors."""
    return _queue_near(line, bb, adjacency)[0]


def _adjacency(lines: list[Line]) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {}
    for ln in lines:
        adj.setdefault(ln.from_bus, set()).add(ln.to_bus)
        adj.setdefault(ln.to_bus, set()).add(ln.from_bus)
    return adj


def _unobserved_table(bb: BucketBResult, table: str) -> bool:
    return bb.table_coverage.get(table, {}).get("coverage") == "unobserved"


def build_signals(
    constraint: Constraint,
    line: Line | None,
    bb: BucketBResult,
    refs: dict,
    adjacency: dict[str, set[str]],
    store: LineageStore,
) -> tuple[dict[str, Range], dict[str, str]]:
    """Signals and their per-signal coverage for one flowgate. `line` is None when
    the monitored element is not on public topology."""
    cid = constraint.constraint_id
    co = bb.congestion.get(cid)
    loading = bb.loading[cid]
    tl = bb.table_lineage
    row = [f"obs:constraints:{cid}"] if f"obs:constraints:{cid}" in store else []

    def absent(table: str) -> list[str]:  # "not in table X" derives from X and this row
        return [*tl.get(table, []), *row] or [loading.lineage_id]

    signals: dict[str, Range] = {}
    coverage: dict[str, str] = {name: "observed" for name in SIGNAL_NAMES}

    def unobserved(name: str, why: str, inputs: list[str]) -> None:
        signals[name] = store.add(_unobserved(cid=cid, name=name, why=why, inputs=inputs))
        coverage[name] = "unobserved"

    # rent magnitude
    if co is not None:
        if co.rent_musd is not None:
            signals["rent"] = store.add(
                _scaled(co.rent_musd, refs["rent_ref_musd"], cid=cid, name="rent",
                        inputs=[co.rent_musd.lineage_id])
            )
        else:
            unobserved("rent", "no observed congestion $ for this flowgate",
                       [co.hours_binding.lineage_id])
        signals["persistence"] = store.add(
            _scaled(co.hours_binding, refs["persist_ref_hours"], cid=cid,
                    name="persistence", inputs=[co.hours_binding.lineage_id])
        )
    elif _unobserved_table(bb, "congestion"):
        for name in ("rent", "persistence"):
            unobserved(name, "congestion data not obtained", absent("congestion"))
    else:
        signals["rent"] = store.add(
            _const(0.0, cid=cid, name="rent", spread=0.05, inputs=absent("congestion")))
        signals["persistence"] = store.add(
            _const(0.0, cid=cid, name="persistence", spread=0.05,
                   inputs=absent("congestion"))
        )

    # loading (a fraction of thermal).
    if bb.screen_status.get(cid) == "unmapped_monitored":
        unobserved("loading", "monitored element not on public topology; DC screen "
                   "cannot see it", [loading.lineage_id])
    else:
        signals["loading"] = store.add(
            _scaled(loading, refs.get("loading_ref", 1.0), cid=cid, name="loading",
                    inputs=[loading.lineage_id])
        )

    # demand pressure: queue MW nearby
    if line is None:
        unobserved("demand_pressure", "constraint location unknown, so nearby queue "
                   "MW cannot be summed", row or [loading.lineage_id])
    elif _unobserved_table(bb, "queue"):
        unobserved("demand_pressure", "interconnection queue not obtained", absent("queue"))
    else:
        qmw, q_ids = _queue_near(line, bb, adjacency)
        signals["demand_pressure"] = store.add(
            _const(qmw / refs["demand_ref_mw"], cid=cid, name="demand_pressure",
                   spread=0.05, inputs=[*q_ids, *absent("queue")])
        )

    # planning flag: named -> strong, scaled up to 1 by assigned cost
    pl = bb.planning.get(cid)
    if pl is not None:
        cost_norm = min(1.0, pl.assigned_cost_musd.expected / refs["planning_cost_ref_musd"])
        signals["planning"] = store.add(
            derived_range(
                lo=0.7, expected=max(0.7, 0.5 + 0.5 * cost_norm), hi=1.0,
                basis="Named in a plan; level scaled by assigned upgrade cost.",
                stage=STAGE, provider="spp_itp", lineage_id=f"signal:planning:{cid}",
                inputs=[pl.assigned_cost_musd.lineage_id],
            )
        )
    elif _unobserved_table(bb, "planning"):
        unobserved("planning", "planning (ITP) items not obtained", absent("planning"))
    else:
        signals["planning"] = store.add(
            _const(0.0, cid=cid, name="planning", spread=0.05, inputs=absent("planning"))
        )

    # cost of inaction: rent x persistence fraction.
    if co is not None and co.rent_musd is None:
        unobserved("cost_of_inaction", "no observed congestion $ for this flowgate",
                   [co.hours_binding.lineage_id])
    elif co is not None:
        coi_exp = co.rent_musd.expected * (co.hours_binding.expected / 8760.0)
        coi_lo = co.rent_musd.lo * (co.hours_binding.lo / 8760.0)
        coi_hi = co.rent_musd.hi * (co.hours_binding.hi / 8760.0)
        d = refs["coi_ref_musd"]
        signals["cost_of_inaction"] = store.add(
            derived_range(
                lo=coi_lo / d, expected=coi_exp / d, hi=coi_hi / d,
                basis="rent x persistence (projected realized congestion), normalized.",
                stage=STAGE, provider="spp_binding_constraints",
                lineage_id=f"signal:cost_of_inaction:{cid}",
                inputs=[co.rent_musd.lineage_id, co.hours_binding.lineage_id],
            )
        )
    elif _unobserved_table(bb, "congestion"):
        unobserved("cost_of_inaction", "congestion data not obtained", absent("congestion"))
    else:
        signals["cost_of_inaction"] = store.add(
            _const(0.0, cid=cid, name="cost_of_inaction", spread=0.05,
                   inputs=absent("congestion"))
        )

    return {name: signals[name] for name in SIGNAL_NAMES}, coverage
