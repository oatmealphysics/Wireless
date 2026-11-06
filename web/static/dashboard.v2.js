let targets = [];
let selectedTargetId = null;
let actionDefs = {};
let packsList = [];
let activeScopes = [];
let scopeCountdownTimer = null;
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

  initTheme();
  initLogViewer();
  initHwWidget();
  initSSEBanner();
  initPacksPanel();
  initArmDialog();
  initDestructiveConfirm();
  initEvidenceViewer();
  initResultStateDisplay();
  connectSSE();
  try {
    const packsResponse = await fetchJson('/api/actions');
    actionDefs = {};
    if (Array.isArray(packsResponse)) {
      for (const pack of packsResponse) {
        for (const action of (pack.actions || [])) {
          if (action.name) actionDefs[action.name] = action;
        }
      }
    }
  } catch (error) {
    showToast(`Unable to load actions: ${error.message}`, 'error');
  }
  await refreshStatus(true);
  refreshScopes();
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

  eventSource.onopen = () => {
    sseDisconnected = false;
    setSSEBanner(false);
    addLogEntry('info', 'Connected to live event stream');
  };

  eventSource.addEventListener('target_found', (e) => {
    const data = parseEventData(e);
    if (!data) return;
    showToast(`Target found: ${data.name} [${data.protocol}]`, 'target');
    addLogEntry('target_found', formatLogMessage('target_found', data));
    queueStatusRefresh();
  });

  eventSource.addEventListener('scanner_done', (e) => {
    const data = parseEventData(e);
    if (!data) return;
    addLogEntry('scanner_done', formatLogMessage('scanner_done', data));
    queueStatusRefresh();
  });

  eventSource.addEventListener('scanner_error', (e) => {
    const data = parseEventData(e);
    if (!data) return;
    showToast(`${data.scanner} scanner error: ${data.error}`, 'error');
    addLogEntry('scanner_error', formatLogMessage('scanner_error', data));
    queueStatusRefresh();
  });

  eventSource.addEventListener('scan_complete', (e) => {
    const data = parseEventData(e);
    if (!data) return;
    showToast(`Scan complete: ${data.target_count} target(s)`, 'target');
    addLogEntry('scan_complete', formatLogMessage('scan_complete', data));
    queueStatusRefresh();
  });

  eventSource.addEventListener('scan_start', (e) => {
    const data = parseEventData(e);
    if (!data) return;
    addLogEntry('scan_start', formatLogMessage('scan_start', data));
    queueStatusRefresh(0);
  });

  eventSource.addEventListener('targets_cleared', (e) => {
    const data = parseEventData(e);
    if (!data) return;
    addLogEntry('targets_cleared', formatLogMessage('targets_cleared', data));
    queueStatusRefresh(0);
  });

  eventSource.addEventListener('action_executed', (e) => {
    const data = parseEventData(e);
    if (!data) return;
    const cls = data.status === 'error' ? 'error' : 'action';
    showToast(`${data.label}: ${data.status}`, cls);
    addLogEntry('action_executed', formatLogMessage('action_executed', data));
    if (selectedTargetId && data.target_id === selectedTargetId) {
      refreshActionLog(selectedTargetId);
    }
  });

  eventSource.addEventListener('result_state_transition', (e) => {
    const data = parseEventData(e);
    if (!data) return;
    addResultStateEntry(data);
    addLogEntry('result_state', `${data.pack_name}: ${data.state} on ${data.target_id}`);
  });

  eventSource.addEventListener('scope_armed', (e) => {
    const data = parseEventData(e);
    if (!data) return;
    showToast(`Armed: ${data.pack_name} on ${data.target_identity}`, 'action');
    addLogEntry('scope_armed', `Armed ${data.pack_name} v${data.pack_version} on ${data.target_identity}`);
    refreshScopes();
  });

  eventSource.addEventListener('scope_disarmed', (e) => {
    const data = parseEventData(e);
    if (!data) return;
    showToast(`Disarmed: ${data.pack_name} on ${data.target_identity}`, 'action');
    addLogEntry('scope_disarmed', `Disarmed ${data.pack_name} v${data.pack_version} on ${data.target_identity}`);
    refreshScopes();
  });

  eventSource.onerror = () => {
    if (!sseDisconnected) {
      sseDisconnected = true;
      setSSEBanner(true);
      addLogEntry('error', 'Live updates disconnected; reconnecting...');
    }
  };
}

