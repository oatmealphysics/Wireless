let targets = [];
let selectedTargetId = null;
let actionDefs = {};
let scanPollTimer = null;
let statusRefreshTimer = null;
let eventSource = null;
let statusRequestSequence = 0;
let renderedTargetSignature = null;
let sseDisconnected = false;

async function init() {
  document.getElementById('btn-scan').addEventListener('click', startScan);
  document.getElementById('btn-clear').addEventListener('click', clearTargets);
  document.getElementById('target-items').addEventListener('keydown', handleTargetListKeydown);

  initLogViewer();
  initHwWidget();
  connectSSE();
  try {
    actionDefs = await fetchJson('/api/actions');
  } catch (error) {
    showToast(`Unable to load actions: ${error.message}`, 'error');
  }
  await refreshStatus(true);
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  let data = null;
  try {
    data = await response.json();
  } catch (error) {
    if (response.ok) throw new Error('Server returned an invalid response');
  }
  if (!response.ok) {
    throw new Error(data?.error || `Request failed (${response.status})`);
  }
  return data;
}

function parseEventData(event) {
  try {
    return JSON.parse(event.data);
  } catch (error) {
    showToast('Received an invalid live update', 'error');
    return null;
  }
}

function connectSSE() {
  if (eventSource) eventSource.close();
  eventSource = new EventSource('/api/events');
  eventSource.onopen = () => { sseDisconnected = false; };

  eventSource.addEventListener('target_found', (e) => {
    const data = parseEventData(e);
    if (!data) return;
    showToast(`Target found: ${data.name} [${data.protocol}]`, 'target');
    queueStatusRefresh();
  });

  eventSource.addEventListener('scanner_done', (e) => {
    const data = parseEventData(e);
    if (!data) return;
    queueStatusRefresh();
  });

  eventSource.addEventListener('scanner_error', (e) => {
    const data = parseEventData(e);
    if (!data) return;
    showToast(`${data.scanner} scanner error: ${data.error}`, 'error');
    queueStatusRefresh();
  });

  eventSource.addEventListener('scan_complete', (e) => {
    const data = parseEventData(e);
    if (!data) return;
    showToast(`Scan complete: ${data.target_count} target(s)`, 'target');
    queueStatusRefresh();
  });

  eventSource.addEventListener('scan_start', (e) => {
    if (!parseEventData(e)) return;
    queueStatusRefresh(0);
  });

  eventSource.addEventListener('targets_cleared', (e) => {
    if (!parseEventData(e)) return;
    queueStatusRefresh(0);
  });

  eventSource.addEventListener('action_executed', (e) => {
    const data = parseEventData(e);
    if (!data) return;
    const cls = data.status === 'error' ? 'error' : 'action';
    showToast(`${data.label}: ${data.status}`, cls);
    if (selectedTargetId && data.target_id === selectedTargetId) {
      refreshActionLog(selectedTargetId);
    }
  });

  eventSource.onerror = () => {
    if (!sseDisconnected) {
      sseDisconnected = true;
      showToast('Live updates disconnected; reconnecting...', 'error');
    }
  };
}

function queueStatusRefresh(delay = 50) {
  if (statusRefreshTimer) return;
  statusRefreshTimer = setTimeout(async () => {
    statusRefreshTimer = null;
    await refreshStatus();
  }, delay);
}

function showToast(msg, cls) {
  const feed = document.getElementById('event-feed');
  const toast = document.createElement('div');
  toast.className = `event-toast ${cls || ''}`;
  toast.textContent = msg;
  feed.prepend(toast);
  // Keep only last 5 toasts
  while (feed.children.length > 5) feed.removeChild(feed.lastChild);
  setTimeout(() => { if (toast.parentNode) toast.remove(); }, 6000);
}

