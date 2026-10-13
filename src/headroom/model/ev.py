"""7.4 Expected value of a GET deployment, per flowgate.

For flowgate f and GET g:

    EV_g = applicable_g(f) x u_g x h x R_f - C_g

- R_f: observed annual congestion rent on the flowgate ($M/yr, a Range with the
  forward band). Rent = sum over binding hours of shadow price x flow, and a binding
  flowgate flows at its limit, so the implied limit is R_f / sum|SP|. Relieving that
  limit by a share u is worth (sum|SP|) x u x (R_f / sum|SP|) = u x R_f to first
  order: ratings and line lengths, which are not public, drop out.
- u_g: relief share the GET delivers during binding hours (a prior).
- h: haircut for shadow prices falling as the limit rises and other constraints
  binding instead (a prior; first order overstates).
- C_g: annualized cost of one deployment that relieves one flowgate ($M/yr, a prior).
- applicable_g(f): Bernoulli with a prior probability by evidence class (element
  type, BASE vs N-1, episodic binding). Whether a GET can relieve a limit is a
  property of the physical element, so each draw takes one uniform per (element,
  GET) and every flowgate on that element compares it with its own class's
  probability: flowgates on one element agree, and a higher-probability class is
  applicable whenever a lower one is. The element is the corridor key: a line, an
  interface, or all of a substation's transformers (SPP's public names do not tell
  the banks apart), so transformers at one substation share their draws.

Priors (u, h, C, applicability) are judgement with cited sources, loaded from a
priors file whose full content and sha256 are recorded in the run manifest. They are
assumptions, not data: they appear in each EV Range's basis, never as lineage leaves.
EV lineage cites only observed facts (rent, the constraint row, its congestion row).

Per Monte Carlo draw, u, h and C are shared across flowgates (they are uncertainty
about the technology, not the element); applicability is drawn per element. Doing
nothing is always an option (EV 0), so the best EV of a draw is never negative. The
ranking metric is the mean over draws of the best EV: the expected net value, with the
chance that no GET applies priced in. p_top_k (the share of draws a flowgate is in the
top k by best EV with a positive best EV; zero-EV ties never count) is reported for
stability but does not order the list: for the leaders it converges to P(some GET
applies to the element), a constant per evidence class, so ordering by it would sort
them by sampling noise. The recommendation is the GET with the highest expected net value
if pursued, E[max(0, EV_g)] with EV_g counted only where g applies (none if no GET has a
positive value); the share of draws each option is best is reported beside it."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from headroom.model.forward import ratio_mean, ratio_sampler, share as forward_share
from headroom.provenance.derived import derived_range
from headroom.provenance.envelope import Range
from headroom.provenance.lineage import LineageStore
from headroom.reconstruct.bucket_b import BucketBResult
from headroom.schema.entities import Constraint

STAGE = "model.ev"
NO_GET = "No cost-effective GET"
SINGLE_GETS = ("Dynamic Line Rating", "Advanced Power Flow Control", "Topology Optimization")
COMBINATION = "Combination"
EVIDENCE_CLASSES = (
    "line_base", "line_n1", "line_n1_episodic", "line_radial",
    "transformer_base", "transformer_n1", "interface", "loopflow",
)
_CHUNK = 1000


def _triangle(spec: dict, name: str) -> tuple[float, float, float]:
    try:
        lo, mode, hi = (float(spec[k]) for k in ("lo", "mode", "hi"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"GET prior {name} needs numeric lo/mode/hi") from exc
    if not lo <= mode <= hi:
        raise ValueError(f"GET prior {name} needs lo <= mode <= hi")
    return lo, mode, hi


def validate_priors(priors: dict) -> dict:
    """Check the priors file's shape; return it unchanged."""
    if not isinstance(priors, dict) or not isinstance(priors.get("version"), str):
        raise ValueError("GET priors need a version string")
    try:  # recorded verbatim in the run manifest and hashed: JSON values only
        json.dumps(priors, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"GET priors must hold JSON values only (quote dates): {exc}") from exc
    lo, _, hi = _triangle(priors.get("haircut", {}), "haircut")
    if lo < 0 or hi > 1:
        raise ValueError("GET prior haircut must lie in [0, 1]")
    gets = priors.get("gets")
    if not isinstance(gets, dict) or set(gets) != set(SINGLE_GETS):
        raise ValueError(f"GET priors must define exactly {SINGLE_GETS}")
    for g, spec in gets.items():
        ulo, _, uhi = _triangle(spec.get("relief_share", {}), f"{g}.relief_share")
        if ulo < 0 or uhi > 1:
            raise ValueError(f"GET prior {g}.relief_share must lie in [0, 1]")
        clo, _, _ = _triangle(spec.get("annual_cost_musd", {}), f"{g}.annual_cost_musd")
        if clo < 0:
            raise ValueError(f"GET prior {g}.annual_cost_musd must be >= 0")
        app = spec.get("applicability")
        if not isinstance(app, dict) or set(app) != set(EVIDENCE_CLASSES):
            raise ValueError(f"GET prior {g}.applicability must cover {EVIDENCE_CLASSES}")
        if any(not isinstance(p, (int, float)) or not 0 <= p <= 1 for p in app.values()):
            raise ValueError(f"GET prior {g}.applicability values must lie in [0, 1]")
    return priors


