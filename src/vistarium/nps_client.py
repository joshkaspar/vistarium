"""NPS Gallery (npgallery.nps.gov) candidate harvesting.

Adapted from a scraper design proven against the live site in an
earlier, unrelated project (2026-08-22/23). No published JSON API --
the site embeds a `var search = {...}` JSON payload in the
search-results HTML.

Endpoint shape:
  1. GET /SearchResults?allFields=<term>&PrimaryType=image  -> SearchID + page 1
  2. GET /SearchResults/<SearchID>?page=N                   -> further pages
  3. Images: GET /GetAsset/<AssetID>/Original                (full-res)

Preferred search strategy (2026-09-01, see DECISIONS.md): NPGallery's own
advanced search supports filter params -- `filter=Units:<code>&filter=
Categories:Scenic&filter=ResourceTypes:Image` -- which target NPS's own
per-park "Scenic" categorization directly, instead of guessing at
DEFAULT_TERMS keywords. Same embedded-JSON payload, same asset_to_
candidate() parsing; only the query differs. Discovered by hand-browsing
the site's own advanced-search UI after keyword search was found to miss
entire named collections (e.g. Acadia's official "Night Skies" curated
gallery never surfaced for the literal terms "Acadia" + "night").
search_park_scenic() is the entry point; DEFAULT_TERMS/search_candidates
are kept for ad hoc/cross-park term search, not removed.
"""

from __future__ import annotations

import json
import re
import threading
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

# Throttles every NPGallery request through the single _http_request choke
# point, not just thumbnail fetching -- the album/thumbnail discovery path
# (curate.py) can generate thousands of requests for one park, versus the
# handful a single keyword/album search used to make. npgallery.nps.gov (the
# DAM/asset-serving host this module talks to) has no published rate limit
# of its own and -- confirmed live 2026-09-01 -- doesn't return the
# X-RateLimit-* headers NPS's *other* public API (developer.nps.gov,
# API-key gated) documents, so there's no way to observe our real quota on
# this host in-flight. That API's default (1000 requests/hour) is still
# the closest signal available for what NPS considers reasonable automated
# access; matched exactly here rather than padded, since a wrong guess
# can't be self-corrected from response headers the way it normally would.
MIN_REQUEST_INTERVAL_S = 3.6  # 1000 req/hour ceiling, across all callers/threads
_rate_lock = threading.Lock()
_last_request_at = 0.0


def _throttle() -> None:
    global _last_request_at
    with _rate_lock:
        wait = _last_request_at + MIN_REQUEST_INTERVAL_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


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
    # Set by curate.select_by_threshold_with_floor() for the curated
    # path only -- carries the thumbnail-based score through to
    # pipeline.build_record() so it lands in the final catalog record.
    # None for every other search path, which never scores candidates.
    aesthetic_score: float | None = None
    aesthetic_method: str | None = None


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return " ".join(_s(x) for x in v)
    return str(v)


