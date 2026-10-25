# Constraint panel

`headroom panel` writes the observed monthly binding history of the constraints a
buyer's footprint matches. Every value is cited to a stamped table and the raw files
it came from. It is history only: no composite score, forecast, power-flow study or
deployment finding.

```bash
.venv/bin/headroom panel --region spp-synth --footprint tests/fixtures/panel_footprint.csv --out data/processed/panel
```

`--start` and `--end` (`YYYY-MM`) set the window. They default to the earliest and
latest covered month. Only `spp-synth` is registered. It is a fabricated fixture
stamped `synthetic_fixture`, so its panel is always internal. A live region is
registered only after its source's retrieval, retention and commercial-report rights
are documented. The SPP tables can already be built, but SPP is not registered yet
([below](#spp-tables-built-not-registered)).

## Footprint CSV

The footprint lists manual matches from a buyer's elements to source constraints.
Headroom does not infer matches. The file must have exactly these columns, in any
order. UTF-8 with a spreadsheet BOM is accepted.

| Column | What it holds |
|---|---|
| `element_id` | The buyer's element. Required. |
| `element_kind` | `settlement_location`, `resource`, `site`, `county`, `owner`, `corridor` or `constraint` |
| `constraint_id` | The source constraint the element matches |
| `corridor_id` | Optional. A constraint verified on two different corridors is rejected. |
| `match_status` | `verified`, `uncertain` or `unmatched` |
| `evidence` | Why the match holds, or why it is in doubt |
| `evidence_refs` | Pointers to that evidence |
| `reviewed_by`, `reviewed_on` | Who verified the match, and when (`YYYY-MM-DD`) |

- `verified` rows need `constraint_id`, `evidence`, `reviewed_by` and `reviewed_on`.
  Only these rows enter the panel.
- `uncertain` rows need `evidence`. `unmatched` rows must not name a constraint. Both
  are listed as unresolved and never guessed into the panel.
- A row may not repeat an `element_kind`, `element_id` and `constraint_id`.
- The artifacts record the footprint's file name and sha256, never its path.

## Snapshot tables

A panel region is a pinned snapshot directory ([SNAPSHOTS.md](SNAPSHOTS.md)) with two
normalized tables. An adapter writes them with `headroom.panel.tables.write_panel_table`.
It validates the rows, writes the CSV atomically, and records the stamp, sha256 and
`derived_from` (the raw input keys the table was built from) in the manifest. A value
column read from another source names that source in `column_sources`, and the source
must also be one of the table's `contributors`, as in the pipeline's tables. Each of
those values carries that column's own source, and the gate checks every contributor.
Only `constraint_history`'s value columns (`hours_binding`, the shadow prices and
`congestion_cost_usd`) can take a column source. Any other column fails the load.

| Table | One row per | Columns |
|---|---|---|
| `constraint_history` | constraint, market (`DA` or `RT`), month | `hours_binding` (above 0: a month without binding has no row), `shadow_price_max` and `shadow_price_mean` (magnitudes, mean ≤ max), `congestion_cost_usd`, `monitored_facility`, `contingent_facility`. The price and cost columns are blank where the source does not publish them. |
| `constraint_coverage` | market, month the source was read for | `days_expected` (the days in that month), `days_covered`, `raw_refs` (raw input keys, separated by `;`) |

A history row needs coverage for its month. Its hours must fit in 24 × `days_covered`,
plus one for the day daylight saving time ends.

Loading fails before any lineage is created when:

- a table's stamp, path or sha256 fails, as for every snapshot table;
- a table's `derived_from` is missing, empty or repeats a key, or history cites a raw
  input that coverage does not;
- a coverage row's `raw_refs` names a key missing from its table's `derived_from`;
- a cited raw input fails `Snapshot.verify_raw`. That happens when it has no manifest
  entry, its path leaves the snapshot, its `url` is empty, its `retrieved_at` has no
  timezone, its sha256 is malformed, or its bytes do not match. Adapters run the same
  check through `common.raw_entry`;
- a table breaks its contract: wrong columns, anything other than plain finite
  decimals, a repeated key, or history outside coverage.

## What a month means

| Month | `coverage` | `binding` | `hours_binding` |
|---|---|---|---|
| The constraint has a history row | `full` or `partial` | `yes` | The reported hours |
| Covered, but no history row | `full` or `partial` | `no` | 0, derived from the coverage record so its lineage traces to it |
| Not covered | `none` | `unknown` | Blank. The month is listed in `months_not_covered` and never counted as zero. |

- In a partial month the hours are a lower bound. Its values carry `partial_coverage`
  and medium confidence.
- A summary covers one constraint and market over the window. `hours_binding` is the
  sum over covered months. `shadow_price_max` is the maximum, `shadow_price_mean` the
  hours-weighted mean, and `congestion_cost_usd` the sum, over binding months.
- A price or cost appears in a summary only when every binding month publishes it.
  Otherwise `missing` says how many months lack it. Cost is as posted, so a month the
  source posts as negative lowers the summary's total.
- Summary confidence is low for a verified constraint the source never reports
  (`not_seen_in_source`). It is medium when a month is partial or not covered, and
  high otherwise.
- Summaries are sorted by market (`DA`, then `RT`), then observed hours (highest
  first), then `constraint_id`. They are not combined into a score.

## SPP tables (built, not registered)

`headroom.ingest.adapters.spp_panel.normalize(snapshot_dir)` writes both tables from
the raw files the SPP adapter fetches for `spp_data_year`. Those are the twelve monthly
day-ahead binding-constraint archives and the twelve congestion-by-constraint files.
SPP is not a registered panel region, and the snapshot builder does not run this
step. SPP's terms allow non-commercial use with citation, but commercial use needs
SPP's written authorization, and registration waits until that is settled. Writing the
tables into an existing snapshot would change its manifest and break replay of the
runs pinned to it. Once the region is registered they go into a new revision
(`--renormalize-from`), written after `spp.normalize`. That step's tripwires refuse
degraded dollar files, and this one does not repeat them.

| Column | What it holds |
|---|---|
| `constraint_id` | `Constraint Name\|Contingency Name`: the pipeline's flowgate id, so a footprint can name shortlist flowgates |
| `hours_binding` | Distinct UTC hours (`GMTIntervalEnd`) the flowgate bound in the month, counted as the pipeline counts them. The local `Interval` minus one hour sets the month. |
| `shadow_price_max`, `shadow_price_mean` | The maximum and mean \|shadow price\| over the month's rows |
| `congestion_cost_usd` | SPP's congestion for that flowgate and month, negated because SPP posts it as a negative `Sum`. It carries the `spp_congestion_by_constraint` stamp through `column_sources`. A month SPP posts as negative rent is reported as posted, while the pipeline's rent leaves such months out. It is blank where SPP posts nothing, or where the dollar file names another monitored element than the binding record (a recycled id). |
| `monitored_facility`, `contingent_facility` | SPP's most frequent spelling in the month |

- Coverage is day-ahead only. A day counts as covered when the month's archive has any
  row for it. That is a lower bound: a day on which nothing bound reads as uncovered,
  never as zero, and a truncated archive shows up as a partial month. Each month cites
  its binding archive and its dollar file.
- The panel keeps every flowgate, including phase shifters and unrecognized monitored
  elements, which the pipeline does not score. It reports history, not a GET decision.
- Where the two overlap, the panel's monthly hours add up to the pipeline's
  `hours_binding`, and its non-negative monthly dollars add up to `rent_musd`. A dollar
  month with no binding hour that month gets no history row. `normalize` returns how
  many there were (`dollar_months_without_binding`), along with the duplicate,
  negative-rent and recycled-id counts.
- Both SPP sources are publishable in `config/sources.yaml`, so an SPP panel passes the
  gate as shareable. The gate does not check commercial-use terms; that is what keeps
  SPP unregistered.

## Outputs

A shareable panel's files have no suffix. A panel the publishable gate refused gets
`.INTERNAL.synthetic` when `synthetic_fixture` is in its lineage, and `.INTERNAL`
otherwise. The gate refuses when any input table, or the lineage of any emitted value,
uses a source that `config/sources.yaml` does not mark publishable.

| File | One row per | What it is |
|---|---|---|
| `panel.json` | panel | Mode, region, footprint (name, sha256, counts), window, markets, the four tables below, sources (each table's stamp, sha256, `derived_from`, publishable flag and basis; each raw input's url, `retrieved_at` and sha256) and caveats |
| `panel_summary.csv` | constraint and market | Window totals, months by coverage, first and last binding month, corridors and footprint elements, confidence, flags, missing values, reported facilities |
| `panel_monthly.csv` | constraint, market and month | Coverage, binding, values, missing values, flags, `raw_refs`, and the lineage id of `hours_binding` |
| `panel_coverage.csv` | market and month in the window | Coverage status, days covered and expected, `raw_refs` |
| `panel_unresolved.csv` | footprint row not verified | The row's match status and evidence |
| `panel_manifest.json` | panel | Inputs (snapshot manifest sha256, table and raw sha256, footprint name and sha256, window), runtime identity (panel code is part of `source_sha256`), source pins, the sha256 of the panel content, and `blocked_sources` |

Unlike `headroom run`, the panel export writes straight into `--out` without a
staging folder, and it does not check for an earlier panel there. Give each panel its
own `--out`. The command exits 0 whether or not the export was shareable.
