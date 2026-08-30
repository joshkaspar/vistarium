from pathlib import Path

from PIL import ExifTags, Image

from vistarium.exif_util import exif_capture_hour


def _make_image(
    path: Path, *, ifd0_datetime: str | None = None, date_time_original: str | None = None
) -> None:
    img = Image.new("RGB", (10, 10), color="red")
    exif = img.getexif()
    if ifd0_datetime:
        exif[306] = ifd0_datetime  # IFD0 DateTime -- file-modified time, not capture time
    if date_time_original:
        sub_ifd = exif.get_ifd(ExifTags.IFD.Exif)
        sub_ifd[36867] = date_time_original  # DateTimeOriginal
    img.save(path, format="JPEG", exif=exif)


def test_date_time_original_wins_over_ifd0_date_time(tmp_path: Path):
    # Regression: found live in the 2026-08-30 validation checkpoint. A
    # Photoshop re-save can rewrite IFD0's DateTime to the edit date while
    # DateTimeOriginal (in the Exif SubIFD) still holds the true capture
    # time. A photo genuinely taken at 07:03 (morning) had IFD0 DateTime
    # showing 22:46 (night) from a 2025 re-save -- must not use that.
    path = tmp_path / "a.jpg"
    _make_image(path, ifd0_datetime="2025:04:02 22:46:39", date_time_original="2017:12:03 07:03:21")
    assert exif_capture_hour(path) == 7


def test_falls_back_to_ifd0_date_time_when_no_original(tmp_path: Path):
    path = tmp_path / "a.jpg"
    _make_image(path, ifd0_datetime="2025:04:02 22:46:39")
    assert exif_capture_hour(path) == 22


def test_returns_none_with_no_exif_at_all(tmp_path: Path):
    path = tmp_path / "a.jpg"
    Image.new("RGB", (10, 10), color="blue").save(path, format="JPEG")
    assert exif_capture_hour(path) is None


def test_returns_none_for_nonexistent_file(tmp_path: Path):
    assert exif_capture_hour(tmp_path / "does_not_exist.jpg") is None
