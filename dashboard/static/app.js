/* Screenbox Dashboard -- main application JS */
/* global SB namespace for tab modules */
window.SB = window.SB || {};

let currentDesktops = [];
let selectedId = null;

// --- localStorage persistence ---
let _vncMode = 'control'; // 'view' or 'control'
const _LS_KEY = 'screenbox_dashboard';
function _loadState() {
  try { return JSON.parse(localStorage.getItem(_LS_KEY)) || {}; } catch(e) { return {}; }
}
function _saveState(patch) {
  const s = {..._loadState(), ...patch};
  localStorage.setItem(_LS_KEY, JSON.stringify(s));
}
const _savedState = _loadState();
if (_savedState.vncMode) { _vncMode = _savedState.vncMode; }

// Pending desktops being created (shown as loading cards)
let pendingDesktops = [];
// Track desktops in transition: {id: {target: 'stopped', from: 'running'}}
let transitions = {};

function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

let _showSaved = false;
function toggleSaved() {
  _showSaved = !_showSaved;
  document.getElementById('grid').classList.toggle('show-saved', _showSaved);
  const btn = document.getElementById('btn-saved');
  btn.textContent = _showSaved ? 'hide saved' : 'show saved';
  btn.classList.toggle('active', _showSaved);
}

function nextDesktopId(desktops) {
  let n = 1;
  const ids = new Set(desktops.map(d => d.id).concat(pendingDesktops));
  while (ids.has('desktop-' + n)) n++;
  return 'desktop-' + n;
}

function fmtFooter(r, d) {
  if (!r || !r.total_rss_mb && r.total_rss_mb !== 0) return '--';
  let cpu = r.total_cpu || '0%';
  const cpuM = cpu.match(/([\d.]+)%/);
  if (cpuM && r.host_cores) cpu = (parseFloat(cpuM[1]) / r.host_cores).toFixed(1) + '%';
  const memLimit = r.mem_limit_mb || 0;
  const memLimitStr = memLimit >= 1024 ? (memLimit / 1024).toFixed(1) + 'G' : memLimit + 'M';
  const memStr = r.total_rss_mb < 1024
    ? Math.round(r.total_rss_mb) + 'M'
    : (r.total_rss_mb / 1024).toFixed(1) + 'G';
  const memPct = memLimit ? (r.total_rss_mb / memLimit * 100) : 0;
  const memClass = memPct > 90 ? 'mem-crit' : memPct > 70 ? 'mem-warn' : '';
  // Row 1: cpu | mem | disk
  let row1 = 'cpu ' + cpu + ' | mem ' + memStr + '/' + memLimitStr + ' | disk ' + r.disk_total_mb + '/' + r.disk_quota_mb + 'M';
  // Row 2: agent | image | resolution
  let parts2 = [];
  if (d) {
    if (d.assigned_name || d.acquired_by) parts2.push(d.assigned_name || d.acquired_by);
    if (d.image) parts2.push(d.image.replace('screenbox:', ''));
    if (d.resolution) parts2.push(d.resolution);
  }
  let row2 = parts2.length ? parts2.join(' | ') : '';
  return '<div class="footer-row">' + row1 + '</div>'
    + (row2 ? '<div class="footer-row">' + row2 + '</div>' : '');
}

