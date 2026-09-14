"""6.4 Bound the unobservables. Anything not observed directly becomes a `Range`,
with `basis` documenting the derivation. Width is information — it propagates
honestly into rank stability."""

from __future__ import annotations

from headroom.provenance.envelope import Dist, Range
from headroom.provenance.synthetic import synthetic_range


def band(
    expected: float,
    *,
    lo_frac: float,
    hi_frac: float,
    basis: str,
    provider: str,
    lineage_id: str,
    dist: Dist = "triangular",
) -> Range:
    """A symmetric-ish uncertainty band around a point estimate."""
    return synthetic_range(
        lo=expected * lo_frac,
        expected=expected,
        hi=expected * hi_frac,
        basis=basis,
        provider=provider,
        lineage_id=lineage_id,
        dist=dist,
    )
