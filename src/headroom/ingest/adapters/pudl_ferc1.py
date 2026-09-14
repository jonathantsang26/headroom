"""PUDL / FERC Form 1 adapter."""

from __future__ import annotations

from pathlib import Path

from headroom.ingest.adapters.common import download, record_raw

STABLE_BASE = "https://pudl.catalyst.coop/stable"
TABLE = "core_ferc1__yearly_transmission_lines_sched422"
DEFAULT_URL = f"{STABLE_BASE}/{TABLE}.parquet"


def fetch(snapshot_dir: Path, url: str = DEFAULT_URL) -> dict:
    raw_path = snapshot_dir / "raw" / f"{TABLE}.parquet"
    download(url, raw_path)
    return record_raw(snapshot_dir, f"raw:{TABLE}", url, raw_path)


def normalize(snapshot_dir: Path) -> None:  # pragma: no cover - L2 work
    raise NotImplementedError(
        "ferc1 normalize requires the canonical-id join and a thermal-limit "
        "source decision (see module docstring)."
    )
