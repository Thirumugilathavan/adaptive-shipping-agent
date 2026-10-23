"""
Training script for the Domestic Indian Courier Price Prediction Model.
Adapts the machine learning methodology from Rahul-Shelke-1/shipment-price-prediction
(Preprocessing Pipeline + Tree-based Ensemble Regressor + Baseline Comparison)
for Indian domestic parcel shipping priced in INR.

Usage:
    python scripts/train_domestic_model.py
"""

import json
import math
import os
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
from datetime import datetime, timedelta, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error, r2_score, root_mean_squared_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

MODELS_DIR = Path("E:/adaptive shipping agent/models")
MODELS_DIR.mkdir(parents=True, exist_ok=True)

MODEL_ARTIFACT_PATH = MODELS_DIR / "domestic_courier_model.joblib"
METADATA_PATH = MODELS_DIR / "domestic_courier_model.json"
DATASET_SAVE_PATH = MODELS_DIR / "domestic_shipping_training_sample.csv"


def generate_benchmark_domestic_dataset(n_samples: int = 15000) -> pd.DataFrame:
    """
    Generate synthetic shipping records conforming to the teammate dataset schema
    with realistic Indian domestic courier rate structures.
    """
    np.random.seed(42)

    couriers = ["DEMO_AIR_EXPRESS", "DEMO_SURFACE_STD", "DEMO_HEAVY_CARGO", "DELHIVERY", "BLUEDART", "DTDC"]
    pkg_types = ["box", "flyer", "wooden_crate", "custom"]
    metro_pins = ["110001", "110020", "400001", "400050", "560001", "560103", "600001", "700001", "500001"]
    non_metro_pins = ["302001", "411001", "452001", "380001", "226001", "641001", "682001", "141001"]
    remote_pins = ["791111", "795001", "797001", "799001", "190001"]

    all_pins = metro_pins + non_metro_pins + remote_pins

    base_date = datetime(2025, 1, 1)

    records = []
    for i in range(n_samples):
        # Time distribution across 1 year
        days_offset = np.random.randint(0, 365)
        ship_date = base_date + timedelta(days=days_offset)

        o_pin = np.random.choice(all_pins)
        d_pin = np.random.choice(all_pins)
        courier = np.random.choice(couriers)
        pkg_type = np.random.choice(pkg_types)

        # Dimensions & Weights
        l = round(float(np.random.uniform(10.0, 80.0)), 1)
        w = round(float(np.random.uniform(10.0, 60.0)), 1)
        h = round(float(np.random.uniform(5.0, 50.0)), 1)
        dead_weight = round(float(np.random.uniform(0.2, 25.0)), 2)

        vol_weight = (l * w * h) / 5000.0
        chargeable = max(dead_weight, vol_weight)
        slab = math.ceil(chargeable * 2.0) / 2.0  # round up to 0.5 kg

        is_cod = np.random.choice([0, 1], p=[0.75, 0.25])
        cod_amt = round(float(np.random.uniform(200.0, 8000.0)), 2) if is_cod else 0.0

        is_o_metro = o_pin in metro_pins
        is_d_metro = d_pin in metro_pins
        is_remote = d_pin in remote_pins or o_pin in remote_pins

        # Delivery SLA
        if courier in ("DEMO_AIR_EXPRESS", "BLUEDART"):
            sla_days = np.random.randint(1, 3)
            base_rate = 120.0 + (slab - 0.5) * 60.0
        elif courier in ("DEMO_SURFACE_STD", "DELHIVERY", "DTDC"):
            sla_days = np.random.randint(3, 6)
            base_rate = 60.0 + (slab - 0.5) * 35.0
        else:  # Heavy Cargo
            sla_days = np.random.randint(5, 8)
            base_rate = 150.0 + (slab - 5.0) * 25.0 if slab > 5.0 else 150.0

        if is_remote:
            base_rate *= 1.35
            sla_days += 2
        elif is_o_metro and is_d_metro:
            base_rate *= 0.90

        fuel_surcharge = base_rate * 0.12
        cod_fee = max(40.0, cod_amt * 0.02) if is_cod else 0.0
        tax = (base_rate + fuel_surcharge + cod_fee) * 0.18

        # Noise to simulate market fluctuations & surcharges
        noise = np.random.normal(0.0, 8.0)
        total_cost = max(40.0, round(base_rate + fuel_surcharge + cod_fee + tax + noise, 2))

        promised_delivery = ship_date + timedelta(days=sla_days)
        actual_delivery = promised_delivery + timedelta(days=int(np.random.choice([0, 0, 0, 1, 2], p=[0.7, 0.15, 0.05, 0.07, 0.03])))

        records.append({
            "order_id": f"ORD-TRN-{i+1:06d}",
            "ship_date": ship_date.strftime("%Y-%m-%d"),
            "origin_pin": o_pin,
            "destination_pin": d_pin,
            "length_cm": l,
            "width_cm": w,
            "height_cm": h,
            "parcel_weight_kg": dead_weight,
            "volumetric_weight_kg": round(vol_weight, 2),
            "package_type": pkg_type,
            "courier_code": courier,
            "is_cod": is_cod,
            "cod_amount": cod_amt,
            "is_metro_route": int(is_o_metro and is_d_metro),
            "is_remote_route": int(is_remote),
            "promised_delivery_date": promised_delivery.strftime("%Y-%m-%d"),
            "actual_delivery_date": actual_delivery.strftime("%Y-%m-%d"),
            "shipping_cost_inr": total_cost,
        })

    return pd.DataFrame(records)


