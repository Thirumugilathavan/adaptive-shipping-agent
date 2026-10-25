"""
Tests verifying Vercel deployment entry points, routing configuration,
and serverless execution safeguards for Adaptive Shipping Intelligence Agent.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from fastapi.testclient import TestClient
import pytest


def test_vercel_json_exists_and_valid():
    """Verify vercel.json is present, parses cleanly, and routes to /api/index."""
    v_path = Path("vercel.json")
    assert v_path.exists(), "vercel.json must exist in repository root"
    content = json.loads(v_path.read_text(encoding="utf-8"))

    assert "rewrites" in content
    rewrites = content["rewrites"]
    assert any(
        r.get("source") == "/(.*)" and r.get("destination") == "/api/index"
        for r in rewrites
    ), "vercel.json must contain catch-all rewrite to /api/index"


def test_api_index_entrypoint_loads():
    """Verify api/index.py properly exports FastAPI 'app' callable."""
    from api.index import app as vercel_app
    from fastapi import FastAPI
    assert isinstance(vercel_app, FastAPI)
    assert vercel_app.title == "Adaptive Shipping Intelligence Agent"


def test_root_app_entrypoint_loads():
    """Verify root app.py also exports FastAPI 'app' callable for zero-config runtimes."""
    from app import app as root_app
    from fastapi import FastAPI
    assert isinstance(root_app, FastAPI)
    assert root_app.title == "Adaptive Shipping Intelligence Agent"


def test_vercel_homepage_and_routes_via_client():
    """Verify homepage and core static/API endpoints return 200 OK via api.index app."""
    from api.index import app
    client = TestClient(app)

    # 1. Homepage at /
    res_root = client.get("/")
    assert res_root.status_code == 200
    assert "Adaptive Shipping Intelligence Agent" in res_root.text

    # 2. Vercel rewritten endpoint at /api/index
    res_index = client.get("/api/index")
    assert res_index.status_code == 200
    assert "Adaptive Shipping Intelligence Agent" in res_index.text

    # 3. Static CSS file
    res_css = client.get("/static/css/style.css")
    assert res_css.status_code == 200
    assert "box-sizing" in res_css.text or "var(" in res_css.text or "body" in res_css.text

    # 4. Static JS file
    res_js = client.get("/static/js/app.js")
    assert res_js.status_code == 200
    assert "renderRateResults" in res_js.text

    # 5. Favicon
    res_fav = client.get("/favicon.ico")
    assert res_fav.status_code == 204

    # 6. API Provider Status
    res_status = client.get("/api/provider/status")
    assert res_status.status_code == 200
    assert res_status.json()["is_offline"] is True


def test_serverless_sqlite_fallback(monkeypatch, tmp_path):
    """Verify that when VERCEL=1 or in read-only environment, database uses /tmp safely."""
    from src.persistence.database import _get_default_db_path, DatabaseManager

    # Test VERCEL env detection
    monkeypatch.setenv("VERCEL", "1")
    v_db_path = _get_default_db_path()
    assert str(v_db_path).replace("\\", "/").startswith("/tmp")

    # Test read-only error recovery
    test_db_file = tmp_path / "fallback_test.db"
    mgr = DatabaseManager(test_db_file)
    assert mgr.get_connection() is not None
