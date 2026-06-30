"""Phase 1 (Bucket A): ingest -> triangulate -> invariants -> confidence, emitting
enveloped `Line`s and a `quality_report`. Done when every record carries
source/version/confidence and the report summarizes flag counts by field."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from headroom.ingest.load import IngestBundle
from headroom.provenance.envelope import Fact
from headroom.provenance.lineage import LineageStore
from headroom.quality import confidence as conf
from headroom.quality import invariants as inv
from headroom.quality.triangulate import triangulate_voltage
from headroom.schema.entities import Bus, Line


@dataclass
class BucketAResult:
    buses: list[Bus]
    lines: list[Line]
    violations: list[inv.Violation]
    quality_report: dict = field(default_factory=dict)

    @property
    def lines_by_id(self) -> dict[str, Line]:
        return {ln.line_id: ln for ln in self.lines}


def run_bucket_a(bundle: IngestBundle, store: LineageStore) -> BucketAResult:
    lines: list[Line] = []
    violations: list[inv.Violation] = []
    all_facts: list[tuple[str, Fact]] = []  # (field, fact) for the report

    for line_id, skel in bundle.line_skeleton.items():
        obs = bundle.voltage_obs[line_id]
        conductor = bundle.conductor_obs.get(line_id)
        thermal = bundle.thermal_obs.get(line_id)
        cost = bundle.cost_obs.get(line_id)

        # 5.1 triangulate voltage across providers
        voltage = triangulate_voltage(line_id, obs, store)

        # 5.2 invariants (flag, never drop)
        violations += inv.check_voltage_in_set(line_id, obs)
        if float(voltage.value) not in inv.NOMINAL_VOLTAGES:
            if "voltage_implausible" not in voltage.flags:
                voltage.flags.append("voltage_implausible")
        violations += inv.check_conductor_matches_voltage(line_id, voltage, conductor)

        # 5.3 confidence: priors + downgrade on flags
        conf.apply_prior("voltage_kv", voltage)
        conf.apply_prior("conductor", conductor)
        conf.apply_prior("thermal_limit_mw", thermal)
        conf.apply_prior("cost_usd", cost)

        lines.append(
            Line(
                line_id=line_id,
                from_bus=skel["from_bus"],
                to_bus=skel["to_bus"],
                voltage_kv=voltage,
                length_mi=skel["length_mi"],
                conductor=conductor,
                thermal_limit_mw=thermal,
                cost_usd=cost,
            )
        )
        all_facts.append(("voltage_kv", voltage))
        for fld, f in (("conductor", conductor), ("thermal_limit_mw", thermal),
                       ("cost_usd", cost)):
            if f is not None:
                all_facts.append((fld, f))
        for o in obs:
            all_facts.append(("voltage_kv_raw", o))

    quality_report = _build_report(bundle, lines, all_facts, violations)
    return BucketAResult(
        buses=bundle.buses,
        lines=lines,
        violations=violations,
        quality_report=quality_report,
    )


def _build_report(
    bundle: IngestBundle,
    lines: list[Line],
    all_facts: list[tuple[str, Fact]],
    violations: list[inv.Violation],
) -> dict:
    flags_by_field: dict[str, Counter] = {}
    confidence_by_field: dict[str, Counter] = {}
    for fld, fact in all_facts:
        for fl in fact.flags:
            flags_by_field.setdefault(fld, Counter())[fl] += 1
        confidence_by_field.setdefault(fld, Counter())[fact.confidence] += 1

    return {
        "region": bundle.region.name,
        "rto": bundle.region.rto,
        "synthetic": bundle.region.synthetic,
        "n_buses": len(bundle.buses),
        "n_lines": len(lines),
        "flags_by_field": {k: dict(v) for k, v in flags_by_field.items()},
        "confidence_by_field": {k: dict(v) for k, v in confidence_by_field.items()},
        "n_violations": len(violations),
        "violations": [
            {"invariant": v.invariant, "line_id": v.line_id, "field": v.field,
             "detail": v.detail}
            for v in violations
        ],
        # Non-filer footprint is unobserved, not zero (blind-spot rule).
        "coverage_note": "Non-filer (muni/co-op) assets are coverage=unobserved.",
    }
