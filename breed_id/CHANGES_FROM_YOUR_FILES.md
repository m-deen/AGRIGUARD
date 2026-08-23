# Changes vs your Downloads originals

**Your originals in `d:\Downloads\` were not modified.**  
Copies / adapted versions live only under `breed_id/`.

## `breed_identification.py`
Copied into `breed_id/breed_identification.py` with these updates:
1. Added **Afrikaner** care entry (your `.h5` includes this class)
2. Added `normalize_breed_name()` so `Boer_Goat` ↔ `Boer Goat`
3. Same REQ-31…40 pipeline and `identify_breed_from_photo()` API

## `download_breed_photos.py` / `prepare_dataset.py`
Copied **as-is** into `breed_id/scripts/` (no logic changes).

## New glue (yours didn’t have this)
- `model_loader.py` — loads `breed_classifier.h5` → `model_predict_fn`
- `class_labels.json` — index → breed name
- `standalone_app.py` + `demo.html` — test on port 5001
- `INTEGRATION.md` — how to plug into AgriGuard later

## Main AgriGuard app
**No changes** to `Backend/app.py`, Frontend nav, or login.
