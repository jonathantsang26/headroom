"""HIFLD adapter: HIFLD Open transmission-line geometry and voltages (the skeleton
authority). Buses are minted by snapping line endpoints; the SPP footprint is the
balancing authority of each bus's nearest EIA-860 plant."""

from __future__ import annotations

import json
import math
import re
import tempfile
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from headroom.ingest.adapters import eia
from headroom.ingest.adapters.common import (
    download,
    raw_entry,
    record_raw,
    region_spec,
    require_pin,
    write_table,
)

LAYER_URL = (
    "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/arcgis/rest/services/"
    "Electric_Power_Transmission_Lines/FeatureServer/0"
)
OUT_FIELDS = (
    "OBJECTID_1,ID,TYPE,STATUS,OWNER,VOLTAGE,VOLT_CLASS,INFERRED,SUB_1,SUB_2,SOURCE,VAL_DATE"
)
PAGE = 2000
RAW_LAYER = "raw:hifld_layer"
RAW_LINES = "raw:hifld_transmission_lines"

EARTH_R_M = 6_371_008.8
M_PER_MI = 1609.344
STUB_M = 1.0  # line parts shorter than this are digitizing stubs, not geometry
EXCLUDED_STATUS = {"INACTIVE": "inactive", "UNDER CONSTRUCTION": "under_construction"}
# Exclusion reasons, checked in this order; a feature counts under the first hit.
REASONS = (
    "bad_id", "duplicate_id", "dc", "inactive", "under_construction",
    "unknown_kv", "below_min_kv", "bad_geometry",
)
# HIFLD endpoint placeholders: numbered ones (TAP140579) identify one node and help
# resolve which end a name belongs to, but are never emitted as a bus name; bare
# ones carry no information at all.
_NUMBERED_PLACEHOLDER = re.compile(r"(TAP|UNKNOWN|RISER|DEADEND)\d+")
_GENERIC_NAMES = {"", "TAP", "UNKNOWN", "RISER", "DEADEND", "NOT AVAILABLE", "NONE"}
LINE_FIELDS = [
    "line_id", "from_bus", "to_bus", "voltage_kv", "length_mi",
    "hifld_id", "owner", "sub_1", "sub_2", "status", "type", "inferred",
]
BUS_FIELDS = [
    "bus_id", "name", "ba", "lat", "lon", "role", "weight",
    "n_lines", "gen_mw", "n_plants", "name_basis", "ba_plant_id", "ba_plant_km", "component",
]


def layer_version(meta: dict) -> str:
    """`hifld_open_YYYY-MM-DD` from the layer's editingInfo.dataLastEditDate (UTC)."""
    ms = _field(meta, "editingInfo").get("dataLastEditDate")
    if not isinstance(ms, int) or isinstance(ms, bool):
        raise ValueError("HIFLD layer metadata has no integer editingInfo.dataLastEditDate")
    return "hifld_open_" + datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()


def _query(envelope: list[float], **extra: str) -> str:
    params = {
        "where": "1=1",
        "geometry": ",".join(str(v) for v in envelope),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        **extra,
    }
    return f"{LAYER_URL}/query?{urllib.parse.urlencode(params)}"


def _field(obj, key: str) -> dict:
    """obj[key] when both are JSON objects, else {} (downloads are untrusted)."""
    value = obj.get(key) if isinstance(obj, dict) else None
    return value if isinstance(value, dict) else {}


def _read_json(path: Path) -> dict:
    data = json.loads(path.read_bytes())
    if not isinstance(data, dict) or "error" in data:
        raise RuntimeError(f"HIFLD query failed: {str(data)[:300]}")
    return data


