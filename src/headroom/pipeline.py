"""End-to-end pipeline: ingest -> Bucket A -> Bucket B -> model. One `LineageStore`
threads through every stage, so provenance is continuous from raw value to score."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

from headroom.ingest import ingest_region
from headroom.ingest.region import RegionConfig
from headroom.model import ModelResult, ScoringConfig, run_model
from headroom.model.references import check_reference_rule
from headroom.provenance.lineage import LineageStore
from headroom.provenance.runtime import runtime_identity
from headroom.quality import BucketAResult, run_bucket_a
from headroom.quality.report import build_quality_report
from headroom.reconstruct import BucketBResult, run_bucket_b
from headroom.sources import SourceRegistry, load_registry


def check_stamp_pins(bundle, registry: SourceRegistry) -> None:
    """Every publishable stamp a live snapshot carries (table, contributor, column)
    must be the version config/sources.yaml pins today, or the run manifest's
    `sources` block would certify inputs the run did not read. Fail closed: rebuild
    the snapshot, or check out the commit whose pins it was built under."""
    wrong = []
    for table, stamps in sorted(bundle.table_sources.items()):
        for st in stamps:
            spec = registry.get(st.source)
            if spec is None:
                wrong.append(f"{table}: {st.source} is not a registered source")
            elif spec.publishable and st.source_version != spec.source_version:
                wrong.append(f"{table}: {st.source} {st.source_version} (pinned "
                             f"{spec.source_version})")
    if wrong:
        raise ValueError("Snapshot stamps differ from the registry pins: "
                         + "; ".join(sorted(set(wrong))))


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
    quality_report: dict = field(default_factory=dict)


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
    if scoring is None and scoring_path is None:
        from headroom.ingest.region import get_region

        scoring_path = get_region(region_name).scoring_path  # region default, if any
    scoring = deepcopy(scoring or ScoringConfig.load(scoring_path))
    runtime = runtime_identity()

    bundle = ingest_region(
        region_name, store, data_dir=data_dir, expected_inputs=expected_inputs,
    )
    if not bundle.region.synthetic:
        check_stamp_pins(bundle, registry)
    check_reference_rule(scoring, bundle.input_identity)
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
        quality_report=build_quality_report(bundle, bucket_a, bucket_b, model, registry),
    )
