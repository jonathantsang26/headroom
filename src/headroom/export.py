"""Phase 4 delivery — the ranked export is the real shareable deliverable."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from headroom.model.ev import priors_sha256
from headroom.model.get_match import _same_station_ends, get_label
from headroom.pipeline import PipelineResult
from headroom.provenance.digests import ranking_digest
from headroom.provenance.gate import PublishabilityError, assert_publishable
from headroom.provenance.versions import build_run_manifest

# Deterministic stamp (no wall-clock in the reproducible path).
DEFAULT_GENERATED_AT = datetime(2026, 6, 30, tzinfo=timezone.utc)

_SIGNAL_ORDER = (
    "rent", "loading", "persistence", "demand_pressure", "planning",
    "cost_of_inaction",
)


@dataclass
class Delivery:
    mode: str  # "shareable" | "internal" (real data, gate refused) | "internal-synthetic"
    public_only: bool
    blocked: list[str]  # constraint_ids whose lineage is non-publishable
    blocked_tables: list[str] = field(default_factory=list)  # non-publishable input tables
    # any other leaf source in the store that fails the gate, and unresolved input ids
    blocked_sources: list[str] = field(default_factory=list)
    paths: dict[str, str] = field(default_factory=dict)
    manifest: dict = field(default_factory=dict)


def scoring_to_manifest(scoring) -> dict:
    out = {
        "seed": scoring.seed,
        "draws": scoring.draws,
        "top_fraction": scoring.top_fraction,
        "weights": scoring.weights,
        "signal_order": list(scoring.weights),
        "references": scoring.references,
    }
    # Recorded only when set, so composite-ranked manifests keep their earlier shape.
    if scoring.ranking != "composite":
        out["ranking"] = scoring.ranking
    if scoring.get_priors is not None:
        out["get_priors"] = scoring.get_priors
        out["get_priors_sha256"] = priors_sha256(scoring.get_priors)
    if scoring.reference_rule is not None:
        out["reference_rule"] = scoring.reference_rule
    if scoring.definitions:
        out["definitions"] = scoring.definitions
    if scoring.forward_rent is not None:
        out["forward_rent"] = scoring.forward_rent
    return out


def build_score_rows(result: PipelineResult) -> list[dict]:
    """One flat row per flowgate, ranked, with signal expecteds + provenance."""
    rows = []
    meta = {c.constraint_id: c for c in result.bucket_b.constraints}
    option_value = (result.model.extras.get("ev") or {}).get("option_value", {})
    get_sets = result.model.extras["get_set"]
    for rank, s in enumerate(result.model.scores, start=1):
        row = {
            "rank": rank,
            "constraint_id": s.constraint_id,
            "physical_corridor": s.physical_corridor,
            "p_top_k": round(s.p_top_k, 4),
            "composite_lo": round(s.composite.lo, 4),
            "composite_expected": round(s.composite.expected, 4),
            "composite_hi": round(s.composite.hi, 4),
            "recommended_get": s.recommended_get,
            **_get_set_columns(get_sets[s.constraint_id]),
            "screen_status": result.bucket_b.screen_status.get(s.constraint_id, ""),
            "monitored_facility": meta[s.constraint_id].monitored_facility or "",
            "contingency_facility": meta[s.constraint_id].contingency_facility or "",
            "get_basis": s.get_basis,
            "provenance": s.provenance,
        }
        for name in _SIGNAL_ORDER:
            row[f"sig_{name}"] = round(s.signals[name].expected, 4)
        for name in _SIGNAL_ORDER:
            row[f"cov_{name}"] = s.signal_coverage.get(name, "observed")
        co = result.bucket_b.congestion.get(s.constraint_id)
        # a missing number is null (blank in the CSV), never "": a mixed column would
        # make the Parquet and JSON readers type it as text
        rnd = lambda v, n=4: round(v, n) if v is not None else None
        # observed quantities, in their own units (what a planner reads first)
        row.update({
            "rent_musd": rnd(co.rent_musd.expected, 6) if co and co.rent_musd else None,
            "value_of_1mw_relief_usd": rnd(co.shadow_price_sum, 2) if co else None,
            "hours_binding": rnd(co.hours_binding.expected, 1) if co else None,
            "breached_hours": rnd(co.breached_hours, 1) if co else None,
            "months_binding": co.months_binding if co else None,
        })
        row.update({
            "ev_mean_musd": rnd(s.ev_mean, 6),
            "ev_lo_musd": rnd(s.ev.lo, 6) if s.ev else None,
            "ev_expected_musd": rnd(s.ev.expected, 6) if s.ev else None,
            "ev_hi_musd": rnd(s.ev.hi, 6) if s.ev else None,
            "p_top_k_composite": rnd(s.p_top_k_composite),
            # recommended_get is the evidence rule's GET (model/get_match.py) under every
            # ranking: which GET physically fits the element, before cost.
            # ev_recommended_get is the EV layer's pick: which GET pays after the cost
            # hurdle — blank without observed rent, "No cost-effective GET" below it.
            "ev_recommended_get": s.ev_get,
            "evidence_rule_get": s.get_rule,
            "caution": ("same_station_ends" if _same_station_ends(
                meta[s.constraint_id].monitored_facility) else ""),
            "get_evidence": s.get_evidence,
            "get_candidates": ";".join(s.get_candidates),
        })
        for option in _GET_OPTIONS:
            p = s.get_probabilities.get(option)
            row[f"p_best_{_slug(option)}"] = round(p, 4) if p is not None else None
        # the decision split: each option's part of ev_mean (they sum to it); blank, not
        # 0, where EV is unobserved
        split = option_value.get(s.constraint_id, {})
        for option in _GET_OPTIONS[1:]:
            row[f"ev_from_{_slug(option)}_musd"] = rnd(split.get(option), 6)
        rows.append(row)
    return rows


def _get_set_columns(gets: list[str]) -> dict:
    """The recommendation to show beside recommended_get: where the evidence leaves
    several GETs open, recommended_get is the rule's tie-break among them."""
    return {"get_shown": get_label(gets), "get_resolved": len(gets) == 1,
            "get_set": ";".join(gets)}


