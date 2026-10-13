# Live adapters and the normalized-table contract

`python -m headroom.ingest.adapters.build_snapshot --date YYYY-MM-DD` builds a dated
snapshot under `data/raw/spp/`. Every adapter's `fetch(snapshot_dir)` runs first
(verbatim downloads into `raw/`, hashed via `record_raw`); then each `normalize(snapshot_dir)`
runs in this order, because each join depends on the previous step:

1. `hifld` — footprint, buses, `hifld_lines` (needs EIA-860 plants from the `eia` raw files)
2. `eia` — `eia_lines`, `load_930`
3. `pudl_ferc1` — `ferc1_lines`, joined onto HIFLD line ids
4. `spp` — `constraints`, `congestion`, `planning`, `queue` (queue POIs map onto buses)

Region choices (footprint, FERC 1 respondents, data years) live in
`config/regions/spp.yaml`. Source pins live in `config/sources.yaml`; an adapter that
sees a different upstream version raises (`common.require_pin`) instead of building on it.

## Rules

- Tables are written only through `common.write_table`, which writes deterministic
  CSV bytes, records `sha256`, the stamp (`source`, `source_version`, `retrieved_at`
  = the raw file's retrieval time), `coverage` and `note`.
- `coverage`: `observed` (the table covers its domain), `partial` (some rows could not
  be obtained or joined; the note quantifies it) or `unobserved` (the data could not
  be obtained; header-only CSV). Unobserved is never zero.
- `contributors`: extra sources a table was derived from, each a full stamp.
- Never fabricate a value or a join. A row that cannot be joined keeps an empty key
  and is counted in the note. Keep audit columns (names, ids, match evidence).
- Extra columns are allowed; the loader reads only the required ones.

## Tables (required columns first)

| table | required columns | extra columns | stamp source |
|---|---|---|---|
| `hifld_lines` | `line_id, from_bus, to_bus, voltage_kv, length_mi` | `hifld_id, owner, sub_1, sub_2, status, type, inferred` | `hifld_transmission` |
| `buses` | `bus_id, name, ba, lat, lon, role, weight` | `n_lines, gen_mw, n_plants, name_basis, ba_plant_id, ba_plant_km, component` | `hifld_transmission` + contributor `eia_860_861` |
| `eia_lines` | `line_id, voltage_kv` | | `eia_860_861` |
| `load_930` | `snapshot, ba, load_mw` | `utc_hour, basis, wind_share` | `eia_930` |
| `ferc1_lines` | `line_id, voltage_kv, conductor, thermal_limit_mw, cost_usd` | `record_id, utility_id_ferc1, utility_name, report_year, start_point, end_point, length_mi, standalone_mi, aggregated_mi, structure, num_circuits, voltage_as_reported, flags, match, match_evidence` | `pudl_ferc_form1` + contributor `hifld_transmission` (the join) |
| `constraints` | `constraint_id, monitored_line, contingency_line, ctype` | `monitored_facility, monitored_facility_as_reported, contingency_facility, contingency_name, spp_constraint_type, contingency_to, contingency_kv` | `spp_binding_constraints` |
| `congestion` | `constraint_id, monthly_cost_musd, hours_binding` | `shadow_price_sum_usd_per_mw, max_abs_shadow_price, breached_hours, months_binding, period, rent_musd, max_month_cost_musd, rent_basis, flags` | `spp_binding_constraints`; `rent_musd`/`max_month_cost_musd` stamped `spp_congestion_by_constraint` via `column_sources`; contributors `spp_congestion_by_constraint`, `spp_tcr_funding` |
| `planning` | `constraint_id, plan, proposed_fix, assigned_cost_musd` | | `spp_itp` |
| `queue` | `request_id, near_bus, mw, fuel` | `poi, state, to_at_poi, status, match` | `spp_queue` |

Column semantics:

- `line_id` = `HIFLD_<ID>`; `bus_id` = `B<n>`, numbered by sorted (lat, lon).
  `from_bus`/`to_bus` come from snapping line endpoints (`bus_snap_m`), not names.
  `length_mi` is geodesic (haversine over the WGS84 polyline), not `Shape__Length`.
  Footprint: a bus takes the BA of its nearest located EIA-860 plant; lines with both
  ends in the region's BA are kept, then only the largest connected piece (the DC
  screen needs one network; the small pieces are labelling/seam artifacts, counted).
