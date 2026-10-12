"""Build and validate a snapshot before making it current."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from uuid import uuid4

from headroom.ingest.adapters import eia, hifld, pudl_ferc1, spp
from headroom.ingest.adapters.common import install_ssl_opener
from headroom.ingest.snapshot import Snapshot, update_manifest

_REPO_ROOT = Path(__file__).resolve().parents[4]
SPP_ROOT = _REPO_ROOT / "data" / "raw" / "spp"

# Every adapter fetches before any normalizes; normalize runs in this order because
# the joins depend on it: HIFLD mints buses/line_ids (using EIA-860 plants for the
# footprint), EIA fills load, FERC 1 joins onto HIFLD lines, SPP maps onto buses.
ADAPTERS = [
    ("hifld", hifld.fetch, hifld.normalize),
    ("eia", eia.fetch, eia.normalize),
    ("pudl_ferc1", pudl_ferc1.fetch, pudl_ferc1.normalize),
    ("spp", spp.fetch, spp.normalize),
]


def _snapshot_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use a date in YYYY-MM-DD format.") from exc
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("Use a date in YYYY-MM-DD format.")
    return value


def activate_snapshot(snapshot_dir: Path) -> None:
    """Verify a completed snapshot and atomically replace the current symlink."""
    snapshot_dir = Path(snapshot_dir)
    if snapshot_dir.is_symlink():
        raise ValueError("A completed snapshot must be a directory, not a symlink.")
    Snapshot(snapshot_dir).require_tables()
    current = snapshot_dir.parent / "current"
    if current.exists() and not current.is_symlink():
        raise ValueError(f"Refusing to replace non-symlink {current}")
    temporary = current.with_name(f".current-{uuid4().hex}")
    try:
        temporary.symlink_to(snapshot_dir.name)
        os.replace(temporary, current)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", type=_snapshot_date, default=date.today().isoformat())
    ap.add_argument(
        "--activate-only", action="store_true",
        help="Validate and activate an existing dated snapshot without fetching.",
    )
    args = ap.parse_args(argv)

    snap = SPP_ROOT / args.date
    if args.activate_only:
        try:
            activate_snapshot(snap)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"[FAILED] activation: {exc}", file=sys.stderr)
            return 1
        print(f"[current] -> {snap}")
        return 0

    if snap.exists() or snap.is_symlink():
        print(
            f"[FAILED] {snap} already exists; snapshots are not overwritten. "
            "Use --activate-only to validate and activate it.", file=sys.stderr,
        )
        return 1

    SPP_ROOT.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=f".{args.date}-", dir=SPP_ROOT))
    (staged / "raw").mkdir()
    (staged / "normalized").mkdir()
    update_manifest(staged, snapshot_date=args.date)

    install_ssl_opener()
    try:
        for name, fetch, _ in ADAPTERS:
            entry = fetch(staged)
            print(f"[fetched] {name}: {entry.get('bytes', '?')} bytes")
        for name, _, normalize in ADAPTERS:
            normalize(staged)
            print(f"[normalized] {name}")
        Snapshot(staged).require_tables()
        if snap.exists() or snap.is_symlink():
            raise FileExistsError(f"Snapshot already exists: {snap}")
        staged.rename(snap)
        activate_snapshot(snap)
    except Exception as exc:
        retained = staged if staged.exists() else snap
        print(f"[FAILED] {exc}; retained at {retained}", file=sys.stderr)
        return 1

    print(f"[current] -> {snap}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
