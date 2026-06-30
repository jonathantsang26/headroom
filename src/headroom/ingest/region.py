"""Region registry. The pipeline is parameterized by region (Decision: SPP pilot;
MISO is RTO #2). `spp-synth` is the offline fixture region used by tests/demos; a
live region would point `data_dir` at pinned raw snapshots under data/raw/."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURES = _REPO_ROOT / "tests" / "fixtures"


@dataclass(frozen=True)
class RegionConfig:
    name: str
    rto: str
    data_dir: Path
    synthetic: bool


REGIONS: dict[str, RegionConfig] = {
    # Offline fixture region: fabricated SPP-shaped data. synthetic=True means the
    # publishable gate will refuse a shareable export (source = synthetic_fixture).
    "spp-synth": RegionConfig(
        name="spp-synth", rto="SPP", data_dir=_FIXTURES / "spp_synth", synthetic=True
    ),
    # A second region with the SAME config shape — proves generalization (Phase 5).
    # Points at the same fixture dir; only here to demonstrate parameterization, not
    # to ship shallow real MISO ingest.
    "miso-synth": RegionConfig(
        name="miso-synth", rto="MISO", data_dir=_FIXTURES / "spp_synth", synthetic=True
    ),
}


def get_region(name: str) -> RegionConfig:
    try:
        return REGIONS[name]
    except KeyError:
        raise KeyError(
            f"Unknown region {name!r}. Known: {sorted(REGIONS)}"
        ) from None
