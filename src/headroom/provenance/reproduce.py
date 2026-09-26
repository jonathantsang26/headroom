"""Replay verified inputs with the recorded scoring and runtime."""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from pathlib import Path

from headroom.ingest.region import get_region
from headroom.model import ScoringConfig
from headroom.pipeline import PipelineResult, run_pipeline
from headroom.provenance.digests import canonical_json, ranking_digest
from headroom.provenance.runtime import runtime_identity
from headroom.provenance.versions import PUDL_RELEASE, RUN_MANIFEST_VERSION, source_pins
from headroom.sources import load_registry


def _require_equal(recorded, actual, label: str) -> None:
    if canonical_json(recorded) != canonical_json(actual):
        raise ValueError(f"Manifest is not reproducible: {label} changed or is incomplete")


def _scoring_from_manifest(recorded: dict) -> ScoringConfig:
    try:
        weights, references = recorded["weights"], recorded["references"]
        order = recorded["signal_order"]
        if (
            not isinstance(weights, dict) or not weights
            or not isinstance(references, dict) or not references
            or not isinstance(order, list) or not all(isinstance(k, str) for k in order)
            or len(order) != len(set(order)) or set(order) != set(weights)
        ):
            raise ValueError("invalid signal_order, weights, or references")
        for mapping in (weights, references):
            for name, value in mapping.items():
                if not isinstance(name, str) or type(value) not in (float, int) or not math.isfinite(value):
                    raise ValueError("scoring values must be finite numbers")
        # Every reference normalizes a signal by division.
        if any(value <= 0 for value in references.values()):
            raise ValueError("references must be positive")
        if type(recorded["draws"]) is not int or recorded["draws"] < 1:
            raise ValueError("draws must be a positive integer")
        if type(recorded["seed"]) is not int or recorded["seed"] < 0:
            raise ValueError("seed must be a nonnegative integer")
        fraction = recorded["top_fraction"]
        if type(fraction) not in (float, int) or not 0 < fraction <= 1:
            raise ValueError("top_fraction must be between zero and one")
        scoring = ScoringConfig.from_manifest(recorded)
        # Each signal consumes RNG draws; JSON object order must not change that.
        scoring.weights = {name: weights[name] for name in order}
        return scoring
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"Manifest is not reproducible: invalid scoring ({exc})") from exc


def _input_directory(region, inputs: dict, override) -> Path:
    expected_kind = "synthetic" if region.synthetic else "snapshot"
    if (
        type(inputs.get("schema_version")) is not int or inputs["schema_version"] != 1
        or inputs.get("region") != region.name or inputs.get("rto") != region.rto
        or inputs.get("kind") != expected_kind
    ):
        raise ValueError("Manifest is not reproducible: input region or kind changed")
    if region.synthetic:
        return Path(override) if override is not None else region.data_dir
    snapshot_id = inputs.get("snapshot_id")
    if (
        not isinstance(snapshot_id, str) or not snapshot_id.strip()
        or snapshot_id in (".", "..") or any(c in snapshot_id for c in ("/", "\\", "\x00"))
    ):
        raise ValueError("Manifest is not reproducible: invalid snapshot_id")
    if override is not None:
        return Path(override)
    return region.data_dir.parent / snapshot_id


def reproduce_from_manifest(
    manifest: dict | str | Path, *, registry=None, data_dir: str | Path | None = None,
) -> PipelineResult:
    """Verify pins, replay the original inputs, and check the full ranking digest."""
    if not isinstance(manifest, dict):
        manifest = json.loads(Path(manifest).read_text())
    if not isinstance(manifest, dict):
        raise ValueError("Manifest is not reproducible: expected a JSON object")
    manifest = deepcopy(manifest)
    required = ("region", "scoring", "inputs", "runtime", "sources", "pinned", "output_sha256")
    missing = [key for key in required if not manifest.get(key)]
    if missing:
        raise ValueError(
            f"Manifest is not reproducible: missing {missing}. Legacy or demo manifests "
            "cannot establish historical inputs; create a new pinned run from trusted data."
        )
    if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != RUN_MANIFEST_VERSION:
        raise ValueError("Manifest is not reproducible: unsupported schema_version")
    for key in ("scoring", "inputs", "runtime", "sources", "pinned"):
        if not isinstance(manifest[key], dict):
            raise ValueError(f"Manifest is not reproducible: {key} must be an object")
    digest = manifest["output_sha256"]
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("Manifest is not reproducible: invalid output_sha256")
    if not isinstance(manifest["region"], str):
        raise ValueError("Manifest is not reproducible: invalid region")
    registry = registry or load_registry()
    _require_equal(manifest["pinned"], {"pudl_release": PUDL_RELEASE}, "version pins")
    _require_equal(manifest["sources"], source_pins(registry), "source registry")
    _require_equal(manifest["runtime"], runtime_identity(), "runtime or pipeline code")
    region = get_region(manifest["region"])
    directory = _input_directory(region, manifest["inputs"], data_dir)
    scoring = _scoring_from_manifest(manifest["scoring"])
    result = run_pipeline(
        region.name, scoring=scoring, registry=registry,
        data_dir=directory, expected_inputs=manifest["inputs"],
    )
    _require_equal(manifest["runtime"], result.runtime, "runtime or pipeline code")
    if ranking_digest(result) != digest:
        raise ValueError("Replay output does not match the recorded ranking digest")
    return result


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
