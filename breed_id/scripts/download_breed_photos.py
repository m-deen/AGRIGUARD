"""
AgriGuard - Bulk Breed Photo Downloader
==========================================
WHAT THIS DOES, IN PLAIN ENGLISH:

Instead of manually saving photos one by one from Pinterest/Google
(slow, and never enough for a niche breed like Nguni or Dorper), this
script automatically searches and downloads real JPEG/PNG photos in
bulk, straight into the folder structure your prepare_dataset.py
script already expects:

    raw_photos/
      Nguni/          <- filled automatically
      Bonsmara/       <- filled automatically
      Boer Goat/      <- filled automatically
      Dorper/         <- filled automatically
      Angus/          <- filled automatically
      Brahman/        <- filled automatically

WHY MULTIPLE SEARCH PHRASES PER BREED?
A single search ("Nguni cattle") runs out of new, unique, decent
results fairly quickly - usually well under 500 for a niche breed.
So instead of one search, this script runs SEVERAL different phrasings
per breed ("Nguni cattle", "Nguni bull", "Nguni cow calf", "Nguni
cattle herd South Africa"...) across TWO search engines (Bing and
Google). Each phrasing/engine combination surfaces a different set of
photos, so the total unique pool per breed is much larger than any
single search would give you.

Realistically, this gets you into the low-to-mid hundreds of unique
real photos per breed for a niche breed like Nguni or Dorper - not
guaranteed to hit exactly 500. Combine the result with
prepare_dataset.py's 5x augmentation afterwards to comfortably clear
500+ TRAINING images per breed, which is what actually matters for
your model - it doesn't need 500 different real photos, it needs 500+
varied training examples, and augmentation is a legitimate, standard
way to get there.

HOW TO USE IT:
    1. Install the downloader library (only needs to be done once):
         pip install icrawler --break-system-packages

    2. Run this script:
         python3 download_breed_photos.py

    3. Once done, check how many images you actually got per breed
       (the script prints a summary), then run prepare_dataset.py to
       clean and augment everything.

IMPORTANT - READ THIS BEFORE SUBMITTING YOUR PROJECT:
    Images downloaded this way come from the open web and are NOT all
    guaranteed to be copyright-free. This is a completely normal and
    widely-used technique for building small academic training
    datasets - but you should:
      - Only use these images for MODEL TRAINING, not for anything
        published or sold outside of your academic submission
      - Mention in your report exactly how the dataset was gathered
        (this script + search terms), which markers respect far more
        than pretending the dataset appeared from nowhere
      - Manually skim through a sample of downloaded images before
        training - automated search occasionally pulls in unrelated
        photos (diagrams, unrelated animals, memes)
"""

import time
import hashlib
from pathlib import Path

from icrawler.builtin import BingImageCrawler, GoogleImageCrawler


# ---------------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------------

OUTPUT_ROOT = "raw_photos"
IMAGES_PER_QUERY = 100   # how many images to request per search phrase/engine

# Several different phrasings per breed = a much bigger combined pool
# than searching just the breed name once.
BREED_SEARCH_QUERIES = {
    "Afrikaner": [
        "Afrikaner cattle South Africa",
        "Afrikaner bull long horns",
        "Afrikaner cow red cattle",
        "Africander cattle herd farm",
    ],
    "Nguni": [
        "Nguni cattle South Africa",
        "Nguni bull",
        "Nguni cow calf",
        "Nguni cattle herd farm",
    ],
    "Bonsmara": [
        "Bonsmara cattle South Africa",
        "Bonsmara bull",
        "Bonsmara cow herd",
        "Bonsmara cattle farm",
    ],
    "Boer Goat": [
        "Boer goat South Africa",
        "Boer goat buck",
        "Boer goat doe kid",
        "Boer goat white body brown head",
    ],
    "Dorper": [
        "Dorper sheep South Africa",
        "Dorper ram black head",
        "Dorper ewe lamb",
        "Dorper sheep flock farm",
    ],
    # Related breeds (not in the current 5-class model — collect for a later expansion)
    "Kalahari Red": [
        "Kalahari Red goat South Africa",
        "Kalahari Red goat buck",
        "Kalahari Red goat herd",
        "Kalahari Red goat farm",
    ],
    "Savanna Goat": [
        "Savanna goat South Africa",
        "Savanna goat white",
        "Savanna goat herd",
        "Savanna goat farm breed",
    ],
    "Merino": [
        "Merino sheep South Africa",
        "Merino ram wool",
        "Merino ewe lamb",
        "Merino sheep flock farm",
    ],
    "Damara": [
        "Damara sheep South Africa",
        "Damara fat tail sheep",
        "Damara ewe lamb",
        "Damara sheep flock farm",
    ],
    "Angus": [
        "Angus cattle",
        "Black Angus bull",
        "Angus cow calf",
        "Angus cattle herd farm",
    ],
    "Brahman": [
        "Brahman cattle",
        "Brahman bull",
        "Brahman cow calf",
        "Brahman cattle herd farm",
    ],
}


