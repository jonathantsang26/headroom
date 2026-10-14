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
    if constraint.base_case is False and constraint.branch_outage:
        # binds after a branch outage shifts flow onto it: the outaged branch shares a
        # loop with it, so a parallel path exists
        return "Advanced Power Flow Control"
    # binds on its own rating with all elements in service (or after a non-branch
    # outage), mesh unknown: raising that rating is the GET that needs no topology
    return "Dynamic Line Rating"


def get_basis(constraint: Constraint, *, radial: bool | None, loading_observed: bool,
              breached_hours: float | None = None, get: str | None = None) -> str:
    """One line on what the GET rule could see for this flowgate, and (without
    topology) which other GETs the evidence does not rule out."""
    bits = [f"ctype={constraint.ctype}"]
    if loading_observed:
        bits.append("radial=unknown(treated meshed)" if radial is None else f"radial={radial}")
        bits.append("heavy=DC screen")
        return "; ".join(bits)
    bits.append("radial=unknown" if radial is None else f"radial={radial}")
    bits.append(f"heavy=DA breached hours ({breached_hours:g} h)" if breached_hours is not None
                else "heavy=unobserved (no DA breach record)")
    base, branch = constraint.base_case, constraint.branch_outage
    if base is True:
        bits.append("contingency=BASE (binds with all elements in service; mesh unknown)")
    elif base is False and branch:
        bits.append("contingency=N-1 (branch outage: the outaged branch shares a loop "
                    "with this element)")
    elif base is False:
        bits.append("contingency=N-1 (outaged element is not a branch: mesh unknown)")
    else:
        bits.append("contingency=unknown")
    if get == "Combination":
        bits.append("rule=breached and persistent")
    elif constraint.ctype == "transformer":
        bits.append("not_applicable=DLR (rates overhead conductors, not transformers); also=TO")
    elif constraint.ctype == "thermal" and get == "Advanced Power Flow Control":
        bits.append("also=DLR (raises the post-contingency rating if the conductor "
                    "limits), TO (post-contingency switching)")
    elif constraint.ctype == "thermal" and get == "Dynamic Line Rating":
        bits.append("also=APFC or TO (need a parallel path or switching options; "
                    "topology not public)")
    if constraint.ctype == "thermal" and _same_station_ends(constraint.monitored_facility):
        bits.append("caution=both ends carry the same station name (a bus tie or an "
                    "intra-station segment, where no GET may apply; element identity is CEII)")
    return "; ".join(bits)


def _same_station_ends(facility: str | None) -> bool:
    """'LN FT_CAL - FT_CAL': a monitored line whose two endpoint names match."""
    if not facility or not facility.startswith("LN ") or " - " not in facility:
        return False
    a, b = facility[3:].split(" - ", 1)
    return a.strip() == b.strip()
