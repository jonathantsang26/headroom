# Snapshot integrity

A live snapshot contains `snapshot_manifest.json` and its input CSVs. The loader
validates every required table before creating lineage. The nine required tables
are `buses`, `hifld_lines`, `eia_lines`, `ferc1_lines`, `constraints`, `congestion`,
`planning`, `queue`, and `load_930`.

Each table entry records a nonempty `source`, `source_version`, timezone-aware
`retrieved_at`, and the file's `sha256`. The optional `path` defaults to
`normalized/<table>.csv`. Paths must be relative and resolve within the snapshot.
Missing files, stamps, hashes, or mismatched bytes stop loading. Older manifests
without hashes must be rebuilt from their trusted inputs; missing hashes are
not treated as verification.

The manifest format is:

```json
{
  "schema_version": 1,
  "snapshot_date": "YYYY-MM-DD",
  "revision": 2,
  "tables": {
    "<table>": {
      "source": "<registered-source>",
      "source_version": "<pinned-version>",
      "retrieved_at": "<ISO-timestamp-with-timezone>",
      "path": "normalized/<table>.csv",
      "sha256": "<64-hex-digit-file-digest>",
      "coverage": "observed | partial | unobserved",
      "note": "<what the table covers and what it could not obtain>",
      "contributors": [{"source": "...", "source_version": "...", "retrieved_at": "..."}],
      "column_sources": {"<column>": {"source": "...", "source_version": "...", "retrieved_at": "..."}}
    }
  },
  "raw": {}
}
```

`revision`, `renormalized_from`, `coverage`, `note`, `contributors` and
`column_sources` are optional. Coverage defaults to `observed`.
An `unobserved` table could not be obtained from a public source and is written
header-only; every signal it feeds becomes a flagged zero that adds nothing to any
composite (never zero evidence), and the run's `quality_report.json` names the
signals and scores it affects. `contributors` lists further sources a table was
derived from (for example bus roles from EIA-860 on HIFLD geometry); each is
validated like the primary stamp and enters lineage. `column_sources` names the
source of a single column when it differs from the table's primary source. Each one
must also be the table's stamp or one of its contributors, so the table-level gate
sees it. Only columns the loader stamps one by one may be listed; any other column
fails the load rather than being silently stamped with the table's source. The
loader stamps that column's row Facts with it. For example, SPP `congestion.rent_musd` comes from SPP's
congestion-by-constraint files, while the rest of the row comes from the binding
constraints. A score's lineage therefore names the source each of its values actually
came from. Column contracts for each table are in [ADAPTERS.md](ADAPTERS.md).

## Snapshot ids and revisions

A snapshot id is its date, `YYYY-MM-DD`. A second build on the same day is a
revision, `YYYY-MM-DD.rN` with N ≥ 2. Its manifest records `"revision": N` and the
same `snapshot_date`. The builder creates only these two forms. A revision is a new,
separate directory. The earlier build stays intact, so runs pinned to it can still
be replayed.

`--renormalize-from <id> --revision N` builds a revision without the network. It
links the source snapshot's raw files, checks each one against its recorded sha256,
and re-runs the normalizers with the current code. The manifest keeps the source's
`raw` entries unchanged and records `renormalized_from` (the source id and manifest
sha256). A live run also requires every publishable stamp to equal today's pin in
`config/sources.yaml`. After a re-pin, an older snapshot loads only at a commit with its
pins. Re-normalizing does not help. The normalizers check each raw file against
today's pin: SPP, HIFLD and queue versions are read from the bytes, and the PUDL files'
URLs name their release. One exception: the HIFLD query envelope in
`config/regions/spp.yaml` is not checked. A widened envelope re-normalizes old raw
files without the features outside the old envelope. Use it when an adapter fix changes what a snapshot's tables or notes should
say. `2026-10-13.r2` was built this way: its nine tables are byte-identical to
`2026-10-13`. Only the congestion table's coverage (now `partial`, because 18
flowgates have no posted dollars) and its note changed.

