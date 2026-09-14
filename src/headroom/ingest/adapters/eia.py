"""EIA adapter (EIA-860/861 asset data + EIA-930 hourly load)."""

from __future__ import annotations

from pathlib import Path


def fetch(snapshot_dir: Path) -> None:  # pragma: no cover - L2 work
    raise NotImplementedError("EIA fetch: confirm bulk URLs / API key first.")


def normalize(snapshot_dir: Path) -> None:  # pragma: no cover - L2 work
    raise NotImplementedError("EIA normalize: needs the canonical-id join.")
