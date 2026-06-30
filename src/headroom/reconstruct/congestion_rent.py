"""6.1 Congestion rent from price shadows. The nodal LMP spread across a binding
constraint IS its shadow price; SPP publishes per-constraint monthly congestion
cost directly, so here the magnitude is largely handed to us and we bound it.

Emits a `CongestionObs` (rent Range + hours-binding Range) per constraint."""

from __future__ import annotations

from headroom.provenance.lineage import LineageStore
from headroom.reconstruct.ranges import band
from headroom.schema.entities import CongestionObs

PROVIDER = "spp_binding_constraints"


def congestion_observations(
    rows: list[dict], store: LineageStore, *, period: str = "2025"
) -> dict[str, CongestionObs]:
    """`rows` carry monthly_cost_musd + hours_binding per constraint."""
    out: dict[str, CongestionObs] = {}
    for r in rows:
        cid = r["constraint_id"]
        annual_rent = float(r["monthly_cost_musd"]) * 12.0
        hours = float(r["hours_binding"])
        rent = store.add(
            band(
                annual_rent,
                lo_frac=0.7,
                hi_frac=1.3,
                basis="SPP monthly DA congestion cost annualized, ±30% band ($M/yr).",
                provider=PROVIDER,
                lineage_id=f"rent:{cid}",
            )
        )
        hours_binding = store.add(
            band(
                hours,
                lo_frac=0.8,
                hi_frac=1.2,
                basis="Reported binding hours, ±20% measurement band.",
                provider=PROVIDER,
                lineage_id=f"hours:{cid}",
            )
        )
        out[cid] = CongestionObs(
            constraint_id=cid,
            period=period,
            rent_musd=rent,
            hours_binding=hours_binding,
        )
    return out
