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

## Pipeline (Phases 0–5, all in)

```
ingest/       region-parameterized loader; envelopes every value at the boundary
quality/      Bucket A: triangulate -> invariants -> confidence  (+ quality_report)
reconstruct/  Bucket B: congestion rent + DC/PTDF N-1 screen + planning exhaust
model/        signals -> which-GET -> composite -> Monte-Carlo rank stability
export.py     ranked CSV/JSON/Parquet; gate decides shareable vs internal
provenance/   envelope (Fact/Range), lineage taint-walk, gate, versions, reproduce
app/ mcp/     Streamlit + pydeck corridor map (GET overlay) + deferred FastMCP stub
config/       sources.yaml (registry) + scoring.yaml (weights/refs/stability)
```

Run the whole thing:

```bash
headroom run --region spp-synth --out data/processed
# -> refuses a shareable export (synthetic data), writes INTERNAL.synthetic artifacts
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

### Map (Phase 4)

```bash
.venv/bin/python -m pip install -e ".[app]"           # streamlit + pydeck + pandas
.venv/bin/streamlit run src/headroom/app/streamlit_app.py
```

Corridors are drawn as line geometry (recovered by joining each corridor's line
endpoints to bus lat/lon), **colored by recommended GET** and **widened by rank
stability (`p_top_k`)**; a detail panel shows each corridor's composite p5/p50/p95
band and signal ranges. The map's data layer (`app/map_data.py`) is unit-tested
offline; the renderer is browser-only.

## Status

All phases implemented against offline SPP-SYNTH fixtures, 66 tests green with no
network or keys:

- **0 Spine** ✓ Fact/Range, sources registry, lineage taint-walk gate, manifest
- **1 Bucket A** ✓ triangulate + invariants + confidence → enveloped base + report
- **2 Bucket B** ✓ congestion rent + DC/PTDF N-1 screen + planning exhaust (Ranges)
- **3 Scoring** ✓ signals → which-GET → composite → `p_top_k` + corridor shortlist
- **4 Delivery** ✓ ranked export + gate-decided mode + CLI + Streamlit/pydeck map
- **5 Harden** ✓ region-parameterized; pinned manifest reproduces a run exactly

Next for a real deliverable: swap SPP-SYNTH fixtures for pinned public SPP snapshots
(live ingest adapters), then add MISO as RTO #2. See `docs/BLIND_SPOTS.md`.
