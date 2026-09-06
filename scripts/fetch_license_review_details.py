#!/usr/bin/env python3
"""One-off: enrich data/license_review_candidates.json with the raw
rights-related fields NPGallery's AssetDetail page renders server-side
but this pipeline never captured -- Copyright, Constraints Information,
Access Constraints, Contacts, and Photographer (the page's label for
PhotoCredit). Built for the 2026-09-05 license review (see
DECISIONS.md, license_review_server.py) after finding these fields
carry real signal a bare "Public domain/Unknown"|"Partial" string
doesn't (e.g. an explicit "IN COPYRIGHT ... no public distribution"
override sitting in Constraints Information's own text).

AssetDetail/<id> is a direct, ID-keyed lookup (unlike a title search,
no ambiguity about which asset matched) -- reuses nps_client's shared
_http_get/_throttle so this respects the same 3.6s/request ceiling
every other NPGallery caller does. 923 candidates * 3.6s is close to
an hour; writes back to the candidates file after every record so it's
safely interruptible and resumable (already-enriched records, detected
by the presence of "detail_fetched", are skipped on a re-run).

Usage: uv run python scripts/fetch_license_review_details.py
"""

import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, "src")

from vistarium import nps_client  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fetch_license_details")

CANDIDATES_PATH = Path("data/license_review_candidates.json")

FIELDS = {
    "detail_copyright": "Copyright:",
    "detail_constraints_information": "Constraints Information:",
    "detail_access_constraints": "Access Constraints:",
    "detail_contacts": "Contacts:",
    "detail_photo_credit": "Photographer:",
}


def _clean_field(raw: str) -> str | None:
    text = re.sub(r"<br\s*/?>", "\n", raw)
    text = re.sub(r"<[^>]+>", "", text)
    lines = [ln.strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]
    result = "\n".join(lines)
    return result or None


def _extract_field(html: str, label: str) -> str | None:
    m = re.search(
        r"<label[^>]*>\s*" + re.escape(label) + r"\s*</label>\s*<div[^>]*>(.*?)</div>",
        html,
        re.DOTALL,
    )
    if not m:
        return None
    return _clean_field(m.group(1))


def main() -> None:
    records = json.loads(CANDIDATES_PATH.read_text())
    todo = [r for r in records if "detail_fetched" not in r]
    log.info(
        "%d/%d records already enriched, %d to go",
        len(records) - len(todo),
        len(records),
        len(todo),
    )

    for i, record in enumerate(todo, 1):
        try:
            html = nps_client._http_get(record["source_url"])
        except Exception as e:
            log.warning("[%d/%d] fetch failed for %s: %s", i, len(todo), record["id"], e)
            continue

        for field_name, label in FIELDS.items():
            record[field_name] = _extract_field(html, label)
        record["detail_fetched"] = True

        CANDIDATES_PATH.write_text(json.dumps(records, indent=2))
        log.info("[%d/%d] %s: %s", i, len(todo), record["id"], record["title"][:50])

    log.info("done")


if __name__ == "__main__":
    main()
