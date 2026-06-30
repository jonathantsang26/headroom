"""Core entities. Analytical values are `Fact` (Bucket A) or `Range` (Bucket B) —
never bare scalars. Structural geometry (coords, length, endpoints) stays scalar.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from headroom.provenance.envelope import Fact, Range

Coverage = Literal["observed", "unobserved"]  # non-filer footprint -> "unobserved"
ConstraintType = Literal["thermal", "loopflow", "interface"]
GET = Literal[
    "Dynamic Line Rating",
    "Topology Optimization",
    "Advanced Power Flow Control",
    "Combination",
]


class Bus(BaseModel):
    bus_id: str
    name: str
    ba: str
    lat: float
    lon: float
    role: Literal["gen", "load"]
    weight: float = 1.0
    coverage: Coverage = "observed"


class Line(BaseModel):
    """Physical segment. The Bucket-A unit; the *component* of a flowgate."""

    line_id: str
    from_bus: str
    to_bus: str
    voltage_kv: Fact
    length_mi: float
    conductor: Fact | None = None
    thermal_limit_mw: Fact | None = None
    cost_usd: Fact | None = None
    reactance_pu: Range | None = None  # estimated impedance (Bucket B, Phase 2)
    coverage: Coverage = "observed"


class Constraint(BaseModel):
    """A flowgate = monitored element + contingency. The atomic SCORED unit
    (Decision 4). Links to the physical line(s) via `monitored_line`."""

    constraint_id: str
    monitored_line: str
    contingency_line: str | None
    ctype: ConstraintType


class CongestionObs(BaseModel):
    constraint_id: str
    period: str
    rent_musd: Range  # congestion rent, $M/yr
    hours_binding: Range


class PlanningItem(BaseModel):
    constraint_id: str
    plan: str  # ITP / FCA / RTEP ...
    proposed_fix: str
    assigned_cost_musd: Range


class QueueItem(BaseModel):
    request_id: str
    near_bus: str
    mw: float
    fuel: str


class Score(BaseModel):
    """Scoring output for one flowgate. Signals + composite are all `Range`s; the
    headline metric is `p_top_k` (rank stability)."""

    constraint_id: str
    physical_corridor: str  # footprint-dedup key for the shortlist (Decision 4)
    signals: dict[str, Range] = Field(default_factory=dict)
    composite: Range
    p_top_k: float
    recommended_get: GET
    provenance: str  # one-line trail
