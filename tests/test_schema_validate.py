import copy

import jsonschema
import pytest

from vistarium.schema_validate import is_valid, validate_record

VALID_RECORD = {
    "id": "abc-123",
    "source": "nps",
    "source_url": "https://npgallery.nps.gov/AssetDetail/abc-123",
    "image_url": "https://npgallery.nps.gov/GetAsset/abc-123/Original",
    "title": "Sunrise over the valley",
    "photographer": "NPS / Jane Doe",
    "date": "2020-06-01",
    "park": "Yosemite National Park",
    "license": "Public Domain/NPS",
    "is_photograph": True,
    "time_of_day": "morning",
    "time_of_day_evidence": "caption",
    "license_confidence": "confirmed",
    "license_evidence": "no rights concerns visible",
    "primary_subject": "landscape",
    "people_present": False,
    "people_prominence": "none",
    "crop_anchor": "center",
    "frame_type": "full_bleed",
    "tags": ["valley", "sunrise", "mountains"],
    "thumbnail_crop_16x9": {"x": 0, "y": 100, "w": 1600, "h": 900},
    "color_mode": "color",
}


def test_valid_record_passes():
    validate_record(VALID_RECORD)  # should not raise
    assert is_valid(VALID_RECORD)


def test_missing_required_field_fails():
    record = copy.deepcopy(VALID_RECORD)
    del record["is_photograph"]
    assert not is_valid(record)
    with pytest.raises(jsonschema.ValidationError):
        validate_record(record)


def test_bad_enum_value_fails():
    record = copy.deepcopy(VALID_RECORD)
    record["time_of_day"] = "teatime"
    assert not is_valid(record)


def test_document_primary_subject_accepted():
    # Added 2026-08-30 for genuine photographs of document-like things
    # (newspaper pages, museum placards, signs, maps, screenshots) --
    # distinct from is_photograph=false, which is for non-photographic
    # media regardless of subject. See DECISIONS.md.
    record = copy.deepcopy(VALID_RECORD)
    record["primary_subject"] = "document"
    assert is_valid(record)


def test_detail_primary_subject_accepted():
    # Added 2026-08-31 for close-up/macro shots of a small piece of the
    # environment (moss, scat, bark texture) with no scenic composition --
    # distinct from "landscape" (a scene/vista). See DECISIONS.md.
    record = copy.deepcopy(VALID_RECORD)
    record["primary_subject"] = "detail"
    assert is_valid(record)


def test_monochrome_color_mode_accepted():
    record = copy.deepcopy(VALID_RECORD)
    record["color_mode"] = "monochrome"
    assert is_valid(record)


def test_missing_color_mode_rejected():
    record = copy.deepcopy(VALID_RECORD)
    del record["color_mode"]
    assert not is_valid(record)


def test_missing_aesthetic_fields_still_valid():
    # aesthetic_score/aesthetic_method are deliberately optional -- a
    # freshly-scraped record won't have them until the separate
    # vistarium-score-aesthetics post-process stage runs.
    assert "aesthetic_score" not in VALID_RECORD
    assert is_valid(VALID_RECORD)


def test_aesthetic_score_and_method_accepted():
    record = copy.deepcopy(VALID_RECORD)
    record["aesthetic_score"] = 5.234
    record["aesthetic_method"] = "laion_predictor_v2"
    assert is_valid(record)


def test_bad_aesthetic_method_rejected():
    record = copy.deepcopy(VALID_RECORD)
    record["aesthetic_score"] = 5.234
    record["aesthetic_method"] = "vibes"
    assert not is_valid(record)


def test_missing_dominant_color_still_valid():
    # Deliberately optional for now -- added to model_client.py's grammar
    # mid-scrape, so older/in-flight records won't have it yet. Will be
    # promoted to required once the corpus is backfilled. See DECISIONS.md.
    assert "dominant_color" not in VALID_RECORD
    assert is_valid(VALID_RECORD)


def test_dominant_color_accepted():
    record = copy.deepcopy(VALID_RECORD)
    record["dominant_color"] = "red"
    assert is_valid(record)


def test_bad_dominant_color_rejected():
    record = copy.deepcopy(VALID_RECORD)
    record["dominant_color"] = "cyan"
    assert not is_valid(record)


def test_corner_anchor_rejected_by_schema():
    # The 9-way corner scheme was tested and rejected -- schema.json should
    # still only accept the 5-way set even if a caller tries to sneak one in.
    record = copy.deepcopy(VALID_RECORD)
    record["crop_anchor"] = "topleft"
    assert not is_valid(record)


def test_additional_property_rejected():
    record = copy.deepcopy(VALID_RECORD)
    record["extra_field"] = "not allowed"
    assert not is_valid(record)


def test_photographer_and_date_accept_null():
    record = copy.deepcopy(VALID_RECORD)
    record["photographer"] = None
    record["date"] = None
    assert is_valid(record)


def test_thumbnail_crop_requires_all_box_fields():
    record = copy.deepcopy(VALID_RECORD)
    del record["thumbnail_crop_16x9"]["h"]
    assert not is_valid(record)
