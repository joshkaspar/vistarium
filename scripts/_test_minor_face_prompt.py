#!/usr/bin/env python3
"""One-off validation: run the new minor_face_present/license_confidence
prompt against a fixed, human-picked test set before trusting it at
scale. See DECISIONS.md, 2026-09-06 -- do NOT wire this into any batch
run until a human has compared these results against the actual images.

Usage (on wopr, where the model + full-res images live):
    python scripts/_test_minor_face_prompt.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from vistarium.model_client import ModelJudgmentError, judge_image  # noqa: E402

TEST_IDS = [
    "07c6877d-46c6-46aa-879e-857d7ac4af3c",
    "1cd4a3e1-c89c-4ce1-8735-ba6f8137bfee",
    "fdc0fcd3-851c-4343-8e17-8ef1e6f97ae4",
    "4348fb56-6cb6-45c4-ba54-2a404e053ee4",
    "796752b1-62ae-4318-8ee2-71054f7a1130",
    "632aa70a-155d-451f-6728-7c4f90df046a",
    "9f98c9b1-9e05-41ec-9f36-2790dc753e8d",
    "4363a003-4f6c-435a-b57e-7e238cc751b8",
    "944e07d7-a944-7d96-57b1-366c73bf4625",
    "81abe315-1c32-4743-8148-d6f065d1b1c0",
]

IMAGES_DIR = Path("data/images")


def main() -> None:
    results = []
    for asset_id in TEST_IDS:
        image_path = IMAGES_DIR / f"{asset_id}.jpg"
        if not image_path.exists():
            results.append({"id": asset_id, "error": "image not found"})
            continue
        try:
            fields = judge_image(image_path)
        except ModelJudgmentError as e:
            results.append({"id": asset_id, "error": str(e)})
            continue
        results.append(
            {
                "id": asset_id,
                "minor_face_present": fields.get("minor_face_present"),
                "minor_face_evidence": fields.get("minor_face_evidence"),
                "license_confidence": fields.get("license_confidence"),
                "license_evidence": fields.get("license_evidence"),
                "people_present": fields.get("people_present"),
                "people_prominence": fields.get("people_prominence"),
            }
        )
        print(f"done: {asset_id}")

    Path("/tmp/minor_face_test_results.json").write_text(json.dumps(results, indent=2))
    print("\nwrote /tmp/minor_face_test_results.json")


if __name__ == "__main__":
    main()
