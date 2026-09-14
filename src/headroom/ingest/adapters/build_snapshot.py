"""Snapshot builder: `python -m headroom.ingest.adapters.build_snapshot [--date D]`"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from headroom.ingest.adapters import eia, hifld, pudl_ferc1, spp
from headroom.ingest.snapshot import update_manifest

_REPO_ROOT = Path(__file__).resolve().parents[4]
SPP_ROOT = _REPO_ROOT / "data" / "raw" / "spp"

FETCHES = [
    ("pudl_ferc1", pudl_ferc1.fetch),
    ("hifld", hifld.fetch),
    ("eia", eia.fetch),
    ("spp", spp.fetch),
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat())
    args = ap.parse_args(argv)

    snap = SPP_ROOT / args.date
    (snap / "raw").mkdir(parents=True, exist_ok=True)
    (snap / "normalized").mkdir(parents=True, exist_ok=True)
    update_manifest(snap, snapshot_date=args.date)

    for name, fn in FETCHES:
        try:
            entry = fn(snap)
            print(f"[fetched] {name}: {entry.get('bytes', '?')} bytes, "
                  f"sha256 {entry.get('sha256', '?')[:12]}...")
        except NotImplementedError as e:
            print(f"[todo]    {name}: {e}")
        except Exception as e:  # keep going; a snapshot can be partial while WIP
            print(f"[FAILED]  {name}: {e}", file=sys.stderr)

    cur = SPP_ROOT / "current"
    if cur.is_symlink() or cur.exists():
        cur.unlink()
    cur.symlink_to(snap.name)
    print(f"[current] -> {snap}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