function initSSEBanner() {
  const retryBtn = document.getElementById('sse-retry-btn');
  if (retryBtn) {
    retryBtn.addEventListener('click', () => connectSSE());
  }
}

function setSSEBanner(show) {
  const banner = document.getElementById('sse-banner');
  if (banner) banner.classList.toggle('visible', show);
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

// --- Theme toggle (R6.3) ---

function initTheme() {
  const toggle = document.getElementById('theme-toggle');
  if (!toggle) return;

  let saved = null;
  try { saved = localStorage.getItem('drone-scanner-theme'); } catch {}

  if (saved === 'dark' || saved === 'light') {
    document.documentElement.setAttribute('data-theme', saved);
  }

  updateThemeIcon(toggle);

  toggle.addEventListener('click', () => {
    const current = document.documentElement.getAttribute('data-theme');
    const isDark = current === 'dark' ||
      (!current && window.matchMedia('(prefers-color-scheme: dark)').matches);
    const next = isDark ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('drone-scanner-theme', next); } catch {}
    updateThemeIcon(toggle);
  });
}

function updateThemeIcon(toggle) {
  const theme = document.documentElement.getAttribute('data-theme');
  const isDark = theme === 'dark' ||
    (!theme && window.matchMedia('(prefers-color-scheme: dark)').matches);
  const icon = toggle.querySelector('.theme-toggle-icon');
  if (icon) icon.textContent = isDark ? '☀' : '☾';
  toggle.setAttribute('aria-label', isDark ? 'Switch to light mode' : 'Switch to dark mode');
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

// --- Collapsible log viewer with hydration, copy, and resize (R6.1) ---

let logEntries = [];
const MAX_LOG_ENTRIES = 200;
const LOG_HEIGHT_KEY = 'drone-scanner-log-height';

function addLogEntry(type, message) {
  if (!message) return;
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

function copyLog() {
  const text = logEntries.map(e => {
    const ts = e.time.toLocaleTimeString();
    return `[${ts}] [${e.type}] ${e.message}`;
  }).join('\n');
  navigator.clipboard.writeText(text).then(() => {
    showToast('Log copied to clipboard', 'info');
  }).catch(() => {
    showToast('Unable to copy log', 'error');
  });
}

async function hydrateLog() {
  try {
    const entries = await fetchJson('/api/log');
    if (!Array.isArray(entries) || entries.length === 0) return;
    const body = document.getElementById('log-viewer-body');
    if (!body) return;
    for (const e of entries) {
      const entry = {
        time: new Date(e.timestamp),
        type: e.action || 'action',
        message: `${e.label || e.action || 'Action'}: ${e.status || 'done'}` +
          (e.target_name ? ` on ${e.target_name}` : ''),
      };
      logEntries.push(entry);
      renderLogEntry(entry);
    }
    if (logEntries.length > MAX_LOG_ENTRIES) {
      logEntries = logEntries.slice(-MAX_LOG_ENTRIES);
    }
    updateLogCount();
  } catch {}
}

function initLogResize() {
  const viewer = document.getElementById('log-viewer');
  const handle = document.getElementById('log-viewer-resize');
  const body = document.getElementById('log-viewer-body');
  if (!handle || !viewer || !body) return;

  let savedHeight = null;
  try { savedHeight = localStorage.getItem(LOG_HEIGHT_KEY); } catch {}
  if (savedHeight) {
    const h = parseInt(savedHeight, 10);
    if (h >= 60 && h <= 600) body.style.maxHeight = h + 'px';
  }

  let startY = 0;
  let startHeight = 0;

  function onMouseMove(e) {
    const delta = startY - e.clientY;
    const newHeight = Math.min(600, Math.max(60, startHeight + delta));
    body.style.maxHeight = newHeight + 'px';
  }

  function onMouseUp() {
    handle.classList.remove('active');
    document.removeEventListener('mousemove', onMouseMove);
    document.removeEventListener('mouseup', onMouseUp);
    try { localStorage.setItem(LOG_HEIGHT_KEY, parseInt(body.style.maxHeight, 10)); } catch {}
  }

  handle.addEventListener('mousedown', (e) => {
    e.preventDefault();
    startY = e.clientY;
    startHeight = body.offsetHeight;
    handle.classList.add('active');
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  });
}

function initLogViewer() {
  const viewer = document.getElementById('log-viewer');
  const header = document.getElementById('log-viewer-header');
  const clearBtn = document.getElementById('log-clear-btn');
  const copyBtn = document.getElementById('log-copy-btn');

  header.addEventListener('click', (e) => {
    if (e.target === clearBtn || clearBtn.contains(e.target)) return;
    if (copyBtn && (e.target === copyBtn || copyBtn.contains(e.target))) return;
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

  if (copyBtn) {
    copyBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      copyLog();
    });
  }

  initLogResize();
  addLogEntry('info', 'Dashboard initialized');
  hydrateLog();
}

// --- Log message formatting ---

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

// --- Pack listing panel ---

function initPacksPanel() {
  const btn = document.getElementById('btn-packs');
  const overlay = document.getElementById('pack-list-overlay');
  const closeBtn = document.getElementById('pack-list-close');
  if (!btn || !overlay) return;

  btn.addEventListener('click', async () => {
    overlay.hidden = false;
    await refreshPacksList();
  });

  if (closeBtn) {
    closeBtn.addEventListener('click', () => { overlay.hidden = true; });
  }
}

async function refreshPacksList() {
  try {
    packsList = await fetchJson('/api/actions');
    renderPacksList();
  } catch (error) {
    showToast(`Unable to load packs: ${error.message}`, 'error');
  }
}

function renderPacksList() {
  const container = document.getElementById('pack-list');
  if (!container || !Array.isArray(packsList)) return;

  if (packsList.length === 0) {
    container.innerHTML = '<div class="empty-state"><p>No capability packs registered</p></div>';
    return;
  }

  container.innerHTML = packsList.map(p => {
    const hasDestructive = (p.effects || []).some(e => e.destructive);
    const destructiveBadge = hasDestructive
      ? '<span class="pack-badge pack-badge-destructive">DESTRUCTIVE</span>'
      : '<span class="pack-badge pack-badge-safe">SAFE</span>';
    const effectBadges = (p.effects || []).map(e =>
      `<span class="pack-badge pack-badge-effect">${escHtml(e.name)}</span>`
    ).join('');
    const prereqBadges = (p.prerequisites || []).map(pr =>
      `<span class="pack-badge pack-badge-prereq">${escHtml(pr.name)}</span>`
    ).join('');

    return `
      <div class="pack-card" data-pack-name="${escAttr(p.name)}" data-pack-version="${escAttr(p.version)}">
        <div class="pack-card-header">
          <span class="pack-card-name">${escHtml(p.name)}</span>
          <span class="pack-card-version">v${escHtml(p.version)}</span>
        </div>
        <div class="pack-card-desc">${escHtml(p.description)}</div>
        <div class="pack-card-meta">${destructiveBadge}${effectBadges}${prereqBadges}</div>
        <div class="pack-card-actions">
          <button type="button" class="btn btn-sm btn-warning pack-arm-btn" data-pack-name="${escAttr(p.name)}" data-pack-version="${escAttr(p.version)}">Arm</button>
        </div>
      </div>`;
  }).join('');

  container.querySelectorAll('.pack-arm-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.getElementById('pack-list-overlay').hidden = true;
      openArmDialog(btn.dataset.packName, btn.dataset.packVersion);
    });
  });
}

