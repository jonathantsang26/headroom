# Flowgate-level evidence in the EV layer

A reviewer of the project page saw that every recommendation in the 103-row shortlist
was the same, with the same probabilities. That came from the model, not from the
display. This note covers which flowgate-level evidence the public SPP snapshot can
add, what the EV layer now uses, and what changed in the run.

**Short version.** One signal is used: the season in which each flowgate's 2025
congestion dollars fell. It scales DLR's relief share, so a line whose congestion
falls in summer gets less from DLR than one that binds in winter. That signal has a
direction in the cited sources. It moves the EV ranking and some GET picks. It cannot
move most of the probability splits, because those splits are the per-class
applicability priors, and no public evidence says whether a given element's limit
can be relieved by a given GET. The evidence rule (`evidence_rule_get`) does not
change.

## 1. Why every flowgate looked the same

For flowgate f and GET g, ev.py deploys the applicable GET with the highest
E[u_g] × E[h] × E[R_f] − E[C_g]. Before this change, u, h and C were the same for every
flowgate, and applicability was set per evidence class (`line_n1`, `transformer_n1`,
...). So within a class:

- **The ordering of GETs depended only on forward rent.** DLR's mean relief (0.083) is
  about TO's (0.080) and APFC's (0.087), at about a third of TO's mean cost and a
  thirteenth of APFC's. Wherever DLR can apply it beats TO and APFC unless rent is
  very large, so every line with a GET worth deploying got DLR (45) and every
  transformer got TO (8).
- **The splits were the applicability priors.** For a `line_n1` flowgate where only DLR
  and TO are worth deploying, nothing is deployed with probability (1 − 0.40)(1 − 0.30)
  = 0.42, DLR is deployed with 0.40, and TO with 0.30 × 0.60 = 0.18. That holds for
  every such flowgate.
- **Value tracked rent.** Within `line_n1`, ev_mean had Spearman 0.999 with rent ×
  months binding / 12.

`evidence_rule_get` is a separate rule over the element type and the contingency. It
gives APFC to 1,901 of 2,072 flowgates, because SPP's bus map is CEII and the rule
cannot see topology. This change leaves it alone. Under `ranking: ev`,
`recommended_get` is the EV layer's pick, and that is the column that moves.

## 2. The candidates, measured on snapshot 2026-10-13

| Candidate | What the public data shows | Used | Why |
|---|---|---|---|
| Season of the congestion dollars | SPP's congestion-by-constraint dollars are monthly. In the top 103, the share falling in Dec-Feb runs p10 0 / p50 0.16 / p90 0.81, and in Jun-Aug 0 / 0.12 / 0.67. 23 of the 103 have half or more in summer, 24 have half or more in winter. | **Yes, for DLR relief** | DLR's gain over ambient-adjusted ratings (AAR) is smallest in hot, calm summers. The over-AAR ceiling is about 9-16% in summer against 22-30% in winter (aar-2 via dlr-10). The signal is observed dollars, so it weights each season by the value at stake. |
| Hour of day | From the hourly DA files, the share of a flowgate's \|shadow price\| in hours ending 13-20 runs p10 0.23 / p50 0.37 / p90 0.66 in the top 103. | No | The direction is not signed. Afternoons are hotter and sunnier, but surface winds mix down. Nights have no sun, which AAR's day and night ratings already credit, and calmer surface winds. The sources disagree on the sign between wind-export and summer-peak constraints (GET_PRIORS.md, section 10, item 6). |
| Breached hours, shadow-price shape | 54 breached hours of 135,820 binding hours, on 22 flowgates (4 in the top 103). In the top 103, the top 10% of hours hold 24-39% of Σ\|SP\|, and median/mean \|SP\| runs 0.58-0.92 (both p10-p90). | No | These would bear on the haircut h, through the margin at which a constraint stops binding. The DA files do not record that margin. Mapping shape to h would be a new assumption on the most sensitive prior, whose hi is set by a system cap ev.py cannot enforce per flowgate. Breached hours already feed the evidence rule's "heavy" test. |
| Contingency structure | Of 1,682 line flowgates, 1,480 bind after a two-station branch outage and 170 at BASE. The rest are a handful of free-form strings. | Already in the class | BASE against N-1 is already the evidence class. Within N-1, nothing left has a cited effect on APFC or TO. The 16 "lines" with the same station at both ends (2 in the top 103) keep their `caution`. Their constraint names suggest series devices, but that is not verified. |
| HIFLD name match for voltage and length | Both ends of a monitored line match one HIFLD line's `sub_1`/`sub_2` exactly for 3 of 1,682 line flowgates. 50 of 1,057 SPP station names match any HIFLD substation name. | No | Too few unambiguous matches to matter. A looser match would not be unambiguous. Separately, SPP's own contingency strings name the kV of the monitored line for 435 line flowgates (21 in the top 103), but no cited cost or applicability gradient by voltage exists in the priors to use it. |