def build_corridor_rows(result: PipelineResult) -> list[dict]:
    """The shortlist's corridor rows, each with its representative flowgate's GET
    set after recommended_get. The model's own rows, which the ranking digest
    covers, are left as they are."""
    get_sets = result.model.extras["get_set"]
    rows = []
    for c in result.model.shortlist:
        row = {}
        for key, value in c.items():
            row[key] = value
            if key == "recommended_get":
                row.update(_get_set_columns(get_sets[c["representative_flowgate"]]))
        rows.append(row)
    return rows


_GET_OPTIONS = ("No cost-effective GET", "Dynamic Line Rating", "Advanced Power Flow Control",
                "Topology Optimization", "Combination")


def _slug(name: str) -> str:
    return "none" if name.startswith("No ") else "".join(w[0] for w in name.split()).lower()


def _gate_blocked(result: PipelineResult) -> list[str]:
    """Scores whose exported values (composite, and EV when present) fail the gate."""
    blocked = []
    for s in result.model.scores:
        try:
            for env in (s.composite, s.ev):
                if env is not None:
                    assert_publishable(env.lineage_id, result.store, result.registry,
                                       public_only=True)
        except PublishabilityError:
            blocked.append(s.constraint_id)
    return blocked


def _tables_blocked(result: PipelineResult) -> list[str]:
    """Input tables whose stamps fail the gate. The quality report summarizes every
    input table, so a shareable run needs every table publishable, not only the
    tables its scores happen to depend on."""
    blocked = []
    for node in result.store._nodes.values():
        if node.lineage_id.startswith("table:") and not node.inputs:
            try:
                assert_publishable(node.lineage_id, result.store, result.registry,
                                   public_only=True)
            except PublishabilityError:
                blocked.append(node.lineage_id.removeprefix("table:"))
    return sorted(blocked)


def _store_blocked(result: PipelineResult) -> list[str]:
    """The whole store, not only what scores and tables reach: every leaf's source
    must pass the gate and every cited input must resolve, so a value minted outside
    the score and table paths cannot reach a shareable artifact unchecked."""
    by_source: dict[str, str] = {}
    for leaf in result.store.leaves():
        by_source.setdefault(leaf.source, leaf.lineage_id)
    blocked = []
    for source, lineage_id in sorted(by_source.items()):
        try:
            assert_publishable(lineage_id, result.store, result.registry, public_only=True)
        except PublishabilityError:
            blocked.append(source)
    # A derived node stamped with a registered source (rather than its stage) would hide
    # that stamp from the taint walk, which follows inputs: check those stamps too.
    for node in result.store._nodes.values():
        spec = result.registry.get(node.source) if node.inputs else None
        if spec is not None and not spec.publishable and node.source not in blocked:
            blocked.append(node.source)
    dangling = result.store.dangling_inputs()
    if dangling:
        blocked.append(f"unresolved inputs ({len(dangling)}, e.g. {sorted(dangling)[0]})")
    return blocked


