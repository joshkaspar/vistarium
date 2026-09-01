"""Parsing-only tests -- no network calls. Uses a synthetic payload shaped
like the real `var search = {...}` blob nps_client.py extracts from HTML,
not a live fixture, since the real site has no stable public sample data."""

from unittest.mock import patch

from vistarium.nps_client import (
    asset_to_candidate,
    extract_payload,
    fetch_unit_codes,
    list_park_albums,
    search_album,
    search_park_scenic,
)

SAMPLE_HTML = """
<html><body>
<script>
var search = {"SearchID": "abc-123", "PageCount": 2, "PageSize": 48, "ResultCount": 90,
  "Results": [{"Asset": {"AssetID": "111"}}]};
</script>
</body></html>
"""

SAMPLE_ASSET = {
    "AssetID": "111",
    "Title": "Sunset at Crater Lake",
    "AltText": "A red sunset over a still lake",
    "Description": "",
    "Keywords": ["lake", "sunset"],
    "PhotoCredit": "NPS / John Smith",
    "ImageCreateDate": {"Date": "2019-08-15"},
    "ImageCreateDateTime": "08/15/2019 07:45:00 PM",
    "ConstraintsInformation": {"Constraint": "Public Domain", "GrantingRights": "NPS"},
    "NPSUnits": [{"Name": "Crater Lake National Park"}],
}


def test_extract_payload_finds_embedded_json():
    payload = extract_payload(SAMPLE_HTML)
    assert payload is not None
    assert payload["SearchID"] == "abc-123"
    assert payload["ResultCount"] == 90
    assert payload["Results"][0]["Asset"]["AssetID"] == "111"


def test_extract_payload_returns_none_without_pagecount():
    assert extract_payload("<html>no results here</html>") is None


def test_asset_to_candidate_maps_fields():
    cand = asset_to_candidate(SAMPLE_ASSET, term="sunset")
    assert cand.id == "111"
    assert cand.source == "nps"
    assert cand.image_url.endswith("/GetAsset/111/Original")
    assert cand.source_url.endswith("/AssetDetail/111")
    assert cand.title == "Sunset at Crater Lake"
    assert cand.photographer == "NPS / John Smith"
    assert cand.date == "2019-08-15"
    assert cand.park == "Crater Lake National Park"
    assert cand.license == "Public Domain/NPS"
    assert "sunset" in cand.caption_text.lower()
    assert cand.search_terms == ["sunset"]


def test_asset_to_candidate_handles_missing_optional_fields():
    minimal = {"AssetID": "222"}
    cand = asset_to_candidate(minimal, term="forest")
    assert cand.id == "222"
    assert cand.title == ""
    assert cand.photographer is None
    assert cand.park == ""
    assert cand.license == ""


MULTI_UNIT_ASSET = {
    "AssetID": "333",
    "Title": "Holy Cross",
    "NPSUnits": [
        {"Name": "Devils Tower National Monument", "Code": "DETO"},
        {"Name": "Grand Canyon National Park", "Code": "GRCA"},
        {"Name": "Grand Teton National Park", "Code": "GRTE"},
        {"Name": "Museum Management Program", "Code": "MMP"},
    ],
}


def test_asset_to_candidate_prefers_matching_unit_over_first():
    # Real bug found live 2026-09-01: a shared historical asset cross-
    # tagged under 4 NPS units, searched via Units:GRTE -- units[0] was
    # Devils Tower, not the park actually searched for.
    cand = asset_to_candidate(MULTI_UNIT_ASSET, term="scenic:GRTE", park_code="GRTE")
    assert cand.park == "Grand Teton National Park"


def test_asset_to_candidate_falls_back_to_first_unit_without_park_code():
    cand = asset_to_candidate(MULTI_UNIT_ASSET, term="holy cross")
    assert cand.park == "Devils Tower National Monument"


def test_asset_to_candidate_falls_back_when_park_code_not_listed():
    cand = asset_to_candidate(MULTI_UNIT_ASSET, term="scenic:ZION", park_code="ZION")
    assert cand.park == "Devils Tower National Monument"


def _html(payload_json: str) -> str:
    return f"<html><body><script>var search = {payload_json};</script></body></html>"


