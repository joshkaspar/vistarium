"""Deterministic crop-box computation from a coarse model-supplied anchor.

The model reports crop_anchor as one of 5 coarse directions (center/top/
bottom/left/right), not pixel coordinates -- see DECISIONS.md, 2026-08-29,
for why (VLMs are unreliable at precise spatial grounding; a 9-way variant
with corners was tested and rejected for tracking bright points rather than
the actual subject). All exact pixel math happens here, deterministically,
from the image's real dimensions.
"""

from __future__ import annotations

CropBox = dict[str, int]

_VALID_ANCHORS = {"center", "top", "bottom", "left", "right"}


def crop_box(img_w: int, img_h: int, target_w: int, target_h: int, anchor: str) -> CropBox:
    """Compute a crop box (x, y, w, h) from `img_w`x`img_h` that matches the
    target_w:target_h aspect ratio, biased toward `anchor`.

    Only one axis is ever cropped -- whichever axis the source image has
    "too much" of relative to the target ratio -- and the full extent of
    the other axis is always kept. `anchor` only takes effect on the axis
    actually being cropped; an anchor naming the other axis (e.g. "left"
    when the crop is vertical) has nothing to act on and is treated as
    "center" for that axis, matching how it behaves in practice.
    """
    if anchor not in _VALID_ANCHORS:
        raise ValueError(
            f"unknown crop_anchor {anchor!r}, expected one of {sorted(_VALID_ANCHORS)}"
        )
    if img_w <= 0 or img_h <= 0 or target_w <= 0 or target_h <= 0:
        raise ValueError("widths/heights must be positive")

    source_ratio = img_w / img_h
    target_ratio = target_w / target_h

    if abs(source_ratio - target_ratio) < 1e-9:
        return {"x": 0, "y": 0, "w": img_w, "h": img_h}

    if source_ratio > target_ratio:
        # Source is wider than the target ratio -- crop width, keep full height.
        crop_w = round(img_h * target_ratio)
        crop_w = min(crop_w, img_w)
        if anchor == "left":
            x = 0
        elif anchor == "right":
            x = img_w - crop_w
        else:
            x = (img_w - crop_w) // 2
        return {"x": x, "y": 0, "w": crop_w, "h": img_h}
    else:
        # Source is taller/narrower than the target ratio -- crop height, keep full width.
        crop_h = round(img_w / target_ratio)
        crop_h = min(crop_h, img_h)
        if anchor == "top":
            y = 0
        elif anchor == "bottom":
            y = img_h - crop_h
        else:
            y = (img_h - crop_h) // 2
        return {"x": 0, "y": y, "w": img_w, "h": crop_h}


def crop_16x9(img_w: int, img_h: int, anchor: str) -> CropBox:
    """Convenience wrapper for the site's 16:9 preview thumbnail."""
    return crop_box(img_w, img_h, 16, 9, anchor)


def crop_9x16(img_w: int, img_h: int, anchor: str) -> CropBox:
    """Convenience wrapper for the site's 9:16 preview thumbnail, used for
    portrait-original images -- see DECISIONS.md, 2026-08-31. Forcing a
    16:9 crop on a portrait photo can throw away most of the frame; this
    keeps the native orientation instead."""
    return crop_box(img_w, img_h, 9, 16, anchor)