# ---------------------------------------------------------------------------
# DOWNLOADER
# ---------------------------------------------------------------------------

def download_photos_for_breed(breed_name: str, search_queries: list[str], images_per_query: int):
    """
    Runs every search phrase for this breed across BOTH Bing and
    Google, all saving into the same folder. `file_idx_offset='auto'`
    tells icrawler to keep numbering upward instead of overwriting
    files from the previous search.
    """
    output_folder = Path(OUTPUT_ROOT) / breed_name
    output_folder.mkdir(parents=True, exist_ok=True)

    print(f"\n[{breed_name}] Starting - {len(search_queries)} search phrases x 2 engines")

    for query in search_queries:
        for engine_name, CrawlerClass in [("Bing", BingImageCrawler), ("Google", GoogleImageCrawler)]:
            print(f"    - {engine_name}: \"{query}\"")
            try:
                crawler = CrawlerClass(storage={"root_dir": str(output_folder)})
                crawler.crawl(keyword=query, max_num=images_per_query, file_idx_offset="auto")
            except Exception as error:
                print(f"      (skipped due to error: {error})")
            time.sleep(1)  # polite pause between requests

    file_count = len(list(output_folder.glob("*")))
    print(f"[{breed_name}] Subtotal after downloading: {file_count} file(s)")
    return output_folder


def remove_duplicate_images(folder: Path) -> int:
    """
    Different search phrases sometimes return the exact same photo.
    This hashes every file's content and deletes exact duplicates,
    keeping only the first copy of each. Returns how many were removed.
    """
    seen_hashes = set()
    removed_count = 0

    for file_path in list(folder.glob("*")):
        if not file_path.is_file():
            continue
        try:
            file_hash = hashlib.md5(file_path.read_bytes()).hexdigest()
        except Exception:
            continue

        if file_hash in seen_hashes:
            file_path.unlink()
            removed_count += 1
        else:
            seen_hashes.add(file_hash)

    return removed_count


def download_all_breeds():
    print("Starting bulk download (multiple phrasings x 2 search engines per breed)...")

    summary = {}

    for breed_name, queries in BREED_SEARCH_QUERIES.items():
        folder = download_photos_for_breed(breed_name, queries, IMAGES_PER_QUERY)
        removed = remove_duplicate_images(folder)
        final_count = len(list(folder.glob("*")))

        if removed:
            print(f"[{breed_name}] Removed {removed} exact duplicate(s) -> {final_count} unique file(s)")

        summary[breed_name] = final_count

    print("\n" + "=" * 55)
    print("DOWNLOAD SUMMARY")
    print("=" * 55)
    for breed_name, count in summary.items():
        note = "" if count >= 100 else "  <- lower than hoped, consider adding more search phrases"
        print(f"  {breed_name:<12} {count} unique photo(s){note}")
    print("=" * 55)
    print(f"Photos saved in: {OUTPUT_ROOT}/")
    print("Next step: run prepare_dataset.py - it will 5x this with augmentation,")
    print("so e.g. 150 unique real photos becomes 750 training images.")
    print("=" * 55)


if __name__ == "__main__":
    download_all_breeds()