async function refreshStatus(showErrors = false) {
  const requestSequence = ++statusRequestSequence;
  try {
    const data = await fetchJson('/api/status');
    if (requestSequence !== statusRequestSequence) return null;
    targets = Array.isArray(data.targets)
      ? data.targets.filter(target => target && typeof target === 'object') : [];

    const modeLabel = data.simulate ? 'SIMULATE MODE'
      : data.dry_run ? 'LIVE MODE (DRY-RUN)' : 'LIVE MODE';
    document.getElementById('mode-badge').textContent = modeLabel;

    updateScannerStatus(data.scanner_status || {});
    document.getElementById('target-count').textContent =
      `${data.target_count} target${data.target_count !== 1 ? 's' : ''}`;

    renderTargetList();

    const overlay = document.getElementById('scan-overlay');
    const scanButton = document.getElementById('btn-scan');
    const clearButton = document.getElementById('btn-clear');
    if (data.scanning) {
      overlay.hidden = false;
      overlay.setAttribute('aria-hidden', 'false');
      scanButton.disabled = true;
      clearButton.disabled = true;
      document.getElementById('scan-found').textContent =
        `${data.target_count} target(s) found so far`;
      scheduleScanPoll();
    } else {
      overlay.hidden = true;
      overlay.setAttribute('aria-hidden', 'true');
      scanButton.disabled = false;
      clearButton.disabled = false;
      stopScanPolling();
    }

    if (selectedTargetId) {
      const t = targets.find(t => t.id === selectedTargetId);
      if (t) {
        renderDetail(t);
      } else {
        resetDetail();
      }
    }
    return data;
  } catch (e) {
    console.error('Status fetch failed:', e);
    if (showErrors) showToast(`Unable to load status: ${e.message}`, 'error');
    return null;
  }
}

function stopScanPolling() {
  if (scanPollTimer) {
    clearTimeout(scanPollTimer);
    scanPollTimer = null;
  }
}

function scheduleScanPoll() {
  if (scanPollTimer) return;
  scanPollTimer = setTimeout(async () => {
    scanPollTimer = null;
    const data = await refreshStatus();
    if (data === null || data.scanning) scheduleScanPoll();
  }, 1000);
}

function updateScannerStatus(status) {
  for (const [name, state] of Object.entries(status)) {
    const el = document.getElementById(`scanner-${name}`);
    if (!el) continue;
    const safeState = ['idle', 'scanning', 'done', 'error'].includes(state) ? state : 'error';
    const dot = el.querySelector('.status-dot');
    if (dot) dot.className = 'status-dot ' + safeState;
    el.setAttribute('aria-label', `${name}: ${safeState}`);

    const modalEl = document.getElementById(`scan-${name}-status`);
    if (modalEl) {
      if (safeState === 'scanning') {
        modalEl.innerHTML = '<span class="spinner"></span>';
      } else if (safeState === 'done') {
        modalEl.innerHTML = '<span class="scan-state-done">Done</span>';
      } else if (safeState === 'error') {
        modalEl.innerHTML = '<span class="scan-state-error">Error</span>';
      } else {
        modalEl.innerHTML = '<span class="scan-state-idle">Idle</span>';
      }
    }
  }
}

async function startScan() {
  const overlay = document.getElementById('scan-overlay');
  const scanButton = document.getElementById('btn-scan');
  if (scanButton.disabled) return;
  stopScanPolling();
  overlay.hidden = false;
  overlay.setAttribute('aria-hidden', 'false');
  scanButton.disabled = true;
  ['wifi','ble','sdr','mavlink','telemetry'].forEach(n => {
    const el = document.getElementById(`scan-${n}-status`);
    if (el) el.innerHTML = '<span class="spinner"></span>';
  });
  document.getElementById('scan-found').textContent = '';

  try {
    await fetchJson('/api/scan', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: '{}',
    });
    const data = await refreshStatus(true);
    if (data === null || data.scanning) scheduleScanPoll();
  } catch (error) {
    overlay.hidden = true;
    overlay.setAttribute('aria-hidden', 'true');
    scanButton.disabled = false;
    showToast(`Unable to start scan: ${error.message}`, 'error');
  }
}

