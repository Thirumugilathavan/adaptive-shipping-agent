/**
 * Adaptive Shipping Intelligence Agent — Interactive Frontend Application.
 * Communicates with FastAPI REST backend for rates, 10-scenario tests,
 * human approval gate, dispatch, label generation, and audit trail.
 */

// Global Application State
const state = {
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
  shippingForm: document.getElementById('shippingForm'),
  btnResetForm: document.getElementById('btnResetForm'),
  btnFindCouriers: document.getElementById('btnFindCouriers'),
  
  // Inputs
  dimLength: document.getElementById('dimLength'),
  dimWidth: document.getElementById('dimWidth'),
  dimHeight: document.getElementById('dimHeight'),
  dimWeight: document.getElementById('dimWeight'),
  paymentMode: document.getElementById('paymentMode'),
  codGroup: document.getElementById('codGroup'),
  codAmount: document.getElementById('codAmount'),
  
  // Volumetric banner
  valVolume: document.getElementById('valVolume'),
  valVolWeight: document.getElementById('valVolWeight'),
  valBillWeight: document.getElementById('valBillWeight'),

  // Results
  emptyResultsState: document.getElementById('emptyResultsState'),
  resultsContainer: document.getElementById('resultsContainer'),
  advCourierName: document.getElementById('advCourierName'),
  advExplanation: document.getElementById('advExplanation'),
  advTradeoffBox: document.getElementById('advTradeoffBox'),
  advTradeoffList: document.getElementById('advTradeoffList'),
  rateCountBadge: document.getElementById('rateCountBadge'),
  courierCardsList: document.getElementById('courierCardsList'),

  // Approval Gate
  approvalGatePanel: document.getElementById('approvalGatePanel'),
  approvalSummary: document.getElementById('approvalSummary'),
  chkOperatorConfirm: document.getElementById('chkOperatorConfirm'),
  btnApproveDispatch: document.getElementById('btnApproveDispatch'),

  // Shipment Ready & Label
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
};

// -----------------------------------------------------------------------------
// Initialization
// -----------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
  setupTabs();
  setupLiveMeasurements();
  setupFormHandlers();
  setupApprovalGate();
  setupEvaluatorTab();
  setupPackagingTab();
  setupAuditTab();
  setupModal();
  updateLiveVolumetric();
});

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
      document.getElementById(targetTabId).classList.add('active');

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
    input.addEventListener('input', updateLiveVolumetric);
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
    elements.resultsContainer.classList.add('hidden');
    elements.emptyResultsState.classList.remove('hidden');
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

  elements.valVolume.textContent = `${volume.toLocaleString()} cm³`;
  elements.valVolWeight.textContent = `${volWeight.toFixed(2)} kg`;
  elements.valBillWeight.textContent = `${billable.toFixed(2)} kg`;
}

// -----------------------------------------------------------------------------
// Rate Shopping Form Submission
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
  elements.btnFindCouriers.textContent = '⏳ Querying Couriers & Calculating Slabs...';

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
      alert(`⚠️ Error:\n${errMsg}`);
      return;
    }

    renderRateResults(data);
  } catch (err) {
    alert(`Failed to calculate rates: ${err.message}`);
  } finally {
    elements.btnFindCouriers.disabled = false;
    elements.btnFindCouriers.textContent = '🔍 Find & Compare Couriers';
  }
}

