"""The two foundational types. Everything else is built on them.

`Fact`  — every cleaned Bucket-A field (value + verbatim original + confidence).
`Range` — every Bucket-B unobservable (lo/expected/hi + how it was derived).

Both carry their own `lineage_id` and the `inputs` they were derived from, so the
provenance graph can be walked end-to-end (used by the publishable export gate).
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Confidence = Literal["high", "medium", "low"]
Dist = Literal["uniform", "triangular", "lognormal"]


def derive_lineage_id(label: str, inputs: list[str]) -> str:
    """Deterministic id for a *derived* envelope: a stable hash of the label plus
    its sorted parent ids. Deterministic (no randomness) so a pinned re-run
    reproduces identical ids — the reproducibility guarantee in §8."""
    h = hashlib.sha256()
    h.update(label.encode("utf-8"))
    for parent in sorted(inputs):
        h.update(b"\x00")
        h.update(parent.encode("utf-8"))
    return f"drv:{h.hexdigest()[:16]}"


class _Enveloped(BaseModel):
    """Shared provenance fields. `source` is a registered source name (or, for a
    derived value, a stage label like ``model.composite``); `inputs` are the
    lineage_ids this value was derived from (empty for a raw observation)."""

    source: str
    source_version: str
    lineage_id: str
    inputs: list[str] = Field(default_factory=list)


class Fact(_Enveloped):
    """A cleaned Bucket-A field. `as_reported` preserves the verbatim original and
    is never overwritten; `flags` records every imputation/anomaly (flag, never
    drop). Confidence is per-record, never global."""

    value: float | str | None
    as_reported: str | None = None
    unit: str | None = None
    confidence: Confidence
    flags: list[str] = Field(default_factory=list)
    retrieved_at: datetime


class Range(_Enveloped):
    """A Bucket-B unobservable, bounded. Width *is* information — it propagates
    honestly into rank stability. `basis` documents the derivation in one sentence."""

    lo: float
    expected: float
    hi: float
    dist: Dist = "triangular"
    basis: str

    @model_validator(mode="after")
    def _ordered(self) -> "Range":
        if not (self.lo <= self.expected <= self.hi):
            raise ValueError(
                f"Range requires lo <= expected <= hi, got "
                f"lo={self.lo}, expected={self.expected}, hi={self.hi}"
            )
        return self
