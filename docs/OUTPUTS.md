# Outputs

`headroom run --out DIR` writes one run into `DIR`. A shareable run's files have no
suffix. A run the publishable gate refused gets `.INTERNAL.synthetic` in every name.
`DIR` holds one run: writing a different run there needs `--overwrite`.

| File | One row per | What it is |
|---|---|---|
| `shortlist.csv` | flowgate, in rank order | The forwarded table: every scored flowgate, not only the top k. |
| `scores.json`, `scores.parquet` | flowgate, in rank order | The same rows as JSON, and as Parquet when DuckDB is installed. |
| `shortlist.json` | physical corridor, in rank order | The corridor roll-up (below). |
| `quality_report.json` | run | Table coverage, unobserved signals and the scores they affect, the EV section, attribution and gaps. |
| `NOTICE.md` | run | Mode, input snapshot, every source used with its terms, sources consulted that gave nothing, and caveats. |
| `run_manifest.json` | run | Inputs (paths and sha256), code and runtime identity, source pins, scoring (with GET priors and their sha256), the export decision, attribution, and the sha256 of every file above. `headroom reproduce` replays from it. |

"Shortlist" names the ranked deliverable. The CSV is not cut at k. The top-k
membership a reader should use is `p_top_k`, or the first `shortlist_k` rows, where
`shortlist_k` is in the manifest.

## What a band means

Every `*_lo`, `*_expected` and `*_hi` triple is a `Range`. What `expected` means
depends on the producer:

| Value | lo / expected / hi |
|---|---|
| `composite_*` | p5 / p50 / p95 of the Monte Carlo composite draws |
| `ev_lo/expected/hi_musd` | p5 / p50 / p95 of the best-GET net value over draws. With a large chance that no GET applies, p50 can be 0 while the mean is positive. |
| `ev_mean_musd` | The mean of the same draws: the expected net value. It ranks under `ranking: ev`. |
| `rent_musd` (signal Range) | The observed value is `expected`. lo and hi are a -40% / +50% forward band. |
| `hours_binding` (signal Range) | Observed hours ±20% |

## Flowgate columns (`shortlist.csv`, `scores.json`)

- `rank`, `constraint_id` (`<Constraint Name>|<Contingency Name>`), `physical_corridor`
  (the corridor key: a line with endpoint order ignored, an interface by constraint
  name, or a substation's transformers).
- `p_top_k`: the share of Monte Carlo draws in which the flowgate is in the top k.
  - Under `ranking: ev` it is EV's own measure, and a draw counts only if the best
    net value is above 0.
  - For EV leaders it converges to P(some GET applies to the element), so it shows
    stability and does not order the list.
- `composite_lo/expected/hi`: the weighted signal composite. Under `ranking: ev` it is
  a diagnostic, and `p_top_k_composite` gives its rank stability.
- `recommended_get`:
  - Under `ranking: ev`, the option with the highest net value in the most draws,
    which can be `No cost-effective GET`.
  - Otherwise, the evidence rule's choice.
  - `get_rule` always holds the evidence rule's choice.
  - `get_basis` says what each rule saw.
  - `get_evidence` is the evidence class the priors used.
  - `get_candidates` lists the GETs that class does not rule out.
- `p_best_none`, `p_best_dlr`, `p_best_apfc`, `p_best_to`, `p_best_c`: the share of
  draws in which each option is best (they sum to 1).
- `ev_mean_musd`, `ev_lo_musd`, `ev_expected_musd`, `ev_hi_musd`: best-GET net value in
  $M/yr (see the table above). Blank when the flowgate has no observed congestion
  dollars.
- Observed quantities in their own units:
  - `rent_musd`: SPP's posted day-ahead congestion dollars, $M/yr.
  - `value_of_1mw_relief_usd`: Σ|shadow price|, $/MW-yr.
  - `hours_binding`, `breached_hours`, `months_binding`.
- `sig_<signal>`: the expected value of each signal in its own units. `cov_<signal>`
  is `observed` or `unobserved`. An unobserved signal adds 0 to the composite; it is
  not evidence of 0.
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
