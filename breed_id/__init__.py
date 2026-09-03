"""AgriGuard breed identification package."""

from .breed_identification import (
    BREED_CARE_LIBRARY,
    PHOTO_TIPS,
    RELATED_BREEDS,
    get_care_recommendations,
    identify_breed_from_photo,
    normalize_breed_name,
)
from .model_loader import (
    get_backend,
    load_class_names,
    load_model,
    model_predict_fn,
)
from .photo_quality import assess_photo_quality_bytes

__all__ = [
    "BREED_CARE_LIBRARY",
    "PHOTO_TIPS",
    "RELATED_BREEDS",
    "get_care_recommendations",
    "identify_breed_from_photo",
    "normalize_breed_name",
    "assess_photo_quality_bytes",
    "get_backend",
    "load_class_names",
    "load_model",
    "model_predict_fn",
]
