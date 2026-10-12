"""6.1 Congestion rent from price shadows. The nodal LMP spread across a binding
constraint IS its shadow price. Two input shapes:

- `monthly_cost_musd`: a per-constraint congestion cost handed to us; we bound it.
- `shadow_price_sum_usd_per_mw`: the sum of |shadow price| over binding hours
  ($/MW-yr, what SPP actually posts). Rent = shadow price x flow, and a binding
  constraint flows at its limit, so rent = sum|SP| x limit. The limit (rating) of
  the monitored element is not public, so it enters as a bounded assumption."""

from __future__ import annotations

from headroom.provenance.lineage import LineageStore
from headroom.reconstruct.ranges import band
from headroom.provenance.derived import derived_range
from headroom.schema.entities import CongestionObs

PROVIDER = "spp_binding_constraints"

# Effective limit of an unplaced monitored element (MW): typical 115-345 kV line
# ratings, mode at the 138 kV class. An assumption, not an observation.
RATING_MW_UNOBSERVED = (100.0, 250.0, 1200.0)
# Forward congestion is genuinely uncertain: next year's rent vs this year's.
FORWARD_BAND = (0.6, 1.5)


def congestion_observations(
    rows: list[dict], store: LineageStore, *, period: str = "2025"
) -> dict[str, CongestionObs]:
    """`rows` carry hours_binding plus either monthly_cost_musd or the observed
    shadow-price sum per constraint."""
    out: dict[str, CongestionObs] = {}
    for r in rows:
        cid = r["constraint_id"]
        lineage = r.get("_lineage") or []
        hours = float(r["hours_binding"])
        monthly = (r.get("monthly_cost_musd") or "").strip()
        if monthly:
            rent = store.add(
                band(
                    float(monthly) * 12.0,
                    lo_frac=FORWARD_BAND[0],
                    hi_frac=FORWARD_BAND[1],
                    basis="SPP monthly DA congestion cost annualized, -40%/+50% band "
                    "(forward congestion is genuinely uncertain) ($M/yr).",
                    provider=PROVIDER,
                    lineage_id=f"rent:{cid}",
                    inputs=lineage,
                )
            )
        else:
            sp = float(r["shadow_price_sum_usd_per_mw"])
            r_lo, r_mid, r_hi = RATING_MW_UNOBSERVED
            rent = store.add(
                derived_range(
                    lo=FORWARD_BAND[0] * sp * r_lo / 1e6,
                    expected=sp * r_mid / 1e6,
                    hi=FORWARD_BAND[1] * sp * r_hi / 1e6,
                    basis=(
                        f"sum |DA shadow price| ({sp:,.0f} $/MW) x unobserved rating "
                        f"{r_lo:.0f}-{r_hi:.0f} MW (mode {r_mid:.0f}), -40%/+50% "
                        "forward band ($M/yr). Rating is an assumption."
                    ),
                    stage="reconstruct",
                    provider=PROVIDER,
                    lineage_id=f"rent:{cid}",
                    inputs=lineage,
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
                inputs=lineage,
            )
        )
        out[cid] = CongestionObs(
            constraint_id=cid,
            period=(r.get("period") or "").strip() or period,
            rent_musd=rent,
            hours_binding=hours_binding,
        )
    return out
