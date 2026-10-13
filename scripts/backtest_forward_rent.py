"""Backtest forward-rent predictors on a snapshot's monthly SPP congestion dollars, and
derive the EV layer's forward_rent calibration (src/headroom/model/forward.py).

Rule: within the snapshot's data year, each split trains on six months and scores on
the other six (H1->H2, H2->H1, odd->even, even->odd months). A predictor is scored by
the held-out dollars its top 5% capture as a share of the best possible top 5%. The
chosen predictor has the highest mean capture. Its eps quantiles are the
realized / predicted ratios of the leaders over all four splits, normalized by the
system-wide change between the halves, at p0, 5, 10, 25, 50, 75, 90, 95 and 99, to
3 significant figures; p1 = p99.

    python scripts/backtest_forward_rent.py data/raw/spp/2026-10-13.r2
    python scripts/backtest_forward_rent.py data/raw/spp/2026-10-13.r2 --check config/regions/spp.scoring.yaml

Reads only raw files whose sha256 matches the snapshot manifest.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import yaml

from headroom.ingest.adapters.common import raw_entry
from headroom.ingest.snapshot import Snapshot
from headroom.model.forward import PREDICTORS, backtest, ratio_mean, ratio_quantiles

RULE = ("within-year splits (H1->H2, H2->H1, odd->even, even->odd); predictor with the "
        "highest mean held-out capture at top 5%; eps = p0..p99 of the leaders' "
        "system-normalized realized/predicted ratios, 3 significant figures, p1 = p99")


def monthly_matrix(snapshot_dir: str | Path) -> tuple[list[str], np.ndarray, list[str]]:
    """(constraint ids, $M by month, raw sha256s) from the snapshot's CBC files.
    Duplicate (month, constraint, contingency) rows count once; negative months are 0,
    as in the adapter."""
    snap = Snapshot(snapshot_dir)
    years = {k[len("raw:spp_cbc_"):][:4] for k in snap.manifest.get("raw", {})
             if k.startswith("raw:spp_cbc_")}
    if len(years) != 1:
        raise ValueError(f"{snap.root} needs the CBC files of exactly one year, has {years}")
    year = int(years.pop())
    per: dict[str, np.ndarray] = defaultdict(lambda: np.zeros(12))
    seen, shas = set(), []
    for month in range(1, 13):
        e = raw_entry(snap.root, f"raw:spp_cbc_{year}{month:02d}")
        shas.append(e["sha256"])
        text = Path(e["abspath"]).read_bytes().decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text, newline=""))
        header = [h.strip() for h in next(reader)]
        for row in reader:
            if not row:
                continue
            r = dict(zip(header, (v.strip() for v in row)))
            key = (r["Date"], r["Constraint Name"], r["Contingency Name"])
            if key in seen:
                continue
            seen.add(key)
            usd = -float(r["Sum"]) / 1e6
            if usd > 0:
                per[f"{r['Constraint Name']}|{r['Contingency Name']}"][month - 1] += usd
    ids = sorted(per)
    return ids, np.array([per[i] for i in ids]), shas


def derive(snapshot_dir: str) -> dict:
    snap = Snapshot(snapshot_dir)
    ids, monthly, shas = monthly_matrix(snapshot_dir)
    bt = backtest(monthly)
    chosen = max(PREDICTORS, key=lambda p: bt[p]["mean_capture"])
    quantiles = ratio_quantiles(bt[chosen]["ratios"])
    spec = {"predictor": chosen, "ratio_quantiles": quantiles}
    return {
        "forward_rent": {
            **spec,
            "rule": RULE,
            "derived_from_snapshot": snap.root.name,
            "cbc_raw_sha256": hashlib.sha256("".join(shas).encode()).hexdigest(),
            "ratio_mean": round(ratio_mean(spec), 3),
            "backtest": {
                p: {"mean_capture": round(bt[p]["mean_capture"], 4),
                    **{s: {k: (round(v, 4) if isinstance(v, float) else v)
                           for k, v in d.items()} for s, d in bt[p]["splits"].items()}}
                for p in PREDICTORS
            },
            "n_flowgates_with_rent": len(ids),
            "script": "scripts/backtest_forward_rent.py",
        }
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("snapshot_dir")
    ap.add_argument("--check", type=Path, help="scoring file whose forward_rent must match")
    args = ap.parse_args(argv)
    got = derive(args.snapshot_dir)["forward_rent"]
    if not args.check:
        json.dump({"forward_rent": got}, sys.stdout, indent=2)
        print()
        return 0
    have = (yaml.safe_load(args.check.read_text()) or {}).get("forward_rent") or {}
    keys = ("predictor", "ratio_quantiles", "cbc_raw_sha256", "backtest")
    diffs = [f"forward_rent.{k}: file {have.get(k)} != derived {got[k]}"
             for k in keys if have.get(k) != got[k]]
    for line in diffs:
        print(line, file=sys.stderr)
    print("OK" if not diffs else f"{len(diffs)} mismatch(es)")
    return 1 if diffs else 0


if __name__ == "__main__":
    raise SystemExit(main())
