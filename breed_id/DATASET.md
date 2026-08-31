# Breed ID dataset notes

The live CNN still has **5 classes**: Afrikaner, Boer Goat, Bonsmara, Dorper, Nguni.

`breed_id/dataset/` was a mix of Bing/Google crawls plus farmer WhatsApp photos. A full pass found several problems that were teaching the model the wrong thing:

| Issue | What we found | What we did |
|---|---|---|
| Afrikaner is tiny and dirty | 6 train / 7 test. Train included a **black wildebeest**. Test included **Cape buffalo**, a **sunset silhouette**, **Nguni** cattle, and mixed dairy herds. Test recall for Afrikaner was **0/7**. | Deleted wildlife, silhouettes, mixed herds. Moved real Nguni photos into `Nguni`. Put remaining Afrikaner photos in train and added 4 augmentations each. |
| Dorper is inflated with generic sheep | Filenames such as Heidschnucke, Scottish/Irish hill sheep, wool stock thumbs, a **sheepdog**, and a **billy goat**. That made “any sheep = Dorper”. | Deleted those files (and their `aug_*` copies). Moved the goat into Boer Goat. |
| Bonsmara test had dairy cows | Jersey-type and Holstein-Friesian photos labelled Bonsmara. | Deleted them. |
| Solid-red WhatsApp goats | Farmer photos of **Kalahari Red**-type goats sat in Boer Goat. The model sometimes called them Bonsmara (red cattle). | Kept them in Boer Goat (only goat class). The UI now warns that a solid-red goat may be a Kalahari Red. |
| Class imbalance | Dorper ≫ Afrikaner. | Retrain uses class weights; Afrikaner was augmented. |

## How to photograph (from the failure modes)

1. One animal, filling the frame.
2. Daylight side or three-quarter view (coat colour + horns).
3. No silhouettes, sunsets, mixed herds, or wildlife.
4. Set species (Cattle / Sheep / Goat) when you know it.

## Retrain after cleaning

Needs TensorFlow (Python 3.11–3.13; this Cloud image is 3.12):

```bash
python3 -m pip install tensorflow-cpu tf2onnx Pillow
python3 breed_id/scripts/clean_dataset.py          # safe to re-run; skips missing files
python3 breed_id/scripts/retrain_breed_model.py    # writes .h5 + .onnx + class_labels.json
python3 breed_id/scripts/eval_breed_model.py
```

Do not add extra CNN classes until there are enough **clean, single-animal** photos of that breed. Kalahari Red, Savanna, Merino and Damara already have care notes; they are not model classes yet.
