"""HIFLD adapter on a recorded real sample (Mandan/Bismarck ND + the Coal Creek DC
line), served offline through a fake urlopen. See fixtures/real_samples/*/README.md."""

import csv
import io
import json
import math
import urllib.parse
from pathlib import Path

import pytest

from headroom.ingest.adapters import common, eia, hifld
from headroom.ingest.snapshot import update_manifest

REAL = Path(__file__).parent / "fixtures" / "real_samples"
LAYER = (REAL / "hifld" / "layer.json").read_bytes()
FEATURES = json.loads((REAL / "hifld" / "features.geojson").read_bytes())["features"]
PUDL = {t: (REAL / "eia" / f"{t}.parquet").read_bytes() for t in (eia.PLANTS, eia.GENERATORS)}


class Response(io.BytesIO):
    def __init__(self, body: bytes):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))}


def serve(request, timeout=None):
    """Answer only the URLs the adapters build; anything else fails the test."""
    url = request.full_url if hasattr(request, "full_url") else request
    if url == f"{hifld.LAYER_URL}?f=json":
        return Response(LAYER)
    for table, body in PUDL.items():
        if url == eia.pudl_url(table):
            return Response(body)
    if url.startswith(f"{hifld.LAYER_URL}/query?"):
        q = dict(urllib.parse.parse_qsl(url.split("?", 1)[1]))
        assert q["geometry"] == ",".join(str(v) for v in common.region_spec()["hifld_envelope"])
        if q.get("returnCountOnly") == "true":
            return Response(json.dumps({"count": len(FEATURES)}).encode())
        start, size = int(q["resultOffset"]), int(q["resultRecordCount"])
        page = FEATURES[start:start + size]
        body = {"type": "FeatureCollection", "features": page}
        if start + size < len(FEATURES):
            body["properties"] = {"exceededTransferLimit": True}
        return Response(json.dumps(body).encode())
    raise AssertionError(f"unexpected network access: {url}")


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", serve)
    monkeypatch.setattr(common.urllib.request, "urlopen", serve)
    monkeypatch.setattr(hifld, "PAGE", 10)  # 33 features -> 4 pages


def _snapshot(tmp_path: Path) -> Path:
    snap = tmp_path / "snap"
    (snap / "raw").mkdir(parents=True)
    update_manifest(snap, snapshot_date="2026-10-12")
    return snap


def _fetch_all(tmp_path: Path) -> Path:
    snap = _snapshot(tmp_path)
    raw = snap / "raw"
    for table in (eia.PLANTS, eia.GENERATORS):  # only the PUDL half of eia.fetch
        url = eia.pudl_url(table)
        common.record_raw(snap, f"raw:{table}", url, common.download(url, raw / f"{table}.parquet"))
    hifld.fetch(snap)
    return snap


def _rows(snap: Path, table: str) -> list[dict]:
    return list(csv.DictReader((snap / "normalized" / f"{table}.csv").open(newline="")))


@pytest.fixture
def built(tmp_path, offline):
    snap = _fetch_all(tmp_path)
    stats = hifld.normalize(snap)
    lines = {r["line_id"]: r for r in _rows(snap, "hifld_lines")}
    buses = {r["bus_id"]: r for r in _rows(snap, "buses")}
    return snap, stats, lines, buses


def test_fetch_pages_into_one_raw_file_and_pins_version(tmp_path, offline):
    snap = _fetch_all(tmp_path)
    manifest = json.loads((snap / "snapshot_manifest.json").read_text())
    entry = manifest["raw"][hifld.RAW_LINES]
    merged = json.loads((snap / entry["path"]).read_bytes())
    assert [f["properties"]["OBJECTID_1"] for f in merged["features"]] == [
        f["properties"]["OBJECTID_1"] for f in FEATURES
    ]
    assert "resultOffset" not in entry["url"] and "f=geojson" in entry["url"]
    assert hifld.layer_version(json.loads(LAYER)) == "hifld_open_2023-09-05"
    assert manifest["raw"][hifld.RAW_LAYER]["bytes"] == len(LAYER)


def test_pin_mismatch_raises(tmp_path, offline, monkeypatch):
    monkeypatch.setattr(common, "registry_pin", lambda source: "hifld_open_2024-01-01")
    with pytest.raises(RuntimeError, match="re-pin|Re-pin"):
        hifld.fetch(_snapshot(tmp_path))


def test_incomplete_paging_raises(tmp_path, offline, monkeypatch):
    def overcount(request, timeout=None):
        if "returnCountOnly=true" in request.full_url:
            return Response(json.dumps({"count": len(FEATURES) + 1}).encode())
        return serve(request, timeout)

    monkeypatch.setattr("urllib.request.urlopen", overcount)
    with pytest.raises(RuntimeError, match="count query"):
        hifld.fetch(_snapshot(tmp_path))


def test_dc_and_bad_lines_are_excluded(built):
    _, stats, lines, _ = built
    # 150123 is the real Coal Creek - Dickinson 400 kV HVDC line ("DC; OVERHEAD").
    assert stats["excluded"] == {"dc": 1}
    assert "HIFLD_150123" not in lines


