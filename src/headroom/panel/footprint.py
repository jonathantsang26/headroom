"""A buyer footprint and its manual matches to source constraints.

Each row records one footprint element's match to one constraint. Only
`verified` rows, with evidence and a named reviewer, enter the panel; `uncertain`
and `unmatched` rows are reported as unresolved and never guessed into it.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ELEMENT_KINDS = (
    "settlement_location", "resource", "site", "county", "owner", "corridor", "constraint",
)
MATCH_STATUSES = ("verified", "uncertain", "unmatched")
FOOTPRINT_COLUMNS = (
    "element_id", "element_kind", "constraint_id", "corridor_id", "match_status",
    "evidence", "evidence_refs", "reviewed_by", "reviewed_on",
)


@dataclass(frozen=True)
class FootprintMatch:
    element_id: str
    element_kind: str
    constraint_id: str
    corridor_id: str
    match_status: str
    evidence: str
    evidence_refs: str
    reviewed_by: str
    reviewed_on: str


@dataclass(frozen=True)
class Footprint:
    name: str  # file name only; buyer paths never enter artifacts
    sha256: str
    matches: tuple[FootprintMatch, ...]

    def verified(self) -> dict[str, list[FootprintMatch]]:
        by_constraint: dict[str, list[FootprintMatch]] = {}
        for match in self.matches:
            if match.match_status == "verified":
                by_constraint.setdefault(match.constraint_id, []).append(match)
        return by_constraint

    def unresolved(self) -> list[FootprintMatch]:
        return [m for m in self.matches if m.match_status != "verified"]


def load_footprint(path: str | Path) -> Footprint:
    path = Path(path)
    return parse_footprint(path.read_bytes(), name=path.name)


def parse_footprint(content: bytes, *, name: str) -> Footprint:
    # utf-8-sig: footprints are often exported from spreadsheets with a BOM.
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""))
    names = reader.fieldnames
    if names is None or len(names) != len(set(names)) or set(names) != set(FOOTPRINT_COLUMNS):
        raise ValueError(f"Footprint must have exactly the columns: {', '.join(FOOTPRINT_COLUMNS)}")

    matches: list[FootprintMatch] = []
    seen: set[tuple[str, str, str]] = set()
    corridor_for: dict[str, str] = {}
    for number, row in enumerate(reader, start=2):
        where = f"footprint line {number}"
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"{where} has the wrong number of fields")
        match = FootprintMatch(**{key: row[key].strip() for key in FOOTPRINT_COLUMNS})
        if not match.element_id:
            raise ValueError(f"{where} needs an element_id")
        if match.element_kind not in ELEMENT_KINDS:
            raise ValueError(f"{where} element_kind must be one of {', '.join(ELEMENT_KINDS)}")
        if match.match_status not in MATCH_STATUSES:
            raise ValueError(f"{where} match_status must be one of {', '.join(MATCH_STATUSES)}")
        if match.match_status == "verified":
            missing = [
                key for key in ("constraint_id", "evidence", "reviewed_by", "reviewed_on")
                if not getattr(match, key)
            ]
            if missing:
                raise ValueError(f"{where} is verified but lacks {', '.join(missing)}")
            try:
                reviewed = date.fromisoformat(match.reviewed_on)
            except ValueError:
                reviewed = None
            if reviewed is None or reviewed.isoformat() != match.reviewed_on:
                raise ValueError(f"{where} reviewed_on must be YYYY-MM-DD")
            if match.corridor_id:
                prior = corridor_for.setdefault(match.constraint_id, match.corridor_id)
                if prior != match.corridor_id:
                    raise ValueError(
                        f"{where} verifies {match.constraint_id} on corridor "
                        f"{match.corridor_id}, but another row verified {prior}"
                    )
        elif match.match_status == "uncertain" and not match.evidence:
            raise ValueError(f"{where} is uncertain but records no evidence")
        elif match.match_status == "unmatched" and match.constraint_id:
            raise ValueError(f"{where} is unmatched but names constraint {match.constraint_id}")
        key = (match.element_kind, match.element_id, match.constraint_id)
        if key in seen:
            raise ValueError(f"{where} repeats {match.element_kind} {match.element_id} -> "
                             f"{match.constraint_id or '(none)'}")
        seen.add(key)
        matches.append(match)

    if not matches:
        raise ValueError("Footprint has no rows")
    return Footprint(
        name=name, sha256=hashlib.sha256(content).hexdigest(), matches=tuple(matches),
    )
