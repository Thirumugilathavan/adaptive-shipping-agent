
// -----------------------------------------------------------------------------
// Shared Indian Rupee (INR) Formatting & Price Extraction Helpers
// -----------------------------------------------------------------------------

function formatINR(val, fallback = 'Unavailable') {
  if (val === null || val === undefined || val === '') {
    return fallback;
  }
  const num = typeof val === 'number' ? val : Number(val);
  if (!Number.isFinite(num) || num < 0) {
    return fallback;
  }
  return `₹${num.toFixed(2)}`;
}

function formatINRSigned(val, fallback = 'Unavailable') {
  if (val === null || val === undefined || val === '') {
    return fallback;
  }
  const num = typeof val === 'number' ? val : Number(val);
  if (!Number.isFinite(num)) {
    return fallback;
  }
  const sign = num > 0 ? '+' : (num < 0 ? '-' : '');
  return `${sign}₹${Math.abs(num).toFixed(2)}`;
}

function extractQuotePrice(q) {
  if (!q) return null;
  if (typeof q.total_cost === 'number' && Number.isFinite(q.total_cost) && q.total_cost >= 0) {
    return q.total_cost;
  }
  if (q.cost_breakdown && typeof q.cost_breakdown.total_cost === 'number' && Number.isFinite(q.cost_breakdown.total_cost) && q.cost_breakdown.total_cost >= 0) {
    return q.cost_breakdown.total_cost;
  }
  if (q.quote && q.quote.cost_breakdown && typeof q.quote.cost_breakdown.total_cost === 'number' && Number.isFinite(q.quote.cost_breakdown.total_cost) && q.quote.cost_breakdown.total_cost >= 0) {
    return q.quote.cost_breakdown.total_cost;
  }
  if (q.quote && typeof q.quote.total_cost === 'number' && Number.isFinite(q.quote.total_cost) && q.quote.total_cost >= 0) {
    return q.quote.total_cost;
  }
  return null;
}

/**
 * Adaptive Shipping Intelligence Agent — Interactive Frontend Application.
 * Communicates with FastAPI REST backend for rates, 10-scenario tests,
 * human approval gate, dispatch, label generation, and audit trail.
 * 
 * Features:
 * - High-contrast Dark Blue & White design palette
 * - Step-by-Step Multi-Window Wizard transitions (Auto-advancing on stage completion)
 * - Zero emojis across all interface elements, notifications, and labels
 */

// Global Application State
const state = {
  currentWizardStep: 1,
  maxCompletedStep: 1,
  activeOrder: null,
  activeQuotes: [],
  selectedQuoteId: null,
  rankingResult: null,
  advisorResult: null,
  lastDispatchResult: null,
  providerMode: 'demo',
  scenarios: [],
  officialTestCases: [],
  testRuns: [],
};

// DOM Elements
const elements = {
  tabButtons: document.querySelectorAll('.sidebar-nav-item[data-tab], .tab-btn'),
  sidebarNavItems: document.querySelectorAll('.sidebar-nav-item'),
  sidebarSubItems: document.querySelectorAll('.sidebar-sub-item'),
  sidebarToggle: document.getElementById('sidebarToggle'),
  appSidebar: document.getElementById('appSidebar'),
  sidebarCloseBtn: document.getElementById('sidebarCloseBtn'),
  sidebarOverlay: document.getElementById('sidebarOverlay'),
  sidebarGroupRates: document.getElementById('sidebarGroupRates'),
  chevronRates: document.getElementById('chevronRates'),
  navItemRates: document.getElementById('navItemRates'),
  navItemEvaluator: document.getElementById('navItemEvaluator'),
  navItemPackaging: document.getElementById('navItemPackaging'),
  navItemAudit: document.getElementById('navItemAudit'),
  tabPanes: document.querySelectorAll('.tab-pane'),
  providerSelect: document.getElementById('providerModeSelect'),
    providerModeBanner: document.getElementById('providerModeBanner'),
  providerBannerContent: document.getElementById('providerBannerContent'),
  providerBannerIcon: document.getElementById('providerBannerIcon'),
  providerBannerText: document.getElementById('providerBannerText'),
  providerBannerActions: document.getElementById('providerBannerActions'),
  modalFieldProvenance: document.getElementById('modalFieldProvenance'),
  modalProvCloseBtn: document.getElementById('modalProvCloseBtn'),
  modalProvTitle: document.getElementById('modalProvTitle'),
  modalProvSubtitle: document.getElementById('modalProvSubtitle'),
  modalProvTableBody: document.getElementById('modalProvTableBody'),
  modalProvRawBox: document.getElementById('modalProvRawBox'),
  modalProvRawPre: document.getElementById('modalProvRawPre'),
  advProvenanceBox: document.getElementById('advProvenanceBox'),
  advProvBadge: document.getElementById('advProvBadge'),
  advProvDetails: document.getElementById('advProvDetails'),

  // PIN validation feedback
  senderPinFeedback: document.getElementById('senderPinFeedback'),
  recipientPinFeedback: document.getElementById('recipientPinFeedback'),

  // Section A: Recommended Courier Card
  advServiceTypeBadge: document.getElementById('advServiceTypeBadge'),
  recSourceFlag: document.getElementById('recSourceFlag'),
  recBillableWeight: document.getElementById('recBillableWeight'),
  recVolumetricWeight: document.getElementById('recVolumetricWeight'),
  recRouteStatus: document.getElementById('recRouteStatus'),
  recTurnaroundTime: document.getElementById('recTurnaroundTime'),
  recDisclaimerText: document.getElementById('recDisclaimerText'),

  // Section B: Why Recommended & Alternative Explorer
  whyRecommendedCard: document.getElementById('whyRecommendedCard'),
  btnToggleAltRecommendations: document.getElementById('btnToggleAltRecommendations'),
  altRecommendationsPanel: document.getElementById('altRecommendationsPanel'),
  btnCloseAltPanel: document.getElementById('btnCloseAltPanel'),
  selPreferredCourier: document.getElementById('selPreferredCourier'),
  btnApplyAltPreference: document.getElementById('btnApplyAltPreference'),
  altRecResultContainer: document.getElementById('altRecResultContainer'),
  altResultNotice: document.getElementById('altResultNotice'),
  altCandidatesList: document.getElementById('altCandidatesList'),

  // Stepper & Wizard Panels
  wizardStepper: document.getElementById('wizardStepper'),
  stepIndicators: [
    document.getElementById('stepIndicator1'),
    document.getElementById('stepIndicator2'),
    document.getElementById('stepIndicator3'),
    document.getElementById('stepIndicator4'),
  ],
  connectors: [
    document.getElementById('connector1'),
    document.getElementById('connector2'),
    document.getElementById('connector3'),
  ],
  wizardPanels: [
    document.getElementById('wizardStep1'),
    document.getElementById('wizardStep2'),
    document.getElementById('wizardStep3'),
    document.getElementById('wizardStep4'),
  ],

  // Step 1 Form
  shippingForm: document.getElementById('shippingForm'),
  btnResetForm: document.getElementById('btnResetForm'),
  btnFindCouriers: document.getElementById('btnFindCouriers'),
  dimLength: document.getElementById('dimLength'),
  dimWidth: document.getElementById('dimWidth'),
  dimHeight: document.getElementById('dimHeight'),
  dimWeight: document.getElementById('dimWeight'),
  paymentMode: document.getElementById('paymentMode'),
  codGroup: document.getElementById('codGroup'),
  codAmount: document.getElementById('codAmount'),
  valVolume: document.getElementById('valVolume'),
  valVolWeight: document.getElementById('valVolWeight'),
  valBillWeight: document.getElementById('valBillWeight'),

  // Step 2 Rate Selection
  step2SummaryStrip: document.getElementById('step2SummaryStrip'),
  unserviceableRouteAlert: document.getElementById('unserviceableRouteAlert'),
  unserviceableAlertTitle: document.getElementById('unserviceableAlertTitle'),
  unserviceableAlertMsg: document.getElementById('unserviceableAlertMsg'),
  btnBackToStep1: document.getElementById('btnBackToStep1'),
  btnNavBackTo1: document.getElementById('btnNavBackTo1'),
  btnProceedToApproval: document.getElementById('btnProceedToApproval'),
  advisorCard: document.getElementById('advisorCard'),
  advCourierName: document.getElementById('advCourierName'),
  advWinningCost: document.getElementById('advWinningCost'),
  advWinningSla: document.getElementById('advWinningSla'),
  advExplanation: document.getElementById('advExplanation'),
  advWinnerReasonsBox: document.getElementById('advWinnerReasonsBox'),
  advWinnerReasonsList: document.getElementById('advWinnerReasonsList'),
  advTradeoffBox: document.getElementById('advTradeoffBox'),
  advTradeoffText: document.getElementById('advTradeoffText'),
  advTradeoffList: document.getElementById('advTradeoffList'),
  rateCountBadge: document.getElementById('rateCountBadge'),
  courierCardsList: document.getElementById('courierCardsList'),
  comparisonTableBody: document.getElementById('comparisonTableBody'),
  lossReasonsSection: document.getElementById('lossReasonsSection'),
  lossReasonsList: document.getElementById('lossReasonsList'),

  // Step 3 Approval Gate
  btnNavBackTo2: document.getElementById('btnNavBackTo2'),
  approvalSummary: document.getElementById('approvalSummary'),
  chkOperatorConfirm: document.getElementById('chkOperatorConfirm'),
  btnApproveDispatch: document.getElementById('btnApproveDispatch'),

  // Step 4 Shipment Ready & Label
  shipmentReadyPanel: document.getElementById('shipmentReadyPanel'),
  readyAwb: document.getElementById('readyAwb'),
  readyShipmentId: document.getElementById('readyShipmentId'),
  readyCarrier: document.getElementById('readyCarrier'),
  readyPickup: document.getElementById('readyPickup'),
  readyVerification: document.getElementById('readyVerification'),
  labelPreviewContent: document.getElementById('labelPreviewContent'),
  btnDownloadLabelPdf: document.getElementById('btnDownloadLabelPdf'),
  btnPrintLabel: document.getElementById('btnPrintLabel'),
  btnExpandLabel: document.getElementById('btnExpandLabel'),
  btnStartNewShipment: document.getElementById('btnStartNewShipment'),

  // Evaluator & Official Test Cases
  officialTcGrid: document.getElementById('officialTcGrid'),
  btnRunAllOfficialTc: document.getElementById('btnRunAllOfficialTc'),
  btnResetOfficialTc: document.getElementById('btnResetOfficialTc'),
  officialMetricPassed: document.getElementById('officialMetricPassed'),
  officialMetricFailed: document.getElementById('officialMetricFailed'),
  officialMetricNotRun: document.getElementById('officialMetricNotRun'),
  officialMetricAudit: document.getElementById('officialMetricAudit'),

  scenariosGrid: document.getElementById('scenariosGrid'),
  btnRunAllScenarios: document.getElementById('btnRunAllScenarios'),
  btnClearTestRuns: document.getElementById('btnClearTestRuns'),
  testRunsCountBadge: document.getElementById('testRunsCountBadge'),
  testRunsTableBody: document.getElementById('testRunsTableBody'),

  // Packaging
  customPkgForm: document.getElementById('customPkgForm'),
  customPackagesList: document.getElementById('customPackagesList'),

  // Audit
  btnRefreshAudit: document.getElementById('btnRefreshAudit'),
  auditTableBody: document.getElementById('auditTableBody'),

  // Modal
  appModal: document.getElementById('appModal'),
  modalTitle: document.getElementById('modalTitle'),
  modalBody: document.getElementById('modalBody'),
  modalCloseBtn: document.getElementById('modalCloseBtn'),

  // ML Rate Intelligence
  mlPredictionCard: document.getElementById('mlPredictionCard'),
  mlModelSelect: document.getElementById('mlModelSelect'),
  mlPredictedPrice: document.getElementById('mlPredictedPrice'),
  mlSelectedCourierPrice: document.getElementById('mlSelectedCourierPrice'),
  mlSelectedCourierName: document.getElementById('mlSelectedCourierName'),
  mlStatusBadge: document.getElementById('mlStatusBadge'),
  mlBenchmarkExplanation: document.getElementById('mlBenchmarkExplanation'),
  mlConfidenceInterval: document.getElementById('mlConfidenceInterval'),
  mlVarianceBadge: document.getElementById('mlVarianceBadge'),
  mlVarianceDesc: document.getElementById('mlVarianceDesc'),
  mlAdviceText: document.getElementById('mlAdviceText'),
  mlRecommendedAction: document.getElementById('mlRecommendedAction'),
  mlMetaModel: document.getElementById('mlMetaModel'),
  mlMetaVersion: document.getElementById('mlMetaVersion'),
  mlMetaSchema: document.getElementById('mlMetaSchema'),
  btnToggleMlDetails: document.getElementById('btnToggleMlDetails'),
  mlLimitationsDrawer: document.getElementById('mlLimitationsDrawer'),
  mlLimitationsList: document.getElementById('mlLimitationsList'),
};

