"""Forward congestion rent for the EV layer, calibrated on held-out months.

A year of day-ahead congestion dollars is a weak forecast of the next period: much of
a flowgate's rent can fall in one or two months. The EV layer therefore does not use
the observed annual rent R as next year's value. It uses

    R_forward = R x share x eps

- share = months_binding / 12, the share of the year's months the flowgate bound in.
  Of the predictors tried on within-year splits (scripts/backtest_forward_rent.py),
  it captured the most held-out dollars at shortlist size.
- eps is drawn from the empirical distribution of realized / predicted dollars of the
  leaders in those splits, normalized by the system-wide change between the halves.
  It is stated as quantiles, read by linear interpolation, and truncated at its last
  quantile.

The calibration lives in the region's scoring file (`forward_rent`), with the rule,
the snapshot and the sha256 of the files it was derived from. The backtest can only
use months of one year (2025). A cross-year test needs snapshots from several years.
"""

from __future__ import annotations

import numpy as np

PREDICTORS = ("annual", "months_binding_share")


def validate_forward(spec: dict) -> dict:
    if not isinstance(spec, dict) or spec.get("predictor") not in PREDICTORS:
        raise ValueError(f"forward_rent.predictor must be one of {PREDICTORS}")
    q = spec.get("ratio_quantiles")
    if not isinstance(q, dict) or len(q) < 2:
        raise ValueError("forward_rent.ratio_quantiles needs at least two {p: value} points")
    ps = sorted(float(p) for p in q)
    vs = [float(q[k]) for k in sorted(q, key=float)]
    if ps[0] != 0.0 or ps[-1] != 1.0:
        raise ValueError("forward_rent.ratio_quantiles must run from p=0 to p=1")
    if any(v < 0 for v in vs) or any(b < a for a, b in zip(vs, vs[1:])):
        raise ValueError("forward_rent.ratio_quantiles must be >= 0 and non-decreasing")
    return spec


def share(predictor: str, months_binding: int | None) -> float:
    if predictor == "annual" or months_binding is None:
        return 1.0
    return min(max(months_binding, 0), 12) / 12.0


def ratio_sampler(spec: dict):
    """eps = F^-1(U) by linear interpolation between the stated quantiles."""
    q = spec["ratio_quantiles"]
    ps = np.array(sorted(float(p) for p in q))
    vs = np.array([float(q[k]) for k in sorted(q, key=float)])
    return lambda u: np.interp(u, ps, vs)


def ratio_mean(spec: dict) -> float:
    """Mean of eps (the integral of the interpolated quantile function)."""
    q = spec["ratio_quantiles"]
    ps = np.array(sorted(float(p) for p in q))
    vs = np.array([float(q[k]) for k in sorted(q, key=float)])
    return float(np.sum((ps[1:] - ps[:-1]) * (vs[1:] + vs[:-1]) / 2))


# --- the backtest (scripts/backtest_forward_rent.py) ---------------------------------

SPLITS = {
    "H1->H2": (range(0, 6), range(6, 12)),
    "H2->H1": (range(6, 12), range(0, 6)),
    "odd->even": ((0, 2, 4, 6, 8, 10), (1, 3, 5, 7, 9, 11)),
    "even->odd": ((1, 3, 5, 7, 9, 11), (0, 2, 4, 6, 8, 10)),
}


def predict(predictor: str, train: np.ndarray) -> np.ndarray:
    """Predicted held-out dollars from training months (rows = flowgates)."""
    total = train.sum(axis=1)
    if predictor == "annual":
        return total
    active = (train > 0).sum(axis=1)
    return total * active / train.shape[1]


def backtest(monthly: np.ndarray, *, top_fraction: float = 0.05) -> dict:
    """For each predictor and split: held-out dollars captured by the predicted top k
    (as a share of the oracle top k), top-k overlap, leaders realizing $0, and the
    system-normalized realized / predicted ratios of the leaders."""
    out: dict = {}
    for predictor in PREDICTORS:
        per_split, ratios = {}, []
        for name, (tr, te) in SPLITS.items():
            train, held = monthly[:, list(tr)], monthly[:, list(te)].sum(axis=1)
            p = predict(predictor, train)
            live = p > 0
            k = max(1, round(int(live.sum()) * top_fraction))
            top = np.argsort(-p, kind="stable")[:k]
            oracle = np.argsort(-held, kind="stable")[:k]
            system = held.sum() / train.sum()
            per_split[name] = {
                "k": k,
                "capture": float(held[top].sum() / held[oracle].sum()),
                "overlap": len(set(top.tolist()) & set(oracle.tolist())) / k,
                "leaders_realizing_zero": int((held[top] == 0).sum()),
            }
            ratios.extend((held[top] / p[top] / system).tolist())
        out[predictor] = {
            "splits": per_split,
            "mean_capture": float(np.mean([s["capture"] for s in per_split.values()])),
            "ratios": np.array(ratios),
        }
    return out


QUANTILE_POINTS = (0.0, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99)


def ratio_quantiles(ratios: np.ndarray) -> dict[str, float]:
    """Quantiles of the leaders' ratios, 3 significant figures; p=1 is the p99 (the
    distribution is truncated there)."""
    qs = {f"{p:g}": float(f"{np.percentile(ratios, 100 * p):.3g}") for p in QUANTILE_POINTS}
    qs["1"] = qs["0.99"]
    return qs
