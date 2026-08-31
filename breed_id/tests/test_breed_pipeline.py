"""Unit tests for breed-ID quality gates and lookalike notes (no model required)."""
from __future__ import annotations

import io
import unittest

import numpy as np
from PIL import Image

from breed_id.breed_identification import (
    RELATED_BREEDS,
    filter_probabilities_by_species,
    lookalike_note,
    normalize_breed_name,
)
from breed_id.photo_quality import PHOTO_TIPS, assess_photo_quality


def _jpeg_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


class PhotoQualityTests(unittest.TestCase):
    def test_silhouette_sunset_is_rejected(self):
        rgb = np.zeros((220, 400, 3), dtype=np.uint8)
        rgb[:90] = (255, 110, 20)  # bright orange sky
        # dark cow-shaped band
        rgb[110:210, 20:380] = (8, 8, 8)
        img = Image.fromarray(rgb, "RGB")
        result = assess_photo_quality(img)
        self.assertEqual(result["level"], "error")
        self.assertEqual(result["code"], "silhouette")

    def test_daylight_side_view_is_ok(self):
        rng = np.random.default_rng(0)
        rgb = rng.integers(40, 200, size=(240, 320, 3), dtype=np.uint8)
        rgb[:, :, 0] = np.clip(rgb[:, :, 0].astype(np.int16) + 40, 0, 255).astype(np.uint8)
        img = Image.fromarray(rgb, "RGB")
        result = assess_photo_quality(img)
        self.assertEqual(result["level"], "ok")

    def test_tiny_image_is_rejected(self):
        img = Image.new("RGB", (80, 80), (120, 80, 40))
        result = assess_photo_quality(img)
        self.assertEqual(result["code"], "too_small")

    def test_photo_tips_exist(self):
        self.assertGreaterEqual(len(PHOTO_TIPS), 4)


class LookalikeTests(unittest.TestCase):
    def test_dorper_boer_note(self):
        note = lookalike_note([
            {"breed": "Dorper", "confidence_percent": 55},
            {"breed": "Boer Goat", "confidence_percent": 40},
        ])
        self.assertIn("Dorper", note)
        self.assertIn("Boer", note)

    def test_red_cattle_note(self):
        note = lookalike_note([
            {"breed": "Afrikaner", "confidence_percent": 48},
            {"breed": "Bonsmara", "confidence_percent": 42},
        ])
        self.assertIn("Nguni", note)
        self.assertIn("Afrikaner", note)

    def test_boer_mentions_kalahari(self):
        note = lookalike_note([{"breed": "Boer Goat", "confidence_percent": 80}])
        self.assertIn("Kalahari", note)

    def test_species_filter_keeps_goats(self):
        raw = {"Boer Goat": 0.2, "Dorper": 0.7, "Nguni": 0.1}
        filtered = filter_probabilities_by_species(raw, "Goat")
        self.assertEqual(set(filtered), {"Boer Goat"})

    def test_alias_boer_goat(self):
        self.assertEqual(normalize_breed_name("Boer_Goat"), "Boer Goat")

    def test_related_breeds_cover_model_classes(self):
        for name in ("Afrikaner", "Bonsmara", "Nguni", "Boer Goat", "Dorper"):
            self.assertIn(name, RELATED_BREEDS)


if __name__ == "__main__":
    unittest.main()
