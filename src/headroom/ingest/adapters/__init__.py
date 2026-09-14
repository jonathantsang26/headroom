"""Live-source adapters. Each adapter owns one upstream (PUDL/FERC1, EIA, HIFLD,
SPP) and does two jobs: fetch() — download raw, hash, record in the snapshot
manifest — and normalize() — emit bundle-shaped tables into normalized/ with a
manifest stamp. The live loader reads only normalized/ + manifest; it never sees
raw. The join (canonical line_ids across HIFLD/EIA/FERC1) lives in normalize, not
in the pipeline: the fixture format is the post-join format."""
