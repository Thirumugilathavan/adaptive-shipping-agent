# Adaptive Shipping Intelligence Agent
> **Autonomous, Constraint-Aware Logistics Decision Support & Execution Gate Engine**  
> *Category:* Business & Productivity AI — ZiroDigital Agentic AI Hackathon 2026  
> *Live Entrypoint:* `python -m streamlit run src/ui/app.py`  
> *Test Suite:* `python -m pytest tests/` (268/269 Passing, 1 opt-in live test skipped) | `python run_official_scenarios.py` (4/4 Passing)

---

## 1. Executive Summary & Problem Fit

E-commerce merchants and warehouse logistics managers face substantial margin leakage from shipping inefficiencies:
1. **Volumetric Weight Surprises:** Bulky, lightweight items incur steep dimensional surcharges (up to 300% of base freight) because packaging is selected arbitrarily.
2. **Hidden Carrier Constraints:** Items exceeding courier dimension thresholds (e.g., length > 100 cm or girth > 300 cm) are accepted at dispatch but rejected or fined during terminal sorting.
3. **Ghost Bookings & Double-Billing:** Network timeouts during booking cause operators to blindly retry, creating duplicate waybills and unrecoverable freight charges.
4. **Stale Rates & Unserviceable Pincodes:** Price drift occurs when quotes age past validity windows, and remote pincodes cause booking rejections if serviceability isn't pre-validated.

The **Adaptive Shipping Intelligence Agent** solves these challenges by combining:
- **Autonomous 6-Orientation Packaging Optimization:** Evaluates 3D rotational fits against standard and user-defined custom boxes, calculating exact volumetric savings.
- **Dual-Provider Architecture:** Pluggable switching between **Demo Multi-Courier Simulator** and **Genuine ShipEngine Sandbox API**.
- **Explainable Multi-Stage Decision Engine:** Chronologically documents Observation, Constraint Evaluation, Deterministic Ranking, Human Gate Approval, Safe Execution, and Independent 5-Point Verification.
- **Zero-Drift Safety & Reconciliation:** Enforces idempotency locks, validates timestamp currency (<15m), and blocks blind retries on uncertain dispatch timeouts.

```mermaid
flowchart LR
    A["Stage A: Observe<br/>(Intake & PIN Validation)"] --> B["Stage B: Evaluate<br/>(6-Orientation Fit & Limits)"]
    B --> C["Stage C: Decide<br/>(Deterministic Ranking & Margins)"]
    C --> D["Stage D: Gate<br/>(Human Approval Mandatory Lock)"]
    D --> E["Stage E: Execute<br/>(Provider Dispatch & Idempotency)"]
    E --> F["Stage F: Verify<br/>(5-Point Audit & PDF Label)"]
```

---

## 2. Quantitative Innovation Benchmark

To validate the agentic capability against conventional shipping calculators (which simply pick the lowest price without constraint checks), we executed a reproducible benchmark matrix (`python -m src.domain.benchmark`):

| Evaluation Metric | Naive Rate Picker (Baseline) | Adaptive Shipping Agent | Measured Improvement |
| :--- | :---: | :---: | :---: |
| **Total Test Scenarios** | 4 | 4 | 100% Evaluated |
| **Packaging Fit Failures (Clipping/Overflow)** | **1** (Box overflow ignored) | **0** (6-orientation fit verified) | **100% Elimination** |
| **Stale Quote Dispatches (>15m age)** | **1** (Stale price booked) | **0** (Freshness gate blocked) | **100% Drift Prevention** |
| **Duplicate Dispatches on Timeout** | **1** (Blind retry created duplicate) | **0** (Idempotency barrier locked) | **Zero Double-Billing** |
| **Infeasible Recommendation Halts** | 0 (Unchecked dispatch) | 1 (Safe operator intervention) | Safe Failure Mode |
| **Decision Trace Transparency & Audit** | 20.0% (Opaque total only) | **100.0%** (Full 6-stage evidence) | **+80.0% Explainability** |

---

## 3. Shipping Provider Modes: Demo vs. ShipEngine Sandbox

The application features a provider toggle in the sidebar:

### Mode 1: Demo Simulator (Default)
- **Zero API credentials required:** 10 simulated couriers covering Air, Surface, Cargo, and Hyperlocal express delivery.
- **Realistic fee breakdowns:** Base freight slabs, fuel surcharges, COD handling fees, and GST tax calculations.
- **Evaluator Scenarios:** 10 one-click benchmark scenarios (`Demo A` through `Demo J`) and 4 official test cases (`TC1` through `TC4`).

