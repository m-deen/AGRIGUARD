"""
AgriGuard - Breed Identification Module (standalone copy)
=========================================================
Based on your Downloads/breed_identification.py.

Changes from your original (kept behaviour, aligned to trained .h5):
  - Added Afrikaner care entry (present in the trained model classes)
  - Normalise breed name aliases (e.g. Boer_Goat -> Boer Goat)
  - Care library keys match display names used by class_labels.json

NOT wired into AgriGuard Backend/app.py yet.
"""

import io
import time
import logging
from datetime import datetime, timezone

from PIL import Image, ImageOps
import numpy as np

from .photo_quality import PHOTO_TIPS, assess_photo_quality_bytes


logger = logging.getLogger("agriguard.breed_identification")
logging.basicConfig(level=logging.INFO)

ALLOWED_FORMATS = {"JPEG", "PNG", "JPG", "WEBP"}
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024
TARGET_IMAGE_SIZE = (224, 224)
MAX_INFERENCE_SECONDS = 2.0
NUMBER_OF_PREDICTIONS_TO_RETURN = 3
LOW_CONFIDENCE_WARNING_THRESHOLD = 40.0

# Display-name aliases from dataset folder names
BREED_NAME_ALIASES = {
    "Boer_Goat": "Boer Goat",
    "boer_goat": "Boer Goat",
    "Savanna_Goat": "Savanna Goat",
    "Kalahari_Red": "Kalahari Red",
}


def normalize_breed_name(name: str) -> str:
    if not name:
        return name
    return BREED_NAME_ALIASES.get(name, BREED_NAME_ALIASES.get(name.replace(" ", "_"), name))


