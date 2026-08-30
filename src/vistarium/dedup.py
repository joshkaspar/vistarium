"""Exact-content deduplication.

v1 is sha256-exact only -- deliberately not perceptual/near-duplicate
hashing (different crops, re-compressions, or watermarked re-uploads of
the same photo would not be caught). See ROADMAP.md for that as a
future improvement; not needed until NPS volume shows it's a real
problem, per the "don't design for hypothetical requirements" rule.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


class Deduplicator:
    def __init__(self) -> None:
        self._seen: dict[str, Path] = {}

    def hash_file(self, path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    def is_duplicate(self, path: Path) -> Path | None:
        """Return the previously-seen path with identical content, or None
        if `path` is new. Registers `path` as seen either way."""
        digest = self.hash_file(path)
        existing = self._seen.get(digest)
        if existing is None:
            self._seen[digest] = path
        return existing