// -----------------------------------------------------------------------------
// Initialization
// -----------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
  setupSidebarNavigation();
  setupWizard();
  setupLiveMeasurements();
  setupFormHandlers();
  setupApprovalGate();
  setupEvaluatorTab();
  setupPackagingTab();
  setupAuditTab();
  setupProviderControls();
  setupModal();
  setupMlPrediction();
  setupAlternativeRecommendations();
  setupPinLiveValidation();
  updateLiveVolumetric();
});

// -----------------------------------------------------------------------------
// Step-by-Step Wizard Navigation
// -----------------------------------------------------------------------------

function setupWizard() {
  // Stepper Header click navigation
  elements.stepIndicators.forEach((indicator, index) => {
    if (!indicator) return;
    indicator.addEventListener('click', () => {
      const targetStep = index + 1;
      // Allow jumping to already completed steps or current step
      if (targetStep <= state.maxCompletedStep) {
        goToWizardStep(targetStep);
      }
    });
  });

  // Step 2 Back & Continue buttons
  if (elements.btnBackToStep1) {
    elements.btnBackToStep1.addEventListener('click', () => goToWizardStep(1));
  }
  if (elements.btnNavBackTo1) {
    elements.btnNavBackTo1.addEventListener('click', () => goToWizardStep(1));
  }
  if (elements.btnProceedToApproval) {
    elements.btnProceedToApproval.addEventListener('click', () => {
      if (state.selectedQuoteId) {
        goToWizardStep(3);
      }
    });
  }

  // Step 3 Back button
  if (elements.btnNavBackTo2) {
    elements.btnNavBackTo2.addEventListener('click', () => goToWizardStep(2));
  }

  // Step 4 New Shipment button
  if (elements.btnStartNewShipment) {
    elements.btnStartNewShipment.addEventListener('click', () => {
      elements.shippingForm.reset();
      state.activeOrder = null;
      state.activeQuotes = [];
      state.selectedQuoteId = null;
      state.maxCompletedStep = 1;
      updateLiveVolumetric();
      goToWizardStep(1);
    });
  }
}

function goToWizardStep(stepNumber) {
  const step = Math.max(1, Math.min(4, stepNumber));
  state.currentWizardStep = step;
  state.maxCompletedStep = Math.max(state.maxCompletedStep, step);

  // Update Stepper Indicators
  elements.stepIndicators.forEach((ind, i) => {
    if (!ind) return;
    const stepIdx = i + 1;
    ind.classList.remove('active', 'completed');
    if (stepIdx === step) {
      ind.classList.add('active');
    } else if (stepIdx < step) {
      ind.classList.add('completed');
    }
  });

  // Update Connecting Lines
  elements.connectors.forEach((conn, i) => {
    if (!conn) return;
    conn.classList.toggle('active', i + 1 < step);
  });

  // Show only active panel, hide previous panels
  elements.wizardPanels.forEach((panel, i) => {
    if (!panel) return;
    panel.classList.toggle('active', i + 1 === step);
  });

  // Update active state in Left Navigation Sidebar
  updateSidebarSubItemActive(step);

  // Scroll smoothly to top of wizard container
  const container = document.querySelector('.wizard-container');
  if (container) {
    container.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

// -----------------------------------------------------------------------------
// Tab Switching
// -----------------------------------------------------------------------------

function updateSidebarSubItemActive(stepNumber) {
  const subItems = document.querySelectorAll('.sidebar-sub-item');
  subItems.forEach(s => {
    const sStep = parseInt(s.getAttribute('data-step'), 10);
    s.classList.toggle('active', sStep === stepNumber);
  });
  const navItemRates = document.getElementById('navItemRates');
  const tabRates = document.getElementById('tab-rates');
  if (navItemRates && tabRates && tabRates.classList.contains('active')) {
    navItemRates.classList.add('active');
  }
}

function setupSidebarNavigation() {
  const sidebarNavItems = document.querySelectorAll('.sidebar-nav-item');
  const sidebarSubItems = document.querySelectorAll('.sidebar-sub-item');
  const sidebarGroupRates = document.getElementById('sidebarGroupRates');
  const chevronRates = document.getElementById('chevronRates');
  const sidebarToggle = document.getElementById('sidebarToggle');
  const appSidebar = document.getElementById('appSidebar');
  const sidebarCloseBtn = document.getElementById('sidebarCloseBtn');
  const sidebarOverlay = document.getElementById('sidebarOverlay');

  function closeMobileSidebar() {
    if (appSidebar) appSidebar.classList.remove('open');
    if (sidebarOverlay) sidebarOverlay.classList.remove('open');
  }

  // Mobile toggle and overlay listeners
  if (sidebarToggle && appSidebar && sidebarOverlay) {
    sidebarToggle.addEventListener('click', () => {
      appSidebar.classList.toggle('open');
      sidebarOverlay.classList.toggle('open');
    });
  }
  if (sidebarCloseBtn) sidebarCloseBtn.addEventListener('click', closeMobileSidebar);
  if (sidebarOverlay) sidebarOverlay.addEventListener('click', closeMobileSidebar);

  // Chevron click on Rate Shipping Dispatch expands/collapses subsections
  if (chevronRates && sidebarGroupRates) {
    chevronRates.addEventListener('click', (e) => {
      e.stopPropagation();
      sidebarGroupRates.classList.toggle('expanded');
      const isExp = sidebarGroupRates.classList.contains('expanded');
      const navItemRates = document.getElementById('navItemRates');
      if (navItemRates) navItemRates.setAttribute('aria-expanded', isExp ? 'true' : 'false');
    });
  }

  // Primary navigation items (Rate Shipping Dispatch, Official Tests, Packaging, Audit)
  sidebarNavItems.forEach(item => {
    item.addEventListener('click', () => {
      const tabId = item.getAttribute('data-tab');
      if (!tabId) return;

      sidebarNavItems.forEach(n => n.classList.remove('active'));
      item.classList.add('active');

      elements.tabPanes.forEach(p => p.classList.remove('active'));
      const targetPane = document.getElementById(tabId);
      if (targetPane) targetPane.classList.add('active');

      if (tabId === 'tab-rates') {
        if (sidebarGroupRates) sidebarGroupRates.classList.add('expanded');
        updateSidebarSubItemActive(state.currentWizardStep || 1);
      } else {
        sidebarSubItems.forEach(s => s.classList.remove('active'));
        if (tabId === 'tab-evaluator') {
          loadOfficialScenarios();
          loadScenarios();
          loadTestRuns();
        } else if (tabId === 'tab-packaging') {
          loadCustomPackages();
        } else if (tabId === 'tab-audit') {
          loadAuditLogs();
        }
      }

      closeMobileSidebar();
    });
  });

  // 4 Nested Subsections under Rate Shipping Dispatch
  sidebarSubItems.forEach(subItem => {
    subItem.addEventListener('click', () => {
      const step = parseInt(subItem.getAttribute('data-step'), 10);
      if (isNaN(step)) return;

      // Ensure tab-rates pane is active
      const navItemRates = document.getElementById('navItemRates');
      if (navItemRates) {
        sidebarNavItems.forEach(n => n.classList.remove('active'));
        navItemRates.classList.add('active');
      }
      elements.tabPanes.forEach(p => p.classList.remove('active'));
      const tabRates = document.getElementById('tab-rates');
      if (tabRates) tabRates.classList.add('active');
      if (sidebarGroupRates) sidebarGroupRates.classList.add('expanded');

      // Navigate to requested step
      if (step <= state.maxCompletedStep) {
        goToWizardStep(step);
      } else if (step === 2 && !state.activeQuotes.length) {
        if (elements.shippingForm.requestSubmit) {
          elements.shippingForm.requestSubmit();
        } else if (elements.btnFindCouriers) {
          elements.btnFindCouriers.click();
        }
      } else if (step === 3 && state.selectedQuoteId) {
        goToWizardStep(3);
      }

      closeMobileSidebar();
    });
  });
}

// -----------------------------------------------------------------------------
// Live Volumetric Calculation
// -----------------------------------------------------------------------------

function setupLiveMeasurements() {
  [elements.dimLength, elements.dimWidth, elements.dimHeight, elements.dimWeight].forEach(input => {
    if (input) input.addEventListener('input', updateLiveVolumetric);
  });

  elements.paymentMode.addEventListener('change', (e) => {
    if (e.target.value === 'cod') {
      elements.codGroup.classList.remove('hidden');
    } else {
      elements.codGroup.classList.add('hidden');
    }
  });

  elements.btnResetForm.addEventListener('click', () => {
    elements.shippingForm.reset();
    elements.codGroup.classList.add('hidden');
    updateLiveVolumetric();
    state.maxCompletedStep = 1;
    goToWizardStep(1);
  });
}

function updateLiveVolumetric() {
  const l = parseFloat(elements.dimLength.value) || 0;
  const w = parseFloat(elements.dimWidth.value) || 0;
  const h = parseFloat(elements.dimHeight.value) || 0;
  const weight = parseFloat(elements.dimWeight.value) || 0;

  const volume = l * w * h;
  const volWeight = volume / 5000.0;
  const billable = Math.max(weight, volWeight);

  elements.valVolume.textContent = `${volume.toLocaleString()} cm3`;
  elements.valVolWeight.textContent = `${volWeight.toFixed(2)} kg`;
  elements.valBillWeight.textContent = `${billable.toFixed(2)} kg`;
}

// -----------------------------------------------------------------------------
// Rate Shopping Form Submission & Stage 1 -> Stage 2 Auto-Advance
// -----------------------------------------------------------------------------

function setupFormHandlers() {
  elements.shippingForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    await fetchAndRenderRates();
  });
}

