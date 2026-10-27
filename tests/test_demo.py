"""The Phase 0 demo (`python -m headroom.demo`) still runs end to end, and the gate
still blocks the composite that folds in a CEII input."""

from headroom.demo import run_demo


def test_demo_passes_the_public_composite_and_blocks_the_ceii_one():
    result = run_demo()
    assert result["public_composite_publishable"] is True
    assert result["ceii_composite_blocked"] is True
    assert result["manifest"]["run_label"] == "phase0-demo"
