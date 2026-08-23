"""
AgriGuard - Dataset Preparation Script
=========================================
WHAT THIS DOES, IN PLAIN ENGLISH:

You'll be collecting breed photos from different places (Google Images,
breed society websites, Kaggle, stock photos). This script:

  1. Checks every photo you collected is actually a valid, readable image
     (deletes broken/corrupt downloads automatically)
  2. Makes sure your folders are organized correctly for training
     (one folder per breed - this is the format ML training expects)
  3. Multiplies your dataset using AUGMENTATION - creates flipped,
     rotated, brightened, and zoomed copies of each photo, so 100 real
     photos becomes 500+ training images without you finding more photos

HOW TO USE IT:

  Step 1: Create this folder structure and drop your raw downloaded
          photos into the matching breed folder:

          raw_photos/
            Nguni/          <- put your Nguni photos here
            Bonsmara/       <- put your Bonsmara photos here
            Boer Goat/      <- put your Boer Goat photos here
            Dorper/         <- put your Dorper photos here
            Angus/          <- put your Angus photos here
            Brahman/        <- put your Brahman photos here

  Step 2: Run this script:
          python3 prepare_dataset.py

  Step 3: Check the new "dataset_ready/" folder - it will contain
          cleaned + augmented images, organized the same way, ready
          to hand to your ML teammate for training.
"""

import os
import shutil
from pathlib import Path

from PIL import Image, ImageEnhance
import numpy as np


# ---------------------------------------------------------------------------
# SETTINGS - change these if your folder names or breed list are different
# ---------------------------------------------------------------------------

RAW_PHOTOS_FOLDER = "raw_photos"
OUTPUT_FOLDER = "dataset_ready"

BREED_FOLDERS = [
    "Nguni", "Bonsmara", "Angus", "Brahman",       # cattle
    "Boer Goat", "Kalahari Red", "Savanna Goat",   # goats
    "Dorper", "Merino", "Damara",                  # sheep
]

TARGET_SIZE = (224, 224)          # matches REQ-32 in the SRS
AUGMENTATIONS_PER_IMAGE = 4       # each real photo becomes 1 original + 4 variations = 5 total


# =============================================================================
# STEP 1: CHECK EVERY PHOTO IS ACTUALLY VALID
# Downloaded images are sometimes broken, tiny, or not real images at all
# (a webpage's "image not found" placeholder, for example). This throws
# those out before they can quietly ruin the training process.
# =============================================================================

def is_valid_image(file_path: Path) -> bool:
    """Returns True if the file is a real, openable image of a reasonable size."""
    try:
        with Image.open(file_path) as img:
            img.verify()
        # re-open since verify() closes the file handle
        with Image.open(file_path) as img:
            width, height = img.size
            if width < 50 or height < 50:
                return False
        return True
    except Exception:
        return False


def clean_raw_folder(breed_folder: Path) -> list[Path]:
    """
    Goes through one breed's folder, keeps only valid images, and
    reports how many were thrown out.
    """
    valid_images = []
    removed_count = 0

    for file_path in breed_folder.iterdir():
        if not file_path.is_file():
            continue
        if is_valid_image(file_path):
            valid_images.append(file_path)
        else:
            removed_count += 1

    if removed_count > 0:
        print(f"    - Skipped {removed_count} broken/invalid file(s)")

    return valid_images


# =============================================================================
# STEP 2: AUGMENTATION - MAKE EACH PHOTO INTO SEVERAL TRAINING IMAGES
# A CNN learns better when it sees the same animal from slightly
# different angles/lighting. Since we can't photograph the same cow
# five times, we fake that variety by transforming each photo.
# =============================================================================

def augment_image(image: Image.Image) -> list[Image.Image]:
    """
    Takes one image and returns 4 modified versions:
      1. Horizontally flipped   (mirrors the photo - a Nguni facing
                                  left is still a Nguni facing right)
      2. Rotated slightly       (photos aren't always perfectly level)
      3. Brighter                (simulates a sunnier day)
      4. Darker                  (simulates an overcast day / shade)
    """
    variations = []

    flipped = image.transpose(Image.FLIP_LEFT_RIGHT)
    variations.append(flipped)

    rotated = image.rotate(15, expand=False, fillcolor=(255, 255, 255))
    variations.append(rotated)

    brighter = ImageEnhance.Brightness(image).enhance(1.3)
    variations.append(brighter)

    darker = ImageEnhance.Brightness(image).enhance(0.7)
    variations.append(darker)

    return variations


# =============================================================================
# STEP 3: RESIZE EVERY IMAGE TO MATCH WHAT THE MODEL EXPECTS
# Matches REQ-32: 224x224, same as the live app will use when a farmer
# uploads a real photo - so training and real use match exactly.
# =============================================================================

def resize_for_training(image: Image.Image) -> Image.Image:
    return image.convert("RGB").resize(TARGET_SIZE)


# =============================================================================
# MAIN PIPELINE - runs steps 1-3 for every breed folder
# =============================================================================

def prepare_dataset():
    raw_root = Path(RAW_PHOTOS_FOLDER)
    output_root = Path(OUTPUT_FOLDER)

    if not raw_root.exists():
        print(f"'{RAW_PHOTOS_FOLDER}/' folder not found.")
        print(f"Create it with one subfolder per breed and add your photos, e.g.:")
        for breed in BREED_FOLDERS:
            print(f"    {RAW_PHOTOS_FOLDER}/{breed}/")
        return

    if output_root.exists():
        shutil.rmtree(output_root)  # start fresh each run
    output_root.mkdir(parents=True)

    print("Preparing dataset...\n")

    total_original = 0
    total_final = 0

    for breed in BREED_FOLDERS:
        breed_input_folder = raw_root / breed
        breed_output_folder = output_root / breed
        breed_output_folder.mkdir(parents=True, exist_ok=True)

        if not breed_input_folder.exists():
            print(f"[{breed}] No folder found - skipping. "
                  f"Create '{breed_input_folder}/' and add photos.")
            continue

        print(f"[{breed}]")
        valid_images = clean_raw_folder(breed_input_folder)

        if not valid_images:
            print(f"    - No valid images found in this folder yet.")
            continue

        image_count_for_breed = 0

        for i, image_path in enumerate(valid_images):
            try:
                with Image.open(image_path) as original:
                    resized_original = resize_for_training(original)

                    # Save the cleaned-up original
                    save_path = breed_output_folder / f"{breed}_{i:04d}_original.jpg"
                    resized_original.save(save_path, "JPEG", quality=90)
                    image_count_for_breed += 1

                    # Save the augmented variations
                    for j, variant in enumerate(augment_image(resized_original)):
                        variant_path = breed_output_folder / f"{breed}_{i:04d}_aug{j}.jpg"
                        variant.convert("RGB").save(variant_path, "JPEG", quality=90)
                        image_count_for_breed += 1

            except Exception as error:
                print(f"    - Could not process {image_path.name}: {error}")

        print(f"    - {len(valid_images)} original photo(s) -> {image_count_for_breed} training images\n")

        total_original += len(valid_images)
        total_final += image_count_for_breed

    print("=" * 50)
    print(f"DONE. {total_original} original photos became {total_final} training images.")
    print(f"Ready-to-use dataset is in: {output_root}/")
    print("=" * 50)


if __name__ == "__main__":
    prepare_dataset()
