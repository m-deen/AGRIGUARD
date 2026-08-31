"""
Photo-quality gates derived from the AgriGuard breed dataset audit.

The training set contained silhouettes, mixed herds, wildlife, and tiny
stock-thumbnails. Those images cannot be classified from coat colour, so
the live pipeline refuses or warns instead of guessing.
"""
from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image

MIN_SIDE_PX = 120
SILHOUETTE_DARK_FRAC = 0.18
SILHOUETTE_BRIGHT_FRAC = 0.10
LOW_COLOUR_SCORE = 6.0

PHOTO_TIPS = [
    "Photograph one animal, close enough that it fills most of the frame.",
    "Use a side or three-quarter view in daylight so coat colour, ears and horns are visible.",
    "Avoid silhouettes, sunsets, mixed herds, and wildlife (wildebeest or buffalo).",
    "If you know the species, choose Cattle, Sheep or Goat — Dorper sheep and Boer goats look alike.",
    "Solid red cattle: long spreading horns usually Afrikaner; smooth beef type usually Bonsmara. Speckled hides are usually Nguni.",
]


def assess_photo_quality(image: Image.Image) -> dict[str, Any]:
    """Return {level, code, message} for an opened PIL image."""
    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    return assess_photo_quality_array(rgb)


def assess_photo_quality_bytes(file_bytes: bytes) -> dict[str, Any]:
    image = Image.open(__import__("io").BytesIO(file_bytes))
    image.load()
    return assess_photo_quality(image)


def assess_photo_quality_array(rgb: np.ndarray) -> dict[str, Any]:
    if rgb.ndim != 3 or rgb.shape[2] < 3:
        return {
            "level": "error",
            "code": "invalid",
            "message": "Please upload a colour JPEG or PNG photo of one animal.",
        }

    height, width = rgb.shape[:3][0], rgb.shape[1]
    if min(height, width) < MIN_SIDE_PX:
        return {
            "level": "error",
            "code": "too_small",
            "message": (
                "That photo is too small for breed ID. Use a clearer close-up "
                "of one animal (at least 120px on the short side)."
            ),
        }

    gray = rgb[..., :3].astype(np.float32).mean(axis=2)
    peak = rgb[..., :3].astype(np.float32).max(axis=2)
    dark_frac = float((gray < 40).mean())
    bright_frac = float((peak > 200).mean())
    colourfulness = float(rgb[..., :3].astype(np.float32).std(axis=2).mean())

    # Sunset / silhouette: dark animal + bright (possibly orange) sky.
    # Use the max RGB channel so a saturated sunset still counts as bright.
    if dark_frac >= SILHOUETTE_DARK_FRAC and bright_frac >= SILHOUETTE_BRIGHT_FRAC:
        return {
            "level": "error",
            "code": "silhouette",
            "message": (
                "This looks like a silhouette or sunset photo. Breed ID needs "
                "coat colour — photograph the animal in daylight from the side."
            ),
        }

    if colourfulness < LOW_COLOUR_SCORE and dark_frac >= 0.12:
        return {
            "level": "warning",
            "code": "low_colour",
            "message": (
                "This photo has little colour. A daylight side-view usually "
                "gives a more reliable breed match."
            ),
        }

    return {"level": "ok", "code": None, "message": None}
