"""One-off, read-only investigation: does NPGallery's YOSE archival
portal (npgallery.nps.gov/YOSE/) `Rating` field (1-5, cataloger-set)
correlate with technical/visual quality, as a possible future signal
neither `aesthetic_score` (CLIP-based, composition/appeal) nor the VLM
judgment (never built to catch "soft/faded old scan") currently checks?

Data-gathering only. Does NOT write to catalog.json/hidden_ids.json,
does NOT make any inclusion/exclusion call. Output is a local JSON
snapshot for a human to look at -- see DECISIONS.md, 2026-09-08, for
the full writeup of what this found.

Endpoint discovered by hand-inspecting the YOSE portal's own search
form (npgallery.nps.gov/YOSE/, id="search-form"): a plain GET to
/YOSE/SearchResults with `collection=<name>` and `controlledkeyword=
<Hierarchy Series value>` returns the same embedded `var search =
{...}` JSON payload nps_client.extract_payload() already parses for
the general npgallery.nps.gov search -- fully server-rendered, no
separate AJAX/JSON API needed despite the client-side-JS-only search
UI. Confirmed: `?collection=1031+Yosemite+Historic+Photo+Collection&
controlledkeyword=Scenics-Canyons&pagesize=100` returns all 75 results
in one page.

`File Size (bytes)` and `Photographer` are NOT in the search JSON
payload (checked: absent from every Result[].Asset in a live pull) --
only available on the per-asset AssetDetail HTML page, so this script
makes one extra request per asset for those two fields.

Usage:
    uv run python scripts/_investigate_yose_rating.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, "src")

from vistarium.nps_client import USER_AGENT, _http_request  # noqa: E402

BASE = "https://npgallery.nps.gov/YOSE"
COLLECTION = "1031 Yosemite Historic Photo Collection"
OUT_PATH = Path("/tmp/yose_rating_investigation.json")

# Confirmed against the portal's own Hierarchy Series dropdown
# (npgallery.nps.gov/YOSE/, select#yoseSeries) -- the full Scenics-*
# list, not just the ones Josh had already seen (Caves, Other Areas,
# Special Effects, Unidentified were not in the original 14).
# Full list confirmed live against the portal's Hierarchy Series
# dropdown -- kept here for the record even though this run only
# pulls the Scenics-Canyons pilot (see DECISIONS.md, 2026-09-08: the
# pilot itself found Rating uniform across the whole parent collection,
# so expanding to the other 17 was paused pending Josh's review).
SCENICS_SERIES_FULL = [
    "Scenics-Canyons",
    "Scenics-Caves",
    "Scenics-Cliffs",
    "Scenics-Domes",
    "Scenics-Lakes",
    "Scenics-Meadows",
    "Scenics-Mountains",
    "Scenics-Other Areas",
    "Scenics-Passes",
    "Scenics-Pinnacles",
    "Scenics-Special Effects",
    "Scenics-Springs",
    "Scenics-StreamsRivers",
    "Scenics-Trees",
    "Scenics-Unidentified",
    "Scenics-Valleys",
    "Scenics-Views",
    "Scenics-Waterfalls",
]
SCENICS_SERIES = ["Scenics-Canyons"]


def extract_payload(html: str) -> dict | None:
    """Same parser as nps_client.extract_payload() -- duplicated here
    rather than imported since that one isn't exported for reuse
    outside the module and this is a throwaway script."""
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


def fetch_series(series: str) -> list[dict]:
    """All results for one Hierarchy Series value, paginating if needed."""
    import urllib.parse

    params = urllib.parse.urlencode(
        {"collection": COLLECTION, "controlledkeyword": series, "pagesize": 100}
    )
    resp = _http_request(f"{BASE}/SearchResults?{params}")
    data = extract_payload(resp.text)
    if not data:
        print(f"  {series}: no payload (0 results)")
        return []
    results = list(data["Results"])
    page_count = data.get("PageCount", 1)
    search_id = data["SearchID"]
    for page in range(2, page_count + 1):
        resp = _http_request(f"{BASE}/SearchResults/{search_id}?page={page}")
        page_data = extract_payload(resp.text)
        if page_data:
            results.extend(page_data["Results"])
    print(f"  {series}: {data['UnfilteredCount']} results")
    return results


def fetch_detail_extras(asset_id: str) -> dict:
    """File Size and Photographer -- not present in the search JSON,
    only on the AssetDetail HTML page. One extra request per asset."""
    resp = _http_request(f"https://npgallery.nps.gov/AssetDetail/{asset_id}")
    html = resp.text

    def _label_value(label: str) -> str | None:
        m = re.search(
            rf'<label[^>]*>{re.escape(label)}:</label>\s*<div[^>]*>(.*?)</div>',
            html,
            re.DOTALL,
        )
        if not m:
            return None
        return re.sub(r"<[^>]+>", "", m.group(1)).strip() or None

    original_link = re.search(
        r'href="(/GetAsset/[a-f0-9]+/original\.tif\?)"[^>]*>([^<]+)</a>', html
    )
    return {
        "file_size_bytes_label": _label_value("File Size (bytes)"),
        "original_download_text": original_link.group(2) if original_link else None,
        "photographer_detail": _label_value("Photographer"),
    }


def main() -> None:
    all_records: dict[str, dict] = {}
    print(f"Pulling {len(SCENICS_SERIES)} Scenics-* series from {COLLECTION!r}...")
    for series in SCENICS_SERIES:
        for r in fetch_series(series):
            a = r["Asset"]
            asset_id = a["AssetID"]
            if asset_id in all_records:
                all_records[asset_id]["series"].append(series)
                continue
            all_records[asset_id] = {
                "asset_id": asset_id,
                "series": [series],
                "title": a.get("Title"),
                "description": a.get("Description"),
                "alt_text": a.get("AltText"),
                "comment": a.get("Comment"),
                "rating": a.get("Rating"),
                "constraints_information": a.get("ConstraintsInformation"),
                "copyright": a.get("Copyright"),
                "photo_credit": a.get("PhotoCredit"),
                "keywords": a.get("Keywords"),
                "image_create_date": a.get("ImageCreateDate"),
                "resource_format": a.get("ResourceFormat"),
                "asset_detail_url": f"https://npgallery.nps.gov/AssetDetail/{asset_id}",
                "thumb_url": f"https://npgallery.nps.gov/GetAsset/{asset_id}/proxymdres.jpg",
            }

    print(f"\n{len(all_records)} unique assets across all Scenics-* series. "
          f"Fetching AssetDetail extras (file size, photographer)...")
    for i, (asset_id, rec) in enumerate(all_records.items(), 1):
        try:
            rec.update(fetch_detail_extras(asset_id))
        except Exception as e:  # noqa: BLE001 -- diagnostic script, log and continue
            rec["detail_fetch_error"] = str(e)
        if i % 25 == 0:
            print(f"  {i}/{len(all_records)} detail pages fetched")

    OUT_PATH.write_text(json.dumps(list(all_records.values()), indent=2))
    print(f"\nWrote {len(all_records)} records to {OUT_PATH}")


if __name__ == "__main__":
    main()
