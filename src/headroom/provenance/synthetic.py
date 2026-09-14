"""Synthetic-provenance helpers (the autonomous-build safety guard)."""

from __future__ import annotations

from datetime import datetime, timezone

from headroom.provenance.envelope import Confidence, Dist, Fact, Range

SYNTHETIC_SOURCE = "synthetic_fixture"
SYNTHETIC_VERSION = "synthetic"
# Fixed epoch so synthetic runs are deterministic (no wall-clock in the data path).
SYNTHETIC_RETRIEVED_AT = datetime(2026, 6, 30, tzinfo=timezone.utc)


def synthetic_fact(
    *,
    value: float | str | None,
    provider: str,
    lineage_id: str,
    confidence: Confidence = "medium",
    unit: str | None = None,
    as_reported: str | None = None,
    flags: list[str] | None = None,
) -> Fact:
    """A Bucket-A Fact imitating `provider` but truly sourced from synthetic data."""
    return Fact(
        value=value,
        as_reported=as_reported,
        unit=unit,
        confidence=confidence,
        flags=list(flags or []),
        source=SYNTHETIC_SOURCE,
        provider=provider,
        source_version=SYNTHETIC_VERSION,
        retrieved_at=SYNTHETIC_RETRIEVED_AT,
        lineage_id=lineage_id,
    )


def synthetic_range(
    *,
    lo: float,
    expected: float,
    hi: float,
    basis: str,
    provider: str,
    lineage_id: str,
    dist: Dist = "triangular",
    inputs: list[str] | None = None,
) -> Range:
    """A Bucket-B Range imitating `provider` but truly sourced from synthetic data."""
    return Range(
        lo=lo,
        expected=expected,
        hi=hi,
        dist=dist,
        basis=basis,
        source=SYNTHETIC_SOURCE,
        provider=provider,
        source_version=SYNTHETIC_VERSION,
        lineage_id=lineage_id,
        inputs=list(inputs or []),
    )
