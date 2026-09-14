"""Map data layer (Phase-4 UI, Decision 3: legibility over polish)."""

from __future__ import annotations

from headroom.pipeline import PipelineResult

# One RGB per recommended GET — the overlay's single source of truth and the legend.
GET_COLORS: dict[str, list[int]] = {
    "Dynamic Line Rating": [31, 119, 180],       # blue
    "Topology Optimization": [255, 127, 14],     # orange
    "Advanced Power Flow Control": [44, 160, 44], # green
    "Combination": [214, 39, 40],                # red
}
GET_FALLBACK: list[int] = [150, 150, 150]        # grey — unknown/missing GET

# Line width in pixels scales with rank stability so stable corridors read as bolder.
_WIDTH_MIN = 1.5
_WIDTH_SPAN = 8.0


def get_color(recommended_get: str) -> list[int]:
    """RGB for a GET label, falling back to grey for anything unmapped."""
    return GET_COLORS.get(recommended_get, GET_FALLBACK)


def width_from_p(p_top_k: float) -> float:
    """Map rank stability (0..1) to a pixel line width."""
    return _WIDTH_MIN + _WIDTH_SPAN * max(0.0, min(1.0, p_top_k))


def corridor_segments(result: PipelineResult) -> list[dict]:
    """One row per shortlist corridor, map-ready. Geometry is joined from buses;
    a corridor with no resolvable geometry is kept with `has_geometry=False`."""
    lines_by_id = result.bucket_a.lines_by_id
    bus_by_id = {b.bus_id: b for b in result.bucket_a.buses}

    rows: list[dict] = []
    for c in result.model.shortlist:
        corridor = c["physical_corridor"]
        get = c["recommended_get"]
        seg = {
            "physical_corridor": corridor,
            "representative_flowgate": c["representative_flowgate"],
            "recommended_get": get,
            "p_top_k": c["p_top_k"],
            "n_flowgates": c["n_flowgates"],
            "composite_lo": c["composite_lo"],
            "composite_expected": c["composite_expected"],
            "composite_hi": c["composite_hi"],
            "provenance": c["provenance"],
            "color": get_color(get),
            "width": width_from_p(c["p_top_k"]),
            "from_lat": None,
            "from_lon": None,
            "to_lat": None,
            "to_lon": None,
            "has_geometry": False,
        }
        line = lines_by_id.get(corridor)
        if line is not None:
            fb = bus_by_id.get(line.from_bus)
            tb = bus_by_id.get(line.to_bus)
            if fb is not None and tb is not None:
                seg.update(
                    from_lat=fb.lat, from_lon=fb.lon,
                    to_lat=tb.lat, to_lon=tb.lon, has_geometry=True,
                )
        rows.append(seg)
    return rows


def bus_points(result: PipelineResult) -> list[dict]:
    """Reference nodes for the base scatter layer."""
    return [
        {"lat": b.lat, "lon": b.lon, "bus": b.bus_id, "role": b.role}
        for b in result.bucket_a.buses
    ]


def map_center(result: PipelineResult) -> tuple[float, float]:
    """Mean (lat, lon) of the buses, for the initial view. (0, 0) if none."""
    buses = result.bucket_a.buses
    if not buses:
        return (0.0, 0.0)
    return (
        sum(b.lat for b in buses) / len(buses),
        sum(b.lon for b in buses) / len(buses),
    )


def corridor_detail(result: PipelineResult, corridor_id: str) -> dict | None:
    """Detail for the selection panel: the representative flowgate's signal Ranges
    (as (lo, expected, hi) triples), composite band, GET, stability, provenance.
    Returns None if the corridor is not in the shortlist."""
    row = next(
        (c for c in result.model.shortlist if c["physical_corridor"] == corridor_id),
        None,
    )
    if row is None:
        return None
    rep = row["representative_flowgate"]
    score = next(
        (s for s in result.model.scores if s.constraint_id == rep), None
    )
    signals = (
        {name: (r.lo, r.expected, r.hi) for name, r in score.signals.items()}
        if score is not None
        else {}
    )
    return {
        "physical_corridor": corridor_id,
        "representative_flowgate": rep,
        "n_flowgates": row["n_flowgates"],
        "flowgates": row["flowgates"],
        "recommended_get": row["recommended_get"],
        "p_top_k": row["p_top_k"],
        "composite": (row["composite_lo"], row["composite_expected"], row["composite_hi"]),
        "signals": signals,
        "provenance": row["provenance"],
    }
