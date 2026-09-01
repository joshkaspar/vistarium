from vistarium.pipeline import resolve_time_of_day


def test_exif_wins_over_caption_when_both_present():
    tod, evidence = resolve_time_of_day(
        caption_text="Golden hour at the overlook",  # would match "evening"
        exif_hour=8,  # morning
        model_time_of_day="afternoon",
    )
    assert (tod, evidence) == ("morning", "exif_timestamp")


def test_caption_used_when_no_exif():
    tod, evidence = resolve_time_of_day(
        caption_text="Sunset over the canyon",
        exif_hour=None,
        model_time_of_day="morning",
    )
    assert (tod, evidence) == ("evening", "caption")


def test_falls_back_to_model_when_neither_exif_nor_caption():
    tod, evidence = resolve_time_of_day(
        caption_text="A view of the canyon",
        exif_hour=None,
        model_time_of_day="afternoon",
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
    )
    assert (tod, evidence) == ("afternoon", "exif_timestamp")


def test_ambiguous_caption_with_no_exif_falls_back_to_model():
    # caption_time_of_day returns None when multiple buckets match --
    # resolve_time_of_day must fall through to the model in that case too.
    tod, evidence = resolve_time_of_day(
        caption_text="From sunrise to sunset at the overlook",
        exif_hour=None,
        model_time_of_day="afternoon",
    )
    assert (tod, evidence) == ("afternoon", "visual_inference")


def test_regression_matted_scan_exif_is_not_trusted():
    # Real bug found 2026-08-31 scraping Yosemite: a 1937 archival negative
    # (frame_type="matted", visible mat/border/burned-in caption) had EXIF
    # DateTimeOriginal "2017:06:30 01:10:03" -- the *scan* timestamp, not
    # the capture time -- bucketing an obviously bright daytime photo to
    # "night". EXIF must be skipped entirely for non-full_bleed frame
    # types, falling through to the model's visual read instead.
    tod, evidence = resolve_time_of_day(
        caption_text="Miguel Meadows",
        exif_hour=1,  # would bucket to "night" if trusted
        model_time_of_day="afternoon",
        frame_type="matted",
    )
    assert (tod, evidence) == ("afternoon", "visual_inference")


def test_full_bleed_still_trusts_exif():
    # Default frame_type ("full_bleed") preserves the original behavior --
    # a native digital photo's EXIF is trustworthy.
    tod, evidence = resolve_time_of_day(
        caption_text="A view of the canyon",
        exif_hour=8,
        model_time_of_day="afternoon",
        frame_type="full_bleed",
    )
    assert (tod, evidence) == ("morning", "exif_timestamp")


def test_regression_model_self_reported_evidence_is_never_trusted():
    # Real bug found in the same checkpoint run: the model's grammar lets
    # it emit "caption" or "exif_timestamp" as its own time_of_day_evidence
    # even though it only ever receives pixels, and it did so at least
    # once with no real caption or EXIF data behind the claim. Whatever
    # the model claims for evidence must be ignored -- Claude Code alone
    # decides the evidence label based on what was actually available.
    tod, evidence = resolve_time_of_day(
        caption_text="National Mall & Memorial Parks",  # no time-of-day keyword
        exif_hour=None,
        model_time_of_day="morning",
    )
    assert (tod, evidence) == ("morning", "visual_inference")
