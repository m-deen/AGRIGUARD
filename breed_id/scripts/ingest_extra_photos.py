"""
Add extra breed photos (especially Afrikaner) into dataset/train and dataset/test.

Afrikaner currently has far fewer images than Dorper / Bonsmara. Drop new
photos in extra_photos/Afrikaner/ (or pass --source) then retrain.

HOW TO USE
----------
  1. Put new photos here (JPEG/PNG):

         breed_id/extra_photos/Afrikaner/*.jpg

     Or any folder:

         python3 ingest_extra_photos.py --source ~/Downloads/afrikaner --breed Afrikaner

  2. Check the class balance:

         python3 ingest_extra_photos.py --counts

  3. Ingest (validates, skips duplicates, 80/20 train/test split):

         python3 ingest_extra_photos.py --breed Afrikaner

  4. Retrain:

         python3 retrain_breed_model.py

Skim the new files before training. Search-engine downloads often include
the wrong animal (Nguni, buffalo, wildebeest) which hurts Afrikaner accuracy.
"""
from __future__ import annotations

import argparse
import hashlib
import random
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from breed_config import (  # noqa: E402
    CLASS_FOLDERS,
    DATASET_DIR,
    EXTRA_PHOTOS_DIR,
    IMAGE_EXTS,
    canonical_breed,
    display_name,
)

MIN_SIDE_PX = 80
MAX_SIDE_PX = 1024
TEST_RATIO = 0.2
SEED = 42


def _try_open_image(path: Path):
    try:
        from PIL import Image
    except ImportError as exc:
        raise SystemExit(
            "Pillow is required. Install with:  pip install Pillow"
        ) from exc
    return Image.open(path)


def is_valid_image(path: Path) -> bool:
    if path.suffix.lower() not in IMAGE_EXTS:
        return False
    try:
        with _try_open_image(path) as img:
            img.verify()
        with _try_open_image(path) as img:
            width, height = img.size
            if width < MIN_SIDE_PX or height < MIN_SIDE_PX:
                return False
            img.convert("RGB")
        return True
    except Exception:
        return False