## 3. How the season factor enters ev.py

- **Data.** The SPP adapter splits each flowgate's congestion-by-constraint dollars by
  meteorological season: `rent_djf_musd`, `rent_mam_musd`, `rent_jja_musd` and
  `rent_son_musd` in the congestion table. December counts in the data year's DJF. The
  four sum to `rent_musd` (checked when loaded), and are stamped
  `spp_congestion_by_constraint` like `rent_musd`. Each is a Fact. Snapshot
  `2026-10-13.r3` was renormalized from `2026-10-13.r2`'s raw files with no network.
  Only the congestion table changed, and its existing columns are byte-identical.
- **Formula.** u_DLR,f = u_DLR × (1 + Σ_s share_f,s × (k_s − 1)), where share_f,s is
  the flowgate's share of its dollars in season s. k_s comes from the priors
  (`gets.Dynamic Line Rating.season_factor`): JJA 0.5 / 0.7 / 0.9, DJF 1.0 / 1.3 / 1.5,
  and spring and fall count 1. Each k_s is drawn once per draw, shared by all
  flowgates and independent of u, like u itself. The expected net value uses
  E[u] × (1 + Σ share × (E[k] − 1)). Combination inherits the factor through DLR's
  relief.
- **Lineage.** Each flowgate's factor is a `Range` (`season_factor:dlr:<cid>`) at the
  priors' lo, mean and hi. Its inputs are the four season Facts, so it reaches only
  `spp_congestion_by_constraint`. The EV Range cites it, and its basis states the
  factor. The factor values are priors: they are in the basis, never lineage leaves.
- **Unobserved stays 1.** A snapshot without the season columns (anything before
  r3, and the committed test subset), or a flowgate with no positive dollars, keeps
  factor 1. Its EV basis says the factor is unobserved. It is never 0. A GET that cannot
  apply to the class (DLR on a transformer) gets no factor.
- **The SPP-wide level is kept.** 2025 dollars fell 30.0% in DJF, 28.3% in MAM, 19.3%
  in JJA and 22.5% in SON. The rent-weighted mean factor is 1.02 over all flowgates and
  1.00 over the lines and interfaces DLR can apply to. The change redistributes DLR's
  value between flowgates rather than raising it.
- **The to-35 cap.** With the winter hi, u × factor × h can reach 0.15 × 1.5 × 0.53 =
  0.119, above the 0.08 cap. For a winter-only line that happens in 0.5% of draws.
  Rent-weighted over every line and interface, it is 0.06% of flowgate-draws. The cap
  is a system total, and the sum of mean EV fell from $11.44M to $11.31M/yr, so the
  aggregate check holds.
- **Why the values.** GET_PRIORS.md, section 4.2.

## 4. The run, before and after

- **Before:** commit `3e57765` (main) on snapshot `2026-10-13.r2`, priors `.2`. Ranking
  digest `a6750ade…`, the README's verified run, reproduced here.
- **After:** commit `eb8939c` on snapshot `2026-10-13.r3`, priors
  `spp-get-priors-2026-10-13.3` (sha256 `503e04a9…`). Shareable. Ranking digest
  `db3d36ae…`, and `headroom reproduce` rebuilds byte-identical scores, shortlist,
  CSV, quality report and NOTICE.
- The old manifest still replays byte-identical at `3e57765`. At the new commit it is
  refused, because the pipeline code changed. Neither snapshot was modified.

| | Before | After |
|---|---|---|
| Flowgates with a GET worth deploying | 53 | 55 |
| Their picks (`recommended_get`) | DLR 45, TO 8 | DLR 46, TO 9 |
| Picks across all 2,072 | none 2,001, APFC 17, DLR 46, TO 8 | none 1,999, APFC 17, DLR 47, TO 9 |
| Distinct probability splits among the positive ones, to the nearest 0.05 | 8 | 9 |
| Distinct splits to 4 decimals (mostly Monte Carlo noise) | 49 | 51 |
| Top 103: members changed | | 0 in, 0 out |
| Top 103: pick changed | | 3 |
| Top 103: probability split changed (to 4 decimals) | | 55 |
| Top 103: rank changed | | 54 |
| Spearman of ev_mean with rent × months / 12, positive `line_n1` | 0.999 (41) | 0.977 (43) |
| Sum of mean EV | $11.44M/yr | $11.31M/yr |
| `evidence_rule_get` | APFC 1,901, DLR 170, DLR+APFC 1 | unchanged |

