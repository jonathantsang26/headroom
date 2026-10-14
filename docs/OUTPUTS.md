# Outputs

`headroom run --out DIR` writes one run into `DIR`. A shareable run's files have no
suffix. A run the publishable gate refused gets `.INTERNAL` in every name, or
`.INTERNAL.synthetic` when the region is a synthetic fixture. Files, the manifest included, are written into a hidden staging folder first. A
failure before they are moved leaves an earlier run untouched. The move itself then
removes the old manifest, replaces each file atomically, and adds the new manifest
last. If it is interrupted, the folder holds no manifest (MCP refuses it), and the
error names the staging folder that keeps the new run's remaining files.
`DIR` holds one run: writing a different run there needs `--overwrite`.

| File | One row per | What it is |
|---|---|---|
| `shortlist.csv` | flowgate, in rank order | The forwarded table: every scored flowgate, not only the top k. |
| `scores.json`, `scores.parquet` | flowgate, in rank order | The same rows as JSON, and as Parquet when DuckDB is installed. |
| `shortlist.json` | physical corridor, in rank order | The corridor roll-up (below). |
| `quality_report.json` | run | Table coverage, unobserved signals and the scores they affect, the EV section, attribution and gaps. |
| `NOTICE.md` | run | Mode, input snapshot, every source used with its terms, sources consulted that gave nothing, and caveats. |
| `run_manifest.json` | run | Inputs (paths and sha256), code and runtime identity, source pins, scoring (with GET priors and their sha256), the export decision, attribution, and the sha256 of every file above. `headroom reproduce` replays from it. |

"Shortlist" names the ranked deliverable. The CSV is not cut at k: the shortlist is
the first `shortlist_k` rows in `rank` order (`shortlist_k` is in the manifest).
`p_top_k` says how stable a row's place is. Under `ranking: ev` it mostly reflects the
chance that some GET applies to the element, so do not re-sort by it.

## What a band means

Every `*_lo`, `*_expected` and `*_hi` triple is a `Range`. What `expected` means
depends on the producer:

| Value | lo / expected / hi |
|---|---|
| `composite_*` | p5 / p50 / p95 of the Monte Carlo composite draws |
| `ev_lo/expected/hi_musd` | p5 / p50 / p95 of the realized net value of the GET decision over draws. It is negative when the deployed GET loses money, and 0 when nothing is deployed. |
| `ev_mean_musd` | The expected net value of the decision. Each draw learns which GETs apply, then deploys the applicable GET with the highest expected net value, or nothing. The value is not floored per draw, so no hindsight is used. It ranks under `ranking: ev`, and it is 0 when no GET is worth deploying. |
| `rent_musd` (signal Range) | The observed value is `expected`. lo and hi are a -40% / +50% forward band. |
| `hours_binding` (signal Range) | Observed hours ±20% |

## Flowgate columns (`shortlist.csv`, `scores.json`)

- `rank`, `constraint_id` (`<Constraint Name>|<Contingency Name>`), `physical_corridor`
  (the corridor key: a line with endpoint order ignored, an interface by constraint
  name, or a substation's transformers).
- `p_top_k`: the share of Monte Carlo draws in which the flowgate is in the top k.
  - Under `ranking: ev` it is EV's own measure, and a draw counts only if the realized
    net value is above 0.
  - For EV leaders it converges to P(some GET applies to the element), so it shows
    stability and does not order the list.
- `composite_lo/expected/hi`: the weighted signal composite. Under `ranking: ev` it is
  a diagnostic, and `p_top_k_composite` gives its rank stability.
- `recommended_get`:
  - Under `ranking: ev`, the GET that contributes most to the decision's expected value.
    `No cost-effective GET` means no applicable GET ever has a positive expected net
    value. `get_basis` gives each GET's expected net value where it applies. The
    `p_best_*` columns give how often each option is the one deployed. Flowgates
    without observed congestion dollars keep the evidence rule's choice.
  - Otherwise, the evidence rule's choice.
  - `evidence_rule_get` always holds the evidence rule's choice, made before cost (for
    example APFC wherever an N-1 branch outage implies a parallel path). Under
    `ranking: ev` it often differs from `recommended_get`, which weighs cost.
  - `get_basis` says what each rule saw.
  - `get_evidence` is the evidence class the priors used.
  - `get_candidates` lists the GETs that class does not rule out.
- `p_best_none`, `p_best_dlr`, `p_best_apfc`, `p_best_to`, `p_best_c`: the share of
  draws in which each option is the one deployed (they sum to 1).
- `ev_mean_musd`, `ev_lo_musd`, `ev_expected_musd`, `ev_hi_musd`: the GET decision's net
  value in $M/yr (see the table above). Blank when the flowgate has no observed
  congestion dollars. Rows tied at 0 are ordered by the composite.
- Observed quantities in their own units:
  - `rent_musd`: SPP's posted day-ahead congestion dollars, $M/yr.
  - `value_of_1mw_relief_usd`: Σ|shadow price|, $/MW-yr.
  - `hours_binding`, `breached_hours`, `months_binding`.
- `sig_<signal>`: the expected value of each signal in its own units. `cov_<signal>`
  is `observed` or `unobserved`. An unobserved signal adds 0 to the composite; it is
  not evidence of 0.
- `caution`: `same_station_ends` when a monitored "line" has the same station name at
  both ends. Such an element may be a bus tie or an intra-station segment, where no GET
  applies, and its identity is CEII. Otherwise blank.
- `screen_status`, `monitored_facility`, `contingency_facility`: the DC screen's status
  and SPP's facility text.
- `provenance`:
  - `rent=` names the source of the rent value.
  - `data=` lists the sources reached through observed signals.
  - `absent=` lists sources reached only through unobserved signals: tables that were
    consulted and gave nothing for this flowgate.

Text cells that would start a spreadsheet formula (`=`, `+`, `-`, `@`) are written
with a leading `'`.

## Corridor columns (`shortlist.json`)

Many flowgates can sit on one physical element. A corridor row reports two views:

- **Champion** (ranks the corridors): the member flowgate that ranks highest, in
  `representative_flowgate`, with its `p_top_k`, `composite_*`, `ev_*` and
  `recommended_get`.
- **Sum**:
  - `expected_topk_slots`: the sum of member `p_top_k`, i.e. the expected number of
    top-k slots the corridor holds.
  - `sum_composite_*`.
  - `sum_ev_mean` and `sum_ev_expected`. These are upper bounds, because one
    deployment on the element is counted once per member flowgate.
- `n_flowgates`, `flowgates`, `get_probabilities` (the champion's).

k is set per flowgate (`top_fraction` × scored flowgates), so `p_top_k` on a corridor
row is its champion's.
