"""Derive a region's scoring references from a pinned snapshot by a stated rule.

Rule: each reference is the 99th percentile (numpy linear interpolation) of the raw
signal's expected value over the scored flowgates where that signal is observed
(unobserved excluded, observed zeros included), rounded to 4 significant figures;
cost_of_inaction follows the scoring file's `definitions`.

    python scripts/derive_scoring_refs.py data/raw/spp/2026-10-13
    python scripts/derive_scoring_refs.py data/raw/spp/2026-10-13 --check config/regions/spp.scoring.yaml

Without --check, prints the references and the reference_rule block to paste into the
region's scoring file. With --check, exits 1 (with a diff) unless the file's references,
congestion_sha256 and cost_of_inaction definition match. The definition defaults to the
one the pipeline would use for that file (its `definitions`, else rent_x_persistence).
Reads only hash-verified tables.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path

import yaml

from headroom.ingest.snapshot import Snapshot
from headroom.model.references import derive_references

RULE = ("p99 (numpy linear) of each raw signal's expected value over scored flowgates where "
        "the signal is observed (unobserved excluded, observed zeros included), 4 significant "
        "figures; cost_of_inaction per `definitions`")


def derive(snapshot_dir: str, coi_definition: str) -> dict:
    snap = Snapshot(snapshot_dir)
    (_, content), = snap.read_tables(["congestion"]).values()
    rows = list(csv.DictReader(io.StringIO(content.decode("utf-8"))))
    d = derive_references(rows, coi_definition=coi_definition)
    return {
        "references": d["references"],
        "reference_rule": {
            "rule": RULE, "quantile": 0.99,
            "derived_from_snapshot": snap.root.name,
            "snapshot_manifest_sha256": snap.manifest_sha256,
            "congestion_sha256": snap.manifest["tables"]["congestion"]["sha256"],
            "coi_definition": coi_definition,
            "n_rent": d["n_rent"], "n_hours": d["n_hours"], "n_coi": d["n_coi"],
            "script": "scripts/derive_scoring_refs.py",
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("snapshot_dir")
    ap.add_argument("--check", type=Path, help="scoring file whose references must match")
    ap.add_argument("--coi-definition", default=None)
    args = ap.parse_args(argv)
    cfg = yaml.safe_load(args.check.read_text()) if args.check else {}
    coi = args.coi_definition or (cfg.get("definitions") or {}).get(
        "cost_of_inaction", "rent_x_persistence")  # the pipeline's default
    got = derive(args.snapshot_dir, coi)
    if not args.check:
        json.dump(got, sys.stdout, indent=2)
        print()
        return 0
    diffs = [f"references.{k}: file {cfg.get('references', {}).get(k)} != derived {v}"
             for k, v in got["references"].items() if cfg.get("references", {}).get(k) != v]
    rule = cfg.get("reference_rule") or {}
    for key in ("congestion_sha256", "coi_definition"):
        if rule.get(key) != got["reference_rule"][key]:
            diffs.append(f"reference_rule.{key}: file {rule.get(key)} != "
                         f"{got['reference_rule'][key]}")
    for line in diffs:
        print(line, file=sys.stderr)
    print("OK" if not diffs else f"{len(diffs)} mismatch(es)")
    return 1 if diffs else 0


if __name__ == "__main__":
    raise SystemExit(main())
