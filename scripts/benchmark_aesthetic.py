"""One-off: benchmark aesthetic_score.py's real throughput on wopr's GPU
before scaling curate.py up to real NPGallery volume (see DECISIONS.md,
2026-09-01). Not part of the installed package -- a diagnostic script,
run directly (`python scripts/benchmark_aesthetic.py <thumbs_dir>`).

Measures, per batch size in the sweep:
- cold start: model load (first call to _load_model()) + first batch
- warm-batch: mean time for every subsequent batch
- preprocessing vs. inference split: processor() call vs. predictor()
  forward pass, timed separately (score_batch() doesn't expose this
  split, so this script reimplements the batch loop with instrumentation
  rather than calling score_batch() as a black box)

Uses a real sample of already-fetched thumbnails, not synthetic images --
synthetic solid-color images decode/preprocess differently than real
JPEGs and wouldn't give a trustworthy number.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from vistarium import aesthetic_score

BATCH_SIZES = [1, 8, 16, 32, 64]


def _time_batch(image_paths: list[Path]) -> tuple[float, float]:
    """Returns (preprocessing_s, inference_s) for one batch."""
    import torch
    from PIL import Image

    predictor, processor, device = aesthetic_score._load_model()

    t0 = time.perf_counter()
    images = [Image.open(p).convert("RGB") for p in image_paths]
    inputs = processor(images=images, return_tensors="pt").to(device)
    t1 = time.perf_counter()

    with torch.no_grad():
        predictor(**inputs)
    t2 = time.perf_counter()

    return t1 - t0, t2 - t1


def main() -> None:
    thumbs_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/benchmark_thumbs")
    all_paths = sorted(thumbs_dir.glob("*.jpg"))
    print(f"{len(all_paths)} thumbnails available in {thumbs_dir}")
    if not all_paths:
        print("no thumbnails found -- nothing to benchmark")
        return

    print("\n--- cold start (includes model load) ---")
    cold_batch = all_paths[: min(8, len(all_paths))]
    t0 = time.perf_counter()
    preprocess_s, inference_s = _time_batch(cold_batch)
    total_s = time.perf_counter() - t0
    print(
        f"batch_size={len(cold_batch)}: total={total_s:.2f}s "
        f"(model_load+first_call), preprocess={preprocess_s:.3f}s, inference={inference_s:.3f}s"
    )

    _predictor, _processor, device = aesthetic_score._load_model()
    print(f"\ndevice: {device}")

    print("\n--- batch size sweep (warm, model already loaded) ---")
    print(
        f"{'batch':>6} {'n_batches':>10} {'mean_total_s':>13} {'img/s':>8} "
        f"{'mean_preprocess_s':>18} {'mean_inference_s':>17}"
    )
    for batch_size in BATCH_SIZES:
        if batch_size > len(all_paths):
            print(f"{batch_size:>6}   (skipped -- fewer than {batch_size} thumbnails available)")
            continue
        n_batches = max(1, len(all_paths) // batch_size)
        preprocess_times = []
        inference_times = []
        t_start = time.perf_counter()
        for i in range(n_batches):
            batch = all_paths[i * batch_size : (i + 1) * batch_size]
            if not batch:
                break
            pre, inf = _time_batch(batch)
            preprocess_times.append(pre)
            inference_times.append(inf)
        elapsed = time.perf_counter() - t_start
        n_images = sum(
            len(all_paths[i * batch_size : (i + 1) * batch_size]) for i in range(n_batches)
        )
        mean_total = elapsed / n_batches
        img_per_s = n_images / elapsed if elapsed > 0 else float("inf")
        mean_pre = sum(preprocess_times) / len(preprocess_times)
        mean_inf = sum(inference_times) / len(inference_times)
        print(
            f"{batch_size:>6} {n_batches:>10} {mean_total:>13.3f} {img_per_s:>8.1f} "
            f"{mean_pre:>18.3f} {mean_inf:>17.3f}"
        )


if __name__ == "__main__":
    main()
