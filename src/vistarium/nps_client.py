"""NPS Gallery (npgallery.nps.gov) candidate harvesting.

Adapted from a scraper design proven against the live site in an
earlier, unrelated project (2026-08-22/23). No published JSON API --
the site embeds a `var search = {...}` JSON payload in the
search-results HTML.

Endpoint shape:
  1. GET /SearchResults?allFields=<term>&PrimaryType=image  -> SearchID + page 1
  2. GET /SearchResults/<SearchID>?page=N                   -> further pages
  3. Images: GET /GetAsset/<AssetID>/Original                (full-res)
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import requests

BASE = "https://npgallery.nps.gov"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) vistarium/0.1 (contact: joshuakaspar@gmail.com)"
MAX_PAGES_PER_TERM = 4  # 48 results/page server default -> 192/term cap
REQUEST_TIMEOUT_S = 90
MAX_RETRIES = 3

DEFAULT_TERMS = [
    "sunset",
    "sunrise",
    "dawn",
    "dusk",
    "evening",
    "golden hour",
    "moonlight",
    "night sky",
    "stars",
    "milky way",
    "full moon",
    "mountain lake",
    "valley",
    "canyon",
    "glacier",
    "waterfall",
    "beach",
    "coast",
    "ocean",
    "desert",
    "meadow",
    "forest",
    "thunderstorm",
    "fog",
    "snow",
    "autumn",
    "winter",
    "spring",
]


@dataclass
class NPSCandidate:
    """Deterministic fields only -- the schema.json subset Claude Code owns
    outright. Nothing here is sent to the judgment model."""

    id: str
    source: str = "nps"
    source_url: str = ""
    image_url: str = ""
    title: str = ""
    photographer: str | None = None
    date: str | None = None
    park: str = ""
    license: str = ""
    # Not part of schema.json -- raw inputs the pipeline uses to derive
    # deterministic time_of_day evidence before ever calling the model.
    caption_text: str = ""
    exif_datetime_raw: str = ""
    search_terms: list[str] = field(default_factory=list)


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return " ".join(_s(x) for x in v)
    return str(v)


def _http_get(url: str) -> str:
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_S)
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            last_exc = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(3 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def extract_payload(html: str) -> dict | None:
    """Parse the embedded `var search = {...}` JSON object out of a
    SearchResults page. Returns None if the page has no such payload
    (e.g. a zero-result search)."""
    i = html.find('"PageCount"')
    if i < 0:
        return None
    matches = [m for m in re.finditer(r"var\s+(\w+)\s*=\s*\{", html) if m.start() < i]
    if not matches:
        return None
    start = html.find("{", matches[-1].start())
    depth, in_str, esc, end = 0, False, False, None
    for j in range(start, len(html)):
        c = html[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = j + 1
                break
    if end is None:
        return None
    return json.loads(html[start:end])


def asset_to_candidate(asset: dict, term: str) -> NPSCandidate:
    ci = asset.get("ConstraintsInformation") or {}
    # Deliberately Title only, not AltText/Description/Keywords -- found live
    # in the 2026-08-30 validation checkpoint that Description/Keywords can
    # carry generic park-level boilerplate ("landscapes that witnessed the
    # dawn of the Revolutionary War", repeated verbatim across many distinct
    # Minute Man NHP assets) rather than anything specific to the individual
    # photo, which produced confidently wrong time-of-day matches on 15/26
    # records in that run. Title is the one field consistently written
    # per-photo. See DECISIONS.md.
    caption_text = _s(asset.get("Title")).strip()
    exif_raw = (
        asset.get("ImageCreateDateTime") or (asset.get("ImageCreateDate") or {}).get("Date") or ""
    )
    aid = str(asset.get("AssetID"))
    units = asset.get("NPSUnits") or []
    photographer = _s(asset.get("PhotoCredit")) or _s(asset.get("Copyright")) or None
    return NPSCandidate(
        id=aid,
        source_url=f"{BASE}/AssetDetail/{aid}",
        image_url=f"{BASE}/GetAsset/{aid}/Original",
        title=(_s(asset.get("Title")) or _s(asset.get("AltText"))).strip(),
        photographer=photographer,
        date=(asset.get("ImageCreateDate") or {}).get("Date") or asset.get("ImageCreateDateTime"),
        park=units[0]["Name"] if units else "",
        license=f"{ci.get('Constraint', '')}/{ci.get('GrantingRights', '')}".strip("/"),
        caption_text=caption_text,
        exif_datetime_raw=exif_raw,
        search_terms=[term],
    )


def _probe(term: str) -> tuple[str, dict | None]:
    qs = urllib.parse.urlencode(
        {
            "allFields": term,
            "PrimaryType": "image",
            "search_param": "all",
            "allFieldsFormat": "AllWords",
            "view": "grid",
            "filters": "default",
        }
    )
    return term, extract_payload(_http_get(f"{BASE}/SearchResults?{qs}"))


def _fetch_page(search_id: str, page: int) -> tuple[str, int, dict | None]:
    html = _http_get(f"{BASE}/SearchResults/{search_id}?page={page}")
    return search_id, page, extract_payload(html)


def search_candidates(
    terms: list[str] | None = None,
    max_pages_per_term: int = MAX_PAGES_PER_TERM,
    workers: int = 3,
) -> list[NPSCandidate]:
    """Search NPS Gallery across `terms` and return deduplicated candidates
    (by asset ID -- content-level dedup happens later, after download, via
    dedup.Deduplicator). Network I/O; no model calls."""
    terms = terms or DEFAULT_TERMS
    by_id: dict[str, NPSCandidate] = {}

    with ThreadPoolExecutor(workers) as ex:
        firsts: dict[str, dict] = {}
        for fut in as_completed([ex.submit(_probe, t) for t in terms]):
            term, payload = fut.result()
            if payload:
                firsts[term] = payload

        jobs: list[tuple[str, int]] = []
        for payload in firsts.values():
            page_size = payload.get("PageSize") or 48
            pages = min(max_pages_per_term, (payload.get("ResultCount") or 0) // page_size or 1)
            for page in range(2, pages + 1):
                jobs.append((payload.get("SearchID"), page))

        page_payloads = [(payload.get("SearchID"), 1, payload) for payload in firsts.values()]
        futs = {ex.submit(_fetch_page, sid, page): (sid, page) for sid, page in jobs}
        for fut in as_completed(futs):
            sid, page, payload = fut.result()
            if payload:
                page_payloads.append((sid, page, payload))

        sid_to_term = {p.get("SearchID"): t for t, p in firsts.items()}
        for sid, _page, payload in page_payloads:
            term = sid_to_term.get(sid, "")
            for row in payload.get("Results") or []:
                cand = asset_to_candidate(row.get("Asset") or {}, term)
                if cand.id in by_id:
                    if term not in by_id[cand.id].search_terms:
                        by_id[cand.id].search_terms.append(term)
                else:
                    by_id[cand.id] = cand

    return list(by_id.values())


def download_image(candidate: NPSCandidate, dest_dir: Path) -> Path:
    """Download the full-resolution original to dest_dir, named by asset ID.
    Returns the local path. Raises requests.RequestException on failure
    after retries -- the pipeline decides whether to skip or abort."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{candidate.id}.jpg"
    if dest_path.exists():
        return dest_path
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(
                candidate.image_url,
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT_S,
            )
            r.raise_for_status()
            dest_path.write_bytes(r.content)
            return dest_path
        except requests.RequestException as e:
            last_exc = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(3 * (attempt + 1))
    assert last_exc is not None
    raise last_exc
