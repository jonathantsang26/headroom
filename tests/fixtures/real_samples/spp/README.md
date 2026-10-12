# Recorded real SPP samples (offline adapter tests)

Small cuts of real public downloads, used by `tests/test_adapter_spp.py`. Not synthetic:
every data line is copied verbatim from the source file. Nothing here is edited by hand.

| file | source | taken |
|---|---|---|
| `da_bc_2025MM_sample.csv.zip` (MM = 01..12) | `https://portal.spp.org/file-browser-api/download/da-binding-constraints?path=%2F2025%2FMM%2FDA-BC-MONTHLY-2025MM.csv.zip` (member `DA-BC-MONTHLY-2025MM.csv`) | downloaded 2026-10-12 |
| `gi_active_queue_sample.csv` | `https://opsportal.spp.org/Studies/GenerateActiveCSV` ("Last Updated On" 10/9/2026) | downloaded 2026-10-11 |
| `buses_sample.csv` | HIFLD Open `Electric_Power_Transmission_Lines` (SUB_1/SUB_2 names) | downloaded 2026-10-12 |

- **DA binding constraints**: twelve monthly archives, each holding the monthly file's
  original header line plus its data lines for the sample rows, in original order. The
  sample rows are every row of nine flowgates (`TMP363_30990|18875`,
  `SUB1399_1500_1_LN|BASE`, `SWECHIMATTAT|5530`, `TMP800_29528|BASE`,
  `T2T2_H_XF@FTSMTH<17246>|17246`, `HUGOPP4|BASE`, `FULTON_T_1961A_A_LN|BASE`,
  `TMP272_27635|14756`, `CARNGJAMES11_11_LN@JAMESTN<16491>|16491`) as published in the
  yearly file, and every row whose local `Interval` is 11/02/2025 01:00-03:00 (the DST
  fall-back window, where `Interval` repeats with two `GMTIntervalEnd` hours) or
  01/01/2026 00:00 (hour-ending label of 2025's last hour). All 468 were found verbatim in
  the monthly files. January also carries the first six rows of `TMP452_24498`
  (`LN LIBERAL_ - TEXAS_CO`), which bound 361 January hours but is missing from
  `DA-BC-YEARLY-2025.csv`: the yearly file is a strict subset of the monthly files
  (6,344 FG rows, 123 constraints, 4.55% of summed |shadow price|). Covers LN, XFMR,
  Multi-Element and blank monitored elements, BASE contingencies, BREACHED rows,
  reversed-endpoint spellings and multi-spelling contingent facilities. The lines are
  verbatim; the zip containers were rewritten (deflate, same member names), so their bytes
  differ from SPP's archives.
- **GI active queue**: the first two lines (`"Last Updated On"` and the header) and 42
  records, all verbatim (CRLF; one POI holds an embedded newline). Picked to include
  commercial-operation, `None`, `ICS`, on-suspension, study-stage, blank-POI, blank-fuel
  and line-POI requests. The active export carries no withdrawn requests (no WITHDRAWN
  status, `Date Withdrawn` always blank), so none can be sampled.
- **Buses**: 13 real HIFLD substation names chosen to exercise exact and ambiguous POI
  matches; `lat`/`lon` are the endpoint vertex of the HIFLD line (`hifld_line_id`)
  carrying that name. `ba` is blank and `role`/`weight` are the contract defaults: this
  is not the HIFLD adapter's output, and `bus_id` numbering is local to this sample.