async function clearTargets() {
  try {
    await fetchJson('/api/clear', { method: 'POST' });
    targets = [];
    resetDetail();
    renderTargetList();
    document.getElementById('target-count').textContent = '0 targets';
  } catch (error) {
    showToast(`Unable to clear targets: ${error.message}`, 'error');
  }
}

function renderTargetList(focusTargetId) {
  const container = document.getElementById('target-items');
  const activeTargetId = focusTargetId !== undefined
    ? focusTargetId
    : document.activeElement?.classList?.contains('target-item')
      ? document.activeElement.dataset.targetId : null;
  if (targets.length === 0) {
    container.innerHTML = `
      <div class="empty-state target-list-empty">
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <circle cx="12" cy="12" r="10"/>
          <path d="M12 6v6l4 2"/>
        </svg>
        <p>No targets found. Click Scan to begin.</p>
      </div>`;
    if (activeTargetId) {
      document.getElementById('btn-scan')?.focus({preventScroll: true});
    }
    return;
  }

  container.innerHTML = targets.map(t => {
    const protocol = String(t?.protocol || 'unknown');
    const targetId = String(t?.id || '');
    const protoClass = 'proto-' + safeCssClass(protocol.replace(/_.*/, ''));
    const selected = t.id === selectedTargetId ? 'selected' : '';
    const signal = renderSignal(t.signal);
    const threatLevel = String(t.threat_level || 'LOW');
    const threatClass = safeCssClass(threatLevel);
    const vulnBadges = (Array.isArray(t.vulns) ? t.vulns : []).slice(0, 3).map(v =>
      `<span class="badge badge-${threatClass}">${escHtml(v)}</span>`
    ).join('');

    return `
      <button type="button" class="target-item ${selected}" data-target-id="${escAttr(targetId)}" aria-pressed="${selected ? 'true' : 'false'}" aria-current="${selected ? 'true' : 'false'}">
        <span class="target-item-top">
          <span class="target-name">${escHtml(t.name || 'Unnamed target')}</span>
          <span>
            <span class="target-protocol ${protoClass}">${escHtml(protocol)}</span>
            <span class="threat-indicator threat-${threatClass}">${escHtml(threatLevel)}</span>
          </span>
        </span>
        <span class="target-type">${escHtml(t.type || 'Unknown')} ${signal}</span>
        <span class="target-badges">${vulnBadges}</span>
      </button>`;
  }).join('');

  container.querySelectorAll('.target-item[data-target-id]').forEach(el => {
    el.addEventListener('click', () => selectTarget(el.dataset.targetId));
  });

  if (activeTargetId) {
    const activeButton = Array.from(container.querySelectorAll('.target-item'))
      .find(el => el.dataset.targetId === activeTargetId);
    const focusFallback = activeButton || container.querySelector('.target-item')
      || document.getElementById('btn-scan');
    focusFallback?.focus({preventScroll: true});
  }
}

function handleTargetListKeydown(event) {
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
  const items = Array.from(document.querySelectorAll('#target-items .target-item'));
  const currentIndex = items.indexOf(document.activeElement);
  if (currentIndex < 0 || items.length === 0) return;

  event.preventDefault();
  let nextIndex = currentIndex;
  if (event.key === 'ArrowDown') nextIndex = (currentIndex + 1) % items.length;
  if (event.key === 'ArrowUp') nextIndex = (currentIndex - 1 + items.length) % items.length;
  if (event.key === 'Home') nextIndex = 0;
  if (event.key === 'End') nextIndex = items.length - 1;
  selectTarget(items[nextIndex].dataset.targetId, true);
}

function renderSignal(dbm) {
  if (dbm == null || dbm === '') return '';
  const value = Number(dbm);
  if (!Number.isFinite(value)) return '';
  const abs = Math.abs(value);
  let cls = 'strong';
  if (abs > 70) cls = 'weak';
  else if (abs > 55) cls = 'medium';
  const bars = [1, 2, 3, 4].map(step =>
    `<span class="signal-step-${step}"></span>`
  ).join('');
  return `<span class="signal-bar ${cls}" aria-hidden="true">${bars}</span> ${escHtml(value)} dBm`;
}

