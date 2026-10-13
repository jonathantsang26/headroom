"""7.2 Which-GET match — turns "where is congestion" into "where would WHICH GET
pay". This module is the evidence screen: a rule over what is observed about the
binding element. The expected-value model (model/ev.py) turns the screen into
applicability and ranks by expected net value where a region asks for it."""

from __future__ import annotations

from headroom.provenance.envelope import Range
from headroom.schema.entities import GET, Constraint

_PERSISTENT = 0.6   # normalized persistence threshold
_HEAVY = 1.0        # loading >= 1.0 means at/over the thermal limit


def recommend_get(
    constraint: Constraint,
    *,
    radial: bool,
    signals: dict[str, Range],
    loading_observed: bool = True,
    breached_hours: float | None = None,
) -> GET:
    """Topology observed (the DC screen saw the element): the original rule. Topology
    unobserved: market facts only. "Heavy" is then DA flow recorded above the limit
    (breached hours >= 1), never merely binding, which every scored flowgate does."""
    persistence = signals["persistence"].expected
    persistent = persistence >= _PERSISTENT

    if constraint.ctype == "loopflow":
        return "Topology Optimization"
    if constraint.ctype == "interface":
        return "Advanced Power Flow Control"

    if loading_observed:
        heavy = signals["loading"].expected >= _HEAVY
        if heavy and persistent:
            return "Combination"
        if constraint.ctype == "thermal" and radial:
            # weather-exposed radial line -> rate it dynamically
            return "Dynamic Line Rating"
        # meshed thermal or a transformer: push flow off the overloaded element
        return "Advanced Power Flow Control"

    heavy = breached_hours is not None and breached_hours >= 1
    if heavy and persistent:
        return "Combination"
    if constraint.ctype == "transformer":
        return "Advanced Power Flow Control"  # DLR rates conductors, not transformers
    if constraint.base_case is False:
        # binds after a contingency shifts flow onto it: a parallel path exists
        return "Advanced Power Flow Control"
    # binds on its own rating with all elements in service, mesh unknown: raising
    # that rating is the GET that needs no topology
    return "Dynamic Line Rating"


def get_basis(constraint: Constraint, *, radial: bool | None, loading_observed: bool,
              breached_hours: float | None = None) -> str:
    """One line on what the GET rule could see for this flowgate."""
    bits = [f"ctype={constraint.ctype}"]
    bits.append("radial=unknown(treated meshed)" if radial is None else f"radial={radial}")
    if loading_observed:
        bits.append("heavy=DC screen")
    else:
        bits.append("heavy=DA breached hours" if breached_hours is not None
                    else "heavy=unobserved (no DA breach record)")
        base = constraint.base_case
        bits.append({True: "contingency=BASE (mesh unknown)",
                     False: "contingency=N-1 (meshed: a parallel path exists)",
                     None: "contingency=unknown"}[base])
    return "; ".join(bits)
