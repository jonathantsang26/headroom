"""Headroom CLI:  headroom run --region spp-synth --out data/processed"""

from __future__ import annotations

import argparse

from headroom.export import deliver
from headroom.pipeline import run_pipeline


def _run(args) -> int:
    result = run_pipeline(args.region, scoring_path=args.scoring)
    d = deliver(result, args.out)
    m = result.model
    print(f"Region {result.region.name} ({result.region.rto})  mode={d.mode}")
    if d.mode != "shareable":
        print(
            "  ! shareable export refused by the publishable gate "
            f"({len(d.blocked)} scores non-publishable) — wrote INTERNAL artifact."
        )
    print(f"  {m.n_constraints} flowgates, shortlist k={m.k}")
    print(f"  top corridor: {m.shortlist[0]['physical_corridor']} "
          f"(p_top_k={m.shortlist[0]['p_top_k']:.2f}, "
          f"{m.shortlist[0]['recommended_get']})")
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

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