function selectTarget(id, moveFocus = false) {
  selectedTargetId = id;
  renderedTargetSignature = null;
  const t = targets.find(t => t.id === id);
  if (t) {
    renderDetail(t);
    renderTargetList(moveFocus ? id : undefined);
  }
}

function resetDetail() {
  selectedTargetId = null;
  renderedTargetSignature = null;
  document.getElementById('detail-content').hidden = true;
  document.getElementById('detail-empty').hidden = false;
}

function renderDetail(t) {
  const signature = JSON.stringify(t);
  if (signature === renderedTargetSignature) return;
  renderedTargetSignature = signature;
  document.getElementById('detail-empty').hidden = true;
  const el = document.getElementById('detail-content');
  el.hidden = false;

  const details = t.details && typeof t.details === 'object' && !Array.isArray(t.details)
    ? t.details : {};
  const detailRows = Object.entries(details).map(([k, v]) => {
    let val = v;
    if (typeof v === 'object') val = JSON.stringify(v);
    if (typeof v === 'boolean') val = v ? 'Yes' : 'No';
    return `<span class="detail-label">${escHtml(k)}</span><span class="detail-value">${escHtml(String(val))}</span>`;
  }).join('');

  const vulns = (Array.isArray(t.vulns) ? t.vulns : []).map(v =>
    `<div class="vuln-tag">${escHtml(v)}</div>`
  ).join('');

  const categoryLabels = {simulated: 'Simulated', passive: 'Passive', active: 'Active'};
  const actions = (Array.isArray(t.available_actions) ? t.available_actions : []).map(a => {
    const def = actionDefs[a] || {};
    const sev = safeCssClass(def.severity || 'info');
    const cat = safeCssClass(def.category || 'active');
    const catLabel = categoryLabels[def.category] || 'Active';
    return `
      <button type="button" class="action-card severity-${sev}" data-action-target="${escAttr(t.id)}" data-action-name="${escAttr(a)}">
        <span class="action-label">${escHtml(def.label || a)} <span class="action-category cat-${cat}">${escHtml(catLabel)}</span></span>
        <span class="action-desc">${escHtml(def.description || '')}</span>
      </button>`;
  }).join('');

  const rawScore = Number(t.score);
  const score = Number.isFinite(rawScore) ? Math.min(100, Math.max(0, rawScore)) : 0;
  const scoreBand = score >= 80 ? 'critical' : score >= 60 ? 'high' : score >= 40 ? 'medium' : 'low';
  const signal = t.signal == null || t.signal === '' ? NaN : Number(t.signal);

  el.innerHTML = `
    <div class="detail-header">
      <div>
        <div class="detail-title">${escHtml(t.name || 'Unnamed target')}</div>
        <div class="detail-subtitle">${escHtml(t.type || 'Unknown')}</div>
      </div>
      <div class="detail-summary">
        <span class="threat-indicator detail-threat threat-${safeCssClass(t.threat_level || 'LOW')}">${escHtml(t.threat_level || 'LOW')}</span>
        <div class="detail-score">
          Score: ${score}/100
          <progress class="score-bar score-${scoreBand}" value="${score}" max="100">${score}%</progress>
        </div>
      </div>
    </div>

    <div class="detail-section">
      <h3>Properties</h3>
      <div class="detail-grid">
        <span class="detail-label">Protocol</span><span class="detail-value">${escHtml(t.protocol)}</span>
        <span class="detail-label">Encryption</span><span class="detail-value">${escHtml(t.encryption || 'Unknown')}</span>
        ${Number.isFinite(signal) ? `<span class="detail-label">Signal</span><span class="detail-value">${escHtml(signal)} dBm</span>` : ''}
        ${detailRows}
      </div>
    </div>

    ${vulns ? `
    <div class="detail-section">
      <h3>Vulnerabilities</h3>
      <div class="vuln-list">${vulns}</div>
    </div>` : ''}

    <div class="detail-section">
      <h3>Available Actions</h3>
      ${actions ? `<div class="actions-grid">${actions}</div>` : '<div class="empty-actions">No actions available</div>'}
      <div id="action-result" role="status" aria-live="polite" aria-atomic="true" aria-busy="false"></div>
    </div>

    <div class="detail-section log-panel">
      <h3>Action Log</h3>
      <div id="action-log-entries"></div>
    </div>`;

  el.querySelectorAll('.action-card[data-action-name]').forEach(card => {
    card.addEventListener('click', () => {
      executeAction(card.dataset.actionTarget, card.dataset.actionName, card);
    });
  });

  refreshActionLog(t.id);
}

