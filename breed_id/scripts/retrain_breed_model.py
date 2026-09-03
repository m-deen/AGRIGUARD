"""
Retrain MobileNetV2 breed classifier on cleaned local dataset.
Exports breed_classifier.h5 + breed_classifier.onnx for AgriGuard inference.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import tensorflow as tf
from tensorflow.keras import layers, models
from tensorflow.keras.applications.mobilenet_v2 import MobileNetV2, preprocess_input

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "dataset"
MODELS = ROOT / "models"
LABELS_PATH = ROOT / "class_labels.json"

IMG_SIZE = (224, 224)
BATCH = 16
SEED = 42
EPOCHS_FROZEN = 6
EPOCHS_FINETUNE = 8
# Alphabetical folder order must match inference labels
CLASS_NAMES = ["Afrikaner", "Boer_Goat", "Bonsmara", "Dorper", "Nguni"]
DISPLAY_NAMES = ["Afrikaner", "Boer Goat", "Bonsmara", "Dorper", "Nguni"]


def make_datasets():
    train_ds = tf.keras.utils.image_dataset_from_directory(
        DATA / "train",
        labels="inferred",
        label_mode="categorical",
        class_names=CLASS_NAMES,
        image_size=IMG_SIZE,
        batch_size=BATCH,
        shuffle=True,
        seed=SEED,
    )
    val_ds = tf.keras.utils.image_dataset_from_directory(
        DATA / "test",
        labels="inferred",
        label_mode="categorical",
        class_names=CLASS_NAMES,
        image_size=IMG_SIZE,
        batch_size=BATCH,
        shuffle=False,
    )

    aug = tf.keras.Sequential(
        [
            layers.RandomFlip("horizontal"),
            layers.RandomRotation(0.08),
            layers.RandomZoom(0.12),
            layers.RandomContrast(0.12),
        ],
        name="aug",
    )

    def prep_train(x, y):
        x = tf.cast(x, tf.float32)
        x = aug(x, training=True)
        x = preprocess_input(x)
        return x, y

    def prep_val(x, y):
        x = preprocess_input(tf.cast(x, tf.float32))
        return x, y

    autotune = tf.data.AUTOTUNE
    train_ds = train_ds.map(prep_train, num_parallel_calls=autotune).prefetch(autotune)
    val_ds = val_ds.map(prep_val, num_parallel_calls=autotune).prefetch(autotune)
    return train_ds, val_ds


def class_weights_from_counts() -> dict:
    counts = []
    for name in CLASS_NAMES:
        n = len(list((DATA / "train" / name).glob("*")))
        counts.append(max(n, 1))
    total = sum(counts)
    # Balanced weights: rarer classes weigh more
    weights = {i: (total / (len(counts) * c)) for i, c in enumerate(counts)}
    print("class_weights", {DISPLAY_NAMES[i]: round(w, 2) for i, w in weights.items()})
    return weights


def build_model(trainable_base: bool = False) -> tf.keras.Model:
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
    model = models.Model(inputs, outputs)
    return model


def export_onnx(h5_path: Path, onnx_path: Path):
    import tf2onnx

    model = tf.keras.models.load_model(h5_path, compile=False)
    spec = (tf.TensorSpec((None, 224, 224, 3), tf.float32, name="input"),)
    model_proto, _ = tf2onnx.convert.from_keras(model, input_signature=spec, opset=13)
    onnx_path.write_bytes(model_proto.SerializeToString())
    print("Wrote", onnx_path)


def main():
    MODELS.mkdir(parents=True, exist_ok=True)
    train_ds, val_ds = make_datasets()
    weights = class_weights_from_counts()

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
        epochs=EPOCHS_FROZEN,
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
        epochs=EPOCHS_FINETUNE,
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

    # Confusion on val
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

    try:
        export_onnx(h5_path, MODELS / "breed_classifier.onnx")
    except Exception as exc:
        print("ONNX export failed (will try keras->onnx via alternate):", exc)
        # Fallback: save and convert with onnxruntime path later
        raise


if __name__ == "__main__":
    main()
