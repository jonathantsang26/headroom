# Headroom architecture review

> **Addendum (after the review, through commit `7a3bd1b` and the 2026-10-13.r2 run).**
> The status lines below are as of `aae5e26`. Later commits address these findings:
>
> - **A61** (priors never committed) and **A66** (committed scoring fails its check):
>   `7a3bd1b` commits `config/get_priors.yaml` (cited, `docs/GET_PRIORS.md`) with the
>   SPP scoring file and adds a test that every region's scoring file loads from tracked
>   files. `f3abce5` makes the reference guard fail closed without a congestion sha.
> - **A62 / A63** (one year as forward value; no out-of-sample path): `26cd6db` adds
>   forward rent for EV (rent × months-binding share × a ratio drawn from a held-out-months
>   backtest). `scripts/backtest_forward_rent.py` derives and `--check`s it, and
>   `docs/FORWARD_RENT.md` gives the results. A cross-year harness is still open.
> - **A18** (laundering): `a8c894b` removes `inputs` from `real_range`/`synthetic_range`.
>   The export now refuses a derived node stamped with a non-publishable registered
>   source. Still open: `add()` overwriting silently, and mutable envelopes.
> - **A67** (CBC join gate): `e9a46a3` adds build tripwires on the monthly CBC/TCR ratio
>   and the joined share of |shadow price|.
> - **A70 / A71**: `f791f70` makes MCP use the run's rank order and states the shortlist
>   rule in OUTPUTS.md. `574f2d5` makes `recommended_get` the GET with the highest
>   expected value if pursued (the priors report's fragility note).
> - **A02 / A28 / A58** (stale published numbers, third-party reproduction, stale docs):
>   README Status, CROSSCHECK, SNAPSHOTS and BLIND_SPOTS were rewritten from the
>   2026-10-13.r2 run. `requirements.lock` pins the environment. A release bundle and a
>   recorded git commit in the manifest are still open.

**Repository:** `claude/real-public-snapshot`, worktree `determined-hawking-c7208e`.

**Review base.** The brief named 90b8046. The lens reviews ran between 90b8046 and d12f6fe, and the verifiers re-checked against heads up to aae5e26. Five commits landed during the review and address findings directly: b850940, 4090a95, a2b3514, 32b0906 and aae5e26. Earlier mid-review commits (bff0d75, 7606c04, 316a129, ebf52d4) also changed affected code. Every finding below therefore carries a **Status at aae5e26** separate from its severity.

**Basis for fix status.** I read the commit diffs. I ran the full suite in a clean `git archive aae5e26` copy (378 passed, 1 skipped in 35 s; the brief's "336" never held, and it was 326 at 90b8046). I re-probed A18 at aae5e26. I did not re-test the other fixes. The uncommitted change to `config/regions/spp.scoring.yaml` is not credited as a fix anywhere.

---

## 1. Executive summary

The provenance core is sound and mostly fails closed: hash-then-parse snapshot loading from one resolved `current`, atomic builds and activation, a registry that defaults to non-publishable, derived values that cannot drop their lineage and, since b850940, a gate over every store leaf plus a load-time check that stamps equal today's pins. The weak points are at the edges, where identity is a label rather than content (pins built from config, keys from SPP display text, snapshot ids from directory names, replay identity over the whole registry and package), so nothing ties a published claim to something anyone else can re-derive. At aae5e26 the documented live path is broken twice while the suite stays green, because no test loads the shipped SPP configuration: `headroom run --region spp` crashes on `config/get_priors.yaml`, which was never committed (A61), and the README replay recipe fails the new pin check while README Status still quotes the superseded yearly-build figures (1,601 flowgates, digest `ed7355d5…`; A02). The headline output, a forward $M/yr EV ranking, rests on one backward-looking year inside an uncalibrated 0.6–1.5× band; a within-2025 split puts only 13.8% of the leaders' next-half outcomes inside that band and gives H1-to-H2 top-5% overlap of 0.31 (A62), and the architecture has no harness to test this across years (A63). Because element locations are CEII, the DC/N-1 screen, radial test, demand pressure and Bucket A never run on SPP, which leaves 0.50 of the composite weight unobserved and several latent correctness bugs in code that has never touched live data. There are no critical findings: 5 high (A01 fixed and A06 largely fixed at HEAD; A61, A62 and A63 open), 29 medium and 40 low, and at aae5e26 38 of the 74 are fixed, superseded or partly fixed.

**The five most important actions**
1. Commit the real GET priors together with the working-tree `spp.scoring.yaml`. Add a test that calls `ScoringConfig.load` for every region's `scoring_path`. Make the reference guard fail closed when `congestion_sha256` is missing. (A61, A66, A44)
2. Regenerate the published SPP result at HEAD from the monthly snapshot (2026-10-13.r2). Publish a release bundle: the run manifest, the snapshot manifest plus `normalized/`, and a tag. Record the git commit in the manifest, and rewrite README Status and the replay recipe. (A02, A28, A03, A58, A74)
3. Build the out-of-sample harness: data year as a build parameter, year-keyed pins, and `headroom backtest`. Commit the within-year result. Until the forward step is calibrated, label EV and rent as "2025 observed × priors". (A63, A62, A68)
4. Close the last gate-laundering path, where a non-leaf node's own registered source is ignored. Freeze envelopes. Add golden and score-discriminating taint tests on the real subset. (A18, A10, A11, A44)
5. Add a fitness axis to the export decision that can force INTERNAL with a named reason, separate from publishability. It should cover the CBC join rate and CBC÷TCR ratio, the rent definition in force, priors provenance and coverage. (A67, A69, A43, A61)

---

## 2. Architecture as built

### 2.1 Components and data flow

```
UPSTREAM (public HTTPS)   HIFLD ArcGIS  EIA-930/860M  PUDL FERC1  SPP DA-BC/CBC/TCR, GI queue
                               |             |            |              |
BUILD  ingest/adapters/build_snapshot (fixed order hifld, eia, pudl_ferc1, spp)
  B1 fetch: common.download -> raw/ (temp+fsync+rename, size cap, Content-Length);
     manifest.raw = {url requested, sha256, bytes, retrieved_at}
  B2 require_pin: HIFLD edit date + queue 'Last Updated On' read from the artifact;
     DA-BC/CBC/TCR, EIA, PUDL pins built from config                   [A19]
  B3 normalize -> 9 normalized/*.csv (deterministic bytes); write_table stamps
     source, version, retrieved_at, sha256, coverage, note, hand-declared
     contributors, column_sources                                     [A45, A47]
  B4 mkdtemp staging -> rename YYYY-MM-DD[.rN] -> atomic `current` swap (no log) [A74]
     snapshot_manifest.json: no builder identity, no raw->table links  [A05]
                               |
RUN    pipeline.run_pipeline(region)
  R1 load: resolve `current` once; hash and parse the same bytes; path containment;
     stamp well-formedness; check_stamp_pins (live, b850940)
  R2 every value -> Fact/Range + SourceStamp in one mutable LineageStore  [A18]
  R3 Bucket A: triangulate kV, flag, confidence (no path to SPP scores)    [A27]
  R4 Bucket B: rent = observed CBC $ or sum|SP| x 100-1200 MW band (by table
     shape) [A69]; hours; DC/N-1 screen (not_run on SPP: CEII); planning
     (header-only); queue
  R5 model: 6 signal Ranges -> composite Monte Carlo -> p_top_k; EV Monte Carlo
     (u, h, C x GET priors) -> rank by ev_mean when ranking: ev [A61, A62];
     evidence GET rule; corridor dedup (champion + sum views)
                               |
DELIVER export.deliver(out)
  D1 gate: composite + EV closures, `table:` leaves, every store leaf, dangling
     inputs -> shareable | .INTERNAL.synthetic (by source name; non-leaf own
     source ignored) [A18, A46]
  D2 scores.json, shortlist.csv/.json, quality_report.json, NOTICE.md, parquet:
     atomic per file; run_manifest last (pins, inputs, runtime + source sha,
     scoring + priors content/sha, ranking digest, per-file sha, export_decision);
     refuses a different run in the same directory (aae5e26)
READERS  CLI report (exit 2 when a live export is refused) | reproduce (pins,
     runtime, inputs, ranking digest, then mode and 5 artifact digests) | MCP
     (sibling-manifest mode only; own ORDER BY) [A14, A70] | Streamlit
     (re-runs synthetic regions in process) [A33]
```

The generic core never imports an adapter. It reads only `normalized/` and the snapshot manifest. Joins happen inside adapters, coupled through the filesystem: HIFLD uses EIA-860 plants for the footprint and bus ids, FERC joins onto `hifld_lines.csv`, and SPP maps queue POIs onto `buses.csv`. There is no join from constraints to topology, because SPP element locations are CEII.

Configuration is split across:
- `config/sources.yaml` (pins, compliance);
- `config/scoring.yaml` (fixture-era defaults);
- `config/regions/spp.scoring.yaml` (SPP default: EV ranking, p99 references);
- `config/regions/spp.yaml` (read only by adapters);
- the `REGIONS` dict in code (data roots).

At SPP scale (2,072 flowgates in 2026-10-13.r2, about 33–48k lineage nodes), everything except the composite Monte Carlo runs in under 1 s. The composite Monte Carlo takes about 56–60 s at 10k draws.

### 2.2 Trust boundaries

1. **Network to raw.** Covered: TLS verification, size caps, atomic downloads.
   - The manifest records the requested URL, not the final one, and redirects may leave HTTPS (A54).
   - HIFLD raw is re-serialized, not stored verbatim (A20).
   - Integrity records are self-consistency hashes, not signatures or content pins (A19).
2. **Raw to normalized.** Covered: pins checked at fetch and at normalize; byte-deterministic writers.
   - After the build, nothing re-verifies raw files, and the code, region spec or library versions that produced a table are not recorded (A05).
3. **Snapshot to lineage.** This is the strongest boundary:
   - hash-then-parse of the exact bytes (`snapshot.py` `read_tables`);
   - containment of relative paths;
   - well-formed, timezone-aware stamps;
   - since b850940, publishable stamps must equal today's pins (`pipeline.check_stamp_pins`).
4. **Lineage to export.** Covered:
   - `Compliance.publishable` defaults to False, and unknown sources and missing nodes count as offenders;
   - `derived_range`/`derived_fact` refuse empty inputs;
   - the gate walks score and EV closures, `table:` leaves, every store leaf and dangling inputs.
   - Not covered: the gate checks source names, not versions. Versions are now enforced at load for publishable sources only. `source_closure` ignores a node's own source when it has inputs (A18).
5. **Export to consumers.** Covered: NOTICE.md carries terms and caveats (4090a95), and replay compares the export decision and artifact bytes.
   - The MCP server trusts the sibling manifest's mode and ignores the digests (A14).
   - Neither MCP nor Streamlit reads the run's own ordering (A70).

### 2.3 Invariants

- **Provenance.** Every leaf carries a `SourceStamp`. Every derived value names its inputs. Fixture runs stamp every leaf `synthetic_fixture`, so they can never pass the gate.
- **Pins.** Before b850940, pins were enforced only at build and at replay (A01). They are now also enforced at load, but replay still compares the whole registry, not the stamps the run consumed (A03, A59).
- **Coverage.** Each table is `observed`, `partial` or `unobserved`. An unobserved signal is a flagged degenerate zero, never evidence. Three latent paths violate this:
  - indeterminate screen statuses are scored as observed (A25);
  - a queue without the bus join becomes an observed zero (A47);
  - signal coverage has no `partial` level (A48).
- **Determinism.** Seed and `signal_order` are recorded, lineage ids are content hashes, and the output is byte-identical across hash seeds and across code versions on one platform.

### 2.4 The three "recently fixed" items

- **Parquet fixtures ignored by .gitignore: fixed.** Every lens confirmed it. `.gitignore:16` has `!tests/fixtures/**/*.parquet`. At HEAD all 72 fixture files are tracked and archived, and `check-ignore` matches only the negation. Other patterns can still hide fixture types; see A53.
- **Non-atomic exports and missing per-file hashes: fixed per file.** Each file is written to a temp file, replaced with `os.replace`, and hashed; the manifest is written last. All recorded digests matched their files in clean runs.
  - Until 4090a95, nothing read those digests. Replay now compares five of them. MCP still compares none (A14).
  - The set of files is not atomic as a whole (A04 residual).
- **Incomplete yearly DA-BC file (now monthly): fixed in the adapter** (e24272d: twelve monthly members, one member per zip, per-row month check). The monthly-built snapshots 2026-10-13 and 2026-10-13.r2 exist. However:
  - every published number still describes the yearly 2026-10-12 build (A02);
  - until b850940 the loader exported that yearly snapshot as shareable under a manifest that certified the monthly pin (A01).

---

## 3. Strengths (keep these)

- **Fail-closed registry and gate.** `publishable` defaults to False, and a misspelled `publishible: true` stays False. Duplicate names raise. Unknown sources and dangling ids are offenders. A queue contributor stamped `ferc_form_715` blocks the run even though no score reads it. b850940 added a store-wide leaf sweep and a dangling-input check.
- **TOCTOU-safe snapshot loading.** Hash-then-parse of the same buffer, `current` resolved once, path containment that refuses absolute paths, `..` and outward symlinks, and fail-closed stamp parsing. Replay never substitutes a newer snapshot.
- **Atomic build and activation everywhere.** Downloads, manifests, tables, the `current` swap and exports are all atomic. A failed build keeps the old `current` and leaves its staging directory for inspection. Dated snapshots are never rebuilt in place, and same-day rebuilds are `.rN` revisions.
- **Deterministic normalization.** Re-normalizing the archived raw data reproduced every table's sha256 under the matching adapter code. HEAD replays the archived ranking digest exactly.
- **Pins read from the artifact where upstream exposes identity.** HIFLD `dataLastEditDate`, the queue's "Last Updated On", the DA-BC member name plus a month check on every row.
- **Honest coverage vocabulary.** Header-only tables are `unobserved`, never zero-filled. Join failures keep blank keys and match evidence. `unobserved_weight_all` is reported, along with `absent=` (32b0906) and NOTICE caveats.
- **Same-draw bands.** The composite band and p_top_k come from the same draws, so they cannot disagree. The corridor sum view is exact by linearity: expected top-k slots sum to k.
- **Rankings robust to some modelling choices.** Rent correlation structure barely matters (Spearman 1.000/0.999). Member hours rarely overlap (ratio 1.02 across 336 corridors). DA-BC deduplication double-counts in 0 of 136,303 rows.
- **The EV layer has the right shape.** It is vectorised with its own RNG stream, applicability is drawn per element, zero-EV ties are excluded, priors content and sha are recorded and checked on replay, and since 316a129 it ranks by the mean.
- **Strong offline test suite.** 378 tests run with 0 network attempts, recorded real-sample fixtures with READMEs, killed control mutants on the gate and replay paths, and roughly 90% line coverage.
- **Safe parsing choices.** `yaml.safe_load` throughout; no subprocess, eval or pickle; parameterized DuckDB; a single-member zip rule that rules out zip-slip and overlap bombs.
- **Defects get fixed quickly once named.** 316a129, b850940, 4090a95, a2b3514, 32b0906 and aae5e26 each close review findings with tests.

---

## 4. Findings by severity

**How to read a finding.**
- **Verdict** is the verification outcome. All findings are CONFIRMED except A42 and A60, which are DISPUTED.
- **Severity.** Where both verifiers gave the same severity, theirs is used. Where they split, the higher is kept only when its argument rests on behaviour demonstrated against current code; if it depends on a hypothetical consumer, deployment, legal reading or future code change, the lower is used. Both verifier severities are shown. A18 is the one exception, explained in its entry.
- **known** marks items on the brief's redesign list.
- **Status** is at aae5e26.
- Evidence uses the verifiers' corrected versions. Line numbers are at HEAD where a verifier gave them, otherwise at the commit named.

| Severity | Count | Ids |
|---|---|---|
| Critical | 0 | none |
| High | 5 | A01, A06, A61, A62, A63 |
| Medium | 29 | A02, A03, A04, A05, A07, A08, A09, A10, A11, A12, A13, A15, A16, A17, A18, A19, A21, A22, A28, A32, A33, A34, A39, A43, A44, A64, A65, A66, A67 |
| Low | 40 | A14, A20, A23–A27, A29–A31, A35–A38, A40–A42, A45–A60, A68–A74 |

### 4.1 Critical

None. No finding produced a non-publishable value in a shareable export with current code and real data, corrupted data, or broke an integrity guarantee that a user could not detect.

### 4.2 High

#### A01 · Table stamps were never checked against registry pins, so the yearly DA-BC snapshot was exported as shareable under a manifest certifying the monthly pin
- **CONFIRMED · high.** The reviewer said high; verifiers split high/medium. Kept high because the failure was demonstrated through the README's own steps. Not known. **Status: Fixed** (b850940, `pipeline.check_stamp_pins`).
- **Files:** `ingest/snapshot.py:23-39`, `provenance/gate.py:24-28`, `provenance/versions.py:15-24`, `provenance/reproduce.py:112`, `pipeline.py:22-38` (the fix).
- **Evidence:**
  - Archived 2026-10-12 stamps `constraints`/`congestion` as `spp_da_bc_2025`; its only DA-BC raw entry is `DA-BC-YEARLY-2025.csv.zip`. The registry pinned `spp_da_bc_monthly_2025`.
  - At 90b8046, `--activate-only` followed by `headroom run --region spp` gave a shareable export (digest `ed7355d5…`). Its `run_manifest.sources` said monthly, while the `quality_report.json` beside it said `spp_da_bc_2025`.
  - Stamps set to `totally_unpinned_version` still exported as shareable.
  - README step 2 writes into `data/processed/spp-2026-10-12`. That overwrote the one archived manifest whose replay would have failed, and step 3 then verified the new manifest against itself.
  - Correction: this was not a publishability leak, since both editions are publishable, and the adjacent quality report did name the true version. The defect is a false certification that nothing cross-checked.
- **Impact:** in a provenance-first tool, the run manifest asserted inputs the run did not read, and this would recur on every re-pin.
- **Recommendation (residual):** the check covers publishable stamps only. Also record consumed stamps in the run manifest (`inputs.input_stamps`) so replay can compare against them (A03).

#### A06 · "Shareable" was one boolean: SPP's terms, PUDL's CC-BY attribution and the model caveats never reached the exported artifacts
- **CONFIRMED · high.** The reviewer said high; verifiers split medium/high. Kept high: the missing unconditional CC-BY attribution on the shareable output was shown by grep. Whether the commercial-use restriction applies is a legal question I did not rely on. Not known. **Status: Partly fixed.** 90afd14 added `quality_report.attribution`. 4090a95 writes NOTICE.md beside every export (sources, versions, tables, terms, sources consulted with nothing obtained, caveats), adds attribution to the manifest, and rewrote the `sources.yaml:8` comment.
- **Files:** `sources/registry.py:15-18`, `provenance/gate.py:24-28`, `provenance/versions.py:15-24`, `export.py` (`build_notice`), `config/sources.yaml:8-13`.
- **Evidence:**
  - At bff0d75/d12f6fe, nothing in `src/` read `Compliance.basis` or `.notes`.
  - Grep for terms, citation, CC-BY and commercial over all six export files found nothing.
  - All seven SPP sources carry SPP's non-commercial-with-citation terms only in `notes`.
  - Extra YAML fields (`license`, `commercial_use`) are silently dropped by `model_validate`.
  - Corrections: the provenance column did name source ids; the DC-screen caveat is moot because the screen did not run; the rent-band caveat depends on which snapshot is used.
- **Impact:** a forwarded CSV carried SPP facility names and scores with no citation and no notice of SPP's commercial-use condition.
- **Recommendation (residual):**
  - `Compliance` is still a boolean plus prose. Model the license, attribution text and `commercial_use` as fields, and compute the most restrictive conditions over each export's closure.
  - NOTICE.md travels only with the directory: the CSV has no inline notice, and MCP responses carry no terms.

#### A61 · The default SPP config points at `config/get_priors.yaml`, which was never committed, so `headroom run --region spp` crashes; the priors that order the EV shortlist are outside the gate and attribution
- **CONFIRMED · high.** Both verifiers said high. Not known. **Status: Open.**
- **Files:** `config/regions/spp.scoring.yaml:8-9`, `ingest/region.py:38`, `pipeline.py:66-70`, `model/run.py:67-75`, `model/ev.py:23-26,224-234`, `tests/test_region_scoring.py`.
- **Evidence:**
  - `git ls-tree` and `git log --all -- config/get_priors.yaml` are both empty, the file is not gitignored, and it is absent in every worktree.
  - `headroom run --region spp` exits 1 with `[Errno 2] … config/get_priors.yaml`, both in clean copies and in the author's worktree with live data. `--scoring` does not help, because `run.py:75` resolves priors against `_REPO_ROOT`.
  - Tests stay green because no test calls `ScoringConfig.load` on a region's `scoring_path`.
  - Using the TEST-ONLY fixture priors, or a priors file labelled "vendor quotes UNDER NDA", the run still exported as shareable. The priors appear in no `sources` entry, no attribution and no lineage. Swapping in a second priors file changed the top 10 (8/10 overlap) with identical attribution.
  - Corrections: the manifest does record the full priors content and sha256, replay checks that sha, and NOTICE prints the version and sha. Keeping priors out of lineage is a stated design choice (ev.py docstring). Nothing proves a real priors file ever existed: all 35 EV manifests in the session scratch used `ev_priors_test`.
- **Impact:** the README's primary live command fails on every checkout. The judgement input that orders the shareable SPP shortlist has no committed baseline and no provenance check, and the shareable bundle re-publishes the priors without terms.
- **Recommendation:**
  - Commit the priors, with a `version` and citations, together with the working-tree `spp.scoring.yaml` (A66).
  - Add a test that `ScoringConfig.load(get_region(r).scoring_path)` succeeds for every region.
  - Either register priors as a judgement-bucket source with a publishable flag and pinned sha, or gate them explicitly.
  - Require the priors path to be git-tracked for shareable runs, and record the path and blob sha.

#### A62 · EV ($M/yr) and cost of inaction treat one backward-looking year as forward value through a fixed 0.6–1.5× band; a within-2025 split contradicts the band
- **CONFIRMED · high.** The reviewer said high; verifiers split high/medium. Kept high: the band's miscalibration was measured on archived data, not hypothesised. Not known. **Status: Open.** 316a129 fixed the ordering, not the band.
- **Files:** `reconstruct/congestion_rent.py:26-27,57-68,102-117`, `model/ev.py:181-186,213,234`, `docs/OUTPUTS.md:29-30`, `docs/BLIND_SPOTS.md:51`.
- **Evidence:**
  - **The band.** Each draw is v × T(0.6, 1, 1.5), with mean 1.0335v. No term models persistence, and grep finds no backtest, decay or forecast code.
  - **Concentration on 2026-10-13.** Of 2,054 flowgates with rent, 1,176 bind in one month. About 1,800 earned at least 50% of their dollars in one month, and these hold 48.3% of the $2,408.7M. TMP-named flowgates (438) hold 49.8% of dollars and 58 of the top 104.
  - **Within-year split.** Both verifiers rebuilt it from the 12 archived CBC files; the rebuild matches `congestion.csv` exactly.
    - H1 to H2 at k=65: top-k overlap 0.31; capture 36.2% against an oracle 64.6%; 20 of 65 leaders realized $0; Spearman 0.57 (n=480).
    - Normalized by the system-wide change, 13.8% of H1 leaders fall inside the band and 69.2% below 0.6×. Persistent binders (4–6 H1 months) still land only about 18% in band, and the miss runs in both directions.
    - Odd/even months, which is less seasonal: overlap 0.66, 51.5% in band.
    - On the composite path, which uses the same band, five of the on-disk 2026-10-12 top 12 realized $0 in H2.
  - **The existing proxies barely act.** `line_n1_episodic` covers 3–4 of the top 104 by rent, while 50 of them earn at least half their dollars in one month. Recurring rent feeds only the composite.
  - Corrections: a common multiplicative band cannot reorder flowgates. The docs do say "2025 day-ahead". "Expected net value" appears at `OUTPUTS.md:29`, `BLIND_SPOTS.md:51` and the `ev.py` docstring, not at CROSSCHECK:106. Within-year halves confound seasonality.
- **Impact:** planners read episodic 2025 dollars as recurring annual value. On the only out-of-sample evidence available, most leaders realize well under the band's floor.
- **Recommendation:**
  - Add a per-flowgate persistence factor that can change the order: largest-month share, months with dollars, TMP/FG naming. Calibrate it on the harness (A63).
  - Until then, label EV and cost of inaction as "2025 observed × priors" in every basis and column header, and state "not tested out of sample" in the docs.

#### A63 · No path to an out-of-sample backtest: one scalar data year, one pin per source, in-sample references, no outcome table or harness
- **CONFIRMED · high.** The reviewer said high; verifiers split high/medium. Kept high: the cross-year barrier is demonstrated in three places in current code. Not known. **Status: Open.** b850940 added the run-time barrier.
- **Files:** `config/regions/spp.yaml:40,42`, `ingest/adapters/spp.py:76-77,95,166-167`, `ingest/adapters/common.py:127-133`, `pipeline.py:22-38`, `provenance/reproduce.py:112`, `model/references.py:13-57`.
- **Evidence:**
  - **One year everywhere.** `spp_data_year` and `eia930_year` are scalars, every source has one `source_version`, `build_snapshot` has no year argument, and the `spp` region has one `data_dir` pointing at `current`.
  - **Three refusals.** Build time (`require_pin`), run time (`check_stamp_pins`) and replay time (`source_pins` equality). Re-pinning only `spp_binding_constraints` to 2024 made the 2025 run raise "Snapshot stamps differ from the registry pins".
  - **No validation harness.** There is no outcome table or harness. CROSSCHECK's Spearman 0.966 compares CBC with TCR within the same year.
  - **References.** They are in-sample p99 values, but under `ranking: ev` they do not change the EV order: doubling them changed 0 `recommended_get`.
  - Corrections: a within-year split takes about 40 lines today with no repo change. `scripts/split_backtest.py` is reviewer scratch, not part of the repo.
- **Impact:** nobody can show that the shortlist predicts next-period congestion, or that it overlaps ITP or MMU lists.
- **Recommendation:**
  - Add `build_snapshot --data-year` and year-keyed pins, so snapshots from several years coexist.
  - Add `headroom backtest --rank-manifest … --realized …`, keyed on stable element keys (A64), reporting Spearman, top-k overlap and captured share against an oracle, recorded in the manifest.
  - Commit the within-year result now. Add MMU and ITP outcome tables later.

### 4.3 Medium

#### A02 · Every DA-BC-derived headline number comes from the yearly file the code now calls incomplete, and no doc says so
- **CONFIRMED · medium.** The reviewer said high; both verifiers said medium. Not known. **Status: Open.** README:37-41 still reads 1,601 / 732 / `ed7355d5…`, and the README:23-25 replay recipe now fails `check_stamp_pins` at HEAD.
- **Files:** `README.md:23-45`, `docs/CROSSCHECK.md:56-82`, `docs/BLIND_SPOTS.md`, `ingest/adapters/spp.py:20-25`.
- **Evidence:**
  - Parsing the archived yearly zip gives 136,303 rows, 969 monitored facilities, Σ|SP| 8.497 M$/MW, 1,601 kept flowgates and 732 corridors, which are the published figures. The digest `ed7355d5` also reproduces.
  - The yearly file lacks at least TMP452_24498 (LN LIBERAL_ - TEXAS_CO), which is present in the January monthly sample. The author's estimate is 6,344 rows, 123 constraints and 4.55% of |SP|. e24272d changed no user-facing doc.
  - Corrections: the HIFLD, FERC, PUDL and queue figures do not depend on DA-BC. The scope is constraint, ranking and rent figures. No rank or cross-check change was shown offline.
- **Impact:** readers take yearly-subset counts as facts about SPP's 2025 day-ahead market.
- **Recommendation:** regenerate the figures from 2026-10-13.r2 at HEAD, which also adds transformers (2,072 flowgates), and date every number. Rewrite the replay recipe (A28).

#### A03 · Replay identity covers more than the run uses, records no commit, and its errors name no key
- **CONFIRMED · medium.** The reviewer said high; both verifiers said medium. Not known. **Status: Partly.** 4090a95 added `export.py` to the hash, which is deliberately stricter (A04). Adapters are still hashed, the registry is compared as a whole, no git commit is recorded, and errors stay generic.
- **Files:** `provenance/runtime.py:10-13,16-44,56-65`, `provenance/reproduce.py:20-22,111-113`, `README.md` (replay).
- **Evidence:**
  - At 90b8046 the archived manifest failed two independent checks:
    - the `spp_binding_constraints` pin;
    - `source_sha256` changing from 53c2a3bb to b1de3eb4, caused only by `ingest/adapters/spp.py`, which a run never imports.
  - The output was unchanged: `ed7355d5`, with 5 of 5 files byte-identical. Replay at 3b2a397 passed.
  - The right code was found only by hashing 20 commits; the match was 353446f..3b2a397.
  - An unused `adapters/miso.py`, or an unrelated `miso` source, breaks replay. A notes-only registry edit does not.
  - Corrections: the strictness is documented ("deliberately strict"; "replayable only with its original pins") and fails closed. The changed pin is in the run's lineage. The module set must be taken after export and digest, because `digests.py` and `versions.py` are not imported by `run_pipeline`.
- **Impact:** replay cannot serve as a cross-commit regression check, and locating the producing code takes git archaeology.
- **Recommendation:** record the git commit, tree and dirty flag. Hash an allowlist of run-path modules and move adapter identity into the snapshot (A05). Compare pins against consumed stamps. Name the drifting key in every error.

#### A04 · Replay printed "verified" without checking the published artifacts or the shareable decision
- **CONFIRMED · medium.** The reviewer said high; verifiers split medium/high. Took medium: the label flip needed a constructed input or a deliberately loosened `export.py` (which would weaken `run` equally), and the MCP consumer is not installed. Not known. **Status: Fixed** (4090a95: `export.py` is in the runtime hash, and `compare_replayed_outputs` checks `mode`, `public_only` and the sha256 of five artifacts).
- **Files:** `provenance/runtime.py`, `cli.py` (`_reproduce`), `export.py` (`compare_replayed_outputs`, `REPLAY_COMPARED`).
- **Evidence:**
  - Before the fix, `export.py` was outside the hash, and `test_runtime_identity.py` asserted that.
  - The source hash stayed 53c2a3bb across 64b5e98, which changed the gate rule.
  - A run recorded at 64b5e98 replayed at 353446f as "verified" with mode flipped from internal to shareable, and a patched `export.py` also "verified".
  - Corrections: the CLI message literally claimed only inputs, runtime and ranking, and SNAPSHOTS documented the scope of the digest.
- **Recommendation (residual, read from code, not tested):**
  - The comparison runs after `deliver()` has written, so a failed replay leaves its artifacts in `--out`. Stage the files, compare, then rename.
  - Parquet is excluded from the comparison.
  - The file set is still not atomic (a stale parquet survives under `--overwrite` if DuckDB fails).

#### A05 · Snapshot provenance ends at build time: raw files are never re-verified, tables do not name their raw inputs, and no builder identity is recorded
- **CONFIRMED · medium.** The reviewer said high; both verifiers said medium. Not known. **Status: Partly.** ebf52d4 added offline `--renormalize-from` into a revision (re-hashes raw through `raw_entry` and checks `--date`), and a2b3514 added raw path containment. Still open: raw re-verification at activate/load, raw-to-table input lists, and a builder identity block.
- **Files:** `ingest/snapshot.py:139-196`, `adapters/build_snapshot.py`, `adapters/common.py:105-114,166-175,187-192`, `adapters/pudl_ferc1.py:311-316`.
- **Evidence:**
  - Tampering a raw queue CSV, truncating an EIA-930 file and deleting the HIFLD geojson left `activate_snapshot` passing and `read_tables` returning nine tables. The run was shareable with an unchanged `snapshot_manifest_sha256`.
  - Manifest top-level keys are only raw, schema_version, snapshot_date and tables.
  - Re-normalizing 2026-10-12 at 90b8046: 5 tables were byte-identical, SPP failed with KeyError, and e24272d^ reproduced all 9.
  - DuckDB's FLOAT→VARCHAR rendering shapes 1,759 FERC values, and duckdb is not recorded in any manifest.
  - Corrections: raw tampering cannot change exports, because the loader never reads raw/. Region-spec values appear in note prose, just unstructured. SNAPSHOTS:72 is an operator instruction.
- **Impact:** no mechanical audit of raw → normalized. Raw drift goes undetected.
- **Recommendation:** add a builder block (commit, ingest hash, region spec and its sha, python/duckdb/numpy versions). Fill per-table `inputs` from `raw_entry` calls. Verify `manifest.raw` on activation. Make files read-only on activation.

#### A07 · The composite Monte Carlo is a scalar Python loop on one shared RNG stream
- **CONFIRMED · medium.** The reviewer said high; both verifiers said medium. Not known. **Status: Open.**
- **Files:** `model/stability.py:31-48`, `model/score.py:11-14`, `provenance/envelope.py:72-91`.
- **Evidence:**
  - The loop is over 99% of a 56–60 s live run: about 3.5 µs per flowgate-draw, with a 128 MB sample matrix.
  - Removing the lowest-ranked flowgate moves p_top_k for 269 of 1,600 others (max 0.0174) and flips 2 top-400 memberships. With per-flowgate streams keyed by (seed, crc32(id)) the same removal moves nothing. A full reseed moves the same ~269 by up to about 0.02, and the max MC standard error is 0.005.
  - 4,805 degenerate Ranges consume no draws. The uniform and lognormal branches are unreachable.
  - A vectorised sampler that preserves the scalar stream order is bit-identical and about 39× faster (1.43 s).
  - Corrections: Streamlit offers only synthetic regions (0.6 s). Replay does not freeze the draw order, because `source_sha256` already pins all model code. The removal effect is MC noise, not a defect.
- **Recommendation:** vectorise in stream order first (no digest change). Then move to per-flowgate substreams under a recorded sampler version once replay scoping allows (A03). Report MC standard error next to p_top_k.

#### A08 · The dense DC/N-1 screen re-solves everything per flowgate, and its bus cap also wipes signals that need no DC solve
- **CONFIRMED · medium.** The reviewer said high; both verifiers said medium. Not known. **Status: Open.** Latent on SPP (`n_mapped=0`).
- **Files:** `reconstruct/network_model.py:71,103-121,239-269`, `reconstruct/bucket_b.py:93-104`, `model/signals.py:176-178`, `schema/entities.py:72-75`.
- **Evidence:**
  - Dense L×N incidence matrix (74 MB at SPP size). B is rebuilt on every solve and base cases are recomputed per flowgate. `ptdf_matrix` is used only in tests.
  - Forced mapping cost 1.7–1.8 s per flowgate, about 45–50 min for full SPP (~12,076 solves). A dense PTDF+LODF computes all 1,601 in about 3.3 s and matches to within 1e-11 MW.
  - Tripping the cap erased every mapping. That made `demand_pressure` unobserved, set radial to None, re-keyed corridors (737→732) and changed `get_basis`. `screen.n_mapped` kept the pre-cap count.
  - Corrections: the run-level status does record the cap; the per-row basis gives the wrong cause. MISO's largest in-envelope piece is 1,767 buses, under the cap. scipy is not required.
- **Recommendation:** use PTDF/LODF once per topology. Make the cap affect loading only. Compute `n_mapped` after the cap. Skip reactance estimation when nothing maps.

#### A09 · The live ingest path is wired to SPP, and region identity is split across REGIONS, SPP_ROOT, spp.yaml and the scoring path
- **CONFIRMED · medium.** The reviewer said high; both verifiers said medium. Not known. **Status: Open.** 7606c04 added `RegionConfig.scoring_path`, a fourth unvalidated location; its missing referenced file is A61.
- **Files:** `adapters/build_snapshot.py:18,23-28`, `adapters/common.py:187-192`, `hifld.py:104,418`, `eia.py:55-62`, `spp.py:77`, `ingest/region.py:20,38`.
- **Evidence:**
  - No `--region` flag (exit 2).
  - Adapters take only `snapshot_dir` and call `region_spec()` with its default `"spp"`. Adding `REGIONS['miso']` and a `miso.yaml` still builds into `data/raw/spp` with the SPP envelope and BA.
  - The `name`/`rto` keys in `spp.yaml` are never read.
  - Corrections: a data-year mismatch fails closed through `require_pin`. The congestion period default is reached only by fixtures. The non-filer list is prose. The SPP-pilot scope is documented.
- **Recommendation:** make a validated `RegionSpec` (pydantic, `config/regions/<name>.yaml`) the single source, including the data root, footprint, adapters, scoring file and the existence of referenced files. Add `--region`, and pass the spec explicitly.

#### A10 · No golden or regression test of ranking output
- **CONFIRMED · medium.** The reviewer said high; both verifiers said medium. Not known. **Status: Partly.** b808faf pins the spp-synth ranking fingerprint. The real-subset order, corridor order, GET mix and CSV column contract are still unpinned (not re-tested at HEAD).
- **Files:** `tests/test_phase3_scoring.py`, `tests/test_phase5_reproducibility.py`, `model/run.py` (sort keys), `export.py` (columns).
- **Evidence:** at bff0d75, 11 single-edit mutants survived the full suite and each changed the output:
  - weight swap; rent_ref ×2; coi_ref ×2;
  - flowgate sort-key swap and corridor sort swap (both visible only on the real subset);
  - percentiles; `_PERSISTENT`; `breached_hours>=100` (flips real labels);
  - `round(p_top_k,1)`; dropping the `provenance` column; dropping the `get_basis` column.
  - Corrections: some invariants were pinned (k, the leader, five synth GET labels, the 0.5 unobserved weight). No mutant changed top-k membership on the fixtures.
- **Recommendation:** golden outputs for spp-synth and the real subset (ordered ids, corridors, GET, bands to a tolerance, CSV header) behind an explicit `--update-golden` step. Add metamorphic tests: raising |SP| must not lower rank, and permuting input rows must not change the result.

#### A11 · The score-level gate was never exercised discriminatingly on real data
- **CONFIRMED · medium.** The reviewer said high; verifiers split medium/high. Took medium: the demonstrated leak paths need a future code regression, and b850940 now backstops them. Not known. **Status: Partly.** b850940's store-wide leaf gate blocks any run in which a leaf carries a non-publishable source, which closes the `column_sources`-without-contributor path. A loader that mis-stamps values is outside any gate's reach and still needs a test.
- **Files:** `export.py` (`_gate_blocked`, `_tables_blocked`, `_store_blocked`), `ingest/load.py:380-393`, `tests/test_real_snapshot.py`.
- **Evidence:**
  - At 7606c04 these mutants survived 337/337: detach_all, leak_rent_input, leak_queue_input, composite_drops_planning_lineage, table_gate_skips_contributors, loader_ignores_column_stamp.
  - With a `column_sources` stamp not listed as a contributor, `blocked_tables=[]`. detach_all plus that manifest wrote a shareable CSV whose hours came from `ferc_form_715` facts.
  - Corrections: the real-data test does run the score gate. Asserting `data=` equals `source_closure` is tautological.
- **Recommendation:** taint one stamp or column source at a time on the real subset and on mixed-stamp synthetic data, and assert the exact set of blocked scores.

#### A12 · The composite is effectively one statistic: binding hours are counted three times, references are fixture-era, and the shaping constants live in code
- **CONFIRMED · medium.** The reviewer said high; verifiers split medium/high. Took medium: the arguments for high are counted under A61 (broken default config) and A03 (replay fragility). **Known.** **Status: Partly superseded.** bff0d75 brought in observed CBC rent. e4e5585/0b0d190 brought in region p99 references with cost-of-inaction weight 0. The shaping constants are still in code.
- **Files:** `reconstruct/congestion_rent.py:25-27,91-92`, `model/signals.py:150-153,194,207-224`, `model/get_match.py:11-12`, `config/scoring.yaml`.
- **Evidence:**
  - At 90b8046 (2026-10-12): Spearman 0.991 against Σ|SP|×hours, 0.957 against Σ|SP|. Persistence carries 59% of the weighted mass on a 0.15 weight, and composites reach 6.98 against a documented "~0-1".
  - At HEAD (2026-10-13, `config/scoring.yaml`): Spearman 0.989 against hours alone, and persistence carries 62–64% of the mass. Coupling or dropping cost of inaction leaves the order unchanged (1.000). The distortion comes from references (rent 50 vs p99 22.45; hours 600 vs p99 1007).
- **Recommendation:** proceed as planned. Move every distribution parameter into recorded scoring config, and publish ranks and percentiles rather than raw composites.

#### A13 · The GET label is a fixed function of element type and contingency on live data, yet every surface presents it as a recommendation
- **CONFIRMED · medium.** The reviewer said high; both verifiers said medium. **Known.** **Status: Partly fixed.** bff0d75 made "heavy" mean `breached_hours>=1`, made DLR reachable, excluded phase shifters and gave transformers their own class. 39069be improved the basis. Under `ranking: ev`, `recommended_get` comes from EV.
- **Files:** `model/get_match.py`, `model/run.py:147`, `model/signals.py:83-85`, `mcp/server.py:54`, `app/streamlit_app.py:85,113`, `README.md:61-63`.
- **Evidence:**
  - At 90b8046 the label was Combination iff thermal and hours ≥ 360, otherwise APFC (0 mismatches over 1,601). Phase shifters at ranks 4 and 47 were labelled APFC.
  - At HEAD (composite ranking): 1,900 APFC, 170 DLR, 2 Combination. It is still a lookup on (ctype, BASE vs N-1).
  - `bool(radial)` is still passed (latent). `is_radial` tests degree only (873 bridges against 561 degree-1 endpoints).
  - `get_basis` is missing from `shortlist.json`, MCP, the map and Streamlit. README still says 1,542/59, "no DLR" and "transformers excluded".
  - At 0e5d3d9, running HEAD-era code on the archived snapshot still labelled the phase shifters APFC, because `get_match` has no guard of its own. At aae5e26 the pin check refuses that snapshot unless it is re-pinned by hand.
- **Recommendation:** return a tri-state with a reason, pass `radial` as `bool | None`, carry `get_basis` everywhere, label the field a hint, and update the README.

#### A15 · Output directories were not run-scoped: the README replay recipe overwrote the archived manifest and then verified its own output
- **CONFIRMED · medium.** Both verifiers said medium. Not known. **Status: Fixed** (aae5e26: `_refuse_other_run` compares inputs, `output_sha256` and mode; `--overwrite` replaces). The README still points the recipe at the archived directory.
- **Files:** `export.py` (`deliver`, `_refuse_other_run`), `cli.py`, `README.md:19-26`, `.gitignore:12,20`, `app/streamlit_app.py:126`.
- **Evidence:**
  - At 90b8046, step 2 changed `run_manifest.json` from b187cbeaa3de to 8232a2d05b2f (new pin, new source hash, an added outputs block). Step 3 "verified" the new file and also overwrote the second copy in `data/replayed`.
  - A run turned INTERNAL wrote `.INTERNAL.synthetic` files next to the stale shareable set, and MCP accepted the stale set.
  - Corrections: there were two copies, both overwritten. The original can be regenerated byte-identically from a199772 plus the snapshot.
- **Recommendation (residual):** a `latest` pointer that an internal run removes, archived manifests kept outside the gitignored tree (A28), and a run-scoped Streamlit export.

#### A16 · The "shortlist" artifacts hold every row, and their columns and k unit were undocumented
- **CONFIRMED · medium.** Both verifiers said medium. Not known. **Status: Partly.** aae5e26's `docs/OUTPUTS.md` documents the files, columns, bands and corridor views. The files are still named `shortlist.*`, there is no membership column or row schema version, and `p_top_k`/`recommended_get` change meaning by mode.
- **Files:** `export.py` (`deliver`, `build_score_rows`), `model/run.py` (`_dedupe_to_corridors`), `cli.py`, `docs/CROSSCHECK.md:66-71`.
- **Evidence:**
  - `shortlist.csv` holds all 1,601 flowgates (1,066 at p=0), and `shortlist.json` holds all 732 corridors. k=400 counts flowgates (Σ `expected_topk_slots` = 400.0).
  - 407 flowgates but only 273 corridors reach p ≥ 0.5.
  - The CSV grew from 25 to 42 columns while the manifest stayed at `schema_version` 2.
  - Corrections: README vs CROSSCHECK is ambiguous wording, not a contradiction. Added columns do not break parsers. Membership is probabilistic, so any flag needs a stated rule.
- **Recommendation:** rename to `flowgates.csv`/`corridors.json` and write a real corridor shortlist. Add `in_shortlist` with a stated rule, plus `k` and `k_unit`, and version the row schema.

#### A17 · The shareable decision checked only composite closures and `table:` leaves
- **CONFIRMED · medium.** Both verifiers said medium. Not known. **Status: Fixed** (b850940: `_store_blocked` gates one leaf per source across the whole store, plus dangling inputs, using `LineageStore.leaves()`). The residual is A18.
- **Files:** `export.py` (`_store_blocked`), `provenance/lineage.py` (`leaves`, `dangling_inputs`).
- **Evidence:**
  - At 4e45b4f, a `ferc_form_715` leaf driving `recommended_get` and `get_basis` exported as shareable.
  - The `ev:{cid}` lineage was ungated until 316a129.
  - 553 of 567 leaves were not `table:` leaves.
- **Recommendation:** extend the gate to the own source of non-leaf nodes (A18).

#### A18 · Lineage semantics allow laundering: `source_closure` ignores a node's own source when it has inputs, `add()` overwrites silently, and envelopes are mutable
- **CONFIRMED · medium.** The reviewer said medium; verifiers split low/medium. This is the exception to the tie-break rule: medium was kept because I reproduced the bypass at aae5e26, after the gate was hardened, through a public constructor whose docstring prescribes the pattern. It has no current caller. Not known. **Status: Open.**
- **Files:** `provenance/lineage.py:27,53-56`, `provenance/real.py:47-72`, `provenance/synthetic.py:40-63`, `provenance/envelope.py` (`model_config {}`), `export.py` (`_store_blocked` uses leaves only).
- **Evidence:**
  - **My probe at aae5e26** (a reviewer scratch probe, not in the repo). On the committed real subset I added `real_range(stamp=ferc_form_715, inputs=[public HIFLD leaf])` to one composite's inputs:
    - `deliver` stayed shareable (0 blocked scores, tables or sources);
    - the node's closure was `{'hifld_transmission'}`;
    - control: a CEII leaf instead turned the run internal-synthetic.
  - `add()` overwrites without warning: two planning rows collapse into one `plancost:FG1`.
  - Envelopes are mutable: `r.lo=5` (above `expected`), `dist='bogus'` and `inputs.append` all succeed.
  - The `_unique` mutant survived 337/337.
  - Latent: `real_range` and `synthetic_range` have no callers. Live runs had 0 id collisions and 0 nodes with both a registered source and inputs.
  - Corrections: Bucket B is idempotent. The recommendation to include `node.source` everywhere would break, because `quality.triangulate` is not a registered source.
- **Recommendation:**
  - Remove `inputs` from `real_range`/`synthetic_range`, or route all derived values through `derived.py`. Alternatively include `node.source` whenever it is a registered source.
  - Make `add()` raise on a conflicting envelope.
  - Freeze `Fact`/`Range`, with updates via `model_copy`.
  - Key planning by (cid, plan).

#### A19 · Several pins carry no upstream content identity, and the docs overstate what they catch
- **CONFIRMED · medium.** Both verifiers said medium. Not known. **Status: Open.**
- **Files:** `ingest/adapters/spp.py:88-95` (`_bc_pin`), `:161-167` (`_money_pins`), `adapters/common.py:110-133`, `eia.py:57,95,252`, `pudl_ferc1.py:107,395`, `docs/ADAPTERS.md:14-15`.
- **Evidence:**
  - The DA-BC, CBC and TCR pins are built from `spp_data_year`; `_money_pins` reads no file at all.
  - Re-posting March DA-BC (one shadow price changed) and March CBC (one Sum tripled) built with identical stamps. Only the raw and table sha256 differed.
  - Changing only `spp_data_year` raises "upstream serves …" when nothing upstream changed.
  - Corrections: the PUDL/EIA-860 pins are tied to immutable versioned S3 paths. `spp_itp` is header-only. Drift is recorded in hashes, so it can be found by diffing, but nothing detects it. README:28 is roughly accurate; ADAPTERS:15 and the `require_pin` message overstate.
- **Recommendation:** keep a committed lock of expected raw sha256, or ETag/Last-Modified plus size, and raise an explicit drift status that requires a re-pin. Fix the docs and the error text.

#### A21 · `Range.expected` means mode, mean or median depending on the producer
- **CONFIRMED · medium.** Both verifiers said medium. Not known. **Status: Partly.** aae5e26's OUTPUTS.md has a "what a band means" table, and since 316a129 EV ranks by `ev_mean`. The field is still overloaded, and README's 63% figure and CROSSCHECK's scale check were not recomputed.
- **Files:** `provenance/envelope.py:72-91`, `reconstruct/congestion_rent.py`, `model/score.py:17-34`, `model/ev.py:212-215`, `README.md:62-64`, `docs/CROSSCHECK.md`.
- **Evidence:** on 2026-10-12:
  - Signals use `expected` as the triangular mode. The composite and EV use it as p50.
  - Rent mean/mode is 2.81 and cost-of-inaction 3.28. The median composite p50 is 1.22× Σw·mode.
  - Top-100 shares: modes give 0.236/0.530/0.235, while MC means give 0.338/0.270/0.392. README's "63%" is the per-row share of modes.
  - Rent: $2,124M at the mode, $5,976M MC mean, against the IMM's ~$1.2B.
  - The effect on rank is small (Spearman 0.995).
  - Corrections: two meanings of lo/hi are live, not three (lognormal and uniform are unused). On the observed-rent path, mean/mode is about 1.03.
- **Recommendation:** store mode, mean and p50 explicitly, give the composite its own percentile type, and recompute the shares in the docs from the draws.

#### A22 · Rank stability uses a flowgate-level k but is reported for corridors, and it saturates
- **CONFIRMED · medium.** Both verifiers said medium. Not known. **Status: Partly.** OUTPUTS.md now says k is per flowgate and that a corridor's p_top_k is its champion's. The computation is unchanged.
- **Files:** `model/stability.py:11-16,39-41`, `model/ev.py:181-186`, `model/run.py` (`_dedupe_to_corridors`).
- **Evidence:**
  - 2026-10-12: 267 flowgates at p=1, 1,066 at 0, 268 in between. About 267 distinct corridors fill the 400 slots per draw.
  - 273 corridors (37%) reach p ≥ 0.5, against 186 under a corridor-level test (k_c=183). 198 sit at exactly 1.00.
  - Current config: about twice the nominal share reaches p ≥ 0.5 (94 vs 46 composite; 97 vs 49 EV with test priors). Under EV nothing reaches 1.0.
  - Corrections: Streamlit exposure is synthetic-only. The champion semantics is documented.
- **Recommendation:** compute corridor stability per draw at k_c, keep flowgate p_top_k as its own field, and show a rank interval.

#### A28 · A third party cannot reproduce the published SPP result
- **CONFIRMED · medium.** Both verifiers said medium. Not known. **Status: Partly.** aae5e26 added an unhashed `requirements.lock` with exact versions, including duckdb, and offline CI. Still missing: a published manifest or snapshot, a tag, and a commit in the manifest. The README recipe fails at HEAD, and the priors do not exist (A61).
- **Files:** `.gitignore:10,12,20`, `README.md:21-38`, `provenance/runtime.py:56-65`, `requirements.lock`.
- **Evidence:**
  - The run manifest and `data/raw` are gitignored, and the README cites a truncated digest. Replay pins Python 3.12.0 on Darwin/arm64.
  - `origin/main` is the synthetic-only README, and the producing commit is on no remote branch. Replay at 3b2a397 verified in about 56 s.
  - Corrections: nothing has been published yet, so this is a release-readiness gap. `source_sha256` lets someone recover the commit range by hashing history. Raw URLs and sha256 are already in the snapshot manifest. The committed real subset is 59 flowgates from 2026-10-13, so it cannot check the 1,601-flowgate result.
- **Recommendation:** publish a release bundle per shareable run: the full manifest, `snapshot_manifest.json` plus `normalized/` (1.4 MB, non-commercial with citation), and a tag. Hash the lockfile and record its digest in the manifest.

#### A32 · Table and scoring contracts are not enforced at the boundary
- **CONFIRMED · medium.** The reviewer said medium; verifiers split low/medium. Took medium: a silent wrong result is reachable today through a supported config edit. Not known. **Status: Open.**
- **Files:** `ingest/snapshot.py:16-19`, `ingest/load.py` (no column check; `_unique` not applied to planning, hifld_lines or buses), `reconstruct/planning_extract.py:32`, `model/run.py` (`ScoringConfig.load`), `model/score.py:14`, `export.py` (`_SIGNAL_ORDER`).
- **Evidence:**
  - Dropping `hours_binding` gives a bare KeyError at `congestion_rent.py:40` after Bucket A. `ctype='Thermal'` gives a ValidationError with no table or row. A blank numeric gives "could not convert string to float".
  - A duplicate planning row keeps the last one (FG3 0.8625 → 0.70), and the first row's facts are orphaned.
  - `persistance` loads, then raises KeyError inside the MC.
  - **A weights map that omits a signal runs silently and drops it.**
  - Corrections: the SPP adapters check upstream columns with named errors, and normalized headers come from FIELDS lists. The planning duplicate is latent.
- **Recommendation:** a per-table schema registry validated at write and load, naming the table and row on failure. Require `set(weights) == SIGNAL_NAMES`. Key planning by (cid, plan).

#### A33 · The Streamlit map cannot show the real run
- **CONFIRMED · medium.** Both verifiers said medium. Not known; part follows from CEII. **Status: Open.**
- **Files:** `app/streamlit_app.py:37-38,126`, `app/map_data.py:59`, `ingest/region.py:27-30`, `tests/test_phase5_reproducibility.py:52-58`, `README.md:8,17`.
- **Evidence:**
  - Only spp-synth and miso-synth are offered, and the pipeline runs uncached (a live run takes 56–75 s).
  - 0 of 892 live corridors have geometry: `monitored_line` is blank, so keys are facility strings.
  - The export writes to `data/processed` relative to the working directory.
  - miso-synth is the spp_synth fixtures relabelled: identical fingerprints, with "spp" strings in its provenance. The generalization tests assert only rto and counts.
  - streamlit, pandas and pydeck are not installed, so the app was not launched.
  - Corrections: a caption discloses that some corridors have no geometry. The fixture reuse is documented in BLIND_SPOTS. The freeze is hypothetical, and at HEAD selecting spp would crash on A61.
- **Recommendation:** read a verified run directory instead of re-running. Add a "location not public (CEII)" badge. Build the region list from the registry. Add a real second fixture with different ids and sources.

#### A34 · The advertised entry points and the full snapshot build are under-tested
- **CONFIRMED · medium.** Both verifiers said medium. Not known. **Status: Partly.** `test_real_snapshot.py:145` now runs `cli.main(['run', …]) == 2`. MCP, Streamlit, demo.py and a real-adapter build remain untested.
- **Files:** `cli.py`, `mcp/server.py:37-60`, `app/streamlit_app.py`, `demo.py`, `tests/test_snapshot_builder.py:46-50`.
- **Evidence:**
  - At b808faf the `_run` body never executed. Mutants that dropped `--scoring`, used a bad ORDER BY column, an unknown demo key or bad `show_cols` all survived.
  - A coordinated rename `months_binding → binding_months` in the adapter and its own test left the suite green. On the real subset `months_binding` silently became unobserved, because the loader's `.get` skips blanks.
  - The builder test fakes ADAPTERS. The real builder run offline on the samples succeeds with every join empty, because the samples are geographically disjoint.
  - Corrections: MCP is a deferred extra and its `pragma: no cover` is deliberate. Adapter-order regressions fail closed. MCP returns tuples, not `list[dict]`.
- **Recommendation:** an offline real-builder integration test through `require_tables` and `_load_live`; a test extra with fastmcp; and moving Streamlit's frame construction into `map_data`.

#### A39 · The CLI exited 0 whether the export was shareable or refused
- **CONFIRMED · medium.** Both verifiers said medium. Not known. **Status: Fixed** for the gate (4090a95: exit 2 when a live export is refused). The stale-artifact case is fixed by aae5e26. Residual: pin drift, replay mismatch, usage-value errors and programming bugs all still exit 1 with one-line messages, and the KeyError/TypeError tracebacks are swallowed.
- **Files:** `cli.py` (`_report`, `main`).
- **Evidence:** at d12f6fe a refused run exited 0, and a refusal into a reused `--out` left the previous shareable files in place. Correction: argparse usage errors already exited 2.
- **Recommendation:** distinct exit codes for drift, replay mismatch and usage errors, and `--debug` to show tracebacks.

#### A43 · Half the composite weight is unobserved and planning is header-only, yet "shareable" has no fitness threshold
- **CONFIRMED · medium.** The reviewer said medium; verifiers split low/medium. Took medium: the corridor roll-up demonstrably carries no coverage field. **Known.** **Status: Partly.** NOTICE.md caveats include the unobserved-weight gap.
- **Files:** `quality/report.py:29-55`, `export.py` (mode decision), `model/score.py:14`, `ingest/adapters/spp.py` (`_planning`).
- **Evidence:**
  - The mode depends only on publishability.
  - `unobserved_weight_all = 0.5` for every flowgate in 2026-10-12 and in 2026-10-13 (2,072 flowgates). Loading, demand and planning are unobserved, and `dc_screen` did not run.
  - Corrections: per-row `cov_*` and `screen_status` are in the CSV, JSON and parquet. Composites are not bounded by 0.5 (the max is 1.87). Uniform renormalization is a pure rescale. Under `ranking: ev` the composite is diagnostic, and the order's dependence on priors is not measured. `report.py`'s phrase "ranking rests on the remaining 0.50" misleads under EV.
- **Recommendation:** a fitness label (for example screening-grade), separate from publishability, that reflects unobserved weight, the screen status and the priors. Add coverage to corridor rows and to MCP.

#### A44 · Known real-data scoring problems cannot trip any test
- **CONFIRMED · medium.** Both verifiers said medium. **Known.** **Status: Partly.** Phase-shifter exclusion, DLR reachability, recurring rent and the reference rule now have tests. The core gap remains.
- **Files:** `tests/test_real_snapshot.py:80`, `tests/test_phase3_scoring.py:54-62`, `tests/test_region_scoring.py:67-72`, `model/get_match.py:44`.
- **Evidence:**
  - Restoring the tautological "heavy" fallback passed every test while Combination on the real subset rose from 2 to 30 of 59.
  - No test asserts the real GET mix.
  - Every real-subset pipeline test uses the fixture-era `config/scoring.yaml`, where normalized persistence reaches 4.6.
  - No test loads `spp.scoring.yaml` through `ScoringConfig.load`, which would have exposed A61. The live reference check skips without data.
  - Correction: "observed signals in 0–1" is not feasible on this subset, which is the top 30 plus the bottom 10 by |SP|.
- **Recommendation:** load every region's scoring config in a test; assert "heavy" is not tautological and pin the real GET mix; round-trip `derive_references` on the subset's own table.

#### A64 · Flowgate and corridor keys are SPP display text with no namespace or alias table
- **CONFIRMED · medium.** The reviewer said high; both verifiers said medium. Not known; the station-level transformer key is documented design. **Status: Open.**
- **Files:** `ingest/adapters/spp.py:196,205,264-277,312`, `schema/entities.py:87-90`, `model/stability.py:35-41`, `model/ev.py:190-191`, `model/run.py:246-291`.
- **Evidence:**
  - 230 (monitored, contingency) groups hold more than one `constraint_id`, covering 482 of 2,072 flowgates. 438 ids (21.1%) are TMP-named.
  - LN OSAGE_OG - WHEGL / 14968 is split across two names: 489 h / $5.34M plus 654 h / $11.81M, i.e. 1,143 h / $17.14M combined.
  - Hourly overlap: about 146–154 groups are hour-disjoint within shared months, 67–77 bind in different months, and 7–9 bind simultaneously.
  - Merging hour-disjoint aliases left the EV top-100 corridors at 99–100/100 (Spearman about 0.99). OSAGE-WHEGL moved from 33 to 16–17; seed noise alone gives 100/100.
  - 379 transformer flowgates sit on 144 station keys. SEMINOLE holds BK_11_1, BK_11_2, BK_22_1 and BK_22_2 under one key, and at least 8 stations carry distinct bank ids. Each station shares one applicability draw.
  - Raw DA-BC has a NERCID column (716 FG flowgates) that the adapter drops.
  - Corrections: many groups are distinct SPP constraint definitions (e.g. MA+FG pairs), not renames, so merging on disjoint hours would merge distinct transformers. The 94 "new" TMP ids come from the yearly-to-monthly switch, not renames over time. The 21% cross-run join loss is an extrapolation. The namespace switch is hypothetical.
- **Recommendation:** namespaced content keys (`spp:ln:<sorted endpoints>`, `spp:xf:<station>:<bank>`, contingency key, `flowgate_key = H(…)`). Keep NERCID. Keep a per-snapshot alias table, and export `flowgate_key`. Do not merge on hour disjointness alone.

#### A65 · No diff or attribution path: replay passes or fails as a whole, and the digest cannot tell a rank change from a metadata change
- **CONFIRMED · medium.** The reviewer said high; both verifiers said medium. Not known. **Status: Open.** 4090a95 adds artifact checks but still raises on the first mismatch.
- **Files:** `cli.py`, `provenance/reproduce.py:111-113`, `provenance/digests.py:13-20`, `model/references.py:41-57`, `pipeline.py:22-38`.
- **Evidence:**
  - Only `run` and `reproduce` exist. One generic message hides 4 changed sources plus a code-hash change.
  - HEAD code on the 2026-10-12 data with the recorded scoring (registry pins patched to pass `check_stamp_pins`):
    - rank, composite and p_top_k are identical for all 1,601 flowgates;
    - `recommended_get` changed for 220 (Combination 59→2, DLR 0→170);
    - `ranking_digest` still moved (ed7355d5→aaebdc35).
  - Moving to 2026-10-13.r2: 31 of the top-100 corridors left. 29 entrants are new keys (28 XFMR), and 2 phase shifters vanished. On the 1,599 shared ids persistence is identical and rent differs for 1,339.
  - Three guards obstruct counterfactual runs: `check_stamp_pins`, `check_reference_rule`, and the missing `--data-dir`.
  - Corrections: the manifests already record every axis. `scores_fingerprint` includes `recommended_get`, so it is not rank-only. The "10 exits from rent alone" double-counted the 2 phase shifters.
- **Recommendation:** `headroom diff <A> <B> [--attribute]` that flags the changed axes from manifests, joins on `flowgate_key` (A64) at a common k, and runs ordered counterfactuals. Add a rank-only digest that excludes `recommended_get`. Make replay report every mismatching axis.

#### A66 · The committed `spp.scoring.yaml` fails its own `--check`, its reference guard is inert, and CI is green only because the check skips
- **CONFIRMED · medium.** The reviewer said medium; verifiers split medium/low. Took medium: the failure is demonstrated on the committed file, and A61's fix lands it. Not known. **Status: Open.** The uncommitted working-tree yaml passes.
- **Files:** `config/regions/spp.scoring.yaml`, `model/references.py:13-14,41-57`, `scripts/derive_scoring_refs.py:63-64`, `docs/BLIND_SPOTS.md:59-68`, `tests/test_region_scoring.py:67-74`, `.github/workflows/tests.yml`.
- **Evidence:**
  - At aae5e26, `--check` on the committed yaml exits 1 (`congestion_sha256` None; `coi_definition` None). With live data present the suite gives 1 failed, 378 passed; CI has no `data/raw`.
  - `check_reference_rule` returns early when there is no sha and accepts a foreign one. That also skips its `coi_definition` check.
  - BLIND_SPOTS describes recurring rent and a guard that are true only of the working-tree file.
  - Corrections: ebf52d4 aligned the derive default to `rent_x_persistence`, so the "coi_ref 1.949 vs 11.87" mismatch no longer reproduces. BLIND_SPOTS already says recurring rent is sampled independently. Under `ranking: ev` only the composite is affected.
- **Recommendation:** commit the working-tree yaml together with the priors. Fail closed when `reference_rule` lacks `congestion_sha256`. Add an offline structural check in CI.

#### A67 · No gate on the CBC join rate: a degraded join stays shareable, shrinks k, and is never checked for month alignment
- **CONFIRMED · medium.** Both verifiers said medium. Not known. **Status: Open.**
- **Files:** `ingest/adapters/spp.py:165-167,376-386,436-439,456`, `model/ev.py:181-186`, `export.py` (gate).
- **Evidence:**
  - I prefixed the contingency names in the Jan–Oct CBC files and re-recorded the sha. The build succeeded with 789 of 2,072 joined (66.443% of |SP|). Rent fell from $2,408.7M to $463.9M against TCR's $1,957.8M, so CBC/TCR went from 1.23 to 0.24, and nothing checks that ratio.
  - `headroom run` exited 0, shareable, with k going from 103 to 39. 335 flowgates carried only Nov–Dec dollars, unflagged, at a median 0.235× their baseline.
  - A header-only July CBC gave 99.468% joined and −5.8% rent, which a 98% floor would miss. `derive_scoring_refs` accepted the degraded table.
  - Corrections: the degradation is visible but not enforced (the CLI prints k and a gap; `quality_report` has `ev.k` and `n_unobserved_rent`). Coverage is already `partial` on the healthy baseline. Real-data month misalignment is 0.002% of |SP| (21 flowgates).
- **Recommendation:** structured join statistics, a CBC/TCR ratio tripwire, per-(month, flowgate) alignment, and forced INTERNAL with a named reason below thresholds or on a drop against the previous snapshot.

### 4.4 Low

Each entry gives verdict, severity provenance, status, evidence and recommendation in compressed form.

- **A14 · MCP trusts the sibling manifest's mode, ignores per-file digests, and a failed parquet step is silent.** CONFIRMED · low (reviewer medium; both verifiers low) · not known · **Status: Open.**
  - Files: `mcp/server.py:7-27,49-58`, `export.py` (`_materialize_parquet`).
  - Evidence: a swapped INTERNAL parquet passed. A re-delivery with DuckDB failing left a stale parquet (145/2,072 p_top_k differ) beside a manifest lacking it. The check runs only in `build_server`. The tool returns 3 columns and repeats corridors.
  - Corrections: fastmcp is not installed and there is no serve entry. The manifest is unsigned, so a digest check catches accidents only.
  - Recommendation: before enabling MCP, verify digests per call, serve corridors, delete the old parquet on failure, and add `headroom serve`.
- **A20 · HIFLD raw is re-serialized, and the recorded URL was never issued.** CONFIRMED · low (reviewer medium; both low) · **Open.**
  - Files: `hifld.py:96-142`, `docs/ADAPTERS.md:5`.
  - Evidence: pages are kept in a TemporaryDirectory and written out as sorted-key JSON. The unpaged URL returns ≤2,000 of 17,665 features. An edit during paging produced a torn file stamped with the pre-edit pin.
  - Corrections: the canonical form is deterministic (a re-fetch reproduces the sha), and no downstream consumer uses the raw hash.
  - Recommendation: record issued page URLs and sha256 values, compare `dataLastEditDate` in ms after paging, and fix ADAPTERS:5.
- **A23 · DC islanding detection depends on whether LinAlgError fires.** CONFIRMED · low (reviewer medium; verifiers medium/low; took low because it needs element mapping, which does not exist) · **Open, latent.** Becomes medium once any mapping lands.
  - Files: `network_model.py:103-121,177-183,271-298`, `docs/BLIND_SPOTS.md` §4.
  - Evidence: 313 bridges strand more than one bus. 151 raise; 162 return finite "ok" flows. Example HIFLD_100238: cond 9.8e16, island flows 1,749 MW, KCL residual about 2,000–2,962 MW. The slack bus B1 has degree 1, so dropping its line leaves 2,695 buses with no angle reference and the result still reads "ok". Divergence is between contingencies, not between flowgates that share one.
  - Recommendation: a topological component check, a well-meshed slack bus (one per island), and a meshed multi-bus island test.
- **A24 · The four load snapshots are one flow pattern scaled by total load, and reactance and rating uncertainty never reach the band.** CONFIRMED · low (reviewer medium; both low) · **Open, latent.**
  - Files: `network_model.py:30-42,76,136-164,286-298`, `bucket_b.py:68-80`.
  - Evidence: flow ratios are identical on every line (0.439029, 0.581488, 0.457887). `wind_share` is never read. Peak always sets hi. Independent ±30% per-line reactance moves flows by a median 5.5% (p90 18%). The imputed rating is a point value.
  - Recommendation: per-snapshot injections (or collapse to one snapshot), and carry the rating band.
- **A25 · Indeterminate screen results are scored as observed loading 1.0.** CONFIRMED · low (reviewer medium; both low) · **Open, latent.**
  - Files: `network_model.py:230-248`, `signals.py:166`, `get_match.py:34-37`.
  - Evidence: `no_base_flows`/`no_thermal_limit` produce `Range(0,1,2)` marked observed, and a midpoint of 1.0 meets `_HEAVY`. A header-only `load_930` flips FG1 and FG8 to Combination. `signal_coverage` says observed while `status_counts` says `no_base_flows`. Live triggers are blocked (EIA-930 empty raises; blank ratings are estimated).
  - Recommendation: treat these statuses as unobserved and check `load_930` coverage.
- **A26 · GET thresholds are tied to the scoring normalization references.** CONFIRMED · low (reviewer medium; both low) · **Open, mostly inert at HEAD.**
  - Files: `get_match.py:11,26-27,44-46`, `signals.py:150-153`.
  - Evidence: the Combination threshold is 0.6 × `persist_ref_hours`. At HEAD Combination counts 2–3 / 2 / 1 / 0 across refs. TMP246_28141|32502 flips at the in-tree ref of 1007. Under EV, `recommended_get` is unaffected.
  - Correction: the manifest does record the references.
  - Recommendation: thresholds in physical units, in config, echoed in `get_basis`.
- **A27 · Bucket A cannot affect any live score.** CONFIRMED · low (reviewer medium; both low) · **Open, latent.**
  - Files: `pudl_ferc1.py:221-228,251-254`, `quality/triangulate.py`, `quality/confidence.py`.
  - Evidence: the FERC→HIFLD join keys on kV, so all 25 joins agree by construction. `eia_lines` is header-only. Confidence is never read outside `quality/`. Setting every voltage to 999 kV, or removing Bucket A entirely, left all 2,072 scores identical. Ties go to whichever source was inserted first. One source and two agreeing sources both rate "medium".
  - Correction: a name-only join is harmful, because 10 of 84 name-only matches disagree on kV (different circuits).
  - Recommendation: corroborate on a field the join does not key on (length), and break ties by tier.
- **A29 · The replay runtime match is all-or-nothing and pins the interpreter patch and OS.** CONFIRMED · low (reviewer medium; both low) · **Partly** (unhashed lockfile including duckdb).
  - Files: `runtime.py:13,56-65`, `reproduce.py:113,121`.
  - Evidence: any field change produces one generic error. A faked 3.12.13 reproduced the digest but was still refused.
  - Corrections: the build-time CVE surface is not the replay host. The platform pin is defensible (libm). pydantic pins pydantic-core exactly.
  - Recommendation: separate "attested" from "re-executed" outcomes, name the differing fields, and record a hashed lock digest.
- **A30 · Manifest metadata is unreliable.** CONFIRMED · low (reviewer medium; both low) · **Partly** (a2b3514: `generated_at` = latest input retrieval, with a stated basis).
  - Files: `versions.py:11-12,42`, `reproduce.py`.
  - Evidence: `schema_version` stayed 2 while coverage, outputs, scoring sub-keys and `export_decision` were added. `PUDL_RELEASE` in code is independent of the registry pins: re-pinning PUDL gave `pinned.pudl_release` v2024.11.0 against `sources` v2025.2.0.
  - Corrections: `shortlist_k` and `n_constraints` predate v2. A plain version bump breaks `reproduce`, which requires exactly 2.
  - Recommendation: a minor-version field with a compatibility check, and derive the PUDL release from the registry.
- **A31 · Config, fixture and data roots resolve from `__file__`.** CONFIRMED · low (reviewer medium; both low) · **Open.**
  - Files: `registry.py:12`, `run.py:28-29`, `region.py:10-11`, `build_snapshot.py:17-18`, `common.py:191`.
  - Evidence: from a real wheel install, every path resolves under site-packages, and `headroom run` exits 1. `get_priors` resolves against the repo root even when `--scoring` is given. The build would write under `lib/python3.12/data`.
  - Corrections: MCP is unaffected, failures are loud, and the documented install is editable.
  - Recommendation: one config root from an env var or flag, importlib.resources defaults, and priors resolved relative to the scoring file.
- **A35 · HIFLD paging had no upper bound.** CONFIRMED · low (reviewer medium; both low) · **Fixed** (a2b3514: `max_pages = expected//PAGE + 2`, abort when features exceed count).
  - Files: `hifld.py:110-133`.
  - Evidence: a server ignoring offset looped past 300 pages at about 15 MB RSS per page.
- **A36 · Rebuilds fail late, and failed staging directories accumulate.** CONFIRMED · low (reviewer medium; both low) · operational cost of the known queue-pin drift (A59) · **Open.**
  - Files: `build_snapshot.py:117-140`, `spp.py:107-129`.
  - Evidence: the queue pin is checked after about 45 downloads (~141 MB). Three failures left 3 × 141.5 MB of 0700 directories. Errors are one line with no traceback. Concurrent builds fail with ENOTEMPTY and do not corrupt anything.
  - Corrections: there is no scheduler. Retention is documented and tested. Permissions do not affect MCP or Streamlit.
  - Recommendation: a queue preflight, pruning, and logged tracebacks.
- **A37 · Docs gave rent's band as 100–1,200 MW, omitting the −40%/+50% forward band.** CONFIRMED · low (reviewer medium; both low) · **Partly** (OUTPUTS.md states the band; README:44-45 does not).
  - Files: `congestion_rent.py:25-27,57-87`, `README.md:44-45`.
  - Evidence: the sampled range is $0.51B/$2.12B/$15.3B against the docs' $0.85B/$2.12B/$10.2B.
  - Corrections: the basis string discloses the band, and CROSSCHECK's mode-based conclusion holds.
- **A38 · BLIND_SPOTS said cost of inaction is ∝ rent and does not move the ranking.** CONFIRMED · low (reviewer medium; both low) · **Superseded** (d12f6fe/46fb24e rewrote §5; committed SPP cost-of-inaction weight is 0). Residual: README:57.
  - Files: `docs/BLIND_SPOTS.md` §5, `model/signals.py:207-224`.
  - Evidence: at 90b8046, removing it moved 8/33/72 positions in the top 10/50/100 against 0/6/20 from seed noise, and corridors #1 and #2 swapped. Top-400 membership was identical.
- **A40 · SPP provider labels are hard-coded in the generic core.** CONFIRMED · low (reviewer medium; both low) · **Partly** (ebf52d4 derives the `rent=` label from the stamp; `TABLE_PROVIDER`, `planning_extract` and `signals.py` providers are still constants).
  - Evidence: a simulated live MISO region exported shareable with `rent=spp_binding_constraints` while `data=` listed miso sources. FCA plans are labelled `spp_itp`.
  - Corrections: miso-synth's SPP labels match its data, and `planning=` text comes from the data.
  - Recommendation: take providers from leaf stamps.
- **A41 · The HIFLD footprint assumes one BA and one connected network.** CONFIRMED · low (reviewer medium; verifiers medium/low; took low because no MISO path exists) · **Open.** Becomes medium once RTO #2 starts.
  - Files: `hifld.py:419,464-483,516`.
  - Evidence: patching the BA to MISO drops the 1,276-bus MISO South component. Mislabelled SWPP Ameren buses cut the only short path, and the note calls it an "artifact".
  - Correction: a list of BA codes would not fix this.
  - Recommendation: keep all large components, and model one slack per island.
- **A42 · Scoring is one global config.** **DISPUTED** (one verifier: refuted, stale at HEAD; one: confirmed low) · low · **Superseded** (7606c04/0b0d190: `RegionConfig.scoring_path`, per-region p99 references with `reference_rule`).
  - Files: `ingest/region.py:20,38`, `pipeline.py:47-51`, `model/references.py:41-57`.
  - Residual: a non-synthetic region with no `scoring_path` silently uses fixture references. No test exercises region-default resolution.
  - Recommendation: require `scoring_path` for live regions.
- **A45 · Stamps cannot say "not retrieved", and contributors are hand-declared.** CONFIRMED · low (both low) · **Partly** (32b0906 splits `data=` from `absent=`).
  - Files: `spp.py:471-483`, `snapshot.py:23-40`, `hifld.py:557-585`, `pudl_ferc1.py:295`.
  - Evidence: the header-only planning table is stamped `itp_2024_v1.0` with the DA-BC `retrieved_at`. `hifld_lines`, `ferc1_lines` and `queue` omit EIA-860 as a contributor. Attribution lists `spp_itp` as used.
  - Corrections: attribution has included contributors since 90afd14. Omitting EIA, HIFLD and queue from `data=` is correct for SPP. A non-publishable EIA-860 still blocks through the buses contributor.
  - Recommendation: an explicit not-retrieved stamp form, and contributors computed from actual reads.
- **A46 · Every refused run is labelled "internal-synthetic".** CONFIRMED · low (both low) · **Partly** (a2b3514 records `export_decision` with blocked scores, tables and sources).
  - Evidence: CEII and terms refusals get `.INTERNAL.synthetic` names while `quality_report.synthetic` is False. Latent, since no adapter emits a non-publishable source.
  - Recommendation: mode and suffix by cause (`internal-restricted:<sources>`).
- **A47 · Cross-adapter dependencies rely on import, path and order, and the SPP queue silently skips its bus join.** CONFIRMED · low (both low) · **Open.**
  - Files: `spp.py:507-603`, `pudl_ferc1.py:277-297`, `build_snapshot.py:20-28`.
  - Evidence: a reordered build exits 0 and writes a queue with 0 `near_bus` that passes `require_tables`. With mapped lines, `demand_pressure` becomes an **observed** 0.0 and ranks move. Manifest checking is duplicated (hash, then re-read).
  - Correction: only the hifld→pudl_ferc1 and hifld→spp dependencies are real.
  - Recommendation: declared inputs, a topological order, and errors when a dependency is missing.
- **A48 · Signal coverage has no "partial", an observed zero is drawn as positive, and duplicate planning rows collapse.** CONFIRMED · low (both low) · **Open, latent.**
  - Files: `schema/entities.py:12,133`, `model/signals.py:48-59,110-111`, `reconstruct/planning_extract.py:32`.
  - Evidence: a partial queue (14.6% of MW placed) yields `demand_pressure` "observed". An observed zero has mean 0.0167 (at most 0.004 of the composite). Planning keeps the last duplicate row.
  - Correction: the table-level gap does reach `gaps`.
- **A49 · Layering leaks and private-member access.** CONFIRMED · low (both low) · **Partly** (`LineageStore.leaves()` is public; `export.py:142` still iterates `store._nodes`).
  - Evidence: importing `provenance.reproduce` loads 41 modules. Adding it to `provenance/__init__` creates an import cycle.
  - Correction: no layering rule is written down, and a rename would fail loudly.
- **A50 · Dead or vestigial code.** CONFIRMED · low (both low) · **Open.**
  - Evidence:
    - Unreferenced: `real_range`, `synthetic_range`, `retrieved_at_of`, `queue_mw_near`, and `write_run_manifest` (a non-atomic duplicate writer).
    - Test-only: `export_shareable_or_raise`, `ptdf_matrix`, `scores_fingerprint`, `build_server`.
    - Never read: `BucketBResult.binding_flags`, `extras['radial']`.
    - Also: `demo.py`; unreachable dist branches; a tautological miso-synth test.
  - Correction: `extras['ev']` is read.
  - Recommendation: delete them (this also removes A18's constructor).
- **A51 · The offline guarantee is enforced file by file.** CONFIRMED · low (both low) · **Open.**
  - Evidence: there is no `conftest.py`. 15 test files have no guard, and an unguarded `spp.fetch` reached portal.spp.org:443. The guard covers only `urlopen`.
  - Correction: a socket block would miss DuckDB's native HTTP.
  - Recommendation: an autouse socket block in a root conftest.
- **A52 · Test runtime.** CONFIRMED · low (both low) · **Open.**
  - Files: `tests/test_phase3_scoring.py`, `tests/test_phase4_delivery.py`, `tests/test_phase5_reproducibility.py`, `model/stability.py:35-41`.
  - Evidence: 32 rebuilds at 10k draws take about 24 of 36 s. At 500 draws the suite runs in 14.5 s.
  - Corrections: the "magic thresholds" have a 2.5× margin. The fixture mutation is restored in `finally`, and order independence was verified. The draw-sensitive assertion is the wobble test `0<p<1`.
  - Recommendation: a session-scoped low-draw fixture, excluding the stability and fingerprint tests.
- **A53 · No CI, and .gitignore can hide fixture types.** CONFIRMED · low (both low) · **Partly** (aae5e26 adds an offline pytest workflow; per its commit message, the workflow file is not pushed).
  - Files: `.gitignore:17,20,45,57,83`, `.github/workflows/tests.yml`.
  - Evidence: `run_manifest.json`, `*.manifest`, `*.log`, `lib/`, `*.duckdb` and `build/` still hide fixtures. CI skips the live reference check (A66).
  - Recommendation: CI checks that `check-ignore` is empty under `tests/` and that `ls-files` matches the disk.
- **A54 · The download client lets redirects leave TLS, records neither the final URL nor content type, and has no overall deadline.** CONFIRMED · low (both low) · **Open.**
  - Files: `common.py:41-102`.
  - Evidence: an https→http redirect was followed and only the requested URL was recorded. Dripping 8 bytes at 0.6 s per byte against `timeout=1` completed. The opener stays installed after a failed main.
  - Corrections: an on-path attacker cannot inject a redirect into verified TLS; it needs an upstream redirect. HTML responses fail closed in the parsers.
  - Recommendation: an https-only redirect handler with a host allowlist, recording the final scheme and host (redacted), and a wall-clock deadline.
- **A55 · The DA-BC size cap trusted the zip header.** CONFIRMED · low (both low) · **Partly** (a2b3514 reads through `z.open` capped at `MAX_CSV_BYTES+1`).
  - Evidence: a 1 MB zip declaring 4 KB peaked at about 1.4 GiB RSS before the CRC error.
  - Residual: the cap is 512 MiB, about 250× a real member (~2 MB). An honest-header file near the cap still costs about 5 GB, because the whole decoded text and row list are held and 12 months accumulate.
  - Recommendation: a data-sized cap (16–32 MiB) and incremental parsing.
- **A56 · CSV formula injection.** CONFIRMED · low (both low) · **Fixed** (a2b3514 `_cell` prefixes `'`).
  - Files: `export.py` (`_cell`, `_csv_bytes`), `ingest/adapters/spp.py:292,304`.
  - Trade-off: a prefixed `constraint_id` in the CSV no longer joins against JSON or parquet. OUTPUTS.md documents this.
  - Evidence: `=HYPERLINK`/DDE names passed the adapter. Real data had 0 such cells.
- **A57 · `raw_entry` lacked path containment.** CONFIRMED · low (both low) · **Fixed** (a2b3514).
  - Files: `adapters/common.py:105-114`.
  - Evidence: `../outside.csv`, an absolute path and an outward symlink, each with a matching sha, were accepted while `table_path` refused all three. Reachable only through staging manifests the builder writes itself.
- **A58 · Stale docs.** CONFIRMED · low (both low) · **Partly**.
  - Files: `docs/SNAPSHOTS.md:94-98,139-141`, `pyproject.toml:6-8`, `docs/ADAPTERS.md:42`.
  - Open: SNAPSHOTS.md:139-141 says replay is tested with "fabricated data", yet `test_real_snapshot` replays real data. The pyproject comment is stale. ADAPTERS.md:42 omits `hifld_transmission` as a queue contributor.
  - Correction: the SNAPSHOTS parquet sentence is accurate.
- **A59 · The queue pin is a refresh date.** CONFIRMED · low (both low) · **Known** · **Open.**
  - Files: `spp.py:98-104`, `reproduce.py:112`.
  - Evidence: bff0d75 re-pinned 2026-10-09 → 10-12 bundled with unrelated changes. A queue-only re-pin breaks replay even of spp-synth, because the whole registry is compared. 16 SPP tests break per re-pin.
  - Corrections: the failure is loud, and SPP refreshes every few business days, not on every build.
  - Recommendation: record the refresh date in the stamp only.
- **A60 · Transformer flowgates are dropped and the taxonomy has no transformer class.** **DISPUTED** (one verifier: refuted as stale; one: confirmed low) · low · **Known** · **Superseded** (bff0d75: 379 transformers scored as `ctype=transformer`; phase shifters excluded with a reason, 2 flowgates, 0.39% of |SP|).
  - Residual: exclusions appear only in the note, `interface` → APFC is unconditional, and the spelling map lives in adapter code.
- **A68 · The CBC-vs-collected overstatement lives only in prose.** CONFIRMED · low (reviewer medium; verifiers low/medium; took low because TCR-as-truth is unconfirmed and the magnitudes use stand-in priors) · **Open.**
  - Files: `congestion_rent.py:57-68`, `spp.py:213-221,463-467`, `docs/CROSSCHECK.md:92-110`.
  - Evidence: per-constraint CBC/TCR has p10 0.97, p50 1.08, p90 about 2.0, and 261 flowgates fall outside the band. Uniform ÷1.13 or ÷1.23 leaves the top 103 unchanged. Per-constraint calibration gives 92/103 flowgates and 96/103 corridors against noise of 101–102. WILLMGRANI is at 26.19× (rank 60 → 542).
  - Corrections: the fixed-cost mechanism does not move the top k. The totals reach `gaps` only incidentally.
  - Recommendation: flag CBC/TCR > 2, and report EV top-k overlap on both rent bases.
- **A69 · The rent definition is chosen by the table's shape and recorded nowhere.** CONFIRMED · low (reviewer medium; both low) · **Partly** (ebf52d4 provider label; `check_stamp_pins` blocks the pre-CBC snapshot unless someone re-pins by hand).
  - Files: `congestion_rent.py:36,70`, `ev.py:181,239`, `quality/report.py:90`.
  - Evidence: with a hand re-pin, 2026-10-12 exported a shareable EV ranking. #1 had `rent_musd` 138.51 = Σ|SP| × 250 MW, labelled "observed rent". The band-to-observed ratio has median 1.38 (p10 0.61, p90 2.78).
  - Correction: provider, closure, basis and attribution already distinguish the two paths at HEAD.
  - Recommendation: a `definitions.rent` key, and EV only on observed rent.
- **A70 · Exports, MCP and UI do not carry the ranking definition or run identity.** CONFIRMED · low (reviewer medium; both low) · **Partly** (NOTICE.md states snapshot, manifest sha, ranking and k; OUTPUTS.md defines `p_top_k`).
  - Files: `mcp/server.py:51-58`, `run.py:228-229`, `export.py:52-53`.
  - Evidence: MCP orders by `p_top_k DESC, composite_expected DESC`. With test priors this gives a different #1 from the export, 2,040/2,072 positions differ, and the top 10 contains 8 distinct corridors. `p_top_k` means different things by mode. Rows, JSON, the quality report and MCP carry no run or snapshot id. `run_label` is constant.
  - Corrections: Streamlit renders the model's own order, and a `rank` column exists.
  - Recommendation: MCP `ORDER BY rank` over corridors; stamp a `run_id` on every artifact.
- **A71 · EV p_top_k is mostly the GET applicability prior.** CONFIRMED · low (reviewer medium; verifiers low/medium; took low because the main-ranking defect was fixed and the remaining MCP path is not live) · **Partly** (316a129 ranks by `ev_mean` and documents the convergence).
  - Evidence: leaders saturate at the class ceiling (line_n1 0.925, transformer_n1 0.80). At d12f6fe the export's #1 changed with the seed. Scaling applicability by 0.6 drops the leaders' p_top_k from 0.92 to 0.72 while 101 of 103 stay in the list.
  - Open: MCP still orders by p_top_k, OUTPUTS.md:17 still points readers to p_top_k, and there is no prior-sensitivity block.
  - Corrections: the Streamlit label refers to the composite. `p_best_none` already exposes the ceiling.
- **A72 · No as-of model.** CONFIRMED · low (reviewer medium; both low) · **Open.**
  - Files: `envelope.py:40-50`, `real.py:11-18`, `entities.py:95`, `congestion_rent.py:31`, `hifld.py:33,53-56`.
  - Evidence: no table, Fact, Score or row carries a validity period. `CongestionObs.period` is written but never read (changing it to 2019 gave byte-identical outputs). Vintages: SPP 2025; EIA-930 2024; EIA-860M operating dates up to 2024-09; FERC report_year 2023; HIFLD layer edit 2023-09-05 but `VAL_DATE` 2014-07 to 2021-06 (fetched, then dropped); queue 2026-10-12.
  - Corrections: the manifest `sources`, attribution and NOTICE carry vintage-bearing labels, and `ferc1_lines` has a `report_year` column.
  - Recommendation: keep `VAL_DATE`, and add structured `as_of`/`valid_from`/`valid_to`.
- **A73 · The DC screen and radial test would judge 2025 binding against 2014–2021 topology.** CONFIRMED · low (reviewer medium; both low) · **Open, latent.**
  - Evidence: no vintage comparison exists. In a synthetic test, adding a post-vintage parallel line flips one flowgate from radial/DLR to meshed/APFC and halves its loading. README:46's "2023-09-05 edition" overstates how current the topology is.
  - Correction: blindness to relief already built overlaps the known planning gap.
  - Recommendation: `topology_as_of` and a vintage-gap flag.
- **A74 · Bus ids are ordinal, flips of `current` are not logged, `run` has no `--snapshot`, and snapshot ids are unchecked directory names.** CONFIRMED · low (both low) · **Open.**
  - Files: `hifld.py:491-495`, `build_snapshot.py:65-79`, `region.py:36`, `load.py:67`, `snapshot.py:73-75`.
  - Evidence: removing one HIFLD feature renumbers all 2,695 remaining buses. Directories renamed `not-a-date` or `2026-10-20` load and are recorded under the new name.
  - Corrections: no code joins bus ids across snapshots, and no export carries them. Replay never reads `current` and rejects a renamed copy. A stale flip fails `check_stamp_pins` loudly.
  - Recommendation: content-derived bus keys, `run --snapshot`, an activation ledger, and a name-vs-manifest check.

---

## 5. Systemic themes

1. **Identity comes from labels, not content.** Covers A01, A03, A19, A20, A30, A59, A64, A72, A74.
   - Pins are strings built from config.
   - Flowgate and corridor keys are SPP display text.
   - Bus ids are ordinal ranks.
   - Snapshot ids are directory names.
   - Run identity is "the whole registry plus nearly the whole package", with no git commit.
   - Hashes are recorded throughout but never compared against an external expectation.
   - Root cause: no first-class content identity for upstream releases, physical elements or runs.
2. **The tested path and the shipped path diverge.** Covers A61, A66, A44, A10, A11, A34, A51, A53.
   - Tests use the fixture-era `config/scoring.yaml` and TEST-ONLY priors.
   - The shipped SPP config is never loaded by any test, and CI skips the one live check.
   - Determinism tests compare a run with a rerun of the same code.
   - So the suite stays green while the documented live command crashes.
3. **Published claims outlive the code that produced them.** Covers A02, A13 (README), A15, A28, A37, A38, A58.
   - README, CROSSCHECK and BLIND_SPOTS carry hand-copied numbers.
   - The archived run sits in a gitignored directory that the README recipe overwrites.
   - Nothing regenerates the docs from a manifest or checks them against one.
4. **The export decision checks publishability only, never fitness.** Covers A43, A62, A67, A68, A69, A61, A46.
   - The gate is the only automated decision, and it asks one question: may this be shared?
   - Join collapse, partial-year dollars, rating-band rent presented as observed, untracked priors and half-unobserved weight all leave a run "shareable".
5. **Forward value with no temporal model.** Covers A62, A63, A72, A73, A59.
   - One data year, one pin per source.
   - No as-of on any value, no persistence term, no outcome table.
   - The product's unit ($M/yr, "expected net value") claims a forecast the architecture cannot test.
6. **Physics code that cannot run on the live region.** Covers A08, A23, A24, A25, A27, A41, A48, A73.
   - CEII means `n_mapped=0` on SPP.
   - The DC screen, radial test, demand pressure and Bucket A are exercised only on 8-bus fixtures or forced mappings.
   - Latent correctness bugs accumulate in this code, and some labels describe evidence that does not exist on the live region.
7. **Read surfaces re-derive instead of reading the run.** Covers A14, A16, A22, A33, A70, A71.
   - MCP has its own ORDER BY and serves flowgate rows as "corridors".
   - Streamlit recomputes the pipeline.
   - File names promise subsets they do not contain.
   - Root cause: no single verified read API over a run directory.
8. **SPP-shaped seams in a nominally generic core.** Covers A09, A31, A32, A40, A41, A42, A47.
   - Region identity is split across several places.
   - Providers are hard-coded and paths resolve from `__file__`.
   - Table contracts exist only as adapter constants and prose.
9. **Change can be detected but not explained.** Covers A03, A29, A65, A64.
   - Replay fails closed with one generic message.
   - The ranking digest moves on metadata.
   - No diff exists, and keys are unstable across postings.

---

## 6. Roadmap

Sizes: **S** is up to a day, **M** is a few days, **L** is one to two weeks or more.

**Now**
1. Commit the real priors (with version and citations) and the working-tree `spp.scoring.yaml`. Add a test that loads every region's scoring config. Make `check_reference_rule` fail closed without `congestion_sha256`. Add an offline structural check in CI. Push the workflow. Closes A61, A66, part of A44, A53. **S**
2. Regenerate the SPP result at HEAD from 2026-10-13.r2. Rewrite README Status, the replay recipe, CROSSCHECK and BLIND_SPOTS from that manifest. Tag the commit and publish the release bundle (manifest, snapshot manifest, `normalized/`, hashed lockfile). Closes A02, A28, A58, the README parts of A13/A15/A37/A38/A74, and part of A29. **M**
3. Close gate laundering: remove `inputs` from `real_range`/`synthetic_range`, or include a node's registered own source. Make `add()` raise on conflict. Freeze envelopes. Delete dead constructors and helpers. Closes A18, A50, part of A49. **S**
4. Add golden ranking tests on spp-synth and the real subset, a CSV column contract, per-stamp taint tests, and a non-tautology assertion for "heavy". Closes A10, A11, the rest of A44. **S–M**
5. Add CBC data-quality gates: structured join statistics, a CBC/TCR ratio tripwire, per-month alignment, and forced INTERNAL with a named reason. Flag CBC/TCR > 2. Closes A67 and the actionable part of A68. **S**
6. Relabel EV and rent as "2025 observed × priors" and commit the within-year split result. Make MCP `ORDER BY rank` over `shortlist.json`, and fix OUTPUTS.md:17. Interim fix for A62; closes A70, A71, part of A14. **S**

**Next**
7. Backtest harness: data year as a build parameter, year-keyed pins, `headroom backtest`, frozen-reference mode. Closes A63 and provides calibration for A62. **L**
8. A persistence model inside EV that conditions on largest-month share, months with dollars and naming, calibrated on item 7. Closes A62. **M**
9. Stable namespaced element and flowgate keys, NERCID retained, a per-snapshot alias table, `flowgate_key` exported, content-derived bus keys. Closes A64, A74 (keys). **M**
10. Replay and attribution: record git commit, tree and dirty flag; hash only the run path; move builder identity into the snapshot; compare consumed stamps; report every mismatching axis; `headroom diff --attribute`; a rank-only digest; `headroom run --snapshot ID`, an append-only activation ledger, and a check that the directory name matches the manifest's date and revision. Closes A03, A05 (builder block), A65, A29, A74 remainder. **M**
11. Make the export decision two-axis: a fitness label (screening-grade or partial) next to publishability, structured Compliance conditions, mode and suffix by cause, priors registered as a source, an explicit `definitions.rent` with EV only on observed rent. Closes A43, A06 residual, A46, A61 provenance, A69. **M**
11a. Finish the known scoring redesign: move every distribution parameter and threshold (forward band, hours band, planning band, `_PERSISTENT`, `_HEAVY`) into recorded scoring config, and publish ranks and percentiles rather than raw unitless composites. Closes A12. **S–M**
12. Vectorise the composite MC in stream order now (no digest change); per-flowgate substreams after item 10. Closes A07. **S–M**
13. A validated `RegionSpec` as the single source, `--region` and an explicit spec argument, providers taken from stamps, one config root. Closes A09, A40, A31, the A42 residual. **M**
14. Temporal fields: keep `VAL_DATE`, `topology_as_of`/`fleet_as_of`, structured `as_of` on stamps and outputs. Closes A72, A73. **M**
15. Output semantics: rename `shortlist.*` to `flowgates.*`/`corridors.*`, add a membership rule column, version the row schema, corridor-level per-draw stability, explicit mode/mean/p50 on Ranges. Closes A16, A21, A22. **M**
16. Table and scoring schemas validated at write and load, `set(weights) == SIGNAL_NAMES`, planning keyed by (cid, plan). Closes A32, part of A48. **M**

**Later** (when element mapping, a second RTO or MCP/Streamlit deployment becomes real)
17. DC screen: PTDF/LODF once per topology; a work-based cap affecting loading only; a topological islanding check with a meshed slack; per-snapshot injections; indeterminate statuses treated as unobserved; tri-state GET with a bridge-based radial test; GET thresholds in physical units; excluded flowgates emitted as rows with `excluded_reason` rather than only counted in the note. Closes A08, A23, A24, A25, A13 residual, A26, A60 residual. **L**
18. Multi-region topology: several BA codes, multiple components, one slack per island. Closes A41. **M**
19. Upstream integrity: content locks or ETags with drift status, verbatim HIFLD pages, an https-only redirect allowlist, a wall-clock deadline, a data-sized zip cap with streaming parse. Closes A19, A20, A54, A55 residual. **M**
20. Ingest hygiene: declared adapter inputs with topological order; queue preflight and staging pruning; a not-retrieved stamp form; computed contributors; non-circular Bucket A corroboration. Closes A47, A36, A45, A27, A59. **M**
21. Read surfaces: a verified run-directory API used by MCP and Streamlit, digest checks per call, `headroom serve`, a CEII badge, a real second fixture. Closes A14, A33, A34 rest. **M**
22. Test and ops hygiene: autouse socket block, session low-draw fixture, CLI exit codes for drift and mismatch, `--debug`, manifest schema minor versions, PUDL release derived from the registry, layering clean-up. Closes A51, A52, A39 residual, A30 residual, A49. **S each**

---

## 7. Refuted or dropped, and the review's limits

### 7.1 Refuted sub-claims and dropped items (one line each)

- **A01:** "a reader of the shareable artifact cannot tell". The adjacent quality report named the true version, and this was no publishability leak.
- **A02:** "every published headline number". The HIFLD, FERC, PUDL and queue figures do not depend on DA-BC.
- **A03:** "the whole repo is hashed". The hash covers 7 packages plus the pipeline; notes-only registry edits are harmless and the strictness is documented.
- **A04:** "replay claimed artifacts were verified". The message literally claimed only inputs, runtime and ranking.
- **A05:** "6 tables re-normalize at HEAD" (it was 5); "region spec not recorded" (it is in note prose); SNAPSHOTS:72 "immutability claim" (it is an operator instruction).
- **A06:** "cites no source". The provenance column named source ids.
- **A07:** Streamlit pays a minute (synthetic only, 0.6 s); "replay freezes draw order" (order-preserving vectorisation is bit-identical); "moves of 0.01–0.05 indistinguishable from real change" (≤ 0.02 against SE 0.005).
- **A08:** "silently" (the run-level status records the cap); MISO exceeds the cap (1,767 buses); "1–1.4 h" (about 45–50 min on the test machine).
- **A09:** "the data year needs three unlinked edits". It is two, and they fail closed.
- **A10:** "nothing is pinned". k, the leader and five synth GET labels were pinned.
- **A13:** "cannot vary on live data". Stale since bff0d75.
- **A14:** "served rows carry data=synthetic_fixture". The tool returns 3 columns and no provenance.
- **A15:** "the only copy". There were two copies, both overwritten, and both are regenerable from a199772.
- **A16:** README "contradicts" CROSSCHECK (it is ambiguous wording); "parsers break silently" (columns were added, not removed).
- **A17:** "rests only on a naming convention". The derived-constructor rule also holds.
- **A18:** Bucket B is not idempotent (re-runs are identical); the `monitored_line` blanking is cross-stage (it touches Bucket B's own objects).
- **A19:** "nothing records drift". Hashes are recorded; they are just not compared.
- **A20:** "the hash proves nothing". The canonical form is deterministic and reproducible.
- **A21:** "three meanings of lo/hi" (two are live); "the corridor sum mixes hard bounds" (it sums percentiles only).
- **A22, A33, A70, A71:** the Streamlit exposure claims. Only composite synthetic regions are selectable, and the app renders the model's own order.
- **A26:** "the manifest records nothing". References and `reference_rule` are recorded.
- **A27:** the name-only join recommendation (harmful, since 10 of 84 matches disagree on kV) and the "quality backbone" framing (no doc claims it).
- **A28:** "42-flowgate committed subset". It is 59 flowgates from 2026-10-13, and nothing has been published yet.
- **A29:** "security exposure from the old interpreter". The replay host is not the build host, and the CVE paths are narrow.
- **A31:** "import-time failures; MCP breaks". Failures happen at run time, and MCP is unaffected.
- **A34:** "the first signal is a failed live build". A coordinated rename degrades silently to unobserved instead, which is worse.
- **A36:** "a daily cron fills the disk" (no scheduler); MCP/Streamlit PermissionError (neither reads raw).
- **A37:** "CROSSCHECK's scale check misleads". Its mode-based conclusion holds.
- **A38:** "591 flowgates move" is mostly tie noise; the top-10/50/100 effect beyond noise is real.
- **A40:** the miso-synth labels are wrong (its data is SPP); the planning provider is exported (the `planning=` text comes from the data).
- **A42:** dropped as stale. Per-region scoring has existed since 7606c04.
- **A43:** "composites bounded by 0.50". The maximum is 1.87.
- **A44:** "observed signals in 0–1" as a test. Infeasible on the biased subset.
- **A45:** "`data=` should include EIA/HIFLD/queue" (correct as is for SPP); "attribution lacks contributors" (present since 90afd14).
- **A46:** "the CLI ignores the gate". It prints the refusal and the blocked counts.
- **A47:** "demand_pressure stays unobserved". It becomes a false observed zero, which is worse.
- **A48:** "constraints partial because transformers are excluded". Stale; it is now the phase-shifter exclusion.
- **A49:** "tests can use `table_lineage`". The tests check all leaves.
- **A50:** "`extras` is never read". `extras['ev']` is read.
- **A52:** brittle thresholds and fixture order dependence. Both were refuted.
- **A53:** "ENV/ case-insensitivity matters". `env/` is already ignored.
- **A54:** "an on-path attacker or captive portal can inject a redirect into verified TLS". It needs an upstream redirect.
- **A55:** "about 2 GiB RSS within the download cap". It saturates at about 1.4 GiB, and an honest-header file is the worse case.
- **A56:** `monitored_facility` as an injection vector. It is always prefixed `LN`/`XFMR`/`Multi-Element`.
- **A58:** "the SNAPSHOTS parquet sentence is stale". It is accurate.
- **A59:** "archived runs become unreplayable silently". The failure is loud.
- **A60:** dropped as stale. Transformers have been scored since bff0d75.
- **A61:** "priors are invisible to the manifest" (content and sha are recorded and checked on replay); "a real priors file existed" (unsupported).
- **A62:** the CROSSCHECK:106 "expected net value" citation (that wording is elsewhere); "no persistence term at all" (crude proxies exist but barely act).
- **A63:** "literally no path" (a within-year split works today); "scripts/split_backtest.py is in the repo" (reviewer scratch).
- **A64:** "230 groups are renames" (many are distinct definitions); "21% cross-run join loss" (an extrapolation; 2 of 1,601 were lost from 10-12 to 10-13).
- **A65:** "`scores_fingerprint` is rank-only" (it includes `recommended_get`); "10 exits from rent alone" (double-counted 2 phase shifters).
- **A66:** the coi_ref definition mismatch (fixed in ebf52d4); "BLIND_SPOTS is silent on independent sampling" (it says so).
- **A67:** "the only sign is a coverage word" (the CLI and quality report show k and the gap); "0.11% month misalignment" (it is 0.002%).
- **A68:** "EV's fixed cost moves the shortlist under uniform scaling" (103/103 unchanged); "about 12% of the shortlist" (it is about 6–10%).
- **A69:** "nothing downstream can detect the definition". The provider, closure, basis and attribution differ at HEAD.
- **A70:** "`generated_at` is a constant" (fixed in a2b3514); "the ranking appears only in the manifest" (NOTICE.md states it).
- **A72:** "no vintage reaches the manifest" (the version labels and notes do); "FERC year only in the note" (a column exists).
- **A73:** "keeps its full 2025 $M/yr". True only of the expected value; the band is uncertainty, not a discount.
- **A74:** "near_bus joins across editions mis-point" (no code joins across snapshots); "a renamed directory replays" (replay rejects it).

Hypotheses that the lenses ruled out:
- DA-BC dedupe does not double-count |SP| (0 of 136,303 rows).
- The rent correlation structure does not move the ranking (Spearman 1.000 and 0.999).
- Summing persistence over corridor members barely double-counts (union ratio 1.02).
- There are 0 lineage-id collisions in 31,866 nodes.

### 7.2 Limits of this review

- **No real priors exist.** Every EV figure (A22, A61, A64, A68, A70, A71) was produced with `tests/fixtures/ev_priors_test.yaml` or other stand-ins. The shapes hold; the magnitudes do not transfer.
- **No network access.** Nothing was rebuilt from upstream. The size of the yearly-vs-monthly gap rests partly on the author's note. Within-year splits confound seasonality, and no second data year exists to test against.
- **Not launched.** MCP (fastmcp) and Streamlit (streamlit, pandas, pydeck) are not installed. Their findings rest on code reading plus running their SQL or data functions directly.
- **Fix status is only partly tested.** Fix status for b850940, 4090a95, a2b3514, 32b0906 and aae5e26 comes from reading the diffs, the full suite at aae5e26 (378 passed, 1 skipped) and one re-probe (A18). The other fixes were not re-tested. In particular, I did not test whether the `compare_replayed_outputs` ordering leaves artifacts behind (A04 residual).
- **Latent physics findings were tested off live data.** A08, A23–A25, A27, A41 and A73 were exercised only on synthetic fixtures, forced mappings or patched BA codes, because SPP element locations are CEII.
- **Machine-dependent numbers.** Timings come from one Apple-silicon machine. A23's 151/162 split may change with a different BLAS/LAPACK build.
- **Mixed commit references.** Line references come from several commits (90b8046 through aae5e26). HEAD lines are used where a verifier supplied them; others are labelled with their commit.
- **Not credited.** The uncommitted change to `config/regions/spp.scoring.yaml` was not credited as a fix. Whether SPP's commercial-use terms apply to derived scores is a legal question and was not assessed.
- **Process note.** One lens (ingest-snapshot) wrote a stray file to `/tmp/_x`, outside the scratch directory. It was left in place under the no-delete rule. Nothing in the repository was modified by this review.
