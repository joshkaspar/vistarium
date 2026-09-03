import json

from PIL import Image

from vistarium.build_site import _date_sortable, _is_360_panorama, build_site


def _record(rid, subject="landscape", anchor="center"):
    return {
        "id": rid,
        "source": "nps",
        "source_url": f"https://example.org/{rid}",
        "image_url": f"https://example.org/{rid}/orig",
        "title": f"Title {rid}",
        "photographer": "Someone",
        "date": "01/01/2024",
        "park": "Test Park",
        "license": "Public domain/Full",
        "thumbnail_crop_16x9": {"x": 0, "y": 0, "w": 100, "h": 56},
        "color_mode": "color",
        "dominant_color": "green",
        "is_photograph": True,
        "time_of_day": "morning",
        "time_of_day_evidence": "exif_timestamp",
        "license_confidence": "confirmed",
        "license_evidence": "no rights concerns visible",
        "primary_subject": subject,
        "people_present": False,
        "people_prominence": "none",
        "crop_anchor": anchor,
        "frame_type": "full_bleed",
        "tags": ["test"],
        "aesthetic_score": 6.0,  # comfortably clears PUBLISH_MIN_AESTHETIC_SCORE by default
        "aesthetic_method": "aesthetics_predictor_v2_l14_linearMSE",
    }


def test_build_site_filters_to_landscape_and_writes_thumbs(tmp_path):
    catalog = [_record("land-1", "landscape"), _record("wild-1", "wildlife")]
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(catalog))

    images_dir = tmp_path / "images"
    images_dir.mkdir()
    Image.new("RGB", (400, 300), "red").save(images_dir / "land-1.jpg")
    Image.new("RGB", (400, 300), "blue").save(images_dir / "wild-1.jpg")

    out_dir = tmp_path / "docs"
    count = build_site(catalog_path, images_dir, out_dir)

    assert count == 1
    data = json.loads((out_dir / "data.json").read_text())
    assert len(data) == 1
    assert data[0]["id"] == "land-1"
    assert (out_dir / "thumbs" / "land-1.webp").exists()
    assert not (out_dir / "thumbs" / "wild-1.webp").exists()


def test_build_site_uses_portrait_thumbnail_for_portrait_originals(tmp_path):
    catalog = [_record("land-1", "landscape"), _record("port-1", "landscape")]
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(catalog))

    images_dir = tmp_path / "images"
    images_dir.mkdir()
    Image.new("RGB", (400, 300), "red").save(images_dir / "land-1.jpg")
    Image.new("RGB", (300, 400), "green").save(images_dir / "port-1.jpg")

    out_dir = tmp_path / "docs"
    build_site(catalog_path, images_dir, out_dir)

    data = {r["id"]: r for r in json.loads((out_dir / "data.json").read_text())}
    assert data["land-1"]["aspect"] == "16/9"
    assert data["port-1"]["aspect"] == "9/16"

    with Image.open(out_dir / "thumbs" / "port-1.webp") as im:
        assert im.height > im.width


def test_date_sortable_parses_source_format():
    assert _date_sortable("03/20/2022") == "2022-03-20"


def test_date_sortable_none_for_missing_date():
    assert _date_sortable(None) is None


def test_date_sortable_none_for_unparseable_date():
    assert _date_sortable("not a date") is None


def test_build_site_includes_image_url(tmp_path):
    # docs/data.json is what scripts/other tools actually read -- the
    # full-res original's direct URL was sitting in data/catalog.json
    # all along but never exposed here, forcing anyone scripting
    # against the site to click through source_url by hand to find it.
    catalog = [_record("land-1", "landscape")]
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(catalog))

    images_dir = tmp_path / "images"
    images_dir.mkdir()
    Image.new("RGB", (400, 300), "red").save(images_dir / "land-1.jpg")

    out_dir = tmp_path / "docs"
    build_site(catalog_path, images_dir, out_dir)

    data = json.loads((out_dir / "data.json").read_text())
    assert data[0]["image_url"] == "https://example.org/land-1/orig"