def file_md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def iter_images(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    files = []
    for path in sorted(folder.iterdir()):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTS:
            files.append(path)
    return files


def existing_hashes() -> set[str]:
    hashes: set[str] = set()
    for split in ("train", "test"):
        for breed in CLASS_FOLDERS:
            for path in iter_images(DATASET_DIR / split / breed):
                try:
                    hashes.add(file_md5(path))
                except OSError:
                    continue
    return hashes


def count_split(split: str) -> dict[str, int]:
    counts = {}
    for breed in CLASS_FOLDERS:
        counts[breed] = len(iter_images(DATASET_DIR / split / breed))
    return counts


def print_counts(title: str = "Current dataset") -> None:
    train = count_split("train")
    test = count_split("test")
    print(f"\n{title}")
    print(f"{'breed':<12} {'train':>7} {'test':>7} {'total':>7}")
    print("-" * 36)
    for breed in CLASS_FOLDERS:
        t, v = train[breed], test[breed]
        print(f"{display_name(breed):<12} {t:>7} {v:>7} {t + v:>7}")
    print("-" * 36)
    tt, tv = sum(train.values()), sum(test.values())
    print(f"{'ALL':<12} {tt:>7} {tv:>7} {tt + tv:>7}")
    minority = min(CLASS_FOLDERS, key=lambda b: train[b])
    majority = max(CLASS_FOLDERS, key=lambda b: train[b])
    if train[majority] and train[minority] * 4 < train[majority]:
        print(
            f"\nNote: {display_name(minority)} train set is thin "
            f"({train[minority]} vs {train[majority]} {display_name(majority)}). "
            "Add more unique photos of that breed, then retrain."
        )


def resolve_source_folders(source: Path | None, breed: str | None) -> list[tuple[str, Path]]:
    """Return (canonical_breed, folder) pairs to ingest."""
    pairs: list[tuple[str, Path]] = []
    if source:
        folder = Path(source).expanduser().resolve()
        if not folder.is_dir():
            raise SystemExit(f"Source folder not found: {folder}")
        # If source itself is a breed folder of images:
        if breed:
            canon = canonical_breed(breed)
            if not canon:
                raise SystemExit(f"Unknown breed '{breed}'. Use one of: {CLASS_FOLDERS}")
            pairs.append((canon, folder))
            return pairs
        # Otherwise treat subfolders as breed names, and also accept images in source
        # if they match a --breed we already handled.
        for child in sorted(folder.iterdir()):
            if child.is_dir():
                canon = canonical_breed(child.name)
                if canon:
                    pairs.append((canon, child))
        if not pairs:
            raise SystemExit(
                f"No breed subfolders in {folder}. Pass --breed Afrikaner "
                "if this folder contains Afrikaner photos directly."
            )
        return pairs

    root = EXTRA_PHOTOS_DIR
    if breed:
        canon = canonical_breed(breed)
        if not canon:
            raise SystemExit(f"Unknown breed '{breed}'. Use one of: {CLASS_FOLDERS}")
        folder = root / canon
        if not folder.is_dir():
            # also accept display-name folders, e.g. extra_photos/Boer Goat
            for alias_folder in root.glob("*"):
                if alias_folder.is_dir() and canonical_breed(alias_folder.name) == canon:
                    folder = alias_folder
                    break
        pairs.append((canon, folder))
        return pairs

    if not root.is_dir():
        return []
    for child in sorted(root.iterdir()):
        if child.is_dir():
            canon = canonical_breed(child.name)
            if canon:
                pairs.append((canon, child))
    return pairs


def save_training_jpeg(source: Path, dest: Path) -> Path:
    """Write an RGB JPEG, capped so the git dataset stays small."""
    from PIL import Image

    dest = dest.with_suffix(".jpg")
    with Image.open(source) as img:
        img = img.convert("RGB")
        w, h = img.size
        longest = max(w, h)
        if longest > MAX_SIDE_PX:
            scale = MAX_SIDE_PX / longest
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
        img.save(dest, "JPEG", quality=90)
    return dest


def unique_dest(folder: Path, original: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    stem = original.stem.replace(" ", "_")
    dest = folder / f"{stem}.jpg"
    n = 1
    while dest.exists():
        dest = folder / f"{stem}_{n}.jpg"
        n += 1
    return dest


def ingest_folder(breed: str, source: Path, test_ratio: float, rng: random.Random) -> dict[str, int]:
    stats = {"kept_train": 0, "kept_test": 0, "skipped_invalid": 0, "skipped_dup": 0}
    if not source.is_dir():
        print(f"[{display_name(breed)}] no folder at {source} — skipping")
        return stats

    images = iter_images(source)
    if not images:
        print(f"[{display_name(breed)}] {source} has no image files — skipping")
        return stats

    seen = existing_hashes()
    valid: list[Path] = []
    for path in images:
        if not is_valid_image(path):
            stats["skipped_invalid"] += 1
            continue
        digest = file_md5(path)
        if digest in seen:
            stats["skipped_dup"] += 1
            continue
        seen.add(digest)
        valid.append(path)

    rng.shuffle(valid)
    n_test = int(round(len(valid) * test_ratio)) if len(valid) >= 5 else max(0, len(valid) // 5)
    # Keep at least one train image when we have any valid files
    if valid and n_test >= len(valid):
        n_test = max(0, len(valid) - 1)
    test_files = set(valid[:n_test])

    train_dir = DATASET_DIR / "train" / breed
    test_dir = DATASET_DIR / "test" / breed
    train_dir.mkdir(parents=True, exist_ok=True)
    test_dir.mkdir(parents=True, exist_ok=True)

    for path in valid:
        split_dir = test_dir if path in test_files else train_dir
        dest = unique_dest(split_dir, path)
        save_training_jpeg(path, dest)
        if path in test_files:
            stats["kept_test"] += 1
        else:
            stats["kept_train"] += 1

    print(
        f"[{display_name(breed)}] {len(images)} found → "
        f"+{stats['kept_train']} train, +{stats['kept_test']} test "
        f"(skipped {stats['skipped_invalid']} invalid, {stats['skipped_dup']} duplicate)"
    )
    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy extra breed photos into dataset/train and dataset/test."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="Folder of new photos (or a parent with breed subfolders). "
        "Default: breed_id/extra_photos/",
    )
    parser.add_argument(
        "--breed",
        default=None,
        help="Breed to ingest (Afrikaner, Boer_Goat, Bonsmara, Dorper, Nguni). "
        "Required if --source is a flat folder of images.",
    )
    parser.add_argument(
        "--test-ratio",
        type=float,
        default=TEST_RATIO,
        help="Fraction of NEW photos to hold out for test (default 0.2).",
    )
    parser.add_argument(
        "--counts",
        action="store_true",
        help="Print train/test counts and exit (does not copy files).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=SEED,
        help="Shuffle seed for the train/test split of new photos.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.counts:
        print_counts()
        return

    pairs = resolve_source_folders(args.source, args.breed)
    if not pairs:
        EXTRA_PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
        for breed in CLASS_FOLDERS:
            (EXTRA_PHOTOS_DIR / breed).mkdir(exist_ok=True)
        print("No extra photos found.")
        print(f"Drop JPEG/PNG files in {EXTRA_PHOTOS_DIR / 'Afrikaner'}/")
        print("or run:  python3 ingest_extra_photos.py --source /path/to/photos --breed Afrikaner")
        print_counts()
        return

    print_counts("Before ingest")
    rng = random.Random(args.seed)
    for breed, folder in pairs:
        ingest_folder(breed, folder, args.test_ratio, rng)
    print_counts("After ingest")
    print("\nNext: skim the new files, then run  python3 retrain_breed_model.py")


if __name__ == "__main__":
    main()
