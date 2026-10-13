# Headroom — scope boundary & known blind spots

State these loudly; they convert limits into honest scope. Every shareable output
should carry the relevant caveats.

## 1. Public-data-only, by choice
CEII (Form 715 power-flow base cases, etc.) is obtainable via a FERC CEII request +
NDA — but ingesting it constrains what can be published. Staying public keeps outputs
shareable, which is the product's whole value. Enforced structurally: the
`publishable` lineage-walk gate (`provenance/gate.py`) refuses to export any value
whose provenance transitively touches a non-publishable source under `public_only`.

## 2. Synthetic fixtures vs the real SPP snapshot
`tests/fixtures/spp_synth/` holds **fabricated SPP-SYNTH fixtures**. All fixture values
carry `source=synthetic_fixture` (`publishable: false`), so the gate refuses a shareable
export and only ever writes an `INTERNAL.synthetic` artifact. **No synthetic ranking
should ever be treated as a real result.** `--region spp` reads a pinned public
snapshot (see ADAPTERS.md). A shareable export needs every score's lineage and every
input table's stamp to pass the gate, so a single synthetic-stamped table makes the
run internal.

What the real snapshot cannot see:
- **Where SPP's constraints are.** SPP names monitored elements by market-model bus, and
  the bus-to-location model is CEII (§1). Every flowgate's monitored line is therefore
  `coverage=unobserved`, and the DC screen, demand pressure and radial test are
  unobserved: they contribute zero to every composite and cannot change the order.
- **Ratings.** No public source gives a per-element limit. Since snapshot 2026-10-13,
  rent is SPP's own posted day-ahead congestion dollars per constraint, so no rating
  enters it. Those dollars total 1.23× SPP's collected DA congestion (CROSSCHECK.md).
  18 of 2,072 flowgates have no posted dollars; their rent is unobserved and they are
  listed, not ranked by EV.
- **Planning.** SPP ITP needs and costs are PDF only.
- **Real-time and outages.** Only the day-ahead market is read. Real-time congestion,
  outage-driven binding and the TCR market are not scored.

## 3. Muni/co-op blind spot (coverage)
Non-filers (many municipals and co-ops) don't appear in FERC Form 1. **Do not treat
non-filers as zero** — entities carry a `coverage` flag (`observed` / `unobserved`);
unobserved footprint is excluded from claims, never implied as absence of assets. The
`quality_report` records a `coverage_note`.

## 4. DC screen ≠ AC study
The PTDF model (`reconstruct/network_model.py`) is a screen: it flags candidates from
public topology + load; it does **not** certify deployments or replace AC contingency
analysis. Impedances are estimated (emitted as flagged `Range`s). Every output says so.
Contingencies that disconnect the grid are recorded with `screen_status=n1_disconnect`
and a widened band — flagged, never dropped.

## 5. Modeling simplifications (logged, not hidden)
- **The SPP ranking rests on judgement priors.** `--region spp` ranks flowgates by the
  expected net value of their best GET (`model/ev.py`). Relief share, the shadow-price
  haircut, annualized cost and applicability by evidence class come from
  `config/get_priors.yaml`. These are cited judgement, not observations, and the order
  of the shortlist depends on them. Their content and sha256 are recorded in every run
  manifest. The value of relief is first order (u × rent): it ignores the network
  response beyond the haircut and assumes one deployment relieves one flowgate. Only 53
  flowgates have a GET worth deploying under these priors. That count ranges from 16 to
  114 across the priors' ranges (`scripts/ev_sensitivity.py`), though the leaders
  hardly move.
- **One year of congestion is a weak forecast.** Within 2025, only about 30% of one
  half-year's top 5% recur in the other half. The EV layer therefore uses forward rent,
  2025 dollars × months-binding share × a backtest ratio draw (docs/FORWARD_RENT.md).
  That model is calibrated within 2025 and not tested across years.
- **Transformers are keyed by substation.** SPP's monitored text names the substation,
  not the bank, so the transformers at one substation share a corridor row and their
  GET applicability draws.
- **GET applicability without topology.** Element locations are CEII, so the GET rule
  and the EV applicability classes see only element type, contingency and binding
  pattern. They do not see parallel paths, terminal limits or line length.
- `cost_of_inaction` on the fixtures is rent × hours, which counts binding hours a
  second time. The SPP region uses recurring rent instead: annual dollars minus the
  largest month (`definitions` in `config/regions/spp.scoring.yaml`). It is still
  sampled independently of rent.
- Signals are normalized by fixed references, not learned. For SPP, references are
  derived by a stated rule (p99 of each observed signal) from the pinned snapshot's
  congestion table (`scripts/derive_scoring_refs.py`). A run on a different congestion
  table fails until they are re-derived. `config/scoring.yaml` keeps the fixture
  values.
- `miso-synth` reuses the SPP fixtures to prove region-parameterization; it is NOT a
  real MISO ingest.

## 6. Borrowed access is positioning, not code
For genuinely gated data the lever is institutional (NREL/LBNL/PNNL, an RTO, RMI) —
"whose access do I borrow," not "squeeze public sources harder." Out of platform scope.
