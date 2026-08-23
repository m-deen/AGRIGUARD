"""AgriGuard breed identification package."""

from .breed_identification import (
    BREED_CARE_LIBRARY,
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

__all__ = [
    "BREED_CARE_LIBRARY",
    "get_care_recommendations",
    "identify_breed_from_photo",
    "normalize_breed_name",
    "get_backend",
    "load_class_names",
    "load_model",
    "model_predict_fn",
]
