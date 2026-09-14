"""Shared adapter plumbing: polite stdlib download -> raw/, hash, manifest entry."""

from __future__ import annotations

import urllib.request
from pathlib import Path

from headroom.ingest.snapshot import sha256_of, update_manifest, utcnow_iso

UA = "headroom-snapshot/0.1 (public-data research; contact: repo owner)"


def download(url: str, dest: Path, *, max_mb: int = 250, timeout: int = 120) -> Path:
    """Download url -> dest (streaming, size-capped). Raises on HTTP errors."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    cap = max_mb * (1 << 20)
    got = 0
    with urllib.request.urlopen(req, timeout=timeout) as resp, dest.open("wb") as out:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            got += len(chunk)
            if got > cap:
                dest.unlink(missing_ok=True)
                raise RuntimeError(f"{url} exceeded {max_mb} MB cap")
            out.write(chunk)
    return dest


def record_raw(snapshot_dir: Path, name: str, url: str, path: Path) -> dict:
    """Hash a downloaded raw file and record it in the snapshot manifest."""
    entry = {
        "url": url,
        "path": str(path.relative_to(snapshot_dir)),
        "sha256": sha256_of(path),
        "retrieved_at": utcnow_iso(),
        "bytes": path.stat().st_size,
    }
    update_manifest(snapshot_dir, raw={name: entry})
    return entry
