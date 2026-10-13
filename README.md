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
Replay the verified run from its archived snapshot (`data/raw/spp/2026-10-13.r2`, kept
out of git):
```bash
.venv/bin/python -m headroom.ingest.adapters.build_snapshot --date 2026-10-13 --revision 2 --activate-only
.venv/bin/headroom run --region spp --out data/processed/spp-2026-10-13
.venv/bin/headroom reproduce --manifest data/processed/spp-2026-10-13/run_manifest.json --out data/replayed/spp-2026-10-13
```
Replay passes only at a commit whose pipeline code and source pins match the manifest
(see [SNAPSHOTS.md](docs/SNAPSHOTS.md)).

- **A fresh build.** `build_snapshot --date <today>` takes about a minute and needs no
  keys. It fetches every source again.
  - It fails if a source no longer matches its pin in `config/sources.yaml`: HIFLD, the
    SPP DA-BC, congestion-by-constraint and TCR files, or the SPP queue date. SPP
    re-dates the queue CSV on each refresh, so a new build first needs `spp_queue`
    re-pinned.
  - Re-pinning has a cost. A live run refuses stamps that differ from today's pins, so
    once `spp_queue` moves on, `2026-10-13.r2` and the committed test subset load only
    at a commit with their pins. Re-stamp the test subset, or build it again from the
    new snapshot, in the same commit.
  - It also fails if the congestion dollars trip a data-quality check.
- **No network.** `--renormalize-from <snapshot> --revision N` re-runs the normalizers
  on an archived snapshot's raw files.
- **Checks of the scoring file.** Its references and forward-rent calibration are
  derived from the snapshot, and both can be re-checked:
  ```bash
  .venv/bin/python scripts/derive_scoring_refs.py data/raw/spp/2026-10-13.r2 --check config/regions/spp.scoring.yaml
  .venv/bin/python scripts/backtest_forward_rent.py data/raw/spp/2026-10-13.r2 --check config/regions/spp.scoring.yaml
  ```

Sources, pins and the table contract: [ADAPTERS.md](docs/ADAPTERS.md). What each output
file and column means: [OUTPUTS.md](docs/OUTPUTS.md).

## Status
Verified on 2026-10-13. `headroom run --region spp` on snapshot `2026-10-13.r2` writes a
**shareable** export: the unchanged publishable gate passed all 2,072 scores, all nine
input tables and every value in the lineage graph. `headroom reproduce` rebuilds the
ranking (digest `b99b84ef…`), the export decision, and byte-identical scores, shortlist,
CSV, quality report and NOTICE. Each export carries `NOTICE.md`, which lists every
source used with its terms. SPP's terms allow non-commercial use with citation;
commercial use needs SPP's written authorization.

Real, from public sources (no keys, no accounts):
- **Scored:** 2,072 flowgates that bound in SPP's 2025 day-ahead market, from the twelve
  monthly DA binding-constraint archives: 1,682 lines, 379 transformers and 11
  interfaces. 2 phase shifters are excluded. They roll up to 892 corridors.
- **Congestion dollars:** SPP's posted congestion by constraint, observed for 2,054
  flowgates ($2,408.7M). That is 1.23× SPP's settled DA Collected ($1,957.8M), and the
  ratio is consistent month to month; see [CROSSCHECK.md](docs/CROSSCHECK.md). Binding
  hours, breached hours and months binding come from the DA files.
- **Topology:** 3,429 HIFLD Open lines (2023-09-05 edition) and 2,696 buses in the SWPP
  footprint, with 93.5 GW of EIA-860 generation attached.
- **FERC Form 1 (PUDL v2024.11.0, report year 2023):** 1,898 rows for 16 SPP filers.
  25 rows join HIFLD unambiguously.
- **Load and queue:** EIA-930 2024 SWPP load snapshots; 682 active SPP queue requests
  (140.8 GW), 106 of them mapped to a bus.

**How the shortlist is ranked.** Flowgates are ranked by the mean expected net value
($M/yr) of their best grid-enhancing technology. The EV layer is in
`model/ev.py`.
- Each Monte Carlo draw combines:
  - **Forward rent:** 2025 dollars × the share of months the flowgate bound in × a
    persistence ratio. The ratio comes from a held-out-months backtest
    ([FORWARD_RENT.md](docs/FORWARD_RENT.md)).
  - **Cited priors** from `config/get_priors.yaml`
    ([GET_PRIORS.md](docs/GET_PRIORS.md)): relief share, a shared haircut, annualized
    cost, and applicability by evidence class (line or transformer, BASE or N-1,
    episodic).
