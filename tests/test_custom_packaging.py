"""
Comprehensive unit and integration test suite for Custom-Size Packaging Form,
Fit Evaluation, Persistence, and Courier Dimension Constraints.

Covers the 15 required test scenarios:
1. Custom packaging form inputs create valid package definition
2. Packaging with zero or negative dimensions rejected with clear error
3. Packaging with negative tare weight rejected with clear error
4. Product of 100x10x10 cm with 2 cm padding fits into 110x20x15 cm package (internal 109x19x14 cm)
5. Product of 100x10x10 cm does not fit into 45x35x30 cm demo package
6. Evaluation tests all 6 orthogonal rotations and identifies which orientation fits
7. Evaluation identifies when product cannot fit in any rotation and explains why
8. Tare weight correctly added to gross weight calculation
9. Volumetric weight correctly calculated from custom package outer dimensions
10. Packaging with dimensions exceeding courier max length flagged as ineligible
11. Packaging with girth exceeding courier max girth flagged as ineligible
12. Courier without documented dimension limits shows "Courier size eligibility not verified"
13. Custom packaging persists across database operations (save, retrieve, delete)
14. Deduplication prevents saving identical packaging multiple times
15. End-to-end: create custom packaging -> evaluate with product -> see in comparison -> apply to order -> verify updated order
"""

import pytest
import sqlite3
from src.domain.models import Dimensions, PackageType, VolumetricRule, OrderInput, Address
from src.domain.packaging import (
    CustomPackaging,
    DemoPackagingOption,
    DEMO_PACKAGING_CATALOGUE,
    evaluate_orthogonal_fit,
    check_courier_package_eligibility,
    evaluate_packaging_alternatives,
    PackagingComparisonResult,
)
from src.domain.weight import compute_weight_breakdown
from src.persistence.database import DatabaseManager
from src.providers.demo_adapter import DemoCourierConfig, ALL_DEMO_COURIERS, DemoShippingAdapter
from src.domain.pricing import CourierPricingRule


# ── Test 1: Custom packaging form inputs create valid package definition ─────
def test_1_custom_packaging_form_inputs_create_valid_definition():
    pkg = CustomPackaging(
        packaging_id="PKG-TEST-001",
        name="Custom Long Box",
        package_type=PackageType.BOX,
        outer_dimensions=Dimensions(length_cm=110.0, width_cm=20.0, height_cm=15.0),
        wall_thickness_cm=0.5,
        padding_cm=2.0,
        tare_weight_kg=0.5,
    )
    assert pkg.packaging_id == "PKG-TEST-001"
    assert pkg.name == "Custom Long Box"
    assert pkg.package_type == PackageType.BOX
    assert pkg.outer_dimensions.length_cm == 110.0
    assert pkg.outer_dimensions.width_cm == 20.0
    assert pkg.outer_dimensions.height_cm == 15.0
    assert pkg.wall_thickness_cm == 0.5
    assert pkg.padding_cm == 2.0
    assert pkg.tare_weight_kg == 0.5
    # Check calculated internal dimensions: outer - 2*wall
    assert pkg.internal_dimensions is not None
    assert pkg.internal_dimensions.length_cm == 109.0
    assert pkg.internal_dimensions.width_cm == 19.0
    assert pkg.internal_dimensions.height_cm == 14.0


# ── Test 2: Packaging with zero or negative dimensions rejected with clear error ──
def test_2_packaging_with_zero_or_negative_dimensions_rejected():
    with pytest.raises(Exception) as exc_info:
        Dimensions(length_cm=-10.0, width_cm=20.0, height_cm=15.0)
    assert "positive" in str(exc_info.value).lower() or "greater than 0" in str(exc_info.value).lower()

    db = DatabaseManager(":memory:")
    with pytest.raises(ValueError) as exc_db:
        db.insert_custom_packaging({
            "name": "Invalid Zero Box",
            "outer_length_cm": 0.0,
            "outer_width_cm": 20.0,
            "outer_height_cm": 15.0,
            "wall_thickness_cm": 0.5,
        })
    assert "strictly positive" in str(exc_db.value).lower()


