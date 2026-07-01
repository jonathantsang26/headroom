"""Phase 4 map data layer — the pure, offline-testable part of the UI (no browser,
no pydeck/streamlit import). The renderer in streamlit_app.py stays browser-only."""

import pytest

from headroom.app.map_data import (
    GET_COLORS,
    bus_points,
    corridor_detail,
    corridor_segments,
    get_color,
    map_center,
    width_from_p,
)
from headroom.pipeline import run_pipeline


@pytest.fixture(scope="module")
def result():
    return run_pipeline("spp-synth")


def test_one_segment_per_shortlist_corridor(result):
    segs = corridor_segments(result)
    assert len(segs) == len(result.model.shortlist)


def test_geometry_joined_from_bus_coords(result):
    segs = corridor_segments(result)
    bus_by_id = {b.bus_id: b for b in result.bucket_a.buses}
    lines = result.bucket_a.lines_by_id
    for seg in segs:
        if not seg["has_geometry"]:
            continue
        line = lines[seg["physical_corridor"]]
        fb, tb = bus_by_id[line.from_bus], bus_by_id[line.to_bus]
        assert (seg["from_lat"], seg["from_lon"]) == (fb.lat, fb.lon)
        assert (seg["to_lat"], seg["to_lon"]) == (tb.lat, tb.lon)


def test_line2_dedup_corridor_has_geometry_and_two_flowgates(result):
    seg = next(s for s in corridor_segments(result) if s["physical_corridor"] == "LINE_2")
    assert seg["n_flowgates"] == 2  # FLOWGATE_1 + FLOWGATE_2 roll up to one corridor
    assert seg["has_geometry"] is True
    assert seg["from_lat"] is not None and seg["to_lat"] is not None


def test_color_matches_get_and_all_gets_have_a_color(result):
    for seg in corridor_segments(result):
        assert seg["color"] == get_color(seg["recommended_get"])
        assert seg["recommended_get"] in GET_COLORS  # no fixture GET falls to grey


def test_width_encodes_stability():
    assert width_from_p(0.0) < width_from_p(1.0)
    assert width_from_p(2.0) == width_from_p(1.0)  # clamped
    assert width_from_p(-1.0) == width_from_p(0.0)


def test_missing_geometry_is_flagged_never_dropped(result):
    # A corridor whose line_id is not in lines_by_id must still surface, with
    # has_geometry=False and null coords — never silently omitted.
    result.model.shortlist.append({
        "physical_corridor": "LINE_DOES_NOT_EXIST",
        "representative_flowgate": "FLOWGATE_X",
        "n_flowgates": 1,
        "flowgates": ["FLOWGATE_X"],
        "p_top_k": 0.0,
        "composite_lo": 0.0, "composite_expected": 0.1, "composite_hi": 0.2,
        "recommended_get": "Combination",
        "provenance": "test",
    })
    try:
        seg = next(
            s for s in corridor_segments(result)
            if s["physical_corridor"] == "LINE_DOES_NOT_EXIST"
        )
        assert seg["has_geometry"] is False
        assert seg["from_lat"] is None and seg["to_lat"] is None
        assert seg["color"] == GET_COLORS["Combination"]  # still styled + present
    finally:
        result.model.shortlist.pop()  # keep the module-scoped fixture clean


def test_corridor_detail_has_ordered_signal_bands(result):
    d = corridor_detail(result, "LINE_2")
    assert d is not None
    assert len(d["signals"]) == 6
    for lo, exp, hi in d["signals"].values():
        assert lo <= exp <= hi
    assert d["provenance"]  # non-empty trail
    clo, cexp, chi = d["composite"]
    assert clo <= cexp <= chi


def test_corridor_detail_unknown_returns_none(result):
    assert corridor_detail(result, "NOPE") is None


def test_bus_points_and_center(result):
    pts = bus_points(result)
    assert len(pts) == len(result.bucket_a.buses)
    assert all("lat" in p and "lon" in p and "role" in p for p in pts)
    lat, lon = map_center(result)
    lats = [b.lat for b in result.bucket_a.buses]
    assert min(lats) <= lat <= max(lats)


def test_generalizes_to_second_region():
    miso = run_pipeline("miso-synth")
    assert len(corridor_segments(miso)) == len(miso.model.shortlist)
