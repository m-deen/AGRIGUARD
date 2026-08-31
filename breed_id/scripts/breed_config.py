"""Shared breed-folder names used by download / ingest / retrain."""
from __future__ import annotations

from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parent
DATASET_DIR = ROOT / "dataset"
EXTRA_PHOTOS_DIR = ROOT / "extra_photos"
RAW_PHOTOS_DIR = ROOT / "raw_photos"
MODELS_DIR = ROOT / "models"
LABELS_PATH = ROOT / "class_labels.json"

# Folder names Keras image_dataset_from_directory must see (alphabetical).
CLASS_FOLDERS = ["Afrikaner", "Boer_Goat", "Bonsmara", "Dorper", "Nguni"]
DISPLAY_NAMES = ["Afrikaner", "Boer Goat", "Bonsmara", "Dorper", "Nguni"]

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# User-facing / download folder names → CLASS_FOLDERS
FOLDER_ALIASES = {
    "afrikaner": "Afrikaner",
    "afrikanner": "Afrikaner",
    "afrikander": "Afrikaner",
    "africander": "Afrikaner",
    "boer goat": "Boer_Goat",
    "boer_goat": "Boer_Goat",
    "boergoat": "Boer_Goat",
    "bonsmara": "Bonsmara",
    "dorper": "Dorper",
    "nguni": "Nguni",
}


def canonical_breed(name: str) -> str | None:
    """Map a folder or display name onto a CLASS_FOLDERS entry."""
    key = (name or "").strip().replace("-", "_")
    if key in CLASS_FOLDERS:
        return key
    return FOLDER_ALIASES.get(key.lower().replace("_", " ")) or FOLDER_ALIASES.get(
        key.lower().replace(" ", "_")
    )


def display_name(folder: str) -> str:
    try:
        return DISPLAY_NAMES[CLASS_FOLDERS.index(folder)]
    except ValueError:
        return folder.replace("_", " ")
