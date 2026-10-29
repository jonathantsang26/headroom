"""Phase 4 map/table UI (Decision 3: minimal, legibility over polish).

Two sources: a finished run, read from its directory and checked against its
manifest (never re-run, A33), or a synthetic fixture region, run in process."""

from __future__ import annotations

RUNS_ROOT = "data/processed"
FINISHED = "Finished run (from its files)"
SYNTHETIC = "Synthetic fixture (fabricated)"
SYNTHETIC_REGIONS = ["spp-synth", "miso-synth"]

# Built only from docs/OUTPUTS.md.
HOW_TO_READ = (
    "Each row is a **physical corridor**: one element (a line with endpoint order "
    "ignored, an interface by constraint name, or a substation's transformers) that "
    "many flowgates can sit on. It ranks by its highest-ranking member flowgate, whose "
    "values it shows. **`p_top_k`** is the share of Monte Carlo draws in which that "
    "flowgate is in the top k; under EV ranking it mostly reflects the chance that "
    "some GET applies to the element, so it shows stability and does not order the "
    "list. The **EV band** is the p5 / p50 / p95 of the realized net value of the GET "
    "decision over draws, in $M/yr: negative when the deployed GET loses money, 0 when "
    "nothing is deployed; `ev_mean` is the expected net value and ranks under EV "
    "ranking. **Most GETs are unresolved (purple)** because the GET rule resolves one "
    "only where the DC screen sees the element (the synthetic regions). Without "
    "topology, which is every real SPP flowgate (element locations are CEII), only the "
    "element rules a GET out, so lines and interfaces read `DLR, APFC, TO or "
    "Combination` and transformers `APFC or TO`. `recommended_get` is then a tie-break "
    "among them, not a finding, and the EV pick (`ev_recommended_get`) is the EV "
    "layer's best bet, not the only option with value."
)

MODE_LABELS = {
    "shareable": "shareable",
    "internal": "INTERNAL",
    "internal-synthetic": "INTERNAL.synthetic",
}


def _legend_html(entries) -> str:  # pragma: no cover - presentation helper
    """entries: (label, rgb) pairs, one per recommendation shown on the map. Labels
    come from run files, so they are escaped."""
    from html import escape

    chips = []
    for g, color in entries:
        r, gg, b = color[:3]
        chips.append(
            f'<span style="display:inline-block;width:12px;height:12px;'
            f'background:rgb({r},{gg},{b});margin-right:6px;border-radius:2px;"></span>'
            f'<span style="margin-right:16px;">{escape(str(g))}</span>'
        )
    return "<div style='font-size:0.85em;'>" + "".join(chips) + "</div>"


def _tint(color) -> str:  # pragma: no cover - presentation helper
    r, g, b = color[:3]
    return f"background-color: rgba({r},{g},{b},0.35)"


