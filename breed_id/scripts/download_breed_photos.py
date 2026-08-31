"""
AgriGuard - Bulk Breed Photo Downloader
==========================================
Downloads JPEG/PNG photos into extra_photos/<Breed>/ so you can review
them, then ingest_extra_photos.py copies keepers into dataset/train|test.

Afrikaner is the smallest class in the current dataset — run this for
that breed first:

    python3 download_breed_photos.py --breed Afrikaner --engine wikimedia
    python3 download_breed_photos.py --breed Afrikaner --engine bing

Then skim extra_photos/Afrikaner/ and drop anything that is not a
red Afrikaner / Africander cow or bull (Nguni, buffalo, wildebeest,
silhouettes, clip-art). After that:

    python3 ingest_extra_photos.py --breed Afrikaner
    python3 retrain_breed_model.py

Images from the open web are for MODEL TRAINING only. Mention the
collection method in your report.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from breed_config import (  # noqa: E402
    CLASS_FOLDERS,
    EXTRA_PHOTOS_DIR,
    IMAGE_EXTS,
    RAW_PHOTOS_DIR,
    canonical_breed,
    display_name,
)

IMAGES_PER_QUERY = 80

# Several phrasings per breed so the unique pool is larger than one search.
BREED_SEARCH_QUERIES = {
    # "Afrikaner" also means the ethnic group — keep queries livestock-specific.
    "Afrikaner": [
        "Afrikanerbees",
        "Afrikaner oxen trek South Africa",
        "Africander cattle bull farm",
        "red Afrikaner cattle veld horns hump",
        "Afrikaner cow Sanga breed South Africa",
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
    "Boer_Goat": [
        "Boer goat South Africa",
        "Boer goat buck",
        "Boer goat doe kid",
        "Boer goat herd farm",
    ],
    "Dorper": [
        "Dorper sheep South Africa",
        "Dorper ram",
        "Dorper ewe lamb",
        "Dorper sheep flock farm",
    ],
}

WIKIMEDIA_QUERIES = {
    "Afrikaner": [
        "Afrikanerbees",
        "incategory:Afrikaner_cattle",
        "Afrikaner cattle cow",
        "Africander cattle",
    ],
    "Nguni": ["Nguni cattle", "Nguni cow"],
    "Bonsmara": ["Bonsmara cattle", "Bonsmara bull"],
    "Boer_Goat": ["Boer goat", "Boer bok"],
    "Dorper": ["Dorper sheep", "Dorper ram"],
}

USER_AGENT = "AgriGuardBreedID/1.0 (academic livestock classifier; extra training photos)"

# Wikimedia search for "Afrikaner" otherwise returns rugby / people / maps.
WIKIMEDIA_TITLE_REQUIRE = {
    "Afrikaner": ("cattle", "cow", "bull", "ox", "bees", "kuh", "os"),
}
WIKIMEDIA_TITLE_REJECT = (
    "rugby", "map", "karte", "portrait", "people", "red bull", "perfume",
)


def remove_duplicate_images(folder: Path) -> int:
    seen_hashes: set[str] = set()
    removed = 0
    for file_path in list(folder.glob("*")):
        if not file_path.is_file():
            continue
        try:
            digest = hashlib.md5(file_path.read_bytes()).hexdigest()
        except OSError:
            continue
        if digest in seen_hashes:
            file_path.unlink()
            removed += 1
        else:
            seen_hashes.add(digest)
    return removed


def download_with_icrawler(
    breed: str,
    output_folder: Path,
    images_per_query: int,
    engine: str,
) -> None:
    try:
        from icrawler.builtin import BingImageCrawler, GoogleImageCrawler
    except ImportError as exc:
        raise SystemExit(
            "icrawler is required for Bing/Google download. "
            "Install with:  pip install icrawler"
        ) from exc

    crawlers = []
    if engine in ("bing", "both"):
        crawlers.append(("Bing", BingImageCrawler))
    if engine in ("google", "both"):
        crawlers.append(("Google", GoogleImageCrawler))

    queries = BREED_SEARCH_QUERIES.get(breed, [display_name(breed)])
    print(f"\n[{display_name(breed)}] icrawler ({engine}) — {len(queries)} queries")
    for query in queries:
        for engine_name, CrawlerClass in crawlers:
            print(f"    - {engine_name}: \"{query}\"")
            try:
                crawler = CrawlerClass(storage={"root_dir": str(output_folder)})
                crawler.crawl(
                    keyword=query,
                    max_num=images_per_query,
                    file_idx_offset="auto",
                )
            except Exception as error:
                print(f"      (skipped due to error: {error})")
            time.sleep(1)


def _title_allowed(breed: str, title: str) -> bool:
    text = title.lower()
    if any(bad in text for bad in WIKIMEDIA_TITLE_REJECT):
        return False
    required = WIKIMEDIA_TITLE_REQUIRE.get(breed)
    if required and not any(token in text for token in required):
        return False
    return True


def _wikimedia_search(query: str, limit: int) -> list[dict]:
    import json

    url = (
        "https://commons.wikimedia.org/w/api.php"
        "?action=query&format=json&generator=search"
        f"&gsrsearch={quote(query)}"
        "&gsrnamespace=6"
        f"&gsrlimit={limit}"
        "&prop=imageinfo&iiprop=url|mime|size"
    )
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    pages = (data.get("query") or {}).get("pages") or {}
    results = []
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        mime = (info.get("mime") or "").lower()
        if not mime.startswith("image/"):
            continue
        if mime not in {"image/jpeg", "image/png", "image/webp"}:
            continue
        url_found = info.get("url")
        if url_found:
            results.append({"title": page.get("title") or "image", "url": url_found})
    return results


def _wikimedia_category(category: str, limit: int) -> list[dict]:
    import json

    url = (
        "https://commons.wikimedia.org/w/api.php"
        "?action=query&format=json"
        f"&generator=categorymembers&gcmtitle={quote(category)}"
        "&gcmtype=file"
        f"&gcmlimit={limit}"
        "&prop=imageinfo&iiprop=url|mime|size"
    )
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    pages = (data.get("query") or {}).get("pages") or {}
    results = []
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        mime = (info.get("mime") or "").lower()
        if mime not in {"image/jpeg", "image/png", "image/webp"}:
            continue
        url_found = info.get("url")
        if url_found:
            results.append({"title": page.get("title") or "image", "url": url_found})
    return results


def _download_url(url: str, dest: Path) -> bool:
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=45) as resp:
            body = resp.read()
        if len(body) < 8_000:
            return False
        dest.write_bytes(body)
        return dest.suffix.lower() in IMAGE_EXTS or dest.stat().st_size > 0
    except Exception:
        return False


def download_wikimedia(breed: str, output_folder: Path, per_query: int) -> int:
    queries = WIKIMEDIA_QUERIES.get(breed, [display_name(breed)])
    print(f"\n[{display_name(breed)}] Wikimedia Commons — {len(queries)} queries")
    output_folder.mkdir(parents=True, exist_ok=True)
    saved = 0
    seen_urls: set[str] = set()
    for query in queries:
        print(f"    - Commons: \"{query}\"")
        try:
            if query.lower().startswith("incategory:") or query.lower().startswith("category:"):
                cat = query.split(":", 1)[1]
                if not cat.lower().startswith("category:"):
                    cat = f"Category:{cat}"
                hits = _wikimedia_category(cat, per_query)
            else:
                hits = _wikimedia_search(query, per_query)
            hits = [h for h in hits if _title_allowed(breed, h["title"])]
        except Exception as error:
            print(f"      (search failed: {error})")
            continue
        for hit in hits:
            if hit["url"] in seen_urls:
                continue
            seen_urls.add(hit["url"])
            ext = ".jpg"
            lower = hit["url"].lower()
            if ".png" in lower:
                ext = ".png"
            elif ".webp" in lower:
                ext = ".webp"
            dest = output_folder / f"wm_{saved:04d}{ext}"
            while dest.exists():
                saved += 1
                dest = output_folder / f"wm_{saved:04d}{ext}"
            if _download_url(hit["url"], dest):
                saved += 1
            else:
                if dest.exists():
                    dest.unlink()
        time.sleep(0.4)
    print(f"    - saved {saved} Wikimedia file(s)")
    return saved


def download_photos_for_breed(
    breed: str,
    engine: str,
    images_per_query: int,
    output_root: Path,
) -> Path:
    output_folder = output_root / breed
    output_folder.mkdir(parents=True, exist_ok=True)
    if engine in ("wikimedia", "all"):
        download_wikimedia(breed, output_folder, images_per_query)
    if engine in ("bing", "google", "both", "all"):
        crawler_engine = "both" if engine == "all" else engine
        download_with_icrawler(breed, output_folder, images_per_query, crawler_engine)
    removed = remove_duplicate_images(output_folder)
    final_count = len([p for p in output_folder.iterdir() if p.is_file()])
    if removed:
        print(f"[{display_name(breed)}] removed {removed} duplicate(s) → {final_count} unique")
    else:
        print(f"[{display_name(breed)}] {final_count} file(s) in {output_folder}")
    return output_folder


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download extra breed photos into extra_photos/<Breed>/."
    )
    parser.add_argument(
        "--breed",
        default=None,
        help="One breed (Afrikaner, Boer_Goat, Bonsmara, Dorper, Nguni). "
        "Default: all five trained classes. Afrikaner is the usual gap.",
    )
    parser.add_argument(
        "--engine",
        choices=["wikimedia", "bing", "google", "both", "all"],
        default="wikimedia",
        help="wikimedia is usually cleaner for Afrikaner. "
        "bing/google need: pip install icrawler. Default: wikimedia.",
    )
    parser.add_argument(
        "--images-per-query",
        type=int,
        default=IMAGES_PER_QUERY,
        help="Max images requested per search phrase (default 80).",
    )
    parser.add_argument(
        "--into-raw",
        action="store_true",
        help="Write to raw_photos/ instead of extra_photos/ "
        "(legacy layout used by prepare_dataset.py).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.breed:
        breed = canonical_breed(args.breed)
        if not breed:
            raise SystemExit(f"Unknown breed '{args.breed}'. Use one of: {CLASS_FOLDERS}")
        breeds = [breed]
    else:
        breeds = list(CLASS_FOLDERS)

    output_root = RAW_PHOTOS_DIR if args.into_raw else EXTRA_PHOTOS_DIR
    output_root.mkdir(parents=True, exist_ok=True)

    print(
        f"Downloading {', '.join(display_name(b) for b in breeds)} "
        f"via {args.engine} → {output_root}/"
    )
    if "Afrikaner" in breeds and args.engine in ("bing", "google", "both", "all"):
        print(
            "Note: web search for 'Afrikaner' often returns people, not cattle. "
            "Delete anything that is not a red Afrikaner/Africander cow or bull "
            "before ingest_extra_photos.py."
        )
    summary = {}
    for breed in breeds:
        folder = download_photos_for_breed(
            breed, args.engine, args.images_per_query, output_root
        )
        summary[breed] = len([p for p in folder.iterdir() if p.is_file()])

    print("\n" + "=" * 55)
    print("DOWNLOAD SUMMARY")
    print("=" * 55)
    for breed, count in summary.items():
        note = "" if count >= 30 else "  <- still thin; add more phrases or extra photos"
        print(f"  {display_name(breed):<12} {count} unique photo(s){note}")
    print("=" * 55)
    print("Review the files (delete wrong animals), then:")
    print("  python3 ingest_extra_photos.py")
    print("  python3 retrain_breed_model.py")
    print("=" * 55)


if __name__ == "__main__":
    main()
