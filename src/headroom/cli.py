"""Headroom CLI:  headroom run --region spp-synth --out data/processed"""

from __future__ import annotations

import argparse

from headroom.export import deliver
from headroom.pipeline import run_pipeline
from headroom.provenance.reproduce import reproduce_from_manifest


def _run(args) -> int:
    result = run_pipeline(args.region, scoring_path=args.scoring)
    d = deliver(result, args.out)
    return _report(result, d)


def _reproduce(args) -> int:
    result = reproduce_from_manifest(args.manifest, data_dir=args.data_dir)
    d = deliver(result, args.out)
    print("Historical replay verified: inputs, runtime, and ranking match.")
    return _report(result, d)


def _report(result, d) -> int:
    m = result.model
    print(f"Region {result.region.name} ({result.region.rto})  mode={d.mode}")
    if d.mode != "shareable":
        print(
            "  ! shareable export refused by the publishable gate "
            f"({len(d.blocked)} scores non-publishable) — wrote INTERNAL artifact."
        )
    print(f"  {m.n_constraints} flowgates, {len(m.shortlist)} corridors, shortlist k={m.k}")
    print("  top corridors:")
    for i, c in enumerate(m.shortlist[:10], start=1):
        print(f"    {i:2d}. {c['physical_corridor']}  p_top_k={c['p_top_k']:.2f}  "
              f"composite={c['composite_expected']:.3f} "
              f"[{c['composite_lo']:.3f}, {c['composite_hi']:.3f}]  {c['recommended_get']}")
    for gap in result.quality_report.get("gaps", []):
        print(f"  gap: {gap if len(gap) <= 160 else gap[:157] + '...'}")
    if result.quality_report.get("gaps"):
        print("  (full notes in quality_report)")
    for key, path in d.paths.items():
        print(f"  {key}: {path}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="headroom", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the full pipeline and export the shortlist")
    r.add_argument("--region", default="spp-synth")
    r.add_argument("--out", default="data/processed")
    r.add_argument("--scoring", default=None, help="path to scoring.yaml override")
    r.set_defaults(func=_run)

    replay = sub.add_parser("reproduce", help="verify and reproduce a recorded run")
    replay.add_argument("--manifest", required=True, help="path to the original run manifest")
    replay.add_argument("--out", required=True, help="directory for verified replay artifacts")
    replay.add_argument("--data-dir", help="relocated input directory (must match recorded pins)")
    replay.set_defaults(func=_reproduce)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        p.exit(1, f"headroom: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
