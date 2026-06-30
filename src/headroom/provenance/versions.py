"""Version pinning + run manifest (§8).

A shareable run pins every source version and records them in a manifest, so
"where's this number from?" has a one-query answer and a pinned manifest re-runs
to identical data output.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

# Pin to a stable PUDL release, never `nightly`, for any shareable run.
PUDL_RELEASE = "v2024.11.0"


def build_run_manifest(
    registry,
    *,
    run_label: str,
    generated_at: datetime,
    public_only: bool = True,
) -> dict:
    """Snapshot the exact source versions a run depends on. `generated_at` is
    passed in (not read from the clock) so a reproduction can stamp it
    deterministically."""
    return {
        "run_label": run_label,
        "generated_at": generated_at.isoformat(),
        "public_only": public_only,
        "pinned": {"pudl_release": PUDL_RELEASE},
        "sources": {
            spec.name: {
                "source_version": spec.source_version,
                "bucket": spec.bucket,
                "tier": spec.tier,
                "publishable": spec.publishable,
            }
            for spec in registry.all()
        },
    }


def write_run_manifest(manifest: dict, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return path