async function fetchAndRenderRates() {
  const payload = {
    sender_name: document.getElementById('senderName').value,
    sender_pin: document.getElementById('senderPin').value,
    sender_city: 'New Delhi',
    sender_state: 'Delhi',
    sender_phone: '9876543210',

    recipient_name: document.getElementById('recipientName').value,
    recipient_pin: document.getElementById('recipientPin').value,
    recipient_city: 'Mumbai',
    recipient_state: 'Maharashtra',
    recipient_phone: '9123456789',

    length_cm: parseFloat(elements.dimLength.value),
    width_cm: parseFloat(elements.dimWidth.value),
    height_cm: parseFloat(elements.dimHeight.value),
    parcel_weight_kg: parseFloat(elements.dimWeight.value),

    package_type: document.getElementById('packageType').value,
    is_cod: elements.paymentMode.value === 'cod',
    cod_amount: elements.paymentMode.value === 'cod' ? parseFloat(elements.codAmount.value) : 0.0,

    ranking_mode: document.getElementById('rankingMode').value,
    provider_mode: state.providerMode,

    max_budget: parseFloat(document.getElementById('prefBudget').value) || null,
    max_delivery_days: parseInt(document.getElementById('prefDays').value) || null,
    priority: 'balanced',
  };

  elements.btnFindCouriers.disabled = true;
  elements.btnFindCouriers.textContent = 'Calculating Rates & Slabs...';

  try {
    const res = await fetch('/api/rates', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    const data = await res.json();
    if (!res.ok) {
      const errDetail = data.detail;
      const errMsg = errDetail?.errors ? errDetail.errors.join('\n') : (data.detail || 'Validation failed');
      alert(`Error:\n${errMsg}`);
      return;
    }

    renderRateResults(data);

    // AUTOMATIC ADVANCE: Stage 1 -> Stage 2 (Carrier rate selection)
    goToWizardStep(2);
  } catch (err) {
    alert(`Failed to calculate rates: ${err.message}`);
  } finally {
    elements.btnFindCouriers.disabled = false;
    elements.btnFindCouriers.textContent = 'Find & Compare Couriers';
  }
}

function renderRateResults(data) {
  state.activeOrder = data.order;
  state.activeQuotes = data.quotes || [];
  state.rankingResult = data.ranking;
  state.advisorResult = data.advisor;

  // 1. Render Step 2 Quick Summary Strip
  if (data.order) {
    const o = data.order;
    const vol = (o.dimensions.length_cm * o.dimensions.width_cm * o.dimensions.height_cm) / 5000.0;
    const billable = Math.max(o.parcel_weight_kg, vol);
    elements.step2SummaryStrip.innerHTML = `
      <span>Route: <strong>PIN ${o.sender.pin_code} to PIN ${o.recipient.pin_code}</strong></span>
      <span>Dimensions: <strong>${o.dimensions.length_cm} x ${o.dimensions.width_cm} x ${o.dimensions.height_cm} cm</strong></span>
      <span>Dead Weight: <strong>${o.parcel_weight_kg.toFixed(2)} kg</strong></span>
      <span>Billable Weight: <strong>${billable.toFixed(2)} kg</strong></span>
      <span>Payment: <strong>${o.is_cod ? 'COD (INR ' + o.cod_amount.toFixed(2) + ')' : 'Prepaid'}</strong></span>
    `;
  }

  const hasEligible = Boolean(data.has_eligible_couriers && data.selected_quote_id);

  // 2. Serviceability Banner & Advisor Recommendation Handling
  if (!hasEligible) {
    // Show prominent unserviceable / no-eligible-courier alert
    if (elements.unserviceableRouteAlert) {
      elements.unserviceableRouteAlert.classList.remove('hidden');
      if (elements.unserviceableAlertTitle) {
        elements.unserviceableAlertTitle.textContent = 'Destination Unserviceable / No Eligible Couriers';
      }
      if (elements.unserviceableAlertMsg) {
        elements.unserviceableAlertMsg.textContent =
          (data.ranking && data.ranking.decision_explanation) ||
          (data.advisor && data.advisor.explanation) ||
          'All evaluated couriers are unserviceable or excluded by operational constraints for this route.';
      }
    }
    if (elements.advisorCard) {
      elements.advisorCard.classList.add('hidden');
    }
    if (elements.whyRecommendedCard) {
      elements.whyRecommendedCard.classList.add('hidden');
    }
    state.selectedQuoteId = null;
  } else {
    // Route is serviceable and has eligible winner
    if (elements.unserviceableRouteAlert) {
      elements.unserviceableRouteAlert.classList.add('hidden');
    }
    if (elements.advisorCard) {
      elements.advisorCard.classList.remove('hidden');
    }

    const rec = (data.advisor && data.advisor.primary_recommendation) ||
                (data.advisor && data.advisor.recommended_quote) ||
                (data.ranking && data.ranking.selected_winner && data.ranking.selected_winner.quote);

    if (rec) {
      if (elements.advCourierName) {
        elements.advCourierName.textContent = rec.courier_name;
      }
      if (elements.advServiceTypeBadge) {
        elements.advServiceTypeBadge.textContent = rec.service_type || 'Standard Service';
      }
      if (elements.advWinningCost) {
        const costVal = extractQuotePrice(rec);
        elements.advWinningCost.textContent = formatINR(costVal);
      }
      if (elements.advWinningSla) {
        elements.advWinningSla.textContent = `${rec.estimated_delivery_days || '--'} Days SLA`;
      }
      if (elements.recTurnaroundTime) {
        elements.recTurnaroundTime.textContent = `${rec.estimated_delivery_days || '--'} Business Days`;
      }
      if (elements.recBillableWeight) {
        const bWt = rec.weight_result?.chargeable_weight_kg || rec.weight_result?.billing_slab_kg || (data.chargeable_weight_estimated || data.actual_weight_kg);
        elements.recBillableWeight.textContent = `${Number(bWt).toFixed(2)} kg`;
      }
      if (elements.recVolumetricWeight) {
        const vWt = rec.weight_result?.volumetric_weight_kg || data.volumetric_weight_5000 || 0;
        elements.recVolumetricWeight.textContent = `${Number(vWt).toFixed(2)} kg`;
      }
      if (elements.recRouteStatus) {
        elements.recRouteStatus.textContent = 'Serviceable';
      }
      if (elements.recSourceFlag) {
        elements.recSourceFlag.textContent = rec.source_type === 'LOCAL_SANDBOX' ? 'LOCAL SANDBOX' : 'SIMULATED DATA';
      }
      if (elements.whyRecommendedCard) {
        elements.whyRecommendedCard.classList.remove('hidden');
      }
      if (elements.advExplanation) {
        elements.advExplanation.textContent =
          (data.advisor && data.advisor.explanation) ||
          (data.ranking && data.ranking.decision_explanation) ||
          'Selected as best candidate based on your shipping criteria.';
      }

      // Winner Reasons
      const winnerReasons = data.winner_reasons || (data.ranking && data.ranking.winner_reasons) || [];
      if (elements.advWinnerReasonsBox && elements.advWinnerReasonsList) {
        if (winnerReasons.length > 0) {
          elements.advWinnerReasonsBox.classList.remove('hidden');
          elements.advWinnerReasonsList.innerHTML = winnerReasons.map(r => `<li>${r}</li>`).join('');
        } else {
          elements.advWinnerReasonsBox.classList.remove('hidden');
          elements.advWinnerReasonsList.innerHTML = '<li>Satisfies operational SLA and provides lowest landed freight cost.</li>';
        }
      }

      // Trade-off Analysis
      const tradeOffText = data.trade_off_analysis || (data.ranking && data.ranking.trade_off_analysis) || '';
      const tradeoffItems = (data.advisor && data.advisor.tradeoffs) || [];
      if (elements.advTradeoffBox) {
        if (tradeOffText || tradeoffItems.length > 0) {
          elements.advTradeoffBox.classList.remove('hidden');
          if (elements.advTradeoffText) {
            elements.advTradeoffText.textContent = tradeOffText;
          }
          if (elements.advTradeoffList) {
            elements.advTradeoffList.innerHTML = tradeoffItems.map(t => `<li>${t}</li>`).join('');
          }
        } else {
          elements.advTradeoffBox.classList.add('hidden');
        }
      }

      state.selectedQuoteId = data.selected_quote_id || rec.quote_id;
    }

    if (elements.advProvenanceBox) {
      const dp = (data.ranking && data.ranking.decision_provenance) || (data.decision_provenance);
      if (dp && dp.winner_quote_id) {
        elements.advProvenanceBox.classList.remove('hidden');
        if (elements.advProvBadge) {
          elements.advProvBadge.textContent = dp.source_type || 'PROVENANCE VERIFIED';
        }
        if (elements.advProvDetails) {
          elements.advProvDetails.innerHTML = `
            <div>Winning Quote ID: <strong style="color:var(--primary); font-family:monospace;">${dp.winner_quote_id}</strong> (${dp.winner_courier_name})</div>
            <div>Source: <strong>${dp.source_identifier || dp.source_type}</strong> &bull; Endpoint / URL: <span style="font-family:monospace; background:#e2e8f0; padding:1px 4px; border-radius:3px;">${dp.source_url_or_endpoint || 'local://mock'}</span></div>
            <div>Extracted At: <strong>${dp.retrieval_timestamp}</strong> via <strong>${dp.retrieval_method}</strong></div>
            <div style="margin-top:4px;"><a href="javascript:void(0)" onclick="viewFieldProvenance('${dp.winner_quote_id}', event)" style="color:var(--primary); font-weight:600;">Inspect Full Field-Level Provenance & Verification Audit &rarr;</a></div>
          `;
        }
      } else {
        elements.advProvenanceBox.classList.add('hidden');
      }
    }
  }

  // 3. Render Courier Cards
  if (elements.rateCountBadge) {
    elements.rateCountBadge.textContent = `${data.quotes.length} Couriers Evaluated`;
  }
  if (elements.courierCardsList) {
    elements.courierCardsList.innerHTML = '';

    data.quotes.forEach(quote => {
      const isSelected = quote.quote_id === state.selectedQuoteId;
      const isWinner = data.ranking?.selected_winner?.quote?.quote_id === quote.quote_id;
      const isSvc = (quote.is_serviceable !== false) && (quote.is_eligible !== false);

      const card = document.createElement('div');
      card.className = `courier-card ${isSelected ? 'selected' : ''} ${!isSvc ? 'unserviceable' : ''}`;
      card.setAttribute('data-id', quote.quote_id);

      const costStr = formatINR(extractQuotePrice(quote));
      const slabKg = quote.weight_result ? `${quote.weight_result.billing_slab_kg.toFixed(2)} kg` : '0.50 kg';

      card.innerHTML = `
        <div class="courier-card-header">
          <div class="courier-info">
            <div class="courier-name-row">
              <span class="courier-name">${quote.courier_name}</span>
              ${isWinner ? '<span class="badge-recommendation">Recommended Winner</span>' : ''}
              ${quote.source_type === 'LOCAL_SANDBOX' ? '<span class="badge-demo-flag" style="font-size:10px; padding:1px 6px; background:#eff6ff; color:#1d4ed8; border-color:#bfdbfe;">LOCAL SANDBOX</span>' : (quote.source_type === 'MANUAL_SANDBOX' ? '<span class="badge-demo-flag" style="font-size:10px; padding:1px 6px; background:#fef3c7; color:#92400e; border-color:#fde68a;">MANUAL ENTRY — NOT API VERIFIED</span>' : '<span class="badge-demo-flag" style="font-size:10px; padding:1px 6px;">SIMULATED</span>')}
              <span class="courier-mode-badge">${quote.service_type}</span>
            </div>
            <div class="courier-meta">
              <span>Estimated Delivery: <strong>${quote.estimated_delivery_days || 3} Business Days</strong></span>
              <span>Billing Slab: <strong>${slabKg}</strong></span>
              ${!isSvc ? '<span class="text-danger" style="font-weight:700;">Unserviceable Route / Excluded</span>' : ''}
            </div>
            <div class="courier-provenance-strip" style="font-size: 11px; color: var(--text-muted); margin-top: 4px; display: flex; gap: 8px; flex-wrap: wrap; align-items: center;">
              <span>Source: <strong style="color: var(--text-main);">${quote.source_id || quote.source_type}</strong></span>
              <span>Retrieved: <strong>${quote.retrieval_timestamp ? quote.retrieval_timestamp.split('T')[0] + ' ' + (quote.retrieval_timestamp.split('T')[1] || '').substring(0,8) + ' UTC' : 'N/A'}</strong></span>
              <span class="provenance-toggle" style="color: var(--primary); cursor: pointer; text-decoration: underline; font-weight: 600;" onclick="viewFieldProvenance('${quote.quote_id}', event)">[View Field Provenance]</span>
            </div>
          </div>
          <div class="courier-pricing">
            <div class="total-cost">${costStr}</div>
            ${quote.cost_breakdown ? `<span class="breakdown-toggle" onclick="toggleBreakdown('${quote.quote_id}', event)">View Fee Breakdown</span>` : ''}
          </div>
        </div>
      `;

      // Fee breakdown drawer
      if (quote.cost_breakdown) {
        const drawer = document.createElement('div');
        drawer.className = 'breakdown-drawer hidden';
        drawer.id = `drawer-${quote.quote_id}`;
        drawer.innerHTML = `
          <div class="breakdown-row"><span>Base Freight:</span> <strong>INR ${quote.cost_breakdown.base_freight.toFixed(2)}</strong></div>
          <div class="breakdown-row"><span>Fuel Surcharge (${quote.cost_breakdown.fuel_surcharge_rate_pct}%):</span> <strong>INR ${quote.cost_breakdown.fuel_surcharge.toFixed(2)}</strong></div>
          <div class="breakdown-row"><span>COD Handling Fee:</span> <strong>INR ${quote.cost_breakdown.cod_fee.toFixed(2)}</strong></div>
          <div class="breakdown-row"><span>GST / Tax (${quote.cost_breakdown.tax_rate_pct}%):</span> <strong>INR ${quote.cost_breakdown.tax_amount.toFixed(2)}</strong></div>
          <div class="breakdown-row" style="border-top:1px solid #cbd5e1; margin-top:6px; padding-top:6px;">
            <span>Total Landed Cost:</span> <strong style="color:#1e3a8a;">INR ${quote.cost_breakdown.total_cost.toFixed(2)}</strong>
          </div>
        `;
        card.appendChild(drawer);
      }

      card.addEventListener('click', (e) => {
        if (e.target.classList.contains('breakdown-toggle')) return;
        if (!isSvc) {
          alert(`Cannot select ${quote.courier_name}: This courier is unserviceable or excluded for this route.`);
          return;
        }
        selectCourier(quote.quote_id);
      });

      elements.courierCardsList.appendChild(card);
    });
  }

  // 4. Render Transparent Comparison Table
  if (elements.comparisonTableBody && data.comparison_table) {
    if (data.comparison_table.length === 0) {
      elements.comparisonTableBody.innerHTML = `
        <tr><td colspan="10" class="text-center" style="padding:16px;">No candidate couriers evaluated.</td></tr>
      `;
    } else {
      elements.comparisonTableBody.innerHTML = data.comparison_table.map(row => {
        const svcBdg = row.is_serviceable
          ? `<span class="badge-status-svc">Serviceable</span>`
          : `<span class="badge-status-unsvc">${row.serviceability_status || 'Unserviceable'}</span>`;

        let outcomeBdg = `<span class="badge-outcome-lost">Alternative</span>`;
        if (row.is_winner) {
          outcomeBdg = `<span class="badge-outcome-won">WINNER</span>`;
        } else if (!row.is_eligible) {
          outcomeBdg = `<span class="badge-outcome-excluded" title="${row.reason_summary || 'Excluded'}">EXCLUDED</span>`;
        }

        const bWt = row.billable_weight_kg != null ? `${row.billable_weight_kg.toFixed(2)} kg` : '—';
        const bFreight = row.base_freight != null ? `₹ ${row.base_freight.toFixed(2)}` : '—';
        const fuel = row.fuel_surcharge != null ? `₹ ${row.fuel_surcharge.toFixed(2)}` : '—';
        const cod = row.cod_fee != null ? `₹ ${row.cod_fee.toFixed(2)}` : '—';
        const tax = row.tax_amount != null ? `₹ ${row.tax_amount.toFixed(2)}` : '—';
        const tot = row.total_cost != null ? `<strong>₹ ${row.total_cost.toFixed(2)}</strong>` : '—';
        const sla = row.estimated_delivery_days != null ? `${row.estimated_delivery_days} Days` : '—';

        return `
          <tr>
            <td><strong>${row.courier_name}</strong><br><small style="color:var(--text-muted);">${row.service_type}</small></td>
            <td>${svcBdg}</td>
            <td>${bWt}</td>
            <td>${bFreight}</td>
            <td>${fuel}</td>
            <td>${cod}</td>
            <td>${tax}</td>
            <td>${tot}</td>
            <td>${sla}</td>
            <td>${outcomeBdg}</td>
          </tr>
        `;
      }).join('');
    }
  }

  // 5. Render Loss & Exclusion Rationales
  if (elements.lossReasonsList) {
    const lossEntries = Object.entries(data.loss_reasons || {});
    if (lossEntries.length > 0) {
      if (elements.lossReasonsSection) elements.lossReasonsSection.classList.remove('hidden');
      elements.lossReasonsList.innerHTML = lossEntries.map(([code, reasons]) => {
        const rList = Array.isArray(reasons) ? reasons.join('; ') : reasons;
        return `<div><strong style="color:var(--primary-dark);">${code}:</strong> ${rList}</div>`;
      }).join('');
    } else {
      if (elements.lossReasonsSection) elements.lossReasonsSection.classList.add('hidden');
    }
  }

  // 6. Navigation Button State
  if (elements.btnProceedToApproval) {
    elements.btnProceedToApproval.disabled = !state.selectedQuoteId;
  }

  updateApprovalSummary();
  fetchAndRenderMlPrediction();
}

window.toggleBreakdown = function(quoteId, event) {
  event.stopPropagation();
  const drawer = document.getElementById(`drawer-${quoteId}`);
  if (drawer) {
    drawer.classList.toggle('hidden');
  }
};

function selectCourier(quoteId) {
  const quote = state.activeQuotes.find(q => q.quote_id === quoteId);
  if (!quote || quote.is_serviceable === false || quote.is_eligible === false) {
    alert('This courier is unserviceable or excluded for this route and cannot be selected.');
    return;
  }

  state.selectedQuoteId = quoteId;
  document.querySelectorAll('.courier-card').forEach(c => {
    c.classList.toggle('selected', c.getAttribute('data-id') === quoteId);
  });

  if (elements.btnProceedToApproval) {
    elements.btnProceedToApproval.disabled = false;
  }

  updateApprovalSummary();
  fetchAndRenderMlPrediction();
}

// -----------------------------------------------------------------------------
// Human Approval Gate & Stage 3 -> Stage 4 Auto-Advance
// -----------------------------------------------------------------------------

function setupApprovalGate() {
  elements.chkOperatorConfirm.addEventListener('change', (e) => {
    elements.btnApproveDispatch.disabled = !e.target.checked;
  });

  elements.btnApproveDispatch.addEventListener('click', async () => {
    await executeDispatch();
  });
}

function updateApprovalSummary() {
  const quote = state.activeQuotes.find(q => q.quote_id === state.selectedQuoteId);
  const order = state.activeOrder;
  if (!quote || !order) return;

  const cost = quote.cost_breakdown ? quote.cost_breakdown.total_cost.toFixed(2) : '0.00';
  elements.approvalSummary.innerHTML = `
    <div><strong>Route:</strong> PIN ${order.sender.pin_code} to PIN ${order.recipient.pin_code}</div>
    <div><strong>Selected Courier:</strong> ${quote.courier_name} (${quote.service_type})</div>
    <div><strong>Total Landed Cost:</strong> INR ${cost} (${order.is_cod ? 'Cash on Delivery: INR ' + order.cod_amount.toFixed(2) : 'Prepaid'})</div>
    <div><strong>Chargeable Weight:</strong> ${order.parcel_weight_kg.toFixed(2)} kg (Dimensions: ${order.dimensions.length_cm} x ${order.dimensions.width_cm} x ${order.dimensions.height_cm} cm)</div>
  `;

  elements.chkOperatorConfirm.checked = false;
  elements.btnApproveDispatch.disabled = true;
}

async function executeDispatch() {
  const quote = state.activeQuotes.find(q => q.quote_id === state.selectedQuoteId);
  const order = state.activeOrder;
  if (!quote || !order) return;

  if (quote.is_serviceable === false || quote.is_eligible === false) {
    alert('Cannot dispatch: Selected courier is unserviceable or ineligible for this route.');
    return;
  }

  elements.btnApproveDispatch.disabled = true;
  elements.btnApproveDispatch.textContent = 'Dispatching with Carrier & Generating AWB...';

  try {
    const payload = {
      order_reference: order.order_reference,
      quote_id: quote.quote_id,
      courier_code: quote.courier_code,
      approved_by: 'Shipping Operator (Web)',
      total_cost: quote.cost_breakdown ? quote.cost_breakdown.total_cost : 0.0,
      provider_mode: state.providerMode,
    };

    const res = await fetch('/api/dispatch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    const data = await res.json();
    if (!res.ok) {
      alert(`Dispatch Failed: ${data.detail || 'Unknown error'}`);
      return;
    }

    renderShipmentReady(data);

    // AUTOMATIC ADVANCE: Stage 3 -> Stage 4 (Label generation & confirmation)
    goToWizardStep(4);
  } catch (err) {
    alert(`Dispatch Network Error: ${err.message}`);
  } finally {
    elements.btnApproveDispatch.disabled = false;
    elements.btnApproveDispatch.textContent = 'Approve and Create Shipment';
  }
}

function renderShipmentReady(data) {
  state.lastDispatchResult = data;
  elements.shipmentReadyPanel.classList.remove('hidden');

  elements.readyAwb.textContent = `AWB / Tracking: ${data.tracking_number || 'N/A'}`;
  elements.readyShipmentId.textContent = data.shipment_id || '-';
  elements.readyCarrier.textContent = data.courier_code || 'Carrier';
  elements.readyPickup.textContent = data.pickup?.pickup_window || '10:00 AM - 01:00 PM IST';

  const vReport = data.verification_report;
  if (vReport) {
    elements.readyVerification.textContent = `${vReport.overall_status} (Passed ${vReport.checks.filter(c => c.passed).length}/5 checks)`;
  }

  // Render HTML / SVG preview of shipping label
  if (data.label_html_preview) {
    elements.labelPreviewContent.innerHTML = data.label_html_preview;
  }

  // Bind Label Action Buttons
  elements.btnDownloadLabelPdf.onclick = () => {
    window.open(`/api/labels/${data.shipment_id}/pdf`, '_blank');
  };

  elements.btnPrintLabel.onclick = () => {
    const printWindow = window.open('', '_blank');
    printWindow.document.write(`
      <!DOCTYPE html>
      <html><head><title>Print Label</title></head>
      <body style="margin:0;display:flex;justify-content:center;padding:20px;">
        ${data.label_html_preview}
      </body></html>
    `);
    printWindow.document.close();
    printWindow.focus();
    setTimeout(() => { printWindow.print(); printWindow.close(); }, 300);
  };

  elements.btnExpandLabel.onclick = () => {
    showModal('Fullscreen Shipping Label Preview', `
      <div style="display:flex;justify-content:center;background:#fff;padding:20px;border-radius:8px;">
        ${data.label_html_preview}
      </div>
    `);
  };
}

// -----------------------------------------------------------------------------
// Evaluator: Official Competition Test Cases & 10 Mock Scenarios
// -----------------------------------------------------------------------------

function setupEvaluatorTab() {
  if (elements.btnRunAllOfficialTc) {
    elements.btnRunAllOfficialTc.addEventListener('click', async () => {
      await runAllOfficialTestCases();
    });
  }

  if (elements.btnResetOfficialTc) {
    elements.btnResetOfficialTc.addEventListener('click', async () => {
      if (confirm('Reset official test case results and clear history?')) {
        await fetch('/api/scenarios/test-runs/clear', { method: 'POST' });
        await loadOfficialScenarios();
        await loadScenarios();
        await loadTestRuns();
      }
    });
  }

  elements.btnRunAllScenarios.addEventListener('click', async () => {
    elements.btnRunAllScenarios.disabled = true;
    elements.btnRunAllScenarios.textContent = 'Running All 10 Scenarios...';
    try {
      for (const sc of state.scenarios) {
        await runScenario(sc.id);
      }
      await loadTestRuns();
    } finally {
      elements.btnRunAllScenarios.disabled = false;
      elements.btnRunAllScenarios.textContent = 'Run All 10 Scenarios';
    }
  });

  elements.btnClearTestRuns.addEventListener('click', async () => {
    if (confirm('Clear all recorded test case execution runs from SQLite?')) {
      await fetch('/api/scenarios/test-runs/clear', { method: 'POST' });
      await loadOfficialScenarios();
      await loadScenarios();
      await loadTestRuns();
    }
  });

  // Initial load
  loadOfficialScenarios();
  loadScenarios();
  loadTestRuns();
}

// -----------------------------------------------------------------------------
// Official Competition Test Cases (TC1 – TC4)
// -----------------------------------------------------------------------------

async function loadOfficialScenarios() {
  try {
    const res = await fetch('/api/official-scenarios');
    state.officialTestCases = await res.json();
    renderOfficialTcGrid();
    updateOfficialMetrics();
  } catch (err) {
    console.error('Failed to load official test cases:', err);
  }
}

function updateOfficialMetrics() {
  if (!state.officialTestCases) return;
  const passed = state.officialTestCases.filter(t => t.status === 'PASSED').length;
  const failed = state.officialTestCases.filter(t => t.status === 'FAILED' || t.status === 'ERROR').length;
  const notRun = state.officialTestCases.filter(t => !t.status || t.status === 'NOT RUN').length;

  if (elements.officialMetricPassed) elements.officialMetricPassed.textContent = `${passed} / 4`;
  if (elements.officialMetricFailed) elements.officialMetricFailed.textContent = `${failed}`;
  if (elements.officialMetricNotRun) elements.officialMetricNotRun.textContent = `${notRun}`;
  if (elements.officialMetricAudit) {
    elements.officialMetricAudit.textContent = passed === 4 ? '100% Verified' : '5-Point Audit';
  }
}

function renderOfficialTcGrid() {
  if (!elements.officialTcGrid) return;
  elements.officialTcGrid.innerHTML = '';

  state.officialTestCases.forEach(tc => {
    const isPassed = tc.status === 'PASSED';
    const isFailed = tc.status === 'FAILED' || tc.status === 'ERROR';
    const isNotRun = !tc.status || tc.status === 'NOT RUN';

    let pillClass = 'status-pill-not-run';
    let pillText = 'NOT RUN';
    if (isPassed) { pillClass = 'status-passed'; pillText = 'PASSED'; }
    else if (isFailed) { pillClass = 'status-failed'; pillText = tc.status; }

    const actualClass = isNotRun ? 'actual-not-run' : (isPassed ? '' : 'actual-failed');
    const actualText = tc.actual_outcome || 'Not run yet — click "Run Test" below to execute verification.';

    const card = document.createElement('div');
    card.className = 'official-tc-card';
    card.id = `official-card-${tc.id}`;
    card.innerHTML = `
      <div>
        <div class="tc-card-header">
          <div style="display:flex; align-items:center; gap:8px;">
            <span class="scenario-badge" style="background:#1e3a8a; color:#ffffff; font-weight:700;">${tc.id}</span>
            <span class="badge-tc-flag">OFFICIAL TEST</span>
          </div>
          <span class="scenario-status-pill ${pillClass}" id="status-pill-${tc.id}">${pillText}</span>
        </div>
        
        <h4 class="tc-title" style="margin-top:10px;">${tc.title}</h4>
        <p class="tc-subtitle">${tc.subtitle || tc.description}</p>

        <div class="tc-route-info" style="margin-top:10px;">
          <span class="tc-spec-badge">Route: ${tc.route}</span>
          <span class="tc-spec-badge">Size: ${tc.dimensions}</span>
          <span class="tc-spec-badge">Weight: ${tc.weight_kg} kg</span>
        </div>

        <div class="outcome-container">
          <div class="outcome-box expected-outcome-box">
            <strong>Expected Outcome</strong>
            <span>${tc.expected}</span>
          </div>
          <div class="outcome-box actual-outcome-box ${actualClass}" id="actual-box-${tc.id}">
            <strong>Actual Outcome</strong>
            <span id="actual-text-${tc.id}">${actualText}</span>
          </div>
        </div>
      </div>

      <div class="scenario-actions">
        <button class="btn-primary btn-sm" onclick="runOfficialTestCase('${tc.id}')" id="btn-run-${tc.id}">Run Test</button>
        <button class="btn-outline btn-sm" onclick="loadOfficialTestCaseIntoForm('${tc.id}')">Load into Form</button>
        <button class="btn-ghost btn-sm" onclick="viewOfficialEvidence('${tc.id}')">View Evidence</button>
      </div>
    `;
    elements.officialTcGrid.appendChild(card);
  });
}

window.runOfficialTestCase = async function(tcId) {
  const btn = document.getElementById(`btn-run-${tcId}`);
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Running...';
  }
  try {
    const res = await fetch(`/api/official-scenarios/${tcId}/run`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) {
      alert(`Error running test case ${tcId}: ${data.detail || 'Execution failed'}`);
      return;
    }

    // Update in state
    const tc = state.officialTestCases.find(t => t.id === tcId);
    if (tc) {
      tc.status = data.status;
      tc.actual_outcome = data.actual_result;
      tc.explanation = data.explanation;
      tc.evidence = data.evidence_references;
      tc.timestamp = data.timestamp;
    }

    // Update card elements
    const pill = document.getElementById(`status-pill-${tcId}`);
    if (pill) {
      pill.className = `scenario-status-pill ${data.status === 'PASSED' ? 'status-passed' : 'status-failed'}`;
      pill.textContent = data.status;
    }

    const box = document.getElementById(`actual-box-${tcId}`);
    const text = document.getElementById(`actual-text-${tcId}`);
    if (box && text) {
      box.className = `outcome-box actual-outcome-box ${data.status === 'PASSED' ? '' : 'actual-failed'}`;
      text.textContent = data.actual_result;
    }

    updateOfficialMetrics();
    await loadTestRuns();
    return data;
  } catch (err) {
    alert(`Error executing ${tcId}: ${err.message}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Run Test';
    }
  }
};

window.runAllOfficialTestCases = async function() {
  if (elements.btnRunAllOfficialTc) {
    elements.btnRunAllOfficialTc.disabled = true;
    elements.btnRunAllOfficialTc.textContent = 'Running All 4 Tests...';
  }
  try {
    const res = await fetch('/api/official-scenarios/run', { method: 'POST' });
    const results = await res.json();
    await loadOfficialScenarios();
    await loadTestRuns();
    return results;
  } catch (err) {
    alert(`Error running all official tests: ${err.message}`);
  } finally {
    if (elements.btnRunAllOfficialTc) {
      elements.btnRunAllOfficialTc.disabled = false;
      elements.btnRunAllOfficialTc.textContent = 'Run All 4 Official Tests';
    }
  }
};

window.loadOfficialTestCaseIntoForm = async function(tcId) {
  try {
    const res = await fetch(`/api/official-scenarios/${tcId}/load`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) {
      alert(`Failed to load ${tcId}: ${data.detail}`);
      return;
    }

    // Switch to rates tab
    elements.tabButtons[0].click();
    
    elements.dimLength.value = data.order.dimensions.length_cm;
    elements.dimWidth.value = data.order.dimensions.width_cm;
    elements.dimHeight.value = data.order.dimensions.height_cm;
    elements.dimWeight.value = data.order.parcel_weight_kg;
    document.getElementById('senderPin').value = data.order.sender.pin_code;
    document.getElementById('recipientPin').value = data.order.recipient.pin_code;
    document.getElementById('packageType').value = data.order.package_type;
    elements.paymentMode.value = data.order.is_cod ? 'cod' : 'prepaid';
    if (data.order.is_cod) {
      elements.codGroup.classList.remove('hidden');
      elements.codAmount.value = data.order.cod_amount;
    } else {
      elements.codGroup.classList.add('hidden');
    }

    updateLiveVolumetric();
    renderRateResults(data);

    // Jump to Step 2 so user sees loaded rates immediately
    goToWizardStep(2);
  } catch (err) {
    alert(`Error loading ${tcId}: ${err.message}`);
  }
};

window.viewOfficialEvidence = function(tcId) {
  const tc = state.officialTestCases.find(t => t.id === tcId);
  if (!tc || !tc.evidence) {
    alert(`No evidence found for ${tcId}. Please run the test case first.`);
    return;
  }

  showModal(`Official Evidence: ${tc.title}`, `
    <div style="margin-bottom:12px;">
      <p style="margin:0 0 4px 0; font-size:13px;"><strong>Status:</strong> <span class="${tc.status === 'PASSED' ? 'text-success' : 'text-danger'}">${tc.status}</span></p>
      <p style="margin:0 0 4px 0; font-size:13px;"><strong>Expected:</strong> ${tc.expected}</p>
      <p style="margin:0 0 4px 0; font-size:13px;"><strong>Actual:</strong> ${tc.actual_outcome || 'N/A'}</p>
      <p style="margin:0; font-size:13px;"><strong>Explanation:</strong> ${tc.explanation || 'N/A'}</p>
    </div>
    <div style="font-family:var(--font-mono);font-size:12px;background:#f8fafc;color:#0f172a;border:1px solid #cbd5e1;padding:14px;border-radius:6px;max-height:400px;overflow:auto;">
      <pre>${JSON.stringify(tc.evidence, null, 2)}</pre>
    </div>
  `);
};

// -----------------------------------------------------------------------------
// 10 Evaluator Edge-Case Scenarios (Demo A through Demo J)
// -----------------------------------------------------------------------------

async function loadScenarios() {
  try {
    const res = await fetch('/api/scenarios');
    state.scenarios = await res.json();
    renderScenariosGrid();
  } catch (err) {
    console.error('Failed to load scenarios:', err);
  }
}

function renderScenariosGrid() {
  elements.scenariosGrid.innerHTML = '';
  state.scenarios.forEach(sc => {
    const isPassed = sc.status === 'PASSED';
    const isFailed = sc.status === 'FAILED' || sc.status === 'ERROR';
    const isNotRun = !sc.status || sc.status === 'NOT RUN';

    let pillClass = 'status-pill-not-run';
    let pillText = 'NOT RUN';
    if (isPassed) { pillClass = 'status-passed'; pillText = 'PASSED'; }
    else if (isFailed) { pillClass = 'status-failed'; pillText = sc.status; }

    const actualClass = isNotRun ? 'actual-not-run' : (isPassed ? '' : 'actual-failed');
    const actualText = sc.actual_outcome || 'Not run yet — click "Run Test" below to execute scenario.';

    const card = document.createElement('div');
    card.className = 'scenario-card';
    card.innerHTML = `
      <div>
        <div style="display:flex;align-items:center;justify-content:space-between;">
          <div style="display:flex;align-items:center;">
            <span class="scenario-badge">${sc.id}</span>
            <span class="badge-demo-flag" style="font-size:10px; padding:1px 6px;">MOCK DATA</span>
          </div>
          <span class="scenario-status-pill ${pillClass}" id="status-pill-${sc.id}">${pillText}</span>
        </div>
        <h4 class="scenario-title">${sc.title}</h4>
        <p class="scenario-desc">${sc.description}</p>

        <div class="outcome-container">
          <div class="outcome-box expected-outcome-box">
            <strong>Expected Outcome</strong>
            <span>${sc.expected}</span>
          </div>
          <div class="outcome-box actual-outcome-box ${actualClass}" id="actual-sc-box-${sc.id}">
            <strong>Actual Outcome</strong>
            <span id="actual-sc-text-${sc.id}">${actualText}</span>
          </div>
        </div>
      </div>
      <div class="scenario-actions">
        <button class="btn-primary btn-sm" onclick="loadScenarioIntoForm('${sc.id}')">Load into Form</button>
        <button class="btn-outline btn-sm" onclick="runScenario('${sc.id}')" id="btn-run-sc-${sc.id}">Run Test</button>
      </div>
    `;
    elements.scenariosGrid.appendChild(card);
  });
}

window.loadScenarioIntoForm = async function(scId) {
  try {
    const res = await fetch(`/api/scenarios/${scId}/load`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) {
      alert(`Failed to load scenario: ${data.detail}`);
      return;
    }

    // Switch to rates tab
    elements.tabButtons[0].click();
    
    elements.dimLength.value = data.order.dimensions.length_cm;
    elements.dimWidth.value = data.order.dimensions.width_cm;
    elements.dimHeight.value = data.order.dimensions.height_cm;
    elements.dimWeight.value = data.order.parcel_weight_kg;
    document.getElementById('senderPin').value = data.order.sender.pin_code;
    document.getElementById('recipientPin').value = data.order.recipient.pin_code;
    document.getElementById('packageType').value = data.order.package_type;
    elements.paymentMode.value = data.order.is_cod ? 'cod' : 'prepaid';
    if (data.order.is_cod) {
      elements.codGroup.classList.remove('hidden');
      elements.codAmount.value = data.order.cod_amount;
    } else {
      elements.codGroup.classList.add('hidden');
    }

    updateLiveVolumetric();
    renderRateResults(data);

    // Jump to Step 2 so user sees the loaded rates immediately
    goToWizardStep(2);
  } catch (err) {
    alert(`Error loading scenario: ${err.message}`);
  }
};

window.runScenario = async function(scId) {
  const btn = document.getElementById(`btn-run-sc-${scId}`);
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Running...';
  }
  try {
    const res = await fetch(`/api/scenarios/${scId}/run`, { method: 'POST' });
    const data = await res.json();
    
    // Update pill
    const pill = document.getElementById(`status-pill-${scId}`);
    if (pill) {
      pill.classList.remove('hidden', 'status-passed', 'status-failed', 'status-pill-not-run');
      const isPassed = data.status === 'PASSED';
      pill.classList.add(isPassed ? 'status-passed' : 'status-failed');
      pill.textContent = data.status;
    }

    // Update actual outcome callout
    const box = document.getElementById(`actual-sc-box-${scId}`);
    const text = document.getElementById(`actual-sc-text-${scId}`);
    if (box && text) {
      box.className = `outcome-box actual-outcome-box ${data.status === 'PASSED' ? '' : 'actual-failed'}`;
      text.textContent = data.actual_outcome || data.actual_result || 'Executed successfully.';
    }

    // Also update in state.scenarios
    const sc = state.scenarios.find(s => s.id === scId);
    if (sc) {
      sc.status = data.status;
      sc.actual_outcome = data.actual_outcome || data.actual_result;
    }

    await loadTestRuns();
    return data;
  } catch (err) {
    alert(`Error executing scenario ${scId}: ${err.message}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Run Test';
    }
  }
};

async function loadTestRuns() {
  try {
    const res = await fetch('/api/scenarios/test-runs');
    state.testRuns = await res.json();
    renderTestRunsTable();
  } catch (err) {
    console.error('Failed to load test runs:', err);
  }
}

function renderTestRunsTable() {
  elements.testRunsCountBadge.textContent = `${state.testRuns.length} Runs`;
  if (state.testRuns.length === 0) {
    elements.testRunsTableBody.innerHTML = `<tr><td colspan="7" class="text-center">No test runs recorded yet. Click 'Run Test' above.</td></tr>`;
    return;
  }

  elements.testRunsTableBody.innerHTML = state.testRuns.map(run => {
    const isPass = run.status === 'PASSED' || run.status === 'PASS';
    const statusClass = isPass ? 'text-success' : 'text-danger';
    const timeFormatted = new Date(run.timestamp).toLocaleTimeString();

    return `
      <tr>
        <td><code>${run.test_case_id}</code></td>
        <td><strong>${run.test_case_name}</strong></td>
        <td class="${statusClass}"><strong>${run.status}</strong></td>
        <td>${run.expected_result}</td>
        <td>${run.actual_result}</td>
        <td>${timeFormatted}</td>
        <td><button class="btn-ghost btn-sm" onclick="viewEvidence(${run.run_id})">View Evidence</button></td>
      </tr>
    `;
  }).join('');
}

window.viewEvidence = function(runId) {
  const run = state.testRuns.find(r => r.run_id === runId);
  if (!run) return;

  let evidenceObj = {};
  try { evidenceObj = JSON.parse(run.evidence_json); } catch (e) { evidenceObj = run.evidence_json; }

  showModal(`Evidence: ${run.test_case_name}`, `
    <div style="font-family:var(--font-mono);font-size:12px;background:#f8fafc;color:#0f172a;border:1px solid #cbd5e1;padding:14px;border-radius:6px;max-height:400px;overflow:auto;">
      <pre>${JSON.stringify(evidenceObj, null, 2)}</pre>
    </div>
  `);
};

// -----------------------------------------------------------------------------

// -----------------------------------------------------------------------------
// Provider Controls & Sandbox Mode Management
// -----------------------------------------------------------------------------

function setupProviderControls() {
  loadProviderStatus();

  if (elements.providerSelect) {
    elements.providerSelect.addEventListener('change', async (e) => {
      const newMode = e.target.value;
      try {
        const res = await fetch('/api/provider/mode', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode: newMode }),
        });
        const data = await res.json();
        if (!res.ok) {
          alert(`Failed to switch provider mode: ${data.detail}`);
          return;
        }
        state.providerMode = data.active_mode;
        await loadProviderStatus();
      } catch (err) {
        alert(`Error switching provider mode: ${err.message}`);
      }
    });
  }


}

