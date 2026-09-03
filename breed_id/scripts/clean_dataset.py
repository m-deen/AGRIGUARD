"""
Remove labels that cannot teach the 5-class breed model.

Rules come from a full pass of breed_id/dataset (web-crawled + WhatsApp):
  - wildlife labelled as Afrikaner (wildebeest, Cape buffalo)
  - Nguni / mixed herds labelled as Afrikaner
  - silhouettes and distant mixed pastures
  - European wool sheep, goats and dogs labelled as Dorper
  - Holstein / Jersey dairy cows labelled as Bonsmara

WhatsApp goat photos stay in Boer_Goat (closest goat class). Solid-red
goats are called out as Kalahari Red lookalikes in the live UI, not deleted.
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageEnhance

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "dataset"
MANIFEST_PATH = ROOT / "dataset_clean_manifest.json"

FOLDER_TO_LABEL = {
    "Afrikaner": "Afrikaner",
    "Boer_Goat": "Boer Goat",
    "Bonsmara": "Bonsmara",
    "Dorper": "Dorper",
    "Nguni": "Nguni",
}

# Explicit path relative to dataset/
MOVE_TO_NGUNI = [
    "test/Afrikaner/6.view-of-grey-nguni-cattle-grazing.jpg",
    "test/Afrikaner/10.6253208006_708208284c_b.jpg",
    "train/Afrikaner/images (6).jpg",
]

MOVE_TO_BOER_GOAT = [
    "train/Dorper/87.billy-goat-goats-animal-farm-thumbnail.jpg",
]

EXPLICIT_REJECT = [
    "train/Afrikaner/5.500px-Black_Wildebeest_%28Connochaetes_gnou%29_%2831746882054%29.jpg",
    "train/Afrikaner/61.cows-on-green-pasture-on-a-farm.jpg",
    "test/Afrikaner/51.cow-sunset-silhouette.jpg",
    "test/Afrikaner/20.image-1621449603Wxu.jpg",  # Cape buffalo
    "test/Afrikaner/24.czNmcy1wcml2YXRlL3Jhd3BpeGVsX2ltYWdlcy93ZWJzaXRlX2NvbnRlbnQvbHIvZmwzMjkxNzk1MTU0Ny1wdWJsaWMtaW1hZ2Uta29ucWZzcnEuanBn.jpg",
    "test/Bonsmara/2.czNmcy1wcml2YXRlL3Jhd3BpeGVsX2ltYWdlcy93ZWJzaXRlX2NvbnRlbnQvbHIvZnJhbmltYWxfbWlsa19kYWlyeV9jb3ctaW1hZ2Uta3ljaWNoYWouanBn.jpg",
    "test/Bonsmara/3.cHJpdmF0ZS9zdGF0aWMvaW1hZ2Uvd2Vic2l0ZS8yMDIyLTA0L2xyL2ZyY293X21pbGtfY293X2JlZWZfMC1pbWFnZS1reWJicmkyZy5qcGc.jpg",
]

# Filename tokens that are almost never a Dorper (hair sheep, SA)
DORPER_JUNK_RE = re.compile(
    r"heidschnucke|scotland|ireland|highlands|normandy|sheepdog|"
    r"billy-goat|goats-animal|sheep-chick|s-wool-wool|wool-thumb|"
    r"wool-animal|england-highlands",
    re.I,
)


def _rel(path: Path) -> str:
    return str(path.relative_to(DATA)).replace("\\", "/")


def _unique_dest(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    for i in range(1, 1000):
        candidate = dest.with_name(f"{stem}__{i}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not place {dest}")


def _iter_images():
    for split in ("train", "test"):
        for breed_dir in sorted((DATA / split).iterdir()):
            if not breed_dir.is_dir() or breed_dir.name.startswith("_"):
                continue
            for path in sorted(breed_dir.iterdir()):
                if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
                    yield split, breed_dir.name, path


def _is_aug_of(name: str, original_name: str) -> bool:
    # prepare/retrain saved aug_{originalstem}_{n}.jpg
    stem = Path(original_name).stem
    return name.startswith(f"aug_{stem}_") or name.startswith(f"aug_{original_name}_")


def clean() -> dict:
    actions = []

    # Moves
    for rel in MOVE_TO_NGUNI:
        src = DATA / rel
        if not src.exists():
            continue
        dest = _unique_dest(DATA / "train" / "Nguni" / src.name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        actions.append({
            "action": "move",
            "src": rel,
            "dest": _rel(dest),
            "reason": "relabel: Nguni pattern / named Nguni, not Afrikaner",
        })

    for rel in MOVE_TO_BOER_GOAT:
        src = DATA / rel
        if not src.exists():
            continue
        dest = _unique_dest(DATA / "train" / "Boer_Goat" / src.name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        actions.append({"action": "move", "src": rel, "dest": _rel(dest), "reason": "goat labelled as Dorper"})
        # move matching augs
        for path in (DATA / "train" / "Dorper").glob("*"):
            if path.is_file() and _is_aug_of(path.name, Path(rel).name):
                dest_aug = _unique_dest(DATA / "train" / "Boer_Goat" / path.name)
                shutil.move(str(path), str(dest_aug))
                actions.append({
                    "action": "move",
                    "src": f"train/Dorper/{path.name}",
                    "dest": _rel(dest_aug),
                    "reason": "aug of goat labelled as Dorper",
                })

    reject_names = {Path(r).name for r in EXPLICIT_REJECT}
    for rel in EXPLICIT_REJECT:
        src = DATA / rel
        if src.exists():
            src.unlink()
            actions.append({"action": "delete", "src": rel, "dest": None, "reason": "unusable or wrong species"})

    # Keyword junk in Dorper + their augmentations
    extra_delete = []
    for split, breed, path in _iter_images():
        if breed != "Dorper":
            continue
        if DORPER_JUNK_RE.search(path.name):
            extra_delete.append(path)

    # Also drop augs whose parent original was deleted
    deleted_stems = {Path(r).stem for r in EXPLICIT_REJECT}
    for path in extra_delete:
        deleted_stems.add(path.stem.replace("aug_", "").rsplit("_", 1)[0] if path.name.startswith("aug_") else path.stem)

    for split, breed, path in list(_iter_images()):
        if path in extra_delete:
            continue
        if path.name.startswith("aug_"):
            # aug_{originalstem}_{i}
            rest = path.name[4:]
            orig_stem = re.sub(r"_\d+\.[^.]+$", "", rest)
            if any(orig_stem.startswith(s) or s.startswith(orig_stem) for s in deleted_stems):
                extra_delete.append(path)

    for path in extra_delete:
        if path.exists():
            rel = _rel(path)
            path.unlink()
            actions.append({
                "action": "delete",
                "src": rel,
                "dest": None,
                "reason": "wrong species/geography for Dorper (wool sheep, goat, dog, EU breed)",
            })

    # Rebalance Afrikaner: keep a real hold-out, rest in train
    af_test = DATA / "test" / "Afrikaner"
    af_train = DATA / "train" / "Afrikaner"
    af_train.mkdir(parents=True, exist_ok=True)
    remaining_test = [p for p in af_test.iterdir() if p.is_file()]
    # Keep one hold-out if we have 2+
    remaining_test.sort(key=lambda p: p.name)
    for path in remaining_test[:-1] if len(remaining_test) > 1 else []:
        dest = _unique_dest(af_train / path.name)
        shutil.move(str(path), str(dest))
        actions.append({
            "action": "move",
            "src": f"test/Afrikaner/{path.name}",
            "dest": _rel(dest),
            "reason": "Afrikaner had more test than train; move extras into train",
        })

    # Boost remaining Afrikaner train images with the same 4 augs used in prepare_dataset
    boosted = 0
    for path in list(af_train.iterdir()):
        if not path.is_file() or path.name.startswith("aug_"):
            continue
        try:
            with Image.open(path) as original:
                img = original.convert("RGB")
                variants = [
                    img.transpose(Image.FLIP_LEFT_RIGHT),
                    img.rotate(12, expand=False, fillcolor=(255, 255, 255)),
                    ImageEnhance.Brightness(img).enhance(1.25),
                    ImageEnhance.Brightness(img).enhance(0.8),
                ]
                for i, variant in enumerate(variants):
                    dest = af_train / f"aug_{path.stem}_{i}.jpg"
                    if dest.exists():
                        continue
                    variant.convert("RGB").save(dest, "JPEG", quality=90)
                    boosted += 1
                    actions.append({
                        "action": "augment",
                        "src": _rel(path),
                        "dest": _rel(dest),
                        "reason": "minority-class boost for Afrikaner",
                    })
        except Exception as exc:
            actions.append({"action": "skip_aug", "src": _rel(path), "dest": None, "reason": str(exc)})

    counts = {}
    for split in ("train", "test"):
        counts[split] = {}
        for breed in FOLDER_TO_LABEL:
            folder = DATA / split / breed
            n = len([p for p in folder.iterdir() if p.is_file()]) if folder.exists() else 0
            counts[split][breed] = n

    manifest = {
        "cleaned_at": datetime.now(timezone.utc).isoformat(),
        "boosted_afrikaner_augs": boosted,
        "counts": counts,
        "actions": actions,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(counts, indent=2))
    print(f"Wrote {MANIFEST_PATH} ({len(actions)} actions, {boosted} Afrikaner augs)")
    return manifest


if __name__ == "__main__":
    clean()
