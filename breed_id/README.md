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

## Improve Afrikaner accuracy with more photos

Afrikaner was the smallest class. The labelled download path
(`--engine labeled`) pulls stud-auction lot photos and university slides.
After ingest, Afrikaner is **52 train / 12 test**. More unique photos still
help, especially close-ups without catalog banners. Do not only duplicate
or heavily augment the same handful of pictures.

### 1. Add photos

Put JPEG/PNG files in:

```
breed_id/extra_photos/Afrikaner/
```

Or point at any folder of Afrikaner photos:

```powershell
cd breed_id\scripts
python3 ingest_extra_photos.py --source C:\photos\afrikaner --breed Afrikaner
```

Need to collect images first? Wikimedia Commons is small for this breed.
Prefer labelled Afrikaner sources (stud auctions + university slides):

```powershell
python3 download_breed_photos.py --breed Afrikaner --engine labeled
```

Wikimedia Commons is the next-cleanest option:

```powershell
python3 download_breed_photos.py --breed Afrikaner --engine wikimedia
```

**Review before training.** Delete anything that is not a red Afrikaner /
Africander cow or bull (Nguni, buffalo, wildebeest, sunsets, clip-art).

Check the balance:

```powershell
python3 ingest_extra_photos.py --counts
```

The train set is now **52 unique Afrikaner photos** (plus 12 held-out test).
Keep a held-out test split (the ingest script uses 80/20).

### 2. Retrain (needs TensorFlow — Python 3.12/3.13 venv)

```powershell
cd breed_id
pip install tensorflow tf2onnx Pillow numpy
python3 scripts/retrain_breed_model.py
```

That writes:

- `models/breed_classifier.h5`
- `models/breed_classifier.onnx`  ← what AgriGuard loads
- `class_labels.json`

Restart Flask after the new ONNX file is in place.

`--boost-class Afrikaner` (default) slightly up-weights that class so the
model does not ignore it while Dorper still has more images. Pass
`--boost-factor 1.0` if you want balanced weights only.

### 3. What extra data actually helps

- Close-ups **and** full-body shots, both sexes, calves, different light
- Other red cattle (Bonsmara) labelled correctly, so Afrikaner is not
  confused with every red hide
- **Not** the same photo flipped five times saved into the folder —
  `retrain_breed_model.py` already flips/rotates/zooms on the fly

## Optional standalone demo

```powershell
cd d:\PROJECT\AGRIGUARD\breed_id
# can use main venv too if onnxruntime is installed there
d:\PROJECT\AGRIGUARD\venv\Scripts\python.exe standalone_app.py
```

Open http://localhost:5001 and upload a livestock photo.

## Test the current model (no retrain)

Three ways, from easiest to most thorough:

**1. Farmer page (same path as a real user)**  
Start AgriGuard (`Backend/app.py`), log in as a farmer, open **Breed ID**, upload a JPEG/PNG. Optional Cattle / Sheep / Goat filter is on that page.

**2. One photo from the command line**

```powershell
cd breed_id\scripts
pip install onnxruntime Pillow numpy
python3 evaluate_breed_model.py --image "..\dataset\test\Afrikaner\images (3).jpg"
```

**3. Score the labelled test folder** (prints accuracy per breed)

```powershell
python3 evaluate_breed_model.py
python3 evaluate_breed_model.py --split train
```

`dataset/test/` is the held-out set. Do not judge the model only on `dataset/train/` — those photos were used to train it.

After the labelled-photo retrain, ONNX scores on `dataset/test/` were:

| Breed | Test photos | Correct | Accuracy |
|-------|-------------|---------|----------|
| Afrikaner | 12 | 10 | 83% |
| Boer Goat | 4 | 4 | 100% |
| Bonsmara | 10 | 8 | 80% |
| Dorper | 15 | 15 | 100% |
| Nguni | 10 | 8 | 80% |
| **Overall** | **51** | **45** | **88%** |

Afrikaner was 0% on the same test photos before this retrain (every shot called Bonsmara). Two Afrikaner misses are still Bonsmara, which is the closest red-cattle lookalike.

## Files

| File | Role |
|------|------|
| `breed_identification.py` | Validate → preprocess → top-3 + care |
| `model_loader.py` | ONNX first, TensorFlow fallback |
| `class_labels.json` | Class index → breed name |
| `scripts/ingest_extra_photos.py` | Copy new photos into train/test |
| `scripts/download_breed_photos.py` | Fetch extra Afrikaner photos (`--engine labeled` = stud auctions) |
| `scripts/evaluate_breed_model.py` | Score the current ONNX model on test photos |
| `scripts/retrain_breed_model.py` | Train + export ONNX |
| `scripts/prepare_dataset.py` | Clean a `raw_photos/` dump |

See `INTEGRATION.md` for details.