def main() -> None:  # pragma: no cover - requires a browser/streamlit runtime
    from pathlib import Path

    import pandas as pd
    import pydeck as pdk
    import streamlit as st

    from headroom.app.map_data import (
        bus_points,
        corridor_detail,
        corridor_segments,
        map_center,
        run_corridor_detail,
        run_segments,
        shown_color,
    )
    from headroom.app.run_dir import RunDirError, list_run_dirs, load_run_dir
    from headroom.export import deliver
    from headroom.pipeline import run_pipeline
    from headroom.provenance.digests import ranking_digest

    @st.cache_resource(show_spinner="Running the synthetic pipeline…")
    def synthetic_result(region: str):
        return run_pipeline(region)

    st.set_page_config(page_title="Headroom — corridor screen", layout="wide")
    st.title("Headroom — transmission-constraint screen for GETs targeting")

    source = st.sidebar.radio("Source", [FINISHED, SYNTHETIC])
    result = None
    if source == FINISHED:
        root = st.sidebar.text_input("Runs folder", RUNS_ROOT)
        dirs = list_run_dirs(root)
        if not dirs:
            st.info(f"No run directories under `{root}`. Write one with "
                    "`headroom run --out DIR`, or pick the synthetic fixture source.")
            st.stop()
        chosen = st.sidebar.selectbox("Run", dirs, index=len(dirs) - 1,
                                      format_func=lambda p: p.name)
        # Checked on every rerun (cheap), so a file changed since is refused, not served.
        try:
            run = load_run_dir(chosen)
        except (RunDirError, OSError) as exc:
            st.error(f"Refused `{chosen}`: {exc}")
            st.stop()
        mode = run.mode
        counts = (run.n_constraints, len(run.shortlist), run.shortlist_k)
        snapshot, digest = run.snapshot_id or "—", run.output_sha256 or "—"
        ranking = run.manifest.get("scoring", {}).get("ranking", "composite")
        segs = run_segments(run.shortlist)
        buses = []
    else:
        region = st.sidebar.selectbox("Synthetic region (fabricated)", SYNTHETIC_REGIONS)
        result = synthetic_result(region)
        # the gate refuses a shareable export of any synthetic region
        mode = "internal-synthetic"
        counts = (result.model.n_constraints, len(result.model.shortlist), result.model.k)
        snapshot, digest = "— (fixture)", ranking_digest(result)
        ranking = result.scoring.ranking
        segs = corridor_segments(result)
        buses = bus_points(result)

    # ---- what this view is: mode, snapshot, digest, counts ----
    label = MODE_LABELS.get(mode, mode)
    ident = f"snapshot `{snapshot}` · output digest `{digest[:12]}…`"
    where = f" Read from `{run.path}`." if source == FINISHED else ""
    if mode == "internal-synthetic":
        st.error(f"**FABRICATED** · mode **{label}** · {ident}  \nSynthetic fixtures, not a "
                 "real result. The publishable gate refuses a shareable export; never share "
                 "this ordering." + where)
    elif mode == "shareable":
        st.success(f"Mode **{label}** · {ident}  \nA finished run on public data, read from "
                   f"`{run.path}`.")
    else:
        st.warning(f"Mode **{label}** · {ident}  \nThe publishable gate refused a shareable "
                   "export of this run; do not share it." + where)
    m = st.columns(3)
    m[0].metric("Flowgates", f"{counts[0]:,}")
    m[1].metric("Corridors", f"{counts[1]:,}")
    m[2].metric("Shortlist k", counts[2], help="top_fraction × scored flowgates")
    if source == FINISHED:
        st.caption(
            f"Ranked by {'EV' if ranking == 'ev' else 'composite'}. Every file the manifest "
            f"records matches its sha256 ({', '.join(run.digests)}). That checks the files "
            "against their own manifest; `headroom reproduce` replays the run from its inputs.")
    else:
        st.caption(f"Ranked by {ranking}. Re-run in process from fixtures and cached per region.")

    with st.expander("How to read this", expanded=True):
        st.markdown(HOW_TO_READ)

    seg_df = pd.DataFrame(segs)
    # the recommendation shown is the evidence's set where it leaves several GETs
    # open; recommended_get is then only the rule's tie-break
    all_gets = sorted(seg_df["get_shown"].unique())
    gets = st.sidebar.multiselect("Recommended GET", all_gets, default=all_gets)
    min_p = st.sidebar.slider("Min rank stability (p_top_k)", 0.0, 1.0, 0.0, 0.05)
    view = seg_df[(seg_df["get_shown"].isin(gets)) & (seg_df["p_top_k"] >= min_p)]

    # ---- map: corridor geometry colored by GET, width by rank stability ----
    st.subheader("Corridors")
    legend = seg_df.drop_duplicates("get_shown").sort_values("get_shown")
    st.markdown(_legend_html(zip(legend["get_shown"], legend["color"])),
                unsafe_allow_html=True)
    if result is None and seg_df["get_resolved"].isna().any():
        st.caption("This run predates get_shown: its rows show recommended_get as written, "
                   "with the evidence's GET set unknown.")
    geo = view[view["has_geometry"]]
    if seg_df["has_geometry"].any():
        st.caption(
            "Line color = recommended GET (purple: the evidence leaves several GETs open) · "
            "line width = rank stability (p_top_k). Grey nodes are buses; corridors without "
            "resolvable geometry are in the table only."
        )
        lat0, lon0 = map_center(result)
        layers = [
            pdk.Layer(
                "ScatterplotLayer",
                data=pd.DataFrame(buses),
                get_position=["lon", "lat"],
                get_radius=7000,
                radius_min_pixels=3,
                get_fill_color=[120, 120, 120, 120],
                pickable=False,
            ),
            pdk.Layer(
                "LineLayer",
                data=geo,
                get_source_position=["from_lon", "from_lat"],
                get_target_position=["to_lon", "to_lat"],
                get_color="color",
                # widths are pixels (LineLayer's default); a string width_units would
                # reach deck.gl as an expression ("@@=pixels"), not as the unit
                get_width="width",
                pickable=True,
            ),
        ]
        tooltip = {
            "text": "{physical_corridor}\n{get_shown}\n"
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
    else:
        st.info("No map: this run's files carry no coordinates (for a real SPP run, "
                "element locations are CEII), so every corridor is in the table below, "
                "with its cell colored as the map would color its line.")

    # ---- ranked corridor shortlist (the presented unit) ----
    st.subheader("Ranked corridors (footprint-deduped)")
    st.caption(f"Showing {len(view):,} of {len(seg_df):,} corridors, in rank order.")
    show_cols = [
        "rank", "physical_corridor", "get_shown", "p_top_k", "n_flowgates",
        "composite_lo", "composite_expected", "composite_hi", "has_geometry",
    ]
    # the EV layer's economics, when it ran: what pays, beside what physically fits
    if view["ev_get"].notna().any():
        show_cols[3:3] = ["ev_get", "ev_mean", "ev_lo", "ev_expected", "ev_hi"]
    table = view[show_cols].style.apply(
        lambda col: [_tint(view.at[i, "color"]) for i in col.index], subset=["get_shown"])
    st.dataframe(
        table,
        hide_index=True,
        column_config={
            "get_shown": st.column_config.TextColumn(
                "get_shown", help="The GET the evidence resolves, or every GET it leaves open"),
            "ev_get": st.column_config.TextColumn(
                "EV pick", help="ev_recommended_get: the GET contributing most to ev_mean"),
            "ev_mean": st.column_config.NumberColumn("ev_mean $M/yr", format="%.2f"),
            "ev_lo": st.column_config.NumberColumn("EV p5", format="%.2f"),
            "ev_expected": st.column_config.NumberColumn("EV p50", format="%.2f"),
            "ev_hi": st.column_config.NumberColumn("EV p95", format="%.2f"),
            "p_top_k": st.column_config.NumberColumn("p_top_k", format="%.3f"),
        },
    )

    # ---- detail panel: uncertainty made legible ----
    if not view.empty:
        st.subheader("Corridor detail")
        sel = st.selectbox("Inspect corridor", list(view["physical_corridor"]))
        d = (corridor_detail(result, sel) if result is not None
             else run_corridor_detail(run.shortlist, run.scores, sel))
        if d is not None:
            lo, exp, hi = d["composite"]
            st.markdown(_legend_html([(f"Recommended GET: {d['get_shown']}",
                                       shown_color(d["get_shown"], d["get_resolved"]))]),
                        unsafe_allow_html=True)
            notes = []
            if d["get_resolved"] is False:
                notes.append("The evidence does not separate these GETs; the rule's "
                             f"tie-break (recommended_get) is {d['recommended_get']}.")
            if d.get("ev_get") and d.get("ev_mean") is not None:
                notes.append(f"EV pick (ev_recommended_get): {d['ev_get']}.")
            if notes:
                st.caption(" ".join(notes))
            c1, c2, c3 = st.columns(3)
            c1.metric("Rank stability (p_top_k)", f"{d['p_top_k']:.3f}")
            if d["ev"] is not None and d.get("ev_mean") is not None:
                elo, eexp, ehi = d["ev"]
                c2.metric("EV mean ($M/yr)", f"{d['ev_mean']:.2f}")
                c2.caption(f"band p5 {elo:.2f} · p50 {eexp:.2f} · p95 {ehi:.2f}")
            c3.metric("Composite (p50)", f"{exp:.3f}")
            c3.caption(f"band p5 {lo:.3f} · p95 {hi:.3f}")
            shown = d["flowgates"][:10]
            more = len(d["flowgates"]) - len(shown)
            st.caption(f"{d['n_flowgates']} flowgate(s): {', '.join(shown)}"
                       + (f" and {more} more" if more > 0 else ""))
            if d.get("get_basis"):
                st.caption(f"What the GET rule saw (get_basis): {d['get_basis']}")
            sig_rows = [
                {"signal": n, "lo": s[0], "expected": s[1], "hi": s[2],
                 "coverage": d["coverage"].get(n)}
                for n, s in d["signals"].items()
            ]
            # a finished run's files record each signal's expected value only
            st.dataframe(pd.DataFrame(sig_rows).dropna(axis=1, how="all"), hide_index=True)
            if d["decision"]:
                st.caption("The decision split: how often each option is the one deployed "
                           "(p_best), and its part of ev_mean in $M/yr (they sum to it).")
                st.dataframe(pd.DataFrame(d["decision"]), hide_index=True)
            st.caption(f"provenance: {d['provenance']}")

    # ---- export (gate-decided mode); a finished run is already exported ----
    if result is not None:
        out_dir = st.text_input("Export to", str(Path(RUNS_ROOT) / region))
        if st.button("Export shortlist"):
            try:
                out = deliver(result, out_dir)
            except (OSError, RuntimeError, ValueError) as exc:
                st.error(f"Export refused: {exc}")
            else:
                label = MODE_LABELS.get(out.mode, out.mode)
                if out.mode == "shareable":
                    st.success(f"Wrote {label} export: {out.paths.get('csv')}")
                else:
                    st.warning(f"Wrote {label} export (not shareable): {out.paths.get('csv')}")


if __name__ == "__main__":  # pragma: no cover
    main()
