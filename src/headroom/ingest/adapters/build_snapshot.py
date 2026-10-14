"""Build and validate a snapshot before making it current."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import signal
import sys
import time
import tempfile
from datetime import date
from pathlib import Path
from uuid import uuid4

from headroom.ingest.adapters import eia, hifld, pudl_ferc1, spp
from headroom.ingest.adapters.common import install_ssl_opener
from headroom.ingest.snapshot import MANIFEST_NAME, Snapshot, sha256_of, update_manifest

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


# mkdtemp(prefix=".<snapshot>-") adds 8 characters
_STAGING = re.compile(r"^\.\d{4}-\d{2}-\d{2}(\.r\d+)?-[A-Za-z0-9_]{8}$")
_UNLOCKED_GRACE_S = 300  # a staging dir younger than this may not hold its lock yet


def _lock_path(staging: Path) -> Path:
    return staging.with_name(staging.name + ".lock")


def _hold_lock(path: Path):
    """An exclusive lock a running build holds on its staging (released at exit, even
    on SIGKILL, so a killed build's lock is free)."""
    fh = open(path, "a")
    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fh


def _is_live(staging: Path) -> bool:
    lock = _lock_path(staging)
    if not lock.exists():
        return time.time() - staging.stat().st_mtime < _UNLOCKED_GRACE_S
    try:
        with open(lock, "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fh, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True


def _stale_staging() -> list[Path]:
    """Hidden staging directories no running build holds: what failed or killed builds
    left behind."""
    if not SPP_ROOT.is_dir():
        return []
    return sorted(p for p in SPP_ROOT.iterdir()
                  if _STAGING.match(p.name) and p.is_dir() and not p.is_symlink()
                  and not _is_live(p))


def _terminate(signum, frame):  # SIGTERM: clean up like Ctrl-C does
    raise KeyboardInterrupt


def _reuse_raw(source: Path, staged: Path) -> dict:
    """Link (or copy) a completed snapshot's raw files into staging, verifying each
    against its recorded sha256; returns the source's raw entries, unchanged."""
    if source.is_symlink() or not source.is_dir():
        raise ValueError(f"Re-normalize needs a completed snapshot directory: {source}")
    raw = json.loads((source / MANIFEST_NAME).read_text()).get("raw") or {}
    if not raw:
        raise ValueError(f"{source} records no raw inputs")
    for name, entry in raw.items():
        rel = Path(entry["path"])
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError(f"Raw input {name!r} has an unsafe path: {rel}")
        src, dst = source / rel, staged / rel
        if sha256_of(src) != entry["sha256"]:
            raise ValueError(f"Raw input {name!r} in {source} does not match its sha256")
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
    return raw


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", type=_snapshot_date, default=None,
                    help="snapshot date (default: today; with --renormalize-from, the "
                         "source snapshot's date)")
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
    ap.add_argument(
        "--clean-staging", action="store_true",
        help="Remove staging directories that killed builds left behind, then exit.",
    )
    ap.add_argument(
        "--renormalize-from", metavar="SNAPSHOT", default=None,
        help="Re-run the normalizers on an existing snapshot's raw files (no network) "
             "into a new revision of its date. Needs --revision.",
    )
    args = ap.parse_args(argv)
    if args.clean_staging:
        for stale in _stale_staging():
            shutil.rmtree(stale)
            _lock_path(stale).unlink(missing_ok=True)
            print(f"[removed] {stale.name}")
        return 0
    source = None
    if args.renormalize_from:
        source = SPP_ROOT / args.renormalize_from
        try:
            day = json.loads((source / MANIFEST_NAME).read_text())["snapshot_date"]
        except (OSError, ValueError, KeyError) as exc:
            print(f"[FAILED] cannot read {source}: {exc}", file=sys.stderr)
            return 1
        if args.date not in (None, day):
            print(f"[FAILED] {source} is a snapshot of {day}, not {args.date}", file=sys.stderr)
            return 1
        if args.revision is None or args.activate_only:
            print("[FAILED] --renormalize-from needs --revision N and no --activate-only",
                  file=sys.stderr)
            return 1
        args.date = day
    args.date = args.date or date.today().isoformat()

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
    stale = _stale_staging()
    if stale:
        print(f"[warning] {len(stale)} staging directories that no running build holds "
              f"({', '.join(p.name for p in stale)}): failed builds kept for inspection or "
              "killed builds; --clean-staging removes them", file=sys.stderr)
    staged = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=SPP_ROOT))
    lock = _hold_lock(_lock_path(staged))
    previous_term = signal.signal(signal.SIGTERM, _terminate)

    try:
        (staged / "raw").mkdir()
        (staged / "normalized").mkdir()
        if source is not None:
            raw = _reuse_raw(source, staged)
            update_manifest(staged, snapshot_date=args.date, revision=args.revision, raw=raw,
                            renormalized_from={
                                "snapshot": source.name,
                                "manifest_sha256": sha256_of(source / MANIFEST_NAME)})
            print(f"[reused] {len(raw)} raw inputs from {source.name}")
        else:
            update_manifest(staged, snapshot_date=args.date, revision=args.revision)
            install_ssl_opener()
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
    except KeyboardInterrupt:
        # an interrupted build (Ctrl-C or SIGTERM) has nothing to inspect: remove its
        # own staging directory
        if staged.exists():
            shutil.rmtree(staged)
            print(f"[INTERRUPTED] removed staging {staged.name}; current unchanged",
                  file=sys.stderr)
        else:  # interrupted after the build became the dated snapshot
            current = SPP_ROOT / "current"
            target = current.readlink() if current.is_symlink() else None
            print(f"[INTERRUPTED] after {snap.name} was built (current -> {target})",
                  file=sys.stderr)
        return 130
    except Exception as exc:
        retained = staged if staged.exists() else snap
        print(f"[FAILED] {exc}; retained at {retained}", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
        _lock_path(staged).unlink(missing_ok=True)

    if args.no_activate:
        current = SPP_ROOT / "current"
        target = current.readlink() if current.is_symlink() else None
        print(f"[built] {snap} (current unchanged -> {target})")
    else:
        print(f"[current] -> {snap}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
