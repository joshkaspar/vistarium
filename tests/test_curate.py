import json
from unittest.mock import patch

from vistarium.curate import select_by_threshold_with_floor, select_candidates_for_park
from vistarium.nps_client import AlbumInfo, NPSCandidate


def _cand(id_, park, score=None):
    return NPSCandidate(id=id_, park=park)


def test_threshold_only_keeps_everything_above_it():
    scored = [(_cand("a", "Park A"), 6.0), (_cand("b", "Park A"), 5.5), (_cand("c", "Park A"), 4.0)]
    selected = select_by_threshold_with_floor(scored, threshold=5.0, floor=1)
    assert {c.id for c in selected} == {"a", "b"}


def test_floor_tops_up_when_threshold_leaves_too_few():
    # Only "a" clears the 6.5 threshold, but floor=3 requires topping up
    # with the next-highest scorers regardless of threshold.
    scored = [
        (_cand("a", "Park A"), 7.0),
        (_cand("b", "Park A"), 5.0),
        (_cand("c", "Park A"), 4.0),
        (_cand("d", "Park A"), 1.0),
    ]
    selected = select_by_threshold_with_floor(scored, threshold=6.5, floor=3)
    assert {c.id for c in selected} == {"a", "b", "c"}


def test_floor_never_excludes_a_park_outright_even_with_a_small_pool():
    # Park B only has 2 candidates total, both below threshold -- floor
    # still returns what's available rather than dropping the park.
    scored = [(_cand("x", "Park B"), 2.0), (_cand("y", "Park B"), 1.5)]
    selected = select_by_threshold_with_floor(scored, threshold=6.0, floor=10)
    assert {c.id for c in selected} == {"x", "y"}


def test_floor_and_threshold_apply_independently_per_park():
    scored = [
        (_cand("a", "Park A"), 6.0),
        (_cand("b", "Park A"), 5.9),
        (_cand("c", "Park B"), 1.0),
        (_cand("d", "Park B"), 0.9),
    ]
    selected = select_by_threshold_with_floor(scored, threshold=5.5, floor=1)
    ids = {c.id for c in selected}
    assert "a" in ids and "b" in ids  # Park A: both clear threshold
    assert "c" in ids  # Park B: floor=1 tops up its top scorer
    assert "d" not in ids


def test_select_candidates_for_park_wires_triage_thumbnail_score_select(tmp_path):
    albums = [
        AlbumInfo(id="alb-good", title="Cadillac Mountain", description="", asset_count=2),
        AlbumInfo(id="alb-bad", title="Access: Parking Lot", description="", asset_count=2),
    ]
    good_candidates = [NPSCandidate(id="keep-1", park="Test Park")]

    def fake_download_thumbnail(candidate, dest_dir):
        dest_dir.mkdir(parents=True, exist_ok=True)
        p = dest_dir / f"{candidate.id}.jpg"
        p.write_bytes(b"fake")
        return p

    keywords_path = tmp_path / "album_keywords.json"
    keywords_path.write_text(json.dumps({"exclude": ["access:"], "include": ["cadillac"]}))

    with (
        patch("vistarium.curate.nps_client.list_park_albums", return_value=albums),
        patch("vistarium.curate.nps_client.search_album") as mock_search_album,
        patch(
            "vistarium.curate.nps_client.download_thumbnail", side_effect=fake_download_thumbnail
        ),
        patch("vistarium.curate.aesthetic_score.score_all", return_value={"keep-1": 7.0}),
    ):
        mock_search_album.return_value = good_candidates
        result = select_candidates_for_park(
            "TEST", tmp_path, threshold=5.0, floor=1, keywords_path=keywords_path
        )

    # Only the surviving (non-excluded) album's contents were fetched.
    mock_search_album.assert_called_once_with("alb-good", park_code="TEST")
    assert [c.id for c in result] == ["keep-1"]
