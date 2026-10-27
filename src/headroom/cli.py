"""Headroom CLI:  headroom run --region spp-synth --out data/processed"""

from __future__ import annotations

import argparse

import json
from pathlib import Path

from headroom.export import compare_replayed_outputs, deliver
from headroom.model.get_match import get_label
from headroom.pipeline import run_pipeline
from headroom.provenance.reproduce import reproduce_from_manifest

# Exit status 2: a live (non-synthetic) run whose export the publishable gate refused.
GATE_REFUSED = 2


def _run(args) -> int:
    result = run_pipeline(args.region, scoring_path=args.scoring)
    d = deliver(result, args.out, overwrite=args.overwrite)
    return _report(result, d)


def _reproduce(args) -> int:
    if Path(args.out).resolve() == Path(args.manifest).resolve().parent:
        raise ValueError("replay into the directory of the run it verifies would replace "
                         "that run's files before they are compared; choose another --out")
    recorded = json.loads(Path(args.manifest).read_text())
    result = reproduce_from_manifest(recorded, data_dir=args.data_dir)
    d = deliver(result, args.out, overwrite=args.overwrite)
    compared = compare_replayed_outputs(recorded, d.manifest)
    print("Historical replay verified: inputs, runtime, ranking and export decision match"
          + (f"; identical artifacts: {', '.join(compared)}." if compared else
             "; the recorded manifest has no artifact digests to compare."))
    return _report(result, d)


def _report(result, d) -> int:
    m = result.model
    print(f"Region {result.region.name} ({result.region.rto})  mode={d.mode}")
    if d.mode != "shareable":
        print(
            "  ! shareable export refused by the publishable gate "
            f"({len(d.blocked)} scores, {len(d.blocked_tables)} input tables "
            "non-publishable) — wrote INTERNAL artifact."
        )
    print(f"  {m.n_constraints} flowgates, {len(m.shortlist)} corridors, shortlist k={m.k}")
    ev_ranked = m.config.ranking == "ev"
    if ev_ranked and "ev" in m.extras:
        print(f"  {m.extras['ev'].get('n_positive')} flowgates have a GET worth deploying")
    print("  top corridors" + (" (by the expected net value of the GET decision, $M/yr):"
                               if ev_ranked else ":"))
    for i, c in enumerate(m.shortlist[:10], start=1):
        if ev_ranked and c.get("ev_mean") is not None:
            value = (f"EV={c['ev_mean']:.2f} p50 {c['ev_expected']:.2f} "
                     f"[{c['ev_lo']:.2f}, {c['ev_hi']:.2f}]")
        else:
            value = (f"composite={c['composite_expected']:.3f} "
                     f"[{c['composite_lo']:.3f}, {c['composite_hi']:.3f}]")
        # the evidence's set where it leaves several GETs open, not the tie-break
        shown = get_label(m.extras["get_set"][c["representative_flowgate"]])
        print(f"    {i:2d}. {c['physical_corridor']}  p_top_k={c['p_top_k']:.2f}  "
              f"{value}  {shown}")
    for gap in result.quality_report.get("gaps", []):
        print(f"  gap: {gap if len(gap) <= 160 else gap[:157] + '...'}")
    if result.quality_report.get("gaps"):
        print("  (full notes in quality_report)")
    for key, path in d.paths.items():
        print(f"  {key}: {path}")
    if not result.region.synthetic and d.mode != "shareable":
        return GATE_REFUSED  # a live run that cannot be shared is a failure to automation
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="headroom", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the full pipeline and export the shortlist "
                                   "(exit 2 if a live run's export is not shareable)")
    r.add_argument("--region", default="spp-synth")
    r.add_argument("--out", default="data/processed")
    r.add_argument("--scoring", default=None, help="path to scoring.yaml override")
    r.add_argument("--overwrite", action="store_true",
                   help="replace a different run already in --out")
    r.set_defaults(func=_run)

    replay = sub.add_parser("reproduce", help="verify and reproduce a recorded run")
    replay.add_argument("--manifest", required=True, help="path to the original run manifest")
    replay.add_argument("--out", required=True, help="directory for verified replay artifacts")
    replay.add_argument("--data-dir", help="relocated input directory (must match recorded pins)")
    replay.add_argument("--overwrite", action="store_true",
                        help="replace a different run already in --out")
    replay.set_defaults(func=_reproduce)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        p.exit(1, f"headroom: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
