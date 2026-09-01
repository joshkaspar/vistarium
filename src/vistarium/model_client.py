"""The one narrow, bounded call to the local judgment model.

Everything here was validated empirically against wopr's qwen3.8-27b
before being written into the pipeline -- see DECISIONS.md, 2026-08-29,
for the actual runs this is built from:

- Grammar-constrained JSON via llama.cpp's `grammar` request field, not
  OpenAI-style response_format -- wopr's qwen3.8-27b does not advertise
  response_format support (confirmed via /v1/models).
- Under thinking mode, grammar-constrained JSON output can land entirely
  in `reasoning_content` with `content` left empty, despite a "stop"
  finish_reason. Must check both fields.
- `enable_thinking: false` was tested as an alternative and rejected: it
  did not fix the content/reasoning_content routing on real vision+
  grammar calls, gave no latency benefit, and produced a measurably
  worse license_confidence result on the one case that mattered most (a
  watercolor painting that should have been flagged). Stick with the
  box's default thinking-mode config; do not add a llama-swap override.
- crop_anchor is 5-way (center/top/bottom/left/right), not 9-way with
  diagonal corners -- corners were tested and tracked the single
  brightest point in frame (sun glare, a bright star) rather than the
  actual subject in two separate cases.
- reasoning_effort=low is the validated preset: cuts verbose output
  ~74% with no correctness cost found on this task family.
"""

from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path

import requests
from PIL import Image

MODEL = "qwen3.8-27b:low"
MAX_DIM = 1568
REQUEST_TIMEOUT_S = 180
MAX_RETRIES = 2

JSON_GRAMMAR = r"""
root ::= "{" ws "\"is_photograph\"" ws ":" ws boolean ws "," ws "\"time_of_day\"" ws ":" ws tod ws "," ws "\"time_of_day_evidence\"" ws ":" ws todevidence ws "," ws "\"license_confidence\"" ws ":" ws licenseconfidence ws "," ws "\"license_evidence\"" ws ":" ws string ws "," ws "\"primary_subject\"" ws ":" ws primarysubject ws "," ws "\"people_present\"" ws ":" ws boolean ws "," ws "\"people_prominence\"" ws ":" ws peopleprominence ws "," ws "\"crop_anchor\"" ws ":" ws cropanchor ws "," ws "\"frame_type\"" ws ":" ws frametype ws "," ws "\"color_mode\"" ws ":" ws colormode ws "," ws "\"tags\"" ws ":" ws tags ws "}"
tod ::= "\"morning\"" | "\"afternoon\"" | "\"evening\"" | "\"night\""
todevidence ::= "\"caption\"" | "\"exif_timestamp\"" | "\"visual_inference\""
licenseconfidence ::= "\"confirmed\"" | "\"flagged_for_review\""
primarysubject ::= "\"landscape\"" | "\"wildlife\"" | "\"structure\"" | "\"vehicle\"" | "\"human_activity\"" | "\"document\"" | "\"detail\""
peopleprominence ::= "\"none\"" | "\"background\"" | "\"midground\"" | "\"foreground_focal\""
cropanchor ::= "\"center\"" | "\"top\"" | "\"bottom\"" | "\"left\"" | "\"right\""
frametype ::= "\"full_bleed\"" | "\"matted\"" | "\"multi_panel\"" | "\"stereograph\""
colormode ::= "\"color\"" | "\"monochrome\""
boolean ::= "true" | "false"
string ::= "\"" ([^"\\])* "\""
tags ::= "[" ws "]" | "[" ws string (ws "," ws string)* ws "]"
ws ::= [ \t\n]*
"""

