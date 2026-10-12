# HIFLD real sample

Recorded 2026-10-12 (07:27 UTC) from HIFLD Open, `Electric_Power_Transmission_Lines`
FeatureServer layer 0 (services1.arcgis.com/Hp6G80Pky0om7QvQ), layer
`editingInfo.dataLastEditDate` 1693930547491 = 2023-09-05 (pin `hifld_open_2023-09-05`).

- `layer.json`: the layer metadata (`…/FeatureServer/0?f=json`), verbatim.
- `features.geojson`: 33 features taken unmodified from the paged envelope query that
  `hifld.fetch` runs (outFields `OBJECTID_1,ID,TYPE,STATUS,OWNER,VOLTAGE,VOLT_CLASS,
  INFERRED,SUB_1,SUB_2,SOURCE,VAL_DATE`, outSR 4326, geometryPrecision 6), re-serialized
  as compact JSON with sorted keys, ordered by `OBJECTID_1`:
  - all 32 features lying wholly inside lat 46.67–47.03, lon −101.145 to −100.785
    (Mandan / Bismarck, ND: MANDAN JUNCTION, HESKETT, NORTHWEST MANDAN SWITCHING
    STATION, BISMARCK SWEET AVENUE JUNCTION), on the SWPP/MISO seam;
  - ID 150123 (OBJECTID_1 36694), the Coal Creek – Dickinson 400 kV HVDC line
    (`TYPE = DC; OVERHEAD`).

The pinned layer has no feature with an unknown or sub-100 kV `VOLTAGE` anywhere in the
US (count queries for `VOLTAGE<100`, `VOLTAGE<=0` and `VOLTAGE IS NULL` returned 0 on
2026-10-12), so the unknown-kV rule is tested by mutating one real feature's `VOLTAGE`
in memory inside the test; the file itself is unmodified.

The plant data this sample needs for the footprint lives in `../eia/`.
