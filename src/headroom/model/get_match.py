"""7.2 Which-GET match — turns "where is congestion" into "where would WHICH GET
pay". This is the differentiator; most congestion maps stop at location."""

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
) -> GET:
    """When the DC screen cannot see the monitored element (`loading_observed`
    False), "heavy" falls back on the market record: a constraint observed binding
    in the day-ahead market flowed at its limit in those hours."""
    persistence = signals["persistence"].expected
    if loading_observed:
        heavy = signals["loading"].expected >= _HEAVY
    else:
        heavy = signals["rent"].expected > 0.0 and persistence > 0.0

    if constraint.ctype == "loopflow":
        return "Topology Optimization"

    if constraint.ctype == "interface":
        return "Advanced Power Flow Control"

    # thermal
    if radial:
        # weather-exposed radial line -> rate it dynamically
        if persistence >= _PERSISTENT and heavy:
            return "Combination"
        return "Dynamic Line Rating"

    # meshed thermal: push flow off the overloaded path
    if heavy and persistence >= _PERSISTENT:
        return "Combination"
    return "Advanced Power Flow Control"


def get_basis(constraint: Constraint, *, radial: bool | None, loading_observed: bool) -> str:
    """One line on what the GET rule could see for this flowgate."""
    bits = [f"ctype={constraint.ctype}"]
    bits.append("radial=unknown(treated meshed)" if radial is None else f"radial={radial}")
    bits.append("heavy=DC screen" if loading_observed else "heavy=binding in DA market")
    return "; ".join(bits)
