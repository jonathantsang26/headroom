"""The source registry: parse `config/sources.yaml` into typed `SourceSpec`s.

FAIL-CLOSED by construction: `Compliance.publishable` defaults to False, and a
source entry with no `compliance` block at all gets a default `Compliance()` —
also non-publishable. A source is never publishable by omission.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

# config/sources.yaml relative to the repo root (this file is src/headroom/sources/).
DEFAULT_SOURCES_PATH = Path(__file__).resolve().parents[3] / "config" / "sources.yaml"


class Compliance(BaseModel):
    publishable: bool = False  # fail-closed: non-publishable unless explicitly true
    basis: str = ""
    notes: str = ""


class SourceSpec(BaseModel):
    name: str
    bucket: Literal["a", "b"]
    tier: int | str
    kind: str = ""
    gives: str = ""
    access: str = ""
    source_version: str
    compliance: Compliance = Field(default_factory=Compliance)

    @property
    def publishable(self) -> bool:
        return self.compliance.publishable


class SourceRegistry:
    """Lookup over `SourceSpec`s, keyed by name."""

    def __init__(self, specs: list[SourceSpec]) -> None:
        self._by_name: dict[str, SourceSpec] = {}
        for spec in specs:
            if spec.name in self._by_name:
                raise ValueError(f"Duplicate source name in registry: {spec.name!r}")
            self._by_name[spec.name] = spec

    def get(self, name: str) -> SourceSpec | None:
        return self._by_name.get(name)

    def __getitem__(self, name: str) -> SourceSpec:
        spec = self._by_name.get(name)
        if spec is None:
            raise KeyError(f"Unknown source: {name!r}")
        return spec

    def __contains__(self, name: str) -> bool:
        return name in self._by_name

    def all(self) -> list[SourceSpec]:
        return list(self._by_name.values())

    @classmethod
    def from_yaml(cls, path: str | Path) -> "SourceRegistry":
        raw = yaml.safe_load(Path(path).read_text()) or {}
        entries = raw.get("sources", [])
        return cls([SourceSpec.model_validate(entry) for entry in entries])


def load_registry(path: str | Path | None = None) -> SourceRegistry:
    return SourceRegistry.from_yaml(path or DEFAULT_SOURCES_PATH)
