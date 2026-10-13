# Forward rent: how far one year of congestion predicts the next

The EV layer turns congestion dollars into the yearly value of relieving a flowgate.
The obvious input, a flowgate's 2025 day-ahead congestion dollars, is a weak forecast
of later congestion. This page gives the evidence and the model that replaces it
(`src/headroom/model/forward.py`, calibrated by `scripts/backtest_forward_rent.py`).

## The test

SPP's congestion-by-constraint files give dollars per flowgate per month. Within
2025, each split trains on six months and scores on the other six. The top 5% by the
predictor are compared with the best possible top 5% of the held-out months, by the
dollars they capture. Snapshot `2026-10-13.r2`, 2,072 flowgates with dollars:

| Split | k | Annual dollars: capture / overlap / leaders at $0 | Annual × months-binding share: capture / overlap / leaders at $0 |
|---|---|---|---|
| H1 → H2 | 65 | 56.1% / 0.31 / 20 | 55.2% / 0.31 / 17 |
| H2 → H1 | 62 | 29.4% / 0.29 / 21 | 35.2% / 0.32 / 9 |
| odd → even months | 68 | 85.5% / 0.66 / 6 | 90.0% / 0.74 / 1 |
| even → odd months | 73 | 84.0% / 0.68 / 5 | 85.1% / 0.68 / 0 |
| **mean capture** | | **63.8%** | **66.4%** |

What it shows:

- **Half-year to half-year, a year's leaders mostly do not repeat.** About 30% of one
  half's top 5% are in the other half's top 5%. 20 of 65 leaders earn nothing in the
  other half. Odd and even months, which share seasons, agree much better (overlap
  about 0.7). Seasonality and one-off events (outages, weather) drive much of a
  year's dollars.
- **Weighting by persistence helps a little and consistently.** Scaling each
  flowgate's dollars by the share of months it bound in captures more held-out dollars
  in three of four splits. In H1 → H2 it is 0.9 points worse. Leaders that later earn
  nothing drop from 52 to 27 across the four splits. The predictor is
  `rent × months_binding / 12`.
- **The old forward band was far too narrow.** After removing the system-wide change
  between halves, the leaders' realized / predicted ratio has p5 0, p25 0.24, p50 0.76,
  p75 1.47, p95 3.7 and mean 1.22. Only about 14% of H1 leaders landed inside the
  -40%/+50% band the rent Range used.

## The model

For each flowgate and Monte Carlo draw, the EV layer uses

    forward rent = 2025 rent × months_binding / 12 × ε

ε comes from the leaders' pooled ratio distribution above. It is stated as quantiles
(p0, 5, 10, 25, 50, 75, 90, 95, 99), read by linear interpolation and truncated at the
p99 (8.4). ε is drawn independently per flowgate and draw.

Three consequences:

- A flowgate that bound in one month keeps 1/12 of its 2025 dollars.
- The p5 of most flowgates' EV is 0.
- The mean EV, which ranks, prices in that much of a year's congestion does not recur.

The calibration is recorded in `config/regions/spp.scoring.yaml` under `forward_rent`:
the predictor, the quantiles, the snapshot, the sha256 of the twelve raw files, and
the backtest table. Every run manifest carries it too. To re-derive and check it:

```bash
.venv/bin/python scripts/backtest_forward_rent.py data/raw/spp/2026-10-13.r2 --check config/regions/spp.scoring.yaml
```

The composite ranking (`ranking: composite`, the fixtures) still uses the rent Range
and its -40%/+50% band.

## Limits

- **One year only.** Every split is within 2025, so the ratio distribution measures
  half-year persistence. It does not measure year-over-year persistence. A cross-year
  test needs snapshots from several data years. The builder has one data year per
  source pin today.
- **Calibrated on the same year it is applied to.** ε is fitted on 2025 months and
  applied to 2025 rent, so it is in-sample for that year.
- **One distribution for all leaders.** ε does not depend on element type, contingency
  or naming (SPP's temporary `TMP`/`TP` flowgates hold about half of the dollars).
- **Two ways to count months.** The backtest counts months with congestion dollars.
  The model's share uses `months_binding` (months with any binding hour), because the
  normalized table carries that. The two differ by one month for 41 of 2,053
  flowgates.
- **System level unmodeled.** It assumes next year's congestion level matches 2025's.
  SPP's own day-ahead totals were $1,734M (2023), $2,163M (2024) and $1,977M (2025).
