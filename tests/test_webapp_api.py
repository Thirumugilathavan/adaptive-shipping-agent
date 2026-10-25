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
    assert "expected_outcome" in run_data
    assert "actual_outcome" in run_data


def test_official_scenarios_api(client):
    """Test official competition scenarios endpoints."""
    # List official scenarios
    res = client.get("/api/official-scenarios")
    assert res.status_code == 200
    tcs = res.json()
    assert len(tcs) == 4
    tc_ids = [t["id"] for t in tcs]
    assert tc_ids == ["TC1", "TC2", "TC3", "TC4"]
    for tc in tcs:
        assert "expected" in tc
        assert "route" in tc

    # Run single official test case (TC1)
    run_res = client.post("/api/official-scenarios/TC1/run")
    assert run_res.status_code == 200
    run_data = run_res.json()
    assert run_data["status"] == "PASSED"
    assert run_data["test_case_id"] == "TC1"
    assert "expected_result" in run_data
    assert "actual_result" in run_data

    # Load official test case into form
    load_res = client.post("/api/official-scenarios/TC1/load")
    assert load_res.status_code == 200
    load_data = load_res.json()
    assert "quotes" in load_data
    assert len(load_data["quotes"]) == 3
    assert load_data["has_eligible_couriers"] is True

    # Run all official test cases
    all_res = client.post("/api/official-scenarios/run")
    assert all_res.status_code == 200
    all_data = all_res.json()
    assert len(all_data) == 4
    assert all(r["status"] == "PASSED" for r in all_data)


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


def test_provider_status_and_mode_endpoints(client):
    """Test /api/provider/status and /api/provider/mode."""
    # 1. Get initial status
    status_res = client.get("/api/provider/status")
    assert status_res.status_code == 200
    st_data = status_res.json()
    assert "active_mode" in st_data
    assert "available_modes" in st_data
    assert "demo" in st_data["available_modes"]
    assert "sandbox" in st_data["available_modes"]
    assert "manual_sandbox" in st_data["available_modes"]
    assert st_data.get("is_offline") is True

    # 2. Switch to sandbox mode
    mode_res = client.post("/api/provider/mode", json={"mode": "sandbox"})
    assert mode_res.status_code == 200
    assert mode_res.json()["active_mode"] in ("sandbox", "local_sandbox")

    # Switch explicitly to local_sandbox
    lsbx_res = client.post("/api/provider/mode", json={"mode": "local_sandbox"})
    assert lsbx_res.status_code == 200
    assert lsbx_res.json()["active_mode"] == "local_sandbox"

    # 3. Switch to manual sandbox mode
    manual_res = client.post("/api/provider/mode", json={"mode": "manual_sandbox"})
    assert manual_res.status_code == 200
    assert manual_res.json()["active_mode"] == "manual_sandbox"

    # 4. Unknown/external modes must be rejected
    bad_res = client.post("/api/provider/mode", json={"mode": "shipengine"})
    assert bad_res.status_code == 400

    # 5. Switch back to demo mode
    back_res = client.post("/api/provider/mode", json={"mode": "demo"})
    assert back_res.status_code == 200
    assert back_res.json()["active_mode"] == "demo"


def test_local_courier_sandbox_mode_works_offline(client):
    """Ensure Local Courier Sandbox works completely offline without credentials."""
    rate_res = client.post("/api/rates", json={
        "sender_name": "Sandbox Warehouse",
        "sender_pin": "110001",
        "recipient_name": "Sandbox Customer",
        "recipient_pin": "400001",
        "length_cm": 30.0,
        "width_cm": 20.0,
        "height_cm": 15.0,
        "parcel_weight_kg": 2.0,
        "package_type": "box",
        "is_cod": False,
        "cod_amount": 0.0,
        "ranking_mode": "lowest_cost",
        "provider_mode": "sandbox",
    })
    assert rate_res.status_code == 200
    data = rate_res.json()
    assert "quotes" in data
    quotes = data["quotes"]
    assert len(quotes) > 0
    # Provenance check
    first_q = quotes[0]
    assert first_q["source_type"] == "LOCAL_SANDBOX"
    assert first_q["is_demo_data"] is True
    assert first_q["quote_id"].startswith("QUOTE-SBX-")


def test_manual_sandbox_quote_entry(client):
    """Test manual entry of quote from sandbox dashboard with 'Manual Entry — Not API Verified' labeling."""
    # Create order first via demo rates
    rate_res = client.post("/api/rates", json={
        "sender_name": "Manual Warehouse",
        "sender_pin": "110001",
        "recipient_name": "Manual Receiver",
        "recipient_pin": "400001",
        "length_cm": 25.0,
        "width_cm": 20.0,
        "height_cm": 10.0,
        "parcel_weight_kg": 1.5,
        "package_type": "box",
        "is_cod": False,
        "cod_amount": 0.0,
        "ranking_mode": "lowest_cost",
        "provider_mode": "demo",
    })
    order_ref = rate_res.json()["order"]["order_reference"]

    # Post manual sandbox quote
    mq_res = client.post("/api/manual-sandbox/quote", json={
        "order_reference": order_ref,
        "courier_name": "Delhivery Surface Sandbox",
        "service_type": "Surface Ground",
        "base_freight": 95.0,
        "fuel_surcharge": 9.5,
        "tax_amount": 18.81,
        "total_cost": 123.31,
        "estimated_delivery_days": 4,
        "dashboard_reference": "DLV-SBX-ORD-9912",
        "notes": "Verified rate entered from Delhivery sandbox portal",
    })
    assert mq_res.status_code == 200
    mq_data = mq_res.json()
    assert mq_data["status"] == "created"
    assert mq_data["quote"]["source_type"] == "MANUAL_SANDBOX"
    assert mq_data["quote"]["is_demo_data"] is False
    assert "Manual Entry — Not API Verified" in mq_data["quote"]["courier_name"]
    assert mq_data["quote"]["cost_breakdown"]["total_cost"] == 123.31


