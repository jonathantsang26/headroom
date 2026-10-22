# Agent work document

The work document for coding agents (Claude Code and similar) working on Headroom.
Read it before you change anything. Paths are relative to the repo root. Line numbers
are as of `f707549` (main, tag `v0.1`); check them before you cite them.

## What Headroom is

- It ranks SPP's 2025 day-ahead flowgates by the expected net value ($M/yr) of relieving each one with a grid-enhancing technology (GET): dynamic line rating (DLR), advanced power flow control (APFC), topology optimization (TO), or DLR plus APFC ("Combination").
- It uses public data only: SPP market files, HIFLD Open, EIA and FERC Form 1 (via PUDL). It needs no keys and no accounts.
- Every input comes from a pinned, hashed snapshot. Every value carries its provenance: a `Fact` if observed, a `Range` if reconstructed.
- Exports fail closed. If a value's lineage touches a synthetic or non-publishable source, the publishable gate writes `.INTERNAL` files instead.
- It is a screen built on cited judgement priors, scoped to SPP 2025. It is not a forecast, an engineering study or a deployment plan.

## Repo map

| Path | What it holds |
|---|---|
| `src/headroom/` | The package (modules below). |
| `config/sources.yaml` | Source registry: pins (`source_version`) and `publishable` flags. Fail closed: a source with no flag is non-publishable. |
| `config/regions/spp.yaml` | SPP footprint, FERC 1 respondents, data years. |
| `config/regions/spp.scoring.yaml` | SPP scoring: `ranking: ev`, weights, references, forward-rent calibration. |
| `config/get_priors.yaml` | Cited GET priors (version `spp-get-priors-2026-10-13.2`). |
| `config/scoring.yaml` | Fixture scoring for `spp-synth`. |
| `docs/` | `ADAPTERS` (sources, table contract), `SNAPSHOTS` (manifests, revisions, replay), `OUTPUTS` (every file and column), `GET_PRIORS` (each prior and its citation), `FORWARD_RENT` (the held-out-months test), `CROSSCHECK` (against SPP totals and echo-gridlock), `BLIND_SPOTS` (scope limits), `reviews/ARCHITECTURE_REVIEW_2026-10-13.md` (findings A01–A74 and a roadmap). |
| `scripts/` | `derive_scoring_refs.py` and `backtest_forward_rent.py` (both `--check` the scoring file), `ev_sensitivity.py` (one-at-a-time priors sensitivity), `crosscheck_echo_gridlock.py`. |
| `tests/` | Offline tests. `fixtures/spp_synth/` holds fabricated data that is never publishable. `fixtures/real_samples/` holds small verbatim cuts of the real sources, including a 59-flowgate subset of snapshot `2026-10-13.r2`. `fixtures/spp_synth_fingerprint.json` pins the synthetic ranking. |
| `.github/workflows/tests.yml` | CI runs on every push and PR: Python 3.12, `pip install -r requirements.lock -e .`, `pytest -q`. |
| `pyproject.toml`, `requirements.lock` | Dependencies. The lock is the exact environment of the verified run. |
| `data/` | Not in git. Builds and runs create `raw/spp/<snapshot>/`, `processed/` and `replayed/`. |
| `vercel.json` | Turns off Vercel builds. The repo has no web entrypoint. |

Key modules in `src/headroom/`:

