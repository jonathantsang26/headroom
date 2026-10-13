"""Derive a region's scoring references from a pinned snapshot by a stated rule.

Rule: each reference is the 99th percentile (numpy linear interpolation) of the raw
signal's expected value over the scored flowgates where that signal is observed,
rounded to 4 significant figures. Unobserved rows are excluded, never zeros.

    python scripts/derive_scoring_refs.py data/raw/spp/2026-10-13

Prints the references and the reference_rule block (snapshot id and manifest sha256)
to paste into config/regions/<region>.scoring.yaml. Reads only hash-verified tables.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys

import numpy as np

from headroom.ingest.snapshot import Snapshot


def _sig4(x: float) -> float:
    return float(f"{x:.4g}")


def derive(snapshot_dir: str) -> dict:
    snap = Snapshot(snapshot_dir)
    (_, content), = snap.read_tables(["congestion"]).values()
    rows = list(csv.DictReader(io.StringIO(content.decode("utf-8"))))
    rent = [float(r["rent_musd"]) for r in rows if (r.get("rent_musd") or "").strip()]
    hours = [float(r["hours_binding"]) for r in rows]
    coi = [float(r["rent_musd"]) * float(r["hours_binding"]) / 8760.0
           for r in rows if (r.get("rent_musd") or "").strip()]
    p99 = lambda v: _sig4(float(np.percentile(v, 99, method="linear")))
    refs = {"rent_ref_musd": p99(rent), "persist_ref_hours": p99(hours),
            "coi_ref_musd": p99(coi)}
    if any(not math.isfinite(v) or v <= 0 for v in refs.values()):
        raise SystemExit(f"non-positive reference: {refs}")
    return {
        "references": refs,
        "reference_rule": {
            "rule": "p99 (numpy linear) of each raw signal's expected value over scored "
                    "flowgates where the signal is observed, 4 significant figures",
            "derived_from_snapshot": snap.root.name,
            "snapshot_manifest_sha256": snap.manifest_sha256,
            "n_rent": len(rent), "n_hours": len(hours),
            "script": "scripts/derive_scoring_refs.py",
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("snapshot_dir")
    args = ap.parse_args(argv)
    json.dump(derive(args.snapshot_dir), sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