async function loadProviderStatus() {
  try {
    const res = await fetch('/api/provider/status');
    const status = await res.json();
    state.providerMode = status.active_mode;
    if (elements.providerSelect) {
      elements.providerSelect.value = (status.active_mode === 'sandbox') ? 'local_sandbox' : status.active_mode;
    }
    updateProviderBanner(status);
  } catch (err) {
    console.error('Failed to load provider status:', err);
  }
}

function updateProviderBanner(status) {
  if (!elements.providerModeBanner) return;

  const mode = status.active_mode;
  elements.providerModeBanner.className = 'provider-mode-banner';
  elements.providerBannerActions.innerHTML = '';

  if (mode === 'demo') {
    elements.providerModeBanner.classList.add('banner-demo');
    elements.providerBannerIcon.textContent = '[DEMO]';
    elements.providerBannerText.innerHTML = 'Operating in <strong>Local Demo Mode</strong> (100% offline simulated carriers). Quotes, AWBs, and tracking are simulated demonstration data.';
  } else {
    elements.providerModeBanner.classList.add('banner-sandbox');
    elements.providerBannerIcon.textContent = '[SANDBOX]';
    elements.providerBannerText.innerHTML = 'Operating in <strong>Local Courier Sandbox (Simulation)</strong>. Deterministic offline sandbox test environment using synthetic carrier data. Zero external API calls or credentials required.';
  }
}

