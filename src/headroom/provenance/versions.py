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
    scoring: dict | None = None,
    region: str | None = None,
    mode: str | None = None,
) -> dict:
    """Snapshot everything that determines a run's output: source versions AND the
    scoring config (seed/weights/draws/top_fraction). Capturing scoring is what makes
    the Phase-5 done-when ("a pinned manifest reproduces a prior run exactly")
    satisfiable — the ranking is a function of these, so they must be recorded.
    `generated_at` is passed in (not read from the clock) so a reproduction can stamp
    it deterministically."""
    manifest = {
        "run_label": run_label,
        "region": region,
        "mode": mode,  # "shareable" | "internal-synthetic"
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
    if scoring is not None:
        # The exact knobs that determine the ranking (seed is the reproducibility key).
        manifest["scoring"] = scoring
    return manifest


def write_run_manifest(manifest: dict, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return path
