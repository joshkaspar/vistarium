#!/usr/bin/env python3
"""One-off validation: run the new fire_smoke_category prompt against the
11 real, hand-labeled examples from Josh's 2026-09-07 policy writeup,
before trusting it on the full 207-record fire/smoke batch or any real
scrape. See DECISIONS.md.

Usage (on wopr, where the model + full-res images live):
    python scripts/_test_fire_smoke_prompt.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from vistarium.model_client import ModelJudgmentError, judge_image  # noqa: E402

# (id, title, expected category per Josh's writeup)
TEST_CASES = [
    ("565d5631-f7d3-4351-8d24-ae260731206b", "Fire Recon", "response_documentation"),
    ("cd146c74-155d-451f-6754-ade816d9621c", "Roberts Fire ridge", "response_documentation"),
    ("54b499f2-155d-451f-6721-af20a28c0e68", "Old Rag Fire", "response_documentation"),
    ("285e6327-1dd8-b71c-0773-a871716b8496", "Misty Sunset", "not_fire"),
    (
        "de385185-1dd8-b71b-0b25-c59f642e28c2",
        "Dawn eruption of Great Fountain Geyser",
        "not_fire",
    ),
    (
        "21a0554a-2dc0-473a-97e6-ddc0023dd106",
        "Lava overflowing from the western vent",
        "volcanic_phenomenon",
    ),
    ("1754e843-87f9-4ad8-9924-a0b62a2d2613", "Makamae Street, May 6th", "volcanic_destruction"),
    (
        "39e5001e-40ae-4f2c-a303-441f59f8967c",
        "Sequoia silhouettes during the KNP Complex",
        "smoky_landscape",
    ),
    (
        "974aa299-8a48-4e0e-8b2e-fafc4c8fc3a6",
        "Horne Fire Orange Sky and Tree Torching",
        "smoky_landscape",
    ),
    ("ecc56a2c-1dd8-b71b-0baf-e4e54bf66d1a", "Spruce Fire from Dunraven Pass", "smoky_landscape"),
    ("cc66f6b1-a352-40df-8a49-6398f271054c", "Crater Hill Fire", "burn_aftermath_dominant"),
]

IMAGES_DIR = Path("data/images")


def main() -> None:
    results = []
    correct = 0
    for asset_id, title, expected in TEST_CASES:
        image_path = IMAGES_DIR / f"{asset_id}.jpg"
        if not image_path.exists():
            results.append({"id": asset_id, "title": title, "error": "image not found"})
            continue
        try:
            fields = judge_image(image_path)
        except ModelJudgmentError as e:
            results.append({"id": asset_id, "title": title, "error": str(e)})
            continue
        actual = fields.get("fire_smoke_category")
        match = actual == expected
        correct += match
        results.append(
            {
                "id": asset_id,
                "title": title,
                "expected": expected,
                "actual": actual,
                "match": match,
                "evidence": fields.get("fire_smoke_evidence"),
            }
        )
        print(f"{'OK  ' if match else 'MISS'} {asset_id}: expected={expected} actual={actual}")

    print(f"\n{correct}/{len(TEST_CASES)} matched expected category")
    Path("/tmp/fire_smoke_test_results.json").write_text(json.dumps(results, indent=2))
    print("wrote /tmp/fire_smoke_test_results.json")


if __name__ == "__main__":
    main()
