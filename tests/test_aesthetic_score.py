import json
import sys
import types
from unittest.mock import MagicMock, patch

from vistarium.aesthetic_score import AESTHETIC_METHOD, backfill


def _record(rid):
    return {
        "id": rid,
        "source": "nps",
        "source_url": f"https://example.org/{rid}",
        "image_url": f"https://example.org/{rid}/orig",
        "title": f"Title {rid}",
        "photographer": None,
        "date": None,
        "park": "Test Park",
        "license": "Public domain/Full",
        "thumbnail_crop_16x9": {"x": 0, "y": 0, "w": 100, "h": 56},
        "color_mode": "color",
        "is_photograph": True,
        "time_of_day": "morning",
        "time_of_day_evidence": "exif_timestamp",
        "license_confidence": "confirmed",
        "license_evidence": "no rights concerns visible",
        "primary_subject": "landscape",
        "people_present": False,
        "people_prominence": "none",
        "crop_anchor": "center",
        "frame_type": "full_bleed",
        "tags": ["test"],
    }


def test_backfill_only_touches_aesthetic_fields(tmp_path):
    catalog = [_record("a"), _record("b")]
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(catalog))

    images_dir = tmp_path / "images"
    images_dir.mkdir()
    (images_dir / "a.jpg").write_bytes(b"fake")
    (images_dir / "b.jpg").write_bytes(b"fake")

    checkpoint_path = tmp_path / "checkpoint.jsonl"
    checkpoint_path.write_text(
        "\n".join(json.dumps({"id": r["id"], "outcome": "catalog", "record": r}) for r in catalog)
        + "\n"
    )

    with patch(
        "vistarium.aesthetic_score.score_all",
        return_value={"a": 5.5, "b": 4.2},
    ):
        count = backfill(catalog_path, images_dir, checkpoint_path)

    assert count == 2
    updated = {r["id"]: r for r in json.loads(catalog_path.read_text())}
    assert updated["a"]["aesthetic_score"] == 5.5
    assert updated["a"]["aesthetic_method"] == AESTHETIC_METHOD
    assert updated["a"]["title"] == "Title a"
    assert updated["b"]["aesthetic_score"] == 4.2

    checkpoint_lines = [json.loads(line) for line in checkpoint_path.read_text().splitlines()]
    assert checkpoint_lines[0]["record"]["aesthetic_score"] == 5.5


def test_backfill_skips_records_missing_local_image(tmp_path):
    catalog = [_record("missing")]
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(catalog))

    images_dir = tmp_path / "images"
    images_dir.mkdir()
    checkpoint_path = tmp_path / "checkpoint.jsonl"
    checkpoint_path.write_text("")

    with patch("vistarium.aesthetic_score.score_all", return_value={}) as mock_score:
        count = backfill(catalog_path, images_dir, checkpoint_path)

    mock_score.assert_called_once_with([])
    assert count == 0
    assert "aesthetic_score" not in json.loads(catalog_path.read_text())[0]


def _install_fake_torch_stack(cuda_available):
    """Injects fake torch/aesthetics_predictor/transformers modules into
    sys.modules so _load_model() can run (and be inspected) without the
    real (optional, heavy) 'aesthetic' extra installed -- this dev
    environment deliberately doesn't have it (see aesthetic_score.py's
    module docstring)."""
    fake_torch = types.ModuleType("torch")
    fake_torch.cuda = types.SimpleNamespace(is_available=lambda: cuda_available)
    fake_torch.device = lambda name: f"device:{name}"
    fake_torch.no_grad = MagicMock()
    fake_torch.no_grad.return_value.__enter__ = MagicMock()
    fake_torch.no_grad.return_value.__exit__ = MagicMock(return_value=False)

    fake_model = MagicMock()
    fake_model.to.return_value = fake_model
    fake_model.eval.return_value = fake_model

    fake_predictor_cls = MagicMock()
    fake_predictor_cls.from_pretrained.return_value = fake_model
    fake_aesthetics_predictor = types.ModuleType("aesthetics_predictor")
    fake_aesthetics_predictor.AestheticsPredictorV2Linear = fake_predictor_cls

    fake_processor_cls = MagicMock()
    fake_transformers = types.ModuleType("transformers")
    fake_transformers.CLIPProcessor = fake_processor_cls

    return {
        "torch": fake_torch,
        "aesthetics_predictor": fake_aesthetics_predictor,
        "transformers": fake_transformers,
    }, fake_model


def test_load_model_uses_cuda_when_available():
    import vistarium.aesthetic_score as aesthetic_score_module

    aesthetic_score_module._predictor = None
    aesthetic_score_module._processor = None
    aesthetic_score_module._device = None

    fake_modules, fake_model = _install_fake_torch_stack(cuda_available=True)
    with patch.dict(sys.modules, fake_modules):
        _predictor, _processor, device = aesthetic_score_module._load_model()

    assert device == "device:cuda"
    fake_model.to.assert_called_once_with("device:cuda")


def test_load_model_falls_back_to_cpu():
    import vistarium.aesthetic_score as aesthetic_score_module

    aesthetic_score_module._predictor = None
    aesthetic_score_module._processor = None
    aesthetic_score_module._device = None

    fake_modules, fake_model = _install_fake_torch_stack(cuda_available=False)
    with patch.dict(sys.modules, fake_modules):
        _predictor, _processor, device = aesthetic_score_module._load_model()

    assert device == "device:cpu"
    fake_model.to.assert_called_once_with("device:cpu")
