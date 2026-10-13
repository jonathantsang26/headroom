# Cross-check against echo-gridlock

echo-gridlock (private repo, `archive/PUDL/A_PUDL.py`, `echo-gridlock-cleaning.ipynb`,
`gets_deployment_prioritization.csv`) is an earlier FERC Form 1 / PUDL analysis. It is
a reference only; nothing from it enters a snapshot. Numbers below come from
`scripts/crosscheck_echo_gridlock.py`. That script reads the pinned PUDL file from the
2026-10-12 snapshot and PUDL v2026.3.0, which reproduces echo-gridlock's `nightly` read.

## FERC Form 1 line miles

| | release | report year | lines | miles | utilities | ≥345 kV, ≥10 mi corridors |
|---|---|---|---|---|---|---|
| echo-gridlock as published | nightly (~Apr 2026) | 2024 | 4,999 | 38,408 | 101 | 81 (3,538 mi) |
| echo-gridlock's rule, reproduced | v2026.3.0 / v2026.4.0 | 2024 | 4,999 | 38,408 | 101 | 81 (3,538 mi) |
| echo-gridlock's rule on the pin | v2024.11.0 | 2023 | 5,050 | 37,548 | 101 | 79 (3,456 mi) |
| corrected rule on the pin, national | v2024.11.0 | 2023 | 24,321 | 276,971 | 157 | 648 (33,634 mi) |
| corrected rule on the pin, SPP respondents | v2024.11.0 | 2023 | 1,898 | 27,218 | 16 | 102 (6,166 mi) |
| echo-gridlock's rule, SPP respondents | v2024.11.0 | 2023 | 124 | 782.5 | 9 | 8 (363 mi) |

The differences have three causes:

1. **Release vintage.** echo-gridlock read PUDL `nightly`, which then held 668,139
   rows through report year 2024. Headroom pins v2024.11.0, which holds 640,619 rows
   ending at report year 2023. On identical rules, the 2023 and 2024 figures differ only
   by a year: 37,548 vs 38,408 miles.
2. **The length column.** PUDL splits schedule 422 pole-miles into
   `transmission_line_length_miles` (lines on stand-alone structures) and
   `transmission_line_and_structures_length_miles` (lines aggregated with other
   structures). echo-gridlock read only the second, for its total and for its ≥10-mile
   corridor filter. After the same cleaning, the pin has 239,422 stand-alone and 37,548
   aggregated miles nationally (2023). echo-gridlock's "38,408 transmission miles" is
   therefore the aggregated-structure subset, about 14% of the cleaned total. For SPP
   respondents it is 2.9% (782.5 of 27,218 mi). Every one of its 8 SPP corridors shows
   the gap:
   - Seminole–Muskogee 345 kV: 13.8 mi in echo-gridlock; FERC also reports 103.5
     stand-alone mi.
   - Muskogee–Pittsburgh 345 kV: 30.5 mi, plus 216.3 stand-alone mi.
   - Stand-alone-only lines like OG&E Sooner–Rose Hill 345 kV (43.9 mi) never pass its
     filter.

   (The raw column sums of 798,430 / 94,578 mi include total and subtotal rows. They are
   not line miles.)
3. **Units.** 121 rows nationally in 2023, all 27 in SPP from Empire District, report
   kV in volts (161000, 345000). echo-gridlock counted them in its line totals, but its
   ≤1000 kV corridor filter dropped them. The adapter divides by 1000, keeps
   `voltage_as_reported` and flags `kv_in_volts`.

## Corridor shortlist

echo-gridlock's 81 corridors are a national asset list: 345 kV+ FERC lines ranked by
voltage × length, ordered by payback. Its "Est. Congestion $/yr" is a top-down
allocation: published ISO-wide congestion totals ($600M for SPP) are spread over lines
by voltage × miles. Nothing observed about any individual line goes into it. 8 corridors
are SPP (7 OG&E, 1 SPS). All 8 rows are present in the pinned 2023 data.