BREED_CARE_LIBRARY = {
    "Nguni": {
        "species": "Cattle",
        "market_value_range": "R12,000 – R28,000",
        "visual_characteristics": [
            "Multi-coloured hide with distinctive patterns",
            "Medium frame with lyre-shaped horns",
            "Compact, hardy body suited to veld",
            "Strong pigment around eyes and muzzle",
        ],
        "feeding_guidelines": "Grazes well on natural veld; needs minimal supplementary feed.",
        "feeding_points": [
            "Thrive on mixed veld grazing with low concentrate input",
            "Provide mineral lick during dry winter months",
            "Calves benefit from creep feed in extensive systems",
        ],
        "housing_requirements": "Hardy breed - basic shelter from wind/rain is sufficient.",
        "health_tips": "Naturally tick and disease resistant; still deworm every 3 months.",
        "care_points": [
            "Basic wind/rain shelter is usually enough",
            "Deworm every 3 months and maintain tick control",
            "Vaccinate for clostridial diseases and lumpy skin disease",
            "Ideal for low-input communal and commercial herds",
        ],
    },
    "Bonsmara": {
        "species": "Cattle",
        "market_value_range": "R18,000 – R40,000",
        "visual_characteristics": [
            "Smooth red-brown coat",
            "Well-muscled beef conformation",
            "Moderate hump and loose dewlap",
            "Alert expression with medium ears",
        ],
        "feeding_guidelines": "Balanced grazing plus protein supplement during dry season.",
        "feeding_points": [
            "Quality grazing with protein lick in winter",
            "Finishing rations improve carcass grade before sale",
            "Ensure clean water access in hot weather",
        ],
        "housing_requirements": "Needs shade structures in high-heat areas.",
        "health_tips": "Monitor for heat stress; routine vaccination against blackleg/anthrax.",
        "care_points": [
            "Provide shade structures in high-heat regions",
            "Routine vaccines: blackleg, anthrax, lumpy skin",
            "Watch for heat stress on hot summer days",
            "Regular weighing helps track growth targets",
        ],
    },
    "Afrikaner": {
        "species": "Cattle",
        "market_value_range": "R14,000 – R32,000",
        "visual_characteristics": [
            "Deep red to golden-red coat",
            "Large lateral horns common in traditional types",
            "Loose skin and heat-adapted frame",
            "Strong walking ability for extensive veld",
        ],
        "feeding_guidelines": "Hardy on natural veld; mineral lick in dry season is helpful.",
        "feeding_points": [
            "Performs well on natural veld with little concentrate",
            "Mineral lick recommended in dry season",
            "Avoid overfeeding grain on extensive systems",
        ],
        "housing_requirements": "Extensive systems; basic shade and windbreaks are enough.",
        "health_tips": "Keep up with clostridial vaccines; monitor for ticks and lumpy skin disease.",
        "care_points": [
            "Basic shade and windbreaks are sufficient",
            "Maintain clostridial and lumpy skin vaccination",
            "Monitor ticks in bushveld areas",
            "Select for fertility and walking ability",
        ],
    },
    "Brahman": {
        "species": "Cattle",
        "market_value_range": "R16,000 – R38,000",
        "visual_characteristics": [
            "Prominent hump and loose dewlap",
            "Light grey to red coat colours",
            "Large ears and heat-tolerant skin",
            "Long legs suited to extensive range",
        ],
        "feeding_guidelines": "Tolerates poor-quality forage well; supplement in winter.",
        "feeding_points": [
            "Handles poorer forage better than many breeds",
            "Winter protein supplement improves condition",
            "Keep mineral blocks available year-round",
        ],
        "housing_requirements": "Very heat tolerant - open shelter is fine.",
        "health_tips": "Watch for horn fly irritation; regular tick control recommended.",
        "care_points": [
            "Open shade shelters are usually enough",
            "Strong tick and fly control programme",
            "Handle calmly - Brahmans respond to quiet stockmanship",
            "Useful in crossbreeding for heat tolerance",
        ],
    },
    "Angus": {
        "species": "Cattle",
        "market_value_range": "R20,000 – R45,000",
        "visual_characteristics": [
            "Solid black or red coat",
            "Polled (naturally hornless) head",
            "Compact, well-muscled beef frame",
            "Smooth early-maturing body type",
        ],
        "feeding_guidelines": "Needs good quality pasture or supplementary feed for optimal beef yield.",
        "feeding_points": [
            "Good pasture or silage for finishing performance",
            "Energy-dense rations near market weight",
            "Do not let cows become over-fat before calving",
        ],
        "housing_requirements": "Less heat tolerant than indigenous breeds - provide ample shade.",
        "health_tips": "Susceptible to sunburn/pinkeye in strong sun; monitor closely in summer.",
        "care_points": [
            "Provide ample shade in South African summers",
            "Watch for pinkeye and sun-related stress",
            "Keep up with standard beef vaccination schedule",
            "Best in higher-management commercial systems",
        ],
    },
    "Boer Goat": {
        "species": "Goat",
        "market_value_range": "R2,500 – R8,000",
        "visual_characteristics": [
            "White body with reddish-brown head and neck",
            "Roman nose and long lop ears",
            "Compact meaty conformation",
            "Short smooth coat",
        ],
        "feeding_guidelines": "Browses shrubs/bushes; needs mineral lick supplement.",
        "feeding_points": [
            "Browse shrubs and bushes rather than pure grass only",
            "Provide mineral lick formulated for goats",
            "Kids need access to creep feed for faster growth",
        ],
        "housing_requirements": "Dry, draft-free shelter - goats are sensitive to damp conditions.",
        "health_tips": "Deworm every 6-8 weeks; prone to internal parasites in humid areas.",
        "care_points": [
            "Keep housing dry and draft-free",
            "Deworm every 6-8 weeks in humid areas",
            "Trim hooves regularly on rocky or soft ground",
            "Vaccinate for pulpy kidney and pasteurella where needed",
        ],
    },
    "Kalahari Red": {
        "species": "Goat",
        "market_value_range": "R2,800 – R7,500",
        "visual_characteristics": [
            "Uniform red to deep red coat",
            "Strong desert-adapted frame",
            "Medium ears and solid meat type",
            "Minimal white markings",
        ],
        "feeding_guidelines": "Excellent browser, thrives on sparse arid-region vegetation with minimal supplement.",
        "feeding_points": [
            "Thrives on sparse arid browse with little supplement",
            "Mineral lick still recommended in dry months",
            "Avoid sudden lush feed changes after drought",
        ],
        "housing_requirements": "Very hardy - basic shelter from wind is usually enough.",
        "health_tips": "Dark red coat gives strong sun/heat tolerance; still monitor for internal parasites.",
        "care_points": [
            "Basic wind shelter is usually enough",
            "Parasite control remains important after rains",
            "Strong heat and sun tolerance",
            "Good choice for extensive arid systems",
        ],
    },
    "Savanna Goat": {
        "species": "Goat",
        "market_value_range": "R2,500 – R7,000",
        "visual_characteristics": [
            "White coat that reflects heat",
            "Large frame meat goat type",
            "Pigmented skin under white hair",
            "Alert head with lop ears",
        ],
        "feeding_guidelines": "Efficient browser on veld; supplement protein during dry months.",
        "feeding_points": [
            "Efficient browser on mixed veld",
            "Protein supplement helps in dry months",
            "Kids respond well to creep feeding",
        ],
        "housing_requirements": "White coat reflects heat well - open shelter with shade is sufficient.",
        "health_tips": "Naturally resistant to harsh conditions and many local diseases; routine deworming still needed.",
        "care_points": [
            "Open shelter with shade is sufficient",
            "Routine deworming still required",
            "Check skin pigment around eyes and udder",
            "Handle quietly in large commercial flocks",
        ],
    },
    "Dorper": {
        "species": "Sheep",
        "market_value_range": "R2,000 – R5,500",
        "visual_characteristics": [
            "White body with black head and neck (classic Dorper)",
            "Hair sheep that sheds rather than needing full shearing",
            "Compact, well-muscled mutton conformation",
            "Short smooth coat suited to arid climates",
        ],
        "feeding_guidelines": "Grazes efficiently on veld; low supplementary feed needs.",
        "feeding_points": [
            "Efficient grazer on veld with low feed costs",
            "Ewes need extra energy in late pregnancy",
            "Lambs finish well on quality pasture or short feedlot period",
        ],
        "housing_requirements": "Sheds wool naturally - simple open shelter is enough.",
        "health_tips": "Hardy in arid conditions; check hooves regularly for rot in wet seasons.",
        "care_points": [
            "Simple open shelter is usually enough",
            "Check hooves in wet seasons for foot rot",
            "Vaccinate for pulpy kidney where risk is high",
            "Separate clearly from Boer goats - similar colour pattern",
        ],
    },
    "Merino": {
        "species": "Sheep",
        "market_value_range": "R1,800 – R4,500",
        "visual_characteristics": [
            "Dense fine-wool fleece",
            "White face and legs common",
            "Wrinkled skin in traditional types",
            "Medium frame wool-focused conformation",
        ],
        "feeding_guidelines": "Needs consistent good-quality grazing to maintain wool quality.",
        "feeding_points": [
            "Consistent good grazing supports wool quality",
            "Supplement protein before shearing if condition is poor",
            "Avoid sudden feed changes that affect fleece",
        ],
        "housing_requirements": "Shelter from rain is important - wet fleece leads to health issues.",
        "health_tips": "Regular shearing required; monitor closely for fly strike in warm, humid weather.",
        "care_points": [
            "Shelter from prolonged rain",
            "Shear on schedule and watch for fly strike",
            "Crutching reduces strike risk in warm months",
            "Foot care on damp pastures",
        ],
    },
    "Damara": {
        "species": "Sheep",
        "market_value_range": "R1,500 – R4,000",
        "visual_characteristics": [
            "Fat-tailed conformation",
            "Varied coat colours and patterns",
            "Long legs for arid browsing",
            "Hardy indigenous frame",
        ],
        "feeding_guidelines": "Very hardy browser/grazer; copes well on poor-quality veld.",
        "feeding_points": [
            "Copes well on poor-quality arid veld",
            "Minimal concentrate needed in extensive systems",
            "Ewes need support when nursing twins",
        ],
        "housing_requirements": "Fat-tailed breed adapted to arid conditions - minimal shelter needed.",
        "health_tips": "Highly disease resistant; still deworm periodically and monitor tail condition.",
        "care_points": [
            "Minimal shelter in arid regions",
            "Periodic deworming still advised",
            "Monitor fat-tail condition as energy reserve",
            "Excellent for low-input communal flocks",
        ],
    },
}


