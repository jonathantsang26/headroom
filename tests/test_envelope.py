"""The two foundational types: no bare scalars, raw preserved, ranges ordered."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from headroom.provenance import Fact, Range, derive_lineage_id

_T0 = datetime(2026, 6, 30, tzinfo=timezone.utc)


def test_fact_preserves_as_reported_and_confidence():
    f = Fact(
        value=345.0,
        as_reported="345 kV",
        unit="kV",
        confidence="high",
        source="pudl_ferc_form1",
        source_version="pudl_v2024.11.0",
        retrieved_at=_T0,
        lineage_id="ferc1:line7",
    )
    assert f.value == 345.0
    assert f.as_reported == "345 kV"  # verbatim original, never overwritten
    assert f.confidence == "high"
    assert f.flags == []
    assert f.inputs == []


def test_fact_rejects_bad_confidence():
    with pytest.raises(ValidationError):
        Fact(
            value=1,
            confidence="kinda-sure",  # not in the Literal
            source="s",
            source_version="v",
            retrieved_at=_T0,
            lineage_id="x",
        )


def test_range_requires_ordering():
    with pytest.raises(ValidationError):
        Range(
            lo=2.0,
            expected=1.0,  # expected < lo
            hi=3.0,
            basis="bad",
            source="s",
            source_version="v",
            lineage_id="x",
        )


def test_range_ok_and_defaults_triangular():
    r = Range(
        lo=0.4,
        expected=1.1,
        hi=2.3,
        basis="ok",
        source="spp_binding_constraints",
        source_version="spp_portal_2025-06",
        lineage_id="spp:bc:1",
    )
    assert r.dist == "triangular"
    assert r.lo <= r.expected <= r.hi


def test_derive_lineage_id_is_deterministic_and_order_independent():
    a = derive_lineage_id("composite", ["p1", "p2"])
    b = derive_lineage_id("composite", ["p2", "p1"])  # order should not matter
    assert a == b
    assert a != derive_lineage_id("composite", ["p1", "p3"])
