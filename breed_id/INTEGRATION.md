# Breed ID on Python 3.14

TensorFlow has **no** official wheels for Python 3.14, so the `.h5` model was
converted to **ONNX** and runs with `onnxruntime` inside the main AgriGuard venv.

## Models

| File | Use |
|------|-----|
| `models/breed_classifier.onnx` | **Primary** — works on Py 3.14 |
| `models/breed_classifier.h5` | Original Keras — needs TensorFlow (Py 3.13) |

`model_loader.py` tries ONNX first, then TensorFlow.

## Run (one server)

```powershell
cd d:\PROJECT\AGRIGUARD\Backend
..\venv\Scripts\python.exe -m pip install onnxruntime Pillow
..\venv\Scripts\python.exe app.py
```

Login → **Breed ID** → upload photo.  
No second terminal / port 5001 required anymore.

## Recreate ONNX from .h5 (if you retrain)

After adding extra Afrikaner (or other) photos — see `README.md` — run:

```powershell
cd d:\PROJECT\AGRIGUARD\breed_id
python3 scripts/ingest_extra_photos.py --source <photos> --breed Afrikaner
python3 scripts/retrain_breed_model.py
```

`retrain_breed_model.py` writes `models/breed_classifier.h5` and then
exports `models/breed_classifier.onnx` with tf2onnx. Restart the AgriGuard
API so `model_loader.py` picks up the new ONNX file.

To export an existing `.h5` only:

```powershell
cd d:\PROJECT\AGRIGUARD\breed_id
python3 -c "from pathlib import Path; import sys; sys.path.insert(0,'scripts'); from retrain_breed_model import export_onnx, MODELS; export_onnx(MODELS/'breed_classifier.h5', MODELS/'breed_classifier.onnx')"
```
