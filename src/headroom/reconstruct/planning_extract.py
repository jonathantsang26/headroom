"""6.3 Planning exhaust. The operational secret bleeds through the public record:
ITP/FCA name binding constraints + proposed fixes + assigned upgrade costs. Each
extracted item cross-links to its constraint (and thence its congestion-rent obs)."""

from __future__ import annotations

from headroom.provenance.lineage import LineageStore
from headroom.reconstruct.ranges import band
from headroom.schema.entities import PlanningItem

# Which public source each plan came from (for provider provenance).
_PLAN_PROVIDER = {"ITP": "spp_itp", "FCA": "spp_fca"}


def planning_items(rows: list[dict], store: LineageStore) -> dict[str, PlanningItem]:
    out: dict[str, PlanningItem] = {}
    for r in rows:
        cid = r["constraint_id"]
        cost = float(r["assigned_cost_musd"])
        provider = _PLAN_PROVIDER.get(r["plan"], "spp_itp")
        assigned = store.add(
            band(
                cost,
                lo_frac=0.8,
                hi_frac=1.4,  # upgrade costs skew high
                basis=f"{r['plan']} assigned upgrade cost, -20%/+40% band ($M).",
                provider=provider,
                lineage_id=f"plancost:{cid}",
            )
        )
        out[cid] = PlanningItem(
            constraint_id=cid,
            plan=r["plan"],
            proposed_fix=r["proposed_fix"],
            assigned_cost_musd=assigned,
        )
    return out
