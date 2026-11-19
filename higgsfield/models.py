"""Request bodies and list prices for the models the nodes support.

Schemas come from https://docs.higgsfield.ai and the model pages on
https://open.higgsfield.ai (checked 2026-11-19). The API rejects null and
unknown keys, so optional fields are left out when they are empty.

The per-second list rates live in web/rates.json, which the price badge in the
browser reads too. The price a run is checked against is the exact one from
POST /estimate, which also knows about volume discounts.
"""

from __future__ import annotations

import json
from pathlib import Path

_RATES = json.loads((Path(__file__).resolve().parent.parent / "web" / "rates.json").read_text(encoding="utf-8"))
PRICES_CHECKED: str = _RATES["checked"]
SEEDANCE_RATES: dict[str, float] = _RATES["per_second"]["seedance-2.5"]
KLING4_RATES: dict[str, float] = _RATES["per_second"]["kling-4.0"]
# Genjutsu bills the source video's length (trimmed to 30 s), Light bills the output (up to 15 s).
GENJUTSU_RATES: dict[str, dict[str, float]] = {
    model: _RATES["per_second"][model] for model in ("genjutsu", "genjutsu-light")
}
GENJUTSU_ENDPOINTS = {
    "genjutsu": "higgsfield/genjutsu/motion-transfer/v1.0",
    "genjutsu-light": "higgsfield/genjutsu-light/motion-transfer/v1.0",
}
GENJUTSU_MAX_SECONDS = {"genjutsu": 30, "genjutsu-light": 15}


def _put(body: dict, key: str, value) -> None:
    if value not in (None, "", []):
        body[key] = value


def seedance_25(
    prompt: str,
    duration: int,
    resolution: str,
    aspect_ratio: str,
    generate_audio: bool,
    image_url: str | None = None,
    end_image_url: str | None = None,
) -> tuple[str, dict]:
    prompt = prompt.strip()
    body: dict = {"duration": duration, "resolution": resolution, "generate_audio": generate_audio}
    if image_url:
        # The frame follows the image, so image-to-video takes no aspect_ratio.
        body["image_url"] = image_url
        _put(body, "end_image_url", end_image_url)
        _put(body, "prompt", prompt)
        return "bytedance/seedance-2.5/image-to-video", body
    if end_image_url:
        raise ValueError("end_image needs a start image too.")
    if not prompt:
        raise ValueError("Write a prompt, or connect a start image.")
    body.update(prompt=prompt, aspect_ratio=aspect_ratio)
    return "bytedance/seedance-2.5/text-to-video", body


def kling_40(
    prompt: str,
    duration: int,
    resolution: str,
    aspect_ratio: str,
    image_url: str | None = None,
    end_image_url: str | None = None,
) -> tuple[str, dict]:
    prompt = prompt.strip()
    body: dict = {"duration": duration, "resolution": resolution}
    if image_url:
        body["image_url"] = image_url
        _put(body, "end_image_url", end_image_url)
        _put(body, "prompt", prompt)
        return "kling-video/v4.0/image-to-video", body
    if end_image_url:
        raise ValueError("end_image needs a start image too.")
    if not prompt:
        raise ValueError("Write a prompt, or connect a start image.")
    body.update(prompt=prompt, aspect_ratio=aspect_ratio)
    return "kling-video/v4.0/text-to-video", body


def genjutsu_motion_transfer(
    model: str, video_url: str, image_urls: list[str], prompt: str, resolution: str
) -> tuple[str, dict]:
    if not 1 <= len(image_urls) <= 8:
        raise ValueError(f"Genjutsu takes 1 to 8 reference images, got {len(image_urls)}.")
    # Genjutsu always takes a prompt; an empty one is "".
    body = {"video_url": video_url, "image_urls": image_urls, "prompt": prompt.strip(), "resolution": resolution}
    return GENJUTSU_ENDPOINTS[model], body


def list_price(rates: dict, resolution: str, seconds: float) -> float:
    return round(rates[resolution] * seconds, 4)
