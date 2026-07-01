"""7.3 Score — the composite is a weighted combination of the signal `Range`s, but
reported as a DISTRIBUTION (see stability.py), never a single sharp point."""

from __future__ import annotations

from headroom.provenance.envelope import Range
from headroom.provenance.lineage import LineageStore
from headroom.provenance.synthetic import synthetic_range


def composite_sample(signals: dict[str, Range], weights: dict[str, float], rng) -> float:
    """One Monte-Carlo draw of the composite: sample each signal's Range, weight, sum.
    `rng` is the seeded numpy Generator (seed in the manifest)."""
    return sum(weights[k] * signals[k].sample(rng) for k in weights)


def composite_from_percentiles(
    cid: str,
    pcts: tuple[float, float, float],
    signals: dict[str, Range],
    store: LineageStore | None = None,
) -> Range:
    """Composite Range as (p5, p50, p95) of the Monte-Carlo draws — the SAME draws
    that produced p_top_k, so the displayed band and the rank stability agree."""
    p5, p50, p95 = pcts
    rng = synthetic_range(
        lo=p5, expected=p50, hi=p95,
        basis="Composite p5/p50/p95 over Monte-Carlo draws of the signal Ranges.",
        provider="model.score", lineage_id=f"composite:{cid}",
    )
    rng.inputs = [signals[k].lineage_id for k in signals]
    if store is not None:
        store.add(rng)
    return rng
