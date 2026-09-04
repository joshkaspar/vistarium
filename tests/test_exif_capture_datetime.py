from datetime import datetime
from pathlib import Path

from PIL import ExifTags, Image

from vistarium.exif_util import exif_capture_datetime


def _make_image(
    path: Path, *, ifd0_datetime: str | None = None, date_time_original: str | None = None
) -> None:
    img = Image.new("RGB", (10, 10), color="red")
    exif = img.getexif()
    if ifd0_datetime:
        exif[306] = ifd0_datetime
    if date_time_original:
        sub_ifd = exif.get_ifd(ExifTags.IFD.Exif)
        sub_ifd[36867] = date_time_original
    img.save(path, format="JPEG", exif=exif)


def test_returns_minute_precision_not_just_hour(tmp_path: Path):
    # The whole point of this function over exif_capture_hour(): two
    # photos in the same hour, minutes apart, must be distinguishable --
    # that's what burst/near-duplicate detection needs (find_duplicates.py).
    path = tmp_path / "a.jpg"
    _make_image(path, date_time_original="2017:08:23 14:07:21")
    assert exif_capture_datetime(path) == datetime(2017, 8, 23, 14, 7, 21)


def test_date_time_original_wins_over_ifd0_date_time(tmp_path: Path):
    path = tmp_path / "a.jpg"
    _make_image(path, ifd0_datetime="2025:04:02 22:46:39", date_time_original="2017:12:03 07:03:21")
    assert exif_capture_datetime(path) == datetime(2017, 12, 3, 7, 3, 21)


def test_sentinel_date_rejected_even_with_ifd0_fallback(tmp_path: Path):
    path = tmp_path / "a.jpg"
    _make_image(path, date_time_original="2000:01:01 00:00:02", ifd0_datetime="2023:02:17 21:07:57")
    assert exif_capture_datetime(path) is None


def test_returns_none_with_no_exif_at_all(tmp_path: Path):
    path = tmp_path / "a.jpg"
    Image.new("RGB", (10, 10), color="blue").save(path, format="JPEG")
    assert exif_capture_datetime(path) is None


def test_returns_none_for_nonexistent_file(tmp_path: Path):
    assert exif_capture_datetime(tmp_path / "does_not_exist.jpg") is None
