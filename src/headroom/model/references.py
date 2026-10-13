"""Scoring references derived by a stated rule, and the guard that ties a region's
references to the congestion table they were derived from."""

from __future__ import annotations

import numpy as np


def _sig4(x: float) -> float:
    return float(f"{x:.4g}")


def derive_references(rows: list[dict], *, q: float = 0.99,
                      coi_definition: str = "recurring_rent") -> dict:
    """p{100q} (numpy linear) of each raw signal's expected value over the flowgates
    where it is observed (unobserved excluded, observed zeros included), 4 significant
    figures. `rows` are normalized congestion rows."""
    has = lambda r, k: bool((r.get(k) or "").strip())
    rent = [float(r["rent_musd"]) for r in rows if has(r, "rent_musd")]
    hours = [float(r["hours_binding"]) for r in rows]
    if coi_definition == "recurring_rent":
        coi = [max(float(r["rent_musd"]) - float(r["max_month_cost_musd"]), 0.0)
               for r in rows if has(r, "rent_musd") and has(r, "max_month_cost_musd")]
    elif coi_definition == "rent_x_persistence":
        coi = [float(r["rent_musd"]) * float(r["hours_binding"]) / 8760.0
               for r in rows if has(r, "rent_musd")]
    else:
        raise ValueError(f"unknown cost_of_inaction definition {coi_definition!r}")
    pops = {"rent_ref_musd": rent, "persist_ref_hours": hours, "coi_ref_musd": coi}
    out = {}
    for name, values in pops.items():
        if not values:
            raise ValueError(f"no observed population for {name}")
        ref = _sig4(float(np.percentile(values, 100 * q, method="linear")))
        if not ref > 0:
            raise ValueError(f"{name} would be {ref}; references must be > 0")
        out[name] = ref
    return {"references": out, "n_rent": len(rent), "n_hours": len(hours), "n_coi": len(coi)}


def check_reference_rule(scoring, input_identity: dict) -> None:
    """Fail closed when a region's references were derived from a different
    congestion table than the one this run reads."""
    rule = scoring.reference_rule or {}
    want = rule.get("congestion_sha256")
    if not want:
        return
    got = input_identity.get("tables", {}).get("congestion", {}).get("sha256")
    if got != want:
        raise ValueError(
            f"scoring references were derived from snapshot {rule.get('derived_from_snapshot')} "
            f"(congestion {want[:12]}); this input's congestion table is {str(got)[:12]}. "
            "Re-derive: python scripts/derive_scoring_refs.py <snapshot> --check <scoring file>, "
            "or run with --scoring config/scoring.yaml"
        )
