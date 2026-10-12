"""Derived-envelope constructors. A derived value names the pipeline stage that
computed it (`source`) and the lineage ids it was computed from (`inputs`); the
export gate's taint walk follows `inputs` to the stamped leaves, so a derived value
is publishable exactly when everything it was computed from is. Inputs are required:
a derived value without inputs would be a leaf naming an unregistered source, which
the gate refuses (fail-closed), so the constructor refuses it first."""

from __future__ import annotations

from datetime import datetime

from headroom.provenance.envelope import Confidence, Dist, Fact, Range

DERIVED_VERSION = "derived"


def _require_inputs(lineage_id: str, inputs) -> list[str]:
    ids = [i for i in (inputs or []) if i]
    if not ids:
        raise ValueError(
            f"Derived value {lineage_id!r} has no inputs; its lineage must reach "
            "stamped source data."
        )
    return list(dict.fromkeys(ids))


def derived_range(
    *,
    lo: float,
    expected: float,
    hi: float,
    basis: str,
    stage: str,
    lineage_id: str,
    inputs: list[str],
    provider: str | None = None,
    dist: Dist = "triangular",
) -> Range:
    return Range(
        lo=lo,
        expected=expected,
        hi=hi,
        dist=dist,
        basis=basis,
        source=stage,
        provider=provider or stage,
        source_version=DERIVED_VERSION,
        lineage_id=lineage_id,
        inputs=_require_inputs(lineage_id, inputs),
    )


def derived_fact(
    *,
    value: float | str | None,
    stage: str,
    lineage_id: str,
    inputs: list[str],
    retrieved_at: datetime,
    provider: str | None = None,
    confidence: Confidence = "low",
    unit: str | None = None,
    as_reported: str | None = None,
    flags: list[str] | None = None,
) -> Fact:
    return Fact(
        value=value,
        as_reported=as_reported,
        unit=unit,
        confidence=confidence,
        flags=list(flags or []),
        source=stage,
        provider=provider or stage,
        source_version=DERIVED_VERSION,
        retrieved_at=retrieved_at,
        lineage_id=lineage_id,
        inputs=_require_inputs(lineage_id, inputs),
    )
