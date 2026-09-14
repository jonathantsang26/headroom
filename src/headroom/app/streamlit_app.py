"""Phase 4 map/table UI (Decision 3: minimal, legibility over polish)."""

from __future__ import annotations


def _legend_html(gets) -> str:  # pragma: no cover - presentation helper
    from headroom.app.map_data import get_color

    chips = []
    for g in gets:
        r, gg, b = get_color(g)[:3]
        chips.append(
            f'<span style="display:inline-block;width:12px;height:12px;'
            f'background:rgb({r},{gg},{b});margin-right:6px;border-radius:2px;"></span>'
            f'<span style="margin-right:16px;">{g}</span>'
        )
    return "<div style='font-size:0.85em;'>" + "".join(chips) + "</div>"


def main() -> None:  # pragma: no cover - requires a browser/streamlit runtime
    import pandas as pd
    import pydeck as pdk
    import streamlit as st

    from headroom.app.map_data import (
        bus_points,
        corridor_detail,
        corridor_segments,
        map_center,
    )
    from headroom.export import deliver
    from headroom.pipeline import run_pipeline

    st.set_page_config(page_title="Headroom — corridor screen", layout="wide")
    st.title("Headroom — transmission-constraint screen for GETs targeting")

    region = st.sidebar.selectbox("Region", ["spp-synth", "miso-synth"])
    result = run_pipeline(region)

    if result.region.synthetic:
        st.warning(
            "SYNTHETIC data — this ordering is fabricated for demonstration and the "
            "publishable gate will refuse a shareable export."
        )

    segs = corridor_segments(result)
    seg_df = pd.DataFrame(segs)

    all_gets = sorted(seg_df["recommended_get"].unique())
    gets = st.sidebar.multiselect("Recommended GET", all_gets, default=all_gets)
    min_p = st.sidebar.slider("Min rank stability (p_top_k)", 0.0, 1.0, 0.0, 0.05)
    view = seg_df[(seg_df["recommended_get"].isin(gets)) & (seg_df["p_top_k"] >= min_p)]

    # ---- map: corridor geometry colored by GET, width by rank stability ----
    st.subheader("Corridors")
    st.markdown(_legend_html(all_gets), unsafe_allow_html=True)
    st.caption(
        "Line color = recommended GET · line width = rank stability (p_top_k). "
        "Grey nodes are buses; corridors without resolvable geometry are in the table only."
    )

    geo = view[view["has_geometry"]]
    lat0, lon0 = map_center(result)
    layers = [
        pdk.Layer(
            "ScatterplotLayer",
            data=pd.DataFrame(bus_points(result)),
            get_position=["lon", "lat"],
            get_radius=7000,
            get_fill_color=[120, 120, 120, 120],
            pickable=False,
        ),
        pdk.Layer(
            "LineLayer",
            data=geo,
            get_source_position=["from_lon", "from_lat"],
            get_target_position=["to_lon", "to_lat"],
            get_color="color",
            get_width="width",
            width_units="pixels",
            pickable=True,
        ),
    ]
    tooltip = {
        "text": "{physical_corridor}\n{recommended_get}\n"
                "p_top_k={p_top_k}  band=[{composite_lo}, {composite_hi}]"
    }
    st.pydeck_chart(
        pdk.Deck(
            layers=layers,
            initial_view_state=pdk.ViewState(latitude=lat0, longitude=lon0, zoom=5),
            tooltip=tooltip,
            map_style=None,
        )
    )

    # ---- ranked corridor shortlist (the presented unit) ----
    st.subheader("Ranked corridors (footprint-deduped)")
    show_cols = [
        "physical_corridor", "recommended_get", "p_top_k", "n_flowgates",
        "composite_lo", "composite_expected", "composite_hi", "has_geometry",
    ]
    st.dataframe(view[show_cols], use_container_width=True)

    # ---- detail panel: uncertainty made legible ----
    if not view.empty:
        st.subheader("Corridor detail")
        sel = st.selectbox("Inspect corridor", list(view["physical_corridor"]))
        d = corridor_detail(result, sel)
        if d is not None:
            lo, exp, hi = d["composite"]
            c1, c2, c3 = st.columns(3)
            c1.metric("Recommended GET", d["recommended_get"])
            c2.metric("Rank stability (p_top_k)", f"{d['p_top_k']:.3f}")
            c3.metric("Composite (p50)", f"{exp:.3f}", f"[{lo:.3f} .. {hi:.3f}]")
            st.caption(f"{d['n_flowgates']} flowgate(s): {', '.join(d['flowgates'])}")
            sig_rows = [
                {"signal": n, "lo": s[0], "expected": s[1], "hi": s[2]}
                for n, s in d["signals"].items()
            ]
            st.dataframe(pd.DataFrame(sig_rows), use_container_width=True)
            st.caption(f"provenance: {d['provenance']}")

    # ---- export (gate-decided mode) ----
    if st.button("Export shortlist"):
        out = deliver(result, "data/processed")
        st.success(f"Wrote {out.mode} export: {out.paths.get('csv')}")


if __name__ == "__main__":  # pragma: no cover
    main()