let _lastDesktops = [];
function renderGrid(desktops) {
  _lastDesktops = desktops;
  const grid = document.getElementById('grid');
  const count = document.getElementById('count');

  const running = desktops.filter(d => d.state === 'running');
  const saved = desktops.filter(d => d.state === 'saved');
  let countText = running.length + ' running';
  if (pendingDesktops.length) countText += ' + ' + pendingDesktops.length + ' starting';
  if (saved.length) countText += ' / ' + saved.length + ' saved';
  count.textContent = countText;
  count.style.color = running.length === 0 ? '#a09050' : '';

  const sig = desktops.map(d => d.id + ':' + d.state + ':' + (d.assigned_name || d.acquired_by || '')).join(',') + '|' + pendingDesktops.join(',') + '|f:' + _createFormOpen;
  if (grid.dataset.sig === sig) return;
  grid.dataset.sig = sig;

  // "+" button always first -- same structure as tile for equal height
  const formOpen = document.querySelector('.tile-create.open');
  let html;
  if (_createFormOpen) {
    const _id = _createFormId || nextDesktopId(desktops);
    html = `<div class="tile tile-create open">
      <div class="tile-header">
        <span class="tile-id" style="color:#8090a8">new desktop</span>
      </div>
      <div class="tile-preview" style="overflow:auto">
        <div class="tile-preview-inner" style="align-items:stretch">
          <form class="create-form" onsubmit="submitCreate(event)">
            <input name="id" value="${_id}" spellcheck="false" autocomplete="off" placeholder="desktop id">
            <div class="form-row">
              <select name="memory">
                <option value="512m">512 MB</option>
                <option value="1024m">1 GB</option>
                <option value="2048m" selected>2 GB</option>
                <option value="4096m">4 GB</option>
              </select>
              <select name="resolution">
                <option value="1280x720">1280x720</option>
                <option value="1920x1080" selected>1920x1080</option>
              </select>
            </div>
            <input type="hidden" name="image" value="screenbox:latest">
            <div class="form-spacer"></div>
            <div class="form-actions">
              <button type="button" class="btn-cancel" onclick="closeCreateForm()">Cancel</button>
              <button type="submit" class="btn-create">Create</button>
            </div>
          </form>
        </div>
      </div>
      <div class="tile-footer" style="color:#606878">configure & create</div>
    </div>`;
  } else {
    html = `<div class="tile tile-create" onclick="openCreateForm()">
      <div class="tile-header">
        <span class="tile-id" style="color:#353c48">new desktop</span>
      </div>
      <div class="tile-preview" style="cursor:pointer">
        <div class="tile-preview-inner"><span class="plus">+</span></div>
      </div>
      <div class="tile-footer" style="color:#353c48">click to create</div>
    </div>`;
  }

  // Pending cards (loading) -- skip if already in real desktop list
  const realIds = new Set(desktops.map(d => d.id));
  html += pendingDesktops.filter(id => !realIds.has(id)).map(id => `
    <div class="tile" data-tile="${id}">
      <div class="tile-header">
        <span><span class="dot"></span><span class="tile-id">${id}</span></span>
        <span class="tile-state state-starting">starting</span>
      </div>
      <div class="tile-preview">
        <div class="tile-preview-inner"><div class="spinner"></div></div>
      </div>
      <div class="tile-footer">starting container...</div>
    </div>`).join('');

  // Real desktops
  html += desktops.map(d => {
    const stateClass = 'state-' + d.state;
    const clickable = d.state === 'running';
    const sel = d.id === selectedId ? ' selected' : '';

    const isTransition = ['starting', 'stopping', 'destroying', 'pausing', 'resuming'].includes(d.state);
    let controls = '';
    if (d.state === 'running') {
      controls = `<button class="tile-btn" onclick="event.stopPropagation();window.open('/view?id=${d.id}','_blank')" title="Open fullscreen">&#8599;</button>`
        + `<button class="tile-btn" onclick="event.stopPropagation();shareDesktop('${d.id}')" title="Share link">&#128279;</button>`
        + `<button class="tile-btn" onclick="event.stopPropagation();controlDesktop('${d.id}','pause')" title="Pause">||</button>`
        + `<button class="tile-btn" onclick="event.stopPropagation();controlDesktop('${d.id}','stop')" title="Stop">&#9632;</button>`
        + `<button class="tile-btn btn-danger" onclick="event.stopPropagation();destroyDesktop('${d.id}')" title="Destroy">&#215;</button>`;
    } else if (d.state === 'paused') {
      controls = `<button class="tile-btn" onclick="event.stopPropagation();controlDesktop('${d.id}','unpause')" title="Resume">&#9654;</button>`
        + `<button class="tile-btn btn-danger" onclick="event.stopPropagation();destroyDesktop('${d.id}')" title="Destroy">&#215;</button>`;
    } else if (d.state === 'stopped') {
      controls = `<button class="tile-btn" onclick="event.stopPropagation();controlDesktop('${d.id}','start')" title="Start">&#9654;</button>`
        + `<button class="tile-btn btn-danger" onclick="event.stopPropagation();destroyDesktop('${d.id}')" title="Destroy">&#215;</button>`;
    } else if (d.state === 'saved') {
      controls = `<button class="tile-btn" onclick="event.stopPropagation();launchSaved('${d.id}')" title="Launch">&#9654;</button>`
        + `<button class="tile-btn btn-danger" onclick="event.stopPropagation();deleteSavedData('${d.id}')" title="Delete data">&#215;</button>`;
    }

    return `
      <div class="tile${sel}${d.state === 'saved' ? ' tile-saved' : ''}" onclick="selectDesktop('${d.id}')" data-tile="${d.id}">
        <div class="tile-header">
          <span><span class="dot${d.state === 'running' ? ' live' : ''}"></span><span class="tile-id">${d.id}</span></span>
          <span class="tile-controls">
            ${isTransition ? '' : controls}
            <span class="tile-state ${stateClass}">${d.state}</span>
          </span>
        </div>
        <div class="tile-preview">
          ${isTransition
            ? '<div class="tile-preview-inner"><div class="spinner"></div></div>'
            : clickable
              ? '<div class="tile-preview-inner tile-preview-screen" data-thumb-desktop="' + d.id + '">'
                + '<div class="tile-connecting"><div class="spinner-small"></div><span>connecting</span></div>'
                + '</div>'
              : d.state === 'saved'
                ? '<div class="tile-preview-inner"><span style="color:#353c48;font-size:10px;letter-spacing:2px;text-align:center">SAVED<br><span style="font-size:9px;color:#2a3040;letter-spacing:1px">' + (d.resources && d.resources.disk_total_mb ? d.resources.disk_total_mb + ' MB on disk' : '') + '</span></span></div>'
                : '<div class="tile-preview-inner">' + d.state + '</div>'}
        </div>
        <div class="tile-footer" data-footer="${d.id}">
          ${isTransition ? d.state + '...' : d.state === 'saved' ? 'data on disk' : fmtFooter(d.resources, d)}
        </div>
      </div>`;
  }).join('');

  grid.innerHTML = html;
}

// Modal dialog
let _modalResolve = null;
function showModal(text, isDanger) {
  document.getElementById('modal-text').innerHTML = text;
  const btn = document.getElementById('modal-confirm');
  btn.className = isDanger ? 'modal-danger' : '';
  document.getElementById('modal').classList.add('visible');
  return new Promise(resolve => { _modalResolve = resolve; });
}
function modalConfirm() {
  document.getElementById('modal').classList.remove('visible');
  if (_modalResolve) _modalResolve(true);
  _modalResolve = null;
}
function modalCancel() {
  document.getElementById('modal').classList.remove('visible');
  if (_modalResolve) _modalResolve(false);
  _modalResolve = null;
}

// --- Auth: cookie-based (set via ?token= on first visit, then cookie auto-sent) ---
function _apiUrl(url) { return url; }

async function _apiFetch(url, opts) {
  return await fetch(url, opts);
}

async function launchSaved(id) {
  pendingDesktops.push(id);
  // Remove from saved list immediately
  currentDesktops = currentDesktops.filter(d => !(d.id === id && d.state === 'saved'));
  renderGrid(currentDesktops);
  try {
    const res = await _apiFetch('/api/create', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id})
    });
    const data = await res.json();
    if (data.error) {
      showModal(data.error, false);
      pendingDesktops = pendingDesktops.filter(p => p !== id);
    }
  } catch (e) {
    showModal('network error', false);
    pendingDesktops = pendingDesktops.filter(p => p !== id);
  }
  setTimeout(refresh, 2000);
}