def fetch(snapshot_dir: Path) -> dict:
    """Pin-check the layer, then page the envelope query into ONE raw GeoJSON file."""
    raw = snapshot_dir / "raw"
    meta_url = f"{LAYER_URL}?f=json"
    meta_path = download(meta_url, raw / "hifld_layer.json", max_mb=5)
    meta_entry = record_raw(snapshot_dir, RAW_LAYER, meta_url, meta_path)
    require_pin("hifld_transmission", layer_version(_read_json(meta_path)))

    envelope = region_spec()["hifld_envelope"]
    feature_query = dict(
        outFields=OUT_FIELDS, orderByFields="OBJECTID_1", outSR="4326",
        geometryPrecision="6", f="geojson",
    )
    features: list[dict] = []
    with tempfile.TemporaryDirectory(dir=snapshot_dir, prefix=".hifld-pages-") as tmp:
        count_path = download(
            _query(envelope, returnCountOnly="true", f="json"), Path(tmp) / "count.json", max_mb=1,
        )
        expected = _read_json(count_path).get("count")
        page = 0
        while True:
            url = _query(
                envelope, resultOffset=str(len(features)), resultRecordCount=str(PAGE),
                **feature_query,
            )
            data = _read_json(download(url, Path(tmp) / f"page{page:04d}.geojson"))
            batch = data.get("features")
            if data.get("type") != "FeatureCollection" or not isinstance(batch, list):
                raise RuntimeError("HIFLD query did not return a GeoJSON FeatureCollection")
            features.extend(batch)
            page += 1
            if not batch or not _field(data, "properties").get("exceededTransferLimit"):
                break
    ids = [_field(f, "properties").get("OBJECTID_1") for f in features]
    if any(not isinstance(i, int) for i in ids) or ids != sorted(set(ids)):
        raise RuntimeError("HIFLD pages are not strictly ordered by unique OBJECTID_1")
    if expected != len(features):
        raise RuntimeError(f"HIFLD returned {len(features)} features; count query said {expected}")

    path = raw / "hifld_transmission_lines.geojson"
    body = json.dumps(
        {"type": "FeatureCollection", "features": features},
        separators=(",", ":"), sort_keys=True, ensure_ascii=True,
    )
    path.write_text(body, encoding="ascii")
    entry = record_raw(snapshot_dir, RAW_LINES, _query(envelope, **feature_query), path)
    return {"bytes": entry["bytes"] + meta_entry["bytes"], "features": len(features)}


# ---- geometry ------------------------------------------------------------------

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_R_M * math.asin(min(1.0, math.sqrt(h)))


def path_m(pts: list[tuple[float, float]]) -> float:
    """Haversine length of a (lat, lon) polyline in metres."""
    return sum(haversine_m(*a, *b) for a, b in zip(pts, pts[1:]))


def _parts(geom) -> list[list[tuple[float, float]]] | None:
    """(lat, lon) parts of a LineString / MultiLineString; None when malformed."""
    if not isinstance(geom, dict):
        return None
    kind, coords = geom.get("type"), geom.get("coordinates")
    raw = [coords] if kind == "LineString" else coords if kind == "MultiLineString" else None
    if not isinstance(raw, list):
        return None
    parts = []
    for part in raw:
        if not isinstance(part, list):
            return None
        pts = []
        for xy in part:
            if not isinstance(xy, list) or len(xy) < 2:
                return None
            lon, lat = _number(xy[0]), _number(xy[1])
            if lon is None or lat is None or abs(lon) > 180 or abs(lat) > 90:
                return None
            pts.append((lat, lon))
        parts.append(pts)
    return parts


def _chain(parts: list[list[tuple[float, float]]]) -> tuple[tuple, tuple, float]:
    """Order and orient parts greedily by nearest free end (HIFLD multipart lines
    are often stored reversed or out of order). Returns the chain's two ends and
    the widest gap bridged in metres; gaps never count toward length."""
    head, tail = parts[0][0], parts[0][-1]
    rest = list(parts[1:])
    widest = 0.0
    while rest:
        best = None
        for i, part in enumerate(rest):
            for first, last in ((part[0], part[-1]), (part[-1], part[0])):
                for at_tail in (True, False):
                    gap = haversine_m(*tail, *first) if at_tail else haversine_m(*last, *head)
                    if best is None or gap < best[0]:
                        best = (gap, i, first, last, at_tail)
        gap, i, first, last, at_tail = best
        rest.pop(i)
        if at_tail:
            tail = last
        else:
            head = first
        widest = max(widest, gap)
    return head, tail, widest


