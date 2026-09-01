import json

from PIL import Image

from vistarium.build_site import build_site


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
