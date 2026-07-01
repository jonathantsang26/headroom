"""Phase 4 map/table UI (Decision 3: minimal, legibility over polish).

STATUS: structurally complete, NOT runtime-verified in this build (no browser here).
The verified deliverable is the export path (headroom.export); this app renders the
same rows + a corridor map and an export button on top of them.

Run:  streamlit run src/headroom/app/streamlit_app.py
"""

from __future__ import annotations


def main() -> None:  # pragma: no cover - requires a browser/streamlit runtime
    import pandas as pd
    import streamlit as st

    from headroom.export import build_score_rows, deliver
    from headroom.pipeline import run_pipeline

    st.set_page_config(page_title="Headroom — corridor screen", layout="wide")
    st.title("Headroom — transmission-constraint screen for GETs targeting")

    region = st.sidebar.selectbox("Region", ["spp-synth", "miso-synth"])
    result = run_pipeline(region)
    rows = build_score_rows(result)
    df = pd.DataFrame(rows)

    if result.region.synthetic:
        st.warning(
            "SYNTHETIC data — this ordering is fabricated for demonstration and the "
            "publishable gate will refuse a shareable export."
        )

    gets = st.sidebar.multiselect(
        "Recommended GET", sorted(df["recommended_get"].unique()),
        default=list(df["recommended_get"].unique()),
    )
    min_p = st.sidebar.slider("Min rank stability (p_top_k)", 0.0, 1.0, 0.0, 0.05)
    view = df[(df["recommended_get"].isin(gets)) & (df["p_top_k"] >= min_p)]

    # corridor map (buses as points; a fuller build draws line geometry)
    bus_df = pd.DataFrame(
        [{"lat": b.lat, "lon": b.lon, "bus": b.bus_id} for b in result.bucket_a.buses]
    )
    st.subheader("Corridors")
    st.map(bus_df, latitude="lat", longitude="lon")

    st.subheader("Ranked flowgates")
    st.dataframe(view, use_container_width=True)

    if st.button("Export shortlist"):
        d = deliver(result, "data/processed")
        st.success(f"Wrote {d.mode} export: {d.paths.get('csv')}")


if __name__ == "__main__":  # pragma: no cover
    main()