- `cli.py`: `headroom run` and `headroom reproduce`.
- `pipeline.py`: ingest, then Bucket A, Bucket B and the model, on one lineage store. `check_stamp_pins` refuses snapshot stamps that differ from today's pins.
- `export.py`: writes the run, decides shareable or internal, and swaps files in atomically from staging.
- `ingest/adapters/build_snapshot.py`: fetches, normalizes, validates and activates a dated snapshot.
- `ingest/adapters/{hifld,eia,pudl_ferc1,spp}.py`: one adapter per upstream. `common.py` has `require_pin`, `write_table` and the downloader.
- `ingest/snapshot.py`: validates snapshot manifests (hashes and stamps).
- `ingest/load.py`: reads tables into `Fact`s. `ingest/region.py` is the region registry (`spp`, `spp-synth`, `miso-synth`).
- `quality/`: triangulation, invariants, confidence, and `report.py` (`quality_report.json`).
- `reconstruct/`: congestion rent, the DC/PTDF screen (`network_model.py`, inert on SPP because locations are CEII), planning exhaust, `Range` bounds.
- `model/ev.py`: the EV decision and the GET label.
- `model/forward.py`: forward rent.
- `model/get_match.py`: the evidence rule (`evidence_rule_get`), the `get_basis` text and the same-station caution.
- `model/run.py`: the model driver. It applies EV over the evidence rule.
- `model/signals.py`, `score.py`, `stability.py`: the weighted composite (a diagnostic under `ranking: ev`).
- `model/references.py`: the reference rule and its guard.
- `provenance/envelope.py`: `Fact` and `Range`. `lineage.py`: the lineage store and taint walk. `gate.py`: the publishable gate.
- `provenance/reproduce.py`: replay. `runtime.py`: the runtime and source hash. `digests.py`: the ranking digest. `versions.py`: the run manifest.
- `provenance/real.py`, `synthetic.py`, `derived.py`: envelope constructors.
- `sources/registry.py`: parses `sources.yaml`. `publishable` defaults to false.
- `schema/entities.py`: core entities.
- `app/`: the Streamlit map. It needs `.[app]` and cannot place SPP flowgates.
- `mcp/server.py`: a read-only MCP surface. It needs `.[mcp]`.
- `demo.py`: the Phase 0 end-to-end demo.

## Setup, run and test

These commands were run on 2026-10-21 in a fresh clone of main (`f707549`), on macOS
arm64 with Python 3.12.0. CI runs them on Ubuntu with Python 3.12. The real-data digest
has been checked on macOS only.

```bash
git clone https://github.com/jonathantsang26/headroom.git && cd headroom
python3.12 -m venv .venv                          # ~2 s
.venv/bin/pip install -r requirements.lock -e .   # ~22 s
.venv/bin/python -m pytest -q                     # ~45 s: 403 passed, 2 skipped
```

- Install from the lock, not `.[dev]`. The run manifest pins numpy, pydantic, PyYAML and the interpreter, and replay checks them.
- The tests are offline. The 2 skips are in `tests/test_region_scoring.py`. They need `data/raw/spp/2026-10-13.r2`, which is not in git, so they skip on every fresh clone and in CI.

**Offline run (fabricated data, never shareable)**

```bash
.venv/bin/headroom run --region spp-synth --out data/processed/spp-synth      # <1 s
.venv/bin/headroom reproduce --manifest data/processed/spp-synth/run_manifest.INTERNAL.synthetic.json \
  --out data/replayed/spp-synth                                                 # <1 s
```

It prints `mode=internal-synthetic`, and every file it writes ends in `.INTERNAL.synthetic`.

**Real SPP run from a fresh public snapshot (network, no keys, about 10 minutes)**

1. Build the snapshot:

   ```bash
   D=$(date +%F)
   .venv/bin/python -m headroom.ingest.adapters.build_snapshot --date $D
   ```

   - It downloads about 141 MB into `data/raw/spp/$D/raw/`: HIFLD 23.8 MB, EIA via PUDL 102.3 MB, PUDL FERC 1 12.4 MB and SPP 3.0 MB.
   - On main it fails after about 3 minutes, like this:

     ```
     [FAILED] spp_queue: upstream serves 'spp_giq_2026-10-20' but config/sources.yaml pins 'spp_giq_2026-10-12'. Re-pin deliberately; never build on a silent change.
     ```

   - SPP re-dates its GI queue CSV (its "Last Updated On" line) on every refresh. Every other pin held on 2026-10-21.
   - The README says a build takes about a minute. Here it took about 4.
2. Re-pin the queue, in your working tree only:
   - In `config/sources.yaml`, set `spp_queue.source_version` to the value the error names.
   - Run `.venv/bin/python -m headroom.ingest.adapters.build_snapshot --clean-staging` to remove the failed staging folder (`data/raw/spp/.<date>-<random>`).
   - Build again with the same command (about 4 minutes).