### Mode 2: ShipEngine Sandbox API
- **Official REST Endpoints:** Direct communication with `https://api.shipengine.com/v1` for real live sandbox carrier rates (`POST /v1/rates`), test shipping labels (`POST /v1/labels/rates/{rate_id}`), and tracking queries (`GET /v1/tracking`).
- **Strict Sandbox Credential Enforcement:** Only API keys prefixed with `TEST_` are accepted. Production/live credentials (`LIVE_`, `PROD_`, `sk_live_`) are immediately refused to prevent accidental charges.
- **Secret Redaction:** API keys are never printed in logs, displayed in full in the UI, or written to SQLite tracebacks. Masked preview format: `TEST_••••••••xxxx`.
- **Zero Silent Fallbacks:** If ShipEngine returns no rates or carrier errors for an unsupported route, the genuine carrier response is displayed directly without fabricating fake rates.

#### Configuring ShipEngine Sandbox Credentials
Set your ShipEngine test key via environment variable:
```powershell
$env:SHIPENGINE_API_KEY = "TEST_your_sandbox_api_key_here"
```
Or in `.streamlit/secrets.toml`:
```toml
shipengine_api_key = "TEST_your_sandbox_api_key_here"
```

Use the **🧪 Test Connection** button in the sidebar to verify your sandbox connection non-destructively (`GET /v1/carriers`).

---

## 4. Smart Personalized Shipping Advisor & Closest-Match Fallback

The agent replaces generic "lowest price" tables with an interactive, constraint-aware shipping advisor:

### User Shipping Preferences
- **Maximum Shipping Budget:** Define a maximum target cost in INR (₹) or choose "Flexible budget".
- **Delivery Deadline:** Target arrival time ("Within 1 day", "Within 2 days", "Within 3 days", or "Flexible delivery time").
- **Live In-Situ Adjustment:** Users can quickly tweak their preferences or click 1-click adjustment buttons (`+₹50`, `+₹100`, `+1 Day`, `Flexible`) directly in the results view to see recommendations update live.

### Decision & Ranking Rules
1. **Exact Preference Match:** Couriers satisfying both budget and deadline are prioritized. The lowest verified total cost among them is selected. Ties are broken by faster delivery SLA and carrier constraints.
2. **Deterministic Closest-Match Fallback:** When no single courier satisfies both constraints, the engine never halts with "no courier found". It evaluates trade-offs and highlights:
   - **Best Compromise:** The candidate with the optimal balance (single constraint violations strictly prioritized over double violations).
   - **Lowest Cost Alternative:** The cheapest serviceable option on the route.
   - **Fastest Alternative:** The quickest verified delivery option.
3. **Personalized Explanations:**
   - Plain-language bullets explaining why the courier was chosen based on budget and speed.
   - Dynamic budget status (`₹XX below limit` or `₹YY over budget`).
   - Dynamic delivery timeline (`estimated in X days vs target`).
   - Explicit advantages, trade-offs, and verification status.
4. **Alternative Courier Switching & Confirmation Gate:**
   - Users can choose any alternative courier with a single click.
   - Dynamic comparative hints explain what is gained or sacrificed (e.g. *"Costs ₹40 more but arrives 1 day earlier"*).
   - If an alternative violates preferred constraints, a prominent warning and mandatory acknowledgement checkbox gate the approval workflow before dispatch.

---

## 5. Evaluator Quick-Start & 1-Click Demo Scenarios

### Launch the Application
```powershell
# 1. Open PowerShell in project directory
cd "E:\adaptive shipping agent"

# 2. Launch the modern Streamlit web dashboard
python -m streamlit run src/ui/app.py
```
*The web interface will open automatically at `http://localhost:8501`.*

### Sidebar 1-Click Demo Scenarios (Designed for Judges)
The sidebar contains ten pre-configured demonstration scenarios:
1. **🎯 Demo A: Standard Balanced Parcel** (25×20×15 cm, 1.8 kg, COD ₹1,200).
2. **🎯 Demo B: Oversized Elongated Item** (110×20×15 cm — auto-suggests custom 110cm box and filters length-limited couriers).
3. **🎯 Demo C: Cheapest Option Rejected** (Re-choice workflow).
4. **🎯 Demo D: Ambiguous Booking & Reconciliation** (Network drop timeout & idempotency lock).
5. **🎯 Demo E (TC1): Same Parcel on 3 Couriers** (Ranked by lowest total cost).
6. **🎯 Demo F (TC2): Large, Light Box** (Volumetric weight penalty with distinct divisors 5000/4500/4000).
7. **🎯 Demo G (TC3): Destination PIN Not Serviceable** (Courier safely excluded with explicit audit reason).
8. **🎯 Demo H (TC4): Label Created & AWB Verification** (5-point verification with printable PDF label).
9. **🎯 Demo I: Mandatory Measurements & COD Validation**.
10. **🎯 Demo J: No Eligible Couriers & Safe Failure Mode**.

