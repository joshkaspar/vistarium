"""Validate catalog records against the checked-in schema.json.

schema.json is the versioned source of truth; changes to it are decision
commits, never silent (see AGENT_DECISION_POLICY.md).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import jsonschema

SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "schema.json"


@lru_cache(maxsize=1)
def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def validate_record(record: dict) -> None:
    """Raises jsonschema.ValidationError if `record` doesn't match
    schema.json. Returns None on success."""
    jsonschema.validate(instance=record, schema=_schema())


def is_valid(record: dict) -> bool:
    try:
        validate_record(record)
        return True
    except jsonschema.ValidationError:
        return False
