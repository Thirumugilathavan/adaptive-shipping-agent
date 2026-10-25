# Adaptive Shipping Intelligence Agent
> **Autonomous, Constraint-Aware Logistics Decision Support & Execution Gate Engine**  
> *Category:* Business & Productivity AI — ZiroDigital Agentic AI Hackathon 2026  
> *Live Entrypoint:* `python app.py` (FastAPI backend + Modern HTML/CSS/JS frontend on `http://localhost:8000`)  
> *Test Suite:* `pytest tests/` (322/322 Passing) | `python run_official_scenarios.py` (4/4 Passing)

---

## 1. Executive Summary & Problem Fit

E-commerce merchants and warehouse logistics managers face substantial margin leakage from shipping inefficiencies:
1. **Volumetric Weight Surprises:** Bulky, lightweight items incur steep dimensional surcharges (up to 300% of base freight) because packaging is selected arbitrarily.
2. **Hidden Carrier Constraints:** Items exceeding courier dimension thresholds (e.g., length > 100 cm or girth > 300 cm) are accepted at dispatch but rejected or fined during terminal sorting.
3. **Ghost Bookings & Double-Billing:** Network timeouts during booking cause operators to blindly retry, creating duplicate waybills and unrecoverable freight charges.
4. **Stale Rates & Unserviceable Pincodes:** Price drift occurs when quotes age past validity windows, and remote pincodes cause booking rejections if serviceability isn't pre-validated.

The **Adaptive Shipping Intelligence Agent** solves these challenges by combining:
- **Autonomous 6-Orientation Packaging Optimization:** Evaluates 3D rotational fits against standard and user-defined custom boxes, calculating exact volumetric savings.
- **Local Multi-Courier Simulation & ML Intelligence:** 100% offline deterministic courier network with integrated Model 1 and Model 2 (CatBoost) machine-learning pricing and turnaround benchmarks.
- **Local Courier Sandbox & Manual Audit Entry:** Completely self-contained offline sandbox simulation and manual dashboard entry without any external network dependencies or API keys.
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

## 3. Shipping Provider Modes: Local Simulation & Offline Sandbox

The application operates 100% locally and offline without any external API calls, credentials, or network dependencies:

```mermaid
flowchart TD
    subgraph UI ["User Interface / Provider Selector"]
        M1["Mode 1: Local Demo (Default)"]
        M2["Mode 2: Local Courier Sandbox (Simulation)"]
        M3["Mode 3: Manual Sandbox Entry"]
    end

    subgraph Adapters ["Execution Adapters (100% Offline)"]
        DA["DemoShippingAdapter<br/>(10 Local Couriers, Offline)"]
        LSA["LocalSandboxShippingAdapter<br/>(Synthetic Sandbox Engine)"]
        MSA["Manual Entry Parser<br/>(Operator Dashboard Audit)"]
    end

    M1 --> DA
    M2 --> LSA
    M3 --> MSA

    DA --> ENG["6-Stage Agent Decision Engine<br/>(Model 1 + Model 2 + 3D Packaging + Approval Gate)"]
    LSA --> ENG
    MSA --> ENG
```

### Mode 1: Local Demo Mode (`demo` — Default)
- **Zero API credentials required:** 10 simulated couriers covering Air, Surface, Cargo, and Hyperlocal express delivery.
- **Realistic fee breakdowns:** Base freight slabs, fuel surcharges, COD handling fees, and GST tax calculations.
- **Evaluator Scenarios:** 10 one-click benchmark scenarios (`Demo A` through `Demo J`) and 4 official test cases (`TC1` through `TC4`).
- **Complete Offline Privacy:** Guaranteed zero external network traffic. Discloses quotes with `[DEMO DATA]` badges and synthetic `DEMO-TRK-` waybills.

### Mode 2: Local Courier Sandbox (Simulation) (`sandbox`)
- **Self-Contained Sandbox Simulation:** Fully offline sandbox test environment using deterministic synthetic carrier quotes and simulated lifecycle states.
- **Synthetic AWB & Label Output:** Generates clearly marked synthetic `SBX-TRK-` tracking numbers, `SBX-PKP-` pickup confirmations, and local PDF shipping labels with prominent sandbox watermarks.
- **Provenance Disclosure:** Tagged with `source_type: "LOCAL_SANDBOX"`, explicitly indicating simulated test data.
- **Safe & Predictable:** Requires zero API keys or tokens. Never calls external courier networks or creates live carrier commitments.

