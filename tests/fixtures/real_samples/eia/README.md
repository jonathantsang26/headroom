# EIA real samples

Recorded 2026-10-12 (07:26–07:27 UTC).

## EIA-860 via PUDL `v2024.11.0` (pin `pudl_v2024.11.0:eia860_2024`)

Sources: `https://s3.us-west-2.amazonaws.com/pudl.catalyst.coop/v2024.11.0/out_eia__yearly_plants.parquet`
and `…/out_eia__yearly_generators.parquet`. Extracted with DuckDB
`COPY (SELECT …) TO … (FORMAT PARQUET)`; values unmodified, only rows and columns cut:

- `out_eia__yearly_plants.parquet`: `plant_id_eia, plant_name_eia, report_date, latitude,
  longitude, balancing_authority_code_eia, state`.
- `out_eia__yearly_generators.parquet`: `plant_id_eia, generator_id, report_date,
  operational_status, operational_status_code, capacity_mw`.

Rows: report years 2023 and 2024 for the 9 plants that are the nearest located EIA-860
plant to, or within 40 km of, an endpoint of the `../hifld` sample lines (R M Heskett
2790 and seven wind/coal plants in BA MISO; Mandan Refinery 52133 in BA SWPP).
PUDL marks these 2024 rows `data_maturity = monthly_update` (EIA-860M based).

## EIA-930 six-month bulk CSVs (pin `eia930_2024`; no API key)

Sources: `https://www.eia.gov/electricity/gridmonitor/sixMonthFiles/EIA930_BALANCE_2024_Jan_Jun.csv`
and `…_Jul_Dec.csv`. Each sample keeps the file's header line and data lines verbatim:

- `Jan_Jun`: SWPP for local dates 04/06/2024–04/07/2024 (holds the 2024 SWPP max wind
  share hour, 2024-04-07 10:00 UTC) and MISO for 04/06/2024.
- `Jul_Dec`: SWPP for 07/15/2024–07/16/2024 (holds the 2024 SWPP peak, 54,247 MW at
  2024-07-15 22:00 UTC) and MISO for 07/15/2024.

The two halves have different headers: from July 2024 EIA splits wind into
"with/without Integrated Battery Storage" columns.