async function deleteSavedData(id) {
  const ok = await showModal(
    'delete all data for <span class="modal-id">' + id + '</span>?<br>'
    + '<span style="font-size:11px;color:#b07070;line-height:1.8">appdata, downloads, workspace -- permanently removed</span>', true
  );
  if (!ok) return;
  // Show destroying state immediately
  const d = currentDesktops.find(x => x.id === id);
  if (d) {
    d.state = 'destroying';
    transitions[id] = {target: '__destroyed__', ts: Date.now()};
    renderGrid(currentDesktops);
  }
  try {
    const res = await _apiFetch('/api/storage/delete', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id})
    });
    const data = await res.json();
    if (data.error) {
      showModal(data.error, false);
      delete transitions[id];
    }
  } catch (e) { delete transitions[id]; }
  setTimeout(refresh, 500);
}

let _creating = false;
let _createFormOpen = false;
let _createFormId = '';

function openCreateForm() {
  _createFormOpen = true;
  _createFormId = nextDesktopId(currentDesktops);
  renderGrid(currentDesktops);
}

function closeCreateForm() {
  _createFormOpen = false;
  _createFormId = '';
  renderGrid(currentDesktops);
}

async function submitCreate(e) {
  e.preventDefault();
  if (_creating) return;

  const form = e.target;
  const id = form.id.value.trim();
  const memory = form.memory.value;
  const resolution = form.resolution.value;
  const image = form.image.value;

  if (!id) { showModal('Desktop ID is required', false); return; }

  _creating = true;
  _createFormOpen = false;
  pendingDesktops.push(id);
  renderGrid(currentDesktops);

  try {
    const res = await _apiFetch('/api/create', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id, memory_limit: memory, resolution, image})
    });
    const data = await res.json();
    if (data.error) {
      showModal(data.error, false);
      pendingDesktops = pendingDesktops.filter(p => p !== id);
      renderGrid(currentDesktops);
    }
  } catch (e) {
    showModal('network error -- try again', false);
    pendingDesktops = pendingDesktops.filter(p => p !== id);
    renderGrid(currentDesktops);
  }
  _creating = false;
}

async function controlDesktop(id, action) {
  const labels = {pause: 'pause', unpause: 'resume', stop: 'stop', start: 'start'};
  const ok = await showModal(
    labels[action] + ' <span class="modal-id">' + id + '</span>?', false
  );
  if (!ok) return;

  // Optimistic local state update -- show spinner for transitional states
  const transMap = {pause: 'stopping', unpause: 'starting', stop: 'stopping', start: 'starting'};
  const targetMap = {pause: 'paused', unpause: 'running', stop: 'stopped', start: 'running'};
  const d = currentDesktops.find(x => x.id === id);
  if (d) {
    d.state = transMap[action] || d.state;
    transitions[id] = {target: targetMap[action], ts: Date.now()};
    renderGrid(currentDesktops);
  }

  try {
    const res = await _apiFetch('/api/control', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id, action})
    });
    const data = await res.json();
    if (data.error) showModal(data.error, false);
  } catch (e) {}
  setTimeout(refresh, 1500);
}

async function shareDesktop(id) {
  try {
    const resp = await _apiFetch('/api/share', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({desktop_id: id, ttl: 3600})
    });
    const data = await resp.json();
    if (data.error) {
      alert('Share error: ' + data.error);
      return;
    }
    // Build URL from current origin (no hardcoded domain)
    const url = location.origin + '/s/' + data.token;
    try {
      await navigator.clipboard.writeText(url);
      _toast('Share link copied (1h)');
    } catch(e) {
      // Fallback: show in prompt
      prompt('Share link (expires in 1 hour):', url);
    }
  } catch(e) {
    alert('Failed to create share link');
  }
}

function _toast(msg) {
  let el = document.getElementById('sb-toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'sb-toast';
    el.style.cssText = 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);'
      + 'background:rgba(63,185,80,0.2);color:#3fb950;border:1px solid rgba(63,185,80,0.3);'
      + 'padding:8px 20px;border-radius:4px;font-size:12px;font-family:monospace;'
      + 'z-index:9999;opacity:0;transition:opacity 0.3s;pointer-events:none;';
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.style.opacity = '1';
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.style.opacity = '0'; }, 2500);
}

async function destroyDesktop(id) {
  const ok = await showModal(
    'destroy <span class="modal-id">' + id + '</span>?<br>'
    + '<span style="font-size:11px;color:#b07070;line-height:1.8">removed: container, running processes, memory</span><br>'
    + '<span style="font-size:11px;color:#8090a8;line-height:1.8">kept: appdata, downloads, workspace (on disk)</span>', true
  );
  if (!ok) return;

  // Show "destroying" spinner immediately
  const d = currentDesktops.find(x => x.id === id);
  if (d) {
    d.state = 'destroying';
    transitions[id] = {target: '__destroyed__', ts: Date.now()};
    renderGrid(currentDesktops);
  }

  if (selectedId === id) {
    selectedId = null;
    document.getElementById('preview-panel').classList.remove('visible');
  }

  try {
    await _apiFetch('/api/destroy', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id})
    });
  } catch (e) {}
  setTimeout(refresh, 500);
}

// --- Tab system ---
let _activeTab = 'screen'; // 'screen' | 'logs' | 'knowledge' | 'recordings'

