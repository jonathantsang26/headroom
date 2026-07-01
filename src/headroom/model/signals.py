"""7.1 Signals — each a `Range`, kept explainable (visible, never folded into one
opaque composite). Raw quantities are normalized to ~0-1 by fixed references so the
weighted composite is interpretable."""

from __future__ import annotations

from headroom.provenance.envelope import Range
from headroom.provenance.lineage import LineageStore
from headroom.provenance.synthetic import synthetic_range
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


def _scaled(rng: Range, denom: float, *, cid: str, name: str,
            inputs: list[str] | None = None) -> Range:
    """Normalize a Range by a fixed reference (preserves the distribution shape).
    `inputs` chains this signal to the upstream Range it was derived from, so the
    publishable gate's taint walk reaches that Range's source."""
    d = denom or 1.0
    return synthetic_range(
        lo=rng.lo / d,
        expected=rng.expected / d,
        hi=rng.hi / d,
        basis=f"{name} normalized by reference {d}.",
        provider=rng.provider or rng.source,
        lineage_id=f"signal:{name}:{cid}",
        inputs=inputs,
    )


def _const(value: float, *, cid: str, name: str, spread: float = 0.0,
           inputs: list[str] | None = None) -> Range:
    return synthetic_range(
        lo=max(0.0, value - spread),
        expected=value,
        hi=value + spread,
        basis=f"{name} (point with small uncertainty band).",
        provider="model.signals",
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


def queue_mw_near(constraint: Constraint, line: Line, bb: BucketBResult,
                  adjacency: dict[str, set[str]]) -> float:
    """Queue MW at the monitored line's endpoints and their 1-hop neighbors."""
    near = {line.from_bus, line.to_bus}
    near |= adjacency.get(line.from_bus, set()) | adjacency.get(line.to_bus, set())
    return sum(q.mw for q in bb.queue if q.near_bus in near)


def _adjacency(lines: list[Line]) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {}
    for ln in lines:
        adj.setdefault(ln.from_bus, set()).add(ln.to_bus)
        adj.setdefault(ln.to_bus, set()).add(ln.from_bus)
    return adj


def build_signals(
    constraint: Constraint,
    line: Line,
    bb: BucketBResult,
    refs: dict,
    adjacency: dict[str, set[str]],
    store: LineageStore,
) -> dict[str, Range]:
    cid = constraint.constraint_id
    co = bb.congestion.get(cid)
    loading = bb.loading[cid]

    signals: dict[str, Range] = {}

    # rent magnitude
    if co is not None:
        signals["rent"] = store.add(
            _scaled(co.rent_musd, refs["rent_ref_musd"], cid=cid, name="rent",
                    inputs=[co.rent_musd.lineage_id])
        )
        signals["persistence"] = store.add(
            _scaled(co.hours_binding, refs["persist_ref_hours"], cid=cid,
                    name="persistence", inputs=[co.hours_binding.lineage_id])
        )
    else:
        signals["rent"] = store.add(_const(0.0, cid=cid, name="rent", spread=0.05))
        signals["persistence"] = store.add(
            _const(0.0, cid=cid, name="persistence", spread=0.05)
        )

    # loading (a fraction of thermal). The reference is now an EXPLICIT config knob
    # (references.loading_ref), not a hidden 1.0, so loading is normalized on the same
    # deliberate, tunable footing as every other signal. Default 1.0 keeps 100%
    # loading -> 1.0 (an overloaded line scores >1, which is intended); retune via the
    # Experiments slider to see the effect on the ranking.
    signals["loading"] = store.add(
        _scaled(loading, refs.get("loading_ref", 1.0), cid=cid, name="loading",
                inputs=[loading.lineage_id])
    )

    # demand pressure: queue MW nearby
    qmw = queue_mw_near(constraint, line, bb, adjacency)
    signals["demand_pressure"] = store.add(
        _const(qmw / refs["demand_ref_mw"], cid=cid, name="demand_pressure",
               spread=0.05)
    )

    # planning flag: named -> strong, scaled up to 1 by assigned cost
    pl = bb.planning.get(cid)
    if pl is not None:
        cost_norm = min(1.0, pl.assigned_cost_musd.expected / refs["planning_cost_ref_musd"])
        signals["planning"] = store.add(
            synthetic_range(
                lo=0.7, expected=max(0.7, 0.5 + 0.5 * cost_norm), hi=1.0,
                basis="Named in a plan; level scaled by assigned upgrade cost.",
                provider="spp_itp", lineage_id=f"signal:planning:{cid}",
                inputs=[pl.assigned_cost_musd.lineage_id],
            )
        )
    else:
        signals["planning"] = store.add(
            _const(0.0, cid=cid, name="planning", spread=0.05)
        )

    # cost of inaction: rent x persistence fraction.
    # KNOWN SIMPLIFICATION: this is emitted as an independent signal Range, so a
    # Monte-Carlo draw samples it independently of `rent`/`persistence` even though
    # it is mechanically ~rent*persistence. At weight 0.10 this does not move the
    # ranking; a fully coherent model would derive it inside composite_sample from the
    # already-sampled rent and persistence. Logged, not silently assumed away.
    if co is not None:
        coi_exp = co.rent_musd.expected * (co.hours_binding.expected / 8760.0)
        coi_lo = co.rent_musd.lo * (co.hours_binding.lo / 8760.0)
        coi_hi = co.rent_musd.hi * (co.hours_binding.hi / 8760.0)
        d = refs["coi_ref_musd"]
        signals["cost_of_inaction"] = store.add(
            synthetic_range(
                lo=coi_lo / d, expected=coi_exp / d, hi=coi_hi / d,
                basis="rent x persistence (projected realized congestion), normalized.",
                provider="spp_binding_constraints",
                lineage_id=f"signal:cost_of_inaction:{cid}",
                inputs=[co.rent_musd.lineage_id, co.hours_binding.lineage_id],
            )
        )
    else:
        signals["cost_of_inaction"] = store.add(
            _const(0.0, cid=cid, name="cost_of_inaction", spread=0.05)
        )

    return signals
