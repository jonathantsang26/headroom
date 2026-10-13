"""Snapshot manifest — the trust hand-off between adapters and the live loader."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from headroom.provenance.real import SourceStamp

MANIFEST_NAME = "snapshot_manifest.json"
REQUIRED_TABLES = (
    "buses", "hifld_lines", "eia_lines", "ferc1_lines",
    "constraints", "congestion", "planning", "queue", "load_930",
)
COVERAGE_LEVELS = ("observed", "partial", "unobserved")


def _parse_stamp(t, label: str) -> SourceStamp:
    if not isinstance(t, dict):
        raise ValueError(f"{label} must be an object")
    for key in ("source", "source_version", "retrieved_at"):
        if not isinstance(t.get(key), str) or not t[key].strip():
            raise ValueError(f"{label} requires a nonempty {key}")
    try:
        retrieved_at = datetime.fromisoformat(t["retrieved_at"])
    except ValueError as e:
        raise ValueError(f"{label} has invalid retrieved_at") from e
    if retrieved_at.utcoffset() is None:
        raise ValueError(f"{label} requires a timezone-aware retrieved_at")
    return SourceStamp(
        source=t["source"],
        source_version=t["source_version"],
        retrieved_at=retrieved_at,
    )


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Snapshot:
    """A pinned snapshot directory + its manifest."""

    def __init__(self, root: Path):
        # Pin a `current` symlink once so later activation cannot switch tables
        # out from underneath the manifest we read here.
        self.root = Path(root).resolve()
        mpath = self.root / MANIFEST_NAME
        if not mpath.exists():
            raise FileNotFoundError(
                f"No {MANIFEST_NAME} in {self.root} — a live region requires a "
                "manifest naming the source/version/retrieved_at of every table. "
                "Run the snapshot builder (headroom.ingest.adapters.build_snapshot)."
            )
        manifest_bytes = mpath.read_bytes()
        self.manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        self.manifest: dict = json.loads(manifest_bytes)
        if not isinstance(self.manifest, dict):
            raise ValueError(f"Snapshot manifest must be an object: {mpath}")
        if type(self.manifest.get("schema_version")) is not int or self.manifest["schema_version"] != 1:
            raise ValueError(f"Unsupported snapshot manifest schema_version in {mpath}")
        if not isinstance(self.manifest.get("tables"), dict):
            raise ValueError(f"Snapshot manifest must contain a tables object: {mpath}")
        rev = self.manifest.get("revision")
        if rev is not None and (type(rev) is not int or rev < 2):
            raise ValueError(f"Snapshot manifest revision must be an integer >= 2: {mpath}")
        self.normalized = self.root / "normalized"

    def _entry(self, table: str) -> dict:
        try:
            t = self.manifest["tables"][table]
        except KeyError as e:
            raise KeyError(
                f"Table {table!r} has no entry in {self.root / MANIFEST_NAME}; "
                "refusing to envelope data without a source stamp (fail-closed)."
            ) from e
        if not isinstance(t, dict):
            raise ValueError(f"Snapshot table {table!r} must have an object entry")
        return t

    def stamp(self, table: str) -> SourceStamp:
        return _parse_stamp(self._entry(table), f"Snapshot table {table!r}")

    def contributors(self, table: str) -> list[SourceStamp]:
        """Additional sources a table was derived from (e.g. bus roles from EIA-860
        on HIFLD bus geometry). Each is validated like the primary stamp."""
        extra = self._entry(table).get("contributors", [])
        if not isinstance(extra, list):
            raise ValueError(f"Snapshot table {table!r} contributors must be a list")
        return [
            _parse_stamp(c, f"Snapshot table {table!r} contributor {i}")
            for i, c in enumerate(extra)
        ]

    def column_sources(self, table: str) -> dict[str, SourceStamp]:
        """Columns whose values come from a different source than the table's own
        stamp (e.g. SPP congestion $ joined onto binding-constraint rows). Each maps
        to a full stamp, validated like the primary one."""
        cols = self._entry(table).get("column_sources", {})
        if not isinstance(cols, dict):
            raise ValueError(f"Snapshot table {table!r} column_sources must be an object")
        out = {
            str(col): _parse_stamp(stamp, f"Snapshot table {table!r} column {col!r} source")
            for col, stamp in cols.items()
        }
        # a column's source must also be one of the table's stamps, so the table-level
        # gate sees every source the table's values came from
        declared = {(s.source, s.source_version) for s in [self.stamp(table), *self.contributors(table)]}
        for col, st in out.items():
            if (st.source, st.source_version) not in declared:
                raise ValueError(
                    f"Snapshot table {table!r} column {col!r} source {st.source} "
                    f"{st.source_version} is not the table's stamp or a contributor")
        return out

    def coverage(self, table: str) -> dict:
        """Declared coverage of a table: observed (default), partial or unobserved,
        with the adapter's note. Unobserved never means zero."""
        t = self._entry(table)
        cov = t.get("coverage", "observed")
        if cov not in COVERAGE_LEVELS:
            raise ValueError(
                f"Snapshot table {table!r} coverage must be one of {COVERAGE_LEVELS}"
            )
        note = t.get("note", "")
        if not isinstance(note, str):
            raise ValueError(f"Snapshot table {table!r} note must be a string")
        return {"coverage": cov, "note": note}

    def table_path(self, table: str) -> Path:
        rel = self._entry(table).get("path", f"normalized/{table}.csv")
        if not isinstance(rel, str) or not rel.strip() or "\x00" in rel:
            raise ValueError(f"Snapshot table {table!r} has an invalid path")
        path = Path(rel)
        if path.is_absolute():
            raise ValueError(f"Snapshot table {table!r} path must be relative to the snapshot")
        resolved = (self.root / path).resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError(f"Snapshot table {table!r} path escapes the snapshot")
        return resolved

    def verify(self, table: str) -> bool:
        """True only when a table matches a valid, recorded SHA256 digest."""
        t = self._entry(table)
        want = t.get("sha256")
        if not isinstance(want, str) or re.fullmatch(r"[0-9a-fA-F]{64}", want) is None:
            return False
        return sha256_of(self.table_path(table)) == want.lower()

    def require_tables(self, names: Iterable[str] = REQUIRED_TABLES) -> None:
        """Validate every required input before the loader creates any lineage."""
        for table in names:
            self.stamp(table)
            self.contributors(table)
            self.column_sources(table)
            self.coverage(table)
            path = self.table_path(table)
            if not path.is_file():
                raise FileNotFoundError(f"Snapshot table {table!r} is not a file: {path}")
            if not self.verify(table):
                raise ValueError(
                    f"Snapshot table {table!r} has a missing or invalid sha256, "
                    "or its file does not match the recorded sha256"
                )

    def read_tables(
        self, names: Iterable[str] = REQUIRED_TABLES,
    ) -> dict[str, tuple[Path, bytes]]:
        """Return validated paths and the exact bytes callers must parse.

        Unlike a separate verification followed by a read, this also detects a
        table replaced between operations: its recorded digest must match the
        returned buffer, not a file opened earlier.
        """
        tables = {}
        for table in names:
            self.stamp(table)
            self.contributors(table)
            self.column_sources(table)
            self.coverage(table)
            path = self.table_path(table)
            if not path.is_file():
                raise FileNotFoundError(f"Snapshot table {table!r} is not a file: {path}")
            content = path.read_bytes()
            want = self._entry(table).get("sha256")
            if (
                not isinstance(want, str)
                or re.fullmatch(r"[0-9a-fA-F]{64}", want) is None
                or hashlib.sha256(content).hexdigest() != want.lower()
            ):
                raise ValueError(
                    f"Snapshot table {table!r} has a missing or invalid sha256, "
                    "or its file does not match the recorded sha256"
                )
            tables[table] = (path, content)
        return tables


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def update_manifest(
    snapshot_dir: Path,
    *,
    tables: dict | None = None,
    raw: dict | None = None,
    snapshot_date: str | None = None,
    revision: int | None = None,
    renormalized_from: dict | None = None,
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
    if revision is not None and not mpath.exists():
        m["revision"] = revision  # the N-th build of snapshot_date (N >= 2)
    if renormalized_from is not None and not mpath.exists():
        m["renormalized_from"] = renormalized_from  # raw bytes reused, code re-run
    if tables:
        m["tables"].update(tables)
    if raw:
        m["raw"].update(raw)
    # Write beside the destination, then replace it so readers never observe a
    # partially serialized manifest. Failed writes leave the prior file intact.
    serialized = json.dumps(m, indent=2, sort_keys=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=snapshot_dir,
            prefix=f".{MANIFEST_NAME}.", suffix=".tmp", delete=False,
        ) as fh:
            temp_path = Path(fh.name)
            fh.write(serialized)
        temp_path.replace(mpath)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return mpath