async function executeAction(targetId, actionName, actionButton) {
  const resultEl = document.getElementById('action-result');
  if (!resultEl || actionButton?.disabled) return;
  if (actionButton) actionButton.disabled = true;
  resultEl.setAttribute('aria-busy', 'true');
  resultEl.innerHTML = '<div class="action-pending">Executing...</div>';

  try {
    const data = await fetchJson(`/api/targets/${encodeURIComponent(targetId)}/action`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ action: actionName }),
    });
    if (selectedTargetId === targetId && resultEl.isConnected) {
      const dataBlock = data.data
        ? `<div class="data-block"><pre>${escHtml(JSON.stringify(data.data, null, 2))}</pre></div>`
        : '';
      const cls = data.status === 'error' ? 'error' : '';
      resultEl.innerHTML = `
        <div class="action-result ${cls}">
          <strong>${escHtml(data.label || actionName)}</strong> [${escHtml(data.status)}]
          <br>${escHtml(data.result)}
          ${dataBlock}
        </div>`;
    }
  } catch(error) {
    if (selectedTargetId === targetId && resultEl.isConnected) {
      resultEl.innerHTML = `<div class="action-result error">Error: ${escHtml(error.message)}</div>`;
    }
  } finally {
    if (resultEl.isConnected) resultEl.setAttribute('aria-busy', 'false');
    if (actionButton?.isConnected) actionButton.disabled = false;
  }

  if (selectedTargetId === targetId) refreshActionLog(targetId);
}

async function refreshActionLog(targetId) {
  try {
    const log = await fetchJson('/api/log');
    if (selectedTargetId !== targetId || !Array.isArray(log)) return;
    const entries = log.filter(e => e.target_id === targetId).reverse().slice(0, 10);
    const el = document.getElementById('action-log-entries');
    if (!el) return;
    if (entries.length === 0) {
      el.innerHTML = '<div class="empty-log">No actions performed yet</div>';
      return;
    }
    el.innerHTML = entries.map(e => {
      const time = new Date(e.timestamp).toLocaleTimeString();
      const resultCls = e.status === 'error' ? 'error' : '';
      return `
        <div class="log-entry">
          <span class="log-time">${time}</span>
          <span class="log-action">${escHtml(e.label || e.action)}</span>
          <span class="log-result ${resultCls}">[${escHtml(e.status)}]</span>
        </div>`;
    }).join('');
  } catch(e) {}
}

function escHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]);
}

function escAttr(s) {
  return escHtml(s);
}

function safeCssClass(s) {
  return String(s || '').replace(/[^a-zA-Z0-9_-]/g, '');
}

// --- Hardware status widget ---

let hwData = null;

async function refreshHardware() {
  const indicators = {
    wifi: document.getElementById('hw-wifi-indicator'),
    ble: document.getElementById('hw-ble-indicator'),
    sdr: document.getElementById('hw-sdr-indicator'),
    mavlink: document.getElementById('hw-mav-indicator'),
  };

  for (const el of Object.values(indicators)) {
    const dot = el?.querySelector('.hw-dot');
    if (dot) dot.className = 'hw-dot checking';
  }

  try {
    hwData = await fetchJson('/api/hardware');
    updateHwIndicators(hwData, indicators);
    renderHwPopover(hwData);
  } catch (e) {
    for (const el of Object.values(indicators)) {
      const dot = el?.querySelector('.hw-dot');
      if (dot) dot.className = 'hw-dot unavailable';
    }
    document.getElementById('hw-popover-body').innerHTML =
      '<div class="hardware-error">Unable to detect hardware</div>';
  }
}

