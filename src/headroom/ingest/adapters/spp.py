"""SPP adapter (Bucket B: binding constraints, LMP, queue, planning exhaust)."""

from __future__ import annotations

from pathlib import Path


def fetch(snapshot_dir: Path) -> None:  # pragma: no cover - L2 work
    raise NotImplementedError("SPP fetch: resolve current portal file paths.")


def normalize(snapshot_dir: Path) -> None:  # pragma: no cover - L2 work
    raise NotImplementedError("SPP normalize: constraint-name -> line_id mapping.")
