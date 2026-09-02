from unittest.mock import patch

from PIL import Image

from vistarium.nps_client import NPSCandidate
from vistarium.pipeline import build_record

FAKE_MODEL_FIELDS = {
    "is_photograph": True,
    "time_of_day": "morning",
    "time_of_day_evidence": "visual_inference",
    "license_confidence": "confirmed",
    "license_evidence": "no rights concerns visible",
    "primary_subject": "landscape",
    "people_present": False,
    "people_prominence": "none",
    "crop_anchor": "center",
    "frame_type": "full_bleed",
    "tags": ["test"],
}


def _fake_image(tmp_path):
    path = tmp_path / "fake.jpg"
    Image.new("RGB", (400, 300), "blue").save(path)
    return path


def test_build_record_includes_aesthetic_score_when_candidate_has_one(tmp_path):
    candidate = NPSCandidate(
        id="a",
        park="Test Park",
        aesthetic_score=6.2,
        aesthetic_method="aesthetics_predictor_v2_l14_linearMSE",
    )
    with patch("vistarium.pipeline.judge_image", return_value=dict(FAKE_MODEL_FIELDS)):
        record = build_record(candidate, _fake_image(tmp_path))

    assert record["aesthetic_score"] == 6.2
    assert record["aesthetic_method"] == "aesthetics_predictor_v2_l14_linearMSE"


def test_build_record_omits_aesthetic_score_for_uncurated_candidates(tmp_path):
    # Non-curated search paths (--term, --park-code, --album-id) never
    # set aesthetic_score on the candidate at all -- the field should
    # stay absent from the record, not show up as an explicit null.
    candidate = NPSCandidate(id="a", park="Test Park")
    with patch("vistarium.pipeline.judge_image", return_value=dict(FAKE_MODEL_FIELDS)):
        record = build_record(candidate, _fake_image(tmp_path))

    assert "aesthetic_score" not in record
    assert "aesthetic_method" not in record
