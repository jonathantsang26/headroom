"""7.3 Stability — the headline metric is rank stability, not a sharp score.

A corridor earns the shortlist by staying in the top-k across the full plausible
range of every uncertain input. We draw from each signal's Range, recompute each
composite, re-rank, and count how often each constraint lands in the top-k. The
SAME draws produce the composite's reported band (p5/p50/p95), so the displayed
uncertainty and the rank stability are mutually consistent — a wide band and a
binary p_top_k can't disagree.

Reproducibility (§8 + Phase-5 done-when): the RNG is seeded and the seed is recorded
in the run manifest, so a pinned re-run reproduces identical p_top_k.
"""

from __future__ import annotations

import numpy as np

from headroom.model.score import composite_sample
from headroom.provenance.envelope import Range


def top_k(n: int, fraction: float) -> int:
    """Size of the shortlist. Floored at 1 so it is never 0 at small N (a top-decile
    of <10 constraints would otherwise admit nobody and the metric would be dead)."""
    return max(1, round(n * fraction))


def rank_stability(
    signals_by_cid: dict[str, dict[str, Range]],
    weights: dict[str, float],
    *,
    draws: int,
    top_fraction: float,
    seed: int,
) -> tuple[dict[str, float], dict[str, tuple[float, float, float]], int]:
    """Return (p_top_k, composite_percentiles, k).

    * p_top_k[cid]        — probability cid lands in the top-k across draws
    * percentiles[cid]    — (p5, p50, p95) of cid's composite over the SAME draws
    * k                   — shortlist size

    Each draw samples every signal of every constraint, ranks the resulting
    composites, and credits the top-k. Percentiles come from the identical sample
    matrix so the reported band reflects exactly what the ranking saw."""
    cids = list(signals_by_cid)
    n = len(cids)
    k = top_k(n, top_fraction)
    rng = np.random.default_rng(seed)

    samples = np.empty((draws, n))
    hits = np.zeros(n, dtype=np.int64)
    for d in range(draws):
        row = samples[d]
        for j, cid in enumerate(cids):
            row[j] = composite_sample(signals_by_cid[cid], weights, rng)
        # indices of the k largest this draw
        topk_idx = np.argpartition(row, n - k)[n - k:]
        hits[topk_idx] += 1

    p_top = {cid: float(hits[j]) / draws for j, cid in enumerate(cids)}
    pcts: dict[str, tuple[float, float, float]] = {}
    for j, cid in enumerate(cids):
        p5, p50, p95 = np.percentile(samples[:, j], [5, 50, 95])
        pcts[cid] = (float(p5), float(p50), float(p95))
    return p_top, pcts, k
