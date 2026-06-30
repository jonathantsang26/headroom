"""6.2 Physics proxy — an approximate DC / PTDF screen from public topology.

This is the SCREEN, not a power-flow study: it flags which segments carry flow
under load patterns and which flowgates load up under their N-1 contingency. It
does NOT replicate AC contingency analysis and every output says so.

Correctness points (the doc flags this module as plan-first):
  * The nodal susceptance matrix B' is singular (one zero eigenvalue, the angle
    reference). We remove the slack row/column before solving / inverting.
  * The injection vector must sum to ~0 or the DC solve is inconsistent; we balance
    it explicitly.
  * N-1 is done honestly: drop the contingency line from the network and re-solve.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from headroom.provenance.lineage import LineageStore
from headroom.reconstruct.ranges import band
from headroom.schema.entities import Bus, Constraint, Line

MVA_BASE = 100.0
_X_OHM_PER_MILE = 0.8  # typical overhead-line series reactance


def estimate_reactance_pu(length_mi: float, voltage_kv: float) -> float:
    """Per-unit series reactance on a 100 MVA base. Higher voltage -> lower pu
    reactance (stiffer line), via Z_base = kV^2 / MVA_base. An ESTIMATE — emitted
    as a flagged Range, never as a hard Fact."""
    z_base = (voltage_kv**2) / MVA_BASE
    return (_X_OHM_PER_MILE * length_mi) / z_base


def attach_reactance(lines: list[Line], store: LineageStore) -> None:
    """Estimate each line's impedance and attach it as a bounded (imputed) Range."""
    for ln in lines:
        x = estimate_reactance_pu(ln.length_mi, float(ln.voltage_kv.value))
        ln.reactance_pu = store.add(
            band(
                x,
                lo_frac=0.7,
                hi_frac=1.3,
                basis="Impedance estimated from length+voltage (0.8 ohm/mi); imputed.",
                provider="hifld_transmission",
                lineage_id=f"reactance:{ln.line_id}",
            )
        )


@dataclass
class DCModel:
    bus_ids: list[str]
    bus_index: dict[str, int]
    line_ids: list[str]
    line_index: dict[str, int]
    incidence: np.ndarray  # (L x N): +1 at from, -1 at to
    b: np.ndarray  # (L,) branch susceptance = 1/x
    slack: int

    @property
    def n_bus(self) -> int:
        return len(self.bus_ids)

    @property
    def n_line(self) -> int:
        return len(self.line_ids)


def build_dc_model(
    buses: list[Bus], lines: list[Line], *, slack_bus: str | None = None
) -> DCModel:
    bus_ids = [b.bus_id for b in buses]
    bus_index = {bid: i for i, bid in enumerate(bus_ids)}
    n = len(bus_ids)

    incidence = np.zeros((len(lines), n))
    b = np.zeros(len(lines))
    line_ids: list[str] = []
    for l, ln in enumerate(lines):
        fi, ti = bus_index[ln.from_bus], bus_index[ln.to_bus]
        x = float(ln.reactance_pu.expected) if ln.reactance_pu else estimate_reactance_pu(
            ln.length_mi, float(ln.voltage_kv.value)
        )
        incidence[l, fi] = 1.0
        incidence[l, ti] = -1.0
        b[l] = 1.0 / x
        line_ids.append(ln.line_id)

    slack = bus_index[slack_bus] if slack_bus else 0
    return DCModel(
        bus_ids=bus_ids,
        bus_index=bus_index,
        line_ids=line_ids,
        line_index={lid: i for i, lid in enumerate(line_ids)},
        incidence=incidence,
        b=b,
        slack=slack,
    )


def _keep_indices(n: int, slack: int) -> list[int]:
    return [i for i in range(n) if i != slack]


def solve_flows(
    model: DCModel, injection: np.ndarray, *, drop_lines: tuple[str, ...] = ()
) -> np.ndarray:
    """DC line flows (MW) for a balanced `injection` vector. `drop_lines` are taken
    out of service (N-1). Raises numpy.linalg.LinAlgError if the (post-contingency)
    network is disconnected — caller flags and skips."""
    n, L = model.n_bus, model.n_line
    bd = model.b.copy()
    for lid in drop_lines:
        bd[model.line_index[lid]] = 0.0

    a = model.incidence
    b_bus = a.T @ (bd[:, None] * a)  # A^T diag(bd) A
    keep = _keep_indices(n, model.slack)
    b_red = b_bus[np.ix_(keep, keep)]
    theta_red = np.linalg.solve(b_red, injection[keep])
    theta = np.zeros(n)
    theta[keep] = theta_red
    return bd * (a @ theta)


def ptdf_matrix(model: DCModel) -> np.ndarray:
    """Power Transfer Distribution Factors (L x N), withdrawal at the slack. Column
    `slack` is exactly zero (an injection at the reference moves no flow)."""
    n = model.n_bus
    a = model.incidence
    b_bus = a.T @ (model.b[:, None] * a)
    keep = _keep_indices(n, model.slack)
    x_inv = np.zeros((n, n))
    x_inv[np.ix_(keep, keep)] = np.linalg.inv(b_bus[np.ix_(keep, keep)])
    return model.b[:, None] * (a @ x_inv)


