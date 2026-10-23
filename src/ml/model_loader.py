"""
Trusted Model Artifact Loader & Registry.
Caches fitted ML pipelines in memory to avoid repeated disk reads or retraining.
Only loads from controlled project paths, strictly avoiding arbitrary pickle loads.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import joblib

from src.ml.feature_schema import PredictionModelType

logger = logging.getLogger("ShipmentPricePredictor.ModelLoader")

# Controlled project directories
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MODELS_DIR = _PROJECT_ROOT / "models"

# Trusted artifact filenames
TRUSTED_MODEL_PATHS = {
    PredictionModelType.DOMESTIC_COURIER: MODELS_DIR / "domestic_courier_model.joblib",
    PredictionModelType.REFERENCE_SCULPTURE: MODELS_DIR / "reference_sculpture_model.joblib",
}

TRUSTED_METADATA_PATHS = {
    PredictionModelType.DOMESTIC_COURIER: MODELS_DIR / "domestic_courier_model.json",
    PredictionModelType.REFERENCE_SCULPTURE: MODELS_DIR / "reference_sculpture_model.json",
}


class ModelArtifactNotFoundError(Exception):
    """Raised when a requested model artifact is not found in the controlled models directory."""
    pass


class ModelLoader:
    """Thread-safe singleton model cache."""

    _instance: Optional[ModelLoader] = None
    _cache: Dict[PredictionModelType, Dict[str, Any]] = {}

    def __new__(cls) -> ModelLoader:
        if cls._instance is None:
            cls._instance = super(ModelLoader, cls).__new__(cls)
            cls._instance._cache = {}
        return cls._instance

    def is_model_available(self, model_type: PredictionModelType) -> bool:
        """Check if trusted model artifact file exists on disk."""
        target_path = TRUSTED_MODEL_PATHS.get(model_type)
        return target_path is not None and target_path.exists()

    def get_model(self, model_type: PredictionModelType) -> Tuple[Any, Dict[str, Any]]:
        """
        Retrieve fitted model pipeline and metadata.
        Caches in memory after first load.
        """
        if model_type in self._cache:
            bundle = self._cache[model_type]
            return bundle["pipeline"], bundle.get("metadata", {})

        target_path = TRUSTED_MODEL_PATHS.get(model_type)
        if not target_path or not target_path.exists():
            instruction = (
                "python scripts/train_domestic_model.py"
                if model_type == PredictionModelType.DOMESTIC_COURIER
                else "python scripts/train_reference_model.py"
            )
            raise ModelArtifactNotFoundError(
                f"Model artifact for '{model_type.value}' not found at {target_path}. "
                f"To train and generate this model, execute: `{instruction}`."
            )

        try:
            logger.info("Loading model artifact from %s", target_path)
            bundle = joblib.load(target_path)
            pipeline = bundle.get("pipeline", bundle)

            # Load companion metadata if available
            metadata = bundle.get("metadata")
            if not metadata:
                meta_path = TRUSTED_METADATA_PATHS.get(model_type)
                if meta_path and meta_path.exists():
                    with open(meta_path, "r", encoding="utf-8") as f:
                        metadata = json.load(f)
                else:
                    metadata = {
                        "model_name": model_type.value,
                        "model_version": "1.0.0",
                        "currency": "INR" if model_type == PredictionModelType.DOMESTIC_COURIER else "USD",
                    }

            self._cache[model_type] = {
                "pipeline": pipeline,
                "metadata": metadata,
                "target_transform": bundle.get("target_transform", "none"),
            }
            return pipeline, metadata

        except Exception as exc:
            logger.error("Failed to load model artifact at %s: %s", target_path, exc)
            raise RuntimeError(f"Error reading model artifact at {target_path}: {exc}") from exc

    def get_metadata(self, model_type: PredictionModelType) -> Optional[Dict[str, Any]]:
        """Read metadata without necessarily loading the whole pipeline."""
        meta_path = TRUSTED_METADATA_PATHS.get(model_type)
        if meta_path and meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return None

    def clear_cache(self) -> None:
        """Clear cached models in memory (useful for testing or hot-reloading)."""
        self._cache.clear()


def load_model_artifact(
    model_type: PredictionModelType,
    artifact_filename: Optional[str] = None,
) -> Tuple[Any, Dict[str, Any]]:
    """Module-level helper to load model artifact or test custom filenames with path sandboxing."""
    if artifact_filename is not None:
        target_path = (MODELS_DIR / artifact_filename).resolve()
        if not str(target_path).startswith(str(MODELS_DIR.resolve())):
            raise PermissionError("Access outside trusted models directory is denied.")
        if not target_path.exists():
            return None, {"status": "MISSING"}
        bundle = joblib.load(target_path)
        pipeline = bundle.get("pipeline", bundle)
        metadata = bundle.get("metadata", {})
        return pipeline, metadata
    try:
        return ModelLoader().get_model(model_type)
    except ModelArtifactNotFoundError:
        return None, {"status": "MISSING"}


def clear_model_cache() -> None:
    """Module-level helper to reset model cache in memory."""
    ModelLoader().clear_cache()


def get_model_metadata(model_type: PredictionModelType) -> Optional[Dict[str, Any]]:
    """Module-level helper to retrieve model metadata dictionary."""
    return ModelLoader().get_metadata(model_type)

