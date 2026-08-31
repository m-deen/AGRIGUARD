"""
Score the current ONNX breed model on dataset/test (and optionally train).

Uses the same preprocess + model_loader path as AgriGuard, so the numbers
match what farmers get from Breed ID.

    python3 evaluate_breed_model.py
    python3 evaluate_breed_model.py --split train
    python3 evaluate_breed_model.py --image ../dataset/test/Afrikaner/images.jpg
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from breed_config import CLASS_FOLDERS, DATASET_DIR, IMAGE_EXTS, display_name
from breed_identification import identify_breed_from_photo
from model_loader import load_class_names, load_model, model_predict_fn


def iter_split_images(split: str) -> list[tuple[str, Path]]:
    rows = []
    for folder in CLASS_FOLDERS:
        breed_dir = DATASET_DIR / split / folder
        if not breed_dir.is_dir():
            continue
        for path in sorted(breed_dir.iterdir()):
            if path.is_file() and path.suffix.lower() in IMAGE_EXTS:
                rows.append((display_name(folder), path))
    return rows


def predict_file(path: Path) -> dict:
    result = identify_breed_from_photo(path.read_bytes(), model_predict_fn)
    if not result.get("success"):
        return {
            "ok": False,
            "error": result.get("error") or "identify failed",
            "top": None,
            "confidence": 0.0,
        }
    top = (result.get("predictions") or [{}])[0]
    return {
        "ok": True,
        "error": None,
        "top": top.get("breed"),
        "confidence": float(top.get("confidence_percent") or 0),
        "predictions": result.get("predictions") or [],
    }


def print_confusion(labels: list[str], pairs: list[tuple[str, str]]) -> None:
    print("\nconfusion (rows = true, columns = predicted)")
    header = f"{'true':<12}" + "".join(f"{name[:7]:>8}" for name in labels)
    print(header)
    for true in labels:
        cells = []
        for pred in labels:
            n = sum(1 for t, p in pairs if t == true and p == pred)
            cells.append(f"{n:>8}")
        print(f"{true:<12}" + "".join(cells))


def evaluate_split(split: str) -> None:
    rows = iter_split_images(split)
    if not rows:
        print(f"No images in {DATASET_DIR / split}")
        return

    labels = [display_name(n) for n in CLASS_FOLDERS]
    correct = 0
    per_true = defaultdict(lambda: {"n": 0, "ok": 0})
    pairs: list[tuple[str, str]] = []
    misses: list[str] = []

    print(f"Evaluating {len(rows)} images in dataset/{split}/ …")
    for true_breed, path in rows:
        pred = predict_file(path)
        per_true[true_breed]["n"] += 1
        top = pred["top"] or "?"
        if pred["ok"] and top == true_breed:
            correct += 1
            per_true[true_breed]["ok"] += 1
        else:
            misses.append(
                f"  {path.name}: true={true_breed}  pred={top} "
                f"({pred['confidence']:.0f}%)"
                + (f"  [{pred['error']}]" if pred["error"] else "")
            )
        pairs.append((true_breed, top))

    print(f"\noverall accuracy: {correct}/{len(rows)} = {correct / len(rows):.1%}")
    print(f"{'breed':<12} {'n':>5} {'correct':>8} {'acc':>7}")
    print("-" * 36)
    for name in labels:
        stats = per_true[name]
        acc = stats["ok"] / stats["n"] if stats["n"] else 0.0
        print(f"{name:<12} {stats['n']:>5} {stats['ok']:>8} {acc:>6.0%}")
    print_confusion(labels, pairs)
    if misses:
        print("\nmisses")
        for line in misses:
            print(line)


def evaluate_one(path: Path) -> None:
    pred = predict_file(path)
    print(f"file: {path}")
    if not pred["ok"]:
        print("error:", pred["error"])
        return
    print("top:", pred["top"], f"({pred['confidence']:.1f}%)")
    for i, item in enumerate(pred["predictions"][:3], 1):
        print(f"  #{i} {item.get('breed')}  {item.get('confidence_percent')}%")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test the current Breed ID ONNX model.")
    parser.add_argument(
        "--split",
        choices=["test", "train"],
        default="test",
        help="Score labelled photos in dataset/test (default) or dataset/train.",
    )
    parser.add_argument(
        "--image",
        type=Path,
        default=None,
        help="Score one photo instead of a whole split (any JPEG/PNG).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_model()
    print("classes:", ", ".join(load_class_names()))
    if args.image:
        evaluate_one(args.image.expanduser().resolve())
        return
    evaluate_split(args.split)


if __name__ == "__main__":
    main()
