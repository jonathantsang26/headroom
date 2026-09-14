"""Phase 5 reproducibility. A pinned manifest records the region + the exact scoring
config (incl. the RNG seed), so re-running from it reproduces the prior ranking
byte-for-byte. This is what makes Headroom a defensible deliverable rather than a
notebook: "where's this number from?" and "does it reproduce?" both have answers."""

from __future__ import annotations

import json
from pathlib import Path

from headroom.model import ScoringConfig
from headroom.pipeline import PipelineResult, run_pipeline


def reproduce_from_manifest(
    manifest: dict | str | Path, *, registry=None
) -> PipelineResult:
    """Re-run the pipeline exactly as a manifest describes it."""
    if not isinstance(manifest, dict):
        manifest = json.loads(Path(manifest).read_text())
    missing = [k for k in ("region", "scoring") if not manifest.get(k)]
    if missing:
        raise ValueError(
            f"Manifest is not reproducible: missing {missing}. Only manifests written "
            f"by export.deliver() (which record region + scoring) can be reproduced; "
            f"the bare demo manifest cannot."
        )
    region = manifest["region"]
    scoring = ScoringConfig.from_manifest(manifest["scoring"])
    return run_pipeline(region, scoring=scoring, registry=registry)


def scores_fingerprint(result: PipelineResult) -> list[tuple]:
    """A comparable fingerprint of a run's ranking output (constraint order,
    p_top_k, composite band, recommended GET). Two runs from the same pinned
    manifest must produce identical fingerprints."""
    return [
        (
            s.constraint_id,
            round(s.p_top_k, 6),
            round(s.composite.lo, 6),
            round(s.composite.expected, 6),
            round(s.composite.hi, 6),
            s.recommended_get,
        )
        for s in result.model.scores
    ]
