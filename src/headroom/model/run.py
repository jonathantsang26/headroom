"""Phase 3 driver: signals -> which-GET -> composite -> rank stability, then
footprint-dedup flowgate scores up to the physical corridor for the shortlist
(Decision 4). Done when each constraint carries p_top_k, a recommended GET, a
composite range, and a one-line provenance trail."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from headroom.model.get_match import recommend_get
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

_DEFAULT_SCORING = Path(__file__).resolve().parents[3] / "config" / "scoring.yaml"


@dataclass
class ScoringConfig:
    weights: dict[str, float]
    references: dict[str, float]
    draws: int
    top_fraction: float
    seed: int

    @classmethod
    def load(cls, path: str | Path | None = None) -> "ScoringConfig":
        raw = yaml.safe_load(Path(path or _DEFAULT_SCORING).read_text())
        w = raw["weights"]
        total = sum(w.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"scoring weights must sum to 1.0, got {total}")
        s = raw["stability"]
        return cls(
            weights=w,
            references=raw["references"],
            draws=int(s["draws"]),
            top_fraction=float(s["top_fraction"]),
            seed=int(s["seed"]),
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
        )


@dataclass
class ModelResult:
    scores: list[Score]  # per flowgate, ranked
    shortlist: list[dict]  # footprint-deduped physical corridors
    k: int
    config: ScoringConfig
    n_constraints: int = 0
    extras: dict = field(default_factory=dict)  # radial flags, get rationale, etc.


def _provenance(signals: dict, bb: BucketBResult, cid: str) -> str:
    bits = [f"rent={bb.congestion[cid].rent_musd.provider}" if cid in bb.congestion else "rent=none",
            f"loading={bb.screen_status.get(cid, 'ok')}"]
    if cid in bb.planning:
        bits.append(f"planning={bb.planning[cid].plan}")
    sources = sorted({s.source for s in signals.values()})
    bits.append("data=" + ",".join(sources))
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
    radial_by_cid: dict[str, bool] = {}
    for c in bucket_b.constraints:
        line = lines_by_id[c.monitored_line]
        signals_by_cid[c.constraint_id] = build_signals(
            c, line, bucket_b, cfg.references, adjacency, store
        )
        radial_by_cid[c.constraint_id] = is_radial(line, degree)

    p_top, pcts, k = rank_stability(
        signals_by_cid, cfg.weights,
        draws=cfg.draws, top_fraction=cfg.top_fraction, seed=cfg.seed,
    )

    scores: list[Score] = []
    for c in bucket_b.constraints:
        cid = c.constraint_id
        signals = signals_by_cid[cid]
        comp = composite_from_percentiles(cid, pcts[cid], signals, store)
        get = recommend_get(c, radial=radial_by_cid[cid], signals=signals)
        scores.append(
            Score(
                constraint_id=cid,
                physical_corridor=c.monitored_line,
                signals=signals,
                composite=comp,
                p_top_k=p_top[cid],
                recommended_get=get,
                provenance=_provenance(signals, bucket_b, cid),
            )
        )

    # rank flowgates: stability first, then composite expected
    scores.sort(key=lambda s: (s.p_top_k, s.composite.expected), reverse=True)

    shortlist = _dedupe_to_corridors(scores)
    return ModelResult(
        scores=scores,
        shortlist=shortlist,
        k=k,
        config=cfg,
        n_constraints=len(scores),
        extras={"radial": radial_by_cid},
    )


def _dedupe_to_corridors(scores: list[Score]) -> list[dict]:
    """Footprint-dedup: many flowgates can sit on one physical line; the deployment
    unit is the corridor. Each corridor row reports BOTH roll-ups (Decision 4):"""
    by_corridor: dict[str, list[Score]] = {}
    for s in scores:
        by_corridor.setdefault(s.physical_corridor, []).append(s)

    rows: list[dict] = []
    for corridor, members in by_corridor.items():
        best = max(members, key=lambda s: (s.p_top_k, s.composite.expected))
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
    rows.sort(key=lambda r: (r["p_top_k"], r["composite_expected"]), reverse=True)
    return rows
