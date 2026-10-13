"""Phase 2 (Bucket B): reconstruct the operational reality from public shadows."""

from __future__ import annotations

from dataclasses import dataclass, field

from headroom.ingest.load import IngestBundle
from headroom.provenance.derived import derived_fact
from headroom.provenance.envelope import Range
from headroom.provenance.lineage import LineageStore
from headroom.quality.bucket_a import BucketAResult
from headroom.reconstruct.congestion_rent import congestion_observations
from headroom.reconstruct.network_model import (
    attach_reactance,
    build_dc_model,
    build_injections,
    screen_loadings,
)
from headroom.reconstruct.planning_extract import planning_items
from headroom.schema.entities import Constraint, CongestionObs, Line, PlanningItem, QueueItem

# The dense DC model is a screen for small/medium networks; beyond this many buses
# it is not run (and says so) rather than silently exhausting memory.
MAX_DC_BUSES = 3000

# Typical thermal rating by nominal kV (MW) for a line whose rating is not public.
# An assumption (flagged imputed, low confidence), used only by the DC screen.
TYPICAL_RATING_MW = {69: 70.0, 115: 150.0, 138: 220.0, 161: 300.0, 230: 500.0,
                     345: 1200.0, 500: 2000.0, 765: 3500.0}


@dataclass
class BucketBResult:
    constraints: list[Constraint]
    loading: dict[str, Range]  # constraint_id -> DC N-1 loading Range
    screen_status: dict[str, str]  # constraint_id -> ok / n1_disconnect / ...
    congestion: dict[str, CongestionObs]
    planning: dict[str, PlanningItem]
    queue: list[QueueItem]
    binding_flags: dict[str, str] = field(default_factory=dict)
    table_lineage: dict[str, list[str]] = field(default_factory=dict)
    table_coverage: dict[str, dict] = field(default_factory=dict)
    screen: dict = field(default_factory=dict)  # what the DC screen could and could not do

    @property
    def constraint_ids(self) -> list[str]:
        return [c.constraint_id for c in self.constraints]


def _constraints(bundle: IngestBundle, lines_by_id: dict[str, Line]) -> list[Constraint]:
    out = []
    for r in bundle.constraints:
        mon = (r.get("monitored_line") or "").strip() or None
        ctg = (r.get("contingency_line") or "").strip() or None
        resolved = mon is not None and mon in lines_by_id
        out.append(Constraint(
            constraint_id=r["constraint_id"],
            monitored_line=mon if resolved else None,
            contingency_line=ctg,
            ctype=r["ctype"],
            monitored_facility=(r.get("monitored_facility") or "").strip() or mon,
            contingency_facility=(r.get("contingency_facility") or "").strip() or None,
            contingency_name=(r.get("contingency_name") or "").strip() or None,
            coverage="observed" if resolved else "unobserved",
        ))
    return out


def _estimate_thermal(line: Line, store: LineageStore) -> None:
    kv = float(line.voltage_kv.value)
    nominal = min(TYPICAL_RATING_MW, key=lambda v: abs(v - kv))
    line.thermal_limit_mw = store.add(derived_fact(
        value=TYPICAL_RATING_MW[nominal],
        stage="reconstruct.thermal_estimate",
        lineage_id=f"thermal_est:{line.line_id}",
        inputs=[line.voltage_kv.lineage_id],
        retrieved_at=line.voltage_kv.retrieved_at,
        unit="MW",
        as_reported=f"typical {nominal} kV rating; no public rating",
        flags=["imputed"],
    ))


def run_bucket_b(
    bundle: IngestBundle, bucket_a: BucketAResult, store: LineageStore
) -> BucketBResult:
    lines_by_id = bucket_a.lines_by_id
    constraints = _constraints(bundle, lines_by_id)
    tl = bundle.table_lineage
    context = [*tl.get("hifld_lines", []), *tl.get("buses", []), *tl.get("load_930", [])]

    # §6.2 physics proxy: estimate impedances, build the DC model, screen N-1 loadings
    attach_reactance(bucket_a.lines, store, length_lineage=tl.get("hifld_lines", []))
    mapped = [c for c in constraints if c.monitored_line is not None]
    screen = {"n_constraints": len(constraints), "n_mapped": len(mapped),
              "n_buses": len(bucket_a.buses), "n_lines": len(bucket_a.lines)}
    model = None
    if not mapped:
        screen["status"] = "not_run: no monitored element resolved to public topology"
    elif len(bucket_a.buses) > MAX_DC_BUSES:
        screen["status"] = f"not_run: {len(bucket_a.buses)} buses exceeds MAX_DC_BUSES={MAX_DC_BUSES}"
        mapped = []
        for c in constraints:
            c.monitored_line, c.coverage = None, "unobserved"
    else:
        for c in mapped:
            line = lines_by_id[c.monitored_line]
            if line.thermal_limit_mw is None:
                _estimate_thermal(line, store)
        model = build_dc_model(bucket_a.buses, bucket_a.lines)
        screen["status"] = "run"
    injections = build_injections(bucket_a.buses, bundle.load_930) if model else {}
    loading, screen_status = screen_loadings(
        model, lines_by_id, constraints, injections, store, context_lineage=context,
    )

    binding_flags: dict[str, str] = {}
    for cid, rng in loading.items():
        if rng.hi > 1.0:
            binding_flags[cid] = "binds_under_n1"

    # §6.1 price shadows, §6.3 planning exhaust
    congestion = congestion_observations(bundle.congestion, store)
    planning = planning_items(bundle.planning, store)

    queue = [
        QueueItem(
            request_id=r["request_id"],
            near_bus=(r.get("near_bus") or "").strip(),
            mw=float(r["mw"]),
            fuel=r["fuel"],
            lineage_ids=r.get("_lineage") or [],
        )
        for r in bundle.queue
    ]

    return BucketBResult(
        constraints=constraints,
        loading=loading,
        screen_status=screen_status,
        congestion=congestion,
        planning=planning,
        queue=queue,
        binding_flags=binding_flags,
        table_lineage=tl,
        table_coverage=bundle.table_coverage,
        screen=screen,
    )