def _atomic_write(path: Path, data: bytes) -> str:
    """Write beside the destination and rename, so a reader never sees a partial
    artifact; returns the bytes' sha256 for the run manifest."""
    tmp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return hashlib.sha256(data).hexdigest()


def _json_bytes(obj) -> bytes:
    return json.dumps(obj, indent=2).encode("utf-8")


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(value):
    """Upstream text (constraint and facility names) is written so a spreadsheet
    shows it rather than evaluating it as a formula."""
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value


def _csv_bytes(rows: list[dict]) -> bytes:
    if not rows:
        return b""
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows({k: _cell(v) for k, v in r.items()} for r in rows)
    return buf.getvalue().encode("utf-8")


def _materialize_parquet(json_path: Path, parquet_path: Path) -> str | None:
    """Write a queryable Parquet of the ranked rows via DuckDB (Decision 2: keep
    outputs cleanly queryable so an MCP server is a 1-day add). Best-effort;
    returns the file's sha256, or None when it could not be written."""
    try:
        import duckdb
    except ImportError:  # pragma: no cover
        return None
    tmp = parquet_path.with_name(f".{parquet_path.name}.{uuid4().hex}.tmp")
    jp = str(json_path).replace("'", "''")
    pp = str(tmp).replace("'", "''")
    try:
        with duckdb.connect() as con:
            con.execute(
                f"COPY (SELECT * FROM read_json_auto('{jp}')) "
                f"TO '{pp}' (FORMAT PARQUET)"
            )
        digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
        os.replace(tmp, parquet_path)
        return digest
    except Exception:  # pragma: no cover - depends on filesystem/DuckDB state
        return None
    finally:
        tmp.unlink(missing_ok=True)


def build_notice(result: PipelineResult, mode: str) -> str:
    """NOTICE.md beside every export: what the files are, the terms of every source
    the run used, and the caveats a reader needs before using the numbers."""
    qr = result.quality_report
    ident = result.input_identity
    snap = ident.get("snapshot_id") or result.region.name
    lines = [
        f"# Headroom export: {result.region.name} ({result.region.rto})",
        "",
        f"- Mode: **{mode}**" + ("" if mode == "shareable" else
                                 " (not for sharing: the publishable gate refused it)"),
        f"- Input snapshot: `{snap}`"
        + (f" (manifest sha256 `{ident['snapshot_manifest_sha256'][:16]}…`)"
           if ident.get("snapshot_manifest_sha256") else ""),
        f"- Ranking: {result.scoring.ranking}; {result.model.n_constraints} flowgates, "
        f"{len(result.model.shortlist)} corridors, k = {result.model.k}",
        "",
        "`shortlist.csv` and `scores.json` hold one row per flowgate in rank order; "
        "`shortlist.json` holds one row per corridor. `run_manifest.json` records the "
        "inputs, code, scoring and a sha256 of every file here.",
    ]
    if result.region.synthetic:
        lines += ["", "**Synthetic fixtures. Not a real result; never share.**"]
    attribution = qr.get("attribution") or []
    if attribution:
        lines += ["", "## Sources and terms", "",
                  "Cite each source below. Using these files is subject to every "
                  "source's terms; where a source restricts commercial use, so do "
                  "these files.", ""]
        for a in attribution:
            lines.append(f"- **{a['source']}** `{a['source_version']}` "
                         f"({', '.join(a.get('tables', []))}): {a['basis']}")
    consulted = qr.get("consulted_nothing_obtained") or []
    if consulted:
        lines += ["", "Consulted, nothing obtained (their tables are unobserved): "
                  + ", ".join(f"{a['source']} `{a['source_version']}`" for a in consulted)
                  + "."]
    ev = qr.get("ev")
    caveats = list(qr.get("gaps") or [])
    if ev:
        caveats.insert(0, f"ev: priors {ev['priors_version']} (sha256 "
                          f"{ev['priors_sha256'][:12]}). {ev['note']}")
    if caveats:
        lines += ["", "## Caveats", ""] + [f"- {c}" for c in caveats]
    lines += ["", "A screening shortlist from public data, not an engineering study or "
              "an investment recommendation. Full notes: `quality_report.json`."]
    return "\n".join(lines) + "\n"


def _inputs_retrieved_at(result: PipelineResult) -> datetime:
    """The latest retrieval time among the input tables' stamps: a deterministic
    timestamp for the manifest (fixtures keep the fixed default)."""
    times = [n.retrieved_at for n in result.store.leaves()
             if n.lineage_id.startswith("table:") and getattr(n, "retrieved_at", None)]
    if result.region.synthetic or not times:
        return DEFAULT_GENERATED_AT
    return max(times)


