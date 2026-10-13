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


def _revision(value: str) -> int:
    try:
        n = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use an integer revision >= 2.") from exc
    if n < 2:
        raise argparse.ArgumentTypeError("Revision 1 is the bare date; use >= 2.")
    return n


def snapshot_name(day: str, revision: int | None) -> str:
    """`YYYY-MM-DD` for a day's first build, `YYYY-MM-DD.rN` for its N-th (N >= 2)."""
    return day if revision is None else f"{day}.r{revision}"


def _next_free(day: str) -> int:
    n = 2
    while (SPP_ROOT / snapshot_name(day, n)).exists():
        n += 1
    return n


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
        "--revision", type=_revision, default=None,
        help="N-th build of the same day (N >= 2): snapshot YYYY-MM-DD.rN. Dated "
             "snapshots are never rebuilt in place.",
    )
    ap.add_argument(
        "--activate-only", action="store_true",
        help="Validate and activate an existing dated snapshot without fetching.",
    )
    ap.add_argument(
        "--no-activate", action="store_true",
        help="Build and validate, but leave `current` where it is.",
    )
    args = ap.parse_args(argv)

    name = snapshot_name(args.date, args.revision)
    snap = SPP_ROOT / name
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
            "Use --activate-only to validate and activate it, or build the next "
            f"revision: --revision {_next_free(args.date)}", file=sys.stderr,
        )
        return 1

    SPP_ROOT.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=SPP_ROOT))
    (staged / "raw").mkdir()
    (staged / "normalized").mkdir()
    update_manifest(staged, snapshot_date=args.date, revision=args.revision)

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
        if not args.no_activate:
            activate_snapshot(snap)
    except Exception as exc:
        retained = staged if staged.exists() else snap
        print(f"[FAILED] {exc}; retained at {retained}", file=sys.stderr)
        return 1

    if args.no_activate:
        current = SPP_ROOT / "current"
        target = current.readlink() if current.is_symlink() else None
        print(f"[built] {snap} (current unchanged -> {target})")
    else:
        print(f"[current] -> {snap}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
