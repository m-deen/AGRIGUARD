"""
AgriGuard - Bulk Breed Photo Downloader
==========================================
Downloads JPEG/PNG photos into extra_photos/<Breed>/ so you can review
them, then ingest_extra_photos.py copies keepers into dataset/train|test.

Afrikaner is the smallest class in the current dataset — run this for
that breed first:

    python3 download_breed_photos.py --breed Afrikaner --engine labeled
    python3 download_breed_photos.py --breed Afrikaner --engine wikimedia

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
        # "Afrikanerbees" alone hits Spanish football (Marca), not cattle.
        "Afrikanerbees koei bul Suid-Afrika",
        "Afrikaner oxen trek ox wagon cattle South Africa",
        "Africander cattle bull farm South Africa",
        "red Afrikaner cattle veld horns hump Sanga",
        "Afrikaner cow Sanga breed cattle South Africa",
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

# Afrikaner-only stud auctions (lot photos are labelled Afrikaner cattle).
VLEISSENTRAAL_AFRIKANER_AUCTIONS = (3626,)
VLEISSENTRAAL_BROWSER_UA = (
    "Mozilla/5.0 (compatible; AgriGuardBreedID/1.0; "
    "academic livestock classifier; extra training photos)"
)
UPSPACE_AFRIKANER_URLS = (
    "https://repository.up.ac.za/bitstream/handle/2263/13495/pas002.jpg?sequence=1&isAllowed=y",
    "https://repository.up.ac.za/bitstream/handle/2263/13495/pas003.jpg?sequence=2&isAllowed=y",
)
SA_DOT_CO_ZA_AFRIKANER_URLS = tuple(
    f"https://southafrica.co.za/images/afrikaner{i}-786x524.jpg" for i in range(1, 6)
)
PLACEHOLDER_MIN_BYTES = 70_000


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


def looks_like_placeholder(path: Path) -> bool:
    """Drop auction stubs ('PHOTO NOT AVAILABLE') which are small and flat."""
    try:
        size = path.stat().st_size
    except OSError:
        return True
    if size < PLACEHOLDER_MIN_BYTES:
        return True
    try:
        from PIL import Image
    except ImportError:
        return False
    try:
        with Image.open(path) as img:
            sample = img.convert("RGB").resize((32, 32))
            pixels = [sample.getpixel((x, y)) for y in range(32) for x in range(32)]
    except Exception:
        return True
    if not pixels:
        return True
    means = [sum(c[i] for c in pixels) / len(pixels) for i in range(3)]
    var = sum(
        (c[0] - means[0]) ** 2 + (c[1] - means[1]) ** 2 + (c[2] - means[2]) ** 2
        for c in pixels
    ) / len(pixels)
    return var < 400.0


def _http_get(url: str, timeout: int = 45, user_agent: str | None = None) -> bytes:
    req = Request(
        url,
        headers={
            "User-Agent": user_agent or USER_AGENT,
            "Accept": "image/jpeg,image/png,image/*,text/html,*/*;q=0.8",
        },
    )
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def extract_vleissentraal_feature_urls(html: str) -> list[str]:
    import re

    found = re.findall(
        r"https://www\.vleissentraal\.co\.za/storage/lot/feature/[^\"']+\.(?:jpg|jpeg|png)",
        html,
        flags=re.I,
    )
    return list(dict.fromkeys(found))


def download_vleissentraal_afrikaner(output_folder: Path) -> int:
    """Fetch lot photos from known Afrikaner stud auctions."""
    print(f"\n[Afrikaner] Vleissentraal — {len(VLEISSENTRAAL_AFRIKANER_AUCTIONS)} auction(s)")
    output_folder.mkdir(parents=True, exist_ok=True)
    saved = 0
    seen_urls: set[str] = set()
    for auction_id in VLEISSENTRAAL_AFRIKANER_AUCTIONS:
        page = f"https://www.vleissentraal.co.za/en/view-auction/{auction_id}"
        print(f"    - auction {auction_id}")
        try:
            html = _http_get(page, user_agent=VLEISSENTRAAL_BROWSER_UA).decode(
                "utf-8", "ignore"
            )
        except Exception as error:
            print(f"      (page failed: {error})")
            continue
        for url in extract_vleissentraal_feature_urls(html):
            if url in seen_urls:
                continue
            seen_urls.add(url)
            dest = output_folder / f"vs_{saved:04d}.jpg"
            while dest.exists():
                saved += 1
                dest = output_folder / f"vs_{saved:04d}.jpg"
            try:
                body = _http_get(url, user_agent=VLEISSENTRAAL_BROWSER_UA)
            except Exception:
                continue
            if not body.startswith(b"\xff\xd8") or len(body) < 8_000:
                continue
            dest.write_bytes(body)
            if looks_like_placeholder(dest):
                dest.unlink(missing_ok=True)
                continue
            saved += 1
            time.sleep(0.12)
    print(f"    - saved {saved} Vleissentraal file(s)")
    return saved


def download_fixed_url_list(
    urls: tuple[str, ...],
    output_folder: Path,
    prefix: str,
) -> int:
    output_folder.mkdir(parents=True, exist_ok=True)
    saved = 0
    for url in urls:
        dest = output_folder / f"{prefix}_{saved:04d}.jpg"
        while dest.exists():
            saved += 1
            dest = output_folder / f"{prefix}_{saved:04d}.jpg"
        try:
            body = _http_get(url)
        except Exception as error:
            print(f"      (skip {url}: {error})")
            continue
        if not (body.startswith(b"\xff\xd8") or body[:8] == b"\x89PNG\r\n\x1a\n"):
            continue
        if len(body) < 8_000:
            continue
        dest.write_bytes(body)
        saved += 1
    return saved


def download_labeled_afrikaner_sources(output_folder: Path) -> int:
    """Sources that are already labelled Afrikaner cattle (not web-search)."""
    print("\n[Afrikaner] labelled sources (auctions + university slides)")
    n = download_vleissentraal_afrikaner(output_folder)
    up_n = download_fixed_url_list(UPSPACE_AFRIKANER_URLS, output_folder, "up")
    print(f"    - saved {up_n} University of Pretoria slide(s)")
    sa_n = download_fixed_url_list(SA_DOT_CO_ZA_AFRIKANER_URLS, output_folder, "sa")
    print(f"    - saved {sa_n} southafrica.co.za photo(s)")
    return n + up_n + sa_n


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
    if breed == "Afrikaner" and engine in ("labeled", "vleissentraal", "all"):
        download_labeled_afrikaner_sources(output_folder)
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
        choices=["labeled", "vleissentraal", "wikimedia", "bing", "google", "both", "all"],
        default="labeled",
        help="labeled = Afrikaner stud-auction + university slides (cleanest). "
        "wikimedia is next. bing/google need icrawler and are noisy for Afrikaner. "
        "Default: labeled.",
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
            "Prefer --engine labeled (Vleissentraal Afrikaner auctions). "
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