3. Run the model (about 65 s):

   ```bash
   .venv/bin/headroom run --region spp --out data/processed/spp-$D
   ```

   It prints `mode=shareable`, `2072 flowgates, 892 corridors, shortlist k=103` and `53 flowgates have a GET worth deploying`. The top ten match the README table.
4. Replay it (about 65 s):

   ```bash
   .venv/bin/headroom reproduce --manifest data/processed/spp-$D/run_manifest.json --out data/replayed/spp-$D
   ```

   It prints "Historical replay verified" and lists the artifacts that are byte-identical.
5. Check the scoring file (under 1 s each; both print `OK`):

   ```bash
   .venv/bin/python scripts/derive_scoring_refs.py data/raw/spp/$D --check config/regions/spp.scoring.yaml
   .venv/bin/python scripts/backtest_forward_rent.py data/raw/spp/$D --check config/regions/spp.scoring.yaml
   ```

6. Undo the re-pin with `git restore config/sources.yaml` before you run tests or commit.

**Expected numbers, and where to read them**

- Ranking digest: `run_manifest.json` → `output_sha256` = `a6750ade345c80802c8bf36a9f1570a5560c64b2fa8efed6bd8f78378d7f3cf9`.
- `run_manifest.json` → `n_constraints` 2072, `shortlist_k` 103, `mode` `shareable`.
- `quality_report.json` → `ev.n_with_a_get_worth_deploying` 53, `ev.n_unobserved_rent` 18, `ev.priors_sha256` `d2dbd3ca81d6ed8e24049e3209b059a475869eea4b61bef2472a91e77798a645`.
- `scores.json`: the 53 rows with `ev_mean_musd > 0` read DLR 45 and TO 8.

**The re-pin trap**

- If you run the tests with the re-pin still in place, you get 33 failed and 11 errors, all `Snapshot stamps differ from the registry pins: queue: spp_queue spp_giq_2026-10-12 (pinned spp_giq_2026-10-20)`. The committed test subset (`tests/fixtures/real_samples/spp_snapshot/2026-10-13.r2/`) carries the old stamp.
- Once you restore the pin, the new snapshot can neither run nor replay:
  - `headroom run` stops with `Snapshot stamps differ from the registry pins`.
  - `headroom reproduce` stops with `Manifest is not reproducible: source registry changed or is incomplete`.
- A run replays only under the pins it was built with. Note the queue pin you used.
- A committed re-pin has to re-stamp or rebuild the test subset in the same commit (README). That is work item (d). Never do it as a side effect.
- The re-pin did not move the digest. The queue feeds only `demand_pressure`, which is unobserved for every SPP flowgate.

## Guardrails for agents

**Claims** apply everywhere you write: code comments, docs, README, commit messages, PR text and outputs.

- Scope every claim to SPP's 2025 day-ahead market. Describe the forward-rent test as "held out within 2025 only".
- Never say the model or the ranking is validated, backtested, reconciled, proven or confirmed. `scripts/backtest_forward_rent.py` has "backtest" in its name; say what it does instead: a held-out-months test within 2025.
- Never say anything is deployed, or that Headroom has users, pilots, partners or customers. Never say that RMI or any other organization reviewed or validated it. Never describe anyone's role (for example "co-founder").
- The priors are cited judgement, not data. Say so wherever a number depends on them.
- Never present 38,000 miles, 81 corridors or 668,139 rows as Headroom's numbers. They describe echo-gridlock's reading (`docs/CROSSCHECK.md`).
- Never headline "103 flowgates". 103 is the shortlist cut k. 53 flowgates have a GET worth deploying, and the other 50 are a watch list.

**Locations are CEII**

- SPP names monitored elements by market-model bus, and the bus-location model is CEII. Headroom maps 0 of 2,072 flowgates.
- Never geocode, name-match or otherwise infer where a monitored element is, and never publish a location.
  - A name-matching probe found unique HIFLD matches for 28 of 2,072 flowgates and 0 of the 53. It was rejected.
- Any figure of flowgates says "not a map".
- Never route CEII or any other non-publishable data into a shareable path. `ferc_form_715` stays `publishable: false`.

**Provenance and exports**

