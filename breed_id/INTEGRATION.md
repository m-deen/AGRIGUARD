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

```powershell
cd d:\PROJECT\AGRIGUARD\breed_id
.\.venv\Scripts\python.exe -m pip install tf2onnx
.\.venv\Scripts\python.exe -c "..."  # see project history / ask agent
```
