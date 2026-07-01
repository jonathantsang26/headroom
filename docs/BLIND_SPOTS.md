# Headroom — scope boundary & known blind spots

State these loudly; they convert limits into honest scope. Every shareable output
should carry the relevant caveats.

## 1. Public-data-only, by choice
CEII (Form 715 power-flow base cases, etc.) is obtainable via a FERC CEII request +
NDA — but ingesting it constrains what can be published. Staying public keeps outputs
shareable, which is the product's whole value. Enforced structurally: the
`publishable` lineage-walk gate (`provenance/gate.py`) refuses to export any value
whose provenance transitively touches a non-publishable source under `public_only`.

## 2. Synthetic pilot data
This build ships with **fabricated SPP-SYNTH fixtures** (`tests/fixtures/spp_synth/`),
not real SPP data. All fixture values carry `source=synthetic_fixture`
(`publishable: false`), so the gate refuses a shareable export and only ever writes an
`INTERNAL.synthetic` artifact. A real run swaps in pinned public snapshots; nothing
downstream changes. **No synthetic ranking should ever be treated as a real result.**

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
- Signals are normalized by fixed references in `config/scoring.yaml`, not learned.
- `miso-synth` reuses the SPP fixtures to prove region-parameterization; it is NOT a
  real MISO ingest.

## 6. Borrowed access is positioning, not code
For genuinely gated data the lever is institutional (NREL/LBNL/PNNL, an RTO, RMI) —
"whose access do I borrow," not "squeeze public sources harder." Out of platform scope.
