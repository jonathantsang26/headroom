"""5.2 Invariants — domain priors catch what statistical outlier detection misses."""

from __future__ import annotations

from dataclasses import dataclass

from headroom.provenance.envelope import Fact

# Discrete domain of nominal transmission voltages (kV).
NOMINAL_VOLTAGES = {69, 115, 138, 161, 230, 345, 500, 765}


@dataclass(frozen=True)
class Violation:
    invariant: str
    line_id: str
    field: str
    detail: str


def _flag(fact: Fact | None, flag: str) -> None:
    if fact is not None and flag not in fact.flags:
        fact.flags.append(flag)


def check_voltage_in_set(line_id: str, voltage_obs: list[Fact]) -> list[Violation]:
    """Flag any raw voltage reading that is not a nominal voltage."""
    violations: list[Violation] = []
    for obs in voltage_obs:
        if float(obs.value) not in NOMINAL_VOLTAGES:
            _flag(obs, "voltage_implausible")
            violations.append(
                Violation(
                    "voltage_in_set",
                    line_id,
                    "voltage_kv",
                    f"{obs.provider} reports {obs.value} kV (not nominal)",
                )
            )
    return violations


def check_conductor_matches_voltage(
    line_id: str, voltage_fact: Fact, conductor_fact: Fact | None
) -> list[Violation]:
    """A 500 kV+ line on a 4/0 distribution conductor is implausible."""
    if conductor_fact is None:
        return []
    if float(voltage_fact.value) >= 500 and str(conductor_fact.value).strip() == "4/0":
        _flag(conductor_fact, "conductor_voltage_mismatch")
        _flag(voltage_fact, "conductor_voltage_mismatch")
        return [
            Violation(
                "conductor_matches_voltage",
                line_id,
                "conductor",
                f"{voltage_fact.value} kV with conductor {conductor_fact.value!r}",
            )
        ]
    return []