- Keep every gate fail-closed. That covers:
  - the default `publishable: false` (`sources/registry.py`);
  - the taint walk (`provenance/gate.py`);
  - `pipeline.check_stamp_pins` and `common.require_pin`;
  - snapshot hash checks;
  - `.INTERNAL` suffixes;
  - exit code 2 when a live export is refused.
- Never weaken a gate to make a run shareable. Never relabel old bytes with a new pin.
- Every analytical value is a `Fact` or a `Range` (`provenance/envelope.py`). Only leaf envelopes name a registered source. Derived envelopes carry a stage label and non-empty `inputs`.
- Unobserved is never zero. Flag a gap; never drop it, impute it or fabricate a join.

**Scoring and priors**

- Never change scoring or priors silently. A change that moves the ranking digest needs all three of these:
  - a stated reason;
  - a test that pins the new behaviour;
  - a PR note with the digest, the 53-count, the GET mix and the top ten, before and after.
- Adding a field to `Score` moves the digest even when no rank changes, because the digest hashes every score row (`provenance/digests.py`).
- If you edit `config/get_priors.yaml`, bump its `version` and update `docs/GET_PRIORS.md`. Its sha256 goes into every run manifest.
- If the synthetic fingerprint changes on purpose, update `tests/fixtures/spp_synth_fingerprint.json` in the same commit and name the change in the commit message.
- Editing anything under `ingest`, `quality`, `reconstruct`, `model`, `provenance`, `schema` or `sources`, or `pipeline.py` or `export.py`, changes the runtime hash. Recorded runs then replay only at their own commit, so say so in the PR. `cli.py`, `app/`, `mcp/` and new top-level modules such as `figure.py` are outside the hash.
- After touching references or forward rent, run both `--check` scripts.

**Repo hygiene**

- Never commit secrets, `.env` files or data. Never open `.env` files.
  - `.gitignore` already excludes `data/raw`, `data/interim`, `data/processed`, `data/replayed`, `*.parquet` (except `tests/fixtures/**/*.parquet`), `*.duckdb` and `run_manifest.json`.
  - Keep those ignored, and stage files by name, not with `git add -A`.
- Fixtures are small verbatim cuts of real sources. Never edit a real value in them.
- Work on a branch and open a PR. Never push to main, never force-push, and never move or rewrite tags.
- Keep the suite green: run `.venv/bin/python -m pytest -q` before every commit, and add tests for every change in behaviour.
- If you cannot reach the public sources, say so in the PR and report the offline results. The maintainer runs the real digest.

## Current state

- `main` = tag `v0.1` = `f707549` (2026-10-14). The remote branch `claude/real-public-snapshot` points at the same commit.
- The suite: 403 passed, 2 skipped.
- The verified run is in README Status: snapshot `2026-10-13.r2` at commit `45a79a0`. Fresh public snapshots built on 2026-10-20 and 2026-10-21 reproduced the digest `a6750ade…`, each with a local queue re-pin.
- Branch `site/congestion-map` is at `e305e08`, one commit on top of main. It is **not pushed**; it exists only on the maintainer's machine. It adds `headroom figure --run DIR [--out OUT]`:
  - It reads one finished run's manifest and `scores.json`, and checks `scores.json` against the recorded sha256. It never re-runs the model.
  - It writes `congestion_field.json`: every flowgate by hours bound (x) and SPP's posted congestion dollars (y), plus the counts, the GET mix of the 53, the run's digest and the priors sha.
  - The 18 flowgates without posted dollars stay blank. A refused run writes `congestion_field.INTERNAL.json` (or `.INTERNAL.synthetic.json`) only.
  - Files: `src/headroom/figure.py` (new, 144 lines), `src/headroom/cli.py` (the `figure` subcommand), `tests/test_figure.py` (4 tests), a section in `docs/OUTPUTS.md` and one README line. All of it is outside the runtime hash.
  - The suite at `e305e08`: 407 passed, 2 skipped.
  - The maintainer's personal site builds its research figure from this JSON (from the 2026-10-20 run). That build asserts the counts 2,072 / 2,054 / 53 / 18 and the GET mix {DLR 45, TO 8}. A PR that changes any of them must say so.

## Work queue

The tasks are in priority order, and each one is a PR.

### (a) Technology concentration: why the leaders all read DLR

