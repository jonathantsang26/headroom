"""Draw the SPP topology layer for the project page from an archived snapshot.

    python web/build_map.py data/raw/spp/2026-10-13.r2

Writes web/topology.svg: HIFLD lines by voltage class, EIA-860 generation at its
bus, and queue requests mapped to a bus. Flowgates are not drawn: SPP names them by
market-model bus, and that map is CEII.
"""

import csv
import json
import math
import sys
from pathlib import Path

LON0, LAT0, LAT_REF = -106.0, 49.6, 40.0
SCALE = 64.0
KX = math.cos(math.radians(LAT_REF))


def project(lon: float, lat: float) -> tuple[float, float]:
    return (lon - LON0) * KX * SCALE, (LAT0 - lat) * SCALE


def path_d(coords: list[list[float]], min_step: float = 0.9) -> str:
    pts = [project(lon, lat) for lon, lat in coords]
    kept = [pts[0]]
    for p in pts[1:-1]:
        if math.dist(p, kept[-1]) >= min_step:
            kept.append(p)
    if len(pts) > 1:
        kept.append(pts[-1])
    out = [f"{round(x, 1):g},{round(y, 1):g}" for x, y in kept]
    return "M" + "L".join(out) if len(out) > 1 else ""


def voltage_class(kv: float) -> str:
    if kv >= 300:
        return "kv345"
    if kv >= 200:
        return "kv230"
    return "kv115"


def main(snapshot: Path) -> None:
    norm = snapshot / "normalized"
    lines = {r["hifld_id"]: float(r["voltage_kv"]) for r in csv.DictReader(open(norm / "hifld_lines.csv"))}
    buses = {r["bus_id"]: r for r in csv.DictReader(open(norm / "buses.csv"))}
    queue = list(csv.DictReader(open(norm / "queue.csv")))

    groups: dict[str, list[str]] = {"kv115": [], "kv230": [], "kv345": []}
    gj = json.load(open(snapshot / "raw" / "hifld_transmission_lines.geojson"))
    for f in gj["features"]:
        hid = str(f["properties"].get("ID"))
        if hid not in lines:
            continue
        g = f["geometry"]
        parts = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]
        for part in parts:
            d = path_d(part)
            if d:
                groups[voltage_class(lines[hid])].append(d)

    gen = []
    for b in buses.values():
        mw = float(b["gen_mw"] or 0)
        if mw > 0:
            x, y = project(float(b["lon"]), float(b["lat"]))
            gen.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{max(1.2, math.sqrt(mw) * 0.16):.2f}"/>')

    q_mw: dict[str, float] = {}
    for r in queue:
        if r["near_bus"] in buses:
            q_mw[r["near_bus"]] = q_mw.get(r["near_bus"], 0.0) + float(r["mw"] or 0)
    qs = []
    for bid, mw in q_mw.items():
        x, y = project(float(buses[bid]["lon"]), float(buses[bid]["lat"]))
        qs.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{max(2.0, math.sqrt(mw) * 0.16):.2f}"/>')

    xs, ys = [], []
    for b in buses.values():
        x, y = project(float(b["lon"]), float(b["lat"]))
        xs.append(x)
        ys.append(y)
    pad = 14
    vb = f"{min(xs) - pad:.0f} {min(ys) - pad:.0f} {max(xs) - min(xs) + 2 * pad:.0f} {max(ys) - min(ys) + 2 * pad:.0f}"

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" preserveAspectRatio="xMidYMid meet">']
    for cls in ("kv115", "kv230", "kv345"):
        svg.append(f'<path class="ln {cls}" data-layer="{cls}" d="{"".join(groups[cls])}"/>')
    svg.append(f'<g class="gen" data-layer="gen">{"".join(gen)}</g>')
    svg.append(f'<g class="queue" data-layer="queue">{"".join(qs)}</g>')
    svg.append("</svg>")
    out = Path(__file__).with_name("topology.svg")
    out.write_text("\n".join(svg) + "\n")
    print(f"{out}: {sum(len(v) for v in groups.values())} line parts, {len(gen)} gen buses, {len(qs)} queue buses")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