function updateHwIndicators(data, indicators) {
  const mapping = {
    wifi: data.wifi,
    ble: data.ble,
    sdr: data.sdr,
    mavlink: data.mavlink,
  };
  for (const [key, info] of Object.entries(mapping)) {
    const el = indicators[key];
    if (!el || !info) continue;
    const dot = el.querySelector('.hw-dot');
    const display = hardwareDisplayState(info, key);
    if (dot) dot.className = 'hw-dot ' + display.dotClass;
    el.setAttribute('aria-label', `${key}: ${display.text}`);
  }
}

function hardwareDisplayState(info, kind) {
  if (info?.status === 'not_checked') {
    return { dotClass: 'not-checked', text: 'Not checked' };
  }
  if (kind === 'mavlink' && info?.status === 'listener_ready') {
    return { dotClass: 'available', text: 'Listener ready' };
  }
  if (info?.available) {
    return { dotClass: 'available', text: 'Detected' };
  }
  return { dotClass: 'unavailable', text: 'Unavailable' };
}

function renderHwPopover(data) {
  const body = document.getElementById('hw-popover-body');
  if (!body) return;

  const simulationNotice = data.mode === 'simulation'
    ? '<div class="hw-popover-detail simulation-notice">Simulation mode — hardware probes disabled.</div>'
    : '';

  const rows = [
    { key: 'wifi', label: 'WiFi', info: data.wifi, detail: () => {
      const ifaces = data.wifi?.interfaces || [];
      return ifaces.length ? ifaces.join(', ') : (data.wifi?.note || data.wifi?.error || 'Not detected');
    }},
    { key: 'ble', label: 'Bluetooth LE', info: data.ble, detail: () => {
      const ifaces = data.ble?.interfaces || [];
      const note = data.ble?.note || '';
      return ifaces.length ? ifaces.join(', ') + (note ? ` (${note})` : '') : (note || data.ble?.error || 'Not detected');
    }},
    { key: 'sdr', label: 'SDR (SoapySDR)', info: data.sdr, detail: () => {
      const devs = data.sdr?.devices || [];
      return devs.length ? devs.join(', ') : (data.sdr?.note || data.sdr?.error || 'Not detected');
    }},
    { key: 'mavlink', label: 'MAVLink UDP', info: data.mavlink, detail: () => {
      const ports = data.mavlink?.bindable_ports || [];
      return ports.length ? `Ports: ${ports.join(', ')}` : (data.mavlink?.note || 'Available');
    }},
  ];

  body.innerHTML = simulationNotice + rows.map(r => {
    const display = hardwareDisplayState(r.info, r.key);
    return `
      <div class="hw-popover-row">
        <div>
          <div class="hw-popover-label">${escHtml(r.label)}</div>
          <div class="hw-popover-detail">${escHtml(r.detail())}</div>
        </div>
        <div class="hw-popover-status">
          <span class="hw-dot ${display.dotClass}"></span>
          <span class="hw-status-text ${display.dotClass}">${display.text}</span>
        </div>
      </div>`;
  }).join('');
}

function initHwWidget() {
  const wrapper = document.getElementById('hw-widget-wrap');
  const widget = document.getElementById('hw-widget');
  const popover = document.getElementById('hw-popover');
  const refreshBtn = document.getElementById('hw-refresh-btn');

  widget.addEventListener('click', (e) => {
    if (e.target === refreshBtn || refreshBtn.contains(e.target)) return;
    const isOpen = popover.classList.toggle('open');
    widget.setAttribute('aria-expanded', isOpen);
  });

  refreshBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    refreshHardware();
  });

  document.addEventListener('click', (e) => {
    if (!wrapper.contains(e.target)) {
      popover.classList.remove('open');
      widget.setAttribute('aria-expanded', 'false');
    }
  });

  refreshHardware();
}

