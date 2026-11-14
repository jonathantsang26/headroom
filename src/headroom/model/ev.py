"""7.4 Expected value of a GET deployment, per flowgate.

For flowgate f and GET g:

    EV_g = applicable_g(f) x u_g x h x R_f - C_g

- R_f: annual congestion rent on the flowgate ($M/yr). With a forward-rent calibration
  (model/forward.py) it is next year's rent: observed rent x months-binding share x a
  backtest ratio; otherwise the observed rent Range. Rent = sum over binding hours of
  shadow price x flow, and a binding flowgate flows at its limit, so the implied limit
  is R_f / sum|SP|. Relieving that limit by a share u is worth (sum|SP|) x u x
  (R_f / sum|SP|) = u x R_f to first order: ratings and line lengths, which are not
  public, drop out.
- u_g: relief share the GET delivers during binding hours (a prior), times an optional
  per-class relief scale (e.g. one line's DLR lifts a multi-element interface limit only
  partly), times an optional per-flowgate season factor: the prior's factor for each
  meteorological season, weighted by the flowgate's observed 2025 congestion $ in that
  season (seasons the prior leaves out count 1). DLR uses it: its gain over ambient-
  adjusted ratings is smallest in hot, calm summers and largest in winter, so a line
  whose congestion falls in summer gets less relief from DLR than one binding in
  winter. Where the $ by season are not recorded the factor is unobserved and stays 1,
  the prior's SPP-wide reference, never 0.
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

The decision valued is the one a planner can make: study which GETs apply to the
element, then deploy the applicable GET with the highest expected net value
E[EV_g | g applies] = E[u_g] E[h] E[R_f] - E[C_g] (or nothing, if none is positive),
before the realized relief, haircut, cost and next year's rent are known. Per Monte
Carlo draw, applicability is drawn per element; u, h, C and the season factors are drawn
and shared across flowgates (uncertainty about the technology, not the element); rent is
drawn per flowgate. A draw's realized value is the chosen GET's net value in that draw
(negative when the deployment loses money) or 0. ev_mean, which ranks, is the expected
value of the decision: the mean over applicability draws of the chosen GET's expected
net value.
The EV Range is the p5/p50/p95 of the realized value. p_top_k (the share of draws in the
top k by realized value with a positive realized value) is reported for stability and
does not order the list. The recommendation (exported as ev_recommended_get; it never
replaces the evidence rule's recommended_get) is the GET contributing most to ev_mean
(none when no GET is ever worth deploying); how often each option is chosen and each
GET's expected net value where it applies are reported beside it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
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
SEASONS = ("djf", "mam", "jja", "son")  # CongestionObs.season_rent_musd keys
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
        scale = spec.get("relief_scale", {})
        if not isinstance(scale, dict) or not set(scale) <= set(EVIDENCE_CLASSES) or any(
                not isinstance(v, (int, float)) or not 0 <= v <= 1 for v in scale.values()):
            raise ValueError(f"GET prior {g}.relief_scale maps evidence classes to [0, 1]")
        ulo, _, uhi = _triangle(spec.get("relief_share", {}), f"{g}.relief_share")
        if ulo < 0 or uhi > 1:
            raise ValueError(f"GET prior {g}.relief_share must lie in [0, 1]")
        season = spec.get("season_factor", {})
        if not isinstance(season, dict) or not set(season) <= set(SEASONS):
            raise ValueError(f"GET prior {g}.season_factor maps seasons {SEASONS} to triangles")
        for s, tri in season.items():
            if _triangle(tri, f"{g}.season_factor.{s}")[0] < 0:
                raise ValueError(f"GET prior {g}.season_factor.{s} must be >= 0")
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
    option_value: dict[str, dict[str, float]]  # cid -> {GET: its share of the decision value}
    expected_if_applies: dict[str, dict[str, float]]  # cid -> {GET: E[net value | applies]}
    candidates: dict[str, list[str]]  # GETs with nonzero applicability
    evidence: dict[str, str]
    unobserved: list[str]  # no observed rent: EV not computed
    k: int
    priors_version: str
    priors_sha256: str
    # cid -> {GET: season factor Range} where the GET has one, can apply to the class and
    # the $ by season are observed; otherwise the GET's relief used 1
    season_factor: dict[str, dict[str, Range]] = field(default_factory=dict)


def _season_shares(co) -> np.ndarray | None:
    """Shares of a flowgate's observed congestion $ by season (SEASONS order), or None
    when the $ by season are not recorded or sum to 0."""
    by = co.season_rent_musd
    if not by:
        return None
    total = sum(by[s] for s in SEASONS)
    return np.array([by[s] / total for s in SEASONS]) if total > 0 else None


def _season_range(g: str, cid: str, shares: np.ndarray, spec: dict, fact_ids: list[str],
                  store: LineageStore) -> Range:
    """One flowgate's season factor for GET g: 1 + sum over seasons of share x (factor - 1),
    at the priors' lo, mean and hi (the mean enters the expected net value)."""
    point = lambda pick: 1.0 + sum(
        sh * (pick(_triangle(spec[s], s)) - 1.0) for s, sh in zip(SEASONS, shares) if s in spec)
    mix = ", ".join(f"{s.upper()} {sh:.0%}" for s, sh in zip(SEASONS, shares))
    slug = "".join(w[0] for w in g.split()).lower()
    return store.add(derived_range(
        lo=point(lambda t: t[0]), expected=point(lambda t: sum(t) / 3.0),
        hi=point(lambda t: t[2]),
        basis=(f"{g} relief factor from the season of the flowgate's observed 2025 "
               f"congestion $ ({mix}), weighting the priors' season factors "
               f"({', '.join(f'{s.upper()} {spec[s]['mode']:g}' for s in SEASONS if s in spec)}"
               f" at the mode; other seasons 1): lo / mean / hi of the factors. The factors "
               f"are cited assumptions, not observations."),
        stage=STAGE, lineage_id=f"season_factor:{slug}:{cid}", inputs=fact_ids,
    ))


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

    tri_mean = lambda spec, name: sum(_triangle(spec, name)) / 3.0
    # relief scale by evidence class (1 unless the priors shrink relief for a class)
    scale = {g: np.array([float((gets[g].get("relief_scale") or {}).get(evidence[c.constraint_id], 1.0))
                          for c, _ in scored]) for g in SINGLE_GETS}
    # season factor (GETs whose priors carry one): each flowgate weights the per-season
    # factors by the shares of its observed $ by season. A flowgate whose $ by season are
    # unobserved gets zero shares, so its factor is 1 in every draw.
    season_spec = {g: gets[g].get("season_factor") or {} for g in SINGLE_GETS}
    shares = {c.constraint_id: _season_shares(co) for c, co in scored}
    share_m = np.array([shares[c.constraint_id] if shares[c.constraint_id] is not None
                        else np.zeros(len(SEASONS)) for c, _ in scored]).reshape(n, len(SEASONS))
    # (a GET that cannot apply to the class, such as DLR on a transformer, gets none)
    seasonal = lambda cid: [g for g in SINGLE_GETS
                            if season_spec[g] and gets[g]["applicability"][evidence[cid]] > 0]
    season_ranges = {c.constraint_id: {g: _season_range(g, c.constraint_id,
                                                        shares[c.constraint_id], season_spec[g],
                                                        co.season_fact_ids, store)
                                       for g in seasonal(c.constraint_id)}
                     for c, co in scored if shares[c.constraint_id] is not None}
    season_ranges = {cid: r for cid, r in season_ranges.items() if r}
    k_mean = {g: np.array([tri_mean(season_spec[g][s], s) if s in season_spec[g] else 1.0
                           for s in SEASONS]) for g in SINGLE_GETS if season_spec[g]}
    best_all = np.empty((n, draws)) if n else np.empty((0, draws))
    decision_sum = np.zeros(n)
    top_hits = np.zeros(n)
    best_counts = np.zeros((n, len(options)))
    option_sums = np.zeros((n, len(options)))  # sum over draws of the chosen option's mu
    mu: dict[str, np.ndarray] = {}
    if n:
        lo = np.array([co.rent_musd.lo for _, co in scored])
        mode = np.array([co.rent_musd.expected for _, co in scored])
        hi = np.array([co.rent_musd.hi for _, co in scored])
        if forward is not None:
            eps = ratio_sampler(forward)
            point = mode * np.array([forward_share(forward["predictor"], co.months_binding)
                                     for _, co in scored])
            rent_mean = point * ratio_mean(forward)
        else:
            rent_mean = (lo + mode + hi) / 3.0
        # Expected net value of each GET where it applies: what a planner can know before
        # deploying (u, h, C and next year's rent are independent of applicability).
        h_mean = (hlo + hmode + hhi) / 3.0
        u_mean = {g: tri_mean(gets[g]["relief_share"], g) * scale[g] for g in SINGLE_GETS}
        for g in k_mean:  # E[u x factor] = E[u] E[factor]: drawn independently
            u_mean[g] = u_mean[g] * (1.0 + share_m @ (k_mean[g] - 1.0))
        c_mean = {g: tri_mean(gets[g]["annual_cost_musd"], g) for g in SINGLE_GETS}
        for g in SINGLE_GETS:
            mu[g] = u_mean[g] * h_mean * rent_mean - c_mean[g]
        dlr, apfc = SINGLE_GETS[0], SINGLE_GETS[1]
        mu[COMBINATION] = ((1.0 - (1.0 - u_mean[dlr]) * (1.0 - u_mean[apfc])) * h_mean * rent_mean
                           - c_mean[dlr] - c_mean[apfc])
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
            relief = {}  # relief share per flowgate and draw
            for g in SINGLE_GETS:
                u[g] = _tri(rng, *_triangle(gets[g]["relief_share"], g), (d,))
                cost[g] = _tri(rng, *_triangle(gets[g]["annual_cost_musd"], g), (d,))
                factor = 1.0
                if season_spec[g]:  # drawn only when the priors carry one
                    by_season = np.ones((len(SEASONS), d))
                    for i, s in enumerate(SEASONS):
                        if s in season_spec[g]:
                            by_season[i] = _tri(rng, *_triangle(season_spec[g][s], s), (d,))
                    factor = 1.0 + share_m @ (by_season - 1.0)
                relief[g] = scale[g][:, None] * factor * u[g]
                draw = rng.random((len(elements), d))  # one per element and GET
                app[g] = draw[element_of] < p_app[g][:, None]
                ev[g] = relief[g] * h * rent - cost[g]
            u_c = 1.0 - (1.0 - relief[dlr]) * (1.0 - relief[apfc])
            ev[COMBINATION] = u_c * h * rent - (cost[dlr] + cost[apfc])
            app[COMBINATION] = app[dlr] & app[apfc]
            # The decision: learn which GETs apply, then deploy the one with the highest
            # expected net value (or none). Realized u, h, C and rent are not known yet.
            planned = np.stack([np.zeros((n, d)), *(np.where(app[g], mu[g][:, None], -np.inf)
                                                     for g in options[1:])])
            choice = planned.argmax(axis=0)  # ties go to the earlier option: none first
            decided = planned.max(axis=0)  # expected value of the decision (>= 0)
            realized = np.zeros((n, d))
            for j, g in enumerate(options[1:], start=1):
                realized = np.where(choice == j, ev[g], realized)
            best_all[:, start:start + d] = realized
            decision_sum += decided.sum(axis=1)
            for j in range(len(options)):
                best_counts[:, j] += (choice == j).sum(axis=1)
                option_sums[:, j] += np.where(choice == j, decided, 0.0).sum(axis=1)
            idx = np.argpartition(realized, n - k, axis=0)[n - k:, :]
            # a draw where nothing is deployed or the deployment loses money is never a
            # top-k hit: zero-value flowgates must not enter the top k by tie-breaking
            hit = np.take_along_axis(realized, idx, axis=0) > 0
            np.add.at(top_hits, idx[hit], 1)

    rent_term = (f"forward rent ({forward['predictor']} x a backtest ratio draw, ratio mean "
                 f"{ratio_mean(forward):.3g})" if forward is not None
                 else "observed rent (forward band)")
    out_ev, out_mean, out_p, out_best, out_rec, out_cand = {}, {}, {}, {}, {}, {}
    out_val, out_mu = {}, {}
    for i, (c, co) in enumerate(scored):
        cid = c.constraint_id
        p5, p50, p95 = (float(x) for x in np.percentile(best_all[i], [5, 50, 95]))
        out_mean[cid] = float(decision_sum[i]) / draws
        out_mu[cid] = {g: float(mu[g][i]) for g in options[1:]}
        factors = season_ranges.get(cid, {})
        season_note = "".join(
            f"; {g} relief x season factor {factors[g].expected:.3g} (mean; {factors[g].lineage_id})"
            if g in factors else f"; {g} season factor unobserved (no congestion $ by season): 1"
            for g in seasonal(cid))
        out_ev[cid] = store.add(derived_range(
            lo=p5, expected=p50, hi=p95,
            basis=(
                f"Net value ($M/yr) of the decision: learn which GETs apply to the element, "
                f"deploy the one with the highest expected net value (relief share x haircut "
                f"x {rent_term} - annualized cost), or none if no applicable GET has a "
                f"positive one. Mean {out_mean[cid]:.4g} (ranks); p5/p50/p95 over {draws} "
                f"draws of the realized value, negative when a deployment loses money; "
                f"evidence class {evidence[cid]}{season_note}; priors {priors['version']} "
                f"(sha256 {sha[:12]}) are cited assumptions, not observations."
            ),
            stage=STAGE, lineage_id=f"ev:{cid}",
            inputs=[co.rent_musd.lineage_id, f"obs:constraints:{cid}", *co.fact_ids,
                    *(r.lineage_id for r in factors.values())],
        ))
        out_p[cid] = float(top_hits[i]) / draws
        out_best[cid] = {opt: float(best_counts[i, j]) / draws for j, opt in enumerate(options)}
        # the GET worth pursuing: the one contributing most to the decision's value, none
        # when no GET is ever worth deploying
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
        option_value=out_val, expected_if_applies=out_mu,
        candidates=out_cand, evidence=evidence, unobserved=unobserved, k=k,
        priors_version=priors["version"], priors_sha256=sha, season_factor=season_ranges,
    )
