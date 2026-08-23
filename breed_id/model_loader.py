"""
Loads the breed classifier and exposes model_predict_fn.

Preference order (so AgriGuard Python 3.14 works without TensorFlow):
  1. ONNX Runtime  → breed_classifier.onnx   (recommended on Py 3.14)
  2. TensorFlow    → breed_classifier.h5     (breed_id/.venv on Py 3.13)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
ONNX_PATH = ROOT / "models" / "breed_classifier.onnx"
H5_PATH = ROOT / "models" / "breed_classifier.h5"
LABELS_PATH = ROOT / "class_labels.json"

_backend = None          # "onnx" | "tensorflow"
_session = None          # onnxruntime session
_tf_model = None
_input_name = None
_class_names: list[str] = []


def load_class_names() -> list[str]:
    global _class_names
    if _class_names:
        return _class_names
    with open(LABELS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    _class_names = list(data.get("labels") or data.get("classes") or [])
    if not _class_names:
        raise KeyError("class_labels.json must contain 'labels' or 'classes'")
    return _class_names


def _load_onnx():
    global _backend, _session, _input_name
    import onnxruntime as ort

    if not ONNX_PATH.exists():
        raise FileNotFoundError(f"ONNX model not found: {ONNX_PATH}")

    _session = ort.InferenceSession(
        str(ONNX_PATH),
        providers=["CPUExecutionProvider"],
    )
    _input_name = _session.get_inputs()[0].name
    _backend = "onnx"
    load_class_names()
    print(f"[OK] Loaded ONNX breed model: {ONNX_PATH.name} ({len(_class_names)} classes)")
    return _session


def _load_tensorflow():
    global _backend, _tf_model
    try:
        from tensorflow import keras
    except ImportError as e:
        raise ImportError("TensorFlow not installed") from e

    if not H5_PATH.exists():
        raise FileNotFoundError(f"H5 model not found: {H5_PATH}")

    _tf_model = keras.models.load_model(H5_PATH)
    _backend = "tensorflow"
    load_class_names()
    print(f"[OK] Loaded Keras breed model: {H5_PATH.name} ({len(_class_names)} classes)")
    return _tf_model


def load_model():
    """Load ONNX first, then TensorFlow."""
    global _backend, _session, _tf_model
    if _backend == "onnx" and _session is not None:
        return _session
    if _backend == "tensorflow" and _tf_model is not None:
        return _tf_model

    errors = []
    try:
        return _load_onnx()
    except Exception as e:
        errors.append(f"onnx: {e}")

    try:
        return _load_tensorflow()
    except Exception as e:
        errors.append(f"tensorflow: {e}")

    raise RuntimeError(
        "Could not load breed model. Install onnxruntime (Py 3.14) "
        "or tensorflow (Py 3.13). Details: " + " | ".join(errors)
    )


def get_backend() -> str | None:
    if _backend is None:
        try:
            load_model()
        except Exception:
            return None
    return _backend


def model_predict_fn(preprocessed_image: np.ndarray) -> dict:
    """
    Black-box predict for identify_breed_from_photo().

    Args:
        preprocessed_image: (224, 224, 3) float32, MobileNetV2-scaled [-1, 1]
    """
    load_model()
    classes = load_class_names()
    batch = np.expand_dims(preprocessed_image.astype(np.float32), axis=0)

    if _backend == "onnx":
        probs = _session.run(None, {_input_name: batch})[0][0]
    else:
        probs = _tf_model.predict(batch, verbose=0)[0]

    probs = np.asarray(probs, dtype=np.float32).reshape(-1)
    if len(probs) != len(classes):
        print(
            f"[WARN] Model output size {len(probs)} != label count {len(classes)}. "
            "Update class_labels.json to match training order."
        )
        n = min(len(probs), len(classes))
        return {classes[i]: float(probs[i]) for i in range(n)}

    return {classes[i]: float(probs[i]) for i in range(len(classes))}