function setActiveTab(tab) {
  _activeTab = tab;
  // Update toolbar buttons
  document.getElementById('btn-mode-view').classList.toggle('active', tab === 'screen' && _vncMode === 'view');
  document.getElementById('btn-mode-control').classList.toggle('active', tab === 'screen' && _vncMode === 'control');
  document.getElementById('btn-tab-logs').classList.toggle('active', tab === 'logs');
  document.getElementById('btn-tab-knowledge').classList.toggle('active', tab === 'knowledge');
  document.getElementById('btn-tab-recordings').classList.toggle('active', tab === 'recordings');

  // Toggle content areas
  document.getElementById('preview-screen').classList.toggle('tab-hidden', tab !== 'screen');
  document.getElementById('tab-content-logs').classList.toggle('active', tab === 'logs');
  document.getElementById('tab-content-knowledge').classList.toggle('active', tab === 'knowledge');
  document.getElementById('tab-content-recordings').classList.toggle('active', tab === 'recordings');

  // Show/hide screen-specific toolbar items
  const statsEl = document.getElementById('preview-stats');
  const fsLink = document.getElementById('toolbar-fullscreen');
  if (tab === 'screen') {
    statsEl.style.display = '';
    fsLink.style.display = '';
  } else {
    statsEl.style.display = 'none';
    fsLink.style.display = 'none';
  }

  // Notify tab modules
  if (tab === 'logs' && SB.logs) {
    SB.logs.activate(selectedId);
  } else if (SB.logs) {
    SB.logs.deactivate();
  }

  if (tab === 'knowledge' && SB.knowledge) {
    SB.knowledge.activate(selectedId);
  } else if (SB.knowledge) {
    SB.knowledge.deactivate();
  }

  if (tab === 'recordings' && SB.recordings) {
    SB.recordings.activate(selectedId);
  } else if (SB.recordings) {
    SB.recordings.deactivate();
  }
}

function selectDesktop(id) {
  selectedId = id;
  _saveState({selectedId: id, panelVisible: true});
  const d = currentDesktops.find(x => x.id === id);
  if (!d) return;

  document.querySelectorAll('.tile').forEach(t => t.classList.remove('selected'));
  const tile = document.querySelector('[data-tile="' + id + '"]');
  if (tile) tile.classList.add('selected');

  const panel = document.getElementById('preview-panel');
  panel.classList.add('visible');
  const agentLabel = d.assigned_name || d.acquired_by || '';
  document.getElementById('preview-title').textContent = id + (agentLabel ? ' | ' + agentLabel : '');

  const screen = document.getElementById('preview-screen');
  // Update fullscreen link
  const fsLink = document.getElementById('toolbar-fullscreen');
  if (d.state === 'running') {
    fsLink.href = '/view?id=' + id;
    fsLink.style.display = '';
  } else {
    fsLink.style.display = 'none';
  }

  if (_activeTab === 'screen') {
    if (d.state === 'running') {
      connectVNC(id, screen);
    } else {
      disconnectVNC();
      screen.innerHTML = '<span style="color:#2a3040;font-size:12px;letter-spacing:2px">' + d.state + '</span>';
    }
  }

  // If logs tab is active, re-activate with new desktop
  if (_activeTab === 'logs' && SB.logs) {
    SB.logs.activate(id);
  }
  if (_activeTab === 'recordings' && SB.recordings) {
    SB.recordings.activate(id);
  }

  updatePreviewStats(d);
  updatePreviewControls(d);
  updateRecordingButton(id);
}

function updatePreviewControls(d) {
  const pause = document.getElementById('ctrl-pause');
  const resume = document.getElementById('ctrl-resume');
  const stop = document.getElementById('ctrl-stop');
  const start = document.getElementById('ctrl-start');
  const rec = document.getElementById('ctrl-rec');
  if (!pause) return;

  if (d.state === 'running') {
    pause.style.display = ''; resume.style.display = 'none';
    stop.style.display = ''; start.style.display = 'none';
    if (rec) rec.style.display = '';
  } else if (d.state === 'paused') {
    pause.style.display = 'none'; resume.style.display = '';
    stop.style.display = ''; start.style.display = 'none';
    if (rec) rec.style.display = 'none';
  } else if (d.state === 'stopped') {
    pause.style.display = 'none'; resume.style.display = 'none';
    stop.style.display = 'none'; start.style.display = '';
    if (rec) rec.style.display = 'none';
  } else {
    // transition states -- hide all
    pause.style.display = 'none'; resume.style.display = 'none';
    stop.style.display = 'none'; start.style.display = 'none';
    if (rec) rec.style.display = 'none';
  }
}

async function previewControl(action) {
  if (!selectedId) return;
  await controlDesktop(selectedId, action);
}

let _isRecording = false;

async function updateRecordingButton(desktopId) {
  const rec = document.getElementById('ctrl-rec');
  const dot = document.getElementById('ctrl-rec-dot');
  if (!rec) return;
  try {
    const res = await _apiFetch('/api/recording/status?id=' + desktopId);
    const data = await res.json();
    if (!data.available) {
      rec.style.display = 'none';
      _isRecording = false;
      return;
    }
    // Show button (updatePreviewControls may have hidden it for non-running)
    const d = currentDesktops.find(d => d.id === desktopId);
    if (d && d.state === 'running') rec.style.display = '';
    // Sync recording state
    _isRecording = data.recording;
    if (_isRecording) {
      rec.classList.add('recording');
      if (dot) dot.classList.add('rec-dot-active');
    } else {
      rec.classList.remove('recording');
      if (dot) dot.classList.remove('rec-dot-active');
    }
  } catch(e) {
    rec.style.display = 'none';
  }
}

async function previewRecord() {
  if (!selectedId) return;
  const btn = document.getElementById('ctrl-rec');
  const dot = document.getElementById('ctrl-rec-dot');

  if (_isRecording) {
    // Confirm stop
    const ok = await showModal(`Stop recording on <b>${selectedId}</b>?`, false);
    if (!ok) return;
    try {
      await _apiFetch('/api/record', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: selectedId, action: 'stop'}),
      });
    } catch(e) {}
    _isRecording = false;
    if (btn) btn.classList.remove('recording');
    if (dot) dot.classList.remove('rec-dot-active');
    // Refresh recordings list after ffmpeg finalizes
    if (SB.recordings) {
      setTimeout(() => SB.recordings.activate(selectedId), 3000);
      // Switch to recordings tab to show result
      setActiveTab('recordings');
    }
  } else {
    // Confirm start
    const ok = await showModal(`Start recording on <b>${selectedId}</b>?`, false);
    if (!ok) return;
    try {
      const res = await _apiFetch('/api/record', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: selectedId, action: 'start'}),
      });
      const data = await res.json();
      if (data.error) return;
    } catch(e) { return; }
    _isRecording = true;
    if (btn) btn.classList.add('recording');
    if (dot) dot.classList.add('rec-dot-active');
  }
}