def test_unknown_and_low_voltage_rule():
    # The pinned layer has no VOLTAGE < 100 kV feature anywhere (count queries return 0),
    # so this exercises the rule on a real feature with its VOLTAGE mutated in memory.
    real = next(f for f in FEATURES if f["properties"]["ID"] == "156018")
    variants = []
    for kv in (-999999, None, "115", 69, 115):
        f = json.loads(json.dumps(real))
        f["properties"]["VOLTAGE"] = kv
        f["properties"]["ID"] = f"156018_{kv}"
        variants.append(f)
    kept, why = hifld._segments(variants, 100)
    assert why["unknown_kv"] == 3 and why["below_min_kv"] == 1
    assert [s["hid"] for s in kept] == ["156018_115"]


def _pre_footprint():
    """The real sample's segments, endpoint clusters and resolved names."""
    segs, _ = hifld._segments(FEATURES, 100)
    points = [p for s in segs for p in (s["a"], s["b"])]
    cluster = hifld.snap(points, 300)
    ends = {s["hid"]: (2 * k, 2 * k + 1) for k, s in enumerate(segs)}
    def token(s, key):
        return hifld._token(hifld._text(s["props"], key))

    names = hifld.resolve_names([
        (cluster[2 * k], cluster[2 * k + 1], token(s, "SUB_1"), token(s, "SUB_2"))
        for k, s in enumerate(segs) if cluster[2 * k] != cluster[2 * k + 1]
    ])
    return points, cluster, ends, names


def test_snapping_joins_two_segments_at_heskett():
    points, cluster, ends, names = _pre_footprint()
    # MANDAN JUNCTION - HESKETT 230 KV (156017) and MANDAN JUNCTION - HESKETT (156018)
    # end at the 230 kV and 115 kV yards of Heskett station, digitized ~105 m apart:
    # snapping (bus_snap_m = 300) makes them one bus, which resolves to HESKETT.
    pairs = [(i, j) for i in ends["156017"] for j in ends["156018"]
             if cluster[i] == cluster[j] and points[i] != points[j]]
    assert len(pairs) == 1
    i, j = pairs[0]
    assert hifld.haversine_m(*points[i], *points[j]) == pytest.approx(104.9, abs=0.5)
    assert names[cluster[i]] == ("HESKETT", "shared")
    assert any(cluster[e] == cluster[i] for e in ends["164983"])  # HESKETT - TAP160699
    # Five real lines carry MANDAN JUNCTION; they meet at one bus with that name.
    junction = {cluster[e] for hid in ("156016", "156017", "157796", "156018", "125715")
                for e in ends[hid]}
    named = [c for c in junction if names.get(c, ("",))[0] == "MANDAN JUNCTION"]
    assert len(named) == 1 and names[named[0]][1] == "shared"
    assert all(named[0] in {cluster[e] for e in ends[h]}
               for h in ("156016", "156017", "157796", "156018", "125715"))


def test_self_loops_dropped(built):
    _, stats, lines, _ = built
    # e.g. 156020 (32 m) and 157797 (38 m) are shorter than bus_snap_m.
    assert stats["self_loops"] == 7
    assert "HIFLD_156020" not in lines and "HIFLD_157797" not in lines


def test_length_is_haversine_along_polyline(built):
    _, _, lines, _ = built
    f = next(f for f in FEATURES if f["properties"]["ID"] == "157801")  # real 2-vertex line
    (lon1, lat1), (lon2, lat2) = f["geometry"]["coordinates"]
    # Hand computation (equirectangular; equal to haversine within 1e-6 at 634 m).
    dx = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    dy = math.radians(lat2 - lat1)
    hand_mi = 6371008.8 * math.hypot(dx, dy) / 1609.344
    assert hand_mi == pytest.approx(0.3943, abs=1e-4)
    assert float(lines["HIFLD_157801"]["length_mi"]) == pytest.approx(hand_mi, abs=5e-4)
    assert hifld.path_m([(lat1, lon1), (lat2, lon2)]) / 1609.344 == pytest.approx(hand_mi, rel=1e-5)


def test_footprint_keeps_only_swpp_labelled_ends(built):
    _, stats, lines, buses = built
    assert all(b["ba"] == "SWPP" for b in buses.values())
    # Heskett (plant 2790) is MISO in EIA-860; Mandan Refinery (52133) is SWPP. Lines
    # whose ends are nearest Heskett are dropped, whatever their OWNER (all NOT AVAILABLE).
    assert {b["ba_plant_id"] for b in buses.values()} == {"52133"}
    for hid in ("156016", "156017", "157796", "156018", "125715"):  # MANDAN JUNCTION lines
        assert f"HIFLD_{hid}" not in lines
    # HESKETT - TAP160699 is a seam (one end each side); TAP160699 - UNKNOWN160700 is kept.
    assert "HIFLD_164983" not in lines and "HIFLD_164982" in lines
    assert (stats["both_ends_lines"], stats["seam_lines"], stats["foreign_lines"]) == (12, 4, 9)
    used = {r["from_bus"] for r in lines.values()} | {r["to_bus"] for r in lines.values()}
    assert used == set(buses)
    # 112945 is cut off from the rest once the seam line is dropped: the island is
    # removed so the network the DC screen sees is one connected piece.
    assert "HIFLD_112945" not in lines
    assert (stats["kept_lines"], stats["island_lines"], stats["components_before"]) == (11, 1, 2)
    assert {b["component"] for b in buses.values()} == {"1"}


