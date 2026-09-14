"""The publishable export gate (Decision 5)."""

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
    """Gate a value before it leaves the system in a shareable artifact."""
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
