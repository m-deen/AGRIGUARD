"""
Retrain MobileNetV2 breed classifier on cleaned local dataset.
Exports breed_classifier.h5 + breed_classifier.onnx for AgriGuard inference.

After adding extra Afrikaner (or other) photos:

    python3 ingest_extra_photos.py --counts
    python3 retrain_breed_model.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from breed_config import (  # noqa: E402
    CLASS_FOLDERS,
    DATASET_DIR,
    DISPLAY_NAMES,
    LABELS_PATH,
    MODELS_DIR,
    display_name,
)

IMG_SIZE = (224, 224)
BATCH = 16
SEED = 42
EPOCHS_FROZEN = 8
EPOCHS_FINETUNE = 10
CLASS_NAMES = CLASS_FOLDERS
DATA = DATASET_DIR
MODELS = MODELS_DIR


def count_images(split: str, breed: str) -> int:
    folder = DATA / split / breed
    if not folder.is_dir():
        return 0
    return sum(1 for p in folder.iterdir() if p.is_file())


def print_dataset_counts() -> dict[str, int]:
    print("dataset counts")
    print(f"{'breed':<12} {'train':>7} {'test':>7}")
    print("-" * 28)
    train_counts = {}
    for name in CLASS_NAMES:
        n_train = count_images("train", name)
        n_test = count_images("test", name)
        train_counts[name] = n_train
        print(f"{display_name(name):<12} {n_train:>7} {n_test:>7}")
    print("-" * 28)
    return train_counts


def make_datasets(batch: int):
    import tensorflow as tf
    from tensorflow.keras import layers
    from tensorflow.keras.applications.mobilenet_v2 import preprocess_input

    missing = [n for n in CLASS_NAMES if count_images("train", n) == 0]
    if missing:
        raise SystemExit(
            "No training images for: "
            + ", ".join(display_name(n) for n in missing)
            + f"\nAdd photos under {DATA / 'train'}/ then re-run. "
            "For Afrikaner: python3 ingest_extra_photos.py --breed Afrikaner"
        )

    train_ds = tf.keras.utils.image_dataset_from_directory(
        DATA / "train",
        labels="inferred",
        label_mode="categorical",
        class_names=CLASS_NAMES,
        image_size=IMG_SIZE,
        batch_size=batch,
        shuffle=True,
        seed=SEED,
    )
    val_ds = tf.keras.utils.image_dataset_from_directory(
        DATA / "test",
        labels="inferred",
        label_mode="categorical",
        class_names=CLASS_NAMES,
        image_size=IMG_SIZE,
        batch_size=batch,
        shuffle=False,
    )

    aug = tf.keras.Sequential(
        [
            layers.RandomFlip("horizontal"),
            layers.RandomRotation(0.08),
            layers.RandomZoom(0.18),
            layers.RandomTranslation(0.08, 0.08),
            layers.RandomContrast(0.12),
            layers.RandomBrightness(0.12),
        ],
        name="aug",
    )

    @tf.autograph.experimental.do_not_convert
    def prep_train(x, y):
        x = tf.cast(x, tf.float32)
        x = aug(x, training=True)
        x = preprocess_input(x)
        return x, y

    @tf.autograph.experimental.do_not_convert
    def prep_val(x, y):
        x = preprocess_input(tf.cast(x, tf.float32))
        return x, y

    autotune = tf.data.AUTOTUNE
    train_ds = train_ds.map(prep_train, num_parallel_calls=autotune).prefetch(autotune)
    val_ds = val_ds.map(prep_val, num_parallel_calls=autotune).prefetch(autotune)
    return train_ds, val_ds


def class_weights_from_counts(boost_breed: str | None, boost_factor: float) -> dict:
    counts = []
    for name in CLASS_NAMES:
        counts.append(max(count_images("train", name), 1))
    total = sum(counts)
    weights = {i: (total / (len(counts) * c)) for i, c in enumerate(counts)}
    if boost_breed:
        from breed_config import canonical_breed

        canon = canonical_breed(boost_breed)
        if not canon:
            raise SystemExit(f"Unknown --boost-class '{boost_breed}'")
        idx = CLASS_NAMES.index(canon)
        weights[idx] *= boost_factor
        print(
            f"boosted {display_name(canon)} class weight "
            f"x{boost_factor:g} → {weights[idx]:.2f}"
        )
    print("class_weights", {DISPLAY_NAMES[i]: round(w, 2) for i, w in weights.items()})
    return weights


def build_model(trainable_base: bool = False):
    import tensorflow as tf
    from tensorflow.keras import layers, models
    from tensorflow.keras.applications.mobilenet_v2 import MobileNetV2

    base = MobileNetV2(
        input_shape=IMG_SIZE + (3,),
        include_top=False,
        weights="imagenet",
    )
    base.trainable = trainable_base
    inputs = layers.Input(shape=IMG_SIZE + (3,))
    x = base(inputs, training=False)
    x = layers.GlobalAveragePooling2D()(x)
    x = layers.Dropout(0.35)(x)
    x = layers.Dense(128, activation="relu")(x)
    x = layers.Dropout(0.25)(x)
    outputs = layers.Dense(len(CLASS_NAMES), activation="softmax")(x)
    return models.Model(inputs, outputs)


def export_onnx(h5_path: Path, onnx_path: Path):
    import tensorflow as tf
    import tf2onnx

    model = tf.keras.models.load_model(h5_path, compile=False)
    spec = (tf.TensorSpec((None, 224, 224, 3), tf.float32, name="input"),)
    model_proto, _ = tf2onnx.convert.from_keras(model, input_signature=spec, opset=13)
    onnx_path.write_bytes(model_proto.SerializeToString())
    print("Wrote", onnx_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Retrain the AgriGuard breed classifier (use after adding Afrikaner photos)."
    )
    parser.add_argument("--epochs-frozen", type=int, default=EPOCHS_FROZEN)
    parser.add_argument("--epochs-finetune", type=int, default=EPOCHS_FINETUNE)
    parser.add_argument("--batch", type=int, default=BATCH)
    parser.add_argument(
        "--boost-class",
        default="Afrikaner",
        help="Extra class-weight multiplier target (default Afrikaner). Empty string to disable.",
    )
    parser.add_argument(
        "--boost-factor",
        type=float,
        default=1.4,
        help="Multiply that class's balanced weight (default 1.4). Use 1.0 for no extra boost.",
    )
    parser.add_argument(
        "--counts-only",
        action="store_true",
        help="Print train/test counts and exit (no TensorFlow needed).",
    )
    parser.add_argument(
        "--skip-onnx",
        action="store_true",
        help="Skip ONNX export (needs tf2onnx).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    MODELS.mkdir(parents=True, exist_ok=True)
    train_counts = print_dataset_counts()
    if args.counts_only:
        return

    import numpy as np
    import tensorflow as tf

    train_ds, val_ds = make_datasets(args.batch)
    boost = args.boost_class.strip() if args.boost_class else None
    weights = class_weights_from_counts(boost, args.boost_factor)

    if train_counts.get("Afrikaner", 0) < 20:
        print(
            "\nWARNING: Afrikaner still has few training photos. "
            "Accuracy for that breed stays low until you add more unique images "
            f"to {DATA / 'train' / 'Afrikaner'}/ "
            "(see ingest_extra_photos.py).\n"
        )

    model = build_model(trainable_base=False)
    model.compile(
        optimizer=tf.keras.optimizers.Adam(1e-3),
        loss="categorical_crossentropy",
        metrics=["accuracy"],
    )
    ckpt = MODELS / "breed_classifier_best.keras"
    callbacks = [
        tf.keras.callbacks.ModelCheckpoint(
            ckpt, monitor="val_accuracy", save_best_only=True, verbose=1
        ),
        tf.keras.callbacks.EarlyStopping(
            monitor="val_accuracy", patience=4, restore_best_weights=True
        ),
    ]
    print("=== stage 1: frozen base ===")
    model.fit(
        train_ds,
        validation_data=val_ds,
        epochs=args.epochs_frozen,
        class_weight=weights,
        callbacks=callbacks,
    )

    print("=== stage 2: fine-tune top of MobileNet ===")
    base = model.layers[1]
    base.trainable = True
    for layer in base.layers[:-40]:
        layer.trainable = False
    model.compile(
        optimizer=tf.keras.optimizers.Adam(1e-4),
        loss="categorical_crossentropy",
        metrics=["accuracy"],
    )
    model.fit(
        train_ds,
        validation_data=val_ds,
        epochs=args.epochs_finetune,
        class_weight=weights,
        callbacks=callbacks,
    )

    if ckpt.exists():
        model = tf.keras.models.load_model(ckpt)

    h5_path = MODELS / "breed_classifier.h5"
    model.save(h5_path)
    print("Saved", h5_path)

    LABELS_PATH.write_text(
        json.dumps(
            {
                "note": "Order matches image_dataset_from_directory class_names / softmax index.",
                "labels": DISPLAY_NAMES,
                "preprocess": "mobilenet_v2.preprocess_input (RGB float -> scale to [-1, 1])",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    y_true, y_pred = [], []
    for xb, yb in val_ds:
        probs = model.predict(xb, verbose=0)
        y_pred.extend(np.argmax(probs, axis=1).tolist())
        y_true.extend(np.argmax(yb.numpy(), axis=1).tolist())
    y_true = np.array(y_true)
    y_pred = np.array(y_pred)
    acc = float((y_true == y_pred).mean()) if len(y_true) else 0.0
    print(f"val_accuracy={acc:.3f}")
    for i, name in enumerate(DISPLAY_NAMES):
        mask = y_true == i
        if mask.any():
            print(f"  {name}: {(y_pred[mask] == i).mean():.2f} ({mask.sum()} images)")
        else:
            print(f"  {name}: no test images")

    if args.skip_onnx:
        print("Skipped ONNX export.")
        return
    try:
        export_onnx(h5_path, MODELS / "breed_classifier.onnx")
    except Exception as exc:
        print("ONNX export failed (will try keras->onnx via alternate):", exc)
        raise


if __name__ == "__main__":
    main()
