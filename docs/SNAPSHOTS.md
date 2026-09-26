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
  "tables": {
    "<table>": {
      "source": "<registered-source>",
      "source_version": "<pinned-version>",
      "retrieved_at": "<ISO-timestamp-with-timezone>",
      "path": "normalized/<table>.csv",
      "sha256": "<64-hex-digit-file-digest>"
    }
  },
  "raw": {}
}
```

## Build and activate

The builder fetches and normalizes into a unique staging directory. Any adapter
failure or incomplete manifest returns a nonzero exit status and preserves the
previous `current` link. Failed staging directories remain available for inspection;
the error names their location. A successful build moves to its dated directory
before `current` is replaced atomically. Existing dated snapshots are never rebuilt
in place by this command.

To verify and activate an existing snapshot without fetching:

```bash
.venv/bin/python -m headroom.ingest.adapters.build_snapshot --date YYYY-MM-DD --activate-only
```

The full builder uses the same command without `--activate-only`. It cannot
complete while adapters or normalizers are unimplemented. Downloads use temporary
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