def _number(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else repr(float(v))


def _text(props: dict, key: str) -> str:
    v = props.get(key)
    return "" if v is None else " ".join(str(v).split())


# ---- filtering, snapping, naming -------------------------------------------------

def _segments(features: list, min_kv: float) -> tuple[list[dict], Counter]:
    """Lines that pass the attribute and geometry filters, plus exclusion counts."""
    kept: list[dict] = []
    why: Counter = Counter({r: 0 for r in REASONS})
    why.update({"multipart": 0, "stub_parts_dropped": 0, "multipart_gap_over_snap": 0})
    seen: set[str] = set()
    widest_gap = 0.0
    for f in features:
        props = _field(f, "properties")
        hid, kv = _text(props, "ID"), _number(props.get("VOLTAGE"))
        status, ftype = _text(props, "STATUS").upper(), _text(props, "TYPE").upper()
        reason = None
        if not hid:
            reason = "bad_id"
        elif hid in seen:
            reason = "duplicate_id"
        elif ftype.startswith("DC"):
            reason = "dc"
        elif status in EXCLUDED_STATUS:
            reason = EXCLUDED_STATUS[status]
        elif kv is None or kv < 0:
            reason = "unknown_kv"
        elif kv < min_kv:
            reason = "below_min_kv"
        parts = None if reason else _parts(f.get("geometry"))
        if reason is None:
            whole = [p for p in parts or [] if len(p) >= 2 and path_m(p) >= STUB_M]
            why["stub_parts_dropped"] += len(parts or []) - len(whole)
            parts = whole
            if not parts:
                reason = "bad_geometry"
        if reason:
            why[reason] += 1
            continue
        seen.add(hid)
        a, b, gap = _chain(parts)
        if len(parts) > 1:
            why["multipart"] += 1
            widest_gap = max(widest_gap, gap)
        kept.append({
            "hid": hid, "props": props, "kv": kv, "a": a, "b": b, "gap_m": gap,
            "length_m": sum(path_m(p) for p in parts),
        })
    why["widest_multipart_gap_m"] = round(widest_gap)
    return kept, why


def snap(points: list[tuple[float, float]], snap_m: float) -> list[int]:
    """Cluster id (smallest member index) per point: single-linkage union-find over
    pairs within snap_m by haversine. Candidate pairs come from a lat/lon grid whose
    cells are at least snap_m wide at the data's highest latitude."""
    dlat = math.degrees(snap_m / EARTH_R_M)
    top = min(max(abs(lat) for lat, _ in points) + dlat, 89.0)
    dlon = dlat / math.cos(math.radians(top))
    keys = [(math.floor(lat / dlat), math.floor(lon / dlon)) for lat, lon in points]
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, k in enumerate(keys):
        grid[k].append(i)
    parent = list(range(len(points)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, (ki, kj) in enumerate(keys):
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for j in grid.get((ki + di, kj + dj), ()):
                    if j > i and haversine_m(*points[i], *points[j]) <= snap_m:
                        ri, rj = find(i), find(j)
                        if ri != rj:
                            parent[max(ri, rj)] = min(ri, rj)
    return [find(i) for i in range(len(points))]


def _token(raw: str) -> str | None:
    return None if raw.upper() in _GENERIC_NAMES else raw


def resolve_names(
    lines: list[tuple[int, int, str | None, str | None]],
) -> dict[int, tuple[str, str]]:
    """bus -> (name token, basis) from lines (from_bus, to_bus, sub_1, sub_2).

    HIFLD does not say which end SUB_1 belongs to. A bus is named once a token is
    offered by lines from at least two distinct neighbouring buses and no other token
    ties ("shared"); naming a bus orients its lines carrying that token, so each
    such line offers only its other token to its far end. A bus whose oriented lines
    offer exactly one token takes it ("elimination"). Anything else stays unnamed."""
    incident: dict[int, list[int]] = defaultdict(list)
    for k, (a, b, _, _) in enumerate(lines):
        incident[a].append(k)
        incident[b].append(k)
    given: dict[tuple[int, int], str | None] = {}  # (line, bus) -> token once oriented
    resolved: dict[int, tuple[str, str]] = {}

    def offers(bus: int, oriented_only: bool = False) -> dict[str, set[int]]:
        out: dict[str, set[int]] = defaultdict(set)
        for k in incident[bus]:
            a, b, x, y = lines[k]
            far = b if a == bus else a
            if (k, bus) in given:
                tokens = {given[(k, bus)]}
            else:
                tokens = set() if oriented_only else {x, y}
            for t in tokens - {None}:
                out[t].add(far)
        return out

    def shared(bus: int) -> str | None:
        support = {t: len(f) for t, f in offers(bus).items()}
        if not support:
            return None
        best = max(support.values())
        winners = [t for t, c in support.items() if c == best]
        return winners[0] if best >= 2 and len(winners) == 1 else None

    def assign(bus: int, token: str, basis: str) -> None:
        resolved[bus] = (token, basis)
        for k in incident[bus]:
            a, b, x, y = lines[k]
            if (k, bus) in given or token not in (x, y):
                continue
            given[(k, bus)] = token
            given[(k, b if a == bus else a)] = y if token == x else x

    changed = True
    while changed:
        changed = False
        claims = sorted(
            (-max(len(f) for f in offers(bus).values()), bus)
            for bus in incident if bus not in resolved and shared(bus) is not None
        )
        for _, bus in claims:  # strongest first; re-check, earlier claims orient lines
            token = shared(bus)
            if token is not None:
                assign(bus, token, "shared")
                changed = True
        for bus in sorted(incident):
            if bus in resolved:
                continue
            offered = offers(bus, oriented_only=True)
            if len(offered) == 1:
                assign(bus, next(iter(offered)), "elimination")
                changed = True
    return resolved


def components(edges: list[tuple[int, int]], nodes: list[int]) -> dict[int, int]:
    """Connected-component rank per node: 1 = most nodes, ties by first node in order."""
    parent = {n: n for n in nodes}

    def find(n: int) -> int:
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    groups: dict[int, list[int]] = defaultdict(list)
    for order, n in enumerate(nodes):
        groups[find(n)].append(order)
    ranked = sorted(groups.values(), key=lambda g: (-len(g), g[0]))
    return {nodes[i]: rank for rank, g in enumerate(ranked, start=1) for i in g}


def nearest(src: np.ndarray, dst: np.ndarray, chunk: int = 512) -> tuple[np.ndarray, np.ndarray]:
    """Index of and haversine distance (m) to the nearest dst point for each src
    point; both are (n, 2) arrays of (lat, lon) degrees. Ties go to the lower index."""
    idx = np.zeros(len(src), dtype=np.int64)
    dist = np.zeros(len(src))
    dlat, dlon = np.radians(dst[:, 0]), np.radians(dst[:, 1])
    cos_d = np.cos(dlat)
    for s in range(0, len(src), chunk):
        la = np.radians(src[s:s + chunk, 0])[:, None]
        lo = np.radians(src[s:s + chunk, 1])[:, None]
        h = np.sin((dlat - la) / 2) ** 2 + np.cos(la) * cos_d * np.sin((dlon - lo) / 2) ** 2
        j = np.argmin(h, axis=1)
        idx[s:s + chunk] = j
        hj = np.clip(h[np.arange(len(j)), j], 0.0, 1.0)
        dist[s:s + chunk] = 2 * EARTH_R_M * np.arcsin(np.sqrt(hj))
    return idx, dist


# ---- normalize -----------------------------------------------------------------

def normalize(snapshot_dir: Path) -> dict:
    """Mint buses and hifld_lines for the SPP footprint; returns the run counts."""
    spec = region_spec()
    ba_code, snap_m = spec["eia_ba_code"], float(spec["bus_snap_m"])
    meta = raw_entry(snapshot_dir, RAW_LAYER)
    version = require_pin("hifld_transmission", layer_version(_read_json(meta["abspath"])))
    lines_raw = raw_entry(snapshot_dir, RAW_LINES)
    features = _read_json(lines_raw["abspath"]).get("features")
    if not isinstance(features, list):
        raise ValueError("HIFLD raw file has no features list")

    segs, why = _segments(features, float(spec["hifld_min_kv"]))
    if not segs:
        raise ValueError("No HIFLD lines survived filtering")
    points = [p for s in segs for p in (s["a"], s["b"])]
    cluster = snap(points, snap_m)
    self_loops = 0
    lines = []
    for k, s in enumerate(segs):
        s["from"], s["to"] = cluster[2 * k], cluster[2 * k + 1]
        if s["from"] == s["to"]:
            self_loops += 1
        else:
            lines.append(s)
    members: dict[int, list[int]] = defaultdict(list)
    for i, c in enumerate(cluster):
        members[c].append(i)
    centroid = {
        c: (sum(points[i][0] for i in m) / len(m), sum(points[i][1] for i in m) / len(m))
        for c, m in members.items()
    }

    names = resolve_names([
        (s["from"], s["to"], _token(_text(s["props"], "SUB_1")), _token(_text(s["props"], "SUB_2")))
        for s in lines
    ])

    # Footprint: each endpoint bus takes the BA of its nearest located EIA-860 plant.
    plants = eia.load_plants(snapshot_dir)
    located = [p for p in plants if p["lat"] is not None and p["lon"] is not None]
    pool = [p for p in located if p["ba"]]
    if not pool:
        raise ValueError("EIA-860 has no located plants with a balancing authority")
    ends = sorted({c for s in lines for c in (s["from"], s["to"])})
    near_i, near_d = nearest(
        np.array([centroid[c] for c in ends]), np.array([(p["lat"], p["lon"]) for p in pool]),
    )
    label = {c: (pool[j], d) for c, j, d in zip(ends, near_i.tolist(), near_d.tolist())}
    kept, seam, foreign = [], [], []
    for s in lines:
        inside = (label[s["from"]][0]["ba"] == ba_code) + (label[s["to"]][0]["ba"] == ba_code)
        (kept if inside == 2 else seam if inside == 1 else foreign).append(s)
    if not kept:
        raise ValueError(f"No HIFLD line has both ends labelled {ba_code}")
    # The DC screen needs one connected network, and the small pieces are labelling
    # or seam artifacts (e.g. a mislabelled plant pulling a few foreign buses in):
    # keep the largest connected piece and count what it drops.
    pre_nodes = sorted({c for s in kept for c in (s["from"], s["to"])})
    pre_comp = components([(s["from"], s["to"]) for s in kept], pre_nodes)
    islands = {
        "components_before": max(pre_comp.values()),
        "island_lines": sum(1 for s in kept if pre_comp[s["from"]] > 1),
        "island_buses": sum(1 for c in pre_nodes if pre_comp[c] > 1),
        "island_miles": round(
            sum(s["length_m"] for s in kept if pre_comp[s["from"]] > 1) / M_PER_MI, 1),
    }
    both_ends = len(kept)
    kept = [s for s in kept if pre_comp[s["from"]] == 1]

    used = sorted(
        {c for s in kept for c in (s["from"], s["to"])},
        key=lambda c: (round(centroid[c][0], 6), round(centroid[c][1], 6), c),
    )
    bus_id = {c: f"B{n}" for n, c in enumerate(used, start=1)}
    radius = max(
        haversine_m(*centroid[c], *points[i]) for c in used for i in members[c]
    )

    # Roles: each located SWPP plant attaches to its nearest kept bus within plant_snap_km.
    region_plants = [p for p in located if p["ba"] == ba_code]
    unlocated = sum(
        1 for p in plants if p["ba"] == ba_code and (p["lat"] is None or p["lon"] is None)
    )
    gen_mw: dict[int, float] = defaultdict(float)
    n_plants: dict[int, int] = defaultdict(int)
    unattached = []
    if region_plants:
        bi, bd = nearest(
            np.array([(p["lat"], p["lon"]) for p in region_plants]),
            np.array([centroid[c] for c in used]),
        )
        for p, j, d in zip(region_plants, bi.tolist(), bd.tolist()):
            if d <= float(spec["plant_snap_km"]) * 1000:
                gen_mw[used[j]] += p["capacity_mw"]
                n_plants[used[j]] += 1
            else:
                unattached.append(p)

    degree: Counter = Counter(c for s in kept for c in (s["from"], s["to"]))
    comp = components([(s["from"], s["to"]) for s in kept], used)
    assert max(comp.values()) == 1, "kept network must be one connected piece"
    bus_rows = []
    for c in used:
        token, basis = names.get(c, (None, ""))
        if token is not None and _NUMBERED_PLACEHOLDER.fullmatch(token.upper()):
            token, basis = None, "placeholder"
        plant, dist = label[c]
        mw = round(gen_mw.get(c, 0.0), 3)
        bus_rows.append({
            "bus_id": bus_id[c], "name": token or "", "ba": ba_code,
            "lat": f"{centroid[c][0]:.6f}", "lon": f"{centroid[c][1]:.6f}",
            "role": "gen" if mw > 0 else "load", "weight": str(mw) if mw > 0 else "1.0",
            "n_lines": degree[c], "gen_mw": str(mw), "n_plants": n_plants.get(c, 0),
            "name_basis": basis, "ba_plant_id": plant["plant_id_eia"],
            "ba_plant_km": f"{dist / 1000:.3f}", "component": comp[c],
        })

    line_rows = []
    for s in sorted(kept, key=lambda s: (len(s["hid"]), s["hid"])):
        p = s["props"]
        line_rows.append({
            "line_id": f"HIFLD_{s['hid']}", "from_bus": bus_id[s["from"]],
            "to_bus": bus_id[s["to"]], "voltage_kv": _fmt(s["kv"]),
            "length_mi": f"{s['length_m'] / M_PER_MI:.3f}", "hifld_id": s["hid"],
            "owner": _text(p, "OWNER"), "sub_1": _text(p, "SUB_1"), "sub_2": _text(p, "SUB_2"),
            "status": _text(p, "STATUS"), "type": _text(p, "TYPE"),
            "inferred": _text(p, "INFERRED"),
        })

    owners_kept = Counter(_text(s["props"], "OWNER") for s in kept)
    owners_dropped = Counter(_text(s["props"], "OWNER") for s in seam + foreign)
    by_kv = Counter(_fmt(s["kv"]) for s in kept)
    miles = sum(s["length_m"] for s in kept) / M_PER_MI
    excluded = {r: why[r] for r in REASONS if why[r]}
    excluded_text = ", ".join(f"{k}={v}" for k, v in excluded.items()) or "none"
    named = sum(1 for r in bus_rows if r["name"])
    gen_buses = sum(1 for r in bus_rows if r["role"] == "gen")
    attached_mw = sum(gen_mw.values())
    stamp = dict(source="hifld_transmission", source_version=version,
                 retrieved_at=lines_raw["retrieved_at"])

    write_table(
        snapshot_dir, "hifld_lines", line_rows, LINE_FIELDS, **stamp, coverage="observed",
        note=(
            f"{len(features)} HIFLD Open features in the hifld_envelope; excluded "
            f"{sum(excluded.values())} ({excluded_text}; "
            f"first matching reason in order {', '.join(REASONS)}); kept non-DC lines >= "
            f"{_fmt(float(spec['hifld_min_kv']))} kV not INACTIVE/UNDER CONSTRUCTION. "
            f"{why['multipart']} multipart lines chained by nearest free end "
            f"({why['stub_parts_dropped']} sub-1 m stub parts dropped; widest gap "
            f"{why['widest_multipart_gap_m']} m, never counted in length). Endpoints "
            f"snapped within {_fmt(snap_m)} m; {self_loops} self-loops dropped. Footprint: "
            f"each endpoint bus takes the balancing authority of its nearest located "
            f"EIA-860 {eia.pin_860()[1]} operating plant ({len(pool)} plants with a BA, all "
            f"BAs); {both_ends} lines have both ends {ba_code}, dropped {len(seam)} with "
            f"one end {ba_code} (seams) and {len(foreign)} with neither. Of those, the "
            f"{islands['components_before'] - 1} pieces outside the largest connected piece "
            f"were dropped ({islands['island_lines']} lines, {islands['island_buses']} buses, "
            f"{islands['island_miles']} mi; e.g. a mislabelled plant pulling a few foreign "
            f"buses in, or pieces cut off by a dropped seam line), so the network is one "
            f"connected piece: {len(kept)} lines kept. OWNER is not used "
            f"as a filter ({owners_kept.get('NOT AVAILABLE', 0)} kept lines have OWNER=NOT "
            f"AVAILABLE). length_mi is haversine along the WGS84 polyline "
            f"({miles:.1f} mi total)."
        ),
    )
    write_table(
        snapshot_dir, "buses", bus_rows, BUS_FIELDS, **stamp, coverage="observed",
        contributors=[eia.stamp_860(snapshot_dir)],
        note=(
            f"{len(bus_rows)} buses minted by single-linkage snapping of kept HIFLD line "
            f"endpoints within {_fmt(snap_m)} m (a bus may span more; widest member offset "
            f"{radius:.0f} m from its centroid); lat/lon = centroid; numbered by sorted "
            f"(lat, lon). name = the SUB_1/SUB_2 value shared by lines meeting there or "
            f"fixed by elimination along a line ({named} named); placeholders "
            f"(TAP*/UNKNOWN*/RISER*/DEADEND*/NOT AVAILABLE/NONE) are left blank. ba_plant_id/"
            f"ba_plant_km = the nearest EIA-860 plant that set the footprint label; component "
            f"= connected-component rank (always 1: islands are dropped). {gen_buses} gen buses carry "
            f"{attached_mw:.1f} MW of EIA-860 operating (OP/SB) nameplate from "
            f"{sum(n_plants.values())} {ba_code} plants snapped to the nearest bus within "
            f"{_fmt(float(spec['plant_snap_km']))} km; {len(unattached)} {ba_code} plants "
            f"({sum(p['capacity_mw'] for p in unattached):.1f} MW) were farther and are not "
            f"attached, {unlocated} lack a location. Other buses are role=load with uniform "
            f"weight 1.0: there is no public nodal load."
        ),
    )
    return {
        "features": len(features), "excluded": excluded, "multipart": why["multipart"],
        "stub_parts_dropped": why["stub_parts_dropped"],
        "widest_multipart_gap_m": why["widest_multipart_gap_m"], "self_loops": self_loops,
        "lines_after_filter": len(lines), "both_ends_lines": both_ends,
        "kept_lines": len(kept), "seam_lines": len(seam),
        "foreign_lines": len(foreign), "buses": len(bus_rows), "named_buses": named,
        "kept_by_kv": dict(sorted(by_kv.items(), key=lambda kv: float(kv[0]))),
        "line_miles": round(miles, 1), "gen_buses": gen_buses,
        "attached_mw": round(attached_mw, 1), "attached_plants": sum(n_plants.values()),
        "unattached_plants": len(unattached),
        "unattached_mw": round(sum(p["capacity_mw"] for p in unattached), 1),
        "unlocated_plants": unlocated, "max_bus_radius_m": round(radius), **islands,
        "owners_kept": owners_kept.most_common(12),
        "owners_dropped": owners_dropped.most_common(12),
    }