**Context**

- The top five, and 45 of the 53, read "Dynamic Line Rating". The other 8 are transformers and read TO. APFC and Combination never win among the 2,054 scored flowgates.
- It is not a bug. A closed-form rebuild of the decision from a run's manifest and `scores.json` matched all 2,054 labels.
- It is an artifact of the priors plus the missing per-element data. The label depends on element type, not on the flowgate's own data.

**Mechanism** (line numbers at `f707549`)

1. Where a GET applies, its expected net value is `mu_g = E[u_g] × relief_scale × E[h] × forward_rent − E[C_g]` (`model/ev.py:234-244`).
   - Forward rent = 2025 rent × months_binding/12 × E[ε], where E[ε] = 1.22 (`ev.py:228-232`, `model/forward.py:44-63`).
2. The priors (`config/get_priors.yaml`) at their triangle means:
   - Relief share is nearly equal: DLR 0.083, APFC 0.087, TO 0.080.
   - Annual cost is not: DLR $0.131M, TO $0.365M, APFC $1.773M.
   - The haircut h has mean 0.327 (`:16-19`).
   - The blocks: DLR `:22-40`, APFC `:42-58`, TO `:60-78`.
3. Applicability depends on the evidence class (`ev.py:130-145`). It is drawn independently per element and GET (`ev.py:259-260`).

   | Evidence class | DLR | APFC | TO |
   |---|---|---|---|
   | `line_n1` | 0.40 | 0.40 | 0.30 |
   | `transformer_n1` | 0.00 | 0.20 | 0.30 |
   | `interface` | 0.30 (relief × 0.35) | 0.25 | 0.30 (relief × 0.5) |

4. The decision and the label:
   - In each draw, the model learns which GETs apply, then deploys the applicable one with the highest `mu`, or nothing (`ev.py:265-270`).
   - The label is the option that contributes most to `ev_mean` (`ev.py:311-316`). `run.py:239-247` copies it into `recommended_get`.
   - The 18 flowgates without posted dollars keep the evidence rule's pick (`run.py:248-249`).

**Why DLR always wins on a line** (closed form):

- DLR always beats TO: `mu_DLR − mu_TO = 0.0033·h·R + 0.234`, which is positive at any rent.
- APFC's extra 0.3 points of relief would cover its extra $1.64M/yr only at a forward rent above about $1,508M/yr. SPP 2025's largest is about $118M.
- Combination is worth more per deployment above about $68M, but it applies in 0.16 of draws against DLR's 0.24. It would win the label only above about $138M of forward rent.
- So lines and interfaces read DLR, and transformers read TO (DLR applicability there is 0). The data decides only whether a flowgate clears zero, and its rank. Both come from forward rent.

**What the single label hides**

- For #1 to #4, the share of `ev_mean` by the option deployed is: DLR 34–39%, DLR+APFC 30–32%, TO 22–25%, APFC 6–12%. Nothing is deployed in 25% of draws.
- For #5 (`LN FT_CAL - FT_CAL`): DLR 76%, TO 24%. Nothing is deployed in 42% of draws.
- The evidence rule disagrees. It picks APFC for 52 of the 53 and Combination for 1, because it reads a branch outage as proof of a parallel path (`get_match.py:49-52`). Only `evidence_rule_get` keeps its pick.
- #5 carries the same-station caution (`get_match.py:90-92`). 16 scored "lines" carry it, and 1 of them is in the 53. They still get DLR applicability 0.40.

**Sensitivity of the label** (closed-form estimates from scratch work; re-derive them, for example with `scripts/ev_sensitivity.py --region spp`, before you quote them):

| What-if | Worth deploying | Labels |
|---|---|---|
| Baseline | 53 | DLR 45, TO 8 |
| u_DLR halved (0.01 / 0.04 / 0.075) | 34 | TO 20, DLR 14 |
| C_DLR × 3 | 24 | TO 18, DLR 6 |
| C_APFC at 0.16 / 0.40 / 0.80 | 53 | DLR 39, TO 8, Combination 5, APFC 1 |
| P_APFC(line_n1) = 0.60 | 53 | DLR 41, TO 8, Combination 4 |
| P_DLR(line_n1) = 0.20 | 53 | DLR 41, TO 12 |