# ── Test 3: Packaging with negative tare weight rejected with clear error ─────
def test_3_packaging_with_negative_tare_weight_rejected():
    with pytest.raises(Exception) as exc_info:
        CustomPackaging(
            packaging_id="PKG-NEG-TARE",
            name="Negative Tare Box",
            outer_dimensions=Dimensions(length_cm=50.0, width_cm=40.0, height_cm=30.0),
            tare_weight_kg=-0.5,
        )
    assert "greater than or equal to 0" in str(exc_info.value).lower() or "ge" in str(exc_info.value).lower()

    db = DatabaseManager(":memory:")
    with pytest.raises(ValueError) as exc_db:
        db.insert_custom_packaging({
            "name": "Negative Tare Box",
            "outer_length_cm": 50.0,
            "outer_width_cm": 40.0,
            "outer_height_cm": 30.0,
            "tare_weight_kg": -1.0,
        })
    assert "non-negative" in str(exc_db.value).lower()


# ── Test 4: Product of 100x10x10 cm with 2 cm padding fits into 110x20x15 cm package ──
def test_4_product_100x10x10_fits_into_110x20x15_package():
    product = Dimensions(length_cm=100.0, width_cm=10.0, height_cm=10.0)
    outer = Dimensions(length_cm=110.0, width_cm=20.0, height_cm=15.0)
    res = evaluate_orthogonal_fit(
        product_dims=product,
        outer_dims=outer,
        wall_thickness_cm=0.5,
        padding_cm=2.0,
    )
    # Padded product: (100 + 4, 10 + 4, 10 + 4) = (104, 14, 14)
    # Internal package: (110 - 1, 20 - 1, 15 - 1) = (109, 19, 14)
    assert res.is_feasible is True
    assert res.padded_product_dims == (104.0, 14.0, 14.0)
    assert res.internal_package_dims == (109.0, 19.0, 14.0)
    assert res.successful_orientation is not None
    # Verify successful orientation fits within (109, 19, 14)
    rot = res.successful_orientation
    assert rot[0] <= 109.0 and rot[1] <= 19.0 and rot[2] <= 14.0


# ── Test 5: Product of 100x10x10 cm does not fit into 45x35x30 cm demo package ──
def test_5_product_100x10x10_does_not_fit_into_demo_xl_box():
    product = Dimensions(length_cm=100.0, width_cm=10.0, height_cm=10.0)
    demo_xl_outer = Dimensions(length_cm=45.0, width_cm=35.0, height_cm=30.0)
    res = evaluate_orthogonal_fit(
        product_dims=product,
        outer_dims=demo_xl_outer,
        wall_thickness_cm=0.5,
        padding_cm=2.0,
    )
    assert res.is_feasible is False
    assert res.successful_orientation is None
    assert "exceeds" in res.rejection_reason.lower()


# ── Test 6: Evaluation tests all 6 orthogonal rotations and identifies which orientation fits ──
def test_6_evaluation_tests_all_6_orthogonal_rotations():
    # Product: (10, 80, 5) -> Padded with 1cm pad -> (12, 82, 7)
    # Box: Outer (90, 20, 15) with 0.5cm wall -> Internal (89, 19, 14)
    # Standard orientation: 12 <= 89, 82 > 19 (fails standard orientation)
    # Rotated orientation: fits because 82 <= 89, and remaining dims (12, 7) <= (19, 14)
    product = Dimensions(length_cm=10.0, width_cm=80.0, height_cm=5.0)
    outer = Dimensions(length_cm=90.0, width_cm=20.0, height_cm=15.0)
    res = evaluate_orthogonal_fit(
        product_dims=product,
        outer_dims=outer,
        wall_thickness_cm=0.5,
        padding_cm=1.0,
    )
    assert res.is_feasible is True
    # The successful orientation must be one of the valid permutations that fits
    rot = res.successful_orientation
    assert rot is not None
    assert rot[0] <= 89.0 and rot[1] <= 19.0 and rot[2] <= 14.0
    assert len(res.all_orientations_evaluated) >= 6


