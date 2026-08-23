# Breed Identification

Runs on **Python 3.14** via **ONNX Runtime** (TensorFlow has no 3.14 wheels).

## Models

- `models/breed_classifier.onnx` — used by AgriGuard (Py 3.14)
- `models/breed_classifier.h5` — original Keras (optional, needs TF on Py 3.13)

## Use inside AgriGuard

```powershell
cd d:\PROJECT\AGRIGUARD\Backend
..\venv\Scripts\python.exe -m pip install onnxruntime Pillow
..\venv\Scripts\python.exe app.py
```

Login → **Breed ID** → upload a photo.  
Only the main API (:5000) is required.

## Optional standalone demo

```powershell
cd d:\PROJECT\AGRIGUARD\breed_id
# can use main venv too if onnxruntime is installed there
d:\PROJECT\AGRIGUARD\venv\Scripts\python.exe standalone_app.py
```

## Files

| File | Role |
|------|------|
| `breed_identification.py` | Validate → preprocess → top-3 + care |
| `model_loader.py` | ONNX first, TensorFlow fallback |
| `class_labels.json` | Class index → breed name |
| `scripts/` | Download + prepare dataset helpers |

See `INTEGRATION.md` for details.