def validate_uploaded_image(file_bytes: bytes) -> None:
    """REQ-31, REQ-40"""
    if not file_bytes:
        raise ValueError("No image was received. Please upload a photo.")

    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        raise ValueError("That image is too large. Please upload a photo under 5MB.")

    try:
        image = Image.open(io.BytesIO(file_bytes))
        image.load()  # fully decode — catches corrupt files better than verify() alone
    except Exception:
        raise ValueError("Please upload JPEG or PNG image.")

    # Some browsers/files report format=None even when the image opens fine
    fmt = (image.format or "").upper()
    if fmt and fmt not in ALLOWED_FORMATS:
        # Still allow if PIL can convert to RGB (e.g. odd JPEG variants)
        try:
            image.convert("RGB")
        except Exception:
            raise ValueError("Please upload JPEG or PNG image.")


def preprocess_image(file_bytes: bytes) -> np.ndarray:
    """REQ-32: RGB, 224x224, MobileNetV2 preprocess (scale to [-1, 1])."""
    image = Image.open(io.BytesIO(file_bytes))
    image = ImageOps.exif_transpose(image).convert("RGB")
    image = image.resize(TARGET_IMAGE_SIZE, Image.BILINEAR)
    pixel_array = np.array(image, dtype=np.float32)
    # Same as tf.keras.applications.mobilenet_v2.preprocess_input
    return (pixel_array / 127.5) - 1.0


