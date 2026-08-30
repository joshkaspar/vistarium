from vistarium.pipeline import resolve_time_of_day


def test_exif_wins_over_caption_when_both_present():
    tod, evidence = resolve_time_of_day(
        caption_text="Golden hour at the overlook",  # would match "evening"
        exif_hour=8,  # morning
        model_time_of_day="afternoon",
        model_evidence="visual_inference",
    )
    assert (tod, evidence) == ("morning", "exif_timestamp")


def test_caption_used_when_no_exif():
    tod, evidence = resolve_time_of_day(
        caption_text="Sunset over the canyon",
        exif_hour=None,
        model_time_of_day="morning",
        model_evidence="visual_inference",
    )
    assert (tod, evidence) == ("evening", "caption")


def test_falls_back_to_model_when_neither_exif_nor_caption():
    tod, evidence = resolve_time_of_day(
        caption_text="A view of the canyon",
        exif_hour=None,
        model_time_of_day="afternoon",
        model_evidence="visual_inference",
    )
    assert (tod, evidence) == ("afternoon", "visual_inference")


def test_regression_dawn_as_a_name_does_not_override_exif():
    # Real bug found in the 2026-08-30 validation checkpoint: "Dawn Marsh"
    # in a photo credit list matched the caption regex's "dawn" keyword,
    # which used to override a correct 11:42 AM EXIF timestamp with an
    # incorrect "morning" bucket. EXIF must win here.
    caption = (
        "Piping Plover Banding on Long Island ... Dawn Marsh, Fish and "
        "Wildlife Biologist, U.S. Fish & Wildlife Service."
    )
    tod, evidence = resolve_time_of_day(
        caption_text=caption,
        exif_hour=11,  # 11:42 AM -> afternoon bucket
        model_time_of_day="morning",
        model_evidence="visual_inference",
    )
    assert (tod, evidence) == ("afternoon", "exif_timestamp")


def test_ambiguous_caption_with_no_exif_falls_back_to_model():
    # caption_time_of_day returns None when multiple buckets match --
    # resolve_time_of_day must fall through to the model in that case too.
    tod, evidence = resolve_time_of_day(
        caption_text="From sunrise to sunset at the overlook",
        exif_hour=None,
        model_time_of_day="afternoon",
        model_evidence="visual_inference",
    )
    assert (tod, evidence) == ("afternoon", "visual_inference")