// --- Collapsible log viewer ---

let logEntries = [];
const MAX_LOG_ENTRIES = 200;

function addLogEntry(type, message) {
  const entry = {
    time: new Date(),
    type: type,
    message: message,
  };
  logEntries.push(entry);
  if (logEntries.length > MAX_LOG_ENTRIES) {
    logEntries = logEntries.slice(-MAX_LOG_ENTRIES);
  }
  renderLogEntry(entry);
  updateLogCount();
}

function renderLogEntry(entry) {
  const body = document.getElementById('log-viewer-body');
  if (!body) return;
  const line = document.createElement('div');
  line.className = 'log-line';
  const ts = entry.time.toLocaleTimeString();
  const typeCls = safeCssClass(entry.type);
  line.innerHTML =
    `<span class="log-ts">${ts}</span>` +
    `<span class="log-type ${typeCls}">${escHtml(entry.type.replace(/_/g, ' '))}</span>` +
    `<span class="log-msg">${escHtml(entry.message)}</span>`;
  body.appendChild(line);
  body.scrollTop = body.scrollHeight;
}

function updateLogCount() {
  const countEl = document.getElementById('log-viewer-count');
  if (countEl) countEl.textContent = logEntries.length;
}

function clearLog() {
  logEntries = [];
  const body = document.getElementById('log-viewer-body');
  if (body) body.innerHTML = '';
  updateLogCount();
}

function initLogViewer() {
  const viewer = document.getElementById('log-viewer');
  const header = document.getElementById('log-viewer-header');
  const clearBtn = document.getElementById('log-clear-btn');

  header.addEventListener('click', (e) => {
    if (e.target === clearBtn || clearBtn.contains(e.target)) return;
    const isExpanded = viewer.classList.contains('expanded');
    viewer.classList.toggle('collapsed', isExpanded);
    viewer.classList.toggle('expanded', !isExpanded);
    header.setAttribute('aria-expanded', !isExpanded);
  });

  header.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      header.click();
    }
  });

  clearBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    clearLog();
  });

  addLogEntry('info', 'Dashboard initialized');
}

// Patch connectSSE to also feed the log viewer
const _originalConnectSSE = connectSSE;
connectSSE = function() {
  _originalConnectSSE();

  const sseLogEvents = [
    'target_found', 'scanner_done', 'scanner_error',
    'scan_complete', 'scan_start', 'targets_cleared', 'action_executed',
  ];

  for (const eventType of sseLogEvents) {
    eventSource.addEventListener(eventType, (e) => {
      let data;
      try { data = JSON.parse(e.data); } catch { return; }
      const msg = formatLogMessage(eventType, data);
      if (msg) addLogEntry(eventType, msg);
    });
  }

  eventSource.addEventListener('open', () => {
    addLogEntry('info', 'Connected to live event stream');
  });
};

function formatLogMessage(type, data) {
  switch (type) {
    case 'target_found':
      return `Target discovered: ${data.name || 'Unknown'} [${data.protocol || ''}]`;
    case 'scanner_done':
      return `${data.scanner || 'Scanner'} scan finished` + (data.count != null ? ` (${data.count} found)` : '');
    case 'scanner_error':
      return `${data.scanner || 'Scanner'} error: ${data.error || 'unknown'}`;
    case 'scan_complete':
      return `Scan complete — ${data.target_count || 0} target(s) found [${data.mode || ''}]`;
    case 'scan_start':
      return `Scan started [${data.mode || ''}]` + (data.protocols ? ` protocols: ${data.protocols.join(', ')}` : '');
    case 'targets_cleared':
      return 'All targets cleared';
    case 'action_executed':
      return `${data.label || data.action || 'Action'}: ${data.status || 'done'}` + (data.target_name ? ` on ${data.target_name}` : '');
    default:
      return JSON.stringify(data);
  }
}

init();