def _refuse_other_run(out: Path, manifest: dict) -> None:
    """An output directory holds one run: writing a different run's artifacts over
    it (other inputs, ranking or export decision), or over artifacts no manifest
    describes, needs overwrite=True."""
    manifests = sorted(out.glob("run_manifest*.json"))
    for existing in manifests:
        try:
            old = json.loads(existing.read_text())
        except (OSError, ValueError):
            old = {}
        if any(old.get(k) != manifest.get(k) for k in ("inputs", "output_sha256", "mode")):
            raise FileExistsError(
                f"{out} already holds a different run ({existing.name}); write to a new "
                "directory or pass --overwrite")
    if not manifests:
        orphans = [p.name for p in _artifact_files(out)]
        if orphans:
            raise FileExistsError(
                f"{out} holds run artifacts with no manifest ({', '.join(orphans[:3])}); "
                "write to a new directory or pass --overwrite")


_ARTIFACTS = ("scores", "shortlist", "quality_report", "NOTICE", "run_manifest")
_SUFFIXES = ("", ".INTERNAL", ".INTERNAL.synthetic")


def _artifact_files(out: Path) -> list[Path]:
    """Every file in `out` named like an export artifact, under any mode's suffix."""
    names = {f"{stem}{suffix}{ext}" for stem in _ARTIFACTS for suffix in _SUFFIXES
             for ext in (".json", ".csv", ".parquet", ".md")}
    return sorted(p for p in out.iterdir() if p.name in names and p.is_file())


def _export_mode(result: PipelineResult, refused: bool) -> tuple[str, bool, str]:
    """(mode, public_only, file suffix). A refused run on real data is internal, not
    synthetic: the label says why it cannot be shared."""
    if not refused:
        return "shareable", True, ""
    if result.region.synthetic:
        return "internal-synthetic", False, ".INTERNAL.synthetic"
    return "internal", False, ".INTERNAL"