def run_model_inference(preprocessed_image: np.ndarray, model_predict_fn) -> dict:
    """REQ-33, REQ-36"""
    start_time = time.perf_counter()
    try:
        raw_probabilities = model_predict_fn(preprocessed_image)
        if not isinstance(raw_probabilities, dict) or not raw_probabilities:
            raise RuntimeError("Model returned no breed scores.")
    except Exception as error:
        logger.exception("Model inference failed: %s", error)
        # Keep farmer-friendly message, but include detail for debugging in logs
        raise RuntimeError(
            f"Unable to identify breed. Please try a different photo. ({type(error).__name__}: {error})"
        ) from error

    elapsed_seconds = time.perf_counter() - start_time
    if elapsed_seconds > MAX_INFERENCE_SECONDS:
        logger.warning(
            "Breed inference took %.2fs, exceeding the %.1fs target (REQ-36).",
            elapsed_seconds, MAX_INFERENCE_SECONDS,
        )

    return {
        "raw_probabilities": raw_probabilities,
        "inference_time_seconds": round(elapsed_seconds, 3),
    }


def filter_probabilities_by_species(raw_probabilities: dict, species: str | None) -> dict:
    """Keep only breeds matching an optional species hint (Cattle/Sheep/Goat)."""
    if not species:
        return raw_probabilities
    wanted = species.strip().lower()
    if wanted not in {"cattle", "sheep", "goat"}:
        return raw_probabilities

    filtered = {}
    for breed, prob in raw_probabilities.items():
        care = BREED_CARE_LIBRARY.get(normalize_breed_name(breed))
        if care and care.get("species", "").lower() == wanted:
            filtered[breed] = float(prob)

    if not filtered:
        return raw_probabilities

    total = sum(filtered.values())
    if total <= 0:
        # Equal share if model put ~0 mass on this species
        even = 1.0 / len(filtered)
        return {k: even for k in filtered}
    return {k: v / total for k, v in filtered.items()}


def get_top_predictions(raw_probabilities: dict, top_n: int = NUMBER_OF_PREDICTIONS_TO_RETURN) -> list:
    sorted_breeds = sorted(raw_probabilities.items(), key=lambda item: item[1], reverse=True)
    top_breeds = sorted_breeds[:top_n]
    return [
        {
            "breed": normalize_breed_name(breed_name),
            "confidence_percent": round(float(probability) * 100, 1),
        }
        for breed_name, probability in top_breeds
    ]


RELATED_BREEDS = {
    "Afrikaner": ["Bonsmara", "Nguni"],
    "Bonsmara": ["Afrikaner", "Nguni"],
    "Nguni": ["Afrikaner", "Bonsmara"],
    "Boer Goat": ["Kalahari Red", "Savanna Goat", "Dorper"],
    "Dorper": ["Boer Goat", "Merino"],
}


