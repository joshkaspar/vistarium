from vistarium.album_triage import classify_album, load_keywords
from vistarium.nps_client import AlbumInfo

KEYWORDS = {
    "exclude": ["access:", "parking", "meeting", "staff"],
    "include": ["panoramic", "scenic", "sunset", "summit"],
}


def _album(title, description=""):
    return AlbumInfo(id="x", title=title, description=description, asset_count=1)


def test_exclude_term_in_title():
    assert classify_album(_album("Access: Hemlock Path"), KEYWORDS) == "exclude"


def test_exclude_term_in_description():
    album = _album("Duck Brook Bridge", "Duck Brook Bridge Parking")
    assert classify_album(album, KEYWORDS) == "exclude"


def test_include_term_in_title():
    assert classify_album(_album("Cadillac Mountain"), KEYWORDS) == "ambiguous"
    assert classify_album(_album("Panoramic Images of Acadia"), KEYWORDS) == "include"


def test_include_term_in_description():
    album = _album("Views of Acadia", "sunset over the coastline")
    assert classify_album(album, KEYWORDS) == "include"


def test_ambiguous_when_neither_matches():
    assert classify_album(_album("Jordan Pond"), KEYWORDS) == "ambiguous"


def test_exclude_wins_over_include_when_both_match():
    # "Access: ..." accessibility documentation, not a real scenic album,
    # even though "summit" also appears.
    album = _album("Access: Summit Trailhead")
    assert classify_album(album, KEYWORDS) == "exclude"


def test_matching_is_case_insensitive():
    assert classify_album(_album("SUNSET Point"), KEYWORDS) == "include"
    assert classify_album(_album("staff Meeting"), KEYWORDS) == "exclude"


def test_load_keywords_reads_real_config_file():
    keywords = load_keywords()
    assert "exclude" in keywords
    assert "include" in keywords
    assert "parking" in keywords["exclude"]
    assert "scenic" in keywords["include"]
