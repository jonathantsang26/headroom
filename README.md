# Headroom

A vendor-neutral, **public-data** screening layer that ranks transmission
constraints by grid-enhancing-technology (GETs) deployment value, with explicit
confidence and uncertainty. It is an upstream *targeting screen*, not a power-flow
replication — the output is a **rank-stable shortlist with confidence**, not point
estimates.

Pilot region: **SPP**.

## The spine

Every design decision descends from one split:

- **Bucket A** — present-but-dirty data → corroborate + validate → emits a `Fact`
  (cleaned value + verbatim original + per-record confidence).
- **Bucket B** — structurally-absent data → reconstruct from price shadows, physics,
  and planning exhaust → emits a `Range` (lo/expected/hi + basis).

**No value enters the system as a bare scalar.** Scoring consumes only `Fact`s and
`Range`s. Both carry `source` + pinned `source_version` + `lineage_id`, so any number
traces back to its filing/report.

## Phase 0 (this scaffold)

The provenance spine is in place and a dummy value flows end-to-end:

```
src/headroom/provenance/
  envelope.py   # Fact + Range  (the only two types scoring consumes)
  lineage.py    # LineageStore + transitive source_closure (taint walk)
  gate.py       # publishable export gate (Decision 5)
  versions.py   # version pinning + run manifest
src/headroom/sources/
  registry.py   # fail-closed loader for config/sources.yaml
config/sources.yaml   # the single source registry (bucket + tier + compliance)
```

### Public-only, enforced (Decision 5)

`config/sources.yaml` tags each source `publishable: true|false` (**fail-closed** —
non-publishable unless explicitly true). The export gate walks the lineage graph and
refuses to emit any value whose provenance *transitively* touches a non-publishable
(e.g. CEII) source under `public_only=True`. A per-source check would leak CEII
through composites; the transitive walk does not.

## Run

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"   # or: pip install pydantic pyyaml pytest
.venv/bin/python -m pytest                     # tests — no network, no keys
.venv/bin/python -m headroom.demo              # the end-to-end dummy flow
```

## Roadmap

Phase 0 spine ✓ → Phase 1 Bucket-A base (FERC/EIA/HIFLD for SPP) → Phase 2 Bucket-B
reconstruction (congestion rent, DC/PTDF, planning exhaust) → Phase 3 scoring +
rank stability → Phase 4 Streamlit map + ranked export.
