# headroom

Ranks transmission flowgates by the expected value of deploying grid-enhancing
technologies (GETs), from public data only. The pilot region is SPP. Every value
carries its provenance, the shortlist carries Monte Carlo uncertainty, and an export
gate keeps synthetic or restricted data out of anything shareable.

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

## Layout
```
ingest/       region loader, snapshot manifests, source adapters (PUDL/FERC 1, EIA, HIFLD, SPP),
              snapshot builder (build_snapshot)
quality/      triangulation, invariants, confidence, the run's quality report
reconstruct/  congestion rent, DC/PTDF screen, planning exhaust
model/        signals, GET match, composite score, rank stability, EV layer (ev.py),
              forward rent (forward.py), scoring references
provenance/   Fact/Range envelopes, lineage, export gate, versions, replay
export.py     ranked shortlist, NOTICE.md, run manifest
app/          Streamlit map
config/       sources.yaml (registry, publishable flags), scoring.yaml (fixture weights),
              regions/ (SPP spec and scoring), get_priors.yaml (cited GET priors)
scripts/      reference rule, forward-rent backtest, EV sensitivity, echo-gridlock cross-check
```

## Run on public data (SPP)
Replay the verified run from its archived snapshot (`data/raw/spp/2026-10-13.r3`, kept
out of git). r3 is `2026-10-13.r2` renormalized with no network: only the congestion
table differs, by its congestion dollars by season. Build it from r2 once, then run:
```bash
.venv/bin/python -m headroom.ingest.adapters.build_snapshot --renormalize-from 2026-10-13.r2 --revision 3 --no-activate
.venv/bin/python -m headroom.ingest.adapters.build_snapshot --date 2026-10-13 --revision 3 --activate-only
.venv/bin/headroom run --region spp --out data/processed/spp-2026-10-13.r3
.venv/bin/headroom reproduce --manifest data/processed/spp-2026-10-13.r3/run_manifest.json --out data/replayed/spp-2026-10-13.r3
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
    once `spp_queue` moves on, `2026-10-13.r2`, `2026-10-13.r3` and the committed test
    subset load only at a commit with their pins. Re-stamp the test subset, or build it again from the
    new snapshot, in the same commit.
  - It also fails if the congestion dollars trip a data-quality check.
- **No network.** `--renormalize-from <snapshot> --revision N` re-runs the normalizers
  on an archived snapshot's raw files.
- **Checks of the scoring file.** Its references and forward-rent calibration are
  derived from the snapshot, and both can be re-checked:
  ```bash
  .venv/bin/python scripts/derive_scoring_refs.py data/raw/spp/2026-10-13.r3 --check config/regions/spp.scoring.yaml
  .venv/bin/python scripts/backtest_forward_rent.py data/raw/spp/2026-10-13.r3 --check config/regions/spp.scoring.yaml
  ```

Sources, pins and the table contract: [ADAPTERS.md](docs/ADAPTERS.md). What each output
file and column means: [OUTPUTS.md](docs/OUTPUTS.md).

## Status
Verified on 2026-11-06. `headroom run --region spp` on snapshot `2026-10-13.r3`, at
commit `eb8939c`, writes a **shareable** export: the unchanged publishable gate passed
all 2,072 scores, all nine input tables and every value in the lineage graph.
`headroom reproduce` rebuilds the ranking (digest `db3d36ae…`), the export decision,
and byte-identical scores, shortlist, CSV, quality report and NOTICE. The previous run,
on `2026-10-13.r2` at `45a79a0` (digest `a6750ade…`), still replays at its own commit;
what changed between them is in [FLOWGATE_EVIDENCE.md](docs/FLOWGATE_EVIDENCE.md). Later commits
replay it as long as they do not change the hashed pipeline code. Each export carries
`NOTICE.md`, which lists every source used with its terms. SPP's terms allow
non-commercial use with citation; commercial use needs SPP's written authorization.

Real, from public sources (no keys, no accounts):
- **Scored:** 2,072 flowgates that bound in SPP's 2025 day-ahead market, from the twelve
  monthly DA binding-constraint archives: 1,682 lines, 379 transformers and 11
  interfaces. 2 phase shifters are excluded. They roll up to 892 corridors.
- **Congestion dollars:** SPP's posted congestion by constraint, observed for 2,054
  flowgates ($2,408.7M). That is 1.23× SPP's settled DA Collected ($1,957.8M), and the
  ratio is consistent month to month; see [CROSSCHECK.md](docs/CROSSCHECK.md). The
  same dollars are split by season. Binding hours, breached hours and months binding
  come from the DA files.
- **Topology:** 3,429 HIFLD Open lines (2023-09-05 edition) and 2,696 buses in the SWPP
  footprint, with 93.5 GW of EIA-860 generation attached.
- **FERC Form 1 (PUDL v2024.11.0, report year 2023):** 1,898 rows for 16 SPP filers.
  25 rows join HIFLD unambiguously.
- **Load and queue:** EIA-930 2024 SWPP load snapshots; 682 active SPP queue requests
  (140.8 GW), 106 of them mapped to a bus.

**How the shortlist is ranked.** Flowgates are ranked by the expected net value
($M/yr) of a GET decision (`model/ev.py`). The decision is: find out which
grid-enhancing technologies apply to the element, then deploy the applicable one with
the highest expected net value, or none. It is made before the realized relief,
haircut, cost and next year's congestion are known. The inputs are:
- **Forward rent:** 2025 dollars × the share of months the flowgate bound in × a
  persistence ratio from a held-out-months backtest
  ([FORWARD_RENT.md](docs/FORWARD_RENT.md)).
- **Cited priors:** `config/get_priors.yaml`, version `spp-get-priors-2026-10-13.3`,
  sha256 `503e04a9…` ([GET_PRIORS.md](docs/GET_PRIORS.md)). They set relief share, a
  shared haircut, annualized cost, and applicability and relief scale by evidence
  class. The priors are judgement, not data, and their content is recorded in the run
  manifest.
- **The season of each flowgate's congestion:** DLR's relief share is scaled by where
  the flowgate's 2025 dollars fell, from 0.7 for all-summer congestion to 1.27 for
  all-winter (means of the priors' season factors). DLR adds least over ambient-adjusted
  ratings in hot, calm summers. This is the one flowgate-level signal the public data
  supports; the others considered, and why they are not used, are in
  [FLOWGATE_EVIDENCE.md](docs/FLOWGATE_EVIDENCE.md).

The weighted composite is still exported as a diagnostic.

| # | Corridor | 2025 DA rent (champion flowgate) | Months binding | Expected net value, $M/yr (p5 to p95) | GET | P(nothing deployed) |
|---|---|---|---|---|---|---|
| 1 | LN OSAGE_OG - WEBBTAP4 | $97.0M | 12 | 2.29 (−1.50 to 11.50) | DLR | 0.25 |
| 2 | LN CLEVLND7 - CLEV_AEC | $83.3M | 12 | 1.59 (−1.59 to 8.26) | TO | 0.26 |
| 3 | LN NEBRCTY - SUB3456 | $77.3M | 12 | 1.55 (−1.69 to 7.97) | DLR | 0.25 |
| 4 | LN RUSSETT - SBROWN | $64.9M | 12 | 1.27 (−1.76 to 7.07) | DLR | 0.25 |
| 5 | LN FT_CAL - FT_CAL | $42.6M | 9 | 0.51 (−0.18 to 2.63) | DLR | 0.41 |
| 6 | LN OASIS - SAN_JUAN | $22.0M | 12 | 0.29 (−0.21 to 1.69) | DLR | 0.43 |
| 7 | XFMR EDWV - EDWV | $60.9M | 8 | 0.27 (−0.24 to 1.77) | TO | 0.70 |
| 8 | LN GORD - MAIZ | $40.2M | 6 | 0.25 (−0.21 to 1.52) | DLR | 0.43 |
| 9 | LN ANADARKO - GRACMONT | $30.6M | 7 | 0.25 (−0.23 to 1.47) | DLR | 0.42 |
| 10 | LN CARTR2 - CKSAW1 | $61.9M | 3 | 0.22 (−0.24 to 1.39) | DLR | 0.42 |

All ten bind under N-1 contingencies. #2 changed from DLR to TO: 68% of its dollars
fell in June-August, where DLR adds least, so TO's relief now beats it where both
apply. #10 is winter-heavy (80% in December-February) and rose from #12. A negative p5 means the deployment loses money in
that draw. #5 has the same station name at both ends. It may be a bus tie or an
intra-station segment, where no GET applies, so its `get_basis` carries a caution. The
same is true of 16 scored "lines" in all.

- **Value.** 55 of 2,054 flowgates (50 corridors) have a GET worth deploying. 48 of
  them exceed $10k/yr, 19 exceed $100k/yr and 4 exceed $1M/yr. The total is
  $11.3M/yr, against $2.4B of 2025 congestion. Relief shares of about 8%, a haircut of
  about 0.35 and costs of $0.1–0.9M/yr leave value only where congestion is large and
  recurs. All 55 carry at least $3.7M of 2025 rent.
  - The other 48 rows of the 103-row shortlist have no GET worth deploying. They are
    ordered by the composite, as a watch list.
- **GETs.** Of the 55, DLR is the GET to pursue for 46 and topology optimization for 9.
  Advanced power flow control never is: its expected net value where it applies is
  negative for 99 of the top 103 (mode cost $0.86M/yr).
  - **Most picks and probabilities still follow the evidence class.** Applicability
    is a prior per class (line, transformer, interface; BASE or N-1), and no public
    data says whether a given element's limit can be relieved by a given GET. So 28 of
    the 55 share one line split (nothing 0.6, DLR 0.4) and all 8 transformers share
    another (nothing 0.7, TO 0.3). The season signal moves the ranking and 3 of the
    top 103 picks; it does not change shortlist membership
    ([FLOWGATE_EVIDENCE.md](docs/FLOWGATE_EVIDENCE.md)).
- **Sensitivity** (`scripts/ev_sensitivity.py`; each relief share, cost and the haircut
  at the ends of its range, each applicability ±0.25, and forward rent off).
  - **The leaders are stable:** under every perturbation at least 7 of the top 10 and
    14 of the top 20 stay.
  - **How many flowgates have a GET worth deploying is not stable.** It ranges from 14
    (haircut at its low end) to 116 (DLR cost at its low end), against 55. It depends
    most on the DLR and TO annual costs and the haircut, then on DLR relief share.
    Without forward rent, using the plain 2025 dollars, it is 84.
  - **The season factors matter little to the list:** at either end of either
    triangle, or removed, 52-57 flowgates have a GET worth deploying, at least 9 of the
    top 10 stay, and 97-99% of the top 103.
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
    pattern. It cannot use parallel paths, terminal limits or line length. DLR's relief
    share also uses the season of the congestion dollars.
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
