"""Core entities. Analytical values are `Fact` (Bucket A) or `Range` (Bucket B) —
never bare scalars. Structural geometry (coords, length, endpoints) stays scalar."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from headroom.provenance.envelope import Fact, Range

Coverage = Literal["observed", "unobserved"]  # non-filer footprint -> "unobserved"
ConstraintType = Literal["thermal", "loopflow", "interface", "transformer"]
GET = Literal[
    "Dynamic Line Rating",
    "Topology Optimization",
    "Advanced Power Flow Control",
    "Combination",
    "No cost-effective GET",  # EV ranking: expected cost exceeds expected relief
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
    (Decision 4). Links to the physical line(s) via `monitored_line`; when the
    monitored element cannot be resolved to public topology, `monitored_line` is
    None and `coverage` is "unobserved" (the line exists; we cannot place it)."""

    constraint_id: str
    monitored_line: str | None
    contingency_line: str | None
    ctype: ConstraintType
    monitored_facility: str | None = None  # as named by the RTO
    contingency_facility: str | None = None
    contingency_name: str | None = None  # "BASE" = binds with all elements in service
    coverage: Coverage = "observed"

    @property
    def branch_outage(self) -> bool | None:
        """For an N-1 flowgate: whether the outaged element is a branch (line or
        transformer). Only then does binding after it imply a shared loop, i.e. a
        parallel path. None when not N-1."""
        if self.base_case is not False:
            return None
        if self.contingency_line:
            return True
        f = (self.contingency_facility or "").strip()
        if ":" in f:
            parts = f.split(":")
            return len(parts) > 1 and len(parts[1].split()) >= 2  # two stations, or STA XF id
        return f.startswith(("LN", "DBL", "XF"))

    @property
    def base_case(self) -> bool | None:
        """True when the flowgate binds with no contingency; None when unknown."""
        name = self.contingency_name or self.contingency_facility
        if name:
            return name == "BASE"
        return None if self.contingency_line is None else False

    @property
    def corridor(self) -> str:
        """Footprint-dedup key: the resolved line, else the RTO's facility name."""
        return self.monitored_line or self.monitored_facility or self.constraint_id


class CongestionObs(BaseModel):
    constraint_id: str
    period: str
    rent_musd: Range | None  # congestion rent, $M/yr; None = unobserved for this row
    hours_binding: Range
    # observed $ outside the flowgate's largest month ($M/yr); None = no monthly $
    recurring_rent_musd: Range | None = None
    breached_hours: float | None = None  # DA hours over the limit; None = not recorded
    months_binding: int | None = None
    shadow_price_sum: float | None = None  # sum |SP| ($/MW-yr): value of 1 MW of relief
    fact_ids: list[str] = Field(default_factory=list)  # the row's observed Facts


class PlanningItem(BaseModel):
    constraint_id: str
    plan: str  # ITP / FCA / RTEP ...
    proposed_fix: str
    assigned_cost_musd: Range


class QueueItem(BaseModel):
    request_id: str
    near_bus: str  # "" when the POI could not be placed on a bus
    mw: float
    fuel: str
    lineage_ids: list[str] = Field(default_factory=list)


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
    # signal -> observed | unobserved (unobserved signals contribute nothing)
    signal_coverage: dict[str, Coverage] = Field(default_factory=dict)
    get_basis: str = ""
    # Expected-value layer (model/ev.py), when the region configures GET priors.
    ev: Range | None = None  # best-GET net value, $M/yr (p5/p50/p95 over draws)
    ev_mean: float | None = None  # mean best-GET net value, $M/yr: ranks under EV
    p_top_k_composite: float | None = None  # composite rank stability when EV ranks
    get_probabilities: dict[str, float] = Field(default_factory=dict)  # option -> P(best)
    get_candidates: list[str] = Field(default_factory=list)
    get_evidence: str = ""  # evidence class the applicability priors used
    get_rule: str = ""  # the evidence rule's GET (model/get_match.py)
    # The EV layer's own pick: the option contributing most to the decision's expected
    # value, an economics answer ("what pays here"). recommended_get stays the evidence
    # rule's physical assignment under every ranking. None when EV is not configured or
    # the flowgate has no observed rent.
    ev_get: GET | None = None
