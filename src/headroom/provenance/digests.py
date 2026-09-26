"""Canonical identities for replay comparison."""

from __future__ import annotations

import hashlib
import json


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def ranking_digest(result) -> str:
    payload = {
        "scores": [score.model_dump(mode="json") for score in result.model.scores],
        "shortlist": result.model.shortlist,
        "k": result.model.k,
        "n_constraints": result.model.n_constraints,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
