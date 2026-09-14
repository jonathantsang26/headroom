"""Lineage graph + taint walk."""

from __future__ import annotations

from typing import Iterable, Union

from headroom.provenance.envelope import Fact, Range

Envelope = Union[Fact, Range]

# Prefix marking an input id that is referenced but absent from the store.
UNKNOWN_PREFIX = "__unknown_node__:"


class LineageStore:
    """In-memory provenance graph. Cheap and dependency-free; the durable record
    is the per-value `source_version` + `lineage_id`, which this reconstructs."""

    def __init__(self, envelopes: Iterable[Envelope] | None = None) -> None:
        self._nodes: dict[str, Envelope] = {}
        if envelopes:
            for env in envelopes:
                self.add(env)

    def add(self, env: Envelope) -> Envelope:
        """Register a value. Returns it, so callers can `store.add(Fact(...))`."""
        self._nodes[env.lineage_id] = env
        return env

    def get(self, lineage_id: str) -> Envelope | None:
        return self._nodes.get(lineage_id)

    def __contains__(self, lineage_id: str) -> bool:
        return lineage_id in self._nodes

    def __len__(self) -> int:
        return len(self._nodes)

    def source_closure(self, lineage_id: str) -> set[str]:
        """The set of registered *root* source names this value depends on."""
        sources: set[str] = set()
        seen: set[str] = set()
        stack: list[str] = [lineage_id]
        while stack:
            node_id = stack.pop()
            if node_id in seen:
                continue
            seen.add(node_id)
            node = self._nodes.get(node_id)
            if node is None:
                sources.add(f"{UNKNOWN_PREFIX}{node_id}")
                continue
            if node.inputs:
                stack.extend(node.inputs)  # derived: taint flows from inputs only
            else:
                sources.add(node.source)  # leaf: names a registered data source
        return sources
