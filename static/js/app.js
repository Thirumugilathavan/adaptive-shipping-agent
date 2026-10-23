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
  testRuns: [],
};

// DOM Elements
const elements = {
  tabButtons: document.querySelectorAll('.tab-btn'),
  tabPanes: document.querySelectorAll('.tab-pane'),
  providerSelect: document.getElementById('providerModeSelect'),

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
  btnBackToStep1: document.getElementById('btnBackToStep1'),
  btnNavBackTo1: document.getElementById('btnNavBackTo1'),
  btnProceedToApproval: document.getElementById('btnProceedToApproval'),
  advisorCard: document.getElementById('advisorCard'),
  advCourierName: document.getElementById('advCourierName'),
  advExplanation: document.getElementById('advExplanation'),
  advTradeoffBox: document.getElementById('advTradeoffBox'),
  advTradeoffList: document.getElementById('advTradeoffList'),
  rateCountBadge: document.getElementById('rateCountBadge'),
  courierCardsList: document.getElementById('courierCardsList'),

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

  // Evaluator
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
  setupTabs();
  setupWizard();
  setupLiveMeasurements();
  setupFormHandlers();
  setupApprovalGate();
  setupEvaluatorTab();
  setupPackagingTab();
  setupAuditTab();
  setupModal();
  setupMlPrediction();
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

  // Scroll smoothly to top of wizard container
  const container = document.querySelector('.wizard-container');
  if (container) {
    container.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

// -----------------------------------------------------------------------------
// Tab Switching
// -----------------------------------------------------------------------------

function setupTabs() {
  elements.tabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      const targetTabId = btn.getAttribute('data-tab');
      
      elements.tabButtons.forEach(b => b.classList.remove('active'));
      elements.tabPanes.forEach(p => p.classList.remove('active'));

      btn.classList.add('active');
      const targetPane = document.getElementById(targetTabId);
      if (targetPane) {
        targetPane.classList.add('active');
      }

      if (targetTabId === 'tab-evaluator') {
        loadScenarios();
        loadTestRuns();
      } else if (targetTabId === 'tab-packaging') {
        loadCustomPackages();
      } else if (targetTabId === 'tab-audit') {
        loadAuditLogs();
      }
    });
  });

  elements.providerSelect.addEventListener('change', (e) => {
    state.providerMode = e.target.value;
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
  state.activeQuotes = data.quotes;
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

  // 2. Advisor Recommendation
  if (data.advisor && data.advisor.recommended_quote) {
    const rec = data.advisor.recommended_quote;
    elements.advCourierName.textContent = `${rec.courier_name} (${rec.service_type})`;
    elements.advExplanation.textContent = data.advisor.explanation || 'Selected as best candidate based on your shipping criteria.';

    // Tradeoffs
    if (data.advisor.tradeoffs && data.advisor.tradeoffs.length > 0) {
      elements.advTradeoffBox.classList.remove('hidden');
      elements.advTradeoffList.innerHTML = data.advisor.tradeoffs.map(t => `<li>${t}</li>`).join('');
    } else {
      elements.advTradeoffBox.classList.add('hidden');
    }
  }

  // 3. Render Courier Cards
  elements.rateCountBadge.textContent = `${data.quotes.length} Couriers Evaluated`;
  elements.courierCardsList.innerHTML = '';

  state.selectedQuoteId = data.selected_quote_id || (data.quotes[0] ? data.quotes[0].quote_id : null);

  data.quotes.forEach(quote => {
    const isSelected = quote.quote_id === state.selectedQuoteId;
    const isWinner = data.ranking?.selected_winner?.quote?.quote_id === quote.quote_id;

    const card = document.createElement('div');
    card.className = `courier-card ${isSelected ? 'selected' : ''}`;
    card.setAttribute('data-id', quote.quote_id);

    const costStr = quote.cost_breakdown ? `INR ${quote.cost_breakdown.total_cost.toFixed(2)}` : 'N/A';
    const slabKg = quote.weight_result ? `${quote.weight_result.billing_slab_kg.toFixed(2)} kg` : '0.50 kg';

    card.innerHTML = `
      <div class="courier-card-header">
        <div class="courier-info">
          <div class="courier-name-row">
            <span class="courier-name">${quote.courier_name}</span>
            ${isWinner ? '<span class="badge-recommendation">Lowest Cost</span>' : ''}
            <span class="courier-mode-badge">${quote.service_type}</span>
          </div>
          <div class="courier-meta">
            <span>Estimated Delivery: <strong>${quote.estimated_delivery_days || 3} Business Days</strong></span>
            <span>Billing Slab: <strong>${slabKg}</strong></span>
            ${quote.is_serviceable === false ? '<span class="text-danger">Unserviceable Route</span>' : ''}
          </div>
        </div>
        <div class="courier-pricing">
          <div class="total-cost">${costStr}</div>
          <span class="breakdown-toggle" onclick="toggleBreakdown('${quote.quote_id}', event)">View Fee Breakdown</span>
        </div>
      </div>
    `;

    // Fee breakdown drawer
    const drawer = document.createElement('div');
    drawer.className = 'breakdown-drawer hidden';
    drawer.id = `drawer-${quote.quote_id}`;
    if (quote.cost_breakdown) {
      drawer.innerHTML = `
        <div class="breakdown-row"><span>Base Freight:</span> <strong>INR ${quote.cost_breakdown.base_freight.toFixed(2)}</strong></div>
        <div class="breakdown-row"><span>Fuel Surcharge (${quote.cost_breakdown.fuel_surcharge_rate_pct}%):</span> <strong>INR ${quote.cost_breakdown.fuel_surcharge.toFixed(2)}</strong></div>
        <div class="breakdown-row"><span>COD Handling Fee:</span> <strong>INR ${quote.cost_breakdown.cod_fee.toFixed(2)}</strong></div>
        <div class="breakdown-row"><span>GST / Tax (${quote.cost_breakdown.tax_rate_pct}%):</span> <strong>INR ${quote.cost_breakdown.tax_amount.toFixed(2)}</strong></div>
        <div class="breakdown-row" style="border-top:1px solid #cbd5e1; margin-top:6px; padding-top:6px;">
          <span>Total Landed Cost:</span> <strong style="color:#1e3a8a;">INR ${quote.cost_breakdown.total_cost.toFixed(2)}</strong>
        </div>
      `;
    }
    card.appendChild(drawer);

    card.addEventListener('click', (e) => {
      if (e.target.classList.contains('breakdown-toggle')) return;
      selectCourier(quote.quote_id);
    });

    elements.courierCardsList.appendChild(card);
  });

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
// Evaluator 10-Scenario Lab
// -----------------------------------------------------------------------------

function setupEvaluatorTab() {
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
      await loadTestRuns();
    }
  });
}

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
    const card = document.createElement('div');
    card.className = 'scenario-card';
    card.innerHTML = `
      <div>
        <div style="display:flex;align-items:center;">
          <span class="scenario-badge">${sc.id}</span>
          <span class="scenario-status-pill hidden" id="status-pill-${sc.id}">NOT RUN</span>
        </div>
        <h4 class="scenario-title">${sc.title}</h4>
        <p class="scenario-desc">${sc.description}</p>
      </div>
      <div class="scenario-actions">
        <button class="btn-primary btn-sm" onclick="loadScenarioIntoForm('${sc.id}')">Load into Form</button>
        <button class="btn-outline btn-sm" onclick="runScenario('${sc.id}')">Run Test</button>
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
  try {
    const res = await fetch(`/api/scenarios/${scId}/run`, { method: 'POST' });
    const data = await res.json();
    
    // Update pill
    const pill = document.getElementById(`status-pill-${scId}`);
    if (pill) {
      pill.classList.remove('hidden', 'status-passed', 'status-failed');
      const isPassed = data.status === 'PASSED';
      pill.classList.add(isPassed ? 'status-passed' : 'status-failed');
      pill.textContent = data.status;
    }

    await loadTestRuns();
    return data;
  } catch (err) {
    alert(`Error executing scenario ${scId}: ${err.message}`);
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
    const res = await fetch('/api/v1/predictions/shipment-price', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      elements.mlPredictedPrice.textContent = 'Unavailable';
      elements.mlConfidenceInterval.textContent = 'Model error';
      return;
    }

    const data = await res.json();
    const currSym = data.currency === 'USD' ? '$' : '₹';

    if (data.predicted_price !== null && data.predicted_price !== undefined) {
      elements.mlPredictedPrice.textContent = `${currSym} ${data.predicted_price.toFixed(2)}`;
    } else {
      elements.mlPredictedPrice.textContent = 'N/A';
    }

    if (data.confidence_interval && data.confidence_interval.length === 2) {
      elements.mlConfidenceInterval.textContent = `90% CI: ${currSym} ${data.confidence_interval[0].toFixed(2)} – ${currSym} ${data.confidence_interval[1].toFixed(2)}`;
    } else {
      elements.mlConfidenceInterval.textContent = 'CI: Not Available';
    }

    // Variance badge & analysis
    if (data.quote_comparison) {
      const qc = data.quote_comparison;
      let badgeClass = 'badge-neutral';

      if (qc.verdict === 'CONGRUENT') {
        badgeClass = 'badge-congruent';
      } else if (qc.verdict === 'COMPETITIVE_DISCOUNT') {
        badgeClass = 'badge-discount';
      } else if (qc.verdict === 'PREMIUM_OVERRUN') {
        badgeClass = 'badge-overrun';
      }

      const sign = qc.percentage_difference > 0 ? '+' : '';
      elements.mlVarianceBadge.innerHTML = `
        <span class="badge-variance ${badgeClass}">
          ${qc.verdict.replace('_', ' ')} (${sign}${qc.percentage_difference}%)
        </span>
      `;
      elements.mlVarianceDesc.textContent = qc.analysis;
    } else {
      elements.mlVarianceBadge.innerHTML = `<span class="badge-variance badge-neutral">No Active Quote</span>`;
      elements.mlVarianceDesc.textContent = 'Select or compute carrier quotes to view comparison variance.';
    }

    // Advice & action
    elements.mlAdviceText.textContent = data.agent_advice || data.explanation;
    elements.mlRecommendedAction.textContent = `Action: ${data.recommended_action || 'Review live rates'}`;

    // Meta chips
    elements.mlMetaModel.textContent = `Model: ${data.model_name}`;
    elements.mlMetaVersion.textContent = `v${data.model_version}`;
    elements.mlMetaSchema.textContent = `Schema v${data.feature_schema_version}`;

    // Limitations
    if (data.limitations && data.limitations.length > 0) {
      elements.mlLimitationsList.innerHTML = data.limitations.map(l => `<li>${l}</li>`).join('');
    }
  } catch (err) {
    console.warn('ML Prediction lookup error:', err);
    elements.mlPredictedPrice.textContent = 'Error';
    elements.mlConfidenceInterval.textContent = err.message;
  }
}