def test_build_site_includes_aesthetic_score_and_date_sortable(tmp_path):
    catalog = [_record("land-1", "landscape")]
    catalog[0]["aesthetic_score"] = 5.42
    catalog[0]["aesthetic_method"] = "laion_predictor_v2"
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(catalog))

    images_dir = tmp_path / "images"
    images_dir.mkdir()
    Image.new("RGB", (400, 300), "red").save(images_dir / "land-1.jpg")

    out_dir = tmp_path / "docs"
    build_site(catalog_path, images_dir, out_dir)

    data = json.loads((out_dir / "data.json").read_text())
    assert data[0]["aesthetic_score"] == 5.42
    assert data[0]["date_sortable"] == "2024-01-01"


def test_build_site_excludes_records_with_no_aesthetic_score(tmp_path):
    # Display-only gate, not a deletion (see PUBLISH_MIN_AESTHETIC_SCORE's
    # docstring) -- a record with no score at all can't be verified
    # against the threshold, so it's treated as not meeting it.
    catalog = [_record("land-1", "landscape")]
    del catalog[0]["aesthetic_score"]
    del catalog[0]["aesthetic_method"]
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(catalog))

    images_dir = tmp_path / "images"
    images_dir.mkdir()
    Image.new("RGB", (400, 300), "red").save(images_dir / "land-1.jpg")

    out_dir = tmp_path / "docs"
    count = build_site(catalog_path, images_dir, out_dir)

    assert count == 0
    assert json.loads((out_dir / "data.json").read_text()) == []


def test_build_site_excludes_records_below_the_aesthetic_threshold(tmp_path):
    catalog = [_record("good-1", "landscape"), _record("bad-1", "landscape")]
    catalog[1]["aesthetic_score"] = 5.39  # just under PUBLISH_MIN_AESTHETIC_SCORE
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(catalog))

    images_dir = tmp_path / "images"
    images_dir.mkdir()
    Image.new("RGB", (400, 300), "red").save(images_dir / "good-1.jpg")
    Image.new("RGB", (400, 300), "blue").save(images_dir / "bad-1.jpg")

    out_dir = tmp_path / "docs"
    count = build_site(catalog_path, images_dir, out_dir)

    assert count == 1
    data = json.loads((out_dir / "data.json").read_text())
    assert data[0]["id"] == "good-1"


def test_build_site_skips_records_missing_local_image(tmp_path):
    catalog = [_record("missing-1", "landscape")]
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(catalog))

    images_dir = tmp_path / "images"
    images_dir.mkdir()

    out_dir = tmp_path / "docs"
    count = build_site(catalog_path, images_dir, out_dir)

    assert count == 0
    assert json.loads((out_dir / "data.json").read_text()) == []


def test_is_360_panorama_detects_nps_titling_conventions():
    assert _is_360_panorama("Acadia National Park (360 photo)")
    assert _is_360_panorama("Acadia National Park (360 image)")
    assert _is_360_panorama("Acadia National Park (360 degrees)")
    assert _is_360_panorama("Grand Canyon Lodge Sun Room - 360 ° Panorama")
    assert not _is_360_panorama("Wood's Ridge")
    assert not _is_360_panorama("Grand Canyon National Park Winter Storm")


def test_build_site_excludes_360_panoramas(tmp_path):
    catalog = [
        _record("normal-1", "landscape"),
        _record("pano-1", "landscape"),
    ]
    catalog[1]["title"] = "Acadia National Park (360 photo)"
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(catalog))

    images_dir = tmp_path / "images"
    images_dir.mkdir()
    Image.new("RGB", (400, 300), "red").save(images_dir / "normal-1.jpg")
    Image.new("RGB", (400, 300), "blue").save(images_dir / "pano-1.jpg")

    out_dir = tmp_path / "docs"
    count = build_site(catalog_path, images_dir, out_dir)

    assert count == 1
    data = json.loads((out_dir / "data.json").read_text())
    assert data[0]["id"] == "normal-1"