function updatePreviewStats(d) {
  const el = document.getElementById('preview-stats');
  const r = d.resources || {};

  const fmtMB = mb => mb < 1024 ? Math.round(mb) + 'M' : (mb / 1024).toFixed(1) + 'G';
  const memUsed = r.total_rss_mb != null ? fmtMB(r.total_rss_mb) : '--';
  const memLimit = r.mem_limit_mb ? fmtMB(r.mem_limit_mb) : '--';

  let cpuDisplay = r.total_cpu || '--';
  const cpuMatch = cpuDisplay.match(/([\d.]+)%/);
  if (cpuMatch) {
    cpuDisplay = (parseFloat(cpuMatch[1]) / (r.host_cores || 1)).toFixed(1) + '%';
  }

  // Row 1: cpu | mem | disk
  let row1 = 'cpu ' + cpuDisplay + ' | mem ' + memUsed + '/' + memLimit + ' | disk ' + (r.disk_total_mb || 0) + '/' + (r.disk_quota_mb || 0) + 'M';
  // Append image + resolution to row 1
  if (d.image) row1 += ' | ' + d.image.replace('screenbox:', '');
  if (d.resolution) row1 += ' | ' + d.resolution;

  el.innerHTML = '<div class="preview-stat-row">' + row1 + '</div>';
}

// Resize handle for preview panel
(function() {
  const handle = document.getElementById('preview-resize');
  const panel = document.getElementById('preview-panel');
  let dragging = false, startX, startW;
  handle.addEventListener('mousedown', e => {
    dragging = true; startX = e.clientX; startW = panel.offsetWidth;
    handle.classList.add('active');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
  });
  document.addEventListener('mousemove', e => {
    if (!dragging) return;
    const newW = startW - (e.clientX - startX);
    const w = Math.max(280, Math.min(newW, window.innerWidth * 0.7));
    panel.style.width = w + 'px';
    _saveState({panelWidth: w});
  });
  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    handle.classList.remove('active');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    // Notify noVNC to rescale after resize
    window.dispatchEvent(new Event('resize'));
  });
  // ResizeObserver: rescale noVNC when preview-screen changes size
  const screen = document.getElementById('preview-screen');
  new ResizeObserver(() => {
    if (rfbConnection) {
      // Trigger noVNC internal rescale
      rfbConnection.scaleViewport = true;
    }
  }).observe(screen);
})();

document.getElementById('preview-close').onclick = () => {
  disconnectVNC();
  selectedId = null;
  // Deactivate tab modules
  if (SB.logs) SB.logs.deactivate();
  if (SB.knowledge) SB.knowledge.deactivate();
  _activeTab = 'screen';
  document.getElementById('preview-panel').classList.remove('visible');
  document.querySelectorAll('.tile').forEach(t => t.classList.remove('selected'));
  _saveState({selectedId: null, panelVisible: false});
};

// --- VNC Thumbnails: live stream instead of polling ---
const _thumbConnections = new Map(); // desktopId -> { rfb, el }

async function _connectThumb(desktopId, containerEl) {
  // Already connected?
  if (_thumbConnections.has(desktopId)) return;

  const wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsPort = location.port ? ':' + location.port : '';
  const wsUrl = wsProto + '//' + location.hostname + wsPort + '/vnc/' + desktopId;

  try {
    const RFB = await _getRFB();
    containerEl.innerHTML = '';
    const rfb = new RFB(containerEl, wsUrl);
    rfb.viewOnly = true;
    rfb.scaleViewport = true;
    rfb.showDotCursor = false;
    rfb.clipViewport = false;
    rfb.resizeSession = false;
    rfb.background = '#0a0e14';
    rfb.qualityLevel = 3;  // low quality for thumbnails
    rfb.compressionLevel = 9;  // max compression

    rfb.addEventListener('connect', () => {
      // Hide "connecting" spinner inside the container
      const conn = containerEl.querySelector('.tile-connecting');
      if (conn) conn.style.display = 'none';
    });
    rfb.addEventListener('disconnect', () => {
      _thumbConnections.delete(desktopId);
      // Show "connecting" spinner again
      const conn = containerEl.querySelector('.tile-connecting');
      if (conn) conn.style.display = '';
      // Auto-reconnect after 3s if tile still exists and tab visible
      setTimeout(() => {
        if (!document.hidden && document.querySelector('[data-thumb-desktop="' + desktopId + '"]')) {
          _connectThumb(desktopId, containerEl);
        }
      }, 3000);
    });

    _thumbConnections.set(desktopId, { rfb, el: containerEl });
  } catch (e) {
    console.log('[thumb] VNC connect failed for ' + desktopId, e);
  }
}

function _disconnectThumb(desktopId) {
  const entry = _thumbConnections.get(desktopId);
  if (entry) {
    try { entry.rfb.disconnect(); } catch(e) {}
    _thumbConnections.delete(desktopId);
  }
}

function refreshScreenshots() {
  // Connect VNC thumbnails for visible running desktops
  document.querySelectorAll('.tile-preview-screen[data-thumb-desktop]').forEach(el => {
    const id = el.dataset.thumbDesktop;
    if (!_thumbConnections.has(id) && !document.hidden) {
      _connectThumb(id, el);
    }
  });

  // Disconnect thumbnails for desktops no longer visible
  for (const [id, entry] of _thumbConnections) {
    if (!document.querySelector('.tile-preview-screen[data-thumb-desktop="' + id + '"]')) {
      _disconnectThumb(id);
    }
  }
}

