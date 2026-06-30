"""Phase 0 'done when': a dummy value flows end-to-end carrying source/version/
confidence, the run is pinned to a manifest, and the publishable gate is exercised
both ways (passes for public lineage, blocks for CEII-tainted lineage).

Run:  .venv/bin/python -m headroom.demo
"""

from __future__ import annotations

from datetime import datetime, timezone

from headroom.provenance import (
    Fact,
    LineageStore,
    PublishabilityError,
    Range,
    assert_publishable,
    derive_lineage_id,
)
from headroom.provenance.versions import build_run_manifest
from headroom.sources import load_registry

# Fixed timestamp so the demo is deterministic (no wall-clock in the data path).
_T0 = datetime(2026, 6, 30, 0, 0, 0, tzinfo=timezone.utc)


def run_demo() -> dict:
    registry = load_registry()
    store = LineageStore()

    # 1. A raw Bucket-A Fact: a line voltage from FERC Form 1 (via PUDL), enveloped
    #    with source + pinned version + confidence. Note `as_reported` preserved.
    voltage = store.add(
        Fact(
            value=345.0,
            as_reported="345 kV",
            unit="kV",
            confidence="high",
            source="pudl_ferc_form1",
            source_version=registry["pudl_ferc_form1"].source_version,
            retrieved_at=_T0,
            lineage_id="ferc1:f1_line:resp42:line7",
        )
    )

    # 2. A Bucket-B Range: congestion rent for a constraint, derived from SPP's
    #    public per-constraint monthly congestion-cost summary. Width is information.
    rent = store.add(
        Range(
            lo=0.4,
            expected=1.1,
            hi=2.3,
            dist="triangular",
            basis="SPP monthly DA congestion cost for the binding constraint, $M/yr.",
            source="spp_binding_constraints",
            source_version=registry["spp_binding_constraints"].source_version,
            lineage_id="spp:bc:FLOWGATE_X@CONTINGENCY_Y:2025",
        )
    )

    # 3. A derived composite built from both public inputs (a stand-in score). Its
    #    lineage_id is deterministic from its parents.
    parents = [voltage.lineage_id, rent.lineage_id]
    composite = store.add(
        Range(
            lo=0.4,
            expected=1.1,
            hi=2.3,
            basis="Dummy composite of the public voltage Fact and rent Range.",
            source="model.composite",
            source_version="headroom-0.0.0",
            lineage_id=derive_lineage_id("composite", parents),
            inputs=parents,
        )
    )

    # 4. Export gate: the public composite passes under public_only=True.
    assert_publishable(composite.lineage_id, store, registry, public_only=True)

    # 5. Demonstrate the gate blocks a CEII-tainted derivation. Add a CEII Range and
    #    a composite that depends on it; the lineage walk must refuse to publish it.
    ceii = store.add(
        Range(
            lo=0.0,
            expected=10.0,
            hi=20.0,
            basis="Impedance pulled from a CEII power-flow base case (Form 715).",
            source="ferc_form_715",
            source_version=registry["ferc_form_715"].source_version,
            lineage_id="ceii:form715:branch99",
        )
    )
    tainted_parents = [rent.lineage_id, ceii.lineage_id]
    tainted = store.add(
        Range(
            lo=0.4,
            expected=5.0,
            hi=11.0,
            basis="Composite that (illegitimately) folds in a CEII input.",
            source="model.composite",
            source_version="headroom-0.0.0",
            lineage_id=derive_lineage_id("tainted", tainted_parents),
            inputs=tainted_parents,
        )
    )
    blocked = False
    try:
        assert_publishable(tainted.lineage_id, store, registry, public_only=True)
    except PublishabilityError:
        blocked = True

    # 6. Pin the run.
    manifest = build_run_manifest(
        registry, run_label="phase0-demo", generated_at=_T0, public_only=True
    )

    return {
        "voltage": voltage,
        "rent": rent,
        "composite": composite,
        "public_composite_publishable": True,
        "ceii_composite_blocked": blocked,
        "manifest": manifest,
    }


def main() -> None:
    result = run_demo()
    v = result["voltage"]
    c = result["composite"]
    print("Headroom — Phase 0 end-to-end demo")
    print("-" * 48)
    print(
        f"Fact:  {v.value} {v.unit}  "
        f"(as_reported={v.as_reported!r}, confidence={v.confidence})"
    )
    print(f"       source={v.source}  version={v.source_version}")
    print(f"       lineage_id={v.lineage_id}")
    print(
        f"Composite Range: lo={c.lo} expected={c.expected} hi={c.hi}  "
        f"inputs={c.inputs}"
    )
    print(
        f"Publishable gate — public composite passes: "
        f"{result['public_composite_publishable']}"
    )
    print(
        f"Publishable gate — CEII-tainted composite blocked: "
        f"{result['ceii_composite_blocked']}"
    )
    print(f"Run manifest pins {len(result['manifest']['sources'])} sources.")
    print("OK — a value flowed end-to-end carrying source / version / confidence.")


if __name__ == "__main__":
    main()