---

## 5. Verification & Test Suite

The test suite covers unit logic, provider adapters, state machines, packaging fit, idempotency, and official scenarios:

```powershell
# Run the complete test suite (250 tests)
pytest tests/

# Run the dedicated ShipEngine Sandbox adapter tests
pytest tests/test_shipengine_adapter.py

# Run the Smart Personalized Shipping Advisor tests
pytest tests/test_personalized_advisor.py

# Run the 4 official test cases (TC1 - TC4)
python run_official_scenarios.py

# Run the reproducible innovation benchmark matrix
python -m src.domain.benchmark
```

### Official Competition Scenarios Summary
- **[PASS] TC1: Same Parcel on Three Couriers** — Verified lowest cost ranking and itemized fee breakdown transparency.
- **[PASS] TC2: Large, Light Box (Volumetric Penalty)** — Verified courier-specific volumetric divisors (5000, 4500, 4000).
- **[PASS] TC3: Destination PIN Not Serviceable** — Verified explicit rejection of remote PIN (791111) without price corruption.
- **[PASS] TC4: Label Created, AWB Matched & Pickup Verified** — Verified 5-point post-dispatch audit and printable label output.

---

## 7. Repository Structure

```
adaptive-shipping-agent/
├── src/
│   ├── domain/
│   │   ├── advisor.py             # Smart Personalized Courier Advisor & Closest-Match Engine
│   │   ├── benchmark.py           # Reproducible 4-test baseline vs agent benchmark
│   │   ├── dimension_engine.py    # Dimension validation & courier size constraints
│   │   ├── evaluator.py           # 10-scenario evaluation suite
│   │   ├── freshness.py           # Quote timestamp currency & drift detector
│   │   ├── label_generator.py     # Printable PDF & HTML label generation (A6 & 4x6)
│   │   ├── models.py              # Pydantic schemas (Order, Address, CourierQuote, etc.)
│   │   ├── official_scenarios.py  # Official competition scenarios TC1 - TC4
│   │   ├── packaging.py           # 6-orientation orthogonal fit & custom box engine
│   │   ├── pricing.py             # Slabs, fuel surcharges, COD fee & tax calculations
│   │   ├── ranking.py             # Deterministic courier ranking & margin calculator
│   │   ├── reconciliation.py      # Idempotent dispatch & timeout recovery service
│   │   ├── stress_tester.py       # Decision stability & perturbation lab
│   │   ├── verification.py        # Independent 5-point shipment verification audit
│   │   ├── weight.py              # Volumetric vs dead weight breakdown
│   │   └── workflow.py            # 6-Stage explainable agent decision engine & trace
│   ├── persistence/
│   │   ├── audit.py               # Append-only audit logger
│   │   ├── database.py            # SQLite schema manager & connection pool
│   │   ├── idempotency.py         # Idempotency locks & timeout transition manager
│   │   └── state_machine.py       # Shipment lifecycle FSM & approval gate records
│   ├── providers/
│   │   ├── base.py                # CourierProvider abstract base class
│   │   ├── demo_adapter.py        # Simulated 10-courier provider with label generator
│   │   ├── registry.py            # Provider registry & factory (Demo vs ShipEngine)
│   │   └── shipengine_adapter.py  # Official ShipEngine Sandbox API integration
│   └── ui/
│       ├── app.py                 # Modern Streamlit logistics dashboard
│       └── helpers.py             # UI formatting & form parsing pure functions
├── tests/
│   ├── test_custom_packaging.py
│   ├── test_demo_adapter.py
│   ├── test_dimension_engine.py
│   ├── test_evaluator_scenarios.py
│   ├── test_freshness_and_reconciliation.py
│   ├── test_label_generator.py
│   ├── test_mandatory_dimensions.py
│   ├── test_official_scenarios.py
│   ├── test_packaging.py
│   ├── test_persistence.py
│   ├── test_personalized_advisor.py # 18 tests: Smart Advisor, preferences & closest match
│   ├── test_pricing.py
│   ├── test_ranking.py
│   ├── test_shipengine_adapter.py # 20 tests: ShipEngine Sandbox auth, rates, labels, tracking
│   ├── test_simplified_ui_flow.py
│   ├── test_stress_tester.py
│   ├── test_ui_helpers.py
│   ├── test_weight.py
│   └── test_workflow.py
├── run_official_scenarios.py      # Standalone runner for TC1 - TC4
├── run_demo.py                    # Standalone CLI demonstration
└── README.md                      # Comprehensive documentation
```
