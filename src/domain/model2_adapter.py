"""
Model 2 Adapter: Courier Intelligence Engine (CatBoost Rate & SLA Models).

Integrates the trained Model 2 artifacts:
- rate_model.cbm: Historical freight rate prediction in INR (CatBoostRegressor)
- delivery_model.cbm: Historical transit turnaround prediction in days (CatBoostRegressor)
- courier_pin_lookup.pkl: Historical synthetic PIN coverage lookup per courier

Safeguards:
1. Strict non-binding benchmark: Predictions never override binding contractual provider quotes.
2. Separation of concerns: Serviceability decisions always precede and constrain model predictions.
3. Feature normalization: Corrects origin_pin/dest_pin mismatch to exact trained features (pickup_pin, delivery_pin).
4. Lazy loading: Models and lookup caches are initialized only on first invocation.
5. Graceful degradation: Controlled errors if CatBoost or model files are missing; never breaks the main dispatch flow.
6. Local and offline: No external APIs, LLM calls, or network requests required.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Base directory for Model 2 artifacts
BASE_DIR = Path(__file__).resolve().parent.parent.parent
MODEL2_DIR = BASE_DIR / "models" / "model2"

RATE_MODEL_PATH = MODEL2_DIR / "rate_model.cbm"
DELIVERY_MODEL_PATH = MODEL2_DIR / "delivery_model.cbm"
PIN_LOOKUP_PATH = MODEL2_DIR / "courier_pin_lookup.pkl"
RATE_META_PATH = MODEL2_DIR / "rate_model_metadata.json"
DELIVERY_META_PATH = MODEL2_DIR / "delivery_model_metadata.json"

# Strict 7-feature order expected by both trained CatBoost models
MODEL2_FEATURE_ORDER = [
    "pickup_pin",
    "delivery_pin",
    "weight_kg",
    "length_cm",
    "width_cm",
    "height_cm",
    "courier_name",
]

# Supported couriers present in Model 2 training dataset
MODEL2_KNOWN_COURIERS = [
    "CourierX",
    "ShipNow",
    "ParcelPro",
    "BlueDart",
    "QuickLogistics",
    "RapidShip",
    "FastShip",
    "ExpressWay",
    "SpeedyExpress",
    "IndiaCourier",
]

# Mapping from application demo codes to historical reference brands for benchmark comparison
COURIER_CODE_MAPPING: Dict[str, str] = {
    "DEMO_AIR_EXPRESS": "BlueDart",
    "DEMO_SURFACE_STD": "IndiaCourier",
    "DEMO_HEAVY_CARGO": "ParcelPro",
    "DEMO_SAME_DAY": "RapidShip",
    "DEMO_OVERNIGHT": "ExpressWay",
    "DEMO_ECONOMY_CARGO": "CourierX",
    "DEMO_BUDGET_SAVER": "ShipNow",
    "DEMO_REGIONAL_ROAD": "QuickLogistics",
    "DEMO_B2B_LOGISTICS": "FastShip",
    "DEMO_SMART_PARCEL": "SpeedyExpress",
    "DEMO_EXPRESS_GOLD": "RapidShip",
    "Blue Dart Express (Simulated)": "BlueDart",
    "Delhivery Surface (Simulated)": "IndiaCourier",
    "DTDC Heavy Cargo (Simulated)": "CourierX",
    "XpressBees Same-Day (Simulated)": "RapidShip",
    "India Post Parcel (Simulated)": "ShipNow",
    "Blue Dart Overnight (Simulated)": "ExpressWay",
    "DTDC Regional (Simulated)": "QuickLogistics",
    "Delhivery Freight (Simulated)": "FastShip",
    "Ecom Express Parcel (Simulated)": "SpeedyExpress",
    "XpressBees Priority (Simulated)": "RapidShip",
    "Blue Dart": "BlueDart",
    "Delhivery": "IndiaCourier",
    "DTDC": "CourierX",
    "India Post": "ShipNow",
    "XpressBees": "RapidShip",
    "Ecom Express": "SpeedyExpress",
}


class Model2NormalizedInput(BaseModel):
    """Clean validated inputs mapped to exact CatBoost training feature names."""
    pickup_pin: str = Field(..., description="6-digit origin pickup postal code")
    delivery_pin: str = Field(..., description="6-digit destination delivery postal code")
    weight_kg: float = Field(..., gt=0.0, description="Shipment billable/chargeable weight in kg")
    length_cm: float = Field(..., gt=0.0, description="Length in cm")
    width_cm: float = Field(..., gt=0.0, description="Width in cm")
    height_cm: float = Field(..., gt=0.0, description="Height in cm")
    courier_name: str = Field(..., description="Courier brand name recognized by Model 2")


class Model2PricePrediction(BaseModel):
    """Structured response for Model 2 historical price estimation."""
    predicted_price_inr: Optional[float] = None
    currency: str = "INR"
    model_name: str = "CatBoostRegressor (Model 2)"
    model_version: str = "2.0.0"
    status: str = "SUCCESS"  # SUCCESS, MODEL_UNAVAILABLE, FRAMEWORK_MISSING, ERROR
    is_provider_quote: bool = False
    mae: Optional[float] = None
    rmse: Optional[float] = None
    error_message: Optional[str] = None
    disclaimer: str = (
        "Non-binding historical rate benchmark from Model 2. "
        "Binding carrier quotes govern actual booking and billing."
    )


class Model2DeliveryPrediction(BaseModel):
    """Structured response for Model 2 transit SLA estimation."""
    predicted_days: Optional[float] = None
    rounded_days: Optional[int] = None
    status: str = "SUCCESS"
    model_name: str = "CatBoostRegressor SLA (Model 2)"
    mae: Optional[float] = None
    rmse: Optional[float] = None
    error_message: Optional[str] = None
    disclaimer: str = (
        "Machine-learning delivery turnaround estimate based on historical transit records. "
        "Carrier service-level agreements govern dispatch deadlines."
    )


class Model2PinServiceabilityResult(BaseModel):
    """Historical synthetic PIN coverage evidence."""
    origin_pin: str
    destination_pin: str
    courier_name: str
    status: str  # SERVICEABLE_CONFIRMED, UNKNOWN
    evidence_type: str  # historical_synthetic_dataset, none
    has_historical_coverage: bool
    evidence_count: int
    disclaimer: str = (
        "Evidence from synthetic historical shipment records. "
        "Does not represent verified live carrier network coverage."
    )


def _resolve_model_path(target_path: Path) -> Path:
    """
    Ensure the target CatBoost model file is a valid binary CBM file.
    If the file is missing, corrupted, or an unresolved Git LFS pointer,
    transparently unpacks the bundled .cbm.gz archive to a temporary directory.
    """
    is_valid_binary = False
    if target_path.is_file() and target_path.stat().st_size > 1000:
        try:
            with open(target_path, "rb") as f:
                header = f.read(4)
                if header == b"CBM1":
                    is_valid_binary = True
        except Exception:
            is_valid_binary = False

    if is_valid_binary:
        return target_path

    # Check for compressed variant in models directory
    gz_path = target_path.with_name(f"{target_path.name}.gz")
    if gz_path.is_file():
        import gzip
        import shutil
        import tempfile

        tmp_target = Path(tempfile.gettempdir()) / target_path.name
        if not tmp_target.is_file() or tmp_target.stat().st_size < 1000:
            with gzip.open(gz_path, "rb") as f_in, open(tmp_target, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        return tmp_target

    return target_path


# -----------------------------------------------------------------------------
# Model 2 Adapter Singleton Engine
# -----------------------------------------------------------------------------

class Model2Adapter:
    """
    Lazy-loading runtime adapter for Model 2 CatBoost artifacts.
    Provides isolated inference, input mapping, and safe PIN lookups.
    """

    def __init__(self, models_dir: Optional[Path] = None) -> None:
        self.models_dir = models_dir or MODEL2_DIR
        self._rate_model: Any = None
        self._delivery_model: Any = None
        self._pin_lookup: Optional[Dict[str, Set[int]]] = None
        self._rate_metadata: Optional[Dict[str, Any]] = None
        self._delivery_metadata: Optional[Dict[str, Any]] = None
        self._catboost_checked: bool = False
        self._catboost_available: bool = False

    def is_catboost_available(self) -> bool:
        """Check whether catboost is installed and importable."""
        if not self._catboost_checked:
            try:
                import catboost  # noqa: F401
                self._catboost_available = True
            except ImportError:
                self._catboost_available = False
            self._catboost_checked = True
        return self._catboost_available

    def _load_metadata(self) -> None:
        """Load metadata for both models if present."""
        rate_meta = self.models_dir / "rate_model_metadata.json"
        if rate_meta.is_file() and self._rate_metadata is None:
            try:
                with open(rate_meta, "r", encoding="utf-8") as f:
                    self._rate_metadata = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to read rate_model_metadata.json: {e}")

        delivery_meta = self.models_dir / "delivery_model_metadata.json"
        if delivery_meta.is_file() and self._delivery_metadata is None:
            try:
                with open(delivery_meta, "r", encoding="utf-8") as f:
                    self._delivery_metadata = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to read delivery_model_metadata.json: {e}")

    def _get_rate_model(self) -> Any:
        """Lazy load rate CatBoost model."""
        if self._rate_model is not None:
            return self._rate_model

        if not self.is_catboost_available():
            raise RuntimeError("CatBoost is not installed in the Python runtime environment.")

        model_path = _resolve_model_path(self.models_dir / "rate_model.cbm")
        if not model_path.is_file():
            raise FileNotFoundError(f"Rate model artifact not found at: {model_path}")

        from catboost import CatBoostRegressor
        model = CatBoostRegressor()
        model.load_model(str(model_path))
        self._rate_model = model
        self._load_metadata()
        return self._rate_model

    def _get_delivery_model(self) -> Any:
        """Lazy load delivery CatBoost model."""
        if self._delivery_model is not None:
            return self._delivery_model

        if not self.is_catboost_available():
            raise RuntimeError("CatBoost is not installed in the Python runtime environment.")

        model_path = _resolve_model_path(self.models_dir / "delivery_model.cbm")
        if not model_path.is_file():
            raise FileNotFoundError(f"Delivery model artifact not found at: {model_path}")

        from catboost import CatBoostRegressor
        model = CatBoostRegressor()
        model.load_model(str(model_path))
        self._delivery_model = model
        self._load_metadata()
        return self._delivery_model

    def _get_pin_lookup(self) -> Dict[str, Set[int]]:
        """Lazy load lightweight PIN lookup table."""
        if self._pin_lookup is not None:
            return self._pin_lookup

        lookup_path = self.models_dir / "courier_pin_lookup.pkl"
        if not lookup_path.is_file():
            raise FileNotFoundError(f"PIN lookup artifact not found at: {lookup_path}")

        import pickle
        with open(lookup_path, "rb") as f:
            data = pickle.load(f)
            if not isinstance(data, dict):
                raise ValueError("Unexpected PIN lookup format; expected dict[str, set[int]].")
            self._pin_lookup = data
        return self._pin_lookup

    def normalize_inputs(self, raw_inputs: Dict[str, Any]) -> Model2NormalizedInput:
        """
        Normalize and map raw inputs to exact CatBoost training feature names and types.
        Resolves origin_pin/dest_pin mismatch and assigns recognized courier brand.
        """
        # Map postal codes
        pickup = (
            raw_inputs.get("pickup_pin")
            or raw_inputs.get("origin_pin")
            or raw_inputs.get("sender_pin")
            or ""
        )
        delivery = (
            raw_inputs.get("delivery_pin")
            or raw_inputs.get("dest_pin")
            or raw_inputs.get("recipient_pin")
            or ""
        )

        # Map weight
        wt = raw_inputs.get("weight_kg")
        if wt is None:
            wt = raw_inputs.get("parcel_weight_kg") or raw_inputs.get("chargeable_weight_kg") or 1.0

        # Map dimensions
        l = raw_inputs.get("length_cm", 20.0)
        w = raw_inputs.get("width_cm", 15.0)
        h = raw_inputs.get("height_cm", 10.0)

        # Map courier name
        raw_c = raw_inputs.get("courier_name") or raw_inputs.get("courier_code") or "BlueDart"
        courier = str(raw_c).strip()
        if courier in COURIER_CODE_MAPPING:
            courier = COURIER_CODE_MAPPING[courier]
        elif courier not in MODEL2_KNOWN_COURIERS:
            # Fall back to BlueDart as standard reference carrier if arbitrary name passed
            courier = "BlueDart"

        return Model2NormalizedInput(
            pickup_pin=str(pickup).strip(),
            delivery_pin=str(delivery).strip(),
            weight_kg=float(wt),
            length_cm=float(l),
            width_cm=float(w),
            height_cm=float(h),
            courier_name=courier,
        )

    def predict_price(self, raw_inputs: Dict[str, Any]) -> Model2PricePrediction:
        """
        Predict historical shipping cost in INR using rate_model.cbm.
        Returns controlled structured prediction without raising unhandled exceptions.
        """
        if not self.is_catboost_available():
            return Model2PricePrediction(
                status="FRAMEWORK_MISSING",
                error_message="CatBoost dependency is not installed in the environment.",
            )

        try:
            model = self._get_rate_model()
            norm = self.normalize_inputs(raw_inputs)
            self._load_metadata()

            import pandas as pd
            row_dict = {
                "pickup_pin": str(norm.pickup_pin),
                "delivery_pin": str(norm.delivery_pin),
                "weight_kg": float(norm.weight_kg),
                "length_cm": float(norm.length_cm),
                "width_cm": float(norm.width_cm),
                "height_cm": float(norm.height_cm),
                "courier_name": str(norm.courier_name),
            }
            df = pd.DataFrame([row_dict])[MODEL2_FEATURE_ORDER]

            pred_val = model.predict(df)[0]
            price = round(float(pred_val), 2)
            # Guard against unphysical negative predictions from regression bounds
            price = max(price, 15.0)

            mae = self._rate_metadata.get("mae") if self._rate_metadata else 43.34
            rmse = self._rate_metadata.get("rmse") if self._rate_metadata else 58.43

            return Model2PricePrediction(
                predicted_price_inr=price,
                status="SUCCESS",
                mae=mae,
                rmse=rmse,
            )
        except FileNotFoundError as e:
            return Model2PricePrediction(
                status="MODEL_UNAVAILABLE",
                error_message=str(e),
            )
        except Exception as e:
            logger.error(f"Model 2 rate inference failed: {e}", exc_info=True)
            return Model2PricePrediction(
                status="ERROR",
                error_message=f"Inference error: {str(e)}",
            )

    def predict_delivery_days(self, raw_inputs: Dict[str, Any]) -> Model2DeliveryPrediction:
        """
        Predict estimated delivery SLA transit days using delivery_model.cbm.
        Returns controlled structured prediction without raising unhandled exceptions.
        """
        if not self.is_catboost_available():
            return Model2DeliveryPrediction(
                status="FRAMEWORK_MISSING",
                error_message="CatBoost dependency is not installed in the environment.",
            )

        try:
            model = self._get_delivery_model()
            norm = self.normalize_inputs(raw_inputs)
            self._load_metadata()

            import pandas as pd
            row_dict = {
                "pickup_pin": str(norm.pickup_pin),
                "delivery_pin": str(norm.delivery_pin),
                "weight_kg": float(norm.weight_kg),
                "length_cm": float(norm.length_cm),
                "width_cm": float(norm.width_cm),
                "height_cm": float(norm.height_cm),
                "courier_name": str(norm.courier_name),
            }
            df = pd.DataFrame([row_dict])[MODEL2_FEATURE_ORDER]

            pred_val = model.predict(df)[0]
            days = float(pred_val)
            # Physical bounds: minimum 1 day transit
            rounded = max(int(round(days)), 1)

            mae = self._delivery_metadata.get("mae") if self._delivery_metadata else 0.54
            rmse = self._delivery_metadata.get("rmse") if self._delivery_metadata else 0.57

            return Model2DeliveryPrediction(
                predicted_days=round(days, 2),
                rounded_days=rounded,
                status="SUCCESS",
                mae=mae,
                rmse=rmse,
            )
        except FileNotFoundError as e:
            return Model2DeliveryPrediction(
                status="MODEL_UNAVAILABLE",
                error_message=str(e),
            )
        except Exception as e:
            logger.error(f"Model 2 SLA inference failed: {e}", exc_info=True)
            return Model2DeliveryPrediction(
                status="ERROR",
                error_message=f"Inference error: {str(e)}",
            )

    def check_historical_pin_coverage(
        self, origin_pin: str, destination_pin: str, courier_name: str
    ) -> Model2PinServiceabilityResult:
        """
        Check whether origin and destination PINs are recorded in the courier's
        historical synthetic dataset PIN set.

        Returns:
        - SERVICEABLE_CONFIRMED: Both PINs explicitly present in courier historical dataset.
        - UNKNOWN: PINs not present in historical set. (Never declares unserviceable).
        """
        norm_courier = courier_name.strip()
        if norm_courier in COURIER_CODE_MAPPING:
            norm_courier = COURIER_CODE_MAPPING[norm_courier]

        try:
            lookup = self._get_pin_lookup()
        except Exception:
            return Model2PinServiceabilityResult(
                origin_pin=origin_pin,
                destination_pin=destination_pin,
                courier_name=norm_courier,
                status="UNKNOWN",
                evidence_type="none",
                has_historical_coverage=False,
                evidence_count=0,
            )

        courier_pins = lookup.get(norm_courier)
        if not courier_pins:
            return Model2PinServiceabilityResult(
                origin_pin=origin_pin,
                destination_pin=destination_pin,
                courier_name=norm_courier,
                status="UNKNOWN",
                evidence_type="none",
                has_historical_coverage=False,
                evidence_count=0,
            )

        try:
            orig_int = int(str(origin_pin).strip())
            dest_int = int(str(destination_pin).strip())
        except (ValueError, TypeError):
            return Model2PinServiceabilityResult(
                origin_pin=str(origin_pin),
                destination_pin=str(destination_pin),
                courier_name=norm_courier,
                status="UNKNOWN",
                evidence_type="none",
                has_historical_coverage=False,
                evidence_count=0,
            )

        if orig_int in courier_pins and dest_int in courier_pins:
            return Model2PinServiceabilityResult(
                origin_pin=str(origin_pin),
                destination_pin=str(destination_pin),
                courier_name=norm_courier,
                status="SERVICEABLE_CONFIRMED",
                evidence_type="historical_synthetic_dataset",
                has_historical_coverage=True,
                evidence_count=2,
            )

        return Model2PinServiceabilityResult(
            origin_pin=str(origin_pin),
            destination_pin=str(destination_pin),
            courier_name=norm_courier,
            status="UNKNOWN",
            evidence_type="none",
            has_historical_coverage=False,
            evidence_count=0,
        )


# Global adapter instance
model2_adapter = Model2Adapter()
