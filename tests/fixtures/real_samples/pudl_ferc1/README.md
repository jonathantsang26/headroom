# Recorded real sample: PUDL FERC Form 1 schedule 422 + HIFLD lines

Real public data, cut down for offline tests of `headroom.ingest.adapters.pudl_ferc1`.
Values are unedited.

## `transmission_lines_sched422_sample.parquet`

- Source: PUDL v2024.11.0 `out_ferc1__yearly_transmission_lines_sched422.parquet`,
  `https://s3.us-west-2.amazonaws.com/pudl.catalyst.coop/v2024.11.0/out_ferc1__yearly_transmission_lines_sched422.parquet`
  (sha256 `be9c9bcc2a01a63505c3248aa6301be0dad8a1d3e42d6bfe2752a2ddd24a1b78`, 640,619 rows,
  report years 1994-2023), downloaded 2026-10-12T07:02Z.
- Extract: 25 rows chosen by `record_id`, all columns, written with
  `COPY (SELECT * FROM read_parquet(...) WHERE record_id IN (...) ORDER BY record_id)
  TO '...' (FORMAT PARQUET)` (duckdb 1.5.6).
- Chosen to cover: OG&E Sooner-Rose Hill (WERE) (stand-alone miles only), Sooner-Woodring
  and Sooner-Cleveland (GRDA) (exact HIFLD matches), Sooner-Thunder Ranch (capex 0),
  Tatonga-Mathewson Line 2 (two HIFLD candidates), Woodward District EHV-Beaver County
  (2 circuits, both length columns), Cimarron-Cornville, OG&E's total row, OG&E
  Sooner-Woodring for 2022 (an older year); PSO's four TL90-902 Oneta-Riverside segment
  rows, TL90-914 Tulsa North-Wekiwa and a SubTotal row; SPS Hitchland-Potter Co, KCP&L
  LaCygne-Stilwell, Westar Swissvale-Lang; Empire's 345000-volt and zero-mile
  structure-class rows; a GMO row with no endpoints; Basin's "LRS plant site lines";
  AEP Southwestern Transmission's only row; and two non-SPP respondents (Ameren
  Cahokia-Roxford_4, Con Edison's 345000-volt Pleasant Valley row).

## `hifld_lines.csv`

- Source: HIFLD Open `Electric_Power_Transmission_Lines` FeatureServer layer
  (services1.arcgis.com/Hp6G80Pky0om7QvQ), envelope query from
  `config/regions/spp.yaml`, retrieved 2026-10-12T07:04Z.
- Extract: 15 features by `ID` in the contract's `hifld_lines` columns: `line_id` =
  `HIFLD_<ID>`; `voltage_kv`, `owner`, `sub_1`, `sub_2`, `status`, `type`, `inferred` as
  served; `length_mi` = haversine over the WGS84 polyline. `from_bus`/`to_bus` are blank:
  bus minting belongs to the HIFLD adapter and the FERC 1 join does not read them.
- Chosen to cover: exact matches (130857, 113704, 310227, 143356, 117710, 123438), the
  Sooner-Rose Hill split at Charles D. Lamb Energy Center (162474, 162472), one line
  claimed by four PSO rows (140010), two Tatonga-Mathewson lines (167240, 169921), and
  placeholder endpoints TAP*/NOT AVAILABLE/UNKNOWN*/NONE (143680, 113620, 104695, 313410).
