"""Parsing-only tests -- no network calls. Uses a synthetic payload shaped
like the real `var search = {...}` blob nps_client.py extracts from HTML,
not a live fixture, since the real site has no stable public sample data."""

from vistarium.nps_client import asset_to_candidate, extract_payload

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
