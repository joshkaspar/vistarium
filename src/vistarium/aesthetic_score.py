"""Batched aesthetic scoring via the LAION aesthetics predictor v2.

A separate stage from the pipeline's per-image judgment call in
model_client.py, deliberately: different stack (CLIP/torch via
transformers, not the GBNF-constrained llama.cpp call), different
purpose (ranking a large corpus, not structured per-field judgment).
Torch/transformers are a heavy (~1-2GB) optional dependency -- see
pyproject.toml's "aesthetic" extra -- not part of the core pipeline.

In practice this runs on wopr (CPU is plenty; ~1s/image batched), not
the dev VM, which doesn't have the disk headroom for the extra. See
DECISIONS.md, 2026-09-01, for the pilot that validated this against a
tricky test set (archival scans, heavily desaturated color photos)
before two rejected deterministic (pixel-statistics) alternatives.

Score is stored raw (not percentile-normalized) -- sorting by the raw
float and sorting by percentile-within-corpus produce the identical
order, and percentile only matters if the number is ever displayed or
bucketed, which the site deliberately doesn't do (see README).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

MODEL_ID = "shunk031/aesthetics-predictor-v2-sac-logos-ava1-l14-linearMSE"
AESTHETIC_METHOD = "aesthetics_predictor_v2_l14_linearMSE"
BATCH_SIZE = 16

_predictor = None
_processor = None
_device = None


def _load_model():
    global _predictor, _processor, _device
    if _predictor is None:
        try:
            import torch
            from aesthetics_predictor import AestheticsPredictorV2Linear
            from transformers import CLIPProcessor
        except ImportError as e:
            raise ImportError(
                "aesthetic scoring needs the 'aesthetic' extra -- "
                "install with `uv sync --extra aesthetic`"
            ) from e
        # GPU when available (this runs on wopr for real volume -- CPU is
        # fine for the dev-VM/CI path and small pilots, but the curated-
        # scale pipeline needs GPU throughput; see DECISIONS.md, 2026-09-01,
        # benchmark). Falls back to CPU transparently everywhere else.
        _device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        _predictor = AestheticsPredictorV2Linear.from_pretrained(MODEL_ID).to(_device)
        _processor = CLIPProcessor.from_pretrained(MODEL_ID)
        _predictor.eval()
    return _predictor, _processor, _device


def score_batch(image_paths: list[Path]) -> list[float]:
    """Scores a batch of local images (any size batch -- callers should
    chunk to BATCH_SIZE for the throughput this was validated at)."""
    import torch
    from PIL import Image

    predictor, processor, device = _load_model()
    images = [Image.open(p).convert("RGB") for p in image_paths]
    inputs = processor(images=images, return_tensors="pt").to(device)
    with torch.no_grad():
        outputs = predictor(**inputs)
    scores = outputs.logits.squeeze(-1).tolist()
    if isinstance(scores, float):
        scores = [scores]
    return [round(s, 3) for s in scores]


def score_all(image_paths: list[Path], batch_size: int = BATCH_SIZE) -> dict[str, float]:
    """Scores every path, batched, keyed by stem (the catalog id)."""
    results: dict[str, float] = {}
    for i in range(0, len(image_paths), batch_size):
        batch = image_paths[i : i + batch_size]
        for path, score in zip(batch, score_batch(batch), strict=True):
            results[path.stem] = score
    return results


def backfill(catalog_path: Path, images_dir: Path, checkpoint_path: Path) -> int:
    """Scores every record in catalog_path with a local image and writes
    aesthetic_score/aesthetic_method into both catalog.json and
    checkpoint.jsonl. Leaves every other field untouched."""
    catalog = json.loads(catalog_path.read_text())
    paths = []
    for r in catalog:
        p = images_dir / f"{r['id']}.jpg"
        if p.exists():
            paths.append(p)

    scores = score_all(paths)

    for r in catalog:
        if r["id"] in scores:
            r["aesthetic_score"] = scores[r["id"]]
            r["aesthetic_method"] = AESTHETIC_METHOD
    catalog_path.write_text(json.dumps(catalog, indent=2))

    if checkpoint_path.exists():
        lines = checkpoint_path.read_text().splitlines()
        out = []
        for line in lines:
            entry = json.loads(line)
            rid = entry.get("id")
            if rid in scores and entry.get("outcome") in ("catalog", "excluded"):
                entry["record"]["aesthetic_score"] = scores[rid]
                entry["record"]["aesthetic_method"] = "laion_predictor_v2"
            out.append(json.dumps(entry))
        checkpoint_path.write_text("\n".join(out) + "\n")

    return len(scores)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=Path("data/catalog.json"))
    parser.add_argument("--images", type=Path, default=Path("data/images"))
    parser.add_argument("--checkpoint", type=Path, default=Path("data/checkpoint.jsonl"))
    args = parser.parse_args()

    count = backfill(args.catalog, args.images, args.checkpoint)
    print(f"scored {count} records")


if __name__ == "__main__":
    main()
