"""The run-level quality report: what each table covers, what the DC screen could
see, which signals were unobserved for which flowgates, and which scores that
affects. Every shareable export carries it."""

from __future__ import annotations

from collections import Counter

from headroom.ingest.load import IngestBundle
from headroom.ingest.snapshot import REQUIRED_TABLES
from headroom.model.signals import SIGNAL_NAMES


def build_quality_report(bundle: IngestBundle, bucket_a, bucket_b, model) -> dict:
    stamps = bundle.table_stamps
    tables = {}
    for t in REQUIRED_TABLES:
        cov = bundle.table_coverage.get(t, {})
        entry = {"coverage": cov.get("coverage", "observed"), "note": cov.get("note", "")}
        if t in stamps:
            entry["source"] = stamps[t].source
            entry["source_version"] = stamps[t].source_version
        tables[t] = entry

    n = len(model.scores)
    weights = model.config.weights
    per_signal = {name: Counter(s.signal_coverage.get(name, "observed") for s in model.scores)
                  for name in SIGNAL_NAMES}
    affected = []
    unobserved_weight = 0.0
    for name in SIGNAL_NAMES:
        miss = per_signal[name].get("unobserved", 0)
        if not miss:
            continue
        w = weights.get(name, 0.0)
        if miss == n:
            unobserved_weight += w
            effect = (f"unobserved for all {n} flowgates: contributes 0 to every "
                      "composite and cannot change the order")
        else:
            effect = (f"unobserved for {miss} of {n} flowgates: those are compared "
                      "without it, so they are biased low on this signal")
        affected.append({"signal": name, "weight": w, "unobserved": miss, "effect": effect})

    status = Counter(bucket_b.screen_status.values())
    radial_unknown = sum(1 for s in model.scores if "radial=unknown" in s.get_basis)
    market_heavy = sum(1 for s in model.scores if "binding in DA market" in s.get_basis)
    gaps = [f"{t}: {e['coverage']} — {e['note']}" for t, e in tables.items()
            if e["coverage"] != "observed"]
    if affected:
        gaps.append(
            f"composite: {unobserved_weight:.2f} of the configured weight is unobserved "
            f"for every flowgate; the ranking rests on the remaining "
            f"{1 - unobserved_weight:.2f}."
        )
    if market_heavy:
        gaps.append(
            f"recommended_get: for {market_heavy} flowgates the 'at limit' test uses DA "
            f"binding instead of the DC screen; radial is unknown for {radial_unknown} "
            "(treated as meshed)."
        )

    return {
        "region": bundle.region.name,
        "synthetic": bundle.region.synthetic,
        "tables": tables,
        "bucket_a": bucket_a.quality_report,
        "dc_screen": {**bucket_b.screen, "status_counts": dict(status)},
        "n_flowgates": n,
        "n_corridors": len(model.shortlist),
        "shortlist_k": model.k,
        "constraint_coverage": dict(Counter(c.coverage for c in bucket_b.constraints)),
        "signal_coverage": {k: dict(v) for k, v in per_signal.items()},
        "scores_affected": affected,
        "unobserved_weight_all": round(unobserved_weight, 6),
        "gaps": gaps,
    }