def load_priors(path: str | Path) -> dict:
    return validate_priors(yaml.safe_load(Path(path).read_text()))


def priors_sha256(priors: dict) -> str:
    canonical = json.dumps(priors, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def evidence_class(c: Constraint, *, radial: bool | None, months_binding: int | None) -> str:
    """What is observed about the binding element, as one class."""
    if c.ctype == "loopflow":
        return "loopflow"
    if c.ctype == "interface":
        return "interface"
    base = c.base_case
    if c.ctype == "transformer":
        return "transformer_base" if base else "transformer_n1"
    if radial:
        return "line_radial"
    if base:
        return "line_base"
    if months_binding is not None and months_binding <= 1:
        return "line_n1_episodic"
    return "line_n1"


@dataclass
class EVResult:
    ev: dict[str, Range]  # constraint_id -> best-EV Range ($M/yr, p5/p50/p95)
    ev_mean: dict[str, float]  # constraint_id -> mean best EV over draws (ranks)
    p_top_k: dict[str, float]
    p_best: dict[str, dict[str, float]]  # constraint_id -> {option: share of draws}
    recommended: dict[str, str]
    option_value: dict[str, dict[str, float]]  # cid -> {GET: E[max(0, EV_g) if applicable]}
    candidates: dict[str, list[str]]  # GETs with nonzero applicability
    evidence: dict[str, str]
    unobserved: list[str]  # no observed rent: EV not computed
    k: int
    priors_version: str
    priors_sha256: str


def _tri(rng, lo, mode, hi, size):
    lo, mode, hi = (np.broadcast_to(np.asarray(x, dtype=float), size) for x in (lo, mode, hi))
    out = np.array(mode, dtype=float, copy=True)
    live = hi > lo
    if live.any():
        out[live] = rng.triangular(lo[live], mode[live], hi[live])
    return out


def run_ev(
    constraints: list[Constraint],
    bb: BucketBResult,
    radial_by_cid: dict[str, bool | None],
    priors: dict,
    store: LineageStore,
    *,
    draws: int,
    top_fraction: float,
    seed: int,
    forward: dict | None = None,
) -> EVResult:
    """Monte Carlo EV of each flowgate's best GET. Uses its own random stream
    (seed + 1) so the composite's draws are untouched. With `forward`
    (model/forward.py), each draw's rent is the observed rent x the months-binding
    share x a draw from the backtest's ratio distribution; without it, a draw from
    the rent Range."""
    rng = np.random.default_rng(seed + 1)
    sha = priors_sha256(priors)
    gets = priors["gets"]
    hlo, hmode, hhi = _triangle(priors["haircut"], "haircut")

    scored, unobserved, evidence = [], [], {}
    for c in constraints:
        co = bb.congestion.get(c.constraint_id)
        months = co.months_binding if co is not None else None
        evidence[c.constraint_id] = evidence_class(
            c, radial=radial_by_cid.get(c.constraint_id), months_binding=months)
        if co is None or co.rent_musd is None:
            unobserved.append(c.constraint_id)
        else:
            scored.append((c, co))
    n = len(scored)
    k = max(1, round(n * top_fraction)) if n else 0
    options = [NO_GET, *SINGLE_GETS, COMBINATION]
    p_app = {g: np.array([gets[g]["applicability"][evidence[c.constraint_id]] for c, _ in scored])
             for g in SINGLE_GETS}
    elements = {e: i for i, e in enumerate(sorted({c.corridor for c, _ in scored}))}
    element_of = np.array([elements[c.corridor] for c, _ in scored], dtype=int)

    best_all = np.empty((n, draws)) if n else np.empty((0, draws))
    top_hits = np.zeros(n)
    best_counts = np.zeros((n, len(options)))
    option_sums = np.zeros((n, len(options)))  # sum over draws of max(0, EV_g), 0 if n/a
    if n:
        lo = np.array([co.rent_musd.lo for _, co in scored])
        mode = np.array([co.rent_musd.expected for _, co in scored])
        hi = np.array([co.rent_musd.hi for _, co in scored])
        if forward is not None:
            eps = ratio_sampler(forward)
            point = mode * np.array([forward_share(forward["predictor"], co.months_binding)
                                     for _, co in scored])
        for start in range(0, draws, _CHUNK):
            d = min(_CHUNK, draws - start)
            if forward is not None:
                rent = point[:, None] * eps(rng.random((n, d)))
            else:
                rent = _tri(rng, lo[:, None], mode[:, None], hi[:, None], (n, d))
            h = _tri(rng, hlo, hmode, hhi, (d,))
            ev = {}
            app = {}
            u = {}
            cost = {}
            for g in SINGLE_GETS:
                u[g] = _tri(rng, *_triangle(gets[g]["relief_share"], g), (d,))
                cost[g] = _tri(rng, *_triangle(gets[g]["annual_cost_musd"], g), (d,))
                draw = rng.random((len(elements), d))  # one per element and GET
                app[g] = draw[element_of] < p_app[g][:, None]
                ev[g] = np.where(app[g], u[g] * h * rent - cost[g], -np.inf)
            dlr, apfc = SINGLE_GETS[0], SINGLE_GETS[1]
            u_c = 1.0 - (1.0 - u[dlr]) * (1.0 - u[apfc])
            ev[COMBINATION] = np.where(app[dlr] & app[apfc],
                                       u_c * h * rent - (cost[dlr] + cost[apfc]), -np.inf)
            stack = np.stack([np.zeros((n, d)), *(ev[g] for g in options[1:])])
            choice = stack.argmax(axis=0)  # ties go to the earlier option: none first
            best = stack.max(axis=0)
            best_all[:, start:start + d] = best
            for j in range(len(options)):
                best_counts[:, j] += (choice == j).sum(axis=1)
                option_sums[:, j] += np.maximum(stack[j], 0.0).sum(axis=1)
            idx = np.argpartition(best, n - k, axis=0)[n - k:, :]
            # a draw where the best option is doing nothing (EV 0) is never a top-k hit:
            # otherwise zero-EV flowgates would enter the top k by tie-breaking alone
            hit = np.take_along_axis(best, idx, axis=0) > 0
            np.add.at(top_hits, idx[hit], 1)

    rent_term = ("forward rent (2025 observed rent x months-binding share x a backtest "
                 f"ratio draw, mean {ratio_mean(forward):.3g})" if forward is not None
                 else "observed rent (forward band)")
    out_ev, out_mean, out_p, out_best, out_rec, out_cand = {}, {}, {}, {}, {}, {}
    out_val = {}
    for i, (c, co) in enumerate(scored):
        cid = c.constraint_id
        p5, p50, p95 = (float(x) for x in np.percentile(best_all[i], [5, 50, 95]))
        out_mean[cid] = float(best_all[i].mean())
        out_ev[cid] = store.add(derived_range(
            lo=p5, expected=p50, hi=p95,
            basis=(
                f"Best-GET net value ($M/yr): per draw, max(0, relief share x haircut x "
                f"{rent_term} - annualized cost) over the GETs drawn applicable to the "
                f"element (none applicable: 0); p5/p50/p95 over {draws} draws, mean "
                f"{out_mean[cid]:.4g} ranks; evidence class {evidence[cid]}; priors "
                f"{priors['version']} (sha256 {sha[:12]}) are cited assumptions, not "
                f"observations."
            ),
            stage=STAGE, lineage_id=f"ev:{cid}",
            inputs=[co.rent_musd.lineage_id, f"obs:constraints:{cid}", *co.fact_ids],
        ))
        out_p[cid] = float(top_hits[i]) / draws
        shares = {opt: float(best_counts[i, j]) / draws for j, opt in enumerate(options)}
        out_best[cid] = shares
        # the GET worth pursuing: the highest expected net value if it is the one pursued
        # (0 where it turns out not to apply or not to pay); none if no GET is worth it.
        # The most frequent best option would read "none" whenever every applicability
        # probability is below one half, however large the expected value.
        value = {opt: float(option_sums[i, j]) / draws for j, opt in enumerate(options)}
        out_val[cid] = {o: v for o, v in value.items() if o != NO_GET}
        best_get = max(options[1:], key=lambda o: (value[o], -options.index(o)))
        out_rec[cid] = best_get if value[best_get] > 0 else NO_GET
    for c in constraints:
        cid = c.constraint_id
        out_cand[cid] = [g for g in SINGLE_GETS
                         if gets[g]["applicability"][evidence[cid]] > 0]
        if all(g in out_cand[cid] for g in SINGLE_GETS[:2]):
            out_cand[cid].append(COMBINATION)
    return EVResult(
        ev=out_ev, ev_mean=out_mean, p_top_k=out_p, p_best=out_best, recommended=out_rec,
        option_value=out_val,
        candidates=out_cand, evidence=evidence, unobserved=unobserved, k=k,
        priors_version=priors["version"], priors_sha256=sha,
    )
