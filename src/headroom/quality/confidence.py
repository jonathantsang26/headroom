"""5.3 Confidence — field-reliability prior, then downgrade on flags. Per-record,
never global. Triangulation has already set voltage confidence from source
agreement (5.1); this sets priors for single-source fields and applies a one-level
downgrade per invariant flag (floored at "low"). Imputed/dropped values are flagged,
never silent."""

from __future__ import annotations

from headroom.provenance.envelope import Confidence, Fact

_LADDER: list[Confidence] = ["low", "medium", "high"]

# Audited dollar figures start high; free-text / GIS-derived fields start lower.
FIELD_PRIORS: dict[str, Confidence] = {
    "voltage_kv": "medium",
    "conductor": "medium",
    "thermal_limit_mw": "medium",
    "cost_usd": "high",
}

# Flags that each knock confidence down one rung.
DOWNGRADE_FLAGS = {
    "source_divergence",
    "voltage_implausible",
    "conductor_voltage_mismatch",
    "imputed",
}


def _downgrade(level: Confidence, steps: int) -> Confidence:
    idx = max(0, _LADDER.index(level) - steps)
    return _LADDER[idx]


def apply_prior(field: str, fact: Fact | None) -> None:
    """Set a single-source field's confidence from its prior (voltage is left to
    triangulation), then downgrade on flags."""
    if fact is None:
        return
    if field != "voltage_kv":  # voltage confidence comes from triangulation
        fact.confidence = FIELD_PRIORS.get(field, "low")
    steps = sum(1 for f in set(fact.flags) if f in DOWNGRADE_FLAGS)
    if steps:
        fact.confidence = _downgrade(fact.confidence, steps)
