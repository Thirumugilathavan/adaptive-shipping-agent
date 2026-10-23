"""
Training script for Reference Sculpture / Specialty Freight Model.
Reproduces the reference repository's (Rahul-Shelke-1/shipment-price-prediction)
feature engineering and Random Forest regressor on the sculpture dataset.
Serializes model artifact and metadata to models/reference_sculpture_model.joblib.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, r2_score, root_mean_squared_error
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

DATA_PATH = Path("E:/ref_shipment_price_prediction/data/dataset/train.csv")
MODELS_DIR = Path("E:/adaptive shipping agent/models")
MODELS_DIR.mkdir(parents=True, exist_ok=True)

MODEL_ARTIFACT_PATH = MODELS_DIR / "reference_sculpture_model.joblib"
METADATA_PATH = MODELS_DIR / "reference_sculpture_model.json"


def train_reference_model():
    print(f"Loading reference training dataset from: {DATA_PATH}")
    if not DATA_PATH.exists():
        raise FileNotFoundError(f"Reference dataset not found at {DATA_PATH}")

    df = pd.read_csv(DATA_PATH)
    print(f"Dataset loaded: {df.shape[0]} rows, {df.shape[1]} columns")

    num_cols = [
        "Artist Reputation",
        "Height",
        "Width",
        "Weight",
        "Price Of Sculpture",
        "Base Shipping Price",
    ]
    cat_cols = [
        "Material",
        "International",
        "Express Shipment",
        "Installation Included",
        "Transport",
        "Fragile",
        "Customer Information",
        "Remote Location",
    ]

    target_col = "Cost"

    X = df[num_cols + cat_cols].copy()
    y_raw = np.abs(df[target_col].fillna(0.0))
    y_log = np.log1p(y_raw)

    X_train, X_test, y_train_log, y_test_log, y_train_raw, y_test_raw = train_test_split(
        X, y_log, y_raw, test_size=0.2, random_state=42
    )

    num_pipeline = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
    ])

    cat_pipeline = Pipeline([
        ("imputer", SimpleImputer(strategy="constant", fill_value="Missing")),
        ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
    ])

    preprocessor = ColumnTransformer(
        transformers=[
            ("num", num_pipeline, num_cols),
            ("cat", cat_pipeline, cat_cols),
        ]
    )

    regressor = RandomForestRegressor(
        n_estimators=100,
        max_depth=12,
        max_features="sqrt",
        random_state=42,
        n_jobs=-1,
    )

    full_pipeline = Pipeline([
        ("preprocessor", preprocessor),
        ("regressor", regressor),
    ])

    print("Fitting Random Forest Regressor pipeline...")
    full_pipeline.fit(X_train, y_train_log)

    # Evaluation
    preds_log = full_pipeline.predict(X_test)
    preds_raw = np.expm1(np.maximum(preds_log, 0.0))

    r2_log = r2_score(y_test_log, preds_log)
    rmse_log = root_mean_squared_error(y_test_log, preds_log)
    mae_log = mean_absolute_error(y_test_log, preds_log)

    mae_raw = mean_absolute_error(y_test_raw, preds_raw)
    rmse_raw = root_mean_squared_error(y_test_raw, preds_raw)

    print("Evaluation Results on Holdout Set (20%):")
    print(f"  Log-scale R² Score : {r2_log:.4f}")
    print(f"  Log-scale RMSE     : {rmse_log:.4f}")
    print(f"  Log-scale MAE      : {mae_log:.4f}")
    print(f"  USD Raw MAE        : ${mae_raw:,.2f}")
    print(f"  USD Raw RMSE       : ${rmse_raw:,.2f}")

    metadata = {
        "model_name": "ReferenceSculpturePricePredictor",
        "model_version": "1.0.0",
        "algorithm": "RandomForestRegressor (100 trees, max_depth=12)",
        "source_repository": "https://github.com/Rahul-Shelke-1/shipment-price-prediction",
        "license": "MIT License (Copyright (c) 2025 Rahul Shelke)",
        "currency": "USD",
        "target": "Cost (Specialty Sculpture Freight)",
        "target_transformation": "log1p(abs(Cost))",
        "features": {
            "numerical": num_cols,
            "categorical": cat_cols,
        },
        "evaluation_metrics": {
            "holdout_r2_log": round(float(r2_log), 4),
            "holdout_rmse_log": round(float(rmse_log), 4),
            "holdout_mae_log": round(float(mae_log), 4),
            "holdout_mae_usd": round(float(mae_raw), 2),
            "holdout_rmse_usd": round(float(rmse_raw), 2),
        },
        "training_samples": len(X_train),
        "test_samples": len(X_test),
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "limitations": [
            "Specialized for art/sculpture freight, not domestic parcel delivery.",
            "Currency is USD.",
            "Requires art-specific features (Artist Reputation, Material, Price Of Sculpture).",
            "Does not account for Indian postal PIN codes or domestic volumetric divisor rules.",
        ],
    }

    bundle = {
        "pipeline": full_pipeline,
        "metadata": metadata,
        "target_transform": "log1p",
    }

    joblib.dump(bundle, MODEL_ARTIFACT_PATH)
    with open(METADATA_PATH, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    print(f"Saved reference model artifact to: {MODEL_ARTIFACT_PATH}")
    print(f"Saved reference model metadata to: {METADATA_PATH}")


if __name__ == "__main__":
    train_reference_model()
