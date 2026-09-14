"""PUDL / FERC Form 1 adapter.

fetch(): pulls the FERC1 page-422 transmission-line statistics table as Parquet
from Catalyst's public bucket over HTTPS (no credentials), hashes it into raw/,
and records it in the snapshot manifest. Uses the `stable` channel to honor the
pinned-release rule (never nightly for a shareable run).

normalize(): NOT IMPLEMENTED YET — contract: emit normalized/ferc1_lines.csv with
    line_id, voltage_kv, conductor, thermal_limit_mw, cost_usd
where line_id is the CANONICAL id assigned by the HIFLD<->EIA<->FERC1 join.
Two honest blockers, both by design:
 1) THE JOIN: FERC1 rows are respondent+free-text designations; matching them to
    HIFLD geometry needs the crosswalk work (spatial + PUDL utility ids).
 2) THERMAL LIMITS ARE NOT IN FORM 1. The fixture pretends ferc1 carries
    thermal_limit_mw; the real column must come from RTO ratings or an estimate
    (flagged Fact) — decide before wiring, don't fake it.
"""

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