window.switchProviderMode = async function(mode) {
  if (elements.providerSelect) {
    elements.providerSelect.value = mode;
    elements.providerSelect.dispatchEvent(new Event('change'));
  }
};



// Packaging 3D Optimizer Tab
// -----------------------------------------------------------------------------

function setupPackagingTab() {
  elements.customPkgForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const payload = {
      name: document.getElementById('pkgName').value,
      outer_length_cm: parseFloat(document.getElementById('pkgL').value),
      outer_width_cm: parseFloat(document.getElementById('pkgW').value),
      outer_height_cm: parseFloat(document.getElementById('pkgH').value),
      wall_thickness_cm: parseFloat(document.getElementById('pkgWall').value),
      padding_cm: parseFloat(document.getElementById('pkgPad').value),
      tare_weight_kg: parseFloat(document.getElementById('pkgTare').value),
    };

    try {
      const res = await fetch('/api/custom-packaging', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        elements.customPkgForm.reset();
        await loadCustomPackages();
      }
    } catch (err) {
      alert(`Failed to save packaging: ${err.message}`);
    }
  });
}

async function loadCustomPackages() {
  try {
    const res = await fetch('/api/custom-packaging');
    const packages = await res.json();
    elements.customPackagesList.innerHTML = packages.map(pkg => `
      <div class="card" style="display:flex;justify-content:space-between;align-items:center;">
        <div>
          <h4>${pkg.name} (${pkg.package_type})</h4>
          <span class="text-muted" style="font-size:12px;">Outer: ${pkg.outer_length_cm} x ${pkg.outer_width_cm} x ${pkg.outer_height_cm} cm | Tare: ${pkg.tare_weight_kg} kg</span>
        </div>
        <button class="btn-ghost btn-sm text-danger" onclick="deletePackage('${pkg.packaging_id}')">Delete</button>
      </div>
    `).join('') || '<p class="text-muted text-center">No custom packaging boxes saved yet.</p>';
  } catch (err) {
    console.error('Failed to load packaging:', err);
  }
}

