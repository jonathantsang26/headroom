"""Phase 2 (Bucket B): reconstruct the operational reality from public shadows."""

from __future__ import annotations

from dataclasses import dataclass, field

from headroom.ingest.load import IngestBundle
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
from headroom.schema.entities import Constraint, CongestionObs, PlanningItem, QueueItem


@dataclass
class BucketBResult:
    constraints: list[Constraint]
    loading: dict[str, Range]  # constraint_id -> DC N-1 loading Range
    screen_status: dict[str, str]  # constraint_id -> ok / n1_disconnect / ...
    congestion: dict[str, CongestionObs]
    planning: dict[str, PlanningItem]
    queue: list[QueueItem]
    binding_flags: dict[str, str] = field(default_factory=dict)

    @property
    def constraint_ids(self) -> list[str]:
        return [c.constraint_id for c in self.constraints]


def run_bucket_b(
    bundle: IngestBundle, bucket_a: BucketAResult, store: LineageStore
) -> BucketBResult:
    constraints = [
        Constraint(
            constraint_id=r["constraint_id"],
            monitored_line=r["monitored_line"],
            contingency_line=r["contingency_line"] or None,
            ctype=r["ctype"],
        )
        for r in bundle.constraints
    ]

    # §6.2 physics proxy: estimate impedances, build the DC model, screen N-1 loadings
    attach_reactance(bucket_a.lines, store)
    model = build_dc_model(bucket_a.buses, bucket_a.lines)
    injections = build_injections(bucket_a.buses, bundle.load_930)
    loading, screen_status = screen_loadings(
        model, bucket_a.lines_by_id, constraints, injections, store
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
            near_bus=r["near_bus"],
            mw=float(r["mw"]),
            fuel=r["fuel"],
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
    )