// Disconnect all thumbnails when tab hidden, reconnect when visible
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    for (const [id] of _thumbConnections) _disconnectThumb(id);
  } else {
    refreshScreenshots();
  }
});

async function refresh() {
  try {
    const res = await _apiFetch('/api/desktops');
    currentDesktops = await res.json();
    // Remove from pending only if container actually exists (not just "saved" dir)
    const activeIds = new Set(currentDesktops.filter(d => d.state !== 'saved').map(d => d.id));
    pendingDesktops = pendingDesktops.filter(p => !activeIds.has(p));
    // Hide "saved" entries that are being created (dir exists but container still starting)
    const pendingSet = new Set(pendingDesktops);
    currentDesktops = currentDesktops.filter(d => !(d.state === 'saved' && pendingSet.has(d.id)));
    const realIds = new Set(currentDesktops.map(d => d.id));
    renderGrid(currentDesktops);
    updateStats(currentDesktops);
    // Connect VNC thumbnails for new/changed desktops
    setTimeout(refreshScreenshots, 500);
  } catch (e) {
    console.error('Refresh failed:', e);
  }
}

function updateStats(desktops) {
  desktops.forEach(d => {
    if (!d.resources) return;
    const footer = document.querySelector('[data-footer="' + d.id + '"]');
    if (footer) {
      footer.innerHTML = fmtFooter(d.resources, d);
    }
    if (d.id === selectedId) updatePreviewStats(d);
  });
}

// System stats + settings
function toggleSettings() {
  const panel = document.getElementById('settings-panel');
  panel.classList.toggle('visible');
}

function barClass(pct) {
  return pct > 85 ? 'crit' : pct > 60 ? 'warn' : '';
}

async function fetchSystem() {
  try {
    const res = await _apiFetch('/api/system');
    const s = await res.json();

    // Show version in logo
    if (s.version) {
      const ve = document.getElementById('app-version');
      if (ve) ve.textContent = s.version;
    }

    const cpuBar = document.getElementById('sys-cpu');
    const ramBar = document.getElementById('sys-ram');
    const diskBar = document.getElementById('sys-disk');

    const loadPct = Math.min((s.load_1m / (s.cpu_cores || 1)) * 100, 100);
    cpuBar.style.width = loadPct + '%';
    cpuBar.className = 'sys-bar-fill ' + barClass(loadPct);
    document.getElementById('sys-cpu-val').textContent = s.load_1m + '/' + s.cpu_cores;

    const memPct = s.mem_used_pct || 0;
    ramBar.style.width = Math.min(memPct, 100) + '%';
    ramBar.className = 'sys-bar-fill ' + barClass(memPct);
    const memUsedMb = s.mem_total_mb - s.mem_available_mb;
    const ramGB = (memUsedMb / 1024).toFixed(1) + '/' + (s.mem_total_mb / 1024).toFixed(0) + 'G';
    document.getElementById('sys-ram-val').textContent = ramGB;

    const diskPct = s.disk_used_pct || 0;
    diskBar.style.width = Math.min(diskPct, 100) + '%';
    diskBar.className = 'sys-bar-fill ' + barClass(diskPct);
    const diskGB = (s.disk_used_mb / 1024).toFixed(0) + '/' + (s.disk_total_mb / 1024).toFixed(0) + 'G';
    document.getElementById('sys-disk-val').textContent = diskGB;

    // Populate settings inputs
    if (s.settings) {
      document.getElementById('set-port-start').value = s.settings.port_start;
      document.getElementById('set-port-end').value = s.settings.port_end;
      document.getElementById('set-max-desktops').value = s.settings.max_desktops;
      document.getElementById('set-memory-limit').value = s.settings.memory_limit;
      document.getElementById('set-shm-size').value = s.settings.shm_size;
      document.getElementById('set-chrome-url').value = s.settings.chrome_url || 'none';
    }
  } catch (e) {}
}

