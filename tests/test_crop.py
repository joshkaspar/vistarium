import pytest

from vistarium.crop import crop_16x9, crop_box


def test_exact_ratio_match_returns_full_image():
    box = crop_box(1600, 900, 16, 9, "center")
    assert box == {"x": 0, "y": 0, "w": 1600, "h": 900}


def test_wide_source_crops_width_centered():
    # 2:1 source, target 16:9 (~1.78) -- narrower than source, so width is cropped.
    box = crop_box(2000, 1000, 16, 9, "center")
    assert box["y"] == 0
    assert box["h"] == 1000
    expected_w = round(1000 * 16 / 9)
    assert box["w"] == expected_w
    assert box["x"] == (2000 - expected_w) // 2


def test_wide_source_left_anchor_hugs_left_edge():
    box = crop_box(2000, 1000, 16, 9, "left")
    assert box["x"] == 0


def test_wide_source_right_anchor_hugs_right_edge():
    box = crop_box(2000, 1000, 16, 9, "right")
    assert box["x"] == 2000 - box["w"]


def test_wide_source_top_bottom_anchor_has_no_effect_on_horizontal_crop():
    # top/bottom name the vertical axis, which isn't the one being cropped
    # here (full height is kept) -- should behave identically to center.
    box_top = crop_box(2000, 1000, 16, 9, "top")
    box_center = crop_box(2000, 1000, 16, 9, "center")
    assert box_top == box_center


def test_tall_source_crops_height_centered():
    # Portrait source, target 16:9 landscape -- height must be cropped.
    box = crop_box(1000, 2000, 16, 9, "center")
    assert box["x"] == 0
    assert box["w"] == 1000
    expected_h = round(1000 / (16 / 9))
    assert box["h"] == expected_h
    assert box["y"] == (2000 - expected_h) // 2


def test_tall_source_top_anchor_hugs_top_edge():
    box = crop_box(1000, 2000, 16, 9, "top")
    assert box["y"] == 0


def test_tall_source_bottom_anchor_hugs_bottom_edge():
    box = crop_box(1000, 2000, 16, 9, "bottom")
    assert box["y"] == 2000 - box["h"]


def test_tall_source_left_right_anchor_has_no_effect_on_vertical_crop():
    box_left = crop_box(1000, 2000, 16, 9, "left")
    box_center = crop_box(1000, 2000, 16, 9, "center")
    assert box_left == box_center


def test_crop_16x9_convenience_wrapper_matches_crop_box():
    assert crop_16x9(2000, 1000, "left") == crop_box(2000, 1000, 16, 9, "left")


def test_unknown_anchor_raises():
    with pytest.raises(ValueError):
        crop_box(2000, 1000, 16, 9, "topleft")


@pytest.mark.parametrize(
    "w,h,tw,th",
    [(0, 100, 16, 9), (100, 0, 16, 9), (100, 100, 0, 9), (100, 100, 16, 0)],
)
def test_non_positive_dimensions_raise(w, h, tw, th):
    with pytest.raises(ValueError):
        crop_box(w, h, tw, th, "center")


def test_crop_box_never_exceeds_source_bounds():
    box = crop_box(4000, 3000, 9, 16, "top")  # extreme portrait target from a landscape source
    assert box["x"] + box["w"] <= 4000
    assert box["y"] + box["h"] <= 3000