def train_domestic_model():
    print("Generating/loading domestic shipping training dataset...")
    df = generate_benchmark_domestic_dataset(15000)
    df.to_csv(DATASET_SAVE_PATH, index=False)
    print(f"Dataset generated: {len(df)} records. Saved to {DATASET_SAVE_PATH}")

    # Data Validation & Cleaning
    df = df[df["length_cm"] > 0]
    df = df[df["width_cm"] > 0]
    df = df[df["height_cm"] > 0]
    df = df[df["parcel_weight_kg"] > 0]
    df = df[df["shipping_cost_inr"] > 0]

    num_features = [
        "length_cm",
        "width_cm",
        "height_cm",
        "parcel_weight_kg",
        "volumetric_weight_kg",
        "cod_amount",
    ]
    cat_features = [
        "package_type",
        "courier_code",
        "is_cod",
        "is_metro_route",
        "is_remote_route",
    ]

    target_col = "shipping_cost_inr"

    # Time-based train-test split (first 80% of calendar dates for train, remaining 20% for test)
    df = df.sort_values(by="ship_date").reset_index(drop=True)
    split_idx = int(len(df) * 0.8)

    train_df = df.iloc[:split_idx]
    test_df = df.iloc[split_idx:]

    X_train = train_df[num_features + cat_features]
    y_train = train_df[target_col]

    X_test = test_df[num_features + cat_features]
    y_test = test_df[target_col]

    print(f"Train split: {len(X_train)} samples ({train_df['ship_date'].min()} to {train_df['ship_date'].max()})")
    print(f"Test split : {len(X_test)} samples ({test_df['ship_date'].min()} to {test_df['ship_date'].max()})")

    preprocessor = ColumnTransformer(
        transformers=[
            ("num", Pipeline([
                ("imputer", SimpleImputer(strategy="median")),
                ("scaler", StandardScaler()),
            ]), num_features),
            ("cat", Pipeline([
                ("imputer", SimpleImputer(strategy="constant", fill_value="missing")),
                ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
            ]), cat_features),
        ]
    )

    # 1. Baseline Model 1: Dummy Median Regressor
    dummy = DummyRegressor(strategy="median")
    dummy.fit(X_train, y_train)
    dummy_preds = dummy.predict(X_test)
    dummy_mae = mean_absolute_error(y_test, dummy_preds)
    dummy_rmse = root_mean_squared_error(y_test, dummy_preds)
    dummy_r2 = r2_score(y_test, dummy_preds)

    # 2. Baseline Model 2: Linear Ridge Regressor
    ridge_pipe = Pipeline([
        ("preprocessor", preprocessor),
        ("regressor", Ridge()),
    ])
    ridge_pipe.fit(X_train, y_train)
    ridge_preds = ridge_pipe.predict(X_test)
    ridge_mae = mean_absolute_error(y_test, ridge_preds)
    ridge_rmse = root_mean_squared_error(y_test, ridge_preds)
    ridge_r2 = r2_score(y_test, ridge_preds)

    # 3. Primary ML Model: Gradient Boosting Regressor (Adapted from Reference Ensembles)
    gbr = GradientBoostingRegressor(
        n_estimators=150,
        max_depth=5,
        learning_rate=0.08,
        random_state=42,
    )
    model_pipeline = Pipeline([
        ("preprocessor", preprocessor),
        ("regressor", gbr),
    ])

    print("Fitting Gradient Boosting Regressor pipeline...")
    model_pipeline.fit(X_train, y_train)
    preds = model_pipeline.predict(X_test)

    mae = mean_absolute_error(y_test, preds)
    rmse = root_mean_squared_error(y_test, preds)
    r2 = r2_score(y_test, preds)
    mape = mean_absolute_percentage_error(y_test, preds)

    print("\n" + "=" * 60)
    print("MODEL COMPARISON ON HOLDOUT TEST SPLIT:")
    print("=" * 60)
    print(f"1. Dummy Baseline (Median) : MAE = INR {dummy_mae:.2f} | RMSE = INR {dummy_rmse:.2f} | R2 = {dummy_r2:.4f}")
    print(f"2. Ridge Linear Baseline   : MAE = INR {ridge_mae:.2f} | RMSE = INR {ridge_rmse:.2f} | R2 = {ridge_r2:.4f}")
    print(f"3. Gradient Boosting Model : MAE = INR {mae:.2f} | RMSE = INR {rmse:.2f} | R2 = {r2:.4f} | MAPE = {mape*100:.2f}%")
    print("=" * 60)

    metadata = {
        "model_name": "DomesticCourierPricePredictor",
        "model_version": "1.0.0",
        "feature_schema_version": "1.0.0",
        "algorithm": "GradientBoostingRegressor (150 estimators, max_depth=5, lr=0.08)",
        "currency": "INR",
        "target": "shipping_cost_inr (Total landed shipping price)",
        "features": {
            "numerical": num_features,
            "categorical": cat_features,
        },
        "evaluation_metrics": {
            "holdout_mae_inr": round(float(mae), 2),
            "holdout_rmse_inr": round(float(rmse), 2),
            "holdout_r2": round(float(r2), 4),
            "holdout_mape_percent": round(float(mape * 100), 2),
            "dummy_baseline_mae_inr": round(float(dummy_mae), 2),
            "ridge_baseline_mae_inr": round(float(ridge_mae), 2),
        },
        "training_samples": len(X_train),
        "test_samples": len(X_test),
        "training_date_range": f"{train_df['ship_date'].min()} to {train_df['ship_date'].max()}",
        "testing_date_range": f"{test_df['ship_date'].min()} to {test_df['ship_date'].max()}",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "limitations": [
            "Estimates based on domestic courier rating structures.",
            "Predictions are statistical benchmarks, not confirmed carrier bookings.",
            "Unseen remote PIN codes or extreme oversize items (>100kg) carry higher uncertainty.",
            "Carrier quotes can deviate during holiday peak surcharges or fuel price volatility.",
        ],
    }

    bundle = {
        "pipeline": model_pipeline,
        "metadata": metadata,
        "target_transform": "none",
    }

    joblib.dump(bundle, MODEL_ARTIFACT_PATH)
    with open(METADATA_PATH, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    print(f"Saved domestic courier model artifact to: {MODEL_ARTIFACT_PATH}")
    print(f"Saved domestic courier model metadata to: {METADATA_PATH}")


if __name__ == "__main__":
    train_domestic_model()
