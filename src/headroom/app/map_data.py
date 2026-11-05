"""Map data layer (Phase-4 UI, Decision 3: legibility over polish)."""

from __future__ import annotations

from headroom.model.get_match import get_label
from headroom.pipeline import PipelineResult

# One RGB per recommended GET — the overlay's single source of truth and the legend.
GET_COLORS: dict[str, list[int]] = {
    "Dynamic Line Rating": [31, 119, 180],       # blue
    "Topology Optimization": [255, 127, 14],     # orange
    "Advanced Power Flow Control": [44, 160, 44], # green
    "Combination": [214, 39, 40],                # red
}
GET_FALLBACK: list[int] = [150, 150, 150]        # grey — unknown/missing GET
# purple — the evidence leaves several GETs open, so no one GET's color is shown
GET_UNRESOLVED: list[int] = [148, 103, 189]

# Line width in pixels scales with rank stability so stable corridors read as bolder.
_WIDTH_MIN = 1.5
_WIDTH_SPAN = 8.0


def get_color(recommended_get: str) -> list[int]:
    """RGB for a GET label, falling back to grey for anything unmapped."""
    return GET_COLORS.get(recommended_get, GET_FALLBACK)


def get_shown(result: PipelineResult, flowgate: str, recommended_get: str) -> dict:
    """What the map shows as the recommendation: the GET when the evidence resolves
    one, otherwise the set it leaves open (recommended_get is then a tie-break). A
    flowgate the model did not score has no set: unknown, not resolved."""
    gets = result.model.extras.get("get_set", {}).get(flowgate)
    if gets is None:
        return {"get_shown": recommended_get, "get_resolved": None, "get_set": None}
    return {"get_shown": get_label(gets), "get_resolved": len(gets) == 1, "get_set": gets}


def shown_color(get_shown: str, resolved: bool | None) -> list[int]:
    """The resolved GET's color, or purple where the evidence leaves several open."""
    return GET_UNRESOLVED if resolved is False else get_color(get_shown)


def width_from_p(p_top_k: float) -> float:
    """Map rank stability (0..1) to a pixel line width."""
    return _WIDTH_MIN + _WIDTH_SPAN * max(0.0, min(1.0, p_top_k))


def _segment(rank: int, c: dict, shown: dict) -> dict:
    """A shortlist corridor row as a map/table row, before any geometry is joined."""
    return {
        "rank": rank,
        "physical_corridor": c["physical_corridor"],
        "representative_flowgate": c["representative_flowgate"],
        "recommended_get": c["recommended_get"],
        **shown,
        "p_top_k": c["p_top_k"],
        "n_flowgates": c["n_flowgates"],
        "composite_lo": c["composite_lo"],
        "composite_expected": c["composite_expected"],
        "composite_hi": c["composite_hi"],
        # economics beside the physical assignment, when the EV layer ran
        "ev_get": c.get("ev_get"),
        "ev_mean": c.get("ev_mean"),
        "ev_lo": c.get("ev_lo"),
        "ev_expected": c.get("ev_expected"),
        "ev_hi": c.get("ev_hi"),
        "provenance": c["provenance"],
        "color": shown_color(shown["get_shown"], shown["get_resolved"]),
        "width": width_from_p(c["p_top_k"]),
        "from_lat": None,
        "from_lon": None,
        "to_lat": None,
        "to_lon": None,
        "has_geometry": False,
    }


def corridor_segments(result: PipelineResult) -> list[dict]:
    """One row per shortlist corridor, map-ready. Geometry is joined from buses;
    a corridor with no resolvable geometry is kept with `has_geometry=False`."""
    lines_by_id = result.bucket_a.lines_by_id
    bus_by_id = {b.bus_id: b for b in result.bucket_a.buses}

    rows: list[dict] = []
    for rank, c in enumerate(result.model.shortlist, start=1):
        seg = _segment(rank, c, get_shown(result, c["representative_flowgate"],
                                          c["recommended_get"]))
        line = lines_by_id.get(c["physical_corridor"])
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


