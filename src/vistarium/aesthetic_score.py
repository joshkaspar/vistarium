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
import logging
from pathlib import Path

log = logging.getLogger("vistarium.aesthetic_score")

MODEL_ID = "shunk031/aesthetics-predictor-v2-sac-logos-ava1-l14-linearMSE"
AESTHETIC_METHOD = "aesthetics_predictor_v2_l14_linearMSE"
# Benchmarked live 2026-09-01 on wopr's GPU (150 real thumbnails, batch
# sweep 1/8/16/32/64) -- see DECISIONS.md and scripts/benchmark_aesthetic.py.
# GPU inference is ~flat (~12ms/batch) regardless of batch size; CPU-side
# preprocessing (image decode + CLIP transform) is the real bottleneck and
# scales linearly, so bigger batches mainly amortize fixed per-call
# overhead. 64 gave the best throughput observed (84 img/s vs. 45 img/s at
# batch=1) with diminishing-but-still-real returns past 32.
BATCH_SIZE = 64

_predictor = None
_processor = None
_device = None
_forced_device: str | None = None


def set_device(device: str) -> None:
    """Force "cuda" or "cpu" regardless of availability, overriding the
    auto-detect in _load_model(). Must be called before the first
    score_batch()/score_all() call -- the model's resident device is
    fixed at first load (the lazy-singleton caching below), same as
    _load_model()'s existing behavior.

    Used by the pipelined curated-scrape driver to force CPU here even
    though wopr has a GPU: that GPU is already resident with the VLM
    tagging model (llama-swap/qwen, ~26.5GB of 32GB used) which the
    pipelined driver runs concurrently with this scorer -- a second
    CUDA context contending for the remaining headroom risked eviction/
    reload thrashing far more costly than CPU's slower raw throughput.
    CPU keeps pace fine here regardless (~1 img/s vs. the NPS throttle's
    ~0.28 img/s), so it costs nothing when overlapped with fetching --
    see DECISIONS.md, 2026-09-02."""
    global _forced_device
    _forced_device = device


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
        # set_device() above overrides this entirely when set.
        if _forced_device is not None:
            _device = torch.device(_forced_device)
        else:
            _device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        _predictor = AestheticsPredictorV2Linear.from_pretrained(MODEL_ID).to(_device)
        _processor = CLIPProcessor.from_pretrained(MODEL_ID)
        _predictor.eval()
    return _predictor, _processor, _device


def score_batch(image_paths: list[Path]) -> list[float | None]:
    """Scores a batch of local images (any size batch -- callers should
    chunk to BATCH_SIZE for the throughput this was validated at).
    Aligned 1:1 with `image_paths`; a path whose image can't be opened
    (found live 2026-09-02: a thumbnail truncated mid-write by an
    unrelated disk-full incident, but any corrupt/partial file could do
    this) gets `None` instead of aborting the *entire* batch -- a single
    bad image used to take down scoring for a whole park's worth of
    candidates, since PIL.Image.open() was called unguarded inside the
    batch list comprehension. See DECISIONS.md, 2026-09-02."""
    import torch
    from PIL import Image, UnidentifiedImageError

    predictor, processor, device = _load_model()
    images: list = []
    good_indices: list[int] = []
    for i, p in enumerate(image_paths):
        try:
            images.append(Image.open(p).convert("RGB"))
            good_indices.append(i)
        except (UnidentifiedImageError, OSError) as e:
            log.warning("skipping unreadable image %s: %s", p, e)

    results: list[float | None] = [None] * len(image_paths)
    if not images:
        return results

    inputs = processor(images=images, return_tensors="pt").to(device)
    with torch.no_grad():
        outputs = predictor(**inputs)
    scores = outputs.logits.squeeze(-1).tolist()
    if isinstance(scores, float):
        scores = [scores]
    for idx, score in zip(good_indices, scores, strict=True):
        results[idx] = round(score, 3)
    return results


def score_all(image_paths: list[Path], batch_size: int = BATCH_SIZE) -> dict[str, float]:
    """Scores every path, batched, keyed by stem (the catalog id).
    Unreadable images are silently absent from the result (logged as a
    warning in score_batch) rather than aborting the whole call."""
    results: dict[str, float] = {}
    for i in range(0, len(image_paths), batch_size):
        batch = image_paths[i : i + batch_size]
        for path, score in zip(batch, score_batch(batch), strict=True):
            if score is not None:
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
