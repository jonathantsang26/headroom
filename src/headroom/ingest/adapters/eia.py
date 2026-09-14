"""EIA adapter (EIA-860/861 asset data + EIA-930 hourly load).

fetch endpoints (confirm before first run; parameterized on purpose):
  - EIA-860 bulk zip: https://www.eia.gov/electricity/data/eia860/  (yearly zip)
  - EIA-930 API:      https://api.eia.gov/v2/electricity/rto/region-data/data/
                      requires a free API key via env var EIA_API_KEY; the
                      six-month balance CSVs are the keyless fallback.

normalize contracts:
  normalized/eia_lines.csv: line_id, voltage_kv           (canonical line_id)
  normalized/load_930.csv:  ba, ts, load_mw               (SPP BA hourly)
"""

from __future__ import annotations

from pathlib import Path


def fetch(snapshot_dir: Path) -> None:  # pragma: no cover - L2 work
    raise NotImplementedError("EIA fetch: confirm bulk URLs / API key first.")


def normalize(snapshot_dir: Path) -> None:  # pragma: no cover - L2 work
    raise NotImplementedError("EIA normalize: needs the canonical-id join.")
