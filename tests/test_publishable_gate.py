"""The lineage-walk publishable gate (Decision 5). The key property: a composite
built on a non-publishable input is itself non-publishable — a per-source check
would miss this; the transitive walk catches it."""

from datetime import datetime, timezone

import pytest

from headroom.provenance import (
    LineageStore,
    PublishabilityError,
    Range,
    assert_publishable,
    derive_lineage_id,
)
from headroom.provenance.lineage import UNKNOWN_PREFIX
from headroom.sources import load_registry

_T0 = datetime(2026, 6, 30, tzinfo=timezone.utc)


def _range(lineage_id, source, inputs=()):
    return Range(
        lo=0.0,
        expected=1.0,
        hi=2.0,
        basis="test",
        source=source,
        source_version="v",
        lineage_id=lineage_id,
        inputs=list(inputs),
    )


def test_public_lineage_passes():
    reg = load_registry()
    store = LineageStore()
    a = store.add(_range("a", "spp_binding_constraints"))
    b = store.add(_range("b", "spp_marketplace_lmp"))
    parents = [a.lineage_id, b.lineage_id]
    comp = store.add(
        _range(derive_lineage_id("c", parents), "model.composite", parents)
    )
    # The derived composite's own 'model.composite' label is a stage label, not a
    # registered source, so it is NOT checked; only its public leaf inputs are.
    assert store.source_closure(comp.lineage_id) == {
        "spp_binding_constraints",
        "spp_marketplace_lmp",
    }
    assert_publishable(comp.lineage_id, store, reg, public_only=True)  # passes


def test_ceii_taint_blocks_publish():
    reg = load_registry()
    store = LineageStore()
    public = store.add(_range("pub", "spp_binding_constraints"))
    ceii = store.add(_range("ceii", "ferc_form_715"))
    parents = [public.lineage_id, ceii.lineage_id]
    tainted = store.add(
        _range(derive_lineage_id("t", parents), "spp_binding_constraints", parents)
    )
    with pytest.raises(PublishabilityError):
        assert_publishable(tainted.lineage_id, store, reg, public_only=True)


def test_direct_ceii_blocks_publish():
    reg = load_registry()
    store = LineageStore()
    ceii = store.add(_range("ceii", "ferc_form_715"))
    with pytest.raises(PublishabilityError):
        assert_publishable(ceii.lineage_id, store, reg, public_only=True)


def test_public_only_false_bypasses_gate():
    reg = load_registry()
    store = LineageStore()
    ceii = store.add(_range("ceii", "ferc_form_715"))
    # Internal path: explicitly allowed to use CEII.
    assert_publishable(ceii.lineage_id, store, reg, public_only=False)


def test_unregistered_source_fails_closed():
    reg = load_registry()
    store = LineageStore()
    bogus = store.add(_range("bogus", "not_a_registered_source"))
    with pytest.raises(PublishabilityError):
        assert_publishable(bogus.lineage_id, store, reg, public_only=True)


def test_missing_lineage_node_fails_closed():
    reg = load_registry()
    store = LineageStore()
    # 'child' references a parent that was never added to the store.
    child = store.add(_range("child", "spp_binding_constraints", ["ghost_parent"]))
    closure = store.source_closure(child.lineage_id)
    assert any(s.startswith(UNKNOWN_PREFIX) for s in closure)
    with pytest.raises(PublishabilityError):
        assert_publishable(child.lineage_id, store, reg, public_only=True)
