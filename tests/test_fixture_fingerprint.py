"""The spp-synth ranking is pinned. A deliberate fixture change updates
tests/fixtures/spp_synth_fingerprint.json in the same commit and names the change
in the commit message."""

import json
from pathlib import Path

from headroom.pipeline import run_pipeline
from headroom.provenance.reproduce import scores_fingerprint

PIN = Path(__file__).parent / "fixtures" / "spp_synth_fingerprint.json"


def test_spp_synth_ranking_is_unchanged():
    want = [tuple(row) for row in json.loads(PIN.read_text())["fingerprint"]]
    got = scores_fingerprint(run_pipeline("spp-synth"))
    assert got == want
