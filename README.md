# headroom

Ranks transmission constraints by grid-enhancing-technology (GET) deployment
value using public data. Outputs is a rank-stable shortlist with
confidence bands. Pilot region: SPP.

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