Headroom's SPP shortlist ranks the 1,601 line and interface flowgates that actually
bound in SPP's 2025 day-ahead market. It rolls them up to 732 corridors, ranked by
reconstructed rent, binding hours and cost of inaction under Monte Carlo rank
stability. SPP names these elements by market-model bus names, and their locations are
CEII, so no public join exists between the two lists. A name search of all 969 monitored
facilities found:

- 7 of the 8 echo-gridlock SPP lines have no recognizable SPP 2025 binding constraint:
  Woodward–Beaver County, Woodward–Thistle, Horseshoe Lake–Seminole, Mathewson–Cimarron,
  Hitchland–Beaver Co, Seminole–Muskogee, Muskogee–Pittsburgh.
- Draper Lake–Seminole plausibly matches `LN DRAPR1 - SEMINOLE`: corridor rank 589 of
  732, p_top_k 0.00. This is a name guess, not a verified join.
- `LN PITTSB9 - SEMINOLE` (rank 320, p_top_k 0.00) is a different line, Pittsburg–Seminole.

None of echo-gridlock's SPP corridors reaches Headroom's top quartile (k = 400
flowgates). The lists disagree because they measure different things. echo-gridlock
measures how big a 345 kV+ asset is. Headroom measures where SPP's market was observed
to bind in 2025. Its top ten corridors bound 671–5,724 hours each, mostly under
contingencies in the OG&E, GRDA, CSWS and Westar areas of Oklahoma and Kansas. The
monitored elements' voltages are not public.

## Scale check on congestion dollars

Since snapshot 2026-10-13, rent is observed. It is no longer Σ|shadow price| × an
assumed rating band. SPP's monthly congestion-by-constraint (CBC) files post the
day-ahead congestion dollars for each constraint and contingency. Headroom joins them
to the binding constraints on name, contingency and monitored element. Three 2025
totals can be compared:

| Measure | 2025 DA congestion | Source |
|---|---|---|
| CBC, sum over every constraint | $2,410.0M | SPP congestion by constraint, 12 monthly files |
| CBC, flowgates Headroom scores | $2,408.7M (2,054 of 2,072 flowgates) | the same, after the join |
| TCR funding, DA Collected | $1,957.8M | SPP TCR funding, 12 monthly files |
| Market Monitor, DA congestion | $1,977M | SPP MMU State of the Market 2025, Fig. 5-22 |

TCR DA Collected matches the Market Monitor's figure to 99.0%. CBC is 1.23× both. The
gap is not netting: no CBC constraint-month is negative, and 73 exact duplicate rows
are dropped before summing. It is per constraint. On the 4,153 (month, constraint)
pairs present in both files, CBC sums to $2,370.3M and DA Collected to $1,942.6M. Among
the 100 largest constraints by CBC, the CBC/DA Collected ratio is 1.13 at p50 (1.01
p10, 2.20 p90). The largest gaps are on external or coordinated flowgates (for
example `FT_CAFT_CA34_2S_LN@FT_CAL<1991>`) and a few transformers. SPP does not
document the difference in these files. One plausible cause is that DA Collected counts
only the rent collected in SPP's settlement while CBC prices total flow, but this has
not been confirmed.

What this means for the ranking:
- Annual totals per constraint agree in order: Spearman 0.966 over the 1,855
  constraints in both files, and 47 of the top 50 are the same.
- Absolute EV and rent figures are probably high by about 13% at the median and more
  on coordinated flowgates. They are stated as SPP's posted per-constraint dollars,
  not as collected rent.
- TCR DA Collected is kept as a calibration total in the congestion table's note. It
  is not used per flowgate.

The 2026-10-12 snapshot had no dollar files. Its rent was Σ|SP| × a 100–1,200 MW band
(mode 250 MW): $2.12B at the mode across 1,601 flowgates. The observed CBC total
implies an |SP|-weighted limit of about 214 MW (p50 169 MW per flowgate), so the old
mode was about 17% high in aggregate.
