"""The publishable export gate (Decision 5).

Headroom may legally *fetch and store* non-public (e.g. CEII) data; it must never
*publish* it. So the gate sits at the EXPORT boundary, not the fetch boundary, and
it walks the lineage graph: a value is publishable only if EVERY source in its
transitive `source_closure` is publishable. This is correct today (every source is
public, so the gate is a no-op that passes) and stays correct the moment a
non-publishable tier is added — without that lineage walk, a CEII input would leak
through any composite built on top of it.

Modeled on office-scout's fail-closed `assert_allowed()` gate, moved from the fetch
boundary to the export boundary and upgraded from per-source to lineage-transitive.
"""

from __future__ import annotations

from headroom.provenance.lineage import LineageStore


class PublishabilityError(RuntimeError):
    """Raised when an export would emit a value whose lineage transitively touches a
    non-publishable source under ``public_only``."""


def assert_publishable(
    lineage_id: str,
    store: LineageStore,
    registry,
    *,
    public_only: bool = True,
) -> None:
    """Gate a value before it leaves the system in a shareable artifact.

    Fail-closed: a source absent from the registry, or whose `publishable` is
    False, or a referenced-but-missing lineage node, all block the export.

    `registry` is any object with `.get(name) -> SourceSpec | None` where
    SourceSpec has a `.publishable` bool (see sources/registry.py).
    """
    if not public_only:
        return

    offenders: list[str] = []
    for source in sorted(store.source_closure(lineage_id)):
        spec = registry.get(source)
        if spec is None or not spec.publishable:
            offenders.append(source)

    if offenders:
        raise PublishabilityError(
            f"Refusing to export '{lineage_id}' under public_only=True: its lineage "
            f"transitively touches non-publishable source(s): {offenders}. "
            f"Either remove the non-publishable input from this value's derivation, "
            f"or export it only on an internal (public_only=False) path."
        )
