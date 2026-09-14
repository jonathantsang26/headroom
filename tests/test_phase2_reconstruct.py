"""Phase 2: DC/PTDF screen + congestion rent + planning exhaust."""

import numpy as np
import pytest

from headroom.ingest import ingest_region
from headroom.provenance.lineage import LineageStore
from headroom.quality import run_bucket_a
from headroom.reconstruct import run_bucket_b
from headroom.reconstruct.network_model import DCModel, ptdf_matrix, solve_flows


# ---- PTDF math (hand-computable 3-bus triangle, equal reactances) ----

def _triangle():
    inc = np.array([[1.0, -1.0, 0.0], [0.0, 1.0, -1.0], [1.0, 0.0, -1.0]])
    return DCModel(
        ["A", "B", "C"], {"A": 0, "B": 1, "C": 2},
        ["AB", "BC", "AC"], {"AB": 0, "BC": 1, "AC": 2},
        inc, np.array([1.0, 1.0, 1.0]), slack=0,
    )


def test_ptdf_slack_column_is_zero():
    P = ptdf_matrix(_triangle())
    assert np.allclose(P[:, 0], 0.0)  # injection at slack moves no flow


def test_ptdf_parallel_path_split():
    P = ptdf_matrix(_triangle())
    # injection at C (withdraw at slack A): direct A-C gets 2/3, A-B-C gets 1/3
    assert abs(abs(P[2, 2]) - 2 / 3) < 1e-9
    assert abs(abs(P[0, 2]) - 1 / 3) < 1e-9
    assert abs(abs(P[1, 2]) - 1 / 3) < 1e-9


def test_n1_reroutes_all_flow():
    m = _triangle()
    inj = np.array([-1.0, 0.0, 1.0])  # balanced
    flows = solve_flows(m, inj, drop_lines=("AC",))
    assert abs(abs(flows[0]) - 1.0) < 1e-9  # A-B carries all
    assert abs(abs(flows[1]) - 1.0) < 1e-9  # B-C carries all
    assert abs(flows[2]) < 1e-12  # A-C out of service


def test_disconnecting_contingency_raises():
    # A radial spur D off A; dropping it islands D.
    inc = np.array([[1.0, -1.0, 0.0, 0.0], [1.0, 0.0, 0.0, -1.0]])  # A-B, A-D
    m = DCModel(["A", "B", "C", "D"], {"A": 0, "B": 1, "C": 2, "D": 3},
                ["AB", "AD"], {"AB": 0, "AD": 1}, inc, np.array([1.0, 1.0]), slack=0)
    inj = np.array([1.0, 0.0, 0.0, -1.0])
    with pytest.raises(np.linalg.LinAlgError):
        solve_flows(m, inj, drop_lines=("AD",))


# ---- integration on SPP-SYNTH ----

def _bucket_b():
    store = LineageStore()
    b = ingest_region("spp-synth", store)
    a = run_bucket_a(b, store)
    return store, run_bucket_b(b, a, store)


def test_every_constraint_has_loading_and_status():
    _, bb = _bucket_b()
    assert len(bb.loading) == len(bb.constraints) == 10  # flag, never drop
    for c in bb.constraints:
        assert c.constraint_id in bb.loading
        assert c.constraint_id in bb.screen_status


def test_disconnect_case_is_flagged_not_dropped():
    _, bb = _bucket_b()
    # FLOWGATE_10's contingency LINE_7 islands the radial BUS_7
    assert bb.screen_status["FLOWGATE_10"] == "n1_disconnect"
    assert "FLOWGATE_10" in bb.loading  # still present


def test_loadings_are_physically_plausible():
    _, bb = _bucket_b()
    for rng in bb.loading.values():
        assert 0.0 <= rng.lo <= rng.expected <= rng.hi
        assert rng.hi < 3.0  # no absurd radial-gen 5x overloads


def test_empty_injections_does_not_crash_and_flags_every_constraint():
    # No load snapshots -> no flows.
    from headroom.reconstruct.network_model import (
        attach_reactance,
        build_dc_model,
        screen_loadings,
    )
    from headroom.schema.entities import Constraint

    store = LineageStore()
    bundle = ingest_region("spp-synth", store)
    a = run_bucket_a(bundle, store)
    attach_reactance(a.lines, store)  # so the DC model can build
    model = build_dc_model(a.buses, a.lines)
    constraints = [
        Constraint(
            constraint_id=r["constraint_id"], monitored_line=r["monitored_line"],
            contingency_line=r["contingency_line"] or None, ctype=r["ctype"],
        )
        for r in bundle.constraints
    ]
    loading, status = screen_loadings(model, a.lines_by_id, constraints, {}, store)
    assert len(loading) == len(constraints)  # every constraint still gets a Range
    assert all(status[c.constraint_id] == "no_base_flows" for c in constraints)


def test_congestion_rent_annualized_and_keyed():
    _, bb = _bucket_b()
    # FLOWGATE_3: 3.6 $M/mo -> 43.2 $M/yr expected
    assert abs(bb.congestion["FLOWGATE_3"].rent_musd.expected - 43.2) < 1e-6


def test_planning_items_only_for_named_constraints():
    _, bb = _bucket_b()
    assert set(bb.planning) == {"FLOWGATE_1", "FLOWGATE_3", "FLOWGATE_5", "FLOWGATE_8"}


def test_reactance_attached_as_range():
    store, bb = _bucket_b()
    # reactance was estimated + attached as a bounded Range
    assert store.get("reactance:LINE_1") is not None
