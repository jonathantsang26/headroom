"""SPP adapter (Bucket B: binding constraints, LMP, queue, planning exhaust).

fetch targets (all public on the SPP Portal / OASIS; exact file paths drift —
resolve at run time from the portal listing rather than hardcoding):
  - Monthly congestion-cost-by-constraint summary  -> congestion.csv
  - DA/RT LMP files (only if computing rent ourselves; the monthly summary is
    the shortcut the fixture mimics)
  - Generator interconnection queue export         -> queue.csv
  - ITP assessment + FCA study PDFs                -> planning.csv (parsed)

normalize contracts:
  normalized/constraints.csv: constraint_id, monitored_line, contingency_line, ctype
  normalized/congestion.csv:  constraint_id, month, rent_musd, hours_binding
  normalized/queue.csv:       q_id, near_bus, mw, fuel
  normalized/planning.csv:    constraint_id, plan, assigned_cost_musd
Constraint->monitored_line mapping requires matching SPP constraint names to
canonical line_ids (join-adjacent work; SPP names are semi-structured).
"""

from __future__ import annotations

from pathlib import Path


def fetch(snapshot_dir: Path) -> None:  # pragma: no cover - L2 work
    raise NotImplementedError("SPP fetch: resolve current portal file paths.")


def normalize(snapshot_dir: Path) -> None:  # pragma: no cover - L2 work
    raise NotImplementedError("SPP normalize: constraint-name -> line_id mapping.")
