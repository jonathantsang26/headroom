"""HIFLD adapter (transmission line geometry + voltages; skeleton authority)."""

from __future__ import annotations

from pathlib import Path


def fetch(snapshot_dir: Path) -> None:  # pragma: no cover - L2 work
    raise NotImplementedError("HIFLD fetch: pick bbox + confirm Open-tier layer URL.")


def normalize(snapshot_dir: Path) -> None:  # pragma: no cover - L2 work
    raise NotImplementedError("HIFLD normalize: canonical-id minting + join.")
