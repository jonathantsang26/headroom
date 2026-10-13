"""Phase 3 driver: signals -> which-GET -> composite -> rank stability, then
footprint-dedup flowgate scores up to the physical corridor for the shortlist
(Decision 4). Done when each constraint carries p_top_k, a recommended GET, a
composite range, and a one-line provenance trail."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from headroom.model.ev import load_priors, run_ev, validate_priors
from headroom.model.forward import validate_forward
from headroom.model.get_match import get_basis, recommend_get
from headroom.model.score import composite_from_percentiles
from headroom.model.signals import (
    _adjacency,
    _bus_degree,
    build_signals,
    is_radial,
)
from headroom.model.stability import rank_stability
from headroom.provenance.lineage import LineageStore
from headroom.quality.bucket_a import BucketAResult
from headroom.reconstruct.bucket_b import BucketBResult
from headroom.schema.entities import Score

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_SCORING = _REPO_ROOT / "config" / "scoring.yaml"
RANKINGS = ("composite", "ev")
DEFINITIONS = {"cost_of_inaction": ("rent_x_persistence", "recurring_rent")}


@dataclass
class ScoringConfig:
    weights: dict[str, float]
    references: dict[str, float]
    draws: int
    top_fraction: float
    seed: int
    # "composite": rank by the weighted signal composite (default, fixtures).
    # "ev": rank by the expected net value of each flowgate's best GET.
    ranking: str = "composite"
    get_priors: dict | None = None  # full priors content (model/ev.py), not a path
    reference_rule: dict | None = None  # how the references were derived (recorded)
    definitions: dict[str, str] = field(default_factory=dict)  # signal variants
    forward_rent: dict | None = None  # EV forward-rent calibration (model/forward.py)

    def __post_init__(self) -> None:
        for name, value in self.definitions.items():
            if value not in DEFINITIONS.get(name, ()):
                raise ValueError(f"unknown signal definition {name}: {value!r}")
        if self.ranking not in RANKINGS:
            raise ValueError(f"scoring ranking must be one of {RANKINGS}")
        if self.ranking == "ev" and not self.get_priors:
            raise ValueError("ranking: ev needs get_priors")
        if self.get_priors is not None:
            validate_priors(self.get_priors)
        if self.forward_rent is not None:
            validate_forward(self.forward_rent)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "ScoringConfig":
        raw = yaml.safe_load(Path(path or _DEFAULT_SCORING).read_text())
        w = raw["weights"]
        total = sum(w.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"scoring weights must sum to 1.0, got {total}")
        s = raw["stability"]
        priors = raw.get("get_priors")
        return cls(
            weights=w,
            references=raw["references"],
            draws=int(s["draws"]),
            top_fraction=float(s["top_fraction"]),
            seed=int(s["seed"]),
            ranking=raw.get("ranking", "composite"),
            get_priors=load_priors(_REPO_ROOT / priors) if priors else None,
            reference_rule=raw.get("reference_rule"),
            definitions=dict(raw.get("definitions") or {}),
            forward_rent=raw.get("forward_rent"),
        )

    @classmethod
    def from_manifest(cls, scoring: dict) -> "ScoringConfig":
        """Rebuild the exact scoring config a manifest recorded, so a pinned run can
        be reproduced identically (Phase-5 done-when)."""
        return cls(
            weights=scoring["weights"],
            references=scoring["references"],
            draws=int(scoring["draws"]),
            top_fraction=float(scoring["top_fraction"]),
            seed=int(scoring["seed"]),
            ranking=scoring.get("ranking", "composite"),
            get_priors=scoring.get("get_priors"),
            reference_rule=scoring.get("reference_rule"),
            definitions=dict(scoring.get("definitions") or {}),
            forward_rent=scoring.get("forward_rent"),
        )


@dataclass
class ModelResult:
    scores: list[Score]  # per flowgate, ranked
    shortlist: list[dict]  # footprint-deduped physical corridors
    k: int
    config: ScoringConfig
    n_constraints: int = 0
    extras: dict = field(default_factory=dict)  # radial flags, get rationale, etc.


def _provenance(composite, bb: BucketBResult, cid: str, store: LineageStore,
                signals: dict | None = None, coverage: dict | None = None) -> str:
    co = bb.congestion.get(cid)
    bits = [f"rent={co.rent_musd.provider}" if co is not None and co.rent_musd is not None
            else ("rent=unobserved" if co is not None else "rent=none"),
            f"loading={bb.screen_status.get(cid, 'ok')}"]
    if cid in bb.planning:
        bits.append(f"planning={bb.planning[cid].plan}")
    # the registered root sources this score's lineage reaches: through observed
    # signals ("data"), and only through unobserved ones ("absent": a table that was
    # consulted and gave nothing for this flowgate)
    if signals is not None and coverage is not None:
        seen = set().union(*(store.source_closure(signals[n].lineage_id) for n in signals
                             if coverage.get(n, "observed") == "observed"))
        absent = set().union(*(store.source_closure(signals[n].lineage_id) for n in signals
                               if coverage.get(n) == "unobserved")) - seen
    else:
        seen, absent = store.source_closure(composite.lineage_id), set()
    bits.append("data=" + ",".join(sorted(seen)))
    if absent:
        bits.append("absent=" + ",".join(sorted(absent)))
    return "; ".join(bits)


def run_model(
    bucket_a: BucketAResult,
    bucket_b: BucketBResult,
    store: LineageStore,
    config: ScoringConfig | None = None,
) -> ModelResult:
    cfg = config or ScoringConfig.load()
    lines_by_id = bucket_a.lines_by_id
    degree = _bus_degree(bucket_a.lines)
    adjacency = _adjacency(bucket_a.lines)

    signals_by_cid: dict[str, dict] = {}
    coverage_by_cid: dict[str, dict] = {}
    radial_by_cid: dict[str, bool | None] = {}  # None: not on public topology
    for c in bucket_b.constraints:
        line = lines_by_id.get(c.monitored_line) if c.monitored_line else None
        signals_by_cid[c.constraint_id], coverage_by_cid[c.constraint_id] = build_signals(
            c, line, bucket_b, cfg.references, adjacency, store, cfg.definitions
        )
        radial_by_cid[c.constraint_id] = is_radial(line, degree) if line else None

    p_top, pcts, k = rank_stability(
        signals_by_cid, cfg.weights,
        draws=cfg.draws, top_fraction=cfg.top_fraction, seed=cfg.seed,
    )

    scores: list[Score] = []
    for c in bucket_b.constraints:
        cid = c.constraint_id
        signals = signals_by_cid[cid]
        comp = composite_from_percentiles(cid, pcts[cid], signals, store)
        radial = radial_by_cid[cid]
        loading_observed = coverage_by_cid[cid]["loading"] == "observed"
        co = bucket_b.congestion.get(cid)
        breached = co.breached_hours if co is not None else None
        get = recommend_get(c, radial=bool(radial), signals=signals,
                            loading_observed=loading_observed, breached_hours=breached)
        scores.append(
            Score(
                constraint_id=cid,
                physical_corridor=c.corridor,
                signals=signals,
                composite=comp,
                p_top_k=p_top[cid],
                recommended_get=get,
                provenance=_provenance(comp, bucket_b, cid, store, signals,
                                       coverage_by_cid[cid]),
                signal_coverage=coverage_by_cid[cid],
                get_basis=get_basis(c, radial=radial, loading_observed=loading_observed,
                                    breached_hours=breached, get=get),
            )
        )

    extras = {"radial": radial_by_cid}
    if cfg.get_priors:
        k = _apply_ev(scores, bucket_b, radial_by_cid, cfg, store, extras, k)

    scores.sort(key=lambda s: _rank_key(s, cfg.ranking), reverse=True)

    shortlist = _dedupe_to_corridors(scores, cfg.ranking)
    return ModelResult(
        scores=scores,
        shortlist=shortlist,
        k=k,
        config=cfg,
        n_constraints=len(scores),
        extras=extras,
    )


def _rank_key(s: Score, ranking: str) -> tuple:
    """Composite: stability first (p_top_k), then the composite p50. EV: the mean
    best-GET net value first. Under EV, p_top_k of the leaders converges to
    P(some GET applies to the element), one value per evidence class, so ordering by
    it first would sort them by sampling noise (model/ev.py)."""
    if ranking == "ev":
        mean = s.ev_mean if s.ev_mean is not None else float("-inf")
        return (mean, s.p_top_k)
    return (s.p_top_k, s.composite.expected)


def _apply_ev(scores: list[Score], bb: BucketBResult, radial_by_cid: dict, cfg: ScoringConfig,
              store: LineageStore, extras: dict, k: int) -> int:
    """Attach the EV layer; when the region ranks by EV, it also sets p_top_k and
    the recommended GET (the evidence rule's choice stays in get_rule)."""
    res = run_ev(bb.constraints, bb, radial_by_cid, cfg.get_priors, store,
                 draws=cfg.draws, top_fraction=cfg.top_fraction, seed=cfg.seed,
                 forward=cfg.forward_rent)
    for s in scores:
        cid = s.constraint_id
        s.get_rule = s.recommended_get
        s.ev = res.ev.get(cid)
        s.ev_mean = res.ev_mean.get(cid)
        s.get_probabilities = res.p_best.get(cid, {})
        s.get_candidates = res.candidates.get(cid, [])
        s.get_evidence = res.evidence.get(cid, "")
        if cfg.ranking != "ev":
            continue
        s.p_top_k_composite = s.p_top_k
        s.p_top_k = res.p_top_k.get(cid, 0.0)
        if cid in res.recommended:
            best = res.recommended[cid]
            s.recommended_get = best
            s.get_basis += (f"; GET = the option with the highest net value in "
                            f"{s.get_probabilities[best]:.0%} of draws (priors "
                            f"{res.priors_version}, evidence {s.get_evidence}); evidence "
                            f"rule: {s.get_rule}")
        else:
            s.get_basis += "; EV unobserved (no observed rent): GET from the evidence rule"
    extras["ev"] = {
        "ranking": cfg.ranking, "k": res.k, "unobserved": res.unobserved,
        "priors_version": res.priors_version, "priors_sha256": res.priors_sha256,
        "forward_rent": cfg.forward_rent,
    }
    return res.k if cfg.ranking == "ev" else k


def _dedupe_to_corridors(scores: list[Score], ranking: str = "composite") -> list[dict]:
    """Footprint-dedup: many flowgates can sit on one physical line; the deployment
    unit is the corridor. Each corridor row reports BOTH roll-ups (Decision 4):"""
    by_corridor: dict[str, list[Score]] = {}
    for s in scores:
        by_corridor.setdefault(s.physical_corridor, []).append(s)

    rows: list[dict] = []
    for corridor, members in by_corridor.items():
        best = max(members, key=lambda s: _rank_key(s, ranking))
        rows.append({
            "physical_corridor": corridor,
            "representative_flowgate": best.constraint_id,
            "n_flowgates": len(members),
            "flowgates": [m.constraint_id for m in members],
            # max view (champion) — primary ranking, unchanged semantics
            "p_top_k": best.p_top_k,
            "composite_lo": best.composite.lo,
            "composite_expected": best.composite.expected,
            "composite_hi": best.composite.hi,
            # sum view (aggregate) — exact stat first, bounded band second
            "expected_topk_slots": sum(m.p_top_k for m in members),
            "sum_composite_lo": sum(m.composite.lo for m in members),
            "sum_composite_expected": sum(m.composite.expected for m in members),
            "sum_composite_hi": sum(m.composite.hi for m in members),
            "recommended_get": best.recommended_get,
            "provenance": best.provenance,
        })
        if best.ev is not None or any(m.ev is not None for m in members):
            evs = [m.ev for m in members if m.ev is not None]
            rows[-1].update({
                "ev_mean": best.ev_mean,
                "ev_lo": best.ev.lo if best.ev else None,
                "ev_expected": best.ev.expected if best.ev else None,
                "ev_hi": best.ev.hi if best.ev else None,
                # sum over flowgates double-counts a shared deployment: an upper bound
                "sum_ev_mean": sum(m.ev_mean for m in members if m.ev_mean is not None),
                "sum_ev_expected": sum(e.expected for e in evs),
                "get_probabilities": best.get_probabilities,
            })
    if ranking == "ev":
        rows.sort(key=lambda r: (r["ev_mean"] if r.get("ev_mean") is not None
                                 else float("-inf"), r["p_top_k"]), reverse=True)
    else:
        rows.sort(key=lambda r: (r["p_top_k"], r["composite_expected"]), reverse=True)
    return rows