// --- Arm dialog ---

let armingPackName = null;
let armingPackVersion = null;

function initArmDialog() {
  const cancelBtn = document.getElementById('arm-dialog-cancel');
  const confirmBtn = document.getElementById('arm-dialog-confirm');

  if (cancelBtn) cancelBtn.addEventListener('click', closeArmDialog);
  if (confirmBtn) confirmBtn.addEventListener('click', submitArm);
}

function openArmDialog(packName, packVersion) {
  armingPackName = packName;
  armingPackVersion = packVersion;

  const dialog = document.getElementById('arm-dialog');
  if (!dialog) return;

  document.getElementById('arm-pack-name').value = `${packName} v${packVersion}`;
  document.getElementById('arm-dialog-title').textContent = `Arm: ${packName}`;

  // Populate target select
  const select = document.getElementById('arm-target-select');
  select.innerHTML = targets.map(t =>
    `<option value="${escAttr(t.id)}">${escHtml(t.name)} (${escHtml(t.id)})</option>`
  ).join('');

  // Check if pack is destructive
  const pack = packsList.find(p => p.name === packName && p.version === packVersion);
  const hasDestructive = pack && (pack.effects || []).some(e => e.destructive);
  const notice = document.getElementById('arm-destructive-notice');
  const sacrificialField = document.getElementById('arm-sacrificial-field');
  if (notice) notice.hidden = !hasDestructive;
  if (hasDestructive) {
    document.getElementById('arm-sacrificial-target').checked = false;
  }

  dialog.hidden = false;
}