- `buses.role` is `gen` when EIA-860 operating capacity snaps to the bus (`weight` =
  MW), else `load` with `weight` 1.0 (no public nodal load; stated in the note).
- `eia_lines`: EIA-860/861 publish no line-level voltages. Header-only, `unobserved`.
- `load_930`: SWPP hourly demand for `eia930_year`; snapshots `peak` (max demand hour),
  `offpeak` (min), `shoulder` (median), `windhigh` (max wind share of net generation).
- `ferc1_lines`: SPP respondents (`ferc1_respondents`), latest report year in the pin.
  `length_mi` = stand-alone + aggregated pole-miles (`transmission_line_length_miles`
  + `transmission_line_and_structures_length_miles`). kV reported in volts (161000)
  is divided by 1000 with `voltage_as_reported` kept and `kv_in_volts` in `flags`.
  `thermal_limit_mw` is blank: Form 1 reports no rating; `cost_usd` is blank where
  capex is null or 0. `line_id` is set only on an unambiguous HIFLD match (both
  endpoint names and voltage), else blank. `flags`: `kv_in_volts, voltage_implausible,
  same_endpoints, aggregate_name, both_length_columns, capex_zero,
  negative_length_part`. A row with both length columns is often a second circuit on
  shared structures, so its `length_mi` counts both circuits.
- `constraints`: SPP DA binding flowgates for `spp_data_year` whose monitored
  element is a line (`ctype=thermal`), a transformer (`ctype=transformer`) or a
  multi-element/unnamed interface (`ctype=interface`). Phase-shifter constraints
  (`*_PHSHFT_PS`) are excluded and counted: the limit is the device's own, and the
  device is itself a power-flow controller.
  `constraint_id` = `<Constraint Name>|<Contingency Name>`. `monitored_facility` is the
  corridor key: `LN` endpoints sorted (SPP spells some lines both ways round), and
  multi-element interfaces suffixed with their constraint name (SPP names them all
  "Multi-Element Constraint"); SPP's text is kept in `monitored_facility_as_reported`. `monitored_line` is blank
  unless resolved to HIFLD (SPP names are model bus names; the bus-location model is
  CEII). `contingency_line` blank for `BASE` or unresolved.
- `congestion`: one row per constraint: `hours_binding` = distinct binding UTC hours
  (`GMTIntervalEnd`), `shadow_price_sum_usd_per_mw` = sum of |shadow price| over those
  hours. SPP's local `Interval` is hour-ending and repeats on the DST fall-back hour;
  those are distinct UTC hours and are kept, so dedupe keys on UTC. `monthly_cost_musd`
  stays blank (that is the fixture shape). `rent_musd` is SPP's congestion by
  constraint (monthly CSVs, sum over intervals of flow x shadow price, posted as a
  negative `Sum`), joined on `Constraint Name|Contingency Name` only when the monitored
  element matches (ids are recycled), summed over months with positive rent; months
  with negative rent are counted in `flags`, never netted. No row means unobserved
  (blank), never zero. SPP's TCR funding by constraint (settled DA Collected) is read
  for a calibration total in the note, and as a build tripwire. The build fails when:
  - a month has no dollars in either file;
  - a month's congestion-by-constraint total is outside 0.8–2.0× that month's DA
    Collected (2025 runs 1.17–1.48), or outside 0.8–1.5× the year's median ratio;
  - flowgates holding more than 5% of kept |shadow price| join no dollar row.
- `planning`: header-only, `unobserved` unless a machine-readable ITP source is fetched.
- `queue`: active GI requests (not withdrawn, not in commercial operation). `near_bus`
  set only on an unambiguous POI-name match to a bus name, else blank.