function renderRateResults(data) {
  state.activeOrder = data.order;
  state.activeQuotes = data.quotes;
  state.rankingResult = data.ranking;
  state.advisorResult = data.advisor;

  elements.emptyResultsState.classList.add('hidden');
  elements.resultsContainer.classList.remove('hidden');
  elements.shipmentReadyPanel.classList.add('hidden');

  // 1. Advisor Recommendation
  if (data.advisor && data.advisor.recommended_quote) {
    const rec = data.advisor.recommended_quote;
    elements.advCourierName.textContent = `${rec.courier_name} (${rec.service_type})`;
    elements.advExplanation.textContent = data.advisor.explanation || 'Selected as best candidate.';

    // Tradeoffs
    if (data.advisor.tradeoffs && data.advisor.tradeoffs.length > 0) {
      elements.advTradeoffBox.classList.remove('hidden');
      elements.advTradeoffList.innerHTML = data.advisor.tradeoffs.map(t => `<li>${t}</li>`).join('');
    } else {
      elements.advTradeoffBox.classList.add('hidden');
    }
  }

  // 2. Render Courier Cards
  elements.rateCountBadge.textContent = `${data.quotes.length} Couriers Evaluated`;
  elements.courierCardsList.innerHTML = '';

  state.selectedQuoteId = data.selected_quote_id || (data.quotes[0] ? data.quotes[0].quote_id : null);

  data.quotes.forEach(quote => {
    const isSelected = quote.quote_id === state.selectedQuoteId;
    const isWinner = data.ranking?.selected_winner?.quote?.quote_id === quote.quote_id;

    const card = document.createElement('div');
    card.className = `courier-card ${isSelected ? 'selected' : ''}`;
    card.setAttribute('data-id', quote.quote_id);

    const costStr = quote.cost_breakdown ? `₹ ${quote.cost_breakdown.total_cost.toFixed(2)}` : 'N/A';
    const slabKg = quote.weight_result ? `${quote.weight_result.billing_slab_kg.toFixed(2)} kg` : '0.5 kg';

    card.innerHTML = `
      <div class="courier-info">
        <div class="courier-name-row">
          <span class="courier-name">${quote.courier_name}</span>
          ${isWinner ? '<span class="badge-recommendation">Lowest Cost</span>' : ''}
          <span class="courier-mode-badge">${quote.service_type}</span>
        </div>
        <div class="courier-meta">
          <span>Est. Delivery: <strong>${quote.estimated_delivery_days || 3} Days</strong></span>
          <span>• Billed Weight: <strong>${slabKg}</strong></span>
          ${quote.is_serviceable === false ? '<span class="text-danger">• Unserviceable</span>' : ''}
        </div>
      </div>
      <div class="courier-pricing">
        <div class="total-cost">${costStr}</div>
        <span class="breakdown-toggle" onclick="toggleBreakdown('${quote.quote_id}', event)">View Fee Breakdown ▾</span>
      </div>
    `;

    // Fee breakdown drawer
    const drawer = document.createElement('div');
    drawer.className = 'breakdown-drawer hidden';
    drawer.id = `drawer-${quote.quote_id}`;
    if (quote.cost_breakdown) {
      drawer.innerHTML = `
        <div class="breakdown-row"><span>Base Freight:</span> <strong>₹ ${quote.cost_breakdown.base_freight.toFixed(2)}</strong></div>
        <div class="breakdown-row"><span>Fuel Surcharge:</span> <strong>₹ ${quote.cost_breakdown.fuel_surcharge.toFixed(2)}</strong></div>
        <div class="breakdown-row"><span>COD Handling Fee:</span> <strong>₹ ${quote.cost_breakdown.cod_fee.toFixed(2)}</strong></div>
        <div class="breakdown-row"><span>GST / Tax (18%):</span> <strong>₹ ${quote.cost_breakdown.tax_amount.toFixed(2)}</strong></div>
        <div class="breakdown-row" style="border-top:1px solid #374151; margin-top:4px; padding-top:4px;">
          <span>Total Landed Cost:</span> <strong style="color:#38bdf8;">₹ ${quote.cost_breakdown.total_cost.toFixed(2)}</strong>
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

  updateApprovalSummary();
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
  updateApprovalSummary();
}

// -----------------------------------------------------------------------------
// Human Approval Gate & Dispatch
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
    <div><strong>Route:</strong> PIN ${order.sender.pin_code} ➔ PIN ${order.recipient.pin_code}</div>
    <div><strong>Selected Courier:</strong> ${quote.courier_name} (${quote.service_type})</div>
    <div><strong>Total Landed Cost:</strong> ₹ ${cost} (${order.is_cod ? 'Cash on Delivery: ₹ ' + order.cod_amount : 'Prepaid'})</div>
    <div><strong>Chargeable Weight:</strong> ${order.parcel_weight_kg.toFixed(2)} kg (Dims: ${order.dimensions.length_cm}×${order.dimensions.width_cm}×${order.dimensions.height_cm} cm)</div>
  `;

  elements.chkOperatorConfirm.checked = false;
  elements.btnApproveDispatch.disabled = true;
}

async function executeDispatch() {
  const quote = state.activeQuotes.find(q => q.quote_id === state.selectedQuoteId);
  const order = state.activeOrder;
  if (!quote || !order) return;

  elements.btnApproveDispatch.disabled = true;
  elements.btnApproveDispatch.textContent = '⏳ Dispatching with Carrier & Generating AWB...';

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
      alert(`⛔ Dispatch Failed: ${data.detail || 'Unknown error'}`);
      return;
    }

    renderShipmentReady(data);
  } catch (err) {
    alert(`Dispatch Network Error: ${err.message}`);
  } finally {
    elements.btnApproveDispatch.disabled = false;
    elements.btnApproveDispatch.textContent = '🚀 Approve and Create Shipment';
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
    showModal('📄 Fullscreen Shipping Label Preview', `
      <div style="display:flex;justify-content:center;background:#fff;padding:20px;border-radius:8px;">
        ${data.label_html_preview}
      </div>
    `);
  };

  // Scroll smoothly down to the ready panel
  elements.shipmentReadyPanel.scrollIntoView({ behavior: 'smooth' });
}

// -----------------------------------------------------------------------------
// Evaluator 10-Scenario Lab
// -----------------------------------------------------------------------------

function setupEvaluatorTab() {
  elements.btnRunAllScenarios.addEventListener('click', async () => {
    elements.btnRunAllScenarios.disabled = true;
    elements.btnRunAllScenarios.textContent = '⏳ Running All 10 Scenarios...';
    try {
      for (const sc of state.scenarios) {
        await runScenario(sc.id);
      }
      await loadTestRuns();
    } finally {
      elements.btnRunAllScenarios.disabled = false;
      elements.btnRunAllScenarios.textContent = '▶️ Run All 10 Scenarios';
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
        <button class="btn-primary btn-sm" onclick="loadScenarioIntoForm('${sc.id}')">🚀 Load into Form</button>
        <button class="btn-outline btn-sm" onclick="runScenario('${sc.id}')">🧪 Run Test</button>
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

    // Switch to rates tab and populate
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
        <td><button class="btn-ghost btn-sm" onclick="viewEvidence(${run.run_id})">🔍 Evidence</button></td>
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
    <div style="font-family:var(--font-mono);font-size:12px;background:#0f172a;padding:14px;border-radius:6px;max-height:400px;overflow:auto;">
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
          <span class="text-muted" style="font-size:12px;">Outer: ${pkg.outer_length_cm}×${pkg.outer_width_cm}×${pkg.outer_height_cm} cm | Tare: ${pkg.tare_weight_kg} kg</span>
        </div>
        <button class="btn-ghost btn-sm text-danger" onclick="deletePackage('${pkg.packaging_id}')">🗑️ Delete</button>
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
        <td><span style="font-size:11px;color:#94a3b8;">${l.details_json.substring(0, 80)}...</span></td>
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
