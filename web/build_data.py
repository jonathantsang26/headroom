"""Export a finished run's shortlist for the project page.

    python web/build_data.py data/processed/spp-2026-10-13

Writes web/shortlist.json: the first `shortlist_k` rows of shortlist.csv, in rank
order, with the manifest fields that identify the run.
"""

import csv
import json
import sys
from pathlib import Path

GET_SHORT = {
    "Dynamic Line Rating": "DLR",
    "Topology Optimization": "TO",
    "Advanced Power Flow Control": "APFC",
    "Combination": "COMB",
    "No cost-effective GET": "",
}


def main(run: Path) -> None:
    manifest = json.loads((run / "run_manifest.json").read_text())
    k = manifest["shortlist_k"]
    rows = list(csv.DictReader(open(run / "shortlist.csv")))[:k]
    out = {
        "run": {
            "snapshot": manifest["inputs"]["snapshot_id"],
            "output_sha256": manifest["output_sha256"],
            "generated_at": manifest["generated_at"],
            "export": manifest["export_decision"]["mode"],
            "shortlist_k": k,
        },
        "columns": ["rank", "flowgate", "corridor", "contingency", "rent", "months", "hours",
                    "ev", "p5", "p95", "get", "none", "evidence", "caution"],
        "rows": [
            [
                int(r["rank"]),
                r["constraint_id"],
                r["physical_corridor"],
                r["contingency_facility"],
                round(float(r["rent_musd"]), 2),
                int(r["months_binding"]),
                int(float(r["hours_binding"])),
                round(float(r["ev_mean_musd"]), 3),
                round(float(r["ev_lo_musd"]), 3),
                round(float(r["ev_hi_musd"]), 3),
                GET_SHORT[r["ev_recommended_get"]],
                round(float(r["p_best_none"]), 3),
                r["get_evidence"],
                r["caution"],
            ]
            for r in rows
        ],
    }
    path = Path(__file__).with_name("shortlist.json")
    path.write_text(json.dumps(out, separators=(",", ":")) + "\n")
    print(f"{path}: {len(rows)} rows from {manifest['inputs']['snapshot_id']} ({manifest['output_sha256'][:8]})")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
