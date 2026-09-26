# headroom

Public-data transmission screening with provenance and Monte Carlo rank stability.

- Scores flowgates, groups corridors, and reports uncertainty with GET screening rules.
- Carries observed `Fact` and reconstructed `Range` values through a lineage graph.
- Blocks shareable exports when lineage includes synthetic or non-publishable sources.
- Includes a Streamlit corridor map and offline reproducibility checks.

## Run offline
```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
.venv/bin/headroom run --region spp-synth --out data/processed
```
Map: install `.[app]`, then run `streamlit run src/headroom/app/streamlit_app.py`.

## Status
Complete runs use fabricated SPP-shaped fixtures and produce internal synthetic artifacts.
Live adapters and real-source reconstruction remain incomplete. SPP is the first target
region; there is no SPP pilot relationship or real-data result in this repository.
Live inputs require stamped, SHA256-verified [snapshots](docs/SNAPSHOTS.md).
Historical replay verifies pinned inputs, runtime, and ranking; see [replay](docs/SNAPSHOTS.md#historical-replay).