def test_search_park_scenic_paginates_and_dedupes():
    # Page 1 has 2 assets, page 2 has 1 new + 1 repeat of page 1's first --
    # real NPGallery pages can overlap slightly; must dedupe by AssetID.
    page1 = _html(
        '{"SearchID": "sid-1", "PageCount": 2, "PageSize": 2, "ResultCount": 3, '
        '"Results": [{"Asset": {"AssetID": "a"}}, {"Asset": {"AssetID": "b"}}]}'
    )
    page2 = _html(
        '{"SearchID": "sid-1", "PageCount": 2, "PageSize": 2, "ResultCount": 3, '
        '"Results": [{"Asset": {"AssetID": "b"}}, {"Asset": {"AssetID": "c"}}]}'
    )

    def fake_http_get(url):
        if "page=2" in url:
            return page2
        return page1

    with patch("vistarium.nps_client._http_get", side_effect=fake_http_get):
        results = search_park_scenic("ACAD")

    assert {c.id for c in results} == {"a", "b", "c"}
    assert all(c.search_terms == ["scenic:ACAD"] for c in results)


def test_search_park_scenic_returns_empty_when_no_payload():
    with patch("vistarium.nps_client._http_get", return_value="<html>no results</html>"):
        assert search_park_scenic("ZZZZ") == []


def test_search_park_scenic_respects_max_pages():
    page1 = _html(
        '{"SearchID": "sid-1", "PageCount": 5, "PageSize": 1, "ResultCount": 5, '
        '"Results": [{"Asset": {"AssetID": "a"}}]}'
    )
    calls = []

    def fake_http_get(url):
        calls.append(url)
        return page1

    with patch("vistarium.nps_client._http_get", side_effect=fake_http_get):
        search_park_scenic("ACAD", max_pages=2)

    # 1 probe request + at most 1 more page (max_pages=2, capped below the
    # 5 pages ResultCount/PageSize would otherwise imply).
    assert len(calls) == 2


def test_fetch_unit_codes_parses_units_facet():
    payload = _html(
        '{"SearchID": "sid-1", "PageCount": 1, "Filters": ['
        '{"Term": "Categories", "Items": [{"Attribute": "Scenic"}]}, '
        '{"Term": "Units", "Items": ['
        '{"Attribute": "ACAD", "DisplayName": "Acadia National Park"}, '
        '{"Attribute": "GRCA", "DisplayName": "Grand Canyon National Park"}'
        "]}]}"
    )
    with patch("vistarium.nps_client._http_get", return_value=payload):
        codes = fetch_unit_codes()

    assert codes["Acadia National Park"] == "ACAD"
    assert codes["Grand Canyon National Park"] == "GRCA"


def test_fetch_unit_codes_empty_when_no_units_facet():
    payload = _html('{"SearchID": "sid-1", "PageCount": 1, "Filters": []}')
    with patch("vistarium.nps_client._http_get", return_value=payload):
        assert fetch_unit_codes() == {}


def test_list_park_albums_parses_album_metadata():
    payload = _html(
        '{"SearchID": "sid-1", "PageCount": 1, "PageSize": 500, "ResultCount": 2, '
        '"Results": ['
        '{"Asset": {"AssetID": "alb-1", "Title": "Night Skies", '
        '"Description": "Milky Way photos", "AssetCount": 12}}, '
        '{"Asset": {"AssetID": "alb-2", "Title": "Staff Meeting", '
        '"Description": "2025 gathering", "AssetCount": 5}}'
        "]}"
    )
    with patch("vistarium.nps_client._http_get", return_value=payload):
        albums = list_park_albums("ACAD")

    assert len(albums) == 2
    assert albums[0].id == "alb-1"
    assert albums[0].title == "Night Skies"
    assert albums[0].asset_count == 12


def test_list_park_albums_empty_when_no_payload():
    with patch("vistarium.nps_client._http_get", return_value="<html>no results</html>"):
        assert list_park_albums("ZZZZ") == []


def test_search_album_parses_direct_json_response():
    # Different endpoint shape than SearchResults pages -- a bare JSON
    # response, not HTML with an embedded `var search = {...}` payload.
    raw_json = (
        '{"AlbumID": "alb-1", "Results": ['
        '{"Asset": {"AssetID": "x", "Title": "Winter Atop Cadillac", '
        '"NPSUnits": [{"Name": "Acadia National Park", "Code": "ACAD"}]}}'
        "]}"
    )
    with patch("vistarium.nps_client._http_get", return_value=raw_json):
        results = search_album("alb-1", park_code="ACAD")

    assert len(results) == 1
    assert results[0].id == "x"
    assert results[0].title == "Winter Atop Cadillac"
    assert results[0].park == "Acadia National Park"
    assert results[0].search_terms == ["album:alb-1"]


def test_search_album_returns_empty_on_bad_json():
    with patch("vistarium.nps_client._http_get", return_value="not json"):
        assert search_album("alb-1") == []