def test_bus_ids_sorted_by_lat_lon_and_gen_attached(built):
    _, _, _, buses = built
    order = sorted(buses.values(), key=lambda b: (float(b["lat"]), float(b["lon"])))
    assert [b["bus_id"] for b in order] == [f"B{n}" for n in range(1, len(buses) + 1)]
    gens = [b for b in buses.values() if b["role"] == "gen"]
    # Mandan Refinery: GEN1 + GEN2 operating at 2.5 MW each; GEN3 is OA (out of service).
    assert [(b["weight"], b["gen_mw"], b["n_plants"]) for b in gens] == [("5.0", "5.0", "1")]
    assert all(b["weight"] == "1.0" for b in buses.values() if b["role"] == "load")


def test_names_are_never_invented(built):
    _, _, lines, buses = built
    for b in buses.values():
        if b["name"]:
            incident = [r for r in lines.values() if b["bus_id"] in (r["from_bus"], r["to_bus"])]
            assert any(b["name"] in (r["sub_1"], r["sub_2"]) for r in incident)
        assert not hifld._NUMBERED_PLACEHOLDER.fullmatch(b["name"])


def test_manifest_stamps(built):
    snap, _, _, _ = built
    tables = json.loads((snap / "snapshot_manifest.json").read_text())["tables"]
    raw = json.loads((snap / "snapshot_manifest.json").read_text())["raw"]
    for t in ("hifld_lines", "buses"):
        assert tables[t]["source"] == "hifld_transmission"
        assert tables[t]["source_version"] == "hifld_open_2023-09-05"
        assert tables[t]["retrieved_at"] == raw[hifld.RAW_LINES]["retrieved_at"]
        assert tables[t]["coverage"] == "observed"
    (contrib,) = tables["buses"]["contributors"]
    assert contrib["source"] == "eia_860_861"
    assert contrib["source_version"] == "pudl_v2024.11.0:eia860_2024"
    assert "no public nodal load" in tables["buses"]["note"]
    assert "footprint" in tables["hifld_lines"]["note"].lower()


def test_normalize_is_deterministic(built, tmp_path):
    snap, _, _, _ = built
    first = {t: (snap / "normalized" / f"{t}.csv").read_bytes() for t in ("hifld_lines", "buses")}
    hifld.normalize(snap)
    assert {t: (snap / "normalized" / f"{t}.csv").read_bytes() for t in first} == first


def test_a_server_that_ignores_the_offset_cannot_page_forever(tmp_path, monkeypatch):
    def stuck(request, timeout=None):  # always the first page, always "more to come"
        url = request.full_url if hasattr(request, "full_url") else request
        if url.startswith(f"{hifld.LAYER_URL}/query?") and "returnCountOnly" not in url:
            body = {"type": "FeatureCollection", "features": FEATURES[:10],
                    "properties": {"exceededTransferLimit": True}}
            return Response(json.dumps(body).encode())
        return serve(request, timeout)

    monkeypatch.setattr("urllib.request.urlopen", stuck)
    monkeypatch.setattr(common.urllib.request, "urlopen", stuck)
    monkeypatch.setattr(hifld, "PAGE", 10)
    with pytest.raises(RuntimeError, match="HIFLD paging passed"):
        hifld.fetch(_snapshot(tmp_path))


def test_raw_entries_cannot_point_outside_the_snapshot(tmp_path):
    snap = _snapshot(tmp_path)
    outside = tmp_path / "elsewhere.bin"
    outside.write_bytes(b"x")
    update_manifest(snap, raw={"raw:escape": {"path": "../elsewhere.bin",
                                              "sha256": common.sha256_of(outside)}})
    with pytest.raises(ValueError, match="escapes the snapshot"):
        common.raw_entry(snap, "raw:escape")


@pytest.mark.parametrize("field,value,match", [
    ("url", "", "nonempty url"),
    ("retrieved_at", "2026-10-12T00:00:00", "timezone-aware"),
    ("sha256", "abc", "invalid sha256"),
])
def test_raw_entries_need_a_full_retrieval_record(tmp_path, field, value, match):
    snap = _snapshot(tmp_path)
    path = snap / "raw" / "layer.json"
    path.write_bytes(LAYER)
    common.record_raw(snap, "raw:layer", "https://example.invalid/layer.json", path)
    assert common.raw_entry(snap, "raw:layer")["abspath"] == path.resolve()
    entry = json.loads((snap / "snapshot_manifest.json").read_text())["raw"]["raw:layer"]
    update_manifest(snap, raw={"raw:layer": {**entry, field: value}})
    with pytest.raises(ValueError, match=match):
        common.raw_entry(snap, "raw:layer")
