/**
 * 日志查看器
 *
 * 独立模块，不修改 app.js。通过 index.html <script> 加载。
 * 暴露 window._openLogViewer 给 app.js 调用。
 */

(function() {
  let pollTimer = null;
  let bound = false;

  function fetchLogs() {
    const sourceEl = document.getElementById('logSourceFilter');
    const levelEl = document.getElementById('logLevelFilter');
    const searchEl = document.getElementById('logSearchInput');
    if (!sourceEl) return;

    const source = sourceEl.value;
    const level = levelEl.value;
    const search = searchEl.value.trim().toLowerCase();

    window.apiClient.get('/api/v1/settings/logs?source=' + source + '&level=' + level + '&lines=500')
      .then(function(res) {
        // 注意：apiClient 拦截器已经返回 response.data，所以 res 就是 {code, data}
        if (res.code !== 0) return;
        let text = res.data.logs || '';
        if (search) text = text.split('\n').filter(function(l) { return l.toLowerCase().includes(search); }).join('\n');

        const el = document.getElementById('logViewerContent');
        if (!el) return;
        el.innerHTML = text.split('\n').map(function(line) {
          let cls = 'log-info';
          if (line.indexOf('[ERROR]') >= 0) cls = 'log-error';
          else if (line.indexOf('[WARN]') >= 0 || line.indexOf('[WARNING]') >= 0) cls = 'log-warn';
          const escaped = line.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
          return '<span class="' + cls + '">' + escaped + '</span>';
        }).join('\n');
        el.scrollTop = el.scrollHeight;

        document.getElementById('logViewerStatus').textContent =
          '共 ' + res.data.total + ' 行' +
          (search ? '，匹配 ' + text.split('\n').filter(Boolean).length + ' 行' : '') +
          ' · 自动刷新中（每3秒）';
      })
      .catch(function(e) {
        const st = document.getElementById('logViewerStatus');
        if (st) st.textContent = '获取失败: ' + e.message;
      });
  }

  function closeLogViewer() {
    const overlay = document.getElementById('logViewerOverlay');
    if (overlay) overlay.style.display = 'none';
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  function downloadLogs() {
    window.apiClient.get('/api/v1/settings/logs/download', { responseType: 'blob' })
      .then(function(res) {
        // 拦截器返回 response.data，所以 res 就是 blob
        const url = URL.createObjectURL(new Blob([res]));
        const a = document.createElement('a');
        a.href = url;
        a.download = 'harness-logs.zip';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      })
      .catch(function(e) { alert('导出失败: ' + e.message); });
  }

  function bindEvents() {
    if (bound) return;
    const overlay = document.getElementById('logViewerOverlay');
    if (!overlay) {
      console.warn('[log-viewer] logViewerOverlay not found in DOM');
      return;
    }
    bound = true;

    document.getElementById('logCloseBtn').addEventListener('click', closeLogViewer);
    document.getElementById('logRefreshBtn').addEventListener('click', fetchLogs);
    document.getElementById('logSourceFilter').addEventListener('change', fetchLogs);
    document.getElementById('logLevelFilter').addEventListener('change', fetchLogs);
    document.getElementById('logSearchInput').addEventListener('keydown', function(e) {
      if (e.key === 'Enter') fetchLogs();
    });
    document.getElementById('logDownloadBtn').addEventListener('click', downloadLogs);
    document.getElementById('logOpenDirBtn').addEventListener('click', function() {
      if (window.electronAPI && window.electronAPI.openLogDir) window.electronAPI.openLogDir();
    });
    overlay.addEventListener('click', function(e) {
      if (e.target === overlay) closeLogViewer();
    });
  }

  // 暴露给 app.js
  window._openLogViewer = function() {
    bindEvents();
    const overlay = document.getElementById('logViewerOverlay');
    console.log('[log-viewer] _openLogViewer called, overlay:', overlay, 'current display:', overlay && overlay.style.display);
    if (!overlay) {
      alert('日志面板未加载，请检查 index.html 中是否包含 #logViewerOverlay');
      return;
    }
    overlay.style.display = 'flex';
    overlay.style.zIndex = '99999';
    console.log('[log-viewer] After set display=flex, computed:', window.getComputedStyle(overlay).display, 'z-index:', window.getComputedStyle(overlay).zIndex);
    fetchLogs();
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(fetchLogs, 3000);
  };

  console.log('[log-viewer] Module loaded, window._openLogViewer ready');
})();
