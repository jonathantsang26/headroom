"""Shared adapter plumbing: polite stdlib download -> raw/, hash, manifest entry,
and the one way a normalized table is written (bytes, sha256, stamp, coverage)."""

from __future__ import annotations

import csv
import io
import json
import os
import ssl
import tempfile
import urllib.request
from pathlib import Path

from headroom.ingest.snapshot import (
    COVERAGE_LEVELS,
    MANIFEST_NAME,
    sha256_of,
    update_manifest,
    utcnow_iso,
)

UA = "headroom-snapshot/0.1 (public-data research; contact: repo owner)"
# Fallback CA bundles when the interpreter ships without one (python.org macOS
# builds): SSL_CERT_FILE wins, then certifi if installed, then the OS bundle.
_SYSTEM_CA_FILES = ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt")


def ssl_context() -> ssl.SSLContext:
    cafile = os.environ.get("SSL_CERT_FILE")
    if not cafile:
        try:
            import certifi  # type: ignore

            cafile = certifi.where()
        except ImportError:
            cafile = next((p for p in _SYSTEM_CA_FILES if Path(p).is_file()), None)
    return ssl.create_default_context(cafile=cafile)


def download(url: str, dest: Path, *, max_mb: int = 250, timeout: int = 120) -> Path:
    """Download url atomically to dest, preserving it if the transfer fails."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    cap = max_mb * (1 << 20)
    got = 0
    temporary = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            expected = None
            # Transfer-Encoding takes precedence over Content-Length.
            length = resp.headers.get("Content-Length", "").strip()
            if (
                not resp.headers.get("Transfer-Encoding")
                and length.isascii()
                and length.isdecimal()
            ):
                expected = int(length)
                if expected > cap:
                    raise RuntimeError(f"{url} exceeded {max_mb} MB cap")
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=dest.parent, prefix=f".{dest.name}.", suffix=".part", delete=False,
            ) as out:
                temporary = Path(out.name)
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    got += len(chunk)
                    if got > cap:
                        raise RuntimeError(f"{url} exceeded {max_mb} MB cap")
                    out.write(chunk)
                if expected is not None and got != expected:
                    raise RuntimeError(
                        f"{url} returned {got} bytes; expected {expected} from Content-Length"
                    )
                out.flush()
                os.fsync(out.fileno())
        temporary.replace(dest)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return dest


def install_ssl_opener() -> None:
    """Route urllib through `ssl_context()` (the builder calls this once)."""
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context()))
    urllib.request.install_opener(opener)


def record_raw(snapshot_dir: Path, name: str, url: str, path: Path) -> dict:
    """Hash a downloaded raw file and record it in the snapshot manifest."""
    entry = {
        "url": url,
        "path": str(path.relative_to(snapshot_dir)),
        "sha256": sha256_of(path),
        "retrieved_at": utcnow_iso(),
        "bytes": path.stat().st_size,
    }
    update_manifest(snapshot_dir, raw={name: entry})
    return entry


def raw_entry(snapshot_dir: Path, name: str) -> dict:
    """A raw input recorded by fetch(), re-verified against its bytes."""
    manifest = json.loads((snapshot_dir / MANIFEST_NAME).read_text())
    entry = manifest.get("raw", {}).get(name)
    if not isinstance(entry, dict):
        raise KeyError(f"Raw input {name!r} was not fetched into {snapshot_dir}")
    path = snapshot_dir / entry["path"]
    if sha256_of(path) != entry["sha256"]:
        raise ValueError(f"Raw input {name!r} does not match its recorded sha256")
    return {**entry, "abspath": path}


def registry_pin(source: str) -> str:
    """The source_version config/sources.yaml pins for a registered source."""
    from headroom.sources import load_registry

    return load_registry()[source].source_version


def require_pin(source: str, observed_version: str) -> str:
    """Fail closed when the live source no longer serves the pinned version."""
    pinned = registry_pin(source)
    if observed_version != pinned:
        raise RuntimeError(
            f"{source}: upstream serves {observed_version!r} but config/sources.yaml "
            f"pins {pinned!r}. Re-pin deliberately; never build on a silent change."
        )
    return pinned


def write_table(
    snapshot_dir: Path,
    table: str,
    rows: list[dict],
    fieldnames: list[str],
    *,
    source: str,
    source_version: str,
    retrieved_at: str,
    coverage: str = "observed",
    note: str = "",
    contributors: list[dict] | None = None,
    column_sources: dict[str, dict] | None = None,
) -> dict:
    """Write normalized/<table>.csv deterministically and stamp it in the manifest.
    `column_sources` maps columns whose values come from another source to that
    source's stamp (the loader stamps those values with it).
    Coverage says what the table can claim: `unobserved` means the data could not
    be obtained, never that it is zero."""
    if coverage not in COVERAGE_LEVELS:
        raise ValueError(f"coverage must be one of {COVERAGE_LEVELS}")
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n", extrasaction="raise")
    w.writeheader()
    for r in rows:
        w.writerow({k: "" if r.get(k) is None else r.get(k) for k in fieldnames})
    path = snapshot_dir / "normalized" / f"{table}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(buf.getvalue().encode("utf-8"))
    tmp.replace(path)
    entry = {
        "source": source,
        "source_version": source_version,
        "retrieved_at": retrieved_at,
        "path": f"normalized/{table}.csv",
        "sha256": sha256_of(path),
        "rows": len(rows),
        "coverage": coverage,
        "note": note,
    }
    if contributors:
        entry["contributors"] = contributors
    if column_sources:
        unknown = sorted(set(column_sources) - set(fieldnames))
        if unknown:
            raise ValueError(f"column_sources names columns not in {table}: {unknown}")
        entry["column_sources"] = column_sources
    update_manifest(snapshot_dir, tables={table: entry})
    return entry


def region_spec(name: str = "spp") -> dict:
    """The committed live-region spec (config/regions/<name>.yaml)."""
    import yaml

    path = Path(__file__).resolve().parents[4] / "config" / "regions" / f"{name}.yaml"
    return yaml.safe_load(path.read_text())
