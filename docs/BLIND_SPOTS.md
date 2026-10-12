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
- **Ratings.** No public source gives a per-element limit, so rent is Σ|DA shadow price|
  × an assumed 100–1,200 MW band (mode 250 MW). The band is wide and uncalibrated; see
  CROSSCHECK.md.
- **Planning.** SPP ITP needs and costs are PDF only.

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
- `cost_of_inaction` is sampled independently of `rent`/`persistence` though it is
  mechanically ∝ rent (weight 0.10; does not move the ranking). See `model/signals.py`.
- Signals are normalized by fixed references in `config/scoring.yaml`, not learned. They
  were set on fixtures. On the 2025 SPP data, binding hours exceed `persist_ref_hours`
  by up to 9.5x, so persistence dominates the composite. Re-referencing is a deliberate,
  recorded config change that has not been made.
- `miso-synth` reuses the SPP fixtures to prove region-parameterization; it is NOT a
  real MISO ingest.

## 6. Borrowed access is positioning, not code
For genuinely gated data the lever is institutional (NREL/LBNL/PNNL, an RTO, RMI) —
"whose access do I borrow," not "squeeze public sources harder." Out of platform scope.
