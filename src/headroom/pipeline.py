"""End-to-end pipeline: ingest -> Bucket A -> Bucket B -> model. One `LineageStore`
threads through every stage, so provenance is continuous from raw value to score."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from headroom.ingest import ingest_region
from headroom.ingest.region import RegionConfig
from headroom.model import ModelResult, ScoringConfig, run_model
from headroom.provenance.lineage import LineageStore
from headroom.provenance.runtime import runtime_identity
from headroom.quality import BucketAResult, run_bucket_a
from headroom.reconstruct import BucketBResult, run_bucket_b
from headroom.sources import SourceRegistry, load_registry


@dataclass
class PipelineResult:
    region: RegionConfig
    store: LineageStore
    bucket_a: BucketAResult
    bucket_b: BucketBResult
    model: ModelResult
    registry: SourceRegistry
    scoring: ScoringConfig
    input_identity: dict
    runtime: dict


def run_pipeline(
    region_name: str,
    *,
    scoring: ScoringConfig | None = None,
    scoring_path=None,
    registry: SourceRegistry | None = None,
    data_dir: str | Path | None = None,
    expected_inputs: dict | None = None,
) -> PipelineResult:
    store = LineageStore()
    registry = deepcopy(registry or load_registry())
    scoring = deepcopy(scoring or ScoringConfig.load(scoring_path))
    runtime = runtime_identity()

    bundle = ingest_region(
        region_name, store, data_dir=data_dir, expected_inputs=expected_inputs,
    )
    bucket_a = run_bucket_a(bundle, store)
    bucket_b = run_bucket_b(bundle, bucket_a, store)
    model = run_model(bucket_a, bucket_b, store, scoring)

    return PipelineResult(
        region=bundle.region,
        store=store,
        bucket_a=bucket_a,
        bucket_b=bucket_b,
        model=model,
        registry=registry,
        scoring=scoring,
        input_identity=bundle.input_identity,
        runtime=runtime,
    )
