"""5.1 Triangulate — cross-check the same quantity across independent sources."""

from __future__ import annotations

from collections import Counter

from headroom.provenance.envelope import Fact
from headroom.provenance.lineage import LineageStore
from headroom.provenance.synthetic import SYNTHETIC_RETRIEVED_AT


def triangulate_voltage(
    line_id: str, observations: list[Fact], store: LineageStore
) -> Fact:
    """Combine independent voltage observations into one derived `Fact`. The result
    is a derived envelope (inputs = the observation lineage_ids), so the gate's
    taint walk reaches the underlying sources."""
    if not observations:
        raise ValueError(
            f"triangulate_voltage({line_id!r}): no voltage observations to combine."
        )
    values = [float(o.value) for o in observations]
    counts = Counter(values)
    chosen, top_n = counts.most_common(1)[0]

    flags: list[str] = []
    if len(counts) > 1:
        flags.append("source_divergence")
        confidence = "low"
    elif len(observations) >= 3:
        confidence = "high"  # all sources present and unanimous
    else:
        confidence = "medium"  # unanimous but thin corroboration

    # Record the disagreeing readings verbatim for the audit trail.
    as_reported = "; ".join(
        f"{o.provider}={o.as_reported}" for o in observations
    )

    return store.add(
        Fact(
            value=chosen,
            as_reported=as_reported,
            unit="kV",
            confidence=confidence,
            flags=flags,
            source="quality.triangulate",
            provider=observations[0].provider if observations else None,
            source_version="quality",
            retrieved_at=SYNTHETIC_RETRIEVED_AT,
            lineage_id=f"fact:{line_id}:voltage",
            inputs=[o.lineage_id for o in observations],
        )
    )