- The priors are judgement, not data. Their content and sha256 (`1dbc8c8d…`) are in the
  run manifest.
- The weighted composite is still exported as a diagnostic. It shares 85 of the top 103
  flowgates with the EV ranking.

| # | Corridor | 2025 DA rent | Months binding | Mean EV, $M/yr (p5–p95) | GET | P(no GET applies or pays) |
|---|---|---|---|---|---|---|
| 1 | LN OSAGE_OG - WEBBTAP4 | $97.0M | 12 | 2.48 (0–10.8) | DLR | 0.42 |
| 2 | LN CLEVLND7 - CLEV_AEC | $83.3M | 12 | 2.08 (0–9.2) | DLR | 0.44 |
| 3 | LN NEBRCTY - SUB3456 | $77.3M | 12 | 1.91 (0–8.8) | DLR | 0.44 |
| 4 | LN RUSSETT - SBROWN | $64.9M | 12 | 1.56 (0–7.1) | DLR | 0.46 |
| 5 | LN FT_CAL - FT_CAL | $42.6M | 9 | 0.66 (0–3.3) | DLR | 0.54 |
| 6 | LN OASIS - SAN_JUAN | $22.0M | 12 | 0.40 (0–2.0) | DLR | 0.58 |
| 7 | XFMR EDWV - EDWV | $60.9M | 8 | 0.37 (0–2.2) | TO | 0.76 |
| 8 | LN GORD - MAIZ | $40.2M | 6 | 0.36 (0–1.8) | DLR | 0.59 |
| 9 | LN ANADARKO - GRACMONT | $30.6M | 7 | 0.29 (0–1.5) | DLR | 0.61 |
| 10 | LN STONEWSW - TUPELO2 | $28.6M | 7 | 0.27 (0–1.3) | DLR | 0.61 |

All ten bind under N-1 contingencies.

- **Value.** 343 of 2,054 flowgates have a positive mean EV, 82 exceed $10k/yr and 4
  exceed $1M/yr. The total is $16.4M/yr, against $2.4B of 2025 congestion. Relief
  shares of about 8%, a haircut of about 0.35 and costs of $0.1–0.9M/yr leave value
  only where congestion is large and persistent.
- **GETs.** In the top 103, DLR is the GET worth pursuing for 82 and topology
  optimization for 21. Advanced power flow control's cost ($0.86M/yr at the mode)
  outweighs its value at the shortlist's rents.
- **Sensitivity of the shortlist.** These perturbations each keep at least 93% of the
  top 103: any single relief share, cost or the haircut moved to the low or high end
  of its range, or any single applicability probability moved by ±0.25. The three it
  depends on most are:
  - DLR relief share: 93% at its low end;
  - TO relief share: 93% at its low end;
  - DLR annual cost: 95% at its low end.
- **Sensitivity of the magnitudes.** These depend most on the haircut: at its low end,
  flowgates above $1k/yr fall from 154 to 69. The model choice matters more than any
  single prior: dropping forward rent (using the plain 2025 dollars) changes 18 of the
  103.
- **Forward rent.** Within 2025, only about 30% of one half-year's top 5% recur in the
  other half. Weighting by months binding captures 66% of held-out dollars, against
  64% for the raw annual dollars. That is a modest gain, and it is not tested across
  years.

Not obtained, so marked unobserved (never zero; see `quality_report.json`):
- **Locations of SPP monitored elements.** SPP names them by market-model bus, and the
  bus map is CEII.
  - Loading (DC screen), demand pressure and radial are unobserved for every flowgate.
  - Half the composite weight (0.50) is unobserved.
  - GET applicability can use only the element type, the contingency and the binding
    pattern. It cannot use parallel paths, terminal limits or line length.
- **Planning (SPP ITP):** PDF only.
- **Line voltages from EIA:** EIA publishes none.
- **Real-time congestion and a second data year:** not read. The forward-rent model is
  calibrated within 2025 only.

The [cross-check](docs/CROSSCHECK.md) against echo-gridlock reproduces its FERC totals
exactly. Those totals used one of PUDL's two length columns, which covers 2.9% of SPP
filers' miles. None of its 8 SPP corridors reaches this shortlist: it ranks the size of
345 kV+ assets, while Headroom ranks where SPP's market bound. Open items from the
[architecture review](docs/reviews/ARCHITECTURE_REVIEW_2026-10-13.md) are in its
roadmap. `spp-synth` fixtures remain for offline tests and are always internal.