def lookalike_note(predictions: list, species: str | None = None) -> str | None:
    """Warn on confusions that showed up in the dataset audit."""
    names = [p["breed"] for p in predictions[:3]]
    name_set = set(names)
    notes = []
    if "Dorper" in name_set and "Boer Goat" in name_set:
        notes.append(
            "Dorper sheep and Boer goats often look alike (white body, dark head). "
            "If you know the species, pick Sheep or Goat to refine the result."
        )
    cattle = [n for n in names if n in {"Afrikaner", "Bonsmara", "Nguni"}]
    if len(cattle) >= 2:
        notes.append(
            "Afrikaner, Bonsmara and Nguni are all South African cattle. "
            "Solid red with long spreading horns is usually Afrikaner; "
            "a smooth red beef type is Bonsmara; a speckled or multi-colour hide is Nguni."
        )
    if names and names[0] == "Boer Goat":
        notes.append(
            "Classic Boer goats have a white body and reddish-brown head. "
            "A solid red goat may be a Kalahari Red (related meat goat — not a separate "
            "class in this 5-breed model)."
        )
    if names and names[0] == "Nguni" and not species:
        notes.append(
            "Nguni coats vary widely. If the animal is solid deep-red with wide "
            "spreading horns, compare with Afrikaner."
        )
    return " ".join(notes) or None


def _average_probability_dicts(dicts: list[dict]) -> dict:
    keys = set()
    for d in dicts:
        keys.update(d)
    out = {}
    n = len(dicts) or 1
    for k in keys:
        out[k] = sum(float(d.get(k, 0.0)) for d in dicts) / n
    return out


def get_care_recommendations(breed_name: str):
    """REQ-37, REQ-38, REQ-39"""
    return BREED_CARE_LIBRARY.get(normalize_breed_name(breed_name))


def identify_breed_from_photo(
    file_bytes: bytes,
    model_predict_fn,
    species: str | None = None,
) -> dict:
    """
    Full pipeline for Use Case #2. Never raises — returns success/error dict.
    Optional species ('Cattle'|'Sheep'|'Goat') re-ranks within that species.
    """
    try:
        validate_uploaded_image(file_bytes)
        quality = assess_photo_quality_bytes(file_bytes)
        if quality.get("level") == "error":
            return {
                "success": False,
                "error": quality.get("message") or "Please upload a clearer livestock photo.",
                "photo_quality": quality,
                "photo_tips": PHOTO_TIPS,
            }

        preprocessed = preprocess_image(file_bytes)
        inference_result = run_model_inference(preprocessed, model_predict_fn)
        # Horizontal-flip TTA — cheap and helped ~2pp on the held-out set.
        flipped = np.flip(preprocessed, axis=1).copy()
        tta = run_model_inference(flipped, model_predict_fn)
        raw = _average_probability_dicts([
            inference_result["raw_probabilities"],
            tta["raw_probabilities"],
        ])
        elapsed = round(
            inference_result["inference_time_seconds"] + tta["inference_time_seconds"],
            3,
        )
        probs = filter_probabilities_by_species(raw, species)
        top_predictions = get_top_predictions(probs)

        top_breed = top_predictions[0]["breed"] if top_predictions else None
        top_confidence = top_predictions[0]["confidence_percent"] if top_predictions else 0
        care_info = get_care_recommendations(top_breed) if top_breed else None
        is_low_confidence = top_confidence < LOW_CONFIDENCE_WARNING_THRESHOLD
        related = RELATED_BREEDS.get(top_breed or "", [])

        return {
            "success": True,
            "predictions": top_predictions,
            "care_recommendations": care_info,
            "related_breeds": related,
            "low_confidence_warning": is_low_confidence,
            "lookalike_note": lookalike_note(top_predictions, species),
            "photo_quality": quality,
            "photo_tips": PHOTO_TIPS,
            "species_filter": species.strip().title() if species else None,
            "inference_time_seconds": elapsed,
            "identified_at": datetime.now(timezone.utc).isoformat(),
        }

    except (ValueError, RuntimeError) as error:
        return {"success": False, "error": str(error)}


def calculate_model_accuracy(true_labels: list[str], predicted_labels: list[str]) -> float:
    """REQ-35 helper for reports — not used in live requests."""
    if not true_labels:
        return 0.0
    correct = sum(1 for true, pred in zip(true_labels, predicted_labels) if true == pred)
    return round((correct / len(true_labels)) * 100, 2)
