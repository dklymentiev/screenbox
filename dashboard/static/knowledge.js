/* Screenbox Dashboard -- Knowledge Library tab module */
(function() {
  'use strict';

  let _container = null;
  let _desktopId = null;       // currently viewed desktop
  let _scope = '';             // '' = global, 'demo-1' = per-desktop
  let _active = false;
  let _apps = [];
  let _selectedSlug = null;
  let _selectedData = null;
  let _knownDesktops = [];     // desktops with own knowledge

  function _scopeParam() {
    return _scope ? '?desktop_id=' + encodeURIComponent(_scope) : '';
  }
  function _scopeParamAmp() {
    return _scope ? '&desktop_id=' + encodeURIComponent(_scope) : '';
  }

  function init(el) {
    _container = el;
    _container.innerHTML = '<div class="knowledge-wrap"></div>';
  }

  function activate(desktopId) {
    _desktopId = desktopId;
    _scope = desktopId || '';
    _active = true;
    _loadDesktops();
    _loadApps();
  }

  function setScope(scope) {
    _scope = scope;
    _selectedSlug = null;
    _selectedData = null;
    _loadApps();
  }

  function deactivate() {
    _active = false;
  }

  // --- API helpers ---
  async function _fetch(url) {
    const res = await _apiFetch(url);
    return res.json();
  }

  async function _post(url, body) {
    const res = await _apiFetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    return res.json();
  }

  async function _del(url) {
    const res = await _apiFetch(url, { method: 'DELETE' });
    return res.json();
  }

  // --- Load desktops with own knowledge ---
  async function _loadDesktops() {
    try {
      _knownDesktops = await _fetch('/api/knowledge/desktops');
    } catch (e) {
      _knownDesktops = [];
    }
  }

  // --- Load app list ---
  async function _loadApps() {
    if (!_active) return;
    try {
      _apps = await _fetch('/api/knowledge' + _scopeParam());
      _render();
    } catch (e) {
      _container.querySelector('.knowledge-wrap').innerHTML =
        '<div class="k-empty">Failed to load knowledge: ' + e.message + '</div>';
    }
  }

  // --- Load single app ---
  async function _loadApp(slug) {
    _selectedSlug = slug;
    try {
      _selectedData = await _fetch('/api/knowledge/' + slug + _scopeParam());
      _render();
    } catch (e) {
      _selectedData = null;
      _render();
    }
  }

  // --- Render ---
  function _render() {
    const wrap = _container.querySelector('.knowledge-wrap');
    if (!wrap) return;

    let html = '<div class="k-header">';
    html += '<span class="k-title">Knowledge Library</span>';
    // Scope selector: global or per-desktop
    html += '<select class="k-scope-select" onchange="SB.knowledge.setScope(this.value)">';
    html += '<option value=""' + (_scope === '' ? ' selected' : '') + '>global</option>';
    if (_desktopId && _knownDesktops.indexOf(_desktopId) < 0) {
      // Always show the current desktop even if it has no knowledge yet
      html += '<option value="' + esc(_desktopId) + '"' + (_scope === _desktopId ? ' selected' : '') + '>' + esc(_desktopId) + '</option>';
    }
    for (const did of _knownDesktops) {
      html += '<option value="' + esc(did) + '"' + (_scope === did ? ' selected' : '') + '>' + esc(did) + '</option>';
    }
    html += '</select>';
    html += '<button class="k-btn k-btn-new" onclick="SB.knowledge.showNewApp()">+ new app</button>';
    html += '<button class="k-btn k-btn-import" id="k-select-btn" onclick="SB.knowledge.selectFromServer()">select</button>';
    html += '<button class="k-btn k-btn-import" onclick="SB.knowledge.uploadFile()">upload</button>';
    html += '</div>';

    // App list (sidebar-style chips)
    html += '<div class="k-apps">';
    if (_apps.length === 0) {
      html += '<div class="k-empty">No knowledge files yet. Add facts via MCP tools or the dashboard.</div>';
    }
    for (const app of _apps) {
      const sel = app.slug === _selectedSlug ? ' k-app-sel' : '';
      const scopeTag = (_scope && app.scope === 'global') ? ' <span class="k-scope-tag">global</span>' : '';
      html += '<button class="k-app' + sel + '" onclick="SB.knowledge.selectApp(\'' + esc(app.slug) + '\')">'
        + '<span class="k-app-name">' + esc(app.app || app.slug) + scopeTag + '</span>'
        + '<span class="k-app-count">' + app.facts_count + '</span>'
        + '</button>';
    }
    html += '</div>';

    // Facts panel
    if (_selectedSlug && _selectedData) {
      const facts = _selectedData.facts || [];
      html += '<div class="k-facts">';
      html += '<div class="k-facts-header">';
      html += '<span class="k-facts-title">' + esc(_selectedData.app || _selectedSlug) + '</span>';
      html += '<span class="k-facts-count">' + facts.length + ' facts</span>';
      html += '<button class="k-btn-del-app" onclick="SB.knowledge.deleteApp(\'' + esc(_selectedSlug) + '\')" title="Delete all knowledge for this app">delete app</button>';
      html += '</div>';

      // Add fact form
      html += '<div class="k-add-form" id="k-add-form">'
        + '<textarea class="k-input" id="k-fact-text" placeholder="New fact (e.g. Alt+Y confirms Yes in dialogs)" rows="2"></textarea>'
        + '<input class="k-input k-triggers-input" id="k-fact-triggers" placeholder="Trigger words (comma-separated, or auto-extracted)">'
        + '<div class="k-add-actions">'
        + '<button class="k-btn k-btn-save" onclick="SB.knowledge.addFact()">add fact</button>'
        + '</div>'
        + '</div>';

      // Fact list
      if (facts.length === 0) {
        html += '<div class="k-empty">No facts yet. Add one above.</div>';
      }
      for (let i = 0; i < facts.length; i++) {
        const f = facts[i];
        const triggers = (f.triggers || []).join(', ');
        const date = f.added ? f.added.split('T')[0] : '';
        const source = f.source || '';
        html += '<div class="k-fact">'
          + '<div class="k-fact-text">' + esc(f.text) + '</div>'
          + '<div class="k-fact-meta">'
          + (triggers ? '<span class="k-triggers">' + esc(triggers) + '</span>' : '')
          + (date ? '<span class="k-date">' + date + '</span>' : '')
          + (source ? '<span class="k-source">' + esc(source) + '</span>' : '')
          + '<button class="k-btn-del" onclick="SB.knowledge.deleteFact(' + i + ')" title="Delete fact">x</button>'
          + '</div>'
          + '</div>';
      }
      html += '</div>';
    }

    wrap.innerHTML = html;
  }

  // --- New app dialog ---
  function showNewApp() {
    const name = prompt('App name (e.g. LibreOffice Calc):');
    if (!name) return;
    const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
    if (!slug) return;
    _selectedSlug = slug;
    _selectedData = { app: name, slug: slug, facts: [] };
    if (!_apps.find(a => a.slug === slug)) {
      _apps.push({ slug: slug, app: name, facts_count: 0 });
    }
    _render();
  }

  // --- Select app ---
  function selectApp(slug) {
    _loadApp(slug);
  }

  // --- Add fact ---
  async function addFact() {
    if (!_selectedSlug) return;
    const textEl = document.getElementById('k-fact-text');
    const triggersEl = document.getElementById('k-fact-triggers');
    if (!textEl) return;

    const text = textEl.value.trim();
    if (!text) return;

    const triggersRaw = triggersEl ? triggersEl.value.trim() : '';
    const triggers = triggersRaw
      ? triggersRaw.split(',').map(t => t.trim()).filter(Boolean)
      : [];

    try {
      const factBody = {
        text: text,
        triggers: triggers,
        app_name: _selectedData ? _selectedData.app : _selectedSlug,
      };
      if (_scope) factBody.desktop_id = _scope;
      const result = await _post('/api/knowledge/' + _selectedSlug + '/facts' + _scopeParam(), factBody);
      if (result.error) {
        alert(result.error);
        return;
      }
      await _loadApp(_selectedSlug);
      await _loadApps();
    } catch (e) {
      alert('Failed: ' + e.message);
    }
  }

  // --- Delete fact ---
  async function deleteFact(index) {
    if (!_selectedSlug) return;
    if (!confirm('Delete this fact?')) return;
    try {
      const result = await _del('/api/knowledge/' + _selectedSlug + '/facts/' + index + _scopeParam());
      if (result.error) {
        alert(result.error);
        return;
      }
      await _loadApp(_selectedSlug);
      await _loadApps();
    } catch (e) {
      alert('Failed: ' + e.message);
    }
  }

  // --- Delete entire app ---
  async function deleteApp(slug) {
    const app = _apps.find(a => a.slug === slug);
    const name = app ? (app.app || slug) : slug;
    if (!confirm('Delete ALL knowledge for "' + name + '"?')) return;
    try {
      const result = await _del('/api/knowledge/' + slug + _scopeParam());
      if (result.error) {
        alert(result.error);
        return;
      }
      _selectedSlug = null;
      _selectedData = null;
      await _loadApps();
    } catch (e) {
      alert('Failed: ' + e.message);
    }
  }

  // --- Select from server ---
  async function selectFromServer() {
    const btn = document.getElementById('k-select-btn');
    if (btn) {
      btn.textContent = 'scanning...';
      btn.classList.add('k-btn-loading');
    }
    try {
      const files = await _fetch('/api/knowledge/importable' + _scopeParam());
      if (btn) {
        btn.textContent = 'select';
        btn.classList.remove('k-btn-loading');
      }
      _showImportModal(files);
    } catch (e) {
      if (btn) {
        btn.textContent = 'select';
        btn.classList.remove('k-btn-loading');
      }
      alert('Failed to scan: ' + e.message);
    }
  }

  // --- Upload from local machine ---
  function uploadFile() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = async () => {
      const file = input.files[0];
      if (!file) return;
      if (file.size > 1024 * 1024) {
        alert('File too large (max 1MB)');
        return;
      }
      const formData = new FormData();
      formData.append('file', file);
      if (_scope) formData.append('desktop_id', _scope);
      try {
        const res = await fetch('/api/knowledge/upload', {
          method: 'POST',
          body: formData,
        });
        const result = await res.json();
        if (result.error) {
          alert(result.error);
          return;
        }
        await _loadApps();
        if (result.slug) _loadApp(result.slug);
      } catch (e) {
        alert('Upload failed: ' + e.message);
      }
    };
    input.click();
  }

  function _slugFromFilename(name) {
    return name.replace(/\.json$/, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  }

  function _showImportModal(serverFiles) {
    const existingSlugs = new Set(_apps.map(a => a.slug));

    const overlay = document.createElement('div');
    overlay.className = 'k-import-overlay';

    let html = '<div class="k-import-box">';
    html += '<div class="k-import-title">Select Knowledge</div>';

    if (serverFiles.length > 0) {
      html += '<div class="k-import-section">Available on server</div>';
      html += '<div class="k-import-list">';
      for (let i = 0; i < serverFiles.length; i++) {
        const f = serverFiles[i];
        const slug = f.slug || _slugFromFilename(f.name);
        const exists = existingSlugs.has(slug);
        const existingApp = exists ? _apps.find(a => a.slug === slug) : null;

        html += '<div class="k-import-row" data-idx="' + i + '">';
        html += '<div class="k-import-file-info">';
        html += '<span class="k-import-name">' + esc(f.name) + '</span>';
        html += '<span class="k-import-info">' + f.facts + ' facts -- ' + esc(f.source) + '</span>';
        html += '</div>';

        if (exists) {
          html += '<div class="k-import-exists">';
          html += '<span class="k-import-exists-label">"' + esc(existingApp.app || slug) + '" already in library (' + existingApp.facts_count + ' facts)</span>';
          html += '<div class="k-import-row-actions">';
          html += '<button class="k-btn k-btn-merge" data-idx="' + i + '" data-mode="merge">merge</button>';
          html += '<button class="k-btn k-btn-replace" data-idx="' + i + '" data-mode="replace">replace</button>';
          html += '</div>';
          html += '</div>';
        } else {
          html += '<div class="k-import-row-actions">';
          html += '<button class="k-btn k-btn-merge" data-idx="' + i + '" data-mode="merge">import</button>';
          html += '</div>';
        }
        html += '</div>';
      }
      html += '</div>';
    } else {
      html += '<div class="k-import-empty">No knowledge files found on server.</div>';
      html += '<div class="k-import-hint">Use the "upload" button to add .json files from your machine.</div>';
    }

    html += '<div class="k-import-status" id="k-import-status"></div>';
    html += '<div class="k-import-actions"><button class="k-btn k-import-cancel">close</button></div>';
    html += '</div>';

    overlay.innerHTML = html;
    document.body.appendChild(overlay);

    // Action button handlers
    overlay.querySelectorAll('.k-btn-merge, .k-btn-replace').forEach(btn => {
      btn.onclick = async () => {
        if (btn.disabled) return;
        const idx = parseInt(btn.dataset.idx);
        const mode = btn.dataset.mode;
        const f = serverFiles[idx];
        const row = overlay.querySelector('.k-import-row[data-idx="' + idx + '"]');
        const statusEl = document.getElementById('k-import-status');

        // Disable all action buttons in this row
        row.querySelectorAll('button').forEach(b => { b.disabled = true; b.classList.add('k-import-disabled'); });
        btn.textContent = mode === 'replace' ? 'replacing...' : 'importing...';
        btn.classList.add('k-btn-loading');

        try {
          const res = await _apiFetch('/api/knowledge/import-server', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ path: f.path, mode: mode }),
          });
          const result = await res.json();

          if (result.error) {
            statusEl.className = 'k-import-status k-status-error';
            statusEl.textContent = result.error;
            btn.textContent = mode;
            btn.classList.remove('k-btn-loading');
            row.querySelectorAll('button').forEach(b => { b.disabled = false; b.classList.remove('k-import-disabled'); });
            return;
          }

          // Success -- update row
          row.innerHTML = '<div class="k-import-file-info">'
            + '<span class="k-import-name">' + esc(f.name) + '</span>'
            + '<span class="k-import-done-info">'
            + (mode === 'replace' ? 'replaced' : (result.imported + ' imported'))
            + (result.skipped ? ', ' + result.skipped + ' skipped' : '')
            + ' -- ' + result.total + ' total</span>'
            + '</div>';
          row.classList.add('k-import-row-done');

          statusEl.className = 'k-import-status k-status-ok';
          statusEl.textContent = (mode === 'replace' ? 'Replaced' : 'Imported into') + ' "' + (result.app || '') + '"';

          _loadApps();
          if (result.slug) _loadApp(result.slug);
        } catch (e) {
          statusEl.className = 'k-import-status k-status-error';
          statusEl.textContent = 'Failed: ' + e.message;
          btn.textContent = mode;
          btn.classList.remove('k-btn-loading');
          row.querySelectorAll('button').forEach(b => { b.disabled = false; b.classList.remove('k-import-disabled'); });
        }
      };
    });

    overlay.querySelector('.k-import-cancel').onclick = () => overlay.remove();
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  }

  // Expose module
  SB.knowledge = {
    init: init,
    activate: activate,
    deactivate: deactivate,
    setScope: setScope,
    selectApp: selectApp,
    showNewApp: showNewApp,
    addFact: addFact,
    deleteFact: deleteFact,
    deleteApp: deleteApp,
    selectFromServer: selectFromServer,
    uploadFile: uploadFile,
  };
})();