def shown_from_row(row: dict) -> dict:
    """get_shown/get_resolved/get_set as an exported row records them (get_set
    `;`-separated). A run exported before they existed did not record which GETs the
    evidence leaves open: it shows recommended_get labelled as the rule's pick, which
    no GET color matches (grey), since the set is unknown, not resolved."""
    if "get_shown" not in row:
        return {"get_shown": f"{row['recommended_get']} (rule's pick; GET set not recorded)",
                "get_resolved": None, "get_set": None}
    gets = row.get("get_set")
    return {"get_shown": row["get_shown"], "get_resolved": row.get("get_resolved"),
            "get_set": gets.split(";") if isinstance(gets, str) else gets}


def run_segments(shortlist: list[dict]) -> list[dict]:
    """The corridor rows of a finished run (shortlist.json), table-ready. Its files
    carry no coordinates, so no row has geometry."""
    return [_segment(rank, c, shown_from_row(c)) for rank, c in enumerate(shortlist, start=1)]


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
        **get_shown(result, rep, row["recommended_get"]),
        "ev_get": row.get("ev_get"),
        "ev_mean": row.get("ev_mean"),
        "ev": _ev_band(row),
        "p_top_k": row["p_top_k"],
        "composite": (row["composite_lo"], row["composite_expected"], row["composite_hi"]),
        "signals": signals,
        "coverage": ({name: score.signal_coverage.get(name, "observed") for name in signals}
                     if score is not None else {}),
        "get_basis": score.get_basis if score is not None else None,
        "decision": [],
        "provenance": row["provenance"],
    }


def _ev_band(row: dict) -> tuple[float, float, float] | None:
    """The champion's EV p5/p50/p95 ($M/yr), or None where EV did not run or saw no rent."""
    band = (row.get("ev_lo"), row.get("ev_expected"), row.get("ev_hi"))
    return None if None in band else band


# The options the EV decision deploys, with the slug its export columns use.
_DECISION_OPTIONS = (
    ("No cost-effective GET", "none"), ("Dynamic Line Rating", "dlr"),
    ("Advanced Power Flow Control", "apfc"), ("Topology Optimization", "to"),
    ("Combination", "c"),
)


def run_corridor_detail(shortlist: list[dict], scores: list[dict],
                        corridor_id: str) -> dict | None:
    """corridor_detail for a finished run: the corridor row joined to its
    representative flowgate's row in scores.json. The files record each signal's
    expected value only, so a signal's lo and hi are None. Returns None if the
    corridor is not in the shortlist."""
    row = next((c for c in shortlist if c["physical_corridor"] == corridor_id), None)
    if row is None:
        return None
    rep = row["representative_flowgate"]
    fg = next((s for s in scores if s["constraint_id"] == rep), {})
    names = [k[len("sig_"):] for k in fg if k.startswith("sig_")]
    decision = []
    for option, slug in _DECISION_OPTIONS:
        p_best = fg.get(f"p_best_{slug}")
        ev_from = fg.get(f"ev_from_{slug}_musd")
        if p_best is not None or ev_from is not None:
            decision.append({"option": option, "p_best": p_best, "ev_from_musd": ev_from})
    return {
        "physical_corridor": corridor_id,
        "representative_flowgate": rep,
        "n_flowgates": row["n_flowgates"],
        "flowgates": row["flowgates"],
        "recommended_get": row["recommended_get"],
        **shown_from_row(row),
        "ev_get": row.get("ev_get"),
        "ev_mean": row.get("ev_mean"),
        "ev": _ev_band(row),
        "p_top_k": row["p_top_k"],
        "composite": (row["composite_lo"], row["composite_expected"], row["composite_hi"]),
        "signals": {name: (None, fg[f"sig_{name}"], None) for name in names},
        "coverage": {name: fg.get(f"cov_{name}") for name in names},
        "get_basis": fg.get("get_basis"),
        "decision": decision,
        "provenance": row["provenance"],
    }
