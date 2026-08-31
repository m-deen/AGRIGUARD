"""Evaluate the ONNX breed model on breed_id/dataset/{train,test}."""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from breed_id.model_loader import load_class_names, load_model, model_predict_fn  # noqa: E402

FOLDER_TO_LABEL = {
    "Afrikaner": "Afrikaner",
    "Boer_Goat": "Boer Goat",
    "Bonsmara": "Bonsmara",
    "Dorper": "Dorper",
    "Nguni": "Nguni",
}


def preprocess(path: Path) -> np.ndarray:
    image = Image.open(path)
    image = ImageOps.exif_transpose(image).convert("RGB")
    image = image.resize((224, 224), Image.BILINEAR)
    return (np.array(image, dtype=np.float32) / 127.5) - 1.0


def eval_split(split: str) -> dict:
    labels = load_class_names()
    y_true, y_pred = [], []
    root = ROOT / "dataset" / split
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or folder.name not in FOLDER_TO_LABEL:
            continue
        true = FOLDER_TO_LABEL[folder.name]
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                continue
            arr = preprocess(path)
            probs = model_predict_fn(arr)
            pred = max(probs, key=probs.get)
            y_true.append(true)
            y_pred.append(pred)

    n = len(y_true)
    acc = float(np.mean([a == b for a, b in zip(y_true, y_pred)])) if n else 0.0
    per = {}
    cm = defaultdict(Counter)
    for t, p in zip(y_true, y_pred):
        cm[t][p] += 1
        per.setdefault(t, {"correct": 0, "n": 0})
        per[t]["n"] += 1
        if t == p:
            per[t]["correct"] += 1
    for name in labels:
        row = per.get(name, {"correct": 0, "n": 0})
        row["recall"] = round(row["correct"] / row["n"], 3) if row["n"] else None
        per[name] = row
    return {
        "split": split,
        "n": n,
        "accuracy": round(acc, 4),
        "per_class": per,
        "confusion": {k: dict(v) for k, v in cm.items()},
    }


def main():
    load_model()
    report = {s: eval_split(s) for s in ("test", "train")}
    out = ROOT / "eval_report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print("Wrote", out)


if __name__ == "__main__":
    main()
