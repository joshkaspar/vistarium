from vistarium.exif_util import caption_time_of_day, hour_to_bucket


def test_caption_detects_morning():
    assert caption_time_of_day("Sunrise over the valley") == "morning"


def test_caption_detects_evening():
    assert caption_time_of_day("Golden hour alpenglow on the peaks, sunset") == "evening"


def test_caption_detects_night():
    assert caption_time_of_day("Milky way and stars over the campsite") == "night"


def test_caption_detects_afternoon():
    assert caption_time_of_day("Hikers at midday on the ridge") == "afternoon"


def test_caption_returns_none_with_no_match():
    assert caption_time_of_day("A view of the canyon") is None


def test_caption_returns_none_when_ambiguous_between_buckets():
    # Contains both a morning and an evening cue -- not confident evidence.
    assert caption_time_of_day("From sunrise to sunset at the overlook") is None


def test_caption_returns_none_for_empty_string():
    assert caption_time_of_day("") is None


def test_hour_to_bucket_boundaries():
    assert hour_to_bucket(0) == "night"
    assert hour_to_bucket(4) == "night"
    assert hour_to_bucket(5) == "morning"
    assert hour_to_bucket(10) == "morning"
    assert hour_to_bucket(11) == "afternoon"
    assert hour_to_bucket(16) == "afternoon"
    assert hour_to_bucket(17) == "evening"
    assert hour_to_bucket(20) == "evening"
    assert hour_to_bucket(21) == "night"
    assert hour_to_bucket(23) == "night"