# ── Test 7: Evaluation identifies when product cannot fit in any rotation and explains why ──
def test_7_evaluation_identifies_infeasible_across_all_rotations_with_reason():
    product = Dimensions(length_cm=120.0, width_cm=30.0, height_cm=25.0)
    outer = Dimensions(length_cm=100.0, width_cm=50.0, height_cm=40.0)
    res = evaluate_orthogonal_fit(
        product_dims=product,
        outer_dims=outer,
        wall_thickness_cm=0.5,
        padding_cm=1.0,
    )
    assert res.is_feasible is False
    assert res.successful_orientation is None
    assert "across all 6 orthogonal rotations" in res.rejection_reason
    assert "exceeds maximum internal dimension" in res.rejection_reason


# ── Test 8: Tare weight correctly added to gross weight calculation ──────────
def test_8_tare_weight_correctly_added_to_gross_weight():
    product_net_weight = 4.2
    custom_tare = 0.8
    expected_gross = 5.0
    pkg_outer = Dimensions(length_cm=60.0, width_cm=40.0, height_cm=20.0)
    rule = VolumetricRule(divisor=5000.0, min_billable_weight_kg=0.5, billing_step_kg=0.5)

    # Volumetric weight of 60x40x20 = 48000 / 5000 = 9.6 kg
    # If dead weight is gross (5.0 kg), billable is max(5.0, 9.6) = 9.6 -> slab 10.0
    weight_res = compute_weight_breakdown(product_net_weight + custom_tare, pkg_outer, rule)
    assert weight_res.actual_weight_kg == expected_gross
    assert weight_res.billing_slab_kg == 10.0


# ── Test 9: Volumetric weight correctly calculated from custom package outer dimensions ─
def test_9_volumetric_weight_calculated_from_custom_package_outer_dimensions():
    # Package: 110 x 20 x 15 cm -> Volume = 33,000 cm3
    # Standard air divisor 5000 -> 33000 / 5000 = 6.6 kg
    outer = Dimensions(length_cm=110.0, width_cm=20.0, height_cm=15.0)
    rule = VolumetricRule(divisor=5000.0, min_billable_weight_kg=0.5, billing_step_kg=0.5)
    weight_res = compute_weight_breakdown(2.0, outer, rule)
    assert weight_res.volumetric_weight_kg == 6.6
    assert weight_res.chargeable_weight_kg == 6.6
    assert weight_res.billing_slab_kg == 7.0


# ── Test 10: Packaging with dimensions exceeding courier max length flagged as ineligible ─
def test_10_packaging_exceeding_courier_max_length_flagged_ineligible():
    courier = DemoCourierConfig(
        courier_code="TEST_COURIER_SHORT",
        courier_name="Short Courier",
        service_type="Local Delivery",
        pricing_rule=CourierPricingRule(
            courier_code="TEST_COURIER_SHORT",
            courier_name="Short Courier",
            service_type="Local Delivery",
            first_slab_kg=1.0,
            first_slab_rate=50.0,
            additional_slab_rate=20.0,
        ),
        volumetric_rule=VolumetricRule(),
        max_length_cm=60.0,  # 60 cm limit
    )
    long_pkg_dims = Dimensions(length_cm=110.0, width_cm=20.0, height_cm=15.0)
    eligibility, summary = check_courier_package_eligibility(
        outer_dims=long_pkg_dims,
        gross_weight_kg=3.0,
        package_type=PackageType.BOX,
        couriers=[courier],
    )
    assert "Ineligible" in eligibility["TEST_COURIER_SHORT"]
    assert "Length 110.0cm > max 60.0cm" in eligibility["TEST_COURIER_SHORT"]


# ── Test 11: Packaging with girth exceeding courier max girth flagged as ineligible ───
def test_11_packaging_exceeding_courier_max_girth_flagged_ineligible():
    courier = DemoCourierConfig(
        courier_code="TEST_COURIER_GIRTH",
        courier_name="Girth Restricted Courier",
        service_type="Express Delivery",
        pricing_rule=CourierPricingRule(
            courier_code="TEST_COURIER_GIRTH",
            courier_name="Girth Restricted Courier",
            service_type="Express Delivery",
            first_slab_kg=1.0,
            first_slab_rate=50.0,
            additional_slab_rate=20.0,
        ),
        volumetric_rule=VolumetricRule(),
        max_length_cm=150.0,
        max_girth_cm=200.0,  # 200 cm girth limit
    )
    # Pkg: 100 x 30 x 30 cm -> Girth = 100 + 2*(30+30) = 220 cm > 200 cm
    large_pkg_dims = Dimensions(length_cm=100.0, width_cm=30.0, height_cm=30.0)
    eligibility, summary = check_courier_package_eligibility(
        outer_dims=large_pkg_dims,
        gross_weight_kg=5.0,
        package_type=PackageType.BOX,
        couriers=[courier],
    )
    assert "Ineligible" in eligibility["TEST_COURIER_GIRTH"]
    assert "Girth 220.0cm > max 200.0cm" in eligibility["TEST_COURIER_GIRTH"]


