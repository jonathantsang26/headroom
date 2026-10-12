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

## Run on public data (SPP)
```bash
.venv/bin/python -m headroom.ingest.adapters.build_snapshot --date 2026-10-12   # ~1 min, no keys
.venv/bin/headroom run --region spp --out data/processed/spp-2026-10-12
.venv/bin/headroom reproduce --manifest data/processed/spp-2026-10-12/run_manifest.json --out data/replayed
```
Sources, pins and the table contract: [ADAPTERS.md](docs/ADAPTERS.md). A rebuild fails
closed if an upstream no longer serves the pinned version: the SPP queue CSV re-dates
itself on each refresh, and EIA revises EIA-930.

## Status
Verified on 2026-10-12. `headroom run --region spp` on snapshot `2026-10-12` writes a
**shareable** export: the unchanged publishable gate passed all 1,601 scores and all
nine input tables. Replay reproduces the ranking (digest `ed7355d5…`).

Real, from public sources (no keys, no accounts):
- **Scored:** 1,601 flowgates that bound in SPP's 2025 day-ahead market, rolled up to 732
  corridors (SPP Portal). Transformer constraints are excluded: 350 flowgates, 21% of
  shadow-price value.
- **Observed signals:** binding hours and summed |shadow price|. Rent = Σ|shadow price| ×
  an assumed 100–1,200 MW rating, because ratings are not public.
- **Topology:** 3,429 HIFLD Open lines (2023-09-05 edition) and 2,696 buses in the SWPP
  footprint, with 93.5 GW of EIA-860 generation attached.
- **FERC Form 1 (PUDL v2024.11.0, report year 2023):** 1,898 rows for 16 SPP filers,
  27,218 pole-mi. 25 rows join HIFLD unambiguously.
- **Load and queue:** EIA-930 2024 SWPP load snapshots; 680 active SPP queue requests
  (140.6 GW).

Not obtained, so marked unobserved (never zero; see `quality_report.json`):
- **Locations of SPP monitored elements.** SPP names them by market-model bus; the bus
  map is CEII. Loading (DC screen), demand pressure and radial are therefore unobserved
  for every flowgate. Half the composite weight (0.50) is unobserved, and the ranking
  rests on rent, persistence and cost of inaction.
- **Planning (SPP ITP):** PDF only.
- **Line voltages from EIA:** EIA publishes none.

Recommended GETs use DA binding as the "at limit" test, and radial is unknown, so no
flowgate gets Dynamic Line Rating: 1,542 get Advanced Power Flow Control and 59 get
Combination. Scoring references are the fixture-era defaults, and they were not retuned.
As a result, binding hours carry 63% of the top-100 composites (expected values). The [cross-check](docs/CROSSCHECK.md) against
echo-gridlock reproduces its FERC totals exactly. Those totals used one of PUDL's two
length columns (2.9% of SPP filers' miles), and none of its 8 SPP corridors reaches this
shortlist. `spp-synth` fixtures remain for offline tests and are always internal.