window.deletePackage = async function(pkgId) {
  if (confirm('Delete this custom packaging box?')) {
    await fetch(`/api/custom-packaging/${pkgId}`, { method: 'DELETE' });
    await loadCustomPackages();
  }
};

// -----------------------------------------------------------------------------
// Audit Log Tab
// -----------------------------------------------------------------------------

function setupAuditTab() {
  elements.btnRefreshAudit.addEventListener('click', loadAuditLogs);
}

async function loadAuditLogs() {
  try {
    const res = await fetch('/api/audit-logs');
    const logs = await res.json();
    elements.auditTableBody.innerHTML = logs.map(l => `
      <tr>
        <td><code>#${l.log_id}</code></td>
        <td>${new Date(l.timestamp).toLocaleTimeString()}</td>
        <td><span class="badge-fsm">${l.event_type}</span></td>
        <td><code>${l.order_reference || '-'}</code></td>
        <td><code>${l.shipment_id || '-'}</code></td>
        <td><strong>${l.actor}</strong></td>
        <td><span style="font-size:11px;color:#475569;">${l.details_json.substring(0, 80)}...</span></td>
      </tr>
    `).join('') || `<tr><td colspan="7" class="text-center">No audit logs recorded yet.</td></tr>`;
  } catch (err) {
    console.error('Failed to load audit logs:', err);
  }
}

// -----------------------------------------------------------------------------
// Modal Dialog
// -----------------------------------------------------------------------------

function setupModal() {
  elements.modalCloseBtn.addEventListener('click', hideModal);
  elements.appModal.addEventListener('click', (e) => {
    if (e.target === elements.appModal) hideModal();
  });
}

function showModal(title, htmlContent) {
  elements.modalTitle.textContent = title;
  elements.modalBody.innerHTML = htmlContent;
  elements.appModal.classList.remove('hidden');
}

function hideModal() {
  elements.appModal.classList.add('hidden');
}


// -----------------------------------------------------------------------------
// Machine Learning Price Prediction & Benchmark
// -----------------------------------------------------------------------------

function setupMlPrediction() {
  if (elements.mlModelSelect) {
    elements.mlModelSelect.addEventListener('change', () => {
      fetchAndRenderMlPrediction();
    });
  }
  if (elements.btnToggleMlDetails) {
    elements.btnToggleMlDetails.addEventListener('click', () => {
      elements.mlLimitationsDrawer.classList.toggle('hidden');
      const isHidden = elements.mlLimitationsDrawer.classList.contains('hidden');
      elements.btnToggleMlDetails.textContent = isHidden ? 'View Limitations & Features' : 'Hide Limitations & Features';
    });
  }
}

