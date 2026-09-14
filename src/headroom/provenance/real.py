"""Real-source envelope constructors — the live twin of `synthetic.py`.

Where `synthetic_fact`/`synthetic_range` hardwire source=synthetic_fixture (never
publishable), these stamp a real registered source from a snapshot manifest, so a
value ingested from pinned live data can clear the publishable gate — provided its
source is registered `publishable: true` in config/sources.yaml AND every ancestor
in its lineage is too. The gate stays fail-closed: an unregistered source name
blocks export exactly like a synthetic one.

`SourceStamp` is the unit of trust hand-off: (source, source_version, retrieved_at)
read from a snapshot's manifest — never invented at call sites.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from headroom.provenance.envelope import Confidence, Dist, Fact, Range


@dataclass(frozen=True)
class SourceStamp:
    """Provenance stamp for one normalized table in a snapshot: which registered
    source it came from, at which pinned version, retrieved when."""

    source: str  # registry name in config/sources.yaml, e.g. "pudl_ferc_form1"
    source_version: str  # e.g. "pudl_v2024.11.0"
    retrieved_at: datetime


def real_fact(
    *,
    value: float | str | None,
    stamp: SourceStamp,
    lineage_id: str,
    provider: str | None = None,
    confidence: Confidence = "medium",
    unit: str | None = None,
    as_reported: str | None = None,
    flags: list[str] | None = None,
) -> Fact:
    """A Bucket-A Fact carrying its TRUE source from a snapshot manifest."""
    return Fact(
        value=value,
        as_reported=as_reported,
        unit=unit,
        confidence=confidence,
        flags=list(flags or []),
        source=stamp.source,
        provider=provider or stamp.source,
        source_version=stamp.source_version,
        retrieved_at=stamp.retrieved_at,
        lineage_id=lineage_id,
    )


def real_range(
    *,
    lo: float,
    expected: float,
    hi: float,
    basis: str,
    stamp: SourceStamp,
    lineage_id: str,
    provider: str | None = None,
    dist: Dist = "triangular",
    inputs: list[str] | None = None,
) -> Range:
    """A Bucket-B Range carrying its TRUE source. Pass `inputs` when derived from
    other envelopes (the gate's taint walk follows them); omit for a leaf."""
    return Range(
        lo=lo,
        expected=expected,
        hi=hi,
        dist=dist,
        basis=basis,
        source=stamp.source,
        provider=provider or stamp.source,
        source_version=stamp.source_version,
        lineage_id=lineage_id,
        inputs=list(inputs or []),
    )
