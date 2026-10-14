"""Version pinning + run manifest (§8)."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path

# Pin to a stable PUDL release, never `nightly`, for any shareable run.
PUDL_RELEASE = "v2024.11.0"
RUN_MANIFEST_VERSION = 2


def source_pins(registry) -> dict:
    return {
        spec.name: {
            "source_version": spec.source_version,
            "bucket": spec.bucket,
            "tier": spec.tier,
            "publishable": spec.publishable,
        }
        for spec in registry.all()
    }


def build_run_manifest(
    registry,
    *,
    run_label: str,
    generated_at: datetime,
    public_only: bool = True,
    scoring: dict | None = None,
    region: str | None = None,
    mode: str | None = None,
    inputs: dict | None = None,
    runtime: dict | None = None,
    output_sha256: str | None = None,
) -> dict:
    """Record scoring, source pins, input identity, runtime, and output digest."""
    manifest = {
        "schema_version": RUN_MANIFEST_VERSION,
        "run_label": run_label,
        "region": region,
        "mode": mode,  # "shareable" | "internal" | "internal-synthetic"
        "generated_at": generated_at.isoformat(),
        "public_only": public_only,
        "pinned": {"pudl_release": PUDL_RELEASE},
        "sources": source_pins(registry),
    }
    if scoring is not None:
        manifest["scoring"] = deepcopy(scoring)
    if inputs is not None:
        manifest["inputs"] = deepcopy(inputs)
    if runtime is not None:
        manifest["runtime"] = deepcopy(runtime)
    if output_sha256 is not None:
        manifest["output_sha256"] = output_sha256
    return manifest


def write_run_manifest(manifest: dict, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return path