## Build and activate

The builder fetches and normalizes into a unique staging directory. Any adapter
failure or incomplete manifest returns a nonzero exit status and preserves the
previous `current` link. Failed staging directories remain available for inspection;
the error names their location. An interrupted build (Ctrl-C or SIGTERM) removes its
own staging directory. A build that was killed outright cannot clean up, so the next
build lists the leftovers, and `--clean-staging` removes them. A successful build moves to its dated directory
before `current` is replaced atomically. Existing dated snapshots are never rebuilt
in place by this command.

To verify and activate an existing snapshot without fetching:

```bash
.venv/bin/python -m headroom.ingest.adapters.build_snapshot --date YYYY-MM-DD --activate-only
```

The full builder uses the same command without `--activate-only`. It fetches every
source first, then normalizes in a fixed order (hifld, eia, pudl_ferc1, spp). If the
dated directory already exists, the builder refuses and names the next free revision.
Pass `--revision N` to build `YYYY-MM-DD.rN` instead. `--no-activate` builds and
verifies the snapshot without moving `current`. Downloads use temporary
files and only replace the destination after a complete transfer. Manifest writes
are also atomic.

## Limits

Hashes check byte integrity, not accuracy, usage rights, source identity, or the
correctness of a canonical-ID join. This check does not clear the export gate.
Once a live load starts, it resolves `current` to one snapshot so later activation
cannot mix tables from different snapshots. It hashes and parses the same bytes
for each table. Keep completed snapshot files immutable.

## Historical replay

New run manifests use schema version 2. They record all nine input paths and hashes,
the live snapshot ID and exact snapshot-manifest hash where applicable, the source
registry pins, scoring settings and signal order, runtime versions, pipeline source
hash, and a digest of the full-precision scores and corridor shortlist. Input paths
are relative; no absolute machine paths are written into the input identity.

Replay the offline example:

```bash
.venv/bin/headroom reproduce \
  --manifest data/processed/run_manifest.INTERNAL.synthetic.json \
  --out data/replayed
```

Live replay looks for the recorded snapshot beside the region's configured input
directory, regardless of where `current` now points. A missing archive causes an
error; replay never substitutes the newer snapshot. If the archive moved, pass
`--data-dir /path/to/archive/YYYY-MM-DD`, preserving its recorded directory name,
manifest bytes, and table paths. Fixture directories may also be relocated with
`--data-dir`.

Source, runtime, or code drift fails before modeling. Input drift fails before
lineage is created. Replay then checks the recomputed ranking digest before any
CLI exports are written. Scoring dictionaries may be serialized in a different
JSON key order: the recorded signal order preserves random-number consumption.

Scoring settings include the full content of a region's GET priors file (EV ranking)
and its sha256. Replay checks that sha before modeling. A run whose priors file has
since changed still replays from the recorded content.

The runtime identity hashes every module under `ingest`, `quality`, `reconstruct`,
`model`, `provenance`, `schema` and `sources`, plus `pipeline.py` and `export.py`.
`export.py` is included because it makes the shareable or internal decision. The CLI,
map and MCP server are not hashed, so changes there do not invalidate replay. Any
change to the hashed modules does. For example, the 2026-10-12 SPP run replays only at
commits `99c95f5` through `3b2a397`: `e24272d` changed the SPP adapter, and `export.py`
was not hashed then. Keep the commit a shared run came from.

Legacy manifests lack the evidence needed for historical replay and are rejected.
They must be replaced by a new run from trusted inputs; adding hashes to an old
manifest cannot establish which inputs the old run used. Runtime matching is
deliberately strict, including pipeline source edits and exact dependency versions.
Retain the original code/environment as well as the input archive.

The digest verifies ranking values and envelopes, not byte-identical Parquet files
or export timestamps. Pins are integrity records, not signatures against someone
rewriting both data and the run manifest. Snapshot replay is tested offline using
fabricated data; live adapters, real-source reconstruction, and source permissions
remain separate requirements. Replay does not change the export gate.
