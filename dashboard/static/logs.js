/* Screenbox Dashboard -- Logs tab module */
(function() {
  'use strict';

  let _container = null;
  let _desktopId = null;
  let _pollTimer = null;
  let _entries = [];
  let _lastTimestamp = null;
  let _expandedIdx = new Set();

  function init(container) {
    _container = container;
  }

  function activate(desktopId) {
    if (!desktopId) {
      _renderEmpty('select a desktop');
      return;
    }
    // If switching desktops, reset
    if (desktopId !== _desktopId) {
      _desktopId = desktopId;
      _entries = [];
      _lastTimestamp = null;
      _expandedIdx = new Set();
    }
    _fetchLogs();
    _startPoll();
  }

  function deactivate() {
    _stopPoll();
  }

  function _startPoll() {
    _stopPoll();
    _pollTimer = setInterval(_fetchNew, 3000);
  }

  function _stopPoll() {
    if (_pollTimer) {
      clearInterval(_pollTimer);
      _pollTimer = null;
    }
  }

  async function _fetchLogs() {
    if (!_desktopId) return;
    _renderLoading();
    try {
      const res = await _apiFetch('/api/logs/' + encodeURIComponent(_desktopId) + '/recent?limit=100');
      if (!res.ok) {
        const text = await res.text();
        _renderEmpty('failed to load logs: ' + res.status);
        return;
      }
      const data = await res.json();
      _entries = Array.isArray(data) ? data : [];
      if (_entries.length > 0) {
        _lastTimestamp = _entries[_entries.length - 1].timestamp || null;
      }
      _render();
    } catch (e) {
      _renderEmpty('error: ' + e.message);
    }
  }

  async function _fetchNew() {
    if (!_desktopId) return;
    try {
      let url = '/api/logs/' + encodeURIComponent(_desktopId) + '/recent?limit=50';
      if (_lastTimestamp) {
        url += '&after=' + encodeURIComponent(_lastTimestamp);
      }
      const res = await _apiFetch(url);
      if (!res.ok) return;
      const data = await res.json();
      if (!Array.isArray(data) || data.length === 0) return;

      // Deduplicate: only add entries with timestamps after last known
      const existing = new Set(_entries.map(e => e.timestamp + '|' + e.tool));
      let added = 0;
      for (const entry of data) {
        const key = entry.timestamp + '|' + entry.tool;
        if (!existing.has(key)) {
          _entries.push(entry);
          added++;
        }
      }
      if (added > 0) {
        _lastTimestamp = _entries[_entries.length - 1].timestamp || _lastTimestamp;
        // Cap at 500 entries
        if (_entries.length > 500) {
          _entries = _entries.slice(-500);
        }
        _render();
        // Auto-scroll to bottom
        const list = _container.querySelector('.logs-list');
        if (list) {
          list.scrollTop = list.scrollHeight;
        }
      }
    } catch (e) {
      // Silently ignore poll errors
    }
  }

  function _renderLoading() {
    if (!_container) return;
    _container.innerHTML = '<div class="tab-placeholder">loading...</div>';
  }

  function _renderEmpty(msg) {
    if (!_container) return;
    _container.innerHTML = '<div class="tab-placeholder">' + _esc(msg) + '</div>';
  }

  function _esc(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  function _fmtTime(ts) {
    if (!ts) return '--:--:--';
    try {
      const d = new Date(ts);
      return d.toLocaleTimeString('en-GB', { hour12: false });
    } catch (e) {
      return ts.substring(11, 19) || '--:--:--';
    }
  }

  function _fmtArgs(args) {
    if (!args || typeof args !== 'object') return '';
    const parts = [];
    for (const [k, v] of Object.entries(args)) {
      if (typeof v === 'string') {
        parts.push(k + '="' + v + '"');
      } else {
        parts.push(k + '=' + JSON.stringify(v));
      }
    }
    return parts.join(', ');
  }

  function _fmtDuration(ms) {
    if (ms == null) return '';
    if (ms < 1000) return ms + 'ms';
    return (ms / 1000).toFixed(1) + 's';
  }

  function _render() {
    if (!_container) return;

    let html = '<div class="logs-header">';
    html += '<span class="logs-title">' + _esc(_desktopId || '') + ' -- ' + _entries.length + ' entries</span>';
    html += '<button class="logs-download" onclick="SB.logs._download()">download</button>';
    html += '</div>';
    html += '<div class="logs-list">';

    for (let i = 0; i < _entries.length; i++) {
      const e = _entries[i];
      const isError = e.level === 'error';
      const isExpanded = _expandedIdx.has(i);
      const errorTag = isError ? ' <span class="log-error">[ERROR]</span>' : '';
      const dur = _fmtDuration(e.duration_ms);
      const durTag = dur ? ' <span class="log-duration">' + dur + '</span>' : '';
      const argsShort = _fmtArgs(e.args);
      const toolDisplay = _esc(e.tool || e.msg || '?');

      html += '<div class="log-entry' + (isError ? ' log-entry-error' : '') + (isExpanded ? ' expanded' : '') + '" data-idx="' + i + '">';
      html += '<div class="log-summary">';
      html += '<span class="log-time">[' + _fmtTime(e.timestamp) + ']</span> ';
      html += '<span class="log-tool">' + toolDisplay + '</span>';
      if (argsShort) {
        html += '<span class="log-args">(' + _esc(argsShort) + ')</span>';
      }
      html += durTag + errorTag;
      html += '</div>';

      if (isExpanded) {
        html += '<pre class="log-detail">' + _esc(JSON.stringify(e, null, 2)) + '</pre>';
      }
      html += '</div>';
    }

    html += '</div>';
    _container.innerHTML = html;

    // Attach click handlers for expand/collapse
    const entries = _container.querySelectorAll('.log-entry');
    entries.forEach(el => {
      el.addEventListener('click', () => {
        const idx = parseInt(el.dataset.idx, 10);
        if (_expandedIdx.has(idx)) {
          _expandedIdx.delete(idx);
        } else {
          _expandedIdx.add(idx);
        }
        _render();
      });
    });

    // Auto-scroll to bottom on initial load
    const list = _container.querySelector('.logs-list');
    if (list && _entries.length > 0) {
      list.scrollTop = list.scrollHeight;
    }
  }

  function _download() {
    if (!_desktopId) return;
    const url = _apiUrl('/api/logs/' + encodeURIComponent(_desktopId) + '/download');
    window.open(url, '_blank');
  }

  // Inject styles
  const style = document.createElement('style');
  style.textContent = `
    .logs-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 8px 14px;
      border-bottom: 1px solid #151a24;
      flex-shrink: 0;
    }
    .logs-title {
      font-size: 10px;
      color: #606878;
      letter-spacing: 1px;
      text-transform: uppercase;
      font-weight: 300;
    }
    .logs-download {
      background: none;
      border: 1px solid #1c2030;
      color: #606878;
      cursor: pointer;
      font-size: 10px;
      padding: 3px 10px;
      border-radius: 3px;
      letter-spacing: 1px;
      font-weight: 300;
      transition: color 0.15s, border-color 0.15s;
    }
    .logs-download:hover { color: #c0c6d0; border-color: #1a3a7a; }
    .logs-list {
      flex: 1;
      overflow-y: auto;
      padding: 4px 0;
      font-family: 'SF Mono', 'Consolas', 'Monaco', monospace;
      font-size: 11px;
      line-height: 1.6;
      scrollbar-width: thin;
      scrollbar-color: #1c2030 transparent;
    }
    .logs-list::-webkit-scrollbar { width: 4px; }
    .logs-list::-webkit-scrollbar-track { background: transparent; }
    .logs-list::-webkit-scrollbar-thumb { background: #1c2030; border-radius: 2px; }
    .logs-list::-webkit-scrollbar-thumb:hover { background: #1a3a7a; }
    .log-entry {
      padding: 2px 14px;
      cursor: pointer;
      transition: background 0.1s;
      border-left: 2px solid transparent;
    }
    .log-entry:hover { background: rgba(26,58,122,0.08); }
    .log-entry.expanded { background: rgba(26,58,122,0.05); border-left-color: #1a3a7a; }
    .log-entry-error { border-left-color: #b05050; }
    .log-entry-error:hover { background: rgba(176,80,80,0.08); }
    .log-summary {
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .log-time { color: #505868; }
    .log-tool { color: #8090a8; font-weight: 400; }
    .log-args { color: #606878; margin-left: 1px; }
    .log-duration { color: #505868; margin-left: 4px; }
    .log-error {
      color: #b05050;
      font-weight: 600;
      margin-left: 4px;
    }
    .log-detail {
      margin: 4px 0 6px 0;
      padding: 8px 12px;
      background: #080c12;
      border: 1px solid #1c2030;
      border-radius: 3px;
      color: #808898;
      font-size: 10px;
      line-height: 1.5;
      max-height: 300px;
      overflow-y: auto;
      white-space: pre-wrap;
      word-break: break-all;
    }
  `;
  document.head.appendChild(style);

  // Register module
  window.SB.logs = {
    init: init,
    activate: activate,
    deactivate: deactivate,
    _download: _download
  };
})();
