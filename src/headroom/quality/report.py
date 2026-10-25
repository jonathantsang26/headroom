"""The run-level quality report: what each table covers, what the DC screen could
see, which signals were unobserved for which flowgates, and which scores that
affects. Every shareable export carries it."""

from __future__ import annotations

from collections import Counter

from headroom.ingest.load import IngestBundle
from headroom.ingest.snapshot import REQUIRED_TABLES
from headroom.model.signals import SIGNAL_NAMES


def build_quality_report(bundle: IngestBundle, bucket_a, bucket_b, model,
                         registry=None) -> dict:
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
    market_heavy = sum(1 for s in model.scores if "heavy=DA breached" in s.get_basis
                       or "heavy=unobserved" in s.get_basis)
    gaps = [f"{t}: {e['coverage']} — {e['note']}" for t, e in tables.items()
            if e["coverage"] != "observed"]
    if affected:
        gaps.append(
            f"composite: {unobserved_weight:.2f} of the configured weight is unobserved "
            f"for every flowgate; the ranking rests on the remaining "
            f"{1 - unobserved_weight:.2f}."
        )
    ev = model.extras.get("ev")
    if market_heavy:
        rule = ("the evidence rule (recommended_get, repeated as evidence_rule_get) uses "
                "market facts only: breached and persistent -> Combination, transformer -> "
                "APFC, N-1 branch outage -> APFC, BASE or unknown contingency -> DLR")
        gaps.append(
            f"recommended_get: for {market_heavy} flowgates the DC screen could not see "
            f"the element, so {rule}. Each get_basis lists the GETs the evidence does "
            "not rule out."
            + (" ev_recommended_get, the EV layer's GET contributing most to the "
               "decision's expected value, is economics, not a physical assignment."
               if ev else "")
        )

    ev_section = None
    if ev:
        # the EV layer's choices only: flowgates without observed rent have none
        mix = Counter(s.ev_get for s in model.scores if s.ev is not None)
        ev_section = {
            "ranking": ev["ranking"],
            "priors_version": ev["priors_version"],
            "priors_sha256": ev["priors_sha256"],
            "k": ev["k"],
            "n_with_a_get_worth_deploying": ev.get("n_positive"),
            "n_unobserved_rent": len(ev["unobserved"]),
            "unobserved_rent": ev["unobserved"],
            "ev_recommended_get_mix": dict(mix),
            "forward_rent": ({k: ev["forward_rent"].get(k) for k in (
                "predictor", "ratio_quantiles", "ratio_mean", "derived_from_snapshot",
                "backtest")} if ev.get("forward_rent") else None),
            "note": ("The decision valued: find out which GETs apply to the element "
                     "(drawn per element from evidence-class probabilities), then deploy "
                     "the applicable GET with the highest expected net value (expected "
                     "relief share x haircut x rent - expected annualized cost), or none. "
                     "It is made before the realized relief, haircut, cost and next year's "
                     "rent are known. ev_mean, the expected value of that decision, ranks "
                     "under ranking: ev. ev lo/expected/hi are the p5/p50/p95 of its "
                     "realized value, which is negative when a deployment loses money. "
                     "p_top_k is the share of draws in the top k with a positive realized "
                     "value. ev_recommended_get is the GET contributing most to ev_mean; "
                     "recommended_get stays the evidence rule's physical assignment. Relief "
                     "share, haircut, cost, applicability and relief scale are cited "
                     "judgement priors (config), not observations; the order of the "
                     "shortlist rests on them."
                     + (" Rent in each draw is forward rent: the 2025 observed rent x the "
                        "share of months the flowgate bound in x a ratio drawn from a "
                        "within-2025 held-out-months backtest (forward_rent). No cross-year "
                        "test exists yet." if ev.get("forward_rent") else
                        " Rent in each draw is the 2025 observed rent with a -40%/+50% band, "
                        "which held-out months show is far too narrow.")),
        }
        if ev["unobserved"]:
            gaps.append(
                f"ev: {len(ev['unobserved'])} flowgates have no observed congestion $, so "
                "their EV is not computed; they are listed, not ranked."
            )

    report = {
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
        # the evidence rule's labels over every flowgate (the EV layer's are under ev)
        "recommended_get_mix": dict(Counter(s.recommended_get for s in model.scores)),
        "ev": ev_section,
        "gaps": gaps,
    }
    if not bundle.region.synthetic and registry is not None:
        # every source the run used, with its terms: a shareable export carries them.
        # A source behind only unobserved tables gave nothing; it is listed apart.
        used: dict[tuple, set] = {}
        consulted: dict[tuple, set] = {}
        for table, stamps_ in bundle.table_sources.items():
            unobserved = tables.get(table, {}).get("coverage") == "unobserved"
            for st in stamps_:
                (consulted if unobserved else used).setdefault(
                    (st.source, st.source_version), set()).add(table)
        entry = lambda src, ver, ts: {
            "source": src, "source_version": ver, "tables": sorted(ts),
            "basis": (registry.get(src).compliance.basis if registry.get(src) else "")}
        report["attribution"] = [entry(src, ver, ts) for (src, ver), ts in sorted(used.items())]
        report["consulted_nothing_obtained"] = [
            entry(src, ver, ts) for (src, ver), ts in sorted(consulted.items())
            if (src, ver) not in used]
    return report
