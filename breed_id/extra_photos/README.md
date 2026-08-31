Drop extra JPEG/PNG photos here, one subfolder per breed, then run:

    python3 scripts/ingest_extra_photos.py
    python3 scripts/retrain_breed_model.py

Afrikaner example:

    extra_photos/Afrikaner/bull_01.jpg
    extra_photos/Afrikaner/cow_veld.jpg

Only keep photos that clearly show that breed. Search downloads often
include Nguni, buffalo, or wildlife, which will *lower* Afrikaner accuracy.