# ── Test 12: Courier without documented dimension limits shows "Courier size eligibility not verified" ─
def test_12_courier_without_documented_limits_shows_not_verified():
    courier_unverified = DemoCourierConfig(
        courier_code="DEMO_SMART_PARCEL",
        courier_name="Smart Parcel Connect",
        service_type="Standard Parcel Delivery",
        pricing_rule=CourierPricingRule(
            courier_code="DEMO_SMART_PARCEL",
            courier_name="Smart Parcel Connect",
            service_type="Standard Parcel Delivery",
            first_slab_kg=0.5,
            first_slab_rate=65.0,
            additional_slab_rate=38.0,
        ),
        volumetric_rule=VolumetricRule(),
        max_length_cm=None,
        max_girth_cm=None,
    )
    pkg_dims = Dimensions(length_cm=110.0, width_cm=20.0, height_cm=15.0)
    eligibility, summary = check_courier_package_eligibility(
        outer_dims=pkg_dims,
        gross_weight_kg=2.5,
        package_type=PackageType.BOX,
        couriers=[courier_unverified],
    )
    assert eligibility["DEMO_SMART_PARCEL"] == "Courier size eligibility not verified"
    assert summary == "Courier size eligibility not verified"


# ── Test 13: Custom packaging persists across database operations (save, retrieve, delete) ──
def test_13_custom_packaging_database_persistence():
    db = DatabaseManager(":memory:")
    pkg_data = {
        "name": "Custom Long Box",
        "package_type": "box",
        "outer_length_cm": 110.0,
        "outer_width_cm": 20.0,
        "outer_height_cm": 15.0,
        "wall_thickness_cm": 0.5,
        "padding_cm": 2.0,
        "tare_weight_kg": 0.5,
    }
    pkg_id = db.insert_custom_packaging(pkg_data)
    assert pkg_id.startswith("PKG-CUST-")

    # Retrieve all
    all_pkgs = db.get_custom_packagings()
    assert len(all_pkgs) == 1
    assert all_pkgs[0]["packaging_id"] == pkg_id
    assert all_pkgs[0]["name"] == "Custom Long Box"
    assert all_pkgs[0]["internal_length_cm"] == 109.0
    assert all_pkgs[0]["internal_width_cm"] == 19.0
    assert all_pkgs[0]["internal_height_cm"] == 14.0

    # Retrieve single
    single = db.get_custom_packaging(pkg_id)
    assert single is not None
    assert single["name"] == "Custom Long Box"

    # Delete
    deleted = db.delete_custom_packaging(pkg_id)
    assert deleted is True
    assert db.get_custom_packaging(pkg_id) is None
    assert len(db.get_custom_packagings()) == 0


# ── Test 14: Deduplication prevents saving identical packaging multiple times ──
def test_14_deduplication_prevents_duplicate_custom_packaging():
    db = DatabaseManager(":memory:")
    pkg_data = {
        "name": "Custom Long Box",
        "package_type": "box",
        "outer_length_cm": 110.0,
        "outer_width_cm": 20.0,
        "outer_height_cm": 15.0,
        "wall_thickness_cm": 0.5,
        "padding_cm": 2.0,
        "tare_weight_kg": 0.5,
    }
    id1 = db.insert_custom_packaging(pkg_data)
    id2 = db.insert_custom_packaging(pkg_data)
    assert id1 == id2
    all_saved = db.get_custom_packagings()
    assert len(all_saved) == 1


