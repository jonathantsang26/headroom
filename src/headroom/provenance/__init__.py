"""Provenance spine — cross-cuts every stage. Nothing moves without a source,
a pinned version, and lineage back to the original filing/report."""

from headroom.provenance.envelope import Fact, Range, derive_lineage_id
from headroom.provenance.lineage import LineageStore
from headroom.provenance.gate import PublishabilityError, assert_publishable

__all__ = [
    "Fact",
    "Range",
    "derive_lineage_id",
    "LineageStore",
    "PublishabilityError",
    "assert_publishable",
]