async function fetchAndRenderMlPrediction() {
  if (!elements.mlPredictedPrice) return;
  if (!state.activeOrder && (!elements.dimLength || !elements.dimLength.value)) return;

  const modelType = elements.mlModelSelect ? elements.mlModelSelect.value : 'domestic_courier';
  const selectedQuote = state.activeQuotes.find(q => q.quote_id === state.selectedQuoteId) || state.activeQuotes[0];

  const payload = {
    origin_pin: document.getElementById('senderPin') ? document.getElementById('senderPin').value : '110001',
    destination_pin: document.getElementById('recipientPin') ? document.getElementById('recipientPin').value : '400001',
    length_cm: parseFloat(elements.dimLength.value) || 20.0,
    width_cm: parseFloat(elements.dimWidth.value) || 15.0,
    height_cm: parseFloat(elements.dimHeight.value) || 10.0,
    parcel_weight_kg: parseFloat(elements.dimWeight.value) || 1.5,
    package_type: document.getElementById('packageType') ? document.getElementById('packageType').value : 'box',
    courier_code: selectedQuote ? selectedQuote.courier_code : 'DEMO_AIR_EXPRESS',
    is_cod: elements.paymentMode ? elements.paymentMode.value === 'cod' : false,
    cod_amount: elements.paymentMode && elements.paymentMode.value === 'cod' ? (parseFloat(elements.codAmount.value) || 0.0) : 0.0,
    model_type: modelType,
    quote_cost: selectedQuote && selectedQuote.cost_breakdown ? selectedQuote.cost_breakdown.total_cost : null,
    quote_courier_name: selectedQuote ? selectedQuote.courier_name : null,
  };

  try {
    elements.mlPredictedPrice.textContent = 'Calculating...';
    if (elements.mlSelectedCourierPrice) {
      const selectedPriceVal = extractQuotePrice(selectedQuote);
      if (selectedQuote && selectedPriceVal !== null) {
        elements.mlSelectedCourierPrice.textContent = formatINR(selectedPriceVal);
        if (elements.mlSelectedCourierName) {
          elements.mlSelectedCourierName.textContent = selectedQuote.courier_name;
        }
      } else {
        elements.mlSelectedCourierPrice.textContent = 'Unavailable';
        if (elements.mlSelectedCourierName) {
          elements.mlSelectedCourierName.textContent = 'No quote selected';
        }
      }
    }

    const res = await fetch('/api/v1/predictions/shipment-price', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      elements.mlPredictedPrice.textContent = 'Unavailable';
      if (elements.mlStatusBadge) {
        elements.mlStatusBadge.className = 'badge-benchmark badge-benchmark-neutral';
        elements.mlStatusBadge.textContent = 'Unavailable';
      }
      if (elements.mlBenchmarkExplanation) {
        elements.mlBenchmarkExplanation.textContent = 'Model endpoint error occurred.';
      }
      if (elements.mlConfidenceInterval) {
        elements.mlConfidenceInterval.textContent = 'Model error';
      }
      return;
    }

    const data = await res.json();
    const currSym = data.currency === 'USD' ? '$' : '₹';
    const estPrice = data.predicted_price;

    if (estPrice !== null && estPrice !== undefined && Number.isFinite(estPrice) && estPrice >= 0) {
      elements.mlPredictedPrice.textContent = formatINR(estPrice);
    } else {
      elements.mlPredictedPrice.textContent = 'Unavailable';
    }

    // Selected courier price & comparison
    const quotePrice = selectedQuote && selectedQuote.cost_breakdown ? selectedQuote.cost_breakdown.total_cost : null;

    if (quotePrice !== null && estPrice !== null && estPrice !== undefined && estPrice > 0) {
      const diff = quotePrice - estPrice;
      const pctDiff = (diff / estPrice) * 100.0;
      const absPct = Math.abs(pctDiff);

      let statusText = '';
      let badgeClass = '';
      let explanation = '';

      if (pctDiff <= -5.0) {
        statusText = 'Below estimate';
        badgeClass = 'badge-below-estimate';
        explanation = `${absPct.toFixed(1)}% below the estimated price`;
      } else if (pctDiff >= 5.0) {
        statusText = 'Above estimate';
        badgeClass = 'badge-above-estimate';
        explanation = `${absPct.toFixed(1)}% above the estimated price`;
      } else {
        statusText = 'Near estimate';
        badgeClass = 'badge-near-estimate';
        explanation = `${absPct.toFixed(1)}% variance (within expected range)`;
      }

      if (elements.mlStatusBadge) {
        elements.mlStatusBadge.className = `badge-benchmark ${badgeClass}`;
        elements.mlStatusBadge.textContent = statusText;
      }
      if (elements.mlBenchmarkExplanation) {
        elements.mlBenchmarkExplanation.textContent = explanation;
      }
    } else if (estPrice !== null && estPrice !== undefined) {
      if (elements.mlStatusBadge) {
        elements.mlStatusBadge.className = 'badge-benchmark badge-benchmark-neutral';
        elements.mlStatusBadge.textContent = 'Awaiting Comparison';
      }
      if (elements.mlBenchmarkExplanation) {
        elements.mlBenchmarkExplanation.textContent = 'Select a courier quote to compare against benchmark.';
      }
    } else {
      if (elements.mlStatusBadge) {
        elements.mlStatusBadge.className = 'badge-benchmark badge-benchmark-neutral';
        elements.mlStatusBadge.textContent = 'Estimate Unavailable';
      }
      if (elements.mlBenchmarkExplanation) {
        elements.mlBenchmarkExplanation.textContent = data.error_message || 'Price benchmark model currently unavailable.';
      }
    }

    // Optional legacy element support
    if (elements.mlVarianceBadge) {
      elements.mlVarianceBadge.innerHTML = elements.mlStatusBadge ? elements.mlStatusBadge.outerHTML : '';
    }
    if (elements.mlVarianceDesc && elements.mlBenchmarkExplanation) {
      elements.mlVarianceDesc.textContent = elements.mlBenchmarkExplanation.textContent;
    }

    // Technical details
    if (elements.mlConfidenceInterval) {
      if (data.confidence_interval && data.confidence_interval.length === 2) {
        elements.mlConfidenceInterval.textContent = `90% CI: ${currSym} ${data.confidence_interval[0].toFixed(2)} – ${currSym} ${data.confidence_interval[1].toFixed(2)}`;
      } else {
        elements.mlConfidenceInterval.textContent = 'CI: Not Available';
      }
    }

    if (elements.mlMetaModel) elements.mlMetaModel.textContent = `Model: ${data.model_name}`;
    if (elements.mlMetaVersion) elements.mlMetaVersion.textContent = `v${data.model_version}`;
    if (elements.mlMetaSchema) elements.mlMetaSchema.textContent = `Schema v${data.feature_schema_version}`;

    if (elements.mlLimitationsList && data.limitations && data.limitations.length > 0) {
      elements.mlLimitationsList.innerHTML = data.limitations.map(l => `<li>${l}</li>`).join('');
    }
  } catch (err) {
    console.warn('ML Prediction lookup error:', err);
    elements.mlPredictedPrice.textContent = 'Error';
    if (elements.mlStatusBadge) {
      elements.mlStatusBadge.className = 'badge-benchmark badge-benchmark-neutral';
      elements.mlStatusBadge.textContent = 'Error';
    }
    if (elements.mlBenchmarkExplanation) {
      elements.mlBenchmarkExplanation.textContent = err.message || 'Unable to retrieve prediction.';
    }
  }
}


window.viewFieldProvenance = async function(quoteId, event) {
  if (event) event.stopPropagation();
  let quote = (state.activeQuotes || []).find(q => q.quote_id === quoteId);
  let provList = [];

  if (quote && quote.field_provenance && Object.keys(quote.field_provenance).length > 0) {
    provList = Object.values(quote.field_provenance);
  } else {
    try {
      const res = await fetch(`/api/quotes/${encodeURIComponent(quoteId)}/provenance`);
      if (res.ok) {
        const pdata = await res.json();
        provList = pdata.field_provenance || [];
      }
    } catch (e) {
      console.warn('Failed to fetch quote provenance:', e);
    }
  }

  if (elements.modalProvTitle) {
    elements.modalProvTitle.textContent = `Data Provenance Audit: ${quote ? quote.courier_name : quoteId}`;
  }
  if (elements.modalProvSubtitle) {
    elements.modalProvSubtitle.innerHTML = `Quote ID: <code>${quoteId}</code> &bull; Source: <strong>${quote ? (quote.source_id || quote.source_type) : 'Aggregator'}</strong> &bull; Timestamp: <strong>${quote ? quote.retrieval_timestamp : 'N/A'}</strong>`;
  }

  if (elements.modalProvTableBody) {
    if (provList.length === 0) {
      elements.modalProvTableBody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding:16px;">No field-level provenance records available.</td></tr>`;
    } else {
      elements.modalProvTableBody.innerHTML = provList.map(p => {
        const st = p.validation_status || 'VALIDATED';
        let badgeColor = '#15803d';
        let badgeBg = '#f0fdf4';
        if (st.includes('UNVERIFIED')) {
          badgeColor = '#92400e';
          badgeBg = '#fef3c7';
        } else if (st.includes('MOCK')) {
          badgeColor = '#1d4ed8';
          badgeBg = '#eff6ff';
        }
        return `
          <tr style="border-bottom: 1px solid #e2e8f0;">
            <td style="padding: 8px 10px; font-weight: 600; color: var(--primary);">${p.field_name}</td>
            <td style="padding: 8px 10px; font-weight: 700;">${p.extracted_value != null ? p.extracted_value : '—'}</td>
            <td style="padding: 8px 10px;"><span style="font-size: 11px; padding: 2px 6px; border-radius: 3px; background: #e2e8f0;">${p.source_type}</span></td>
            <td style="padding: 8px 10px; font-family: monospace; font-size: 11px;">${p.source_identifier || '—'}</td>
            <td style="padding: 8px 10px; font-family: monospace; font-size: 11px; max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${p.source_url_or_endpoint || ''}">${p.source_url_or_endpoint || '—'}</td>
            <td style="padding: 8px 10px; font-size: 11px;">${p.retrieval_timestamp || '—'}</td>
            <td style="padding: 8px 10px; font-size: 11px;">${p.retrieval_method || '—'}</td>
            <td style="padding: 8px 10px;"><span style="font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: 3px; color: ${badgeColor}; background: ${badgeBg};">${st}</span></td>
          </tr>
        `;
      }).join('');
    }
  }

  if (elements.modalProvRawBox && elements.modalProvRawPre) {
    const rawSnap = quote ? (quote.raw_response_snapshot || (provList[0] && provList[0].raw_snapshot_json)) : null;
    if (rawSnap) {
      elements.modalProvRawBox.classList.remove('hidden');
      elements.modalProvRawPre.textContent = typeof rawSnap === 'string' ? rawSnap : JSON.stringify(rawSnap, null, 2);
    } else {
      elements.modalProvRawBox.classList.add('hidden');
    }
  }

  if (elements.modalFieldProvenance) {
    elements.modalFieldProvenance.classList.remove('hidden');
  }
};

window.closeFieldProvenanceModal = function() {
  if (elements.modalFieldProvenance) {
    elements.modalFieldProvenance.classList.add('hidden');
  }
};

window.openDashboardExportModal = function() {
  if (elements.modalDashboardExport) {
    elements.modalDashboardExport.classList.remove('hidden');
  }
};

window.closeDashboardExportModal = function() {
  if (elements.modalDashboardExport) {
    elements.modalDashboardExport.classList.add('hidden');
  }
};

// -----------------------------------------------------------------------------
// Interactive Alternative Recommendations (Requirement 1)
// -----------------------------------------------------------------------------

let currentAltPreference = 'lowest_cost';
let isApplyingPreference = false;

function setupAlternativeRecommendations() {
  if (elements.btnToggleAltRecommendations && elements.altRecommendationsPanel) {
    elements.btnToggleAltRecommendations.addEventListener('click', () => {
      elements.altRecommendationsPanel.classList.toggle('hidden');
      if (!elements.altRecommendationsPanel.classList.contains('hidden')) {
        const preferred = elements.selPreferredCourier ? elements.selPreferredCourier.value : null;
        fetchAndRenderAlternativeRecommendations(currentAltPreference, preferred, false);
      }
    });
  }

  if (elements.btnCloseAltPanel && elements.altRecommendationsPanel) {
    elements.btnCloseAltPanel.addEventListener('click', () => {
      elements.altRecommendationsPanel.classList.add('hidden');
    });
  }

  // Preference Chips (5 choices: lowest_cost, fastest, balanced, preferred_courier, next_best)
  document.querySelectorAll('.btn-pref-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.btn-pref-chip').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentAltPreference = btn.getAttribute('data-pref');

      // If preferred_courier clicked and no carrier chosen, prompt user
      if (currentAltPreference === 'preferred_courier' && elements.selPreferredCourier && !elements.selPreferredCourier.value) {
        elements.selPreferredCourier.focus();
      }

      const preferred = elements.selPreferredCourier ? elements.selPreferredCourier.value : null;
      fetchAndRenderAlternativeRecommendations(currentAltPreference, preferred, false);
    });
  });

  // When courier select changes, automatically set preference to preferred_courier
  if (elements.selPreferredCourier) {
    elements.selPreferredCourier.addEventListener('change', () => {
      if (elements.selPreferredCourier.value) {
        currentAltPreference = 'preferred_courier';
        document.querySelectorAll('.btn-pref-chip').forEach(b => {
          b.classList.toggle('active', b.getAttribute('data-pref') === 'preferred_courier');
        });
        fetchAndRenderAlternativeRecommendations('preferred_courier', elements.selPreferredCourier.value, false);
      }
    });
  }

  // Apply Preference Button click handler
  if (elements.btnApplyAltPreference) {
    elements.btnApplyAltPreference.addEventListener('click', async () => {
      await applyPreferenceWorkflow();
    });
  }
}

async function applyPreferenceWorkflow() {
  if (isApplyingPreference) return;
  if (!state.activeOrder || !state.activeOrder.order_reference) {
    if (elements.altResultNotice) {
      elements.altResultNotice.textContent = 'Please submit shipment details to calculate courier rates first.';
      if (elements.altRecResultContainer) elements.altRecResultContainer.classList.remove('hidden');
    }
    return;
  }

  let prefMode = currentAltPreference || 'balanced';
  const preferredVal = elements.selPreferredCourier ? elements.selPreferredCourier.value : '';

  // If user selected a carrier in dropdown, ensure mode is preferred_courier
  if (preferredVal && prefMode !== 'preferred_courier') {
    prefMode = 'preferred_courier';
    currentAltPreference = 'preferred_courier';
    document.querySelectorAll('.btn-pref-chip').forEach(b => {
      b.classList.toggle('active', b.getAttribute('data-pref') === 'preferred_courier');
    });
  }

  if (prefMode === 'preferred_courier' && !preferredVal) {
    if (elements.altResultNotice) {
      elements.altResultNotice.textContent = 'Please choose a carrier from the dropdown to apply Preferred Courier preference.';
      if (elements.altRecResultContainer) elements.altRecResultContainer.classList.remove('hidden');
    }
    if (elements.selPreferredCourier) elements.selPreferredCourier.focus();
    return;
  }

  // Apply with loading state and duplicate prevention
  await fetchAndRenderAlternativeRecommendations(prefMode, preferredVal, true);
}