function closeArmDialog() {
  const dialog = document.getElementById('arm-dialog');
  if (dialog) dialog.hidden = true;
  armingPackName = null;
  armingPackVersion = null;
}

async function submitArm() {
  if (!armingPackName || !armingPackVersion) return;

  const targetId = document.getElementById('arm-target-select').value;
  const timeLimit = parseInt(document.getElementById('arm-time-limit').value, 10) || 300;
  const stopConditions = document.getElementById('arm-stop-conditions').value
    .split(',').map(s => s.trim()).filter(Boolean);
  const sacrificial = document.getElementById('arm-sacrificial-target').checked;
  const evidenceDest = document.getElementById('arm-evidence-dest').value || 'default';

  try {
    const data = await fetchJson(`/api/packs/${encodeURIComponent(armingPackName)}/${encodeURIComponent(armingPackVersion)}/arm`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        target_identity: targetId,
        permitted_actions: targets.find(t => t.id === targetId)?.available_actions || [],
        time_limit_seconds: timeLimit,
        stop_conditions: stopConditions,
        sacrificial_target: sacrificial,
        evidence_destination: evidenceDest,
      }),
    });
    showToast(`Armed: ${data.pack_name} on ${data.target_identity}`, 'action');
    closeArmDialog();
    refreshScopes();
  } catch (error) {
    showToast(`Arm failed: ${error.message}`, 'error');
  }
}

// --- Scope management ---

async function refreshScopes() {
  try {
    activeScopes = await fetchJson('/api/scopes');
    renderScopeIndicator();
    startScopeCountdown();
  } catch (error) {
    activeScopes = [];
  }
}

function renderScopeIndicator() {
  const indicator = document.getElementById('scope-indicator');
  if (!indicator) return;

  const active = activeScopes.filter(s => !s.disarmed && !s.is_expired);
  const expired = activeScopes.filter(s => s.is_expired && !s.disarmed);
  const disarmed = activeScopes.filter(s => s.disarmed);

  if (active.length === 0 && expired.length === 0) {
    indicator.innerHTML = '';
    return;
  }

  let html = '';
  for (const scope of active) {
    const remaining = Math.max(0, Math.floor(scope.time_remaining_seconds));
    const mins = Math.floor(remaining / 60);
    const secs = remaining % 60;
    html += `<span class="scope-armed">${escHtml(scope.pack_name)}</span>`;
    html += `<span class="scope-countdown">${mins}:${String(secs).padStart(2, '0')}</span>`;
    html += `<button type="button" class="btn btn-sm btn-danger scope-disarm-btn" data-pack-name="${escAttr(scope.pack_name)}" data-pack-version="${escAttr(scope.pack_version)}" data-target="${escAttr(scope.target_identity)}">Disarm</button>`;
  }
  for (const scope of expired) {
    html += `<span class="scope-expired">${escHtml(scope.pack_name)} EXPIRED</span>`;
  }

  indicator.innerHTML = html;

  indicator.querySelectorAll('.scope-disarm-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      try {
        await fetchJson(`/api/packs/${encodeURIComponent(btn.dataset.packName)}/${encodeURIComponent(btn.dataset.packVersion)}/disarm`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ target_identity: btn.dataset.target }),
        });
        refreshScopes();
      } catch (error) {
        showToast(`Disarm failed: ${error.message}`, 'error');
      }
    });
  });
}

