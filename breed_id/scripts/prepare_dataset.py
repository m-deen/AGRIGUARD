"""
AgriGuard - Dataset Preparation Script
=========================================
Cleans raw downloaded photos and (optionally) writes them into the
train/test folders that retrain_breed_model.py uses.

Preferred path when you already have extra Afrikaner photos:

    python3 ingest_extra_photos.py --source /path/to/photos --breed Afrikaner
    python3 retrain_breed_model.py

This script is useful when you have a raw_photos/ dump from
download_breed_photos.py --into-raw and want a cleaned, resized copy.

    python3 prepare_dataset.py --into-dataset
    python3 retrain_breed_model.py
"""
from __future__ import annotations

import argparse
import random
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageEnhance

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from breed_config import (  # noqa: E402
    CLASS_FOLDERS,
    DATASET_DIR,
    RAW_PHOTOS_DIR,
    canonical_breed,
    display_name,
)

TARGET_SIZE = (224, 224)
AUGMENTATIONS_PER_IMAGE = 4
TEST_RATIO = 0.2
SEED = 42


def is_valid_image(file_path: Path) -> bool:
    try:
        with Image.open(file_path) as img:
            img.verify()
        with Image.open(file_path) as img:
            width, height = img.size
            if width < 50 or height < 50:
                return False
        return True
    except Exception:
        return False


def clean_raw_folder(breed_folder: Path) -> list[Path]:
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


def augment_image(image: Image.Image) -> list[Image.Image]:
    variations = []
    variations.append(image.transpose(Image.FLIP_LEFT_RIGHT))
    variations.append(image.rotate(15, expand=False, fillcolor=(255, 255, 255)))
    variations.append(ImageEnhance.Brightness(image).enhance(1.3))
    variations.append(ImageEnhance.Brightness(image).enhance(0.7))
    return variations


def resize_for_training(image: Image.Image) -> Image.Image:
    return image.convert("RGB").resize(TARGET_SIZE)


def write_image(image: Image.Image, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(dest, "JPEG", quality=90)


def prepare_dataset(raw_root: Path, output_root: Path, augment: bool, into_dataset: bool) -> None:
    if not raw_root.exists():
        print(f"'{raw_root}/' folder not found.")
        print("Create it with one subfolder per breed and add your photos, e.g.:")
        for breed in CLASS_FOLDERS:
            print(f"    {raw_root}/{breed}/")
        print("\nOr skip this script and run ingest_extra_photos.py on extra_photos/.")
        return

    rng = random.Random(SEED)
    if into_dataset:
        output_root = DATASET_DIR
        print(f"Writing cleaned originals into {output_root}/train and {output_root}/test")
        print("(Keras already augments on the fly during retrain — no extra copies.)\n")
    else:
        if output_root.exists():
            shutil.rmtree(output_root)
        output_root.mkdir(parents=True)

    print("Preparing dataset...\n")
    total_original = 0
    total_final = 0

    # Accept both CLASS_FOLDERS names and aliases in raw_photos/
    discovered: dict[str, Path] = {}
    for child in raw_root.iterdir():
        if child.is_dir():
            canon = canonical_breed(child.name)
            if canon:
                discovered[canon] = child

    for breed in CLASS_FOLDERS:
        breed_input_folder = discovered.get(breed, raw_root / breed)
        print(f"[{display_name(breed)}]")
        if not breed_input_folder.exists():
            print(f"    - No folder found — create '{raw_root / breed}/' and add photos.\n")
            continue

        valid_images = clean_raw_folder(breed_input_folder)
        if not valid_images:
            print("    - No valid images found in this folder yet.\n")
            continue

        if into_dataset:
            rng.shuffle(valid_images)
            n_test = int(round(len(valid_images) * TEST_RATIO)) if len(valid_images) >= 5 else max(
                0, len(valid_images) // 5
            )
            if valid_images and n_test >= len(valid_images):
                n_test = max(0, len(valid_images) - 1)
            test_set = set(valid_images[:n_test])
            added = 0
            for i, image_path in enumerate(valid_images):
                split = "test" if image_path in test_set else "train"
                dest_dir = output_root / split / breed
                dest_dir.mkdir(parents=True, exist_ok=True)
                try:
                    with Image.open(image_path) as original:
                        resized = resize_for_training(original)
                        dest = dest_dir / f"{breed}_{i:04d}_original.jpg"
                        n = 1
                        while dest.exists():
                            dest = dest_dir / f"{breed}_{i:04d}_original_{n}.jpg"
                            n += 1
                        write_image(resized, dest)
                        added += 1
                except Exception as error:
                    print(f"    - Could not process {image_path.name}: {error}")
            print(
                f"    - {len(valid_images)} original photo(s) -> "
                f"{added} copied into train/test\n"
            )
            total_original += len(valid_images)
            total_final += added
            continue

        breed_output_folder = output_root / breed
        breed_output_folder.mkdir(parents=True, exist_ok=True)
        image_count_for_breed = 0
        for i, image_path in enumerate(valid_images):
            try:
                with Image.open(image_path) as original:
                    resized_original = resize_for_training(original)
                    save_path = breed_output_folder / f"{breed}_{i:04d}_original.jpg"
                    write_image(resized_original, save_path)
                    image_count_for_breed += 1
                    if augment:
                        for j, variant in enumerate(augment_image(resized_original)):
                            variant_path = breed_output_folder / f"{breed}_{i:04d}_aug{j}.jpg"
                            write_image(variant, variant_path)
                            image_count_for_breed += 1
            except Exception as error:
                print(f"    - Could not process {image_path.name}: {error}")

        print(
            f"    - {len(valid_images)} original photo(s) -> "
            f"{image_count_for_breed} training images\n"
        )
        total_original += len(valid_images)
        total_final += image_count_for_breed

    print("=" * 50)
    print(f"DONE. {total_original} original photos became {total_final} files.")
    print(f"Output: {output_root}/")
    if into_dataset:
        print("Next: python3 retrain_breed_model.py")
    print("=" * 50)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean raw breed photos for training.")
    parser.add_argument(
        "--raw",
        type=Path,
        default=RAW_PHOTOS_DIR,
        help="Folder with one subfolder per breed (default: breed_id/raw_photos).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Legacy output folder (default: breed_id/dataset_ready).",
    )
    parser.add_argument(
        "--into-dataset",
        action="store_true",
        help="Write into dataset/train and dataset/test (what retrain uses).",
    )
    parser.add_argument(
        "--no-augment",
        action="store_true",
        help="Do not write flipped/rotated copies (retrain already augments).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = args.output
    if output is None:
        output = Path(__file__).resolve().parents[1] / "dataset_ready"
    prepare_dataset(
        raw_root=args.raw,
        output_root=output,
        augment=not args.no_augment and not args.into_dataset,
        into_dataset=args.into_dataset,
    )


if __name__ == "__main__":
    main()
