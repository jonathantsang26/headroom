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

## Scale check on reconstructed rent

SPP posts shadow prices, not dollars. Rent = Σ|shadow price| × the element's limit, and
the limit is not public, so it enters as a stated 100–1,200 MW band with mode 250 MW.
Across the 1,601 flowgates, Σ|SP| is 8.50 M$/MW, which gives $0.85B / $2.12B / $10.2B
at the band's low / mode / high. The only SPP Market Monitor figure found is about
$1.2B of congestion in 2021 (draft report, via S&P Global). That suggests the 250 MW
mode is on the high side. A single shared rating band scales every flowgate's expected
rent by the same factor, so it changes magnitudes and band widths, not the order of
expected rents. Calibrating it would need a public per-constraint limit, such as SPP's
flowgate ratings. That is a follow-up.