### Mode 3: Manual Sandbox Dashboard Entry (`manual_sandbox`)
- **Operator Dashboard Entry:** Allows operators to manually enter verified rates, fuel surcharges, tax amounts, and tracking references copied from external courier sandbox portals (e.g. Delhivery, Shiprocket, BlueDart, India Post).
- **Audit Labeling:** Explicitly labeled as **Manual Entry — Not API Verified** to ensure operators never mistake manually transcribed rates for real-time live carrier API quotes.
- **Integrated Decision Pipeline:** Once entered, manual quotes pass through the exact same 6-Stage decision engine, volumetric optimization, human approval gate, and 5-point verification checks.

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

## 5. Evaluator Quick-Start & Interactive Web Application

### Launch the Application
```powershell
# 1. Open PowerShell in project directory
cd "E:\adaptive shipping agent"

# 2. Launch the FastAPI server and HTML/CSS/JS frontend
python app.py
```
*The web interface will open automatically at `http://localhost:8000`.*

### Application Workflow Features
1. **Multi-Window 4-Step Shipping Wizard**:
   - **Step 1: Dimensions & Intake**: Origin/Destination PIN validation, dead weight, and 3D parcel dimensions.
   - **Step 2: Rates & ML Benchmark**: Deterministic courier rate comparison, 6-orientation box packaging recommendations, and statistical ML rate intelligence.
   - **Step 3: Approval Gate & Review**: Mandatory human confirmation gate with complete fee breakdowns and audit traces.
   - **Step 4: Dispatch & Label**: Real-time idempotency locking, instant PDF label generation, and 5-point verification.
2. **Dual-Model ML Intelligence**:
   - **Model 1 (Integrated)**: Domestic Courier Price Prediction model providing a non-binding statistical market baseline in INR, 90% confidence intervals, and quote discount/premium variance badges.
   - **Model 2 (Specification)**: CatBoost 500-tree regression model architecture (see `docs/MODEL2_INTEGRATION_SPEC.md`).
3. **Professional Dark Blue & White Theme**: Clean, high-contrast, accessible corporate design with zero emojis.

---

## 6. Verification & Test Suite

The test suite covers unit logic, provider adapters, state machines, packaging fit, idempotency, ML prediction, and official competition scenarios (100% offline):

```powershell
# Run the complete test suite (322 tests passing)
pytest tests/

# Run Provider Adapter & Local Sandbox tests (10 tests)
pytest tests/test_demo_adapter.py

# Run FastAPI webapp & provider mode API tests (9 tests)
pytest tests/test_webapp_api.py

# Run the dedicated Machine Learning Price Prediction tests
pytest tests/test_price_prediction.py

# Run the dedicated Model 2 Courier Intelligence tests
pytest tests/test_model2_adapter.py

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
│   │   ├── demo_adapter.py        # Simulated 10-courier & Local Sandbox provider
│   │   └── registry.py            # Offline provider registry (Demo, Sandbox, Manual)
│   ├── server.py                  # FastAPI server with offline provider mode management
│   └── ui/
│       ├── app.py                 # Logistics dashboard runner
│       └── helpers.py             # UI formatting, provider badges & pure helpers
├── static/                        # Frontend UI (HTML5, Modern CSS, Modular Vanilla JS)
│   ├── index.html                 # Modern single-page app with Provider controls
│   ├── css/style.css              # Dark Blue / White high-contrast corporate theme
│   └── js/app.js                  # Async client handling wizard, offline modes, live rates
├── tests/
│   ├── test_custom_packaging.py
│   ├── test_demo_adapter.py       # 10 tests: Provider contract & Local Sandbox execution
│   ├── test_dimension_engine.py
│   ├── test_evaluator_scenarios.py
│   ├── test_freshness_and_reconciliation.py
│   ├── test_label_generator.py
│   ├── test_mandatory_dimensions.py
│   ├── test_model2_adapter.py     # 10 tests: CatBoost rate, SLA & PIN coverage
│   ├── test_official_scenarios.py
│   ├── test_packaging.py
│   ├── test_persistence.py
│   ├── test_personalized_advisor.py # 18 tests: Smart Advisor, preferences & closest match
│   ├── test_pricing.py
│   ├── test_ranking.py
│   ├── test_serviceability_and_explanations.py # 10 tests: deterministic serviceability
│   ├── test_simplified_ui_flow.py
│   ├── test_stress_tester.py
│   ├── test_ui_helpers.py
│   ├── test_webapp_api.py         # 9 tests: FastAPI REST endpoints & provider mode switching
│   ├── test_weight.py
│   └── test_workflow.py
├── run_official_scenarios.py      # Standalone runner for TC1 - TC4
├── run_demo.py                    # Standalone CLI demonstration
├── app.py                         # Application entry point (FastAPI server)
└── README.md                      # Comprehensive documentation
```
