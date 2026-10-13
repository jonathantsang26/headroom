"""One-at-a-time sensitivity of a region's EV shortlist to its GET priors.

For each perturbation, the EV layer is re-run on the same inputs, seed and draws as the
region's scoring file. The perturbations:
- each relief share, annual cost and the haircut set to the low or high end of its
  triangle;
- each applicability probability moved by +/-0.25, clipped to [0, 1];
- forward rent turned off (plain observed rent with its band).

Each one is compared with the baseline by the share of the top-k flowgates kept and the
number of flowgates whose mean EV exceeds $1k/yr.

    python scripts/ev_sensitivity.py --region spp            # 10,000 draws, ~1 min
    python scripts/ev_sensitivity.py --region spp --draws 2000
"""

from __future__ import annotations

import argparse
import copy

from headroom.ingest import ingest_region
from headroom.ingest.region import get_region
from headroom.model import ScoringConfig
from headroom.model.ev import EVIDENCE_CLASSES, SINGLE_GETS, run_ev
from headroom.model.signals import _bus_degree, is_radial
from headroom.provenance.lineage import LineageStore
from headroom.quality import run_bucket_a
from headroom.reconstruct import run_bucket_b


def _ends(spec: dict) -> dict:
    return {end: {"lo": spec[end], "mode": spec[end], "hi": spec[end]} for end in ("lo", "hi")}


def perturbations(priors: dict):
    for end, spec in _ends(priors["haircut"]).items():
        p = copy.deepcopy(priors)
        p["haircut"] = spec
        yield f"haircut at {end}", p, True
    for g in SINGLE_GETS:
        for param in ("relief_share", "annual_cost_musd"):
            for end, spec in _ends(priors["gets"][g][param]).items():
                p = copy.deepcopy(priors)
                p["gets"][g][param] = spec
                yield f"{g} {param} at {end}", p, True
        for cls in EVIDENCE_CLASSES:
            for delta in (-0.25, 0.25):
                v = priors["gets"][g]["applicability"][cls]
                nv = min(1.0, max(0.0, v + delta))
                if nv != v:
                    p = copy.deepcopy(priors)
                    p["gets"][g]["applicability"][cls] = nv
                    yield f"{g} applicability[{cls}] {v:.2f}->{nv:.2f}", p, True
    yield "forward rent off", priors, False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--region", default="spp")
    ap.add_argument("--draws", type=int, default=None)
    args = ap.parse_args(argv)
    cfg = ScoringConfig.load(get_region(args.region).scoring_path)
    draws = args.draws or cfg.draws
    store = LineageStore()
    bundle = ingest_region(args.region, store)
    ba = run_bucket_a(bundle, store)
    bb = run_bucket_b(bundle, ba, store)
    degree = _bus_degree(ba.lines)
    radial = {}
    for c in bb.constraints:
        line = ba.lines_by_id.get(c.monitored_line) if c.monitored_line else None
        radial[c.constraint_id] = is_radial(line, degree) if line else None

    def ranked(priors, forward):
        r = run_ev(bb.constraints, bb, radial, priors, store, draws=draws,
                   top_fraction=cfg.top_fraction, seed=cfg.seed, forward=forward)
        order = sorted(r.ev_mean, key=lambda c: (r.ev_mean[c], r.p_top_k[c]), reverse=True)
        return order, r

    base, r0 = ranked(cfg.get_priors, cfg.forward_rent)
    k = r0.k
    above = lambda r: sum(v > 0.001 for v in r.ev_mean.values())
    print(f"baseline: {len(base)} flowgates with EV, k = {k}, {draws} draws, priors "
          f"{r0.priors_version}; mean EV > $1k/yr: {above(r0)}; sum of mean EV "
          f"${sum(r0.ev_mean.values()):.2f}M/yr")
    results = []
    for name, priors, use_forward in perturbations(cfg.get_priors):
        order, r = ranked(priors, cfg.forward_rent if use_forward else None)
        kept = len(set(order[:k]) & set(base[:k])) / k
        results.append((kept, name, above(r)))
        print(f"{name:58s} top-k kept {kept:5.1%}  mean EV > $1k: {above(r)}")
    print("\nlargest shortlist changes:")
    for kept, name, n in sorted(results)[:8]:
        print(f"  {kept:5.1%} kept  {name}  (mean EV > $1k: {n})")
    print("\nlargest changes in flowgates with mean EV > $1k/yr:")
    for kept, name, n in sorted(results, key=lambda x: abs(x[2] - above(r0)), reverse=True)[:6]:
        print(f"  {n:4d} (baseline {above(r0)})  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