**The recommended model change** (decide on each item; don't adopt them all by default):

1. Report the decision split, not one label: each option's share of `ev_mean`, and P(nothing deployed).
   - `ev.py` already computes `option_value` and `p_best`; only the export, `docs/OUTPUTS.md` and the README table need them.
   - If one word is needed, use "check first", not "recommended". This is GET_PRIORS.md §10 item 2.
2. Give DLR's relief share per-flowgate evidence. The SPP adapter already reads every binding hour (`ingest/adapters/spp.py:339-360`).
   - A high share of summer-afternoon binding hours would scale u_DLR down, and wind-export binding would scale it up.
   - This is the change that would let labels differ between lines. A new congestion column must be stamped and documented in `docs/ADAPTERS.md`.
3. Make the two pickers agree. `evidence_class` does not separate branch from non-branch outages.
   - Either add a branch-outage class with higher APFC and TO applicability,
   - or drop the parallel-path argument from the evidence rule.
4. Add the AAR shrink to DLR as a per-GET forward-rent multiplier (GET_PRIORS.md §4.1 and §10 item 7).
5. Let a caution change the decision. Same-station rows should get DLR applicability 0, or no label.

Also weigh GET_PRIORS.md §10 item 8: a per-GET haircut, or a correlation between u and h.

**Files:** `model/ev.py`, `model/run.py`, `model/get_match.py`, `config/get_priors.yaml`, `export.py`, `docs/OUTPUTS.md`, `docs/GET_PRIORS.md`, the README (the table and the GETs paragraph), `tests/test_ev_model.py`, `scripts/ev_sensitivity.py`.

**Acceptance**

- A written decision: which of items 1–5 you adopt, which you reject, and why. Put it in the PR and in `docs/GET_PRIORS.md`.
- Tests for each adopted item. For example:
  - option shares sum to `ev_mean` on a small fixture;
  - a same-station line cannot read DLR;
  - the new evidence class is assigned correctly.
- The digest before and after. Explain every change. If only export columns change, show that the digest did not move.
- The 53-count and GET mix before and after. Update the README table from a real run.
- The suite is green. Re-run `scripts/ev_sensitivity.py --region spp` if the priors or classes changed.
- **Suggested PR title:** "EV: report the GET decision split instead of a single label" (adjust it to the decision).

### (b) Land parcels and right-of-way: design proposal first

**Context**

- GETs work on the wires already there. DLR, TO and most APFC need no new land, which is a reason to try them first.
- Bigger fixes need right-of-way, parcel by parcel: reconductoring, new lines, substation expansion. Headroom does not model land.
- Flowgates cannot be placed (CEII), so a land score cannot attach to a flowgate. It can attach to:
  - corridors with public geometry (HIFLD lines);
  - named projects with public endpoints, such as SPP ITP upgrades and queue network upgrades that name both stations and the county;
  - a flowgate that a transmission owner locates under a CEII agreement (internal only, never shareable).

**Suggestions to work from**

1. Give every fix a land class:
   - none: DLR and TO;
   - pad: APFC (tower-mounted, or inside an existing substation fence);
   - existing right-of-way: reconductoring;
   - new right-of-way: new lines, and expansion past the fence.

   That is one column beside `get_candidates`. It needs no locations, so do it first.
2. Add the next fix to the comparison. Score reconductoring and new lines with two land terms:
   - acquisition cost: acres × the county's $/acre;
   - delay: years of rent lost before the fix is in service. A GET takes 1–2 years; a line takes 7–10.

   Land-free fixes then show up as value.
3. Count the parcels and distinct owners along a corridor, not just its acres. Holdouts and condemnation scale with owners.
   - Sources: county assessor parcel GIS (many Kansas, Nebraska and Oklahoma counties publish it free), Regrid (paid, nationwide; check its licence before any shareable use), PAD-US, and BIA land-area representations.
4. Reward reuse of existing corridors with a "share of route inside an existing corridor" term.
   - Sources: HIFLD's public transmission, rail and pipeline layers, and state DOT highway right-of-way maps.
5. Screen out land that is slow or closed to build on.
   - Sources: USFWS wetlands (NWI), USFWS critical habitat, PAD-US, and USDA's Cropland Data Layer.
   - A flag raises the delay or removes the route. It never adds a dollar guess.

**Files:** `model/get_match.py` and `model/ev.py` (where `get_candidates` is built), `export.py`, `config/sources.yaml` (for draft entries), `docs/ADAPTERS.md`, `docs/BLIND_SPOTS.md` §1 and §6.

**Acceptance**

- PR 1 is a design document only (for example `docs/LAND.md`). No code, no digest change. It covers:
  - each data source, with its licence and a draft `sources.yaml` entry (`publishable: false` until its terms are checked);
  - what a land score attaches to;
  - the scoring terms;
  - how the design stays clear of CEII;
  - what stays unobserved.
- Never link a HIFLD line to a flowgate by name in order to attach land to it.
- An optional PR 2 adds the land-class column (item 1) with tests. The digest stays unchanged unless the column goes on `Score`; if it does, say so.
- **Suggested PR title:** "Design: land and right-of-way for fixes beyond GETs".

### (c) Land the `headroom figure` export

**Blocked until the maintainer pushes `site/congestion-map`.** Check with `git ls-remote --heads origin site/congestion-map`.

**Files:** `src/headroom/figure.py`, `src/headroom/cli.py`, `tests/test_figure.py`, `src/headroom/provenance/runtime.py`, `docs/OUTPUTS.md`, the README.

**Acceptance**

- The branch is rebased on current main, with conflicts resolved.
- `figure.py` is reviewed for:
  - the sha256 check on `scores.json`;
  - the mode and suffix rules (a shareable run must be `public_only`, and the manifest name must match its mode);
  - blanks that never become zero;
  - `worth_deploying` (`ev_mean_musd > 0`) matching `quality_report.json` → `ev.n_with_a_get_worth_deploying`.
- Tests:
  - the 4 tests in `tests/test_figure.py` pass;
  - a CLI test is added (exit code and output path);
  - the suite reads 407 or more passed.
- The `docs/OUTPUTS.md` section and the README line are accurate.
- The digest is unchanged, and recorded runs still replay. Confirm that `figure.py` and `cli.py` are outside the hash in `provenance/runtime.py`.
- If item (a) has landed and changed the labels or added option shares:
  - decide whether the JSON carries them;
  - bump `SCHEMA` (`headroom.congestion_field/1`) if the format changes;
  - flag the downstream count assertions.
- **Suggested PR title:** "headroom figure: congestion field JSON for one finished run".

### (d) Snapshot robustness: SPP re-dates its queue file

**Context**

- The queue's version is its CSV's "Last Updated On" date (`ingest/adapters/spp.py:106-112`). It is checked at fetch (`:136`) and at normalize (`:581`).
- SPP refreshes the file often, so main cannot build a fresh snapshot without a hand re-pin.
- A committed re-pin breaks the committed test subset (33 failed, 11 errors) and the archived `2026-10-13.r2`.
- The queue does not enter the EV ranking.

**Options** (state each one's cost to fail-closed):

1. A rolling pin for sources whose upstream version is a date. Pin the source and format, record the observed date in the stamp and the manifest, and accept any date on or after the pin. It must not apply to DA-BC, congestion by constraint, TCR, HIFLD or PUDL.
2. Let a build reuse a hash-checked queue file recorded by an earlier snapshot, like `--renormalize-from`, and keep the pin.
3. Make fixtures carry the pins they were built under, so tests check stamps against the fixture's registry rather than today's.
4. A documented re-pin helper that edits the pin and re-stamps the test subset in one commit.

**Files:** `ingest/adapters/spp.py`, `ingest/adapters/common.py` (`require_pin`), `pipeline.py` (`check_stamp_pins`), `ingest/snapshot.py`, `ingest/adapters/build_snapshot.py`, `config/sources.yaml`, `tests/fixtures/real_samples/spp_snapshot/2026-10-13.r2/snapshot_manifest.json`, `tests/test_snapshot_builder.py`, `docs/SNAPSHOTS.md`, README "A fresh build".

**Acceptance**

- On main, a fresh `build_snapshot` succeeds without a hand edit of `config/sources.yaml`, or it fails with a one-command fix.
- The committed test subset still loads, the suite is green, and no new skips appear.
- Every other source still fails closed on a version change. Add a test for it.
- Replay still refuses a run whose consumed stamps differ, so the manifest must record the queue version actually read.
- The digest is unchanged (`a6750ade…`).
- `docs/SNAPSHOTS.md` and README "A fresh build" are updated, with a realistic build time (about 4 minutes).
- **Suggested PR title:** "Snapshots: build through SPP's queue re-dating without a hand re-pin".

### (e) Still open from BLIND_SPOTS and the architecture review

Take one item per PR. For the large ones, propose a design before you write code.

- **Acceptance for each item:**
  - the PR (or design note) names the finding it closes (for example A18, A28 or A63) or the BLIND_SPOTS section;
  - tests for the change;
  - the digest before and after;
  - the matching doc is updated (BLIND_SPOTS, SNAPSHOTS, FORWARD_RENT or the review's addendum).
- **Suggested PR title:** `<area>: <what>`, naming the finding, for example "Lineage: add() refuses conflicting envelopes (A18)".
- **Files:** each finding's section in `docs/reviews/ARCHITECTURE_REVIEW_2026-10-13.md`, and `docs/BLIND_SPOTS.md`.

- **Cross-year forward rent.** Sources: BLIND_SPOTS §5, FORWARD_RENT "Limits", review A63 and roadmap item 7.
  - Make the data year a build parameter, with year-keyed pins. Build a 2024 snapshot, then test 2024 → 2025 persistence.
  - Until that exists, every claim stays "held out within 2025 only". This item is large; design it first.
- **The lineage residual (review A18).** `LineageStore.add()` overwrites silently, and envelopes are mutable. Make `add()` raise on conflict and freeze the envelopes. The digest should not move; show that it didn't.
- **Release bundle and commit identity (review A28, roadmap item 10).** Record the git commit, tree and dirty flag in the run manifest. Publish the run manifest, the snapshot manifest and `normalized/` as release assets. Editing `versions.py` changes the runtime hash.
- **Planning is header-only (BLIND_SPOTS §2).** SPP ITP is PDF only.
  - A pinned, reproducible extraction of ITP constraints and projects would light up the planning signal, and it would feed item (b).
  - Check the PDF's terms and how to pin it.
- **Real-time congestion and outages are not read (BLIND_SPOTS §2).** Adding real-time data changes the rent definition, and with it the digest.
- **Transformers are keyed by substation (BLIND_SPOTS §5).** Banks at one substation share a row and their applicability draws.
- **`cost_of_inaction` is sampled independently of rent (BLIND_SPOTS §5).**

Element locations and ratings are CEII. Public data cannot fix them, so don't try.

## How to report

- Open one PR per task, from a branch off current main. Use the suggested title.
- The PR description covers:
  - what changed and why, naming the work-queue item;
  - the tests you ran, with the exact commands and pass/skip counts;
  - the ranking digest (`output_sha256`) before and after, with the snapshot and queue pin you used, or "not run" and why;
  - the 53-count and GET mix before and after, if the model changed;
  - the runtime-hash impact: whether recorded runs still replay;
  - open questions for the maintainer.
- In the same PR, append one dated line to the Log below, in this form: `YYYY-MM-DD · <branch> · <what changed> · digest <unchanged | old → new>`.
- Never merge your own PR.

## Log

- 2026-10-21 · docs/agent-work · Added docs/AGENT_WORK.md. Verified the setup, the tests (403 passed, 2 skipped) and a fresh SPP build on 2026-10-21 with a local queue re-pin · digest unchanged (`a6750ade…`).
- 2026-10-21 · claude/tender-brown-1l12ow · Work item (a): export the GET decision split (`ev_from_dlr/apfc/to/c_musd`, each option's part of ev_mean) beside the label; decision on items 1–5 in GET_PRIORS.md §10.1 (1 adopted, 2–5 deferred). Offline only (SPP unreachable from the session) · digest unchanged on spp-synth (`2253ae9c…`) and on the committed real subset under SPP priors (`00c3fcd7…`); real-run digest not run.