function startScopeCountdown() {
  if (scopeCountdownTimer) clearInterval(scopeCountdownTimer);
  const hasActive = activeScopes.some(s => !s.disarmed && !s.is_expired);
  if (!hasActive) return;

  scopeCountdownTimer = setInterval(() => {
    for (const scope of activeScopes) {
      if (!scope.disarmed && !scope.is_expired) {
        scope.time_remaining_seconds = Math.max(0, scope.time_remaining_seconds - 1);
        if (scope.time_remaining_seconds <= 0) {
          scope.is_expired = true;
        }
      }
    }
    renderScopeIndicator();
    const stillActive = activeScopes.some(s => !s.disarmed && !s.is_expired);
    if (!stillActive) {
      clearInterval(scopeCountdownTimer);
      scopeCountdownTimer = null;
    }
  }, 1000);
}

// --- Destructive action confirmation ---

let destructiveCallback = null;

function initDestructiveConfirm() {
  const cancelBtn = document.getElementById('destructive-confirm-cancel');
  const proceedBtn = document.getElementById('destructive-confirm-proceed');
  const checkbox = document.getElementById('destructive-confirm-checkbox');

  if (cancelBtn) cancelBtn.addEventListener('click', closeDestructiveConfirm);
  if (checkbox) {
    checkbox.addEventListener('change', () => {
      if (proceedBtn) proceedBtn.disabled = !checkbox.checked;
    });
  }
  if (proceedBtn) {
    proceedBtn.addEventListener('click', () => {
      closeDestructiveConfirm();
      if (destructiveCallback) destructiveCallback();
    });
  }
}

function showDestructiveConfirm(desc, callback) {
  destructiveCallback = callback;
  const overlay = document.getElementById('destructive-confirm');
  const descEl = document.getElementById('destructive-confirm-desc');
  const checkbox = document.getElementById('destructive-confirm-checkbox');
  const proceedBtn = document.getElementById('destructive-confirm-proceed');
  if (descEl) descEl.textContent = desc;
  if (checkbox) checkbox.checked = false;
  if (proceedBtn) proceedBtn.disabled = true;
  if (overlay) overlay.hidden = false;
}

function closeDestructiveConfirm() {
  const overlay = document.getElementById('destructive-confirm');
  if (overlay) overlay.hidden = true;
  destructiveCallback = null;
}

// --- Result State display ---

function initResultStateDisplay() {
  const closeBtn = document.getElementById('result-state-close');
  if (closeBtn) {
    closeBtn.addEventListener('click', () => {
      const display = document.getElementById('result-state-display');
      if (display) display.hidden = true;
    });
  }
}

function addResultStateEntry(data) {
  const display = document.getElementById('result-state-display');
  const container = document.getElementById('result-state-transitions');
  if (!display || !container) return;

  display.hidden = false;
  const stateCls = safeCssClass(data.state);
  const ts = data.timestamp ? new Date(data.timestamp).toLocaleTimeString() : '';
  const entry = document.createElement('div');
  entry.className = `result-state-entry state-${stateCls}`;
  entry.innerHTML = `
    <span class="result-state-name">${escHtml(data.state)}</span>
    <span class="result-state-ts">${escHtml(ts)}</span>
    <span class="result-state-target">${escHtml(data.target_id || '')}</span>`;
  container.appendChild(entry);
}

// --- Evidence viewer ---

function initEvidenceViewer() {
  const btn = document.getElementById('btn-evidence');
  const overlay = document.getElementById('evidence-viewer');
  const closeBtn = document.getElementById('evidence-viewer-close');

  if (btn) {
    btn.addEventListener('click', async () => {
      if (overlay) overlay.hidden = false;
      await refreshEvidenceList();
    });
  }
  if (closeBtn) {
    closeBtn.addEventListener('click', () => {
      if (overlay) overlay.hidden = true;
      const detail = document.getElementById('evidence-viewer-detail');
      if (detail) detail.hidden = true;
    });
  }
}

