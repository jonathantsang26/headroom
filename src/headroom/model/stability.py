"""7.3 Stability — the headline metric is rank stability, not a sharp score."""

from __future__ import annotations

import numpy as np

from headroom.model.score import composite_sample
from headroom.provenance.envelope import Range


def top_k(n: int, fraction: float) -> int:
    """Size of the shortlist = round(n * fraction), where `fraction` is the configured
    `top_fraction` (default 0.25, a top quartile). Floored at 1 so it is never 0 at
    small N (any small fraction of <1/fraction constraints would otherwise admit
    nobody and the metric would be dead)."""
    return max(1, round(n * fraction))


def rank_stability(
    signals_by_cid: dict[str, dict[str, Range]],
    weights: dict[str, float],
    *,
    draws: int,
    top_fraction: float,
    seed: int,
) -> tuple[dict[str, float], dict[str, tuple[float, float, float]], int]:
    """Return (p_top_k, composite_percentiles, k)."""
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