async function fetchAndRenderAlternativeRecommendations(prefMode, preferredCourier, applyAsWinner = false) {
  if (!state.activeOrder || !state.activeOrder.order_reference) {
    return;
  }

  if (applyAsWinner) {
    isApplyingPreference = true;
    if (elements.btnApplyAltPreference) {
      elements.btnApplyAltPreference.disabled = true;
      elements.btnApplyAltPreference.textContent = 'Applying Preference...';
    }
  }

  try {
    const payload = {
      order_reference: state.activeOrder.order_reference,
      current_recommended_quote_id: state.selectedQuoteId,
      preference_mode: prefMode || 'balanced',
      preferred_courier: preferredCourier || null,
      exclude_current: true,
    };

    const res = await fetch('/api/recommendations/alternative', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      const errMsg = errData.detail || 'Failed to process alternative recommendations.';
      if (elements.altResultNotice) {
        elements.altResultNotice.textContent = `Error: ${errMsg}`;
      }
      if (elements.altRecResultContainer) elements.altRecResultContainer.classList.remove('hidden');
      return;
    }

    const data = await res.json();
    const altRes = data.alternative_result;
    if (!altRes) return;

    if (elements.altRecResultContainer) {
      elements.altRecResultContainer.classList.remove('hidden');
    }

    if (elements.altResultNotice) {
      elements.altResultNotice.textContent = altRes.decision_explanation;
    }

    // Render candidate list with formatted INR prices and trade-offs
    renderAlternativeCandidates(altRes);

    // If "Apply Preference" button was clicked, synchronize recommendation to active UI
    if (applyAsWinner && altRes.selected_recommendation) {
      const selectedAlt = altRes.selected_recommendation;
      const newQuoteId = selectedAlt.quote_id;

      // 1. Update active selected quote
      state.selectedQuoteId = newQuoteId;
      selectCourier(newQuoteId);

      // 2. Synchronize Recommended Courier Card
      if (elements.advCourierName) elements.advCourierName.textContent = selectedAlt.courier_name;
      if (elements.advServiceTypeBadge) elements.advServiceTypeBadge.textContent = selectedAlt.service_type || 'Standard Service';
      if (elements.advWinningCost) elements.advWinningCost.textContent = formatINR(selectedAlt.total_cost);
      if (elements.advWinningSla) elements.advWinningSla.textContent = `${selectedAlt.estimated_delivery_days || '--'} Days SLA`;
      if (elements.recTurnaroundTime) elements.recTurnaroundTime.textContent = `${selectedAlt.estimated_delivery_days || '--'} Business Days`;

      // 3. Synchronize Explanation
      if (elements.advExplanation) {
        elements.advExplanation.textContent = altRes.decision_explanation;
      }

      // 4. Invalidate operator approval
      if (elements.chkOperatorConfirm) elements.chkOperatorConfirm.checked = false;
      if (elements.btnApproveDispatch) elements.btnApproveDispatch.disabled = true;
      state.isApprovalGranted = false;

      // 5. Update notice with renewed operator confirmation requirement
      if (elements.altResultNotice) {
        elements.altResultNotice.textContent = `Applied: ${altRes.decision_explanation} Note: Renewed operator confirmation is required before booking.`;
      }

      // 6. Synchronize ML Benchmark comparison for the new selected quote
      await fetchAndRenderMlPrediction();

      // 7. Refresh candidate list to reflect new selected quote
      renderAlternativeCandidates(altRes);
    }

  } catch (err) {
    console.error('Error fetching alternative recommendations:', err);
    if (elements.altResultNotice) {
      elements.altResultNotice.textContent = 'Network error while fetching alternative recommendations.';
    }
  } finally {
    if (applyAsWinner) {
      isApplyingPreference = false;
      if (elements.btnApplyAltPreference) {
        elements.btnApplyAltPreference.disabled = false;
        elements.btnApplyAltPreference.textContent = 'Apply Preference';
      }
    }
  }
}

function renderAlternativeCandidates(altRes) {
  if (!elements.altCandidatesList) return;
  elements.altCandidatesList.innerHTML = '';

  if (!altRes.eligible_alternatives || altRes.eligible_alternatives.length === 0) {
    elements.altCandidatesList.innerHTML = '<div style="font-size:12px; color:var(--text-muted); padding:8px;">No other eligible alternatives available for this route.</div>';
    return;
  }

  altRes.eligible_alternatives.forEach(alt => {
    const isCurrentSelected = alt.quote_id === state.selectedQuoteId;
    const card = document.createElement('div');
    card.className = `alt-candidate-card ${isCurrentSelected ? 'selected' : ''}`;
    card.innerHTML = `
      <div class="alt-candidate-info">
        <span class="alt-candidate-name">${alt.courier_name} (${alt.service_type})</span>
        <div style="font-size: 13px; font-weight: 700; color: var(--primary);">
          ${formatINR(alt.total_cost)} &bull; ${alt.estimated_delivery_days || '--'} Days SLA
        </div>
        <span class="alt-candidate-tradeoff">${alt.tradeoff_explanation}</span>
      </div>
      <button type="button" class="btn-sm ${isCurrentSelected ? 'btn-ghost' : 'btn-primary'}" onclick="applyAlternativeQuote('${alt.quote_id}')">
        ${isCurrentSelected ? 'Currently Selected' : 'Switch to this Courier'}
      </button>
    `;
    elements.altCandidatesList.appendChild(card);
  });
}

window.applyAlternativeQuote = async function(quoteId) {
  selectCourier(quoteId);

  // Invalidate operator approval
  if (elements.chkOperatorConfirm) elements.chkOperatorConfirm.checked = false;
  if (elements.btnApproveDispatch) elements.btnApproveDispatch.disabled = true;
  state.isApprovalGranted = false;

  // Find quote in active quotes to update Recommended Courier card
  const quote = state.activeQuotes.find(q => q.quote_id === quoteId);
  if (quote) {
    if (elements.advCourierName) elements.advCourierName.textContent = quote.courier_name;
    if (elements.advServiceTypeBadge) elements.advServiceTypeBadge.textContent = quote.service_type || 'Standard Service';
    if (elements.advWinningCost) elements.advWinningCost.textContent = formatINR(extractQuotePrice(quote));
    if (elements.advWinningSla) elements.advWinningSla.textContent = `${quote.estimated_delivery_days || '--'} Days SLA`;
    if (elements.recTurnaroundTime) elements.recTurnaroundTime.textContent = `${quote.estimated_delivery_days || '--'} Business Days`;
  }

  if (elements.altResultNotice) {
    elements.altResultNotice.textContent = `Switched to ${quote ? quote.courier_name : quoteId}. Renewed operator confirmation is required.`;
  }

  // Update candidates cards
  document.querySelectorAll('.alt-candidate-card').forEach(card => {
    const btn = card.querySelector('button');
    if (btn && btn.getAttribute('onclick') && btn.getAttribute('onclick').includes(quoteId)) {
      card.classList.add('selected');
      btn.className = 'btn-sm btn-ghost';
      btn.textContent = 'Currently Selected';
    } else if (btn) {
      card.classList.remove('selected');
      btn.className = 'btn-sm btn-primary';
      btn.textContent = 'Switch to this Courier';
    }
  });

  await fetchAndRenderMlPrediction();
};

// -----------------------------------------------------------------------------
// Real-time Verified Indian PIN Code Validation (Requirement 2)
// -----------------------------------------------------------------------------

async function validatePinField(pinInput, feedbackEl, role) {
  if (!pinInput || !feedbackEl) return true;
  const pin = pinInput.value.trim();

  if (!pin) {
    feedbackEl.className = 'pin-feedback-msg';
    feedbackEl.textContent = '';
    return false;
  }

  // Pure PIN format validation
  if (!/^\d{6}$/.test(pin)) {
    feedbackEl.className = 'pin-feedback-msg pin-feedback-invalid';
    if (!/^\d+$/.test(pin)) {
      feedbackEl.textContent = 'Invalid format: PIN must contain only 6 digits (no letters or symbols).';
    } else if (pin.length < 6) {
      feedbackEl.textContent = `Incomplete PIN: enter ${6 - pin.length} more digit(s).`;
    } else {
      feedbackEl.textContent = 'Invalid format: Indian PIN codes must have exactly 6 digits.';
    }
    return false;
  }

  if (pin.startsWith('0')) {
    feedbackEl.className = 'pin-feedback-msg pin-feedback-invalid';
    feedbackEl.textContent = 'Invalid PIN: Indian PIN codes never start with 0.';
    return false;
  }

  try {
    const sPin = document.getElementById('senderPin') ? document.getElementById('senderPin').value.trim() : '110001';
    const rPin = document.getElementById('recipientPin') ? document.getElementById('recipientPin').value.trim() : '400001';
    const sCity = document.getElementById('senderCity') ? document.getElementById('senderCity').value.trim() : '';
    const sState = document.getElementById('senderState') ? document.getElementById('senderState').value.trim() : '';
    const rCity = document.getElementById('recipientCity') ? document.getElementById('recipientCity').value.trim() : '';
    const rState = document.getElementById('recipientState') ? document.getElementById('recipientState').value.trim() : '';

    const res = await fetch('/api/serviceability', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        origin_pin: role === 'pickup' ? pin : sPin,
        destination_pin: role === 'delivery' ? pin : rPin,
        origin_city: sCity,
        origin_state: sState,
        destination_city: rCity,
        destination_state: rState,
      }),
    });

    if (!res.ok) return false;
    const data = await res.json();
    const evalData = role === 'pickup' ? data.origin_eval : data.destination_eval;

    if (!evalData) return true;

    if (evalData.status === 'INVALID_FORMAT') {
      feedbackEl.className = 'pin-feedback-msg pin-feedback-invalid';
      feedbackEl.textContent = evalData.message;
      return false;
    } else if (evalData.status === 'PIN_NOT_FOUND') {
      feedbackEl.className = 'pin-feedback-msg pin-feedback-invalid';
      feedbackEl.textContent = 'PIN code not found in verified Indian postal database.';
      return false;
    } else if (evalData.status === 'VALID_PIN_UNSERVICEABLE') {
      feedbackEl.className = 'pin-feedback-msg pin-feedback-warning';
      feedbackEl.textContent = `Valid PIN (${evalData.postal_info?.city || 'India'}), but no available couriers service this area.`;
      return true;
    } else {
      feedbackEl.className = 'pin-feedback-msg pin-feedback-valid';
      feedbackEl.textContent = `Verified Indian PIN: ${evalData.postal_info?.city || ''}, ${evalData.postal_info?.state || ''}`;
      if (evalData.address_conflict && evalData.conflict_message) {
        feedbackEl.className = 'pin-feedback-msg pin-feedback-warning';
        feedbackEl.textContent += ` — ${evalData.conflict_message}`;
      }
      return true;
    }
  } catch (err) {
    console.warn('Real-time PIN lookup failed:', err);
    return true;
  }
}

function setupPinLiveValidation() {
  if (elements.senderPin && elements.senderPinFeedback) {
    elements.senderPin.addEventListener('input', () => {
      validatePinField(elements.senderPin, elements.senderPinFeedback, 'pickup');
    });
    elements.senderPin.addEventListener('blur', () => {
      validatePinField(elements.senderPin, elements.senderPinFeedback, 'pickup');
    });
  }

  if (elements.recipientPin && elements.recipientPinFeedback) {
    elements.recipientPin.addEventListener('input', () => {
      validatePinField(elements.recipientPin, elements.recipientPinFeedback, 'delivery');
    });
    elements.recipientPin.addEventListener('blur', () => {
      validatePinField(elements.recipientPin, elements.recipientPinFeedback, 'delivery');
    });
  }
}