def test_sandbox_selection_switches_provider_and_next_quote_uses_sandbox_adapter(client):
    """
    REGRESSION TEST: Selecting Sandbox from dropdown / setting mode switches active provider
    to 'local_sandbox', and subsequent quote requests automatically use LocalSandboxShippingAdapter
    generating sandbox-labelled synthetic quotes without any external network calls.
    Shipment dispatch must occur only after explicit human approval.
    """
    # 1. Switch mode to sandbox (simulating dropdown selection)
    mode_res = client.post("/api/provider/mode", json={"mode": "sandbox"})
    assert mode_res.status_code == 200
    assert mode_res.json()["active_mode"] == "local_sandbox"

    # Verify status reports local_sandbox active and offline
    status_res = client.get("/api/provider/status")
    assert status_res.status_code == 200
    st_json = status_res.json()
    assert st_json["active_mode"] == "local_sandbox"
    assert st_json["is_offline"] is True
    assert "Sandbox" in st_json["mode_description"]

    # 2. Issue quote request WITHOUT explicit provider_mode (inheriting active server provider mode)
    rate_res = client.post("/api/rates", json={
        "sender_name": "Warehouse Alpha",
        "sender_pin": "560103",
        "recipient_name": "Customer Beta",
        "recipient_pin": "411001",
        "length_cm": 25.0,
        "width_cm": 20.0,
        "height_cm": 15.0,
        "parcel_weight_kg": 2.0,
        "package_type": "box",
        "is_cod": False,
        "cod_amount": 0.0,
        "ranking_mode": "lowest_cost",
    })
    assert rate_res.status_code == 200
    rate_data = rate_res.json()
    quotes = rate_data["quotes"]
    assert len(quotes) > 0

    # 3. Confirm all quotes generated came from LocalSandboxShippingAdapter
    for q in quotes:
        assert q["source_type"] == "LOCAL_SANDBOX"
        assert q["is_demo_data"] is True
        assert q["quote_id"].startswith("QUOTE-SBX-")
        assert "Sandbox Simulation" in q["courier_name"]
        assert "[Local Sandbox Simulation]" in q["decision_explanation"]
        assert any("sandbox" in ev.lower() for ev in q.get("missing_evidence", []))

    winning_quote = quotes[0]
    order_ref = rate_data["order"]["order_reference"]

    # 4. Confirm unapproved dispatch is rejected by approval gate
    unapproved_dispatch = client.post("/api/dispatch", json={
        "order_reference": order_ref,
        "quote_id": winning_quote["quote_id"],
        "courier_code": winning_quote["courier_code"],
        "approved_by": "Operator",
        "total_cost": winning_quote["cost_breakdown"]["total_cost"] + 50.0,  # Price mismatch triggers invalidation
    })
    assert unapproved_dispatch.status_code in (400, 404, 409)

    # 5. Confirm approved dispatch generates SBX-TRK- tracking and local printable label
    approved_dispatch = client.post("/api/dispatch", json={
        "order_reference": order_ref,
        "quote_id": winning_quote["quote_id"],
        "courier_code": winning_quote["courier_code"],
        "approved_by": "Compliance Lead",
        "total_cost": winning_quote["cost_breakdown"]["total_cost"],
    })
    assert approved_dispatch.status_code == 200
    disp_data = approved_dispatch.json()
    assert disp_data["status"] == "CREATED"
    assert disp_data["tracking_number"].startswith("SBX-TRK-")
    assert disp_data["shipment_id"].startswith("SHP-")
    assert disp_data["label_html_preview"] is not None
    assert disp_data["pickup"] is not None
    assert disp_data["pickup"]["pickup_reference"].startswith("SBX-PKP-")
    assert disp_data["pickup"]["status"] in ("CONFIRMED", "CONFIRMED_SIMULATED")
    assert disp_data["pickup"]["is_demo_data"] is True
    assert "LOCAL COURIER SANDBOX" in disp_data["pickup"]["disclaimer"]
    assert disp_data["verification_report"]["overall_status"] == "VERIFIED"

    # Verify binary PDF label is downloadable and valid
    pdf_res = client.get(f"/api/labels/{disp_data['shipment_id']}/pdf")
    assert pdf_res.status_code == 200
    assert pdf_res.headers["content-type"] == "application/pdf"
    assert len(pdf_res.content) > 1000

    # 6. Reset provider mode back to demo
    reset_res = client.post("/api/provider/mode", json={"mode": "demo"})
    assert reset_res.status_code == 200
    assert reset_res.json()["active_mode"] == "demo"
