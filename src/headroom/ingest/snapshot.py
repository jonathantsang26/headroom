"""Snapshot manifest — the trust hand-off between adapters and the live loader."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from headroom.provenance.real import SourceStamp

MANIFEST_NAME = "snapshot_manifest.json"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Snapshot:
    """A pinned snapshot directory + its manifest."""

    def __init__(self, root: Path):
        self.root = Path(root)
        mpath = self.root / MANIFEST_NAME
        if not mpath.exists():
            raise FileNotFoundError(
                f"No {MANIFEST_NAME} in {self.root} — a live region requires a "
                "manifest naming the source/version/retrieved_at of every table. "
                "Run the snapshot builder (headroom.ingest.adapters.build_snapshot)."
            )
        self.manifest: dict = json.loads(mpath.read_text())
        self.normalized = self.root / "normalized"

    def stamp(self, table: str) -> SourceStamp:
        try:
            t = self.manifest["tables"][table]
        except KeyError as e:
            raise KeyError(
                f"Table {table!r} has no entry in {self.root / MANIFEST_NAME}; "
                "refusing to envelope data without a source stamp (fail-closed)."
            ) from e
        return SourceStamp(
            source=t["source"],
            source_version=t["source_version"],
            retrieved_at=datetime.fromisoformat(t["retrieved_at"]),
        )

    def table_path(self, table: str) -> Path:
        rel = self.manifest["tables"][table].get("path", f"normalized/{table}.csv")
        return self.root / rel

    def verify(self, table: str) -> bool:
        """True if the table file matches its recorded sha256 (or none recorded)."""
        t = self.manifest["tables"][table]
        want = t.get("sha256")
        return True if not want else sha256_of(self.table_path(table)) == want


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def update_manifest(
    snapshot_dir: Path,
    *,
    tables: dict | None = None,
    raw: dict | None = None,
    snapshot_date: str | None = None,
) -> Path:
    """Create or merge entries into a snapshot's manifest (adapters call this)."""
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    mpath = snapshot_dir / MANIFEST_NAME
    m = json.loads(mpath.read_text()) if mpath.exists() else {
        "schema_version": 1,
        "snapshot_date": snapshot_date or utcnow_iso()[:10],
        "tables": {},
        "raw": {},
    }
    if tables:
        m["tables"].update(tables)
    if raw:
        m["raw"].update(raw)
    mpath.write_text(json.dumps(m, indent=2, sort_keys=True))
    return mpath