async function saveSettings() {
  try {
    const body = {
      port_start: parseInt(document.getElementById('set-port-start').value),
      port_end: parseInt(document.getElementById('set-port-end').value),
      max_desktops: parseInt(document.getElementById('set-max-desktops').value),
      memory_limit: document.getElementById('set-memory-limit').value,
      shm_size: document.getElementById('set-shm-size').value,
      chrome_url: document.getElementById('set-chrome-url').value,
    };
    const res = await _apiFetch('/api/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    });
    const data = await res.json();
    if (data.ok) {
      document.getElementById('settings-panel').classList.remove('visible');
    }
  } catch (e) {}
}

// --- VNC integration (noVNC direct canvas -- replaces KasmVNC iframe) ---
let rfbConnection = null;
let rfbDesktopId = null;
let _vncReconnectTimer = null;
let _vncReconnectAttempts = 0;
const VNC_RECONNECT_MAX = 5;
let _noVNCModule = null;

// Lazy-load noVNC RFB module
async function _getRFB() {
  if (_noVNCModule) return _noVNCModule;
  _noVNCModule = (await import('/novnc/core/rfb.js')).default;
  return _noVNCModule;
}

function setVNCMode(mode) {
  _vncMode = mode;
  _saveState({vncMode: mode});
  // Switch to screen tab when clicking view/control
  if (_activeTab !== 'screen') {
    setActiveTab('screen');
  }
  document.getElementById('btn-mode-view').classList.toggle('active', mode === 'view');
  document.getElementById('btn-mode-control').classList.toggle('active', mode === 'control');
  if (rfbConnection) {
    rfbConnection.viewOnly = (mode === 'view');
  }
}

function _scheduleReconnect() {
  if (_vncReconnectTimer) return;
  if (_vncReconnectAttempts >= VNC_RECONNECT_MAX) {
    console.log('[VNC] max reconnect attempts reached');
    const screen = document.getElementById('preview-screen');
    if (screen && rfbDesktopId) {
      const btn = document.createElement('div');
      btn.style.cssText = 'display:flex;align-items:center;justify-content:center;height:100%;flex-direction:column;gap:8px';
      btn.innerHTML = '<span style="color:#606878;font-size:11px;letter-spacing:1px">Session disconnected</span>'
        + '<button style="background:none;color:#d0d4dc;border:1px solid #1a3a7a;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:11px;letter-spacing:0.5px" onclick="reconnectVNC()">reconnect</button>';
      screen.innerHTML = '';
      screen.appendChild(btn);
    }
    return;
  }
  const delay = Math.min(2000 * Math.pow(1.5, _vncReconnectAttempts), 10000);
  _vncReconnectAttempts++;
  console.log('[VNC] reconnecting in ' + Math.round(delay) + 'ms (attempt ' + _vncReconnectAttempts + ')');
  _vncReconnectTimer = setTimeout(() => {
    _vncReconnectTimer = null;
    if (rfbDesktopId) {
      connectVNC(rfbDesktopId, document.getElementById('preview-screen'));
    }
  }, delay);
}

function reconnectVNC() {
  _vncReconnectAttempts = 0;
  if (rfbDesktopId) {
    connectVNC(rfbDesktopId, document.getElementById('preview-screen'));
  }
}

async function connectVNC(desktopId, screenEl) {
  // Disconnect previous
  if (_vncReconnectTimer) { clearTimeout(_vncReconnectTimer); _vncReconnectTimer = null; }
  if (rfbConnection) { try { rfbConnection.disconnect(); } catch(e) {} rfbConnection = null; }

  if (desktopId !== rfbDesktopId) _vncReconnectAttempts = 0;
  rfbDesktopId = desktopId;

  screenEl.innerHTML = '<span class="vnc-status connecting">connecting...</span>';

  // Build WebSocket URL to our VNC proxy
  const wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsPort = location.port ? ':' + location.port : '';
  const wsUrl = wsProto + '//' + location.hostname + wsPort + '/vnc/' + desktopId;

  try {
    const RFB = await _getRFB();
    screenEl.innerHTML = '';

    rfbConnection = new RFB(screenEl, wsUrl);
    rfbConnection.viewOnly = (_vncMode === 'view');
    rfbConnection.scaleViewport = true;
    rfbConnection.showDotCursor = false;
    rfbConnection.clipViewport = false;
    rfbConnection.resizeSession = false;
    rfbConnection.background = '#0a0e14';
    rfbConnection.focusOnClick = true;

    // Force browser cursor visible -- noVNC tries to hide it via inline style
    const _forceDefaultCursor = () => {
      const allCanvas = screenEl.querySelectorAll('canvas');
      const allDivs = screenEl.querySelectorAll('div');
      // Force cursor on everything inside screenEl
      for (const el of screenEl.querySelectorAll('*')) {
        el.style.setProperty('cursor', 'default', 'important');
      }
      screenEl.style.setProperty('cursor', 'default', 'important');
      new MutationObserver((mutations) => {
        for (const m of mutations) {
          m.target.style.setProperty('cursor', 'default', 'important');
        }
      }).observe(screenEl, { attributes: true, attributeFilter: ['style'], subtree: true });
    };
    setTimeout(_forceDefaultCursor, 1000);
    setTimeout(_forceDefaultCursor, 3000);

    rfbConnection.addEventListener('connect', () => {
      console.log('[VNC] connected to', desktopId);
      _vncReconnectAttempts = 0;
      // Focus the canvas for keyboard input
      const c = screenEl.querySelector('canvas');
      if (c) c.focus();
    });

    rfbConnection.addEventListener('disconnect', (e) => {
      console.log('[VNC] disconnected from', desktopId, 'clean=' + e.detail.clean);
      rfbConnection = null;
      if (!e.detail.clean && rfbDesktopId === desktopId) {
        // Check if desktop still exists before reconnecting
        const d = _lastDesktops.find(x => x.id === desktopId);
        if (d && d.state === 'running') {
          _scheduleReconnect();
        } else {
          console.log('[VNC] desktop', desktopId, 'not running, skip reconnect');
        }
      }
    });

    rfbConnection.addEventListener('credentialsrequired', () => {
      // x11vnc runs with -nopw, no password needed
      rfbConnection.sendCredentials({ password: '' });
    });

  } catch (e) {
    console.error('[VNC] connect error:', e);
    screenEl.innerHTML = '<span style="color:#606878;font-size:11px">Connection failed</span>';
    _scheduleReconnect();
  }
}

function disconnectVNC() {
  if (_vncReconnectTimer) { clearTimeout(_vncReconnectTimer); _vncReconnectTimer = null; }
  if (rfbConnection) {
    try { rfbConnection.disconnect(); } catch (e) {}
    rfbConnection = null;
  }
  rfbDesktopId = null;
  _vncReconnectAttempts = 0;
}

// --- Overlay toggles (trail / dots) ---
let _overlayState = { trail: false, dots: true, enabled: true, cursor: true };

async function toggleOverlay(which) {
  if (!rfbDesktopId) return;
  const cb = document.getElementById('btn-toggle-' + which);
  _overlayState[which] = cb.checked;
  try {
    await _apiFetch('/api/overlay', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        id: rfbDesktopId,
        text: `enabled=${_overlayState.enabled ? 1 : 0},cursor=${_overlayState.cursor ? 1 : 0},dots=${_overlayState.dots ? 1 : 0},trail=${_overlayState.trail ? 1 : 0}`
      })
    });
  } catch (e) {
    console.log('[overlay] toggle failed:', e);
  }
}

// Init overlay toggle states
function _initOverlayButtons() {
  document.getElementById('btn-toggle-dots').checked = _overlayState.dots;
  document.getElementById('btn-toggle-trail').checked = _overlayState.trail;
}
setTimeout(_initOverlayButtons, 100);