def deliver(
    result: PipelineResult,
    out_dir: str | Path,
    *,
    generated_at: datetime | None = None,
    overwrite: bool = False,
) -> Delivery:
    out = Path(out_dir)
    if generated_at is None:
        generated_at = _inputs_retrieved_at(result)
    out.mkdir(parents=True, exist_ok=True)

    blocked = _gate_blocked(result)
    blocked_tables = _tables_blocked(result)
    blocked_sources = _store_blocked(result)
    # Non-publishable (synthetic, CEII, ...) lineage -> shareable export refused.
    mode, public_only, suffix = _export_mode(
        result, bool(blocked or blocked_tables or blocked_sources))

    rows = build_score_rows(result)
    corridors = build_corridor_rows(result)

    rows_json = out / f"scores{suffix}.json"
    shortlist_json = out / f"shortlist{suffix}.json"
    csv_path = out / f"shortlist{suffix}.csv"  # the forwarded artifact
    quality_path = out / f"quality_report{suffix}.json"
    parquet_path = out / f"scores{suffix}.parquet"
    manifest_path = out / f"run_manifest{suffix}.json"
    notice_path = out / f"NOTICE{suffix}.md"

    qr = result.quality_report
    manifest = build_run_manifest(
        result.registry,
        run_label=f"{result.region.name}-{mode}",
        generated_at=generated_at,
        public_only=public_only,
        scoring=scoring_to_manifest(result.scoring),
        region=result.region.name,
        mode=mode,
        inputs=result.input_identity,
        runtime=result.runtime,
        output_sha256=ranking_digest(result),
    )
    manifest["generated_at_basis"] = (
        "latest retrieval time of the input tables (deterministic, so a replay writes the "
        "same manifest); not the wall-clock time of the run")
    manifest["export_decision"] = {
        "mode": mode, "blocked_scores": len(blocked), "blocked_tables": blocked_tables,
        "blocked_sources": blocked_sources,
    }
    manifest["shortlist_k"] = result.model.k
    manifest["n_constraints"] = result.model.n_constraints
    manifest["coverage"] = {
        "tables": {t: e["coverage"] for t, e in qr.get("tables", {}).items()},
        "unobserved_weight_all": qr.get("unobserved_weight_all", 0.0),
    }
    # Serialize everything before writing anything: a value that cannot be written
    # fails here, not after the artifacts already replaced an earlier run's.
    blobs = {
        "scores_json": _json_bytes(rows),
        "shortlist_json": _json_bytes(corridors),
        "csv": _csv_bytes(rows),
        "quality_report": _json_bytes(qr),
        "notice": build_notice(result, mode).encode("utf-8"),
    }
    if qr.get("attribution") is not None:
        manifest["attribution"] = qr["attribution"]
    _json_bytes(manifest)
    if not overwrite:
        _refuse_other_run(out, manifest)
    names = {"scores_json": rows_json, "shortlist_json": shortlist_json, "csv": csv_path,
             "quality_report": quality_path, "notice": notice_path, "parquet": parquet_path}
    # Write the whole new set into a staging directory first (Parquet included), so
    # nothing of an earlier run is touched until every new file exists; then move the
    # files into place, the manifest last.
    staging = out / f".staging-{uuid4().hex}"
    staging.mkdir()
    try:
        staged = {key: staging / path.name for key, path in names.items()}
        outputs = {key: _atomic_write(staged[key], data) for key, data in blobs.items()}
        parquet_sha = _materialize_parquet(staged["scores_json"], staged["parquet"])
        if parquet_sha is not None:
            outputs["parquet"] = parquet_sha
        # Per-file digests of the artifacts written beside this manifest.
        manifest["outputs"] = {
            key: {"path": names[key].name, "sha256": digest} for key, digest in outputs.items()
        }
        staged_manifest = staging / manifest_path.name
        _atomic_write(staged_manifest, _json_bytes(manifest))
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    # Swap. Old manifests go first, so no manifest ever describes a mixed set; each
    # file then replaces its namesake atomically; the manifest lands last; files of an
    # earlier run that this run does not write (another mode's names) go after.
    try:
        for old in out.glob("run_manifest*.json"):
            old.unlink()
        for key in outputs:
            os.replace(staged[key], names[key])
        os.replace(staged_manifest, manifest_path)
        keep = {names[key].name for key in outputs} | {manifest_path.name}
        for stale in _artifact_files(out):
            if stale.name not in keep:
                stale.unlink()
        staging.rmdir()
    except BaseException as exc:
        raise RuntimeError(
            f"export interrupted while moving files into {out}; the new run's remaining "
            f"files, manifest included, are kept in {staging}") from exc

    paths = {
        "scores_json": str(rows_json),
        "shortlist_json": str(shortlist_json),
        "csv": str(csv_path),
        "manifest": str(manifest_path),
        "quality_report": str(quality_path),
        "notice": str(notice_path),
    }
    if parquet_sha is not None:
        paths["parquet"] = str(parquet_path)

    return Delivery(
        mode=mode, public_only=public_only, blocked=blocked, blocked_tables=blocked_tables,
        blocked_sources=blocked_sources, paths=paths, manifest=manifest,
    )


# Artifacts a replay must reproduce byte for byte (Parquet bytes depend on DuckDB).
REPLAY_COMPARED = ("scores_json", "shortlist_json", "csv", "quality_report", "notice")


def compare_replayed_outputs(recorded: dict, replayed: dict) -> list[str]:
    """Fail unless a replay reached the same export decision and wrote the same
    artifact bytes as the recorded run; returns the artifacts compared."""
    for key in ("mode", "public_only"):
        if recorded.get(key) != replayed.get(key):
            raise ValueError(f"Replay export {key} {replayed.get(key)!r} differs from the "
                             f"recorded {recorded.get(key)!r}")
    rec, new = recorded.get("outputs") or {}, replayed.get("outputs") or {}
    compared = [k for k in REPLAY_COMPARED if k in rec]
    differ = [k for k in compared if rec[k].get("sha256") != new.get(k, {}).get("sha256")]
    if differ:
        raise ValueError(f"Replayed artifacts differ from the recorded run: {differ}")
    return compared


def export_shareable_or_raise(result: PipelineResult, out_dir: str | Path) -> Delivery:
    """Strict path: refuse to write anything unless the whole shortlist is
    publishable. Used where only a shareable artifact is acceptable."""
    blocked = _gate_blocked(result)
    blocked_tables = _tables_blocked(result)
    blocked_sources = _store_blocked(result)
    if blocked or blocked_tables or blocked_sources:
        raise PublishabilityError(
            f"Shareable export refused: {len(blocked)} scores have non-publishable "
            f"lineage (e.g. {blocked[:3]}); non-publishable input tables: "
            f"{blocked_tables}; non-publishable sources in lineage: {blocked_sources}. "
            "This is a synthetic/non-public run."
        )
    return deliver(result, out_dir)
