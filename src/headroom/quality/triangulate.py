"""5.1 Triangulate — cross-check the same quantity across independent sources."""

from __future__ import annotations

from collections import Counter

from headroom.provenance.envelope import Fact
from headroom.provenance.lineage import LineageStore
from headroom.sources.registry import SourceRegistry, load_registry


def _tier_rank(obs: Fact, registry: SourceRegistry) -> tuple[int, int]:
    """Sort key for an observation's source tier: tier 1 first; a non-numeric tier
    ("synthetic", "x") or an unregistered source last. The tier is the imitated
    source's (`provider`) where there is one, as on the synthetic fixtures."""
    spec = registry.get(obs.provider or "") or registry.get(obs.source)
    tier = spec.tier if spec is not None else None
    return (0, tier) if isinstance(tier, int) else (1, 0)


def triangulate_voltage(
    line_id: str, observations: list[Fact], store: LineageStore,
    registry: SourceRegistry | None = None,
) -> Fact:
    """Combine independent voltage observations into one derived `Fact`. The result
    is a derived envelope (inputs = the observation lineage_ids), so the gate's
    taint walk reaches the underlying sources. The most common value wins; a tie
    (one source against one, as every HIFLD/FERC1 disagreement is) goes to the value
    of the highest-tier source, then to the first observed."""
    if not observations:
        raise ValueError(
            f"triangulate_voltage({line_id!r}): no voltage observations to combine."
        )
    values = [float(o.value) for o in observations]
    counts = Counter(values)
    top_n = max(counts.values())
    tied = [v for v, n in counts.items() if n == top_n]  # first-observed order
    chosen = tied[0]
    if len(tied) > 1:
        registry = registry or load_registry()
        chosen = min(tied, key=lambda v: min(
            _tier_rank(o, registry) for o in observations if float(o.value) == v))

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
            # as fresh as the freshest observation it combines
            retrieved_at=max(o.retrieved_at for o in observations),
            lineage_id=f"fact:{line_id}:voltage",
            inputs=[o.lineage_id for o in observations],
        )
    )
