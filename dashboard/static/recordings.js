/* Screenbox Dashboard -- Recordings tab module */
(function() {
  let _container = null;
  let _desktopId = null;
  let _active = false;
  let _refreshTimer = null;
  let _overlay = null;

  function init(container) {
    _container = container;
    _container.innerHTML = '<div class="rec-tab"><div class="rec-empty">select a desktop</div></div>';
    // Create player overlay (once, appended to body)
    _overlay = document.createElement('div');
    _overlay.className = 'rec-overlay';
    _overlay.innerHTML = `
      <div class="rec-overlay-box">
        <div class="rec-overlay-header">
          <span class="rec-overlay-title" id="rec-player-title"></span>
          <div class="rec-overlay-controls">
            <select id="rec-speed" onchange="SB.recordings.setSpeed(this.value)">
              <option value="0.5">0.5x</option>
              <option value="1" selected>1x</option>
              <option value="2">2x</option>
              <option value="4">4x</option>
              <option value="8">8x</option>
            </select>
            <a id="rec-download" class="rec-btn-small" download>download</a>
            <button class="rec-btn-small" onclick="SB.recordings.closePlayer()">close</button>
          </div>
        </div>
        <video id="rec-video" controls></video>
      </div>`;
    _overlay.addEventListener('click', function(e) {
      if (e.target === _overlay) closePlayer();
    });
    document.body.appendChild(_overlay);
  }

  function activate(desktopId) {
    _desktopId = desktopId;
    _active = true;
    _render();
    _refresh();
    if (_refreshTimer) clearInterval(_refreshTimer);
    _refreshTimer = setInterval(_refresh, 5000);
  }

  function deactivate() {
    _active = false;
    if (_refreshTimer) {
      clearInterval(_refreshTimer);
      _refreshTimer = null;
    }
  }

  function _render() {
    if (!_container) return;
    _container.innerHTML = `
      <div class="rec-tab">
        <div class="rec-list" id="rec-list">
          <div class="rec-empty">loading...</div>
        </div>
      </div>`;
  }

  async function _refresh() {
    if (!_active || !_desktopId) return;
    try {
      const res = await _apiFetch('/api/recordings?id=' + _desktopId);
      const recs = await res.json();

      const list = document.getElementById('rec-list');
      if (!list) return;

      if (!recs || recs.length === 0) {
        list.innerHTML = '<div class="rec-empty">no recordings</div>';
        return;
      }

      list.innerHTML = recs.map(r => {
        const date = new Date(r.created * 1000);
        const dateStr = date.toLocaleString('en-GB', {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'});
        const size = r.size_mb < 0.1 ? '<0.1' : r.size_mb.toFixed(1);
        return `
          <div class="rec-item" onclick="SB.recordings.play('${r.file}')">
            <span class="rec-item-icon">&#9654;</span>
            <span class="rec-item-name">${r.file.replace('.mp4','')}</span>
            <span class="rec-item-meta">${size} MB</span>
            <span class="rec-item-date">${dateStr}</span>
            <button class="rec-btn-small rec-btn-danger" onclick="event.stopPropagation(); SB.recordings.deleteRec('${r.file}')">del</button>
          </div>`;
      }).join('');
    } catch(e) {
      console.error('[recordings] refresh error:', e);
    }
  }

  async function toggleRecording() {
    if (!_desktopId) return;
    const btn = document.getElementById('rec-toggle');
    const status = document.getElementById('rec-status-text');

    if (btn && btn.classList.contains('rec-btn-recording')) {
      try {
        await _apiFetch('/api/record', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({id: _desktopId, action: 'stop'}),
        });
      } catch(e) {}
      btn.classList.remove('rec-btn-recording');
      btn.innerHTML = '<span class="rec-dot"></span> REC';
      if (status) status.textContent = '';
      setTimeout(_refresh, 2000);
    } else {
      try {
        const res = await _apiFetch('/api/record', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({id: _desktopId, action: 'start'}),
        });
        const data = await res.json();
        if (data.error) {
          if (status) status.textContent = data.error;
          return;
        }
      } catch(e) {
        if (status) status.textContent = 'failed to start';
        return;
      }
      if (btn) {
        btn.classList.add('rec-btn-recording');
        btn.innerHTML = '<span class="rec-dot rec-dot-active"></span> STOP';
      }
      if (status) status.textContent = 'recording...';
    }
  }

  function play(filename) {
    if (!_overlay) return;
    const video = document.getElementById('rec-video');
    const title = document.getElementById('rec-player-title');
    const download = document.getElementById('rec-download');
    if (!video) return;

    const url = _apiUrl('/api/recording/stream?id=' + _desktopId + '&file=' + filename);
    video.src = url;
    video.playbackRate = 1;
    document.getElementById('rec-speed').value = '1';
    title.textContent = filename.replace('.mp4', '');
    download.href = url;
    download.download = filename;
    _overlay.classList.add('visible');
    video.play().catch(() => {});
  }

  function closePlayer() {
    if (!_overlay) return;
    const video = document.getElementById('rec-video');
    _overlay.classList.remove('visible');
    if (video) { video.pause(); video.src = ''; }
  }

  function setSpeed(speed) {
    const video = document.getElementById('rec-video');
    if (video) video.playbackRate = parseFloat(speed);
  }

  async function deleteRec(filename) {
    const ok = await showModal('delete recording <span class="modal-id">' + filename.replace('.mp4','') + '</span>?', true);
    if (!ok) return;
    try {
      await _apiFetch('/api/recording/delete', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: _desktopId, file: filename}),
      });
      _refresh();
    } catch(e) {
      console.error('[recordings] delete error:', e);
    }
  }

  // Esc to close player
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && _overlay && _overlay.classList.contains('visible')) {
      closePlayer();
    }
  });

  // Export
  window.SB.recordings = {
    init, activate, deactivate,
    toggleRecording, play, closePlayer, setSpeed, deleteRec,
  };
})();