// --- Click effect overlay ---
const clickOverlay = document.getElementById('click-overlay');
const clickCtx = clickOverlay.getContext('2d');
let clickDots = []; // [{x, y, time}]
let clickAnimFrame = null;

function resizeClickOverlay() {
  const rect = document.getElementById('preview-screen').getBoundingClientRect();
  clickOverlay.width = rect.width;
  clickOverlay.height = rect.height;
}

document.getElementById('preview-screen').addEventListener('mousedown', (e) => {
  if (_vncMode !== 'control') return;
  const rect = document.getElementById('preview-screen').getBoundingClientRect();
  clickDots.push({
    x: e.clientX - rect.left,
    y: e.clientY - rect.top,
    time: performance.now()
  });
  if (!clickAnimFrame) animateClicks();
});

function animateClicks() {
  const now = performance.now();
  const FADE_MS = 400;
  clickDots = clickDots.filter(d => now - d.time < FADE_MS + 50);

  resizeClickOverlay();
  clickCtx.clearRect(0, 0, clickOverlay.width, clickOverlay.height);

  for (const dot of clickDots) {
    const age = now - dot.time;
    const frac = Math.min(age / FADE_MS, 1.0);
    if (frac >= 1.0) continue;
    const alpha = 1.0 - frac;
    const r = 14 + frac * 10;

    // Soft yellow dot (layered circles)
    for (let layer = 0; layer < 3; layer++) {
      const lr = r * (1.0 - layer * 0.25);
      const la = alpha * (0.25 + layer * 0.12);
      clickCtx.beginPath();
      clickCtx.arc(dot.x, dot.y, lr, 0, Math.PI * 2);
      clickCtx.fillStyle = `rgba(255, 214, 0, ${la})`;
      clickCtx.fill();
    }
  }

  if (clickDots.length > 0) {
    clickAnimFrame = requestAnimationFrame(animateClicks);
  } else {
    clickAnimFrame = null;
  }
}

// Restore saved state from localStorage
(function restoreState() {
  const s = _savedState;
  // Restore panel width
  if (s.panelWidth) {
    document.getElementById('preview-panel').style.width = s.panelWidth + 'px';
  }
  // Restore VNC mode buttons
  if (s.vncMode) {
    document.getElementById('btn-mode-view').classList.toggle('active', s.vncMode === 'view');
    document.getElementById('btn-mode-control').classList.toggle('active', s.vncMode === 'control');
  }
  // Restore selected desktop after first data load
  if (s.panelVisible && s.selectedId) {
    const _origRefresh = refresh;
    let _restored = false;
    const _restoreOnce = async () => {
      await _origRefresh();
      if (!_restored && currentDesktops.find(d => d.id === s.selectedId)) {
        _restored = true;
        selectDesktop(s.selectedId);
      }
    };
    _restoreOnce();
  } else {
    refresh();
  }
})();
fetchSystem();
// ---------------------------------------------------------------------------
// WebSocket real-time updates (polling as fallback)
// ---------------------------------------------------------------------------
// Simple: server sends state, we render it. No local state computation.
let _wsConnected = false;
let _wsRetryMs = 1000;
let _pollInterval = null;

function _startPolling() {
  if (_pollInterval) return;
  _pollInterval = setInterval(refresh, 3000);
}

function _stopPolling() {
  if (_pollInterval) {
    clearInterval(_pollInterval);
    _pollInterval = null;
  }
}

function _applyStateChange(desktopId, state) {
  if (state === 'destroyed') {
    currentDesktops = currentDesktops.filter(d => d.id !== desktopId);
  } else {
    const existing = currentDesktops.find(d => d.id === desktopId);
    if (existing) {
      existing.state = state;
    } else {
      currentDesktops.push({id: desktopId, state: state, name: 'screenbox-' + desktopId});
    }
  }
  renderGrid(currentDesktops);
  updateStats(currentDesktops);
  // After state change, refresh to get full data (ports, resources, etc.)
  if (state === 'running') {
    setTimeout(refresh, 2000);
  }
}

function _connectWs() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${proto}//${location.host}/ws/events`);

  ws.onopen = () => {
    _wsConnected = true;
    _wsRetryMs = 1000;
    _stopPolling();
    console.log('[ws] connected');
    const el = document.getElementById('ws-status');
    if (el) { el.className = 'ws-status connected'; el.title = 'Connected'; }
  };

  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);

      // Full state from server (on connect or periodic sync)
      if (msg.event === 'init' || msg.full_state) {
        currentDesktops = msg.desktops || [];
        console.log('[ws] state:', currentDesktops.map(d => d.id + '=' + d.state + (d.assigned_name ? ' [' + d.assigned_name + ']' : '')).join(', ') || '(empty)');
        renderGrid(currentDesktops);
        updateStats(currentDesktops);
        return;
      }

      // Single desktop state change
      if (msg.desktop_id && msg.state) {
        console.log('[ws]', msg.desktop_id, '->', msg.state);
        _applyStateChange(msg.desktop_id, msg.state);
      }
    } catch (err) {
      console.error('[ws] error:', err);
    }
  };

  ws.onclose = () => {
    _wsConnected = false;
    console.log('[ws] disconnected, polling fallback');
    _startPolling();
    const el = document.getElementById('ws-status');
    if (el) { el.className = 'ws-status reconnecting'; el.title = 'Reconnecting...'; }
    setTimeout(_connectWs, _wsRetryMs);
    _wsRetryMs = Math.min(_wsRetryMs * 2, 30000);
  };

  ws.onerror = () => { ws.close(); };
}

// Start
_connectWs();
_startPolling();
// VNC thumbnails: connect once after render, reconnect on desktop list change
// No polling needed — VNC pushes frame updates automatically
setTimeout(refreshScreenshots, 2000); // initial connect after tiles render
setInterval(refreshScreenshots, 10000); // periodic reconnect check
setInterval(fetchSystem, 10000);