PROMPT = """Look at this photograph and produce a JSON object with exactly these fields:

- is_photograph: true/false -- false if this is a painting, illustration, engraving, sketch, map, or other non-photographic image
- time_of_day: morning | afternoon | evening | night (judge from the light in the image itself, not any filename or caption you might infer)
- time_of_day_evidence: caption | exif_timestamp | visual_inference -- use "visual_inference" since you only have the pixels, not real EXIF/caption data
- license_confidence: confirmed | flagged_for_review -- flag if the image itself shows something that complicates its stated public-domain/open status (visible watermark, third-party logo, recognizable identifiable person in a way that suggests a rights concern, embedded copyright notice, or is not a photograph at all)
- license_evidence: one short sentence, what you saw (or "no rights concerns visible" if confirmed)
- primary_subject: landscape | wildlife | structure | vehicle | human_activity | document | detail -- use "document" for a genuine photograph of a document-like thing (a newspaper page, museum placard, interpretive sign, map, or screenshot), not for the image itself being a scanned document/map/painting (that's is_photograph=false instead). Use "detail" for a close-up/macro shot of a small piece of the environment with no scenic composition -- moss on bark, animal scat, gravel, bark texture, a single leaf or pinecone -- as opposed to "landscape", which is a scene or vista. A trail or forest floor photographed as a wide scene is "landscape"; the same trail with the frame dominated by one small object on the ground is "detail".
- people_present: true/false
- people_prominence: none | background | midground | foreground_focal
- crop_anchor: center | top | bottom | left | right -- which direction the main subject/point of interest is weighted toward, for downstream cropping to arbitrary aspect ratios
- frame_type: full_bleed | matted | multi_panel | stereograph -- is this a plain photo filling the frame, a scan with visible mat/border/mount around it, multiple images in one file, or a stereograph card?
- color_mode: color | monochrome -- "monochrome" for black-and-white/grayscale/sepia-toned images (including a color scan of a black-and-white print or negative), "color" for anything with real color information, even if muted, desaturated, or shot in flat/overcast light. Judge this the way a person looking at the image would, not by how saturated it looks -- a dim, grey, foggy color photo is still "color".
- tags: a short JSON array of 3-6 lowercase single/double-word visual descriptors (subject matter, notable features)

Respond with ONLY the JSON object, no other text."""


class ModelJudgmentError(RuntimeError):
    """Raised when the model never returns schema-valid JSON after retries."""


class ConfigError(RuntimeError):
    """Raised for missing/invalid configuration -- distinct from a runtime
    model failure, so callers (and error messages) can tell "you forgot to
    set something up" apart from "the model itself misbehaved"."""


def _wopr_base_url() -> str:
    url = os.environ.get("WOPR_BASE_URL")
    if not url:
        raise ConfigError(
            "WOPR_BASE_URL is not set. Copy .env.example to .env and set it to your "
            "local model endpoint (e.g. llama-swap's address), or export it directly."
        )
    return url.rstrip("/")


def _load_and_encode(path: Path) -> str:
    img = Image.open(path)
    img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > MAX_DIM:
        scale = MAX_DIM / max(w, h)
        img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _extract_json(message: dict) -> dict:
    content = message.get("content") or ""
    reasoning = message.get("reasoning_content") or ""
    raw = content if content.strip() else reasoning
    return json.loads(raw)


def judge_image(image_path: Path) -> dict:
    """The one call site for the local model. Returns a dict matching the
    'model' subset of schema.json. Raises ModelJudgmentError if the model
    doesn't return valid JSON after MAX_RETRIES attempts -- the pipeline
    decides whether to skip the image or abort the run."""
    b64 = _load_and_encode(image_path)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }
    ]
    body = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "max_tokens": 800,
        "temperature": 1.0,
        "top_p": 0.95,
        "top_k": 20,
        "grammar": JSON_GRAMMAR,
    }

    base_url = _wopr_base_url()
    last_exc: Exception | None = None
    for _attempt in range(MAX_RETRIES + 1):
        try:
            r = requests.post(
                f"{base_url}/v1/chat/completions", json=body, timeout=REQUEST_TIMEOUT_S
            )
            r.raise_for_status()
            message = r.json()["choices"][0]["message"]
            return _extract_json(message)
        except (requests.RequestException, json.JSONDecodeError, KeyError, IndexError) as e:
            last_exc = e

    raise ModelJudgmentError(f"model never returned valid JSON for {image_path}: {last_exc}")
