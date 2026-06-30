"""Headroom — public-data transmission-constraint screening for GETs targeting.

The one invariant that makes the system defensible: no value enters as a bare
scalar. Bucket-A data becomes a `Fact` (cleaned value + raw + confidence);
Bucket-B unobservables become a `Range` (lo/expected/hi + basis). Scoring
consumes only those two types. See provenance/envelope.py.
"""

__version__ = "0.0.0"