def _http_request(url: str) -> requests.Response:
    """The one place every NPGallery request actually goes out -- throttled
    (see MIN_REQUEST_INTERVAL_S) and retried. Used for HTML/JSON pages
    (_http_get) and raw image bytes (download_image/download_thumbnail)
    alike, so the throttle can't be bypassed by a call site that fetches
    bytes directly instead of going through _http_get."""
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        _throttle()
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_S)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last_exc = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(3 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def _http_get(url: str) -> str:
    return _http_request(url).text


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


def asset_to_candidate(asset: dict, term: str, park_code: str | None = None) -> NPSCandidate:
    """`park_code`, when given, is the NPS unit code the search was
    actually scoped to (e.g. search_park_scenic's `Units:<code>` filter).
    An asset can legitimately belong to several NPSUnits at once --
    found live 2026-09-01 searching Grand Teton: a shared historical
    asset was cross-tagged under Devils Tower, Grand Canyon, Grand
    Teton, and the Museum Management Program all at once, and
    units[0] was Devils Tower, not the park actually searched for.
    park_code lets the matching NPSUnits entry win instead of always
    trusting the list's first element; falls back to units[0] when
    park_code is None (generic keyword search, no single target park)
    or doesn't match any listed unit."""
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
    park_name = ""
    if units:
        matched = (
            next((u for u in units if u.get("Code") == park_code), None) if park_code else None
        )
        park_name = (matched or units[0])["Name"]
    photographer = _s(asset.get("PhotoCredit")) or _s(asset.get("Copyright")) or None
    return NPSCandidate(
        id=aid,
        source_url=f"{BASE}/AssetDetail/{aid}",
        image_url=f"{BASE}/GetAsset/{aid}/Original",
        title=(_s(asset.get("Title")) or _s(asset.get("AltText"))).strip(),
        photographer=photographer,
        date=(asset.get("ImageCreateDate") or {}).get("Date") or asset.get("ImageCreateDateTime"),
        park=park_name,
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


# A safety ceiling, not a practical target -- fetching search-result pages
# is cheap (HTTP only, no model calls), so there's no real reason to
# truncate a park's candidate pool. Found live 2026-09-01: Kenai Fjords
# alone has 15,242 Categories:Scenic images (31 pages at 500/page); a
# small default cap here would silently bias which subset of a large
# park's photos are ever even seen, on top of NPS's own (non-random)
# default result ordering. 200 pages = 100,000 candidates is far above
# any single park's real count, just a backstop against a runaway fetch.
DEFAULT_MAX_PAGES_PER_PARK = 200


def _probe_park_scenic(park_code: str) -> dict | None:
    qs = urllib.parse.urlencode(
        [
            ("filter", f"Units:{park_code}"),
            ("filter", "Categories:Scenic"),
            ("filter", "ResourceTypes:Image"),
            ("view", "grid"),
            ("sort", "default"),
        ]
    )
    return extract_payload(_http_get(f"{BASE}/SearchResults?{qs}"))


def search_park_scenic(
    park_code: str,
    max_pages: int = DEFAULT_MAX_PAGES_PER_PARK,
    workers: int = 6,
) -> list[NPSCandidate]:
    """Searches NPGallery's own `Categories:Scenic` tag scoped to one park
    (`Units:<code>`), NPS's own curation rather than a guessed keyword --
    see the module docstring. `park_code` is the 4-letter NPS unit code
    (e.g. "ACAD"); resolve one via fetch_unit_codes()."""
    first = _probe_park_scenic(park_code)
    if not first:
        return []

    by_id: dict[str, NPSCandidate] = {}
    search_id = first.get("SearchID")
    page_size = first.get("PageSize") or 500
    result_count = first.get("ResultCount") or 0
    total_pages = min(max_pages, -(-result_count // page_size) or 1)  # ceil div

    page_payloads = [(search_id, 1, first)]
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(_fetch_page, search_id, p): p for p in range(2, total_pages + 1)}
        for fut in as_completed(futs):
            sid, page, payload = fut.result()
            if payload:
                page_payloads.append((sid, page, payload))

    term = f"scenic:{park_code}"
    for _sid, _page, payload in page_payloads:
        for row in payload.get("Results") or []:
            cand = asset_to_candidate(row.get("Asset") or {}, term, park_code=park_code)
            by_id.setdefault(cand.id, cand)

    return list(by_id.values())


def fetch_unit_codes() -> dict[str, str]:
    """Returns {display_name: unit_code} for every NPS unit (683+ as of
    2026-09-01), resolved from the "Units" filter facet that any
    SearchResults response includes regardless of what filters were
    applied -- no separate API/key needed, one HTTP request."""
    qs = urllib.parse.urlencode({"filter": "ResourceTypes:Image", "view": "grid"})
    payload = extract_payload(_http_get(f"{BASE}/SearchResults?{qs}"))
    if not payload:
        return {}
    units_facet = next((f for f in payload.get("Filters") or [] if f.get("Term") == "Units"), None)
    if not units_facet:
        return {}
    return {
        item["DisplayName"]: item["Attribute"]
        for item in units_facet.get("Items", [])
        if item.get("DisplayName") and item.get("Attribute")
    }


@dataclass
class AlbumInfo:
    """Metadata for one curated album -- discovery only, not itself a
    photo candidate. See list_park_albums()."""

    id: str
    title: str
    description: str
    asset_count: int


def list_park_albums(park_code: str, max_pages: int = 20) -> list[AlbumInfo]:
    """Lists every album NPGallery has tagged under a park
    (`ResourceTypes:Album`), for a human to review and pick from. There's
    no reliable way to automatically tell a landscape-worthy album
    ("Carriage Roads - Day Mountain Loop") from an administrative one
    ("Acadia Awards Gathering 2025") from title/description alone --
    confirmed live 2026-09-01, Acadia alone has 211 albums spanning both.
    Discovery only; pass a chosen album's id to search_album()."""
    qs = urllib.parse.urlencode(
        [
            ("filter", f"Units:{park_code}"),
            ("filter", "ResourceTypes:Album"),
            ("view", "grid"),
            ("sort", "default"),
        ]
    )
    first = extract_payload(_http_get(f"{BASE}/SearchResults?{qs}"))
    if not first:
        return []

    search_id = first.get("SearchID")
    page_size = first.get("PageSize") or 500
    result_count = first.get("ResultCount") or 0
    total_pages = min(max_pages, -(-result_count // page_size) or 1)  # ceil div

    payloads = [first]
    with ThreadPoolExecutor(6) as ex:
        futs = {ex.submit(_fetch_page, search_id, p): p for p in range(2, total_pages + 1)}
        for fut in as_completed(futs):
            _sid, _page, payload = fut.result()
            if payload:
                payloads.append(payload)

    albums = []
    for payload in payloads:
        for row in payload.get("Results") or []:
            a = row.get("Asset") or {}
            albums.append(
                AlbumInfo(
                    id=str(a.get("AssetID")),
                    title=_s(a.get("Title")).strip(),
                    description=_s(a.get("Description")).strip(),
                    asset_count=a.get("AssetCount") or 0,
                )
            )
    return albums


def search_album(album_id: str, park_code: str | None = None) -> list[NPSCandidate]:
    """Fetches every image in one hand-curated album by id (see
    list_park_albums()) -- the highest-payoff strategy for landscape-
    worthy content: these are park staff's own picks, not a keyword or
    category guess. Different endpoint shape than the SearchResults
    pages (a direct JSON response, no HTML/embedded-payload wrapper to
    extract), but the same Asset structure -- reuses asset_to_candidate()
    unchanged. 2000-image page size comfortably covers any real curated
    album (the largest seen so far is in the dozens, not hundreds)."""
    qs = urllib.parse.urlencode({"pagesize": "2000", "primarytype": "image"})
    text = _http_get(f"{BASE}/api/search/execute/albumid/{album_id}?{qs}")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []

    term = f"album:{album_id}"
    return [
        asset_to_candidate(row.get("Asset") or {}, term, park_code=park_code)
        for row in payload.get("Results") or []
    ]


def download_image(candidate: NPSCandidate, dest_dir: Path) -> Path:
    """Download the full-resolution original to dest_dir, named by asset ID.
    Returns the local path. Raises requests.RequestException on failure
    after retries -- the pipeline decides whether to skip or abort."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{candidate.id}.jpg"
    if dest_path.exists():
        return dest_path
    dest_path.write_bytes(_http_request(candidate.image_url).content)
    return dest_path


def download_thumbnail(candidate: NPSCandidate, dest_dir: Path) -> Path:
    """Download the cheap `ProxyLoRes` derivative (~500x375, ~78KB vs.
    Original's ~1-2MB+) to dest_dir, named by asset ID -- for aesthetic
    pre-filtering at scale (curate.py), where fetching full-res for every
    candidate in a large pool before scoring would be needless bandwidth
    for images most of which won't survive the threshold. Confirmed live
    2026-09-01: GetAsset/<id>/proxy/lores serves exactly the ProxyLoRes
    kind the album API's FileInfo already advertises."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{candidate.id}.jpg"
    if dest_path.exists():
        return dest_path
    url = f"{BASE}/GetAsset/{candidate.id}/proxy/lores"
    dest_path.write_bytes(_http_request(url).content)
    return dest_path
