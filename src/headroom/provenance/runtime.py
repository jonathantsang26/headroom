"""Portable runtime and source identity for historical pipeline replay."""

from __future__ import annotations

import hashlib
import platform
from importlib import metadata
from pathlib import Path

_SOURCE_DIRS = (
    "ingest", "quality", "reconstruct", "model", "provenance", "schema", "sources",
)
_DEPENDENCIES = ("numpy", "pydantic", "PyYAML")


def _source_sha256(package_root: Path) -> str:
    required = [package_root / "__init__.py", package_root / "pipeline.py"]
    required.extend(package_root / directory / "__init__.py" for directory in _SOURCE_DIRS)
    for path in required:
        if not path.is_file():
            raise RuntimeError(f"Required pipeline source is missing: {path.relative_to(package_root)}")

    modules = set(required)
    for directory in _SOURCE_DIRS:
        modules.update(
            path for path in (package_root / directory).rglob("*.py")
            if "__pycache__" not in path.relative_to(package_root).parts
        )

    digest = hashlib.sha256()
    for path in sorted(modules, key=lambda item: item.relative_to(package_root).as_posix()):
        relative = path.relative_to(package_root).as_posix().encode("utf-8")
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise RuntimeError(f"Cannot read pipeline source: {relative.decode('utf-8')}") from exc
        # Length prefixes keep file names and bodies unambiguous.
        for value in (relative, content):
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
    return digest.hexdigest()


def runtime_identity() -> dict:
    """Return strict, JSON-friendly pins for the code and its runtime."""
    dependencies = {}
    for name in _DEPENDENCIES:
        try:
            version = metadata.version(name)
        except metadata.PackageNotFoundError as exc:
            raise RuntimeError(f"Required runtime dependency metadata is missing: {name}") from exc
        if not version:
            raise RuntimeError(f"Required runtime dependency version is missing: {name}")
        dependencies[name] = version

    return {
        "schema_version": 1,
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
        },
        "platform": {"system": platform.system(), "machine": platform.machine()},
        "dependencies": dependencies,
        "source_sha256": _source_sha256(Path(__file__).resolve().parents[1]),
    }
