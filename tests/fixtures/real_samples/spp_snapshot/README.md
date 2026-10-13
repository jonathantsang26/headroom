# Real snapshot subset (SPP, 2026-10-13)

A small, internally consistent cut of the live snapshot built on 2026-10-13 by
`python -m headroom.ingest.adapters.build_snapshot --date 2026-10-13`. Rows are
copied verbatim from that snapshot's normalized tables; nothing is edited or
invented. Manifest stamps (source, source_version, retrieved_at, coverage,
contributors, column_sources) are the originals; each note is prefixed
`TEST SUBSET (n of N rows)` and `sha256` is recomputed for the subset files. `raw` is
empty: the raw downloads are not committed.

Contents: 59 SPP 2025 DA flowgates (the 30 with the largest summed |shadow price|,
the 10 smallest, every multi-element interface, the 6 transformers with the largest
observed rent, and 4 with no congestion-by-constraint row, whose rent is unobserved)
with their congestion rows (observed rent stamped `spp_congestion_by_constraint`);
the four EIA-930 2024 SWPP load snapshots; 80 HIFLD lines around FERC-matched lines and
their 82 buses; 26 FERC Form 1 rows (matched and unmatched); 34 queue requests;
header-only `planning` and `eia_lines` (unobserved, as in the live snapshot).