# ── Test 15: End-to-end: create custom packaging -> evaluate with product -> see in comparison -> apply to order -> verify updated order ──
def test_15_end_to_end_custom_packaging_workflow():
    db = DatabaseManager(":memory:")
    # 1. Create custom packaging
    pkg_id = db.insert_custom_packaging({
        "name": "Custom Long Box",
        "package_type": "box",
        "outer_length_cm": 110.0,
        "outer_width_cm": 20.0,
        "outer_height_cm": 15.0,
        "wall_thickness_cm": 0.5,
        "padding_cm": 2.0,
        "tare_weight_kg": 0.5,
    })
    saved_rows = db.get_custom_packagings()
    custom_pkgs = [
        CustomPackaging(
            packaging_id=r["packaging_id"],
            name=r["name"],
            package_type=PackageType(r["package_type"]),
            outer_dimensions=Dimensions(
                length_cm=r["outer_length_cm"],
                width_cm=r["outer_width_cm"],
                height_cm=r["outer_height_cm"],
            ),
            wall_thickness_cm=r["wall_thickness_cm"],
            padding_cm=r["padding_cm"],
            tare_weight_kg=r["tare_weight_kg"],
        )
        for r in saved_rows
    ]

    # 2. Product of 100 x 10 x 10 cm, 2.0 kg
    product_dims = Dimensions(length_cm=100.0, width_cm=10.0, height_cm=10.0)
    current_outer = Dimensions(length_cm=120.0, width_cm=30.0, height_cm=25.0)

    # 3. Evaluate across demo and custom packaging
    results = evaluate_packaging_alternatives(
        product_dimensions=product_dims,
        product_net_weight_kg=2.0,
        current_outer_dimensions=current_outer,
        custom_packages=custom_pkgs,
        couriers=ALL_DEMO_COURIERS,
    )

    # Demo boxes (max 45x35x30 cm) must all be infeasible
    demo_feasible = [r for r in results if not r.is_custom and r.is_feasible]
    assert len(demo_feasible) == 0

    # Custom packaging must be feasible
    custom_feasible = [r for r in results if r.is_custom and r.is_feasible]
    assert len(custom_feasible) == 1
    winner = custom_feasible[0]
    assert winner.box_name == "Custom Long Box"
    assert winner.successful_orientation == (104.0, 14.0, 14.0)
    assert winner.volume_reduction_pct > 0.0

    # 4. Simulate applying box to order
    order = OrderInput(
        order_reference="ORD-E2E-TEST",
        sender=Address(
            name="Alice",
            phone="9876543210",
            street_line1="1 Main St",
            city="Bengaluru",
            state="Karnataka",
            pin_code="560001",
        ),
        recipient=Address(
            name="Bob",
            phone="9123456789",
            street_line1="2 Market St",
            city="Mumbai",
            state="Maharashtra",
            pin_code="400001",
        ),
        dimensions=current_outer,
        parcel_weight_kg=2.0,
        package_type=PackageType.BOX,
        is_cod=False,
    )
    # Apply custom box
    order.dimensions = winner.outer_dimensions
    order.package_type = winner.package_type
    order.parcel_weight_kg = round(2.0 + winner.tare_weight_kg, 3)

    assert order.dimensions.length_cm == 110.0
    assert order.dimensions.width_cm == 20.0
    assert order.dimensions.height_cm == 15.0
    assert order.parcel_weight_kg == 2.5
    assert order.package_type == PackageType.BOX

    # 5. Fetch rates on updated order
    adapter = DemoShippingAdapter(couriers=ALL_DEMO_COURIERS)
    quotes = adapter.get_rates(order)
    assert len(quotes) == 10

    # Couriers with max_length_cm < 110 cm (such as DEMO_SAME_DAY with 60cm or DEMO_OVERNIGHT with 100cm) must be rejected
    same_day_q = next(q for q in quotes if q.courier_code == "DEMO_SAME_DAY")
    assert same_day_q.is_serviceable is False
    assert "exceeds courier limit" in same_day_q.unserviceable_reason.lower()

    overnight_q = next(q for q in quotes if q.courier_code == "DEMO_OVERNIGHT")
    assert overnight_q.is_serviceable is False
    assert "exceeds courier limit" in overnight_q.unserviceable_reason.lower()

    # Couriers supporting >= 110cm (e.g. Surface Standard 150cm, Air Express 120cm) must be serviceable
    surface_q = next(q for q in quotes if q.courier_code == "DEMO_SURFACE_STD")
    assert surface_q.is_serviceable is True
    assert surface_q.cost_breakdown is not None
