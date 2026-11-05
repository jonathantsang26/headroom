"""A finished run for the map: read from its directory and checked against its
manifest, never re-run (A33)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

# export.deliver names the manifest after the run's mode; one directory holds one run.
MANIFEST_MODES = {
    "run_manifest.json": "shareable",
    "run_manifest.INTERNAL.json": "internal",
    "run_manifest.INTERNAL.synthetic.json": "internal-synthetic",
}
# The files the map reads; every other recorded output is checked too.
_REQUIRED = ("scores_json", "shortlist_json")
# What the map reads from every corridor row (map_data._segment, run_corridor_detail).
_CORRIDOR_KEYS = (
    "physical_corridor", "representative_flowgate", "flowgates", "n_flowgates",
    "recommended_get", "p_top_k", "composite_lo", "composite_expected", "composite_hi",
    "provenance",
)


class RunDirError(ValueError):
    """The directory is not a finished run whose files match its manifest."""


@dataclass(frozen=True)
class FinishedRun:
    path: Path
    manifest: dict
    scores: list[dict]     # scores*.json: one row per flowgate, in rank order
    shortlist: list[dict]  # shortlist*.json: one row per corridor, in rank order
    digests: dict[str, str]  # output key -> sha256, as checked

    @property
    def mode(self) -> str:
        return self.manifest["mode"]

    @property
    def region(self) -> str:
        return self.manifest.get("region", "")

    @property
    def snapshot_id(self) -> str | None:
        return (self.manifest.get("inputs") or {}).get("snapshot_id")

    @property
    def output_sha256(self) -> str | None:
        return self.manifest.get("output_sha256")

    @property
    def shortlist_k(self) -> int | None:
        return self.manifest.get("shortlist_k")

    @property
    def n_constraints(self) -> int:
        return self.manifest["n_constraints"]


def _holds_manifest(path: Path) -> bool:
    try:
        return path.is_dir() and any((path / name).is_file() for name in MANIFEST_MODES)
    except OSError:  # a directory this process cannot read is not offered
        return False


def list_run_dirs(root: str | Path) -> list[Path]:
    """The run directories at `root`: `root` itself when it holds a run manifest (as
    `headroom run --out data/processed` leaves it), then the directories directly
    under it that do, sorted. Directories that cannot be read are skipped."""
    root = Path(root)
    try:
        children = list(root.iterdir())
    except OSError:  # missing, not a directory, or unreadable
        children = []
    return ([root] if _holds_manifest(root) else []) + sorted(
        p for p in children if _holds_manifest(p))


def load_run_dir(path: str | Path) -> FinishedRun:
    """Read a run directory written by export.deliver. Refuses it unless it holds one
    manifest whose name matches its mode, and every output the manifest records is
    there with the recorded sha256 (as `headroom reproduce` compares a replay's)."""
    path = Path(path)
    if not path.is_dir():
        raise RunDirError(f"{path} is not a directory")
    found = [name for name in MANIFEST_MODES if (path / name).is_file()]
    if len(found) != 1:
        raise RunDirError(f"{path} holds {len(found)} run manifests ({', '.join(found) or 'none'}); "
                          "a run directory holds exactly one")
    manifest_name = found[0]
    try:
        manifest = json.loads((path / manifest_name).read_bytes())
    except ValueError as exc:
        raise RunDirError(f"{path / manifest_name} is not valid JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise RunDirError(f"{path / manifest_name} is not a run manifest")
    if manifest.get("mode") != MANIFEST_MODES[manifest_name]:
        raise RunDirError(f"{manifest_name} records mode {manifest.get('mode')!r}; a file of "
                          f"that name holds a {MANIFEST_MODES[manifest_name]!r} run")

    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise RunDirError(f"{manifest_name} records no output digests to check the files against")
    missing = [key for key in _REQUIRED if key not in outputs]
    if missing:
        raise RunDirError(f"{manifest_name} records no digest for {missing}")

    data: dict[str, bytes] = {}
    digests: dict[str, str] = {}
    differ: list[str] = []
    for key, entry in outputs.items():
        name = (entry or {}).get("path") if isinstance(entry, dict) else None
        recorded = (entry or {}).get("sha256") if isinstance(entry, dict) else None
        # a bare file name in this directory, never a path out of it
        if not isinstance(name, str) or not name or Path(name).name != name or name in (".", ".."):
            raise RunDirError(f"{manifest_name} records {key} at {name!r}, not a file in {path}")
        file = path / name
        if not file.is_file():
            raise RunDirError(f"{manifest_name} records {key} as {name}, which is missing")
        blob = file.read_bytes()
        digests[key] = hashlib.sha256(blob).hexdigest()
        if digests[key] != recorded:
            differ.append(key)
        data[key] = blob
    if differ:
        raise RunDirError(f"Run files differ from the sha256s in {manifest_name}: {differ}")

    # parse the bytes that were hashed, not a second read
    scores = _rows(outputs["scores_json"]["path"], data["scores_json"], ("constraint_id",))
    shortlist = _rows(outputs["shortlist_json"]["path"], data["shortlist_json"], _CORRIDOR_KEYS)
    if len(scores) != manifest.get("n_constraints"):
        raise RunDirError(f"{outputs['scores_json']['path']} holds {len(scores)} flowgates; "
                          f"the manifest records {manifest.get('n_constraints')}")
    return FinishedRun(path=path, manifest=manifest, scores=scores, shortlist=shortlist,
                       digests=digests)


def _rows(name: str, blob: bytes, keys: tuple[str, ...]) -> list[dict]:
    """A recorded file's rows: a JSON list of objects, each carrying `keys`. A file
    whose digest matches but whose content is not that is refused, not displayed."""
    try:
        rows = json.loads(blob)
    except ValueError as exc:  # JSONDecodeError and UnicodeDecodeError
        raise RunDirError(f"{name} is not valid JSON: {exc}") from exc
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise RunDirError(f"{name} is not a list of rows")
    for i, row in enumerate(rows):
        missing = [k for k in keys if k not in row]
        if missing:
            raise RunDirError(f"{name} row {i} has no {missing}")
    return rows
