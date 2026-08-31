"""Unit tests for breed folder aliases and extra-photo ingest helpers."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from breed_config import CLASS_FOLDERS, canonical_breed, display_name
from download_breed_photos import _title_allowed
from download_breed_photos import extract_vleissentraal_feature_urls
from download_breed_photos import looks_like_placeholder
from ingest_extra_photos import unique_dest


class BreedConfigTests(unittest.TestCase):
    def test_afrikaner_spellings(self):
        for name in ("Afrikaner", "Afrikanner", "Africander", "afrikander"):
            self.assertEqual(canonical_breed(name), "Afrikaner", name)

    def test_class_folders_are_canonical(self):
        for folder in CLASS_FOLDERS:
            self.assertEqual(canonical_breed(folder), folder)

    def test_boer_goat_alias(self):
        self.assertEqual(canonical_breed("Boer Goat"), "Boer_Goat")
        self.assertEqual(display_name("Boer_Goat"), "Boer Goat")

    def test_unknown_breed(self):
        self.assertIsNone(canonical_breed("Holstein"))


class UniqueDestTests(unittest.TestCase):
    def test_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = unique_dest(folder, Path("bull.jpg"))
            first.write_text("a", encoding="utf-8")
            second = unique_dest(folder, Path("bull.jpg"))
            self.assertNotEqual(first, second)
            self.assertEqual(second.name, "bull_1.jpg")


class WikimediaTitleFilterTests(unittest.TestCase):
    def test_afrikaner_keeps_cattle_titles(self):
        self.assertTrue(_title_allowed("Afrikaner", "File:Afrikaner cattle.jpg"))
        self.assertTrue(_title_allowed("Afrikaner", "File:Kuh in transkei.jpg"))

    def test_afrikaner_rejects_people_and_sports(self):
        self.assertFalse(_title_allowed("Afrikaner", "File:Afrikaner rugby match.jpg"))
        self.assertFalse(_title_allowed("Afrikaner", "File:Jan Jonker Afrikaner Portrait.jpg"))
        self.assertFalse(_title_allowed("Afrikaner", "File:Red Bull racing.jpg"))


class VleissentraalSourceTests(unittest.TestCase):
    def test_extracts_unique_lot_feature_urls(self):
        html = """
        <img src="https://www.vleissentraal.co.za/storage/lot/feature/20230620071604.jpg">
        <img src="https://www.vleissentraal.co.za/storage/lot/feature/20230620071604.jpg">
        <img src="https://www.vleissentraal.co.za/storage/lot/feature/20230620071642.jpg">
        <img src="/img/nav_logo.png">
        """
        urls = extract_vleissentraal_feature_urls(html)
        self.assertEqual(
            urls,
            [
                "https://www.vleissentraal.co.za/storage/lot/feature/20230620071604.jpg",
                "https://www.vleissentraal.co.za/storage/lot/feature/20230620071642.jpg",
            ],
        )

    def test_placeholder_is_small_flat_image(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stub.jpg"
            Image.new("RGB", (400, 300), (180, 90, 40)).save(path, "JPEG", quality=80)
            self.assertTrue(looks_like_placeholder(path))

    def test_real_photo_is_not_placeholder(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cow.jpg"
            img = Image.new("RGB", (1200, 900), (20, 120, 40))
            for x in range(80, 900):
                for y in range(80, 700):
                    img.putpixel((x, y), ((x * 3) % 200 + 40, 70, (y * 2) % 160 + 30))
            img.save(path, "JPEG", quality=92)
            self.assertGreaterEqual(path.stat().st_size, 70_000)
            self.assertFalse(looks_like_placeholder(path))


class LookalikeNoteTests(unittest.TestCase):
    def test_afrikaner_bonsmara_note(self):
        sys.path.insert(0, str(ROOT))
        try:
            from breed_identification import lookalike_note
        except ImportError as exc:
            self.skipTest(f"breed_identification deps missing: {exc}")

        note = lookalike_note(
            [
                {"breed": "Afrikaner", "confidence_percent": 51.0},
                {"breed": "Bonsmara", "confidence_percent": 40.0},
            ]
        )
        self.assertIsNotNone(note)
        self.assertIn("Afrikaner", note)
        self.assertIn("Bonsmara", note)


if __name__ == "__main__":
    unittest.main()
