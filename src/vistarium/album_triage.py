"""Deterministic album-level triage: which of a park's albums are worth
fetching at all, decided from title/description alone -- no image bytes
touched at this stage.

Confirmed live 2026-09-01 (see DECISIONS.md): there's no reliable way to
*automate* telling a landscape-worthy album ("Cadillac Mountain") from
an administrative one ("Acadia Awards Gathering 2025") -- Acadia's 211
albums split roughly evenly between the two, distinguishable to a human
reading the title/description but not by any single obvious pattern.
This module doesn't attempt that judgment call generically; it applies
a versioned, human-curated keyword list (album_keywords.json) as a
cheap first pass, catching the clearly-administrative and clearly-
scenic cases, and lets everything genuinely ambiguous fall through to
the real signal (aesthetic scoring on actual thumbnails) rather than
being silently dropped.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from vistarium.nps_client import AlbumInfo

DEFAULT_KEYWORDS_PATH = Path(__file__).resolve().parent.parent.parent / "album_keywords.json"

Classification = Literal["include", "exclude", "ambiguous"]


def load_keywords(path: Path = DEFAULT_KEYWORDS_PATH) -> dict[str, list[str]]:
    data = json.loads(path.read_text())
    return {"exclude": data.get("exclude", []), "include": data.get("include", [])}


def classify_album(album: AlbumInfo, keywords: dict[str, list[str]]) -> Classification:
    """Exclude terms win over include terms when both match (an album
    titled "Access: Scenic Overlook" is accessibility documentation, not
    a scenic collection, despite "overlook" matching include)."""
    haystack = f"{album.title} {album.description}".lower()
    if any(term.lower() in haystack for term in keywords.get("exclude", [])):
        return "exclude"
    if any(term.lower() in haystack for term in keywords.get("include", [])):
        return "include"
    return "ambiguous"