def build_injections(
    buses: list[Bus], load_rows: list[dict]
) -> dict[str, np.ndarray]:
    """Per-snapshot balanced injection vectors from EIA-930-style load. Load is
    distributed across load buses by weight; an equal total of generation across gen
    buses by weight; the result is mean-corrected so it sums to exactly ~0."""
    bus_index = {b.bus_id: i for i, b in enumerate(buses)}
    n = len(buses)
    gen_buses = [b for b in buses if b.role == "gen"]
    load_buses = [b for b in buses if b.role == "load"]
    gen_wsum = sum(b.weight for b in gen_buses) or 1.0
    load_wsum = sum(b.weight for b in load_buses) or 1.0

    snapshots: dict[str, float] = {}
    for r in load_rows:
        snapshots[r["snapshot"]] = snapshots.get(r["snapshot"], 0.0) + float(
            r["load_mw"]
        )

    out: dict[str, np.ndarray] = {}
    for snap, total_load in snapshots.items():
        inj = np.zeros(n)
        for b in load_buses:
            inj[bus_index[b.bus_id]] -= total_load * b.weight / load_wsum
        for b in gen_buses:
            inj[bus_index[b.bus_id]] += total_load * b.weight / gen_wsum
        inj -= inj.mean()  # enforce exact zero-sum (advisor: DC solve consistency)
        out[snap] = inj
    return out


def _loadings_over_snapshots(
    model: DCModel,
    line_idx: int,
    thermal: float,
    injections: dict[str, np.ndarray],
    drop: tuple[str, ...],
) -> list[float]:
    """|flow|/thermal on `line_idx` across snapshots; snapshots whose contingency
    disconnects the grid contribute nothing (caught by the caller)."""
    out: list[float] = []
    for inj in injections.values():
        try:
            flows = solve_flows(model, inj, drop_lines=drop)
        except np.linalg.LinAlgError:
            continue
        out.append(abs(flows[line_idx]) / thermal)
    return out


def screen_loadings(
    model: DCModel,
    lines_by_id: dict[str, Line],
    constraints: list[Constraint],
    injections: dict[str, np.ndarray],
    store: LineageStore,
) -> tuple[dict[str, "object"], dict[str, str]]:
    """For EVERY flowgate, emit a loading Range (lo=min, expected=mean, hi=worst) and
    a `screen_status`. A contingency that disconnects the grid is INFORMATION, not a
    reason to drop the constraint (flag, never drop): we fall back to the base-case
    (N-0) loading, widen the band, and mark `screen_status="n1_disconnect"`."""
    from headroom.provenance.envelope import Range  # keep module top light

    loading: dict[str, Range] = {}
    status: dict[str, str] = {}

    for c in constraints:
        cid = c.constraint_id
        mon_idx = model.line_index[c.monitored_line]
        thermal_fact = lines_by_id[c.monitored_line].thermal_limit_mw
        if thermal_fact is None or not thermal_fact.value:
            status[cid] = "no_thermal_limit"
            loading[cid] = store.add(
                _loading_range(cid, 0.0, 1.0, 2.0, store_basis="indeterminate: no "
                "thermal limit on monitored line; treated as uncertain."))
            continue
        thermal = float(thermal_fact.value)

        base = _loadings_over_snapshots(model, mon_idx, thermal, injections, ())
        if c.contingency_line:
            n1 = _loadings_over_snapshots(
                model, mon_idx, thermal, injections, (c.contingency_line,)
            )
        else:
            n1 = base

        if c.contingency_line and not n1:
            # contingency islands part of the grid: base-case loading, widened, flagged
            status[cid] = "n1_disconnect"
            loading[cid] = store.add(
                _loading_range(
                    cid,
                    min(base) * 0.8,
                    float(np.mean(base)),
                    max(base) * 1.5,
                    store_basis="N-1 contingency disconnects the grid; base-case "
                    "loading with a widened band — screen indeterminate.",
                )
            )
        else:
            use = n1 if c.contingency_line else base
            status[cid] = "ok"
            loading[cid] = store.add(
                _loading_range(
                    cid,
                    min(use),
                    float(np.mean(use)),
                    max(use),
                    store_basis="DC N-1 screen: monitored-line loading over load "
                    "snapshots (screen only, not an AC study).",
                )
            )
    return loading, status


def _loading_range(cid: str, lo: float, exp: float, hi: float, *, store_basis: str):
    """Build a loading Range directly from min/mean/max (no point-and-band detour)."""
    from headroom.provenance.synthetic import synthetic_range

    return synthetic_range(
        lo=lo,
        expected=exp,
        hi=hi,
        basis=store_basis,
        provider="hifld_transmission",
        lineage_id=f"loading:{cid}",
    )