async function refreshEvidenceList() {
  const container = document.getElementById('evidence-viewer-list');
  if (!container) return;

  try {
    const records = await fetchJson('/api/evidence');
    if (!Array.isArray(records) || records.length === 0) {
      container.innerHTML = '<div class="empty-state"><p>No evidence records</p></div>';
      return;
    }

    container.innerHTML = records.map(r => {
      const time = new Date(r.start_time).toLocaleTimeString();
      const lastState = r.transition_log.length > 0
        ? r.transition_log[r.transition_log.length - 1].state
        : 'none';
      return `
        <div class="evidence-record-card" data-record-id="${escAttr(r.id)}">
          <div class="evidence-record-header">
            <span class="evidence-record-target">${escHtml(r.target_identity)}</span>
            <span class="evidence-record-time">${escHtml(time)}</span>
          </div>
          <div class="evidence-record-state">
            v${escHtml(r.capability_version)} | ${escHtml(lastState)} | ${escHtml(r.stop_reason)}
          </div>
        </div>`;
    }).join('');

    container.querySelectorAll('.evidence-record-card').forEach(card => {
      card.addEventListener('click', () => showEvidenceDetail(card.dataset.recordId));
    });
  } catch (error) {
    container.innerHTML = `<div class="empty-state"><p>Unable to load evidence: ${escHtml(error.message)}</p></div>`;
  }
}

async function showEvidenceDetail(recordId) {
  const detail = document.getElementById('evidence-viewer-detail');
  if (!detail) return;

  try {
    const record = await fetchJson(`/api/evidence/${encodeURIComponent(recordId)}`);
    const transitionHtml = record.transition_log.map(entry => {
      const state = typeof entry === 'object' ? entry.state : entry;
      const ts = typeof entry === 'object' && entry.timestamp
        ? new Date(entry.timestamp).toLocaleTimeString() : '';
      return `<div class="evidence-transition-entry"><span>${escHtml(state)}</span><span>${escHtml(ts)}</span></div>`;
    }).join('');

    const prereqModes = Object.entries(record.prerequisite_modes || {}).map(([k, v]) =>
      `<span class="detail-label">${escHtml(k)}</span><span class="detail-value">${escHtml(v)}</span>`
    ).join('');

    detail.hidden = false;
    detail.innerHTML = `
      <div class="evidence-detail-section">
        <h4>Execution Record</h4>
        <div class="evidence-detail-grid">
          <span class="evidence-detail-label">Capability Version</span>
          <span class="evidence-detail-value">${escHtml(record.capability_version)}</span>
          <span class="evidence-detail-label">Target</span>
          <span class="evidence-detail-value">${escHtml(record.target_identity)}</span>
          <span class="evidence-detail-label">Start Time</span>
          <span class="evidence-detail-value">${escHtml(new Date(record.start_time).toLocaleString())}</span>
          <span class="evidence-detail-label">End Time</span>
          <span class="evidence-detail-value">${escHtml(new Date(record.end_time).toLocaleString())}</span>
          <span class="evidence-detail-label">Stop Reason</span>
          <span class="evidence-detail-value">${escHtml(record.stop_reason)}</span>
          <span class="evidence-detail-label">Hardware</span>
          <span class="evidence-detail-value">${escHtml(record.hardware_identity)}</span>
          <span class="evidence-detail-label">Scope Ref</span>
          <span class="evidence-detail-value">${escHtml(record.authorization_scope_ref)}</span>
        </div>
      </div>
      ${prereqModes ? `
      <div class="evidence-detail-section">
        <h4>Prerequisite Modes</h4>
        <div class="evidence-detail-grid">${prereqModes}</div>
      </div>` : ''}
      <div class="evidence-detail-section">
        <h4>Result State Transitions</h4>
        <div class="evidence-transition-list">${transitionHtml || '<em>None</em>'}</div>
      </div>
      <div class="evidence-detail-section">
        <h4>Verification Evidence</h4>
        <pre class="data-block">${escHtml(JSON.stringify(record.verification_evidence, null, 2))}</pre>
      </div>`;
  } catch (error) {
    detail.hidden = false;
    detail.innerHTML = `<p>Unable to load record: ${escHtml(error.message)}</p>`;
  }
}

init();
