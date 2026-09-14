"""HIFLD adapter (transmission line geometry + voltages; skeleton authority).

fetch: HIFLD Open "Electric Power Transmission Lines" layer (ArcGIS Open Data;
GeoJSON export is several hundred MB nationally — filter to SPP-state bbox at
download or immediately after; keep only the clipped file in raw/). Confirm the
layer is HIFLD OPEN, not the secured tier (sources.yaml compliance note).

normalize contracts (this adapter OWNS the canonical id — the join lives here):
  normalized/buses.csv:       bus_id, name, ba, lat, lon, role, weight
  normalized/hifld_lines.csv: line_id, from_bus, to_bus, length_mi, voltage_kv
Canonical line_id minting + endpoint/bus derivation + the spatial match to
EIA/FERC1 records is THE JOIN — the real work of activation (see runbook).
"""

from __future__ import annotations

from pathlib import Path


def fetch(snapshot_dir: Path) -> None:  # pragma: no cover - L2 work
    raise NotImplementedError("HIFLD fetch: pick bbox + confirm Open-tier layer URL.")


def normalize(snapshot_dir: Path) -> None:  # pragma: no cover - L2 work
    raise NotImplementedError("HIFLD normalize: canonical-id minting + join.")
