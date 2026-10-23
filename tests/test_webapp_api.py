"""
Test suite for the HTML, CSS, JavaScript Web Application & FastAPI backend.
Validates static file serving, rate shopping, 10-scenario tests, dispatch,
and label generation.
"""

import pytest
from fastapi.testclient import TestClient
from src.server import app


@pytest.fixture
def client():
    return TestClient(app)


def test_index_html_served(client):
    """Test that index.html is served at root."""
    res = client.get("/")
    assert res.status_code == 200
    assert "Adaptive Shipping Intelligence Agent" in res.text
    assert "<!DOCTYPE html>" in res.text


def test_static_assets_served(client):
    """Test that CSS and JavaScript static assets are accessible."""
    css_res = client.get("/static/css/style.css")
    assert css_res.status_code == 200
    assert "--bg-app" in css_res.text

    js_res = client.get("/static/js/app.js")
    assert js_res.status_code == 200
    assert "fetchAndRenderRates" in js_res.text


def test_health_endpoint(client):
    """Test health check API."""
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert "database" in data


def test_list_and_run_scenarios(client):
    """Test listing scenarios and running a deterministic test."""
    res = client.get("/api/scenarios")
    assert res.status_code == 200
    scenarios = res.json()
    assert len(scenarios) == 10
    sc_ids = [s["id"] for s in scenarios]
    assert "DEMO_A" in sc_ids
    assert "DEMO_J" in sc_ids

    # Run Demo A
    run_res = client.post("/api/scenarios/DEMO_A/run")
    assert run_res.status_code == 200
    run_data = run_res.json()
    assert run_data["status"] == "PASSED"


def test_rate_shopping_and_dispatch_flow(client):
    """Test complete flow: calculate rates, approve, dispatch, and download label."""
    # 1. Rates
    rate_res = client.post("/api/rates", json={
        "sender_name": "Test Warehouse",
        "sender_pin": "110001",
        "recipient_name": "Jane Customer",
        "recipient_pin": "400001",
        "length_cm": 30.0,
        "width_cm": 20.0,
        "height_cm": 15.0,
        "parcel_weight_kg": 2.0,
        "package_type": "box",
        "is_cod": False,
        "cod_amount": 0.0,
        "ranking_mode": "lowest_cost",
        "provider_mode": "demo",
    })
    assert rate_res.status_code == 200
    data = rate_res.json()
    assert len(data["quotes"]) > 0
    winner_quote = data["quotes"][0]
    order_ref = data["order"]["order_reference"]

    # 2. Dispatch
    disp_res = client.post("/api/dispatch", json={
        "order_reference": order_ref,
        "quote_id": winner_quote["quote_id"],
        "courier_code": winner_quote["courier_code"],
        "approved_by": "Senior Dispatcher",
        "total_cost": winner_quote["cost_breakdown"]["total_cost"],
        "provider_mode": "demo",
    })
    assert disp_res.status_code == 200
    disp_data = disp_res.json()
    assert disp_data["status"] == "CREATED"
    assert disp_data["tracking_number"] is not None
    shp_id = disp_data["shipment_id"]

    # 3. Download Label PDF
    pdf_res = client.get(f"/api/labels/{shp_id}/pdf")
    assert pdf_res.status_code == 200
    assert pdf_res.headers["content-type"] == "application/pdf"
    assert len(pdf_res.content) > 1000