APFC picks across all 2,072 are flowgates without observed dollars, which keep the
evidence rule's pick.

The three picks that changed:
- **#2, LN CLEVLND7 - CLEV_AEC** (`TMP270_23432|18695`): DLR to **TO**. 68% of its
  $83.3M fell in June-August, so its DLR factor is 0.81. Where both apply, TO's
  relief now beats DLR's at this rent.
- **#50 (`TMP709_30212|7525`) and #52 (`TMP760_29357|7213`)**: none to **DLR**. Both are
  winter-heavy (factors 1.23 and 1.16), which lifts DLR above its cost.

New top 10 (flowgates; GET is `recommended_get`; p is none / DLR / APFC / TO / DLR+APFC):

| # | Monitored element | Class | EV mean, $M/yr | 2025 rent | DJF / JJA share | DLR factor | GET | p | Before |
|---|---|---|---|---|---|---|---|---|---|
| 1 | LN OSAGE_OG - WEBBTAP4 | line_n1 | 2.287 | $97.0M | 27% / 1% | 1.07 | DLR | 0.25 / 0.25 / 0.17 / 0.18 / 0.15 | #1, DLR, 2.222 |
| 2 | LN CLEVLND7 - CLEV_AEC | line_n1 | 1.588 | $83.3M | 7% / 68% | 0.81 | **TO** | 0.26 / 0.17 / 0.17 / 0.25 / 0.16 | #2, DLR, 1.761 |
| 3 | LN NEBRCTY - SUB3456 | line_n1 | 1.546 | $77.3M | 12% / 36% | 0.93 | DLR | 0.25 / 0.24 / 0.17 / 0.18 / 0.16 | #3, DLR, 1.616 |
| 4 | LN RUSSETT - SBROWN | line_n1 | 1.269 | $64.9M | 25% / 14% | 1.02 | DLR | 0.25 / 0.24 / 0.17 / 0.18 / 0.16 | #4, DLR, 1.224 |
| 5 | LN FT_CAL - FT_CAL | line_n1 | 0.507 | $42.6M | 28% / 16% | 1.03 | DLR | 0.41 / 0.40 / 0 / 0.18 / 0 | #5, DLR, 0.492 |
| 6 | LN OASIS - SAN_JUAN | line_n1 | 0.286 | $22.0M | 5% / 15% | 0.97 | DLR | 0.43 / 0.39 / 0 / 0.18 / 0 | #6, DLR, 0.300 |
| 7 | XFMR EDWV - EDWV | transformer_n1 | 0.274 | $60.9M | 86% / 0% | n/a | TO | 0.70 / 0 / 0 / 0.29 / 0 | #7, TO, 0.281 |
| 8 | LN GORD - MAIZ | line_n1 | 0.255 | $40.2M | 0% / 8% | 0.97 | DLR | 0.43 / 0.40 / 0 / 0.17 / 0 | #8, DLR, 0.267 |
| 9 | LN ANADARKO - GRACMONT | line_n1 | 0.252 | $30.6M | 48% / 0% | 1.13 | DLR | 0.42 / 0.40 / 0 / 0.18 / 0 | #9, DLR, 0.220 |
| 10 | LN CARTR2 - CKSAW1 | line_n1 | 0.221 | $61.9M | 80% / 0% | 1.21 | DLR | 0.42 / 0.40 / 0 / 0.18 / 0 | #12, DLR, 0.174 |

`LN STONEWSW - TUPELO2` (#10 before) is now #12.

## 5. What it does not fix, and what would

Most splits are still the class priors: 28 of the 55 positive flowgates have the
line split none 0.6 / DLR 0.4, and all 8 transformers have none 0.7 / TO 0.3. To move
them, the model needs evidence on whether a GET can relieve a given element's limit:

- **Applicability per element.** Whether the conductor sets the limit (against
  terminal equipment or stability) decides DLR, and whether a parallel path has spare
  capacity decides APFC and TO. Both need the element's identity, ratings or topology.
  SPP's are CEII.
- **A per-GET haircut, or a u-h correlation** (GET_PRIORS.md, section 10, item 8). That
  is a model change, not new evidence.
- **One deployment per element.** One DLR on a line relieves every flowgate on it, but
  each flowgate now pays the full cost. Valuing per element would favor elements that
  bind under many contingencies. That is a model change too.
- **Voltage from SPP's contingency names** (435 line flowgates), if a cited cost or
  applicability gradient by voltage is found.
