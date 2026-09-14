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
) -> GET:
    loading = signals["loading"].expected
    persistence = signals["persistence"].expected

    if constraint.ctype == "loopflow":
        return "Topology Optimization"

    if constraint.ctype == "interface":
        return "Advanced Power Flow Control"

    # thermal
    if radial:
        # weather-exposed radial line -> rate it dynamically
        if persistence >= _PERSISTENT and loading >= _HEAVY:
            return "Combination"
        return "Dynamic Line Rating"

    # meshed thermal: push flow off the overloaded path
    if loading >= _HEAVY and persistence >= _PERSISTENT:
        return "Combination"
    return "Advanced Power Flow Control"
