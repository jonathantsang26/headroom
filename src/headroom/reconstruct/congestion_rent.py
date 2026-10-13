"""6.1 Congestion rent from price shadows. The nodal LMP spread across a binding
constraint IS its shadow price. Input shapes, in precedence order:

- `monthly_cost_musd`: a per-constraint monthly congestion cost handed to us (the
  fixture shape); annualized and bounded.
- `rent_musd`: observed annual rent ($M/yr) per flowgate, e.g. SPP's congestion by
  constraint (sum over intervals of flow x shadow price), carried under its own
  column stamp; bounded by the forward band only.
- `shadow_price_sum_usd_per_mw` alone (snapshots that predate `rent_musd`): rent =
  sum|SP| x limit, with the unpublished limit as a bounded assumption.
- A table that has the `rent_musd` column but no value for a row: rent unobserved
  (never the band, never zero)."""

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
    observed_column = any("rent_musd" in r for r in rows)
    for r in rows:
        cid = r["constraint_id"]
        lineage = r.get("_lineage") or []
        hours = float(r["hours_binding"])
        monthly = (r.get("monthly_cost_musd") or "").strip()
        observed = (r.get("rent_musd") or "").strip()
        rent = None
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
        elif observed:
            v = float(observed)
            lo, hi = sorted((FORWARD_BAND[0] * v, FORWARD_BAND[1] * v))
            rent = store.add(
                derived_range(
                    lo=lo, expected=v, hi=hi,
                    basis="Observed DA congestion rent (sum of flow x shadow price), "
                    "-40%/+50% forward band ($M/yr).",
                    stage="reconstruct", provider=r.get("_rent_provider") or PROVIDER,
                    lineage_id=f"rent:{cid}",
                    inputs=r.get("_rent_lineage") or lineage,
                )
            )
        elif not observed_column:
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
        num = lambda k: float(r[k]) if (r.get(k) or "").strip() else None
        months = num("months_binding")
        recurring = None
        if observed and num("max_month_cost_musd") is not None:
            v = float(observed) - num("max_month_cost_musd")
            if v < -1e-9:
                raise ValueError(f"recurring rent < 0 for {cid}: the largest month exceeds the year")
            v = max(v, 0.0)
            recurring = store.add(derived_range(
                lo=FORWARD_BAND[0] * v, expected=v, hi=FORWARD_BAND[1] * v,
                basis="Observed DA congestion $ outside the flowgate's largest month (annual "
                "minus largest month), -40%/+50% forward band ($M/yr). 0 = all of the "
                "year's congestion $ fell in one month.",
                stage="reconstruct", provider=r.get("_rent_provider") or PROVIDER,
                lineage_id=f"recurring_rent:{cid}",
                inputs=[*(r.get("_rent_lineage") or []), *(r.get("_max_month_lineage") or [])]
                or lineage,
            ))
        out[cid] = CongestionObs(
            constraint_id=cid,
            period=(r.get("period") or "").strip() or period,
            rent_musd=rent,
            hours_binding=hours_binding,
            recurring_rent_musd=recurring,
            breached_hours=num("breached_hours"),
            months_binding=int(months) if months is not None else None,
            shadow_price_sum=num("shadow_price_sum_usd_per_mw"),
            fact_ids=[*lineage, *(r.get("_rent_lineage") or [])],
        )
    return out
