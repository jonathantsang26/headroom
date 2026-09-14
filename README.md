# headroom

Ranks transmission constraints by grid-enhancing-technology (GET) deployment
value using public data only. The output is a rank-stable shortlist with
confidence bands, not point estimates. Pilot region: SPP.

## What it does
- Loads line, bus and market tables for a region and wraps every value in a
  `Fact` (observed, with source and confidence) or a `Range` (reconstructed
  lo/expected/hi). Nothing enters scoring as a bare number.
- Reconstructs what filings don't report: congestion rent from price shadows,
  a DC/PTDF N-1 screen, planning-queue exhaust.
- Scores each constraint, picks the best-fit GET, and runs Monte-Carlo rank
  stability (`p_top_k`) so the shortlist comes with a probability, not a rank.
- Exports a ranked CSV/JSON/Parquet shortlist. The export gate walks the
  lineage graph and refuses any value whose provenance touches a
  non-publishable (CEII) source, so a public run cannot leak restricted data.
- Pins every source version in a run manifest; a pinned run reproduces exactly.
- Streamlit + pydeck map of the shortlisted corridors, colored by recommended GET.

## Run
```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest                            # offline, no keys
headroom run --region spp-synth --out data/processed
```
Map: `pip install -e ".[app]"`, then `streamlit run src/headroom/app/streamlit_app.py`.

## Layout
```
ingest/       region loader, snapshot manifests, source adapters (PUDL/FERC 1, EIA, HIFLD, SPP)
quality/      triangulation, invariants, confidence
reconstruct/  congestion rent, DC/PTDF screen, planning exhaust
model/        signals, GET match, composite score, rank stability
provenance/   Fact/Range envelopes, lineage, export gate, versions
export.py     ranked shortlist
app/          Streamlit map
config/       sources.yaml (registry, publishable flags), scoring.yaml (weights)
```

## Status
Runs end to end on offline SPP fixtures (69 tests). Live ingest reads pinned
snapshots built by the adapters; a live run stays gate-blocked as internal
until reconstruction is stamped with real sources. MISO is next.
