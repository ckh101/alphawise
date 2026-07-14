/**
 * Harness AI — Apple Design
 */

console.log('[Harness AI] Application starting');

const appState = {
    messages: [],
    sessionId: null,
    isAnalyzing: false,
    currentSymbol: null,
    uploadedFiles: [],  // [{ file_id, filename, content_text }]
    activeConversationId: null,
    sidebarOpen: true,
    editMode: false
};

document.addEventListener('DOMContentLoaded', async () => {
    await initializeApp();
    setupEventListeners();

    // 禁用输入直到后端就绪
    document.getElementById('chatInput').disabled = true;

    // 等待后端就绪后初始化应用
    const onBackendReady = async () => {
        // 移除 loading overlay
        const overlay = document.getElementById('startupOverlay');
        if (overlay) {
            overlay.classList.add('fade-out');
            setTimeout(() => overlay.remove(), 300);
        }

        // 启用输入
        document.getElementById('chatInput').disabled = false;

        // 加载数据
        _updateStartupStatus('加载配置…');
        await checkBackendConnection();
        _subscribeFeishuEvents();
        _loadChannelSelector();
        _updateStartupStatus('加载会话…');
        loadSessionList();
        startNewChat();
        _initChannelDropdown();
    };

    if (window.electronAPI?.onBackendReady) {
        // Electron：先快速检测 Worker 是否已就绪（刷新页面场景），否则等 IPC 通知
        let ready = false;
        let triggered = false;
        const runOnce = () => { if (!triggered) { triggered = true; onBackendReady(); } };
        window.electronAPI.onBackendReady(runOnce);
        try {
            const r = await fetch('http://127.0.0.1:9999/ready').then(res => res.json());
            if (r && r.status === 'ok') ready = true;
        } catch { /* Worker 未就绪 */ }
        if (ready) runOnce();
    } else {
        // 非 Electron（浏览器）：前端自行轮询等待后端
        _waitForBackendThen(onBackendReady);
    }

    // === 通道下拉 ===

    // 暴露通道列表更新函数
    window._updateChannelList = function(channels, activeChannelId) {
        const dropdownList = document.getElementById('channelDropdownList');
        const selectorLabel = document.getElementById('channelSelectorLabel');
        if (!dropdownList || !selectorLabel) return;

        // 显示名映射
        const label = activeChannelId ? (channels.find(c => c.id === activeChannelId || c.name === activeChannelId)?.name || activeChannelId) : '本地对话';
        selectorLabel.textContent = label;

        let html = `<div class="channel-dropdown-item ch-all${!activeChannelId ? ' active' : ''}" data-channel="">
            <span class="ch-name">本地对话</span>
        </div>`;
        channels.forEach(ch => {
            const cid = ch.id || ch.name;
            const isActive = cid === activeChannelId;
            const status = ch.running ? ' (运行中)' : '';
            html += `<div class="channel-dropdown-item${isActive ? ' active' : ''}" data-channel="${escapeHtml(cid)}">
                <span class="ch-name">${escapeHtml(ch.name || ch.id)}${status}</span>
            </div>`;
        });
        dropdownList.innerHTML = html;

        dropdownList.querySelectorAll('.channel-dropdown-item').forEach(item => {
            item.addEventListener('click', () => {
                const channelId = item.dataset.channel;
                dropdownList.querySelectorAll('.channel-dropdown-item').forEach(i => i.classList.remove('active'));
                item.classList.add('active');
                selectorLabel.textContent = channelId ? item.querySelector('.ch-name').textContent : '本地对话';
                document.getElementById('channelDropdown').classList.remove('open');
                if (typeof _onChannelChange === 'function') _onChannelChange(channelId);
            });
        });
    };

    // === 左侧图标栏激活状态 ===
    document.querySelectorAll('.rail-icon').forEach(icon => {
        icon.addEventListener('click', function() {
            document.querySelectorAll('.rail-icon').forEach(i => i.classList.remove('active'));
            this.classList.add('active');
            // 点击对话图标时关闭页面视图
            if (this.id === 'railChat') {
                _hidePageView();
            }
        });
    });

    // === 底部数据条时间更新 ===
    const barTime = document.getElementById('barTime');
    if (barTime) {
        const updateTime = () => {
            const now = new Date();
            const dateStr = now.toLocaleDateString('zh-CN', { year:'numeric', month:'long', day:'numeric', weekday:'short' });
            barTime.textContent = dateStr + ' ' + now.toLocaleTimeString('zh-CN', { hour12: false });
        };
        updateTime();
        setInterval(updateTime, 1000);
    }

    // === 底部数据条行情更新 ===
    const INDEX_MAP = { sh: '000001.SH', sz: '399001.SZ', cy: '399006.SZ', kc: '000688.SH' };
    async function updateBarQuotes() {
        try {
            const codes = Object.values(INDEX_MAP).join(',');
            const resp = await window.tdxAPI.getQuote(codes);
            const list = resp.data?.data || resp.data || [];
            const items = Array.isArray(list) ? list : [];
            Object.entries(INDEX_MAP).forEach(([key, code]) => {
                const barItem = document.querySelector(`.bar-item[data-index="${key}"]`);
                if (!barItem) return;
                const q = items.find(d => d.symbol === code);
                if (!q) return;
                const price = q.price ?? '--';
                const lastClose = q.last_close || q.pre_close;
                barItem.querySelector('.bar-value').textContent = typeof price === 'number' ? price.toFixed(2) : price;
                const changeEl = barItem.querySelector('.bar-change');
                if (lastClose && typeof price === 'number') {
                    const pct = ((price - lastClose) / lastClose * 100);
                    const sign = pct >= 0 ? '+' : '';
                    changeEl.textContent = `${sign}${pct.toFixed(2)}%`;
                    changeEl.className = 'bar-change ' + (pct >= 0 ? 'up' : 'down');
                }
            });
        } catch (_) { /* TDX 不可用时静默忽略 */ }
    }
    updateBarQuotes();
    setInterval(updateBarQuotes, 30000);

    // === 自定义确认弹窗 ===
    const confirmOverlay = document.getElementById('confirmOverlay');
    const confirmTitleEl = document.getElementById('confirmTitle');
    const confirmMessageEl = document.getElementById('confirmMessage');
    const confirmOkBtn = document.getElementById('confirmOk');
    const confirmCancelBtn = document.getElementById('confirmCancel');

    let confirmResolve = null;

    function showConfirm(title, message) {
        return new Promise((resolve) => {
            confirmResolve = resolve;
            confirmTitleEl.textContent = title;
            confirmMessageEl.textContent = message;
            confirmOverlay.classList.add('visible');
        });
    }
    window._showConfirm = showConfirm;

    if (confirmCancelBtn) {
        confirmCancelBtn.addEventListener('click', () => {
            confirmOverlay.classList.remove('visible');
            if (confirmResolve) { confirmResolve(false); confirmResolve = null; }
        });
    }
    if (confirmOkBtn) {
        confirmOkBtn.addEventListener('click', () => {
            confirmOverlay.classList.remove('visible');
            if (confirmResolve) { confirmResolve(true); confirmResolve = null; }
        });
    }
    if (confirmOverlay) {
        confirmOverlay.addEventListener('click', (e) => {
            if (e.target === confirmOverlay) {
                confirmOverlay.classList.remove('visible');
                if (confirmResolve) { confirmResolve(false); confirmResolve = null; }
            }
        });
    }

    console.log('[Harness AI] App initialized');
});

async function initializeApp() {
    try {
        console.log('[Harness AI] marked type:', typeof marked, 'has parse:', typeof marked?.parse);
        if (typeof marked !== 'undefined' && typeof marked.parse === 'function') {
            marked.setOptions({ breaks: true, gfm: true });
        } else {
            console.error('[Harness AI] marked library not available or missing parse method');
        }
        console.log('[Harness AI] Initialized');
    } catch (error) {
        console.error('[Harness AI] Initialization error:', error);
    }
}

function _updateStartupStatus(text) {
    const el = document.getElementById('startupStatus');
    if (el) el.textContent = text;
}

async function _waitForBackendThen(callback, maxRetries = 30) {
    for (let i = 0; i < maxRetries; i++) {
        try {
            await window.systemAPI.healthCheck();
            callback();
            return;
        } catch {
            if (i === 0) _updateStartupStatus('正在连接后端…');
            await new Promise(r => setTimeout(r, 2000));
        }
    }
    _updateStartupStatus('后端连接失败，请重启应用');
}

async function checkBackendConnection(retries = 3) {
    for (let i = 0; i < retries; i++) {
        try {
            await window.systemAPI.healthCheck();
            updateStatus(true);
            console.log('[Harness AI] Backend connected');
            updateLlmStatus(true, 'SDK');
            return;
        } catch (error) {
            if (i === 0) console.warn('[Harness AI] Backend not ready, waiting...');
            if (i < retries - 1) {
                await new Promise(r => setTimeout(r, 2000));
            }
        }
    }
    updateStatus(false);
    console.error('[Harness AI] Backend unavailable after retries');
}

function updateLlmStatus(configured, modelName) {
    // SDK 使用 Claude Code 默认配置，始终显示就绪
    const dot = document.getElementById('llmStatusDot');
    const text = document.getElementById('llmStatusText');
    if (!dot || !text) return;
    dot.className = 'status-dot on';
    text.textContent = 'SDK';
}

function updateStatus(connected) {
    document.querySelectorAll('.status-dot').forEach(dot => {
        dot.classList.toggle('connected', connected);
    });
}

function showConnectionError() {
    const chatMessages = document.getElementById('chatMessages');
    chatMessages.innerHTML = `
        <div class="error-message">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <circle cx="12" cy="12" r="10"/>
                <line x1="12" y1="8" x2="12" y2="12"/>
                <line x1="12" y1="16" x2="12.01" y2="16"/>
            </svg>
            <h3>无法连接到后端服务</h3>
            <p>请确保后端服务正在运行 (http://127.0.0.1:9998)</p>
            <button onclick="location.reload()" class="retry-btn">重新连接</button>
        </div>
    `;
}

function setupEventListeners() {
    // New chat
    document.getElementById('newChatBtn').addEventListener('click', startNewChat);

    // Settings
    document.getElementById('settingsBtn').addEventListener('click', openSettings);

    // Backtest
    document.getElementById('backtestPanelBtn').addEventListener('click', openBacktestPanel);

    // Monitor
    document.getElementById('monitorBtn').addEventListener('click', openMonitorPanel);

    // Scheduler
    document.getElementById('schedulerBtn').addEventListener('click', openSchedulerPanel);

    // Watchlist
    document.getElementById('watchlistBtn').addEventListener('click', openWatchlistPanel);

    // Example prompts
    document.querySelectorAll('.example-btn').forEach(btn => {
        btn.addEventListener('click', () => sendMessage(btn.dataset.prompt));
    });

    // Input
    const chatInput = document.getElementById('chatInput');
    const sendBtn = document.getElementById('sendBtn');

    chatInput.addEventListener('input', () => {
        chatInput.style.height = 'auto';
        chatInput.style.height = Math.min(chatInput.scrollHeight, 120) + 'px';
        sendBtn.disabled = !chatInput.value.trim();
    });

    chatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            if (chatInput.value.trim() && !appState.isAnalyzing) {
                sendMessage(chatInput.value.trim());
            }
        }
    });

    sendBtn.addEventListener('click', () => {
        if (chatInput.value.trim() && !appState.isAnalyzing) {
            sendMessage(chatInput.value.trim());
        }
    });

    // File upload
    const attachBtn = document.getElementById('attachBtn');
    const fileInput = document.getElementById('fileInput');
    attachBtn.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', handleFileSelect);

    // Window controls (Electron frameless window)
    // Sidebar
    const sidebarToggle = document.getElementById('railChat');
    if (sidebarToggle) sidebarToggle.addEventListener('click', toggleSidebar);
    const clearAllBtn = document.getElementById('clearAllSessionsBtn');
    if (clearAllBtn) clearAllBtn.addEventListener('click', clearAllConversations);

    const editBtn = document.getElementById('editSessionsBtn');
    if (editBtn) editBtn.addEventListener('click', enterEditMode);
    const cancelEditBtn = document.getElementById('cancelEditBtn');
    if (cancelEditBtn) cancelEditBtn.addEventListener('click', exitEditMode);
    const deleteSelectedBtn = document.getElementById('deleteSelectedBtn');
    if (deleteSelectedBtn) deleteSelectedBtn.addEventListener('click', deleteSelected);
    const selectAllCb = document.getElementById('selectAllSessionsCb');
    if (selectAllCb) selectAllCb.addEventListener('change', () => {
        const checked = selectAllCb.checked;
        document.querySelectorAll('.session-item-checkbox').forEach(cb => { cb.checked = checked; });
        _updateDeleteSelectedBtn();
    });

    setupWindowControls();
}

function startNewChat() {
    if (_activeView !== 'local') {
        _channelMessages[_activeView] = '';
        _renderView(_activeView);
        return;
    }
    appState.messages = [];
    appState.sessionId = null;
    appState.currentSymbol = null;
    appState.uploadedFiles = [];
    appState.activeConversationId = null;

    const headerSessionName = document.getElementById('headerSessionName');
    if (headerSessionName) headerSessionName.textContent = '新对话';

    renderFilePreview();
    _updateSidebarSelection();

    const chatMessages = document.getElementById('chatMessages');
    chatMessages.innerHTML = `
        <div class="welcome-screen">
            <div class="welcome-logo">
                <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                    <path d="M3 3v18h18"/>
                    <path d="M18.7 8l-5.1 5.2-2.8-2.7L7 14.3"/>
                </svg>
            </div>
            <h1 class="welcome-title">灵智投研助手</h1>
            <p class="welcome-subtitle">专业级智能投研平台，让 AI 为你的投资决策赋能。</p>
            <div class="capability-grid">
                <div class="capability-card">
                    <div class="capability-icon">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M3 3v18h18"/><path d="M18.7 8l-5.1 5.2-2.8-2.7L7 14.3"/>
                        </svg>
                    </div>
                    <div>
                        <div class="capability-title">智能分析</div>
                        <div class="capability-desc">自动识别股票，深度研究</div>
                    </div>
                </div>
                <div class="capability-card">
                    <div class="capability-icon">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/>
                        </svg>
                    </div>
                    <div>
                        <div class="capability-title">实时数据</div>
                        <div class="capability-desc">通达信实时行情数据</div>
                    </div>
                </div>
                <div class="capability-card">
                    <div class="capability-icon">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                            <polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
                        </svg>
                    </div>
                    <div>
                        <div class="capability-title">投研报告</div>
                        <div class="capability-desc">生成完整投资分析报告</div>
                    </div>
                </div>
                <div class="capability-card">
                    <div class="capability-icon">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>
                        </svg>
                    </div>
                    <div>
                        <div class="capability-title">风险评估</div>
                        <div class="capability-desc">多维度风险分析</div>
                    </div>
                </div>
            </div>
            <div class="example-prompts">
                <div class="example-title">试试这样问</div>
                <button class="example-btn" data-prompt="分析贵州茅台的投资价值">分析贵州茅台的投资价值</button>
                <button class="example-btn" data-prompt="600519 最近走势怎么样，值得入手吗">600519 最近走势怎么样，值得入手吗</button>
                <button class="example-btn" data-prompt="帮我看下宁德时代的基本面情况">帮我看下宁德时代的基本面情况</button>
                <button class="example-btn" data-prompt="回测600519的MA均线策略">回测600519的MA均线策略</button>
            </div>
        </div>
    `;

    // Rebind example buttons
    document.querySelectorAll('.example-btn').forEach(btn => {
        btn.addEventListener('click', () => sendMessage(btn.dataset.prompt));
    });
}

async function handleFileSelect(e) {
    const files = Array.from(e.target.files);
    e.target.value = '';

    for (const file of files) {
        try {
            const resp = await window.glmAPI.uploadFile(file);
            if (resp.code === 0 && resp.data) {
                appState.uploadedFiles.push(resp.data);
                renderFilePreview();
            } else {
                addAssistantMessage(resp.message || '文件上传失败');
            }
        } catch (err) {
            addAssistantMessage(`文件上传失败：${err.message}`);
        }
    }
}

function renderFilePreview() {
    const container = document.getElementById('filePreview');
    container.innerHTML = appState.uploadedFiles.map((f, i) =>
        `<div class="file-tag">
            <span class="file-tag-name">${f.filename}</span>
            <button class="file-remove" data-index="${i}">&times;</button>
        </div>`
    ).join('');
    container.querySelectorAll('.file-remove').forEach(btn => {
        btn.addEventListener('click', () => {
            appState.uploadedFiles.splice(parseInt(btn.dataset.index), 1);
            renderFilePreview();
        });
    });
}

async function sendMessage(message) {
    if (!message?.trim()) return;

    // 检查 LLM 是否已配置
    try {
        const resp = await window.settingsAPI.getLlmStatus();
        if (resp.data && !resp.data.configured) {
            showToast('请先在设置中配置大模型厂商', 'error');
            openSettings();
            return;
        }
    } catch (e) {
        // 网络异常时允许继续（后端可能正在启动）
        console.warn('LLM status check failed:', e);
    }

    hideWelcomeScreen();

    // Collect file context
    const fileContext = appState.uploadedFiles.length > 0
        ? { filename: appState.uploadedFiles.map(f => f.filename).join(', '), content_text: appState.uploadedFiles.map(f => f.content_text).join('\n\n') }
        : null;

    // Show file info in user message
    const displayMsg = fileContext
        ? `${message}\n\n📎 ${appState.uploadedFiles.map(f => f.filename).join(', ')}`
        : message;
    addUserMessage(displayMsg);

    const chatInput = document.getElementById('chatInput');
    const sendBtn = document.getElementById('sendBtn');
    chatInput.value = '';
    chatInput.style.height = 'auto';
    sendBtn.disabled = true;

    // Clear uploaded files
    appState.uploadedFiles = [];
    renderFilePreview();

    appState.isAnalyzing = true;
    const thinkingId = showThinkingIndicator();

    // 中断控制器
    const abortController = new AbortController();
    appState._abortController = abortController;

    // 显示停止按钮
    if (sendBtn) {
        sendBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>';
        sendBtn.style.background = 'rgba(255,255,255,0.08)';
        sendBtn.style.color = '#e8e4dd';
        sendBtn.style.border = '1px solid rgba(255,255,255,0.12)';
        sendBtn.disabled = false;
        sendBtn.title = '停止生成';
        sendBtn.onclick = () => {
            abortController.abort();
            removeThinkingIndicator(thinkingId);
            appState.isAnalyzing = false;
            appState._abortController = null;
            _resetSendBtn();
            if (!appState.messages.length || appState.messages[appState.messages.length - 1]?.role !== 'assistant') {
                addAssistantMessage('⏹ 已中断');
            }
        };
    }

    window.glmAPI.reactAnalyzeStream(
        message,
        appState.sessionId,
        fileContext,
        // onProgress
        (progress) => {
            if (progress.message) {
                updateThinkingText(thinkingId, progress.message);
            }
        },
        // onResult
        (data) => {
            removeThinkingIndicator(thinkingId);
            _resetSendBtn();

            if (data.session_id) appState.sessionId = data.session_id;
            if (data.stock_symbol) appState.currentSymbol = data.stock_symbol;
            if (!appState.activeConversationId && data.session_id) {
                appState.activeConversationId = data.session_id;
            }

            if (data.status === 'completed') {
                if (data.backtest_result) {
                    window.renderBacktestPanel(data.backtest_result);
                } else if (data.response_type === 'stock_screening') {
                    addAssistantMessage(data.report || '选股完成', true);
                    _renderScreeningTable(data.screening_data);
                } else if (data.report) {
                    // 传递元信息用于 PDF 生成
                    const metadata = {
                        stock_symbol: data.stock_symbol || '',
                        stock_name: data.stock_name || '',
                        session_id: data.session_id || '',
                        generated_at: new Date().toISOString(),
                    };
                    addAssistantMessage(data.report, true, metadata);
                } else if (data.message) {
                    addAssistantMessage(data.message);
                } else {
                    addAssistantMessage(JSON.stringify(data, null, 2));
                }

                if (_activeView !== 'local') {
                    const reportText = data.report || data.message || '';
                    console.log('[Feishu Push] activeView=', _activeView, 'reportLen=', reportText.length);
                    if (reportText) {
                        window.channelAPI.sendMessageToFeishu({
                            channel_id: _activeView,
                            text: `📌 ${message}\n\n${reportText}`,
                        }).then(resp => {
                            console.log('[Feishu Push] response:', resp);
                            if (resp.code !== 0) {
                                showToast('飞书推送失败: ' + (resp.message || ''), 'error');
                            }
                        }).catch(e => {
                            console.warn('[Feishu] Push failed:', e.message);
                            showToast('飞书推送异常: ' + e.message, 'error');
                        });
                    } else {
                        console.warn('[Feishu Push] No report/message to push, data=', Object.keys(data));
                    }
                }
            } else {
                addAssistantMessage('分析状态未知，请重试');
            }
            appState.isAnalyzing = false;
            appState._abortController = null;
            loadSessionList();
        },
        // onError
        (errorData) => {
            removeThinkingIndicator(thinkingId);
            _resetSendBtn();
            if (errorData.message !== 'AbortError' && !String(errorData.message).includes('abort')) {
                addAssistantMessage(`分析失败：${errorData.message || '未知错误'}`);
            }
            appState.isAnalyzing = false;
            appState._abortController = null;
            loadSessionList();
        },
        abortController.signal
    );
}

function hideWelcomeScreen() {
    const el = document.querySelector('.welcome-screen');
    if (el) el.remove();
}

// ==================== Page View ====================
// 将弹窗内容嵌入 main-content 区域

function _showPageView(title, bodyHtml) {
    const chatContainer = document.getElementById('chatContainer');
    const inputContainer = document.getElementById('inputContainer');
    const pageView = document.getElementById('pageView');

    chatContainer.style.display = 'none';
    if (inputContainer) inputContainer.style.display = 'none';
    pageView.style.display = 'flex';
    pageView.innerHTML = `
        <div class="page-header">
            <div class="page-title">${title}</div>
            <button class="page-close" id="pageCloseBtn">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                    <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                </svg>
            </button>
        </div>
        <div class="page-body">${bodyHtml}</div>
    `;
    document.getElementById('pageCloseBtn').addEventListener('click', _hidePageView);
}

function _hidePageView() {
    const chatContainer = document.getElementById('chatContainer');
    const inputContainer = document.getElementById('inputContainer');
    const pageView = document.getElementById('pageView');

    pageView.style.display = 'none';
    pageView.innerHTML = '';
    chatContainer.style.display = '';
    if (inputContainer) inputContainer.style.display = '';

    // 切回对话 rail 图标
    document.querySelectorAll('.rail-icon').forEach(i => i.classList.remove('active'));
    document.getElementById('railChat').classList.add('active');

    // 清理定时器等
    if (_schedulerPollTimer) { clearInterval(_schedulerPollTimer); _schedulerPollTimer = null; }
}

// ==================== 自选股 ====================

async function openWatchlistPanel() {
    _showPageView('自选股', `
        <div style="display:flex;gap:8px;align-items:center;margin-bottom:16px">
            <div style="position:relative;flex:1">
                <input type="text" class="settings-input" id="wlAddInput" placeholder="输入股票名称或代码，如 贵州茅台 或 600519" autocomplete="off">
                <div class="symbol-suggest" id="wlAddSuggest"></div>
            </div>
            <button class="settings-btn primary" id="wlAddBtn" style="white-space:nowrap">添加</button>
            <button class="settings-btn secondary" id="wlSyncBtn" title="更新股票池" style="white-space:nowrap">更新股票池</button>
        </div>
        <div id="wlStockStatus" style="font-size:10px;color:var(--text-tertiary);margin-bottom:8px"></div>
        <div id="wlList"><div style="text-align:center;padding:40px;color:var(--text-tertiary)">加载中...</div></div>
    `);

    document.getElementById('wlAddBtn').addEventListener('click', _wlAddItem);
    document.getElementById('wlAddInput').addEventListener('keydown', e => { if (e.key === 'Enter') _wlAddItem(); });
    document.getElementById('wlSyncBtn').addEventListener('click', _wlSyncStockPool);
    attachSymbolSuggest('wlAddInput', 'wlAddSuggest');

    await _wlLoadList();
    _wlRefreshStockStatus();
}

function _analyzeStockFromWatchlist(symbol, name) {
    // 1. 关闭 page-view，回到对话页面
    _hidePageView();
    // 2. 切回对话 rail 图标
    document.querySelectorAll('.rail-icon').forEach(i => i.classList.remove('active'));
    document.getElementById('railChat').classList.add('active');
    // 3. 发送分析消息
    const prompt = `分析${name || symbol}`;
    sendMessage(prompt);
}

async function _wlRefreshStockStatus() {
    const el = document.getElementById('wlStockStatus');
    if (!el) return;
    try {
        const resp = await window.stocksAPI.getStatus();
        const d = resp.data;
        if (d.syncing) {
            el.textContent = `股票池同步中... 当前 ${d.count} 条`;
        } else if (d.last_sync_at) {
            const dt = new Date(d.last_sync_at);
            el.textContent = `股票池 ${d.count} 条 · 上次同步 ${dt.toLocaleString('zh-CN')}`;
        } else {
            el.textContent = `股票池 ${d.count} 条 · 未同步`;
        }
    } catch (_) {}
}

async function _wlSyncStockPool() {
    const btn = document.getElementById('wlSyncBtn');
    if (btn) { btn.disabled = true; btn.textContent = '同步中...'; }
    try {
        const resp = await window.stocksAPI.sync();
        if (resp.data?.ok === false) {
            showToast('同步失败: ' + resp.data.message, 'error');
        } else {
            showToast(`已同步 ${resp.data?.count || 0} 只股票`, 'success');
            await _wlRefreshStockStatus();
        }
    } catch (e) {
        showToast('同步失败: ' + e.message, 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '更新股票池'; }
    }
}

async function _wlLoadList() {
    const container = document.getElementById('wlList');
    if (!container) return;
    try {
        const resp = await window.watchlistAPI.listItems();
        const items = resp.data?.items || [];
        if (!items.length) {
            container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-tertiary)">暂无自选股，添加一只吧</div>';
            return;
        }

        // 立即渲染列表（行情区显示占位符）
        container.innerHTML = items.map(item => `
            <div class="watchlist-row" data-id="${item.id}" data-symbol="${escapeHtml(item.symbol)}">
                <div class="watchlist-row-info">
                    <span class="watchlist-row-name">${escapeHtml(item.name || item.symbol)}</span>
                    <span class="watchlist-row-code">${escapeHtml(item.symbol)}</span>
                </div>
                <div class="watchlist-row-quote" data-symbol="${escapeHtml(item.symbol)}">
                    <span class="wl-quote-loading" style="color:var(--text-tertiary);font-size:11px">加载中</span>
                </div>
                <button class="watchlist-del-btn" data-id="${item.id}" title="删除">×</button>
            </div>
        `).join('');

        // 事件绑定
        container.querySelectorAll('.watchlist-row').forEach(row => {
            row.addEventListener('click', e => {
                if (e.target.closest('.watchlist-del-btn')) return;
                _showWatchlistDetail(row.dataset.symbol);
            });
        });
        container.querySelectorAll('.watchlist-del-btn').forEach(btn => {
            btn.addEventListener('click', async e => {
                e.stopPropagation();
                await window.watchlistAPI.removeItem(parseInt(btn.dataset.id));
                await _wlLoadList();
            });
        });

        // 异步加载行情（批量）
        const symbols = items.map(i => i.symbol);
        window.tdxAPI.getQuote(symbols.join(',')).then(quoteResp => {
            const qlist = quoteResp?.data;
            if (!Array.isArray(qlist)) return;
            qlist.forEach(q => {
                const sym = q.symbol || q.code;
                const slot = container.querySelector(`.watchlist-row-quote[data-symbol="${sym}"]`);
                if (!slot) return;
                if (!q.price) {
                    slot.innerHTML = '<span style="color:var(--text-tertiary);font-size:11px">-</span>';
                    return;
                }
                const lastClose = q.last_close || 0;
                const chg = lastClose > 0 ? q.price - lastClose : 0;
                const pct = lastClose > 0 ? (chg / lastClose * 100) : 0;
                const sign = chg > 0 ? '+' : '';
                const color = chg > 0 ? 'var(--red)' : chg < 0 ? 'var(--green)' : 'var(--text-secondary)';
                slot.innerHTML = `<span style="font-family:var(--font-mono);font-size:13px">${q.price.toFixed(2)}</span>
                    <span style="color:${color};font-family:var(--font-mono);font-size:11px">${sign}${pct.toFixed(2)}%</span>`;
            });
        }).catch(() => {
            container.querySelectorAll('.wl-quote-loading').forEach(el => {
                el.textContent = '-';
                el.style.color = 'var(--text-tertiary)';
            });
        });
    } catch (e) {
        container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--red)">加载失败</div>';
    }
}

async function _wlAddItem() {
    const input = document.getElementById('wlAddInput');
    const val = input.value.trim();
    if (!val) return;
    try {
        await window.watchlistAPI.addItem({ symbol: val });
        input.value = '';
        await _wlLoadList();
    } catch (e) {
        showToast('添加失败: ' + e.message, 'error');
    }
}

async function _showWatchlistDetail(symbol) {
    // 先用 listItems 已有数据立即渲染首屏骨架（不卡在“正在获取数据”）
    let initialName = symbol;
    try {
        const listResp = await window.watchlistAPI.listItems();
        const item = (listResp.data?.items || []).find(i => i.symbol === symbol);
        if (item) initialName = item.name || symbol;
    } catch (_) { /* ignore */ }

    _showPageView(`${initialName} ${symbol}`, `
        <div id="wlDetailContent">
            <div class="wl-detail-section">
                <div class="wl-detail-title">
                    <span>基本信息</span>
                    <button class="settings-btn primary" id="wlAnalyzeBtn" style="padding:3px 10px;font-size:11px">分析</button>
                </div>
                <div id="wlDetailInfo" class="wl-detail-grid"><span style="color:var(--text-tertiary);font-size:11px">加载中...</span></div>
            </div>
            <div class="wl-detail-section">
                <div class="wl-detail-title">
                    K线图
                    <div class="wl-kline-tabs">
                        <button class="wl-kline-tab active" data-period="daily">日K</button>
                        <button class="wl-kline-tab" data-period="weekly">周K</button>
                        <button class="wl-kline-tab" data-period="monthly">月K</button>
                    </div>
                </div>
                <div id="wlKlineChart" style="height:400px;position:relative;display:flex;align-items:center;justify-content:center;color:var(--text-tertiary);font-size:11px">加载中...</div>
            </div>
            <div class="wl-detail-section">
                <div class="wl-detail-title">实时行情</div>
                <div id="wlDetailQuote" class="wl-detail-grid"><span style="color:var(--text-tertiary);font-size:11px">加载中...</span></div>
            </div>
        </div>
    `);

    // 分析按钮在详情数据加载后绑定（此时 name 更准）

    // 异步加载完整详情
    try {
        const listResp = await window.watchlistAPI.listItems();
        const item = (listResp.data?.items || []).find(i => i.symbol === symbol);
        if (!item) { showToast('未找到该股票', 'error'); return; }

        const resp = await window.watchlistAPI.getItemDetail(item.id);
        const d = resp.data;
        const name = d.name || initialName;

        // 基本信息卡片
        const infoEl = document.getElementById('wlDetailInfo');
        if (d.info) {
            const i = d.info;
            const price = d.quote?.price || 0;
            const fmtMoney = (v) => v ? (v / 1e8).toFixed(2) + '亿' : '-';
            const fmtShare = (v) => v ? (v / 1e8).toFixed(2) + '亿股' : '-';
            const fmtDate = (v) => v ? `${v.slice(0,4)}-${v.slice(4,6)}-${v.slice(6,8)}` : '-';
            infoEl.innerHTML = [
                ['名称', i.name || name],
                ['行业', i.industry || '-'],
                ['地区', i.province || i.area || '-'],
                ['总市值', price && i.total_shares ? fmtMoney(price * i.total_shares) : '-'],
                ['流通市值', price && i.float_shares ? fmtMoney(price * i.float_shares) : '-'],
                ['总股本', fmtShare(i.total_shares)],
                ['流通股本', fmtShare(i.float_shares)],
                ['净资产', i.net_assets ? fmtMoney(i.net_assets) : '-'],
                ['营业收入', i.revenue ? fmtMoney(i.revenue) : '-'],
                ['净利润', i.net_profit ? fmtMoney(i.net_profit) : '-'],
                ['每股净资产', i.eps ? i.eps.toFixed(2) : '-'],
                ['股东人数', i.shareholders ? (i.shareholders / 10000).toFixed(2) + '万' : '-'],
                ['上市日期', fmtDate(i.ipo_date || i.list_date)],
            ].filter(r => r[1] && r[1] !== '-').map(([k, v]) =>
                `<div class="wl-detail-cell"><span class="wl-detail-label">${k}</span><span class="wl-detail-value">${escapeHtml(String(v))}</span></div>`
            ).join('');
        } else {
            infoEl.innerHTML = '<div style="color:var(--text-tertiary)">暂无数据</div>';
        }

        // 实时行情卡片
        const quoteEl = document.getElementById('wlDetailQuote');
        if (d.quote && d.quote.price > 0) {
            const q = d.quote;
            const lastClose = q.last_close || 0;
            const chg = lastClose > 0 ? q.price - lastClose : 0;
            const pct = lastClose > 0 ? (chg / lastClose * 100) : 0;
            const sign = chg > 0 ? '+' : '';
            const color = chg > 0 ? 'var(--red)' : chg < 0 ? 'var(--green)' : 'var(--text-secondary)';
            quoteEl.innerHTML = [
                ['最新价', `<span style="color:${color}">${q.price.toFixed(2)}</span>`],
                ['涨跌幅', `<span style="color:${color}">${sign}${pct.toFixed(2)}%</span>`],
                ['今开', q.open?.toFixed(2) || '-'],
                ['最高', q.high?.toFixed(2) || '-'],
                ['最低', q.low?.toFixed(2) || '-'],
                ['成交量', q.volume ? (q.volume / 10000).toFixed(0) + '万手' : '-'],
                ['成交额', q.amount ? (q.amount / 1e8).toFixed(2) + '亿' : '-'],
            ].map(([k, v]) =>
                `<div class="wl-detail-cell"><span class="wl-detail-label">${k}</span><span class="wl-detail-value">${v}</span></div>`
            ).join('');
        } else {
            quoteEl.innerHTML = '<div style="color:var(--text-tertiary)">非交易时段，暂无行情</div>';
        }

        // K线图 — 用 canvas 绘制简单K线
        const klines = d.klines || {};
        _renderWlKline(klines.daily || []);

        // K线周期切换
        document.querySelectorAll('.wl-kline-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.wl-kline-tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                _renderWlKline(klines[tab.dataset.period] || []);
            });
        });

        // 分析按钮：跳转到对话页面触发分析
        const analyzeBtn = document.getElementById('wlAnalyzeBtn');
        if (analyzeBtn) {
            analyzeBtn.addEventListener('click', () => {
                _analyzeStockFromWatchlist(symbol, name);
            });
        }

    } catch (e) {
        _showPageView('加载失败', `<div style="text-align:center;padding:60px;color:var(--red)">${escapeHtml(e.message)}</div>`);
    }
}

// K线图实例（用于切换周期时 dispose 重建）
let _wlKlineChart = null;

function _renderWlKline(bars) {
    const container = document.getElementById('wlKlineChart');
    if (!container) return;
    if (!bars.length) {
        container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-tertiary)">暂无K线数据</div>';
        return;
    }

    // 销毁旧实例
    if (_wlKlineChart) { _wlKlineChart.dispose(); _wlKlineChart = null; }
    container.innerHTML = '';

    const chart = echarts.init(container, null, { renderer: 'canvas' });
    _wlKlineChart = chart;

    // 数据预处理：拆分 OHLC + 计算简单 MA
    const dates = bars.map(b => b.date);
    const ohlc = bars.map(b => [b.open, b.close, b.low, b.high]);
    const closes = bars.map(b => b.close);
    const vols = bars.map(b => b.volume);

    function ma(arr, n) {
        const out = [];
        for (let i = 0; i < arr.length; i++) {
            if (i < n - 1) { out.push(null); continue; }
            let s = 0;
            for (let j = i - n + 1; j <= i; j++) s += arr[j];
            out.push(+(s / n).toFixed(2));
        }
        return out;
    }
    const ma5 = ma(closes, 5);
    const ma20 = ma(closes, 20);

    const option = {
        animation: false,
        backgroundColor: 'transparent',
        tooltip: {
            trigger: 'axis',
            axisPointer: { type: 'cross' },
            backgroundColor: 'rgba(13,13,18,0.95)',
            borderColor: '#c89640',
            borderWidth: 1,
            textStyle: { color: '#e8e4dd', fontSize: 11 },
            formatter: p => {
                if (!p || !p.length) return '';
                const date = p[0].axisValue;
                const idx = dates.indexOf(date);
                const o = ohlc[idx];
                if (!o) return '';
                const [op, cl, lo, hi] = o;
                const v = vols[idx];
                const chg = cl - op;
                const pct = op > 0 ? (chg / op * 100).toFixed(2) : '0.00';
                const sign = chg > 0 ? '+' : '';
                const color = chg > 0 ? '#ef4444' : chg < 0 ? '#22c55e' : '#e8e4dd';
                return `<div style="font-size:10px;color:#8a8780">${date}</div>
                    <table style="font-size:11px;color:${color};font-family:var(--font-mono)">
                    <tr><td style="color:#8a8780">开</td><td>${op.toFixed(2)}</td></tr>
                    <tr><td style="color:#8a8780">高</td><td style="color:#ef4444">${hi.toFixed(2)}</td></tr>
                    <tr><td style="color:#8a8780">低</td><td style="color:#22c55e">${lo.toFixed(2)}</td></tr>
                    <tr><td style="color:#8a8780">收</td><td>${cl.toFixed(2)}</td></tr>
                    <tr><td style="color:#8a8780">涨跌</td><td>${sign}${chg.toFixed(2)} (${sign}${pct}%)</td></tr>
                    <tr><td style="color:#8a8780">量</td><td>${(v / 10000).toFixed(0)}万手</td></tr>
                    </table>`;
            },
        },
        axisPointer: { link: [{ xAxisIndex: 'all' }] },
        grid: [
            { left: 50, right: 30, top: 20, height: '60%' },   // K线区
            { left: 50, right: 30, top: '76%', height: '16%' }, // 成交量区
        ],
        xAxis: [
            { type: 'category', data: dates, scale: true, boundaryGap: false, splitLine: { show: false }, axisLine: { lineStyle: { color: '#2e2e36' } }, axisLabel: { color: '#8a8780', fontSize: 10 } },
            { type: 'category', gridIndex: 1, data: dates, scale: true, boundaryGap: false, splitLine: { show: false }, axisLine: { lineStyle: { color: '#2e2e36' } }, axisLabel: { show: false } },
        ],
        yAxis: [
            { scale: true, splitLine: { lineStyle: { color: '#1e1e26' } }, axisLabel: { color: '#8a8780', fontSize: 10, formatter: v => v.toFixed(2) } },
            { gridIndex: 1, splitNumber: 2, splitLine: { show: false }, axisLabel: { show: false } },
        ],
        dataZoom: [
            { type: 'inside', xAxisIndex: [0, 1], start: 60, end: 100 },   // 内置（鼠标拖动/滚轮/触摸）
            { type: 'slider', xAxisIndex: [0, 1], bottom: 10, height: 18, start: 60, end: 100, borderColor: '#2e2e36', fillerColor: 'rgba(200,150,64,0.12)', handleStyle: { color: '#c89640' }, textStyle: { color: '#8a8780', fontSize: 9 }, dataBackground: { lineStyle: { color: '#3a3a44' }, areaStyle: { color: '#1a1a20' } } },
        ],
        brush: {
            xAxisIndex: 0,
            brushStyle: { color: 'rgba(200,150,64,0.15)', borderColor: '#c89640' },
            throttleType: 'debounce',
            throttleDelay: 300,
            toolbox: ['rect', 'clear'],
        },
        toolbox: {
            right: 12, top: 0, iconStyle: { borderColor: '#8a8780' }, emphasis: { iconStyle: { borderColor: '#c89640' } },
            feature: {
                brush: { type: ['rect', 'clear'], title: { rect: '框选区间', clear: '清除' } },
                dataZoom: { yAxisIndex: 'none', title: { zoom: '区域缩放', back: '还原' } },
            },
        },
        series: [
            {
                name: 'K线',
                type: 'candlestick',
                data: ohlc,
                itemStyle: {
                    color: '#ef4444',        // 阳线实体填充
                    color0: '#22c55e',       // 阴线实体填充
                    borderColor: '#ef4444',
                    borderColor0: '#22c55e',
                },
            },
            { name: 'MA5', type: 'line', data: ma5, smooth: true, symbol: 'none', lineStyle: { color: '#c89640', width: 1 } },
            { name: 'MA20', type: 'line', data: ma20, smooth: true, symbol: 'none', lineStyle: { color: '#7c93c4', width: 1 } },
            {
                name: '成交量',
                type: 'bar',
                xAxisIndex: 1,
                yAxisIndex: 1,
                data: bars.map((b, i) => ({
                    value: b.volume,
                    itemStyle: { color: b.close >= b.open ? 'rgba(239,68,68,0.6)' : 'rgba(34,197,94,0.6)' },
                })),
            },
        ],
    };

    chart.setOption(option);

    // 区间选择回调：显示区间涨跌幅
    chart.on('brushSelected', params => {
        const detail = params.batch?.[0];
        if (!detail || !detail.selected?.length) return;
        const sel = detail.selected[0];
        const idxs = sel.dataIndex || [];
        if (idxs.length < 2) return;
        const first = ohlc[idxs[0]];
        const last = ohlc[idxs[idxs.length - 1]];
        const startPrice = first[0]; // 区间起点用开盘价
        const endPrice = last[1];    // 区间终点用收盘价
        const chg = endPrice - startPrice;
        const pct = startPrice > 0 ? (chg / startPrice * 100) : 0;
        const color = chg > 0 ? '#ef4444' : chg < 0 ? '#22c55e' : '#e8e4dd';
        const sign = chg > 0 ? '+' : '';
        console.log(`[K线区间] ${dates[idxs[0]]} → ${dates[idxs[idxs.length-1]]}: ${sign}${chg.toFixed(2)} (${sign}${pct.toFixed(2)}%) color=${color}`);
        // 在图上显示提示
        _showKlineRangeTip(dates[idxs[0]], dates[idxs[idxs.length-1]], endPrice, chg, pct, color);
    });

    // 响应窗口大小变化
    if (!chart.__resize_bound) {
        chart.__resize_bound = true;
        window.addEventListener('resize', () => chart.resize());
    }
}

function _showKlineRangeTip(startDate, endDate, price, chg, pct, color) {
    let tip = document.getElementById('wlKlineRangeTip');
    if (!tip) {
        const c = document.getElementById('wlKlineChart');
        if (!c) return;
        tip = document.createElement('div');
        tip.id = 'wlKlineRangeTip';
        tip.style.cssText = 'position:absolute;right:32px;top:30px;background:rgba(13,13,18,0.9);border:1px solid #2e2e36;border-radius:4px;padding:6px 10px;font-size:10px;font-family:var(--font-mono);z-index:5;pointer-events:none';
        c.style.position = 'relative';
        c.appendChild(tip);
    }
    const sign = chg > 0 ? '+' : '';
    tip.innerHTML = `<div style="color:#8a8780;font-size:9px">${startDate} ~ ${endDate}</div>
        <div style="color:${color}">${price.toFixed(2)} <span>${sign}${chg.toFixed(2)}</span> <span>(${sign}${pct.toFixed(2)}%)</span></div>`;
}


function _resetSendBtn() {
    const sendBtn = document.getElementById('sendBtn');
    if (!sendBtn) return;
    sendBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>';
    sendBtn.style.background = '';
    sendBtn.style.color = '';
    sendBtn.style.border = '';
    sendBtn.disabled = true;
    sendBtn.title = '发送';
    sendBtn.onclick = null;
}

function addUserMessage(content) {
    const chatMessages = document.getElementById('chatMessages');
    const div = document.createElement('div');
    div.className = 'chat-message user';
    div.innerHTML = `
        <div class="message-content">
            <div class="message-bubble">${escapeHtml(content)}</div>
        </div>
        <div class="message-avatar">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
                <circle cx="12" cy="7" r="4"/>
            </svg>
        </div>
    `;
    chatMessages.appendChild(div);
    scrollToBottom();
    appState.messages.push({ role: 'user', content });
}

function _fixMarkdownTables(text) {
    // Normalize table blocks: detect runs of pipe-delimited lines and
    // collapse any blank lines that appear within them so marked's GFM
    // parser can recognise them as tables.
    return text.replace(/((?:^\|.+\|?$\n?)+)/gm, (block) => {
        return block.replace(/\n{2,}/g, '\n');
    });
}

function addAssistantMessage(content, isMarkdown = false, metadata = null) {
    const chatMessages = document.getElementById('chatMessages');
    // 历史会话加载时不传 metadata，兜底为空对象，避免下方读 metadata.stock_symbol 崩溃
    if (!metadata) metadata = {};
    const div = document.createElement('div');
    div.className = 'chat-message assistant';

    const avatarSvg = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M12 2L2 7l10 5 10-5-10-5z"/>
        <path d="M2 17l10 5 10-5"/>
        <path d="M2 12l10 5 10-5"/>
    </svg>`;

    const canParse = typeof marked !== 'undefined' && typeof marked.parse === 'function';
    let contentHtml;
    if (isMarkdown && canParse) {
        contentHtml = marked.parse(_fixMarkdownTables(content));
    } else {
        contentHtml = escapeHtml(content);
    }

    // Find the last user message for re-execute
    const lastUserMsg = appState.messages.filter(m => m.role === 'user').pop();
    const lastUserContent = lastUserMsg ? escapeHtml(lastUserMsg.content) : '';

    // 判断是否显示下载按钮（有 markdown 报告内容即显示，不依赖预设股票）
    const showDownloadBtn = isMarkdown && content && content.length > 50;

    div.innerHTML = `
        <div class="message-avatar">${avatarSvg}</div>
        <div class="message-content">
            <div class="message-name">灵智投研助手</div>
            <div class="result-card">
                <div class="message-bubble ${isMarkdown ? 'markdown-content' : ''}">${contentHtml}</div>
                <div class="message-actions">
                    ${showDownloadBtn ? `<button class="msg-action-btn" data-action="download" data-content="${encodeURIComponent(content)}" data-symbol="${escapeHtml(metadata.stock_symbol || '')}" data-name="${escapeHtml(metadata.stock_name || metadata.stock_symbol || '')}" title="下载 PDF 报告">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
                            <polyline points="7 10 12 15 17 10"/>
                            <line x1="12" y1="15" x2="12" y2="3"/>
                        </svg>
                        <span>下载报告</span>
                    </button>` : ''}
                    <button class="msg-action-btn" data-action="copy" title="复制结果">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <rect x="9" y="9" width="13" height="13" rx="2"/>
                            <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>
                        </svg>
                        <span>复制</span>
                    </button>
                    ${lastUserContent ? `<button class="msg-action-btn" data-action="retry" data-prompt="${lastUserContent}" title="重新执行">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 102.13-9.36L1 10"/>
                        </svg>
                        <span>重新执行</span>
                    </button>` : ''}
                </div>
            </div>
        </div>
    `;

    // Download button
    const downloadBtn = div.querySelector('[data-action="download"]');
    if (downloadBtn) {
        downloadBtn.addEventListener('click', async () => {
            const markdown = decodeURIComponent(downloadBtn.dataset.content);
            const stockSymbol = decodeURIComponent(downloadBtn.dataset.symbol);
            const stockName = decodeURIComponent(downloadBtn.dataset.name);
            const span = downloadBtn.querySelector('span');
            const originalText = span.textContent;

            try {
                span.textContent = '生成中...';
                downloadBtn.disabled = true;

                // 用 marked.js 渲染 markdown → HTML
                let bodyHtml;
                if (typeof marked !== 'undefined' && typeof marked.parse === 'function') {
                    bodyHtml = marked.parse(_fixMarkdownTables(markdown));
                } else {
                    bodyHtml = escapeHtml(markdown).replace(/\n/g, '<br>');
                }

                const now = new Date().toLocaleString('zh-CN', { hour12: false });
                const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<style>
  @page { size: A4; margin: 2cm; }
  body { font-family: "Microsoft YaHei", "PingFang SC", sans-serif; font-size: 12pt; line-height: 1.8; color: #222; }
  h1 { font-size: 18pt; border-bottom: 2px solid #c89640; padding-bottom: 6px; }
  h2 { font-size: 15pt; margin-top: 20px; border-bottom: 1px solid #ddd; padding-bottom: 4px; }
  h3 { font-size: 13pt; margin-top: 16px; }
  table { border-collapse: collapse; width: 100%; margin: 10px 0; }
  th, td { border: 1px solid #ccc; padding: 6px 10px; text-align: left; }
  th { background: #f5f5f5; font-weight: bold; }
  code { background: #f5f5f5; padding: 1px 4px; font-family: Consolas, monospace; }
  pre { background: #f5f5f5; padding: 10px; overflow-x: auto; font-size: 10pt; }
  blockquote { border-left: 3px solid #c89640; padding-left: 12px; color: #555; margin: 8px 0; }
  .report-header { text-align: center; margin-bottom: 20px; }
  .report-header h1 { font-size: 20pt; border: none; margin-bottom: 4px; }
  .report-meta { font-size: 9pt; color: #666; text-align: right; margin-bottom: 24px; border-bottom: 1px solid #ddd; padding-bottom: 8px; }
</style>
</head>
<body>
<div class="report-header"><h1>灵智投研助手 - 深度分析报告</h1></div>
<div class="report-meta">股票: ${escapeHtml(stockName || stockSymbol)} (${escapeHtml(stockSymbol)}) | 生成: ${now}</div>
${bodyHtml}
</body>
</html>`;

                // 通过 Electron IPC 生成 PDF
                let pdfBase64;
                if (window.electronAPI && window.electronAPI.generatePDF) {
                    pdfBase64 = await window.electronAPI.generatePDF(html);
                } else {
                    // 回退：调用后端 API（需要 weasyprint）
                    const response = await window.glmAPI.generatePDF({
                        markdown,
                        stock_symbol: stockSymbol,
                        stock_name: stockName,
                    });
                    if (response.code === 0 && response.data && response.data.pdf_base64) {
                        pdfBase64 = response.data.pdf_base64;
                    } else {
                        throw new Error(response.message || '后端 PDF 生成失败');
                    }
                }

                // 下载 PDF
                const filename = stockSymbol
                    ? `${stockSymbol}_${stockName || '分析'}_报告.pdf`
                    : '分析报告.pdf';
                const link = document.createElement('a');
                link.href = 'data:application/pdf;base64,' + pdfBase64;
                link.download = filename;
                link.click();

                span.textContent = '已下载';
                setTimeout(() => { span.textContent = originalText; }, 1500);

            } catch (err) {
                console.error('PDF download error:', err);
                showToast('PDF 下载失败: ' + err.message, 'error');
                span.textContent = originalText;
            } finally {
                downloadBtn.disabled = false;
            }
        });
    }

    // Copy button
    const copyBtn = div.querySelector('[data-action="copy"]');
    if (copyBtn) {
        copyBtn.addEventListener('click', () => {
            navigator.clipboard.writeText(content).then(() => {
                const span = copyBtn.querySelector('span');
                span.textContent = '已复制';
                setTimeout(() => { span.textContent = '复制'; }, 1500);
            }).catch(() => {
                showToast('复制失败', 'error');
            });
        });
    }

    // Retry button
    const retryBtn = div.querySelector('[data-action="retry"]');
    if (retryBtn) {
        retryBtn.addEventListener('click', () => {
            const prompt = retryBtn.dataset.prompt;
            if (prompt) sendMessage(prompt);
        });
    }

    chatMessages.appendChild(div);
    scrollToBottom();
    appState.messages.push({ role: 'assistant', content });
}

function showThinkingIndicator() {
    const chatMessages = document.getElementById('chatMessages');
    const div = document.createElement('div');
    div.className = 'chat-message assistant';
    div.id = 'thinking-' + Date.now();

    div.innerHTML = `
        <div class="message-avatar">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M12 2L2 7l10 5 10-5-10-5z"/>
                <path d="M2 17l10 5 10-5"/>
                <path d="M2 12l10 5 10-5"/>
            </svg>
        </div>
        <div class="message-content">
            <div class="thinking-indicator">
                <span>正在分析</span>
                <div class="thinking-dots">
                    <div class="thinking-dot"></div>
                    <div class="thinking-dot"></div>
                    <div class="thinking-dot"></div>
                </div>
                <div class="thinking-phase">准备开始...</div>
            </div>
        </div>
    `;

    chatMessages.appendChild(div);
    scrollToBottom();
    return div.id;
}

function removeThinkingIndicator(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

function updateThinkingText(id, text) {
    const el = document.getElementById(id);
    if (!el) return;
    const phaseEl = el.querySelector('.thinking-phase');
    if (phaseEl) {
        phaseEl.textContent = text;
    }
    scrollToBottom();
}

function scrollToBottom() {
    const container = document.getElementById('chatContainer');
    if (container) container.scrollTop = container.scrollHeight;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ========== Settings Panel ==========

async function openSettings() {
    let settings = {};
    let feishuConfig = {};
    let providersData = { providers: [], active: '' };
    let envVars = {};
    try {
        const resp = await window.settingsAPI.getSettings();
        settings = resp.data || {};
    } catch (e) {
        console.warn('Failed to load settings:', e);
    }
    try {
        const resp = await window.channelAPI.getFeishuConfig();
        feishuConfig = resp.data || {};
    } catch (e) {
        console.warn('Failed to load feishu config:', e);
    }
    try {
        const resp = await window.settingsAPI.getLlmProviders();
        providersData = resp.data || { providers: [], active: '' };
    } catch (e) {
        console.warn('Failed to load LLM providers:', e);
    }
    try {
        const resp = await window.settingsAPI.getEnvVars();
        envVars = resp.data || {};
    } catch (e) {
        console.warn('Failed to load env vars:', e);
    }

    const fsEnabled = feishuConfig.enabled === 'true' ? 'checked' : '';
    const fsAppId = feishuConfig.app_id || '';
    const fsAppSecret = feishuConfig.app_secret || '';
    const fsToken = feishuConfig.verification_token || '';
    const fsEncryptKey = feishuConfig.encrypt_key || '';

    _showPageView('设置', `
            <div class="settings-group">
                <div class="settings-group-title" style="display:flex;align-items:center;justify-content:space-between">
                    <span>大模型</span>
                    <button class="settings-btn primary" style="padding:4px 12px;font-size:12px" id="llmAddProviderBtn">添加厂商</button>
                </div>
                <div style="margin-bottom:10px;display:flex;align-items:center;gap:8px">
                    <span style="font-size:11px;color:var(--text-tertiary);white-space:nowrap">当前使用</span>
                    <select id="llmActiveSelect" class="settings-select" style="flex:1">
                        ${providersData.providers.length
                            ? providersData.providers.map(p => `<option value="${p.id}" ${p.id === providersData.active ? 'selected' : ''}>${p.name} — ${p.model || '未设置模型'}</option>`).join('')
                            : '<option value="">未配置</option>'}
                    </select>
                </div>
                <div id="llmProvidersList"></div>
            </div>
            <div class="settings-group">
                <div class="settings-group-title" style="display:flex;align-items:center;justify-content:space-between">
                    <span>SDK 技能管理</span>
                    <button class="settings-btn primary" style="padding:4px 12px;font-size:12px" id="settingsAddSkillBtn">添加技能</button>
                </div>
                <div id="settingsBuiltinSkills" style="margin-bottom:8px"></div>
                <div id="settingsCustomSkills"></div>
            </div>
            <div class="settings-group">
                <div class="settings-group-title" style="display:flex;align-items:center;justify-content:space-between">
                    <span>MCP 服务</span>
                    <button class="settings-btn primary" style="padding:4px 12px;font-size:12px" id="settingsAddMcpBtn">添加服务</button>
                </div>
                <div id="settingsMcpList"></div>
            </div>
            <div class="settings-group">
                <div class="settings-group-title" style="display:flex;align-items:center;justify-content:space-between">
                    <span>飞书通道</span>
                    <button class="settings-btn primary" style="padding:4px 12px;font-size:12px" id="settingsAddChannelBtn">添加通道</button>
                </div>
                <div id="settingsChannelList">
                    <div style="text-align:center;padding:20px;color:var(--text-tertiary)">加载中...</div>
                </div>
            </div>
            <div class="settings-group">
                <div class="settings-group-title" style="display:flex;align-items:center;justify-content:space-between">
                    <span>环境变量</span>
                    <button class="settings-btn primary" style="padding:4px 12px;font-size:12px" id="envAddVarBtn">添加变量</button>
                </div>
                <div style="font-size:11px;color:var(--text-tertiary);margin-bottom:8px">注入到后端子进程的环境变量，修改后需重启 Worker 生效</div>
                <div id="envVarsList"></div>
            </div>
            <div class="settings-group">
                <div class="settings-group-title">关闭按钮行为</div>
                <div style="font-size:11px;color:var(--text-tertiary);margin-bottom:8px">点窗口关闭按钮（×）时的行为。选"每次询问"会重新弹出选择对话框。</div>
                <select id="closeBehaviorSelect" class="settings-input" style="width:100%;padding:6px;background:var(--bg-secondary);color:var(--text-primary);border:1px solid var(--border);border-radius:4px">
                    <option value="ask">每次询问</option>
                    <option value="minimize">最小化到托盘</option>
                    <option value="quit">退出程序</option>
                </select>
            </div>
            <div class="settings-group">
                <div class="settings-group-title">后端服务</div>
                <div style="font-size:11px;color:var(--text-tertiary);margin-bottom:8px">Worker 进程负责行情、K线、AI 分析等。修改环境变量后需重启生效。</div>
                <button class="settings-btn secondary" id="restartWorkerBtn" style="width:100%">重启 Worker 进程</button>
            </div>
        <div class="settings-footer" style="padding:10px 0;border-top:1px solid var(--border);margin-top:12px;display:flex;gap:6px;justify-content:flex-end">
            <button class="settings-btn secondary" id="openLogViewerBtn">查看日志</button>
            <button class="settings-btn secondary" id="settingsCancelBtn">取消</button>
            <button class="settings-btn primary" id="settingsSaveBtn">保存</button>
        </div>
    `);

    var _lvBtn = document.getElementById('openLogViewerBtn');
    console.log('[openSettings] _lvBtn:', _lvBtn, 'window._openLogViewer:', typeof window._openLogViewer);
    if (_lvBtn) {
      _lvBtn.addEventListener('click', function() {
        console.log('[openSettings] log viewer button clicked');
        if (window._openLogViewer) {
          window._openLogViewer();
        } else {
          alert('日志查看器未加载');
        }
      });
    }
    const _rwBtn = document.getElementById('restartWorkerBtn');
    if (_rwBtn) {
      _rwBtn.addEventListener('click', async function() {
        const original = _rwBtn.textContent;
        _rwBtn.disabled = true;
        _rwBtn.textContent = '重启中...';
        try {
          const resp = await window.systemAPI.restartWorker();
          if (resp.code === 0) {
            showToast('Worker 重启成功', 'success');
          } else {
            showToast('重启失败: ' + (resp.message || ''), 'error');
          }
        } catch (e) {
          showToast('重启失败: ' + e.message, 'error');
        } finally {
          _rwBtn.disabled = false;
          _rwBtn.textContent = original;
        }
      });
    }
    document.getElementById('settingsCancelBtn').addEventListener('click', _hidePageView);

    // === LLM Provider 管理 ===
    let _providers = providersData.providers || [];
    let _activeId = providersData.active || '';

    function _renderProviders() {
        const container = document.getElementById('llmProvidersList');
        if (!_providers.length) {
            container.innerHTML = '<div style="text-align:center;padding:16px;color:var(--text-tertiary);font-size:12px">暂无厂商配置，点击"添加厂商"开始</div>';
            return;
        }
        container.innerHTML = _providers.map((p, i) => {
            const isActive = p.id === _activeId;
            const borderColor = isActive ? 'var(--accent)' : 'var(--border)';
            const badge = isActive ? '<span style="font-size:10px;color:var(--accent);background:rgba(200,150,64,0.12);padding:1px 6px;border-radius:2px;margin-left:6px">当前使用</span>' : '';
            return `
            <div style="border:1px solid ${borderColor};border-radius:5px;padding:10px;margin-bottom:8px;position:relative" data-provider-idx="${i}">
                <div style="display:flex;align-items:center;margin-bottom:6px">
                    <span style="font-size:12px;font-weight:500;color:var(--text-primary)">${p.name || '未命名'}${badge}</span>
                    <div style="margin-left:auto;display:flex;gap:4px">
                        ${!isActive ? `<button class="llm-activate-btn" data-id="${p.id}" style="font-size:11px;background:none;border:1px solid var(--border);color:var(--text-secondary);border-radius:3px;padding:2px 8px;cursor:pointer">设为当前</button>` : ''}
                        <button class="llm-delete-btn" data-idx="${i}" style="background:none;border:none;color:var(--text-tertiary);cursor:pointer;font-size:14px;padding:2px 4px" title="删除">×</button>
                    </div>
                </div>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">
                    <div>
                        <label style="font-size:11px;color:var(--text-tertiary)">名称</label>
                        <input class="llm-field" data-field="name" value="${p.name || ''}" style="width:100%;background:var(--bg-primary);color:var(--text-primary);border:1px solid var(--border);border-radius:3px;padding:4px 6px;font-size:12px;margin-top:2px">
                    </div>
                    <div>
                        <label style="font-size:11px;color:var(--text-tertiary)">模型</label>
                        <input class="llm-field" data-field="model" value="${p.model || ''}" style="width:100%;background:var(--bg-primary);color:var(--text-primary);border:1px solid var(--border);border-radius:3px;padding:4px 6px;font-size:12px;margin-top:2px">
                    </div>
                    <div>
                        <label style="font-size:11px;color:var(--text-tertiary)">API Key</label>
                        <input class="llm-field" data-field="api_key" type="password" value="${p.api_key || ''}" placeholder="${p.api_key_masked || ''}" style="width:100%;background:var(--bg-primary);color:var(--text-primary);border:1px solid var(--border);border-radius:3px;padding:4px 6px;font-size:12px;margin-top:2px">
                    </div>
                    <div>
                        <label style="font-size:11px;color:var(--text-tertiary)">Base URL</label>
                        <input class="llm-field" data-field="base_url" value="${p.base_url || ''}" style="width:100%;background:var(--bg-primary);color:var(--text-primary);border:1px solid var(--border);border-radius:3px;padding:4px 6px;font-size:12px;margin-top:2px">
                    </div>
                </div>
            </div>
        `}).join('');

        // 字段变更 → 同步到 _providers
        container.querySelectorAll('.llm-field').forEach(input => {
            input.addEventListener('change', () => {
                const idx = parseInt(input.closest('[data-provider-idx]').dataset.providerIdx);
                const field = input.dataset.field;
                if (_providers[idx]) _providers[idx][field] = input.value;
            });
        });

        // 删除
        container.querySelectorAll('.llm-delete-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const idx = parseInt(btn.dataset.idx);
                _providers.splice(idx, 1);
                _renderProviders();
                _updateActiveSelect();
            });
        });

        // 设为当前
        container.querySelectorAll('.llm-activate-btn').forEach(btn => {
            btn.addEventListener('click', async () => {
                const id = btn.dataset.id;
                _activeId = id;
                try {
                    await window.settingsAPI.setActiveLlmProvider(id);
                    _updateActiveSelect();
                    _renderProviders();
                    showToast('已切换到 ' + (_providers.find(p => p.id === id)?.name || id), 'success');
                } catch (err) {
                    showToast('切换失败：' + err.message, 'error');
                }
            });
        });
    }

    function _updateActiveSelect() {
        const sel = document.getElementById('llmActiveSelect');
        sel.innerHTML = _providers.map(p => `<option value="${p.id}" ${p.id === _activeId ? 'selected' : ''}>${p.name || p.id}</option>`).join('');
    }

    _renderProviders();

    // 激活切换
    document.getElementById('llmActiveSelect').addEventListener('change', async (e) => {
        _activeId = e.target.value;
        try {
            await window.settingsAPI.setActiveLlmProvider(_activeId);
            showToast('已切换到 ' + (_providers.find(p => p.id === _activeId)?.name || _activeId), 'success');
        } catch (err) {
            showToast('切换失败：' + err.message, 'error');
        }
    });

    // 添加厂商
    document.getElementById('llmAddProviderBtn').addEventListener('click', () => {
        const id = 'provider_' + Date.now();
        _providers.push({ id, name: '', api_key: '', base_url: '', model: '', timeout: 600 });
        _activeId = _activeId || id;
        _renderProviders();
        _updateActiveSelect();
    });

    // === 环境变量管理 ===
    let _envVars = { ...envVars };

    function _renderEnvVars() {
        const container = document.getElementById('envVarsList');
        const entries = Object.entries(_envVars);
        if (!entries.length) {
            container.innerHTML = '<div style="text-align:center;padding:16px;color:var(--text-tertiary);font-size:12px">暂无环境变量</div>';
            return;
        }
        container.innerHTML = entries.map(([key, value], i) => `
            <div class="env-var-row" style="display:flex;align-items:center;gap:8px;margin-bottom:6px" data-idx="${i}">
                <input class="env-var-key" value="${escapeHtml(key)}" placeholder="变量名" style="flex:1;background:var(--bg-primary);color:var(--text-primary);border:1px solid var(--border);border-radius:3px;padding:4px 6px;font-size:12px;font-family:var(--font-mono)">
                <input class="env-var-value" value="${escapeHtml(value)}" placeholder="值" style="flex:2;background:var(--bg-primary);color:var(--text-primary);border:1px solid var(--border);border-radius:3px;padding:4px 6px;font-size:12px;font-family:var(--font-mono)">
                <button class="env-var-delete-btn" style="background:none;border:none;color:var(--text-tertiary);cursor:pointer;font-size:14px;padding:2px 4px;flex-shrink:0" title="删除">×</button>
            </div>
        `).join('');

        // 字段变更 → 同步到 _envVars
        container.querySelectorAll('.env-var-row').forEach(row => {
            const idx = parseInt(row.dataset.idx);
            const keyInput = row.querySelector('.env-var-key');
            const valInput = row.querySelector('.env-var-value');
            const sync = () => {
                const newKey = keyInput.value.trim();
                const newVal = valInput.value;
                // 重新构建 entries，删除旧 key 再设新 key
                const entries = Object.entries(_envVars);
                entries.splice(idx, 1);
                if (newKey) entries.splice(idx, 0, [newKey, newVal]);
                _envVars = Object.fromEntries(entries);
            };
            keyInput.addEventListener('change', () => { sync(); _renderEnvVars(); });
            valInput.addEventListener('change', sync);
            row.querySelector('.env-var-delete-btn').addEventListener('click', () => {
                const e = Object.entries(_envVars);
                e.splice(idx, 1);
                _envVars = Object.fromEntries(e);
                _renderEnvVars();
            });
        });
    }

    _renderEnvVars();

    document.getElementById('envAddVarBtn').addEventListener('click', () => {
        _envVars[''] = '';
        _renderEnvVars();
    });

    // 保存按钮：保存 providers + env vars
    document.getElementById('settingsSaveBtn').addEventListener('click', async () => {
        try {
            // 清除空 key
            if (_envVars[''] !== undefined) {
                delete _envVars[''];
                _renderEnvVars();
            }
            await window.settingsAPI.updateLlmProviders(_providers);
            await window.settingsAPI.updateEnvVars(_envVars);
            showToast('配置已保存', 'success');
            setTimeout(_hidePageView, 800);
        } catch (e) {
            showToast('保存失败：' + e.message, 'error');
        }
    });

    // 加载 SDK 技能列表
    _renderSkillSettings();

    // 加载 MCP 服务列表
    _renderMcpSettings();

    // 添加技能按钮
    document.getElementById('settingsAddSkillBtn').addEventListener('click', async () => {
        try {
            const filePath = await window.electronAPI.selectFile({
                filters: [{ name: 'ZIP', extensions: ['zip'] }],
                title: '选择技能 ZIP 包',
            });
            if (!filePath) return;
            showToast('正在上传...', 'info');
            const result = await window.settingsAPI.uploadSkill(filePath);
            if (result.data.code === 0) {
                showToast(result.data.message || '上传成功', 'success');
                _renderSkillSettings();
            } else {
                showToast(result.data.message || '上传失败', 'error');
            }
        } catch (e) {
            const msg = e.response?.data?.detail || e.message || '上传失败';
            showToast(msg, 'error');
        }
    });

    // 添加 MCP 服务按钮
    document.getElementById('settingsAddMcpBtn').addEventListener('click', () => {
        _showMcpEditForm(null);
    });

    // 加载飞书通道列表
    _renderChannelSettings();

    document.getElementById('settingsAddChannelBtn').addEventListener('click', () => {
        _showChannelEditForm(null);
    });

    async function _initCloseBehavior() {
        const select = document.getElementById('closeBehaviorSelect');
        if (!select) return;
        try {
            const behavior = await window.electronAPI.getCloseBehavior();
            select.value = behavior || 'ask';
        } catch (e) {
            console.warn('getCloseBehavior failed', e);
        }
        if (!select.dataset.bound) {
            select.addEventListener('change', async () => {
                try {
                    await window.electronAPI.setCloseBehavior(select.value);
                    showToast('关闭行为已更新', 'success');
                } catch (e) {
                    showToast('保存失败: ' + e.message, 'error');
                }
            });
            select.dataset.bound = '1';
        }
    }
    _initCloseBehavior();

}

function showToast(message, type = 'success') {
    const existing = document.querySelector('.settings-toast');
    if (existing) existing.remove();

    const toast = document.createElement('div');
    toast.className = `settings-toast ${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);

    setTimeout(() => toast.remove(), 2500);
}

// ========== SDK 技能管理 UI ==========

async function _renderSkillSettings() {
    const builtinEl = document.getElementById('settingsBuiltinSkills');
    const customEl = document.getElementById('settingsCustomSkills');
    if (!builtinEl || !customEl) return;

    try {
        const resp = await window.settingsAPI.getSkills();
        const data = resp.data?.data || resp.data || {};
        const builtin = data.builtin || [];
        const custom = data.custom || [];

        // 内置技能（只读）
        if (builtin.length === 0) {
            builtinEl.innerHTML = '<div style="text-align:center;padding:8px;color:var(--text-tertiary);font-size:12px">暂无内置技能</div>';
        } else {
            builtinEl.innerHTML = '<div style="font-size:11px;color:var(--text-tertiary);margin-bottom:6px;padding-left:4px">内置技能</div>' +
                builtin.map(s => `
                    <div style="display:flex;align-items:center;padding:8px 10px;border-radius:6px;margin-bottom:4px;background:var(--bg-secondary)">
                        <span style="width:6px;height:6px;border-radius:50%;background:var(--accent);margin-right:10px;flex-shrink:0"></span>
                        <span style="flex:1;font-size:13px">${escapeHtml(s.display_name || s.name)}</span>
                        <span style="font-size:11px;color:var(--text-tertiary);margin-left:8px">v${escapeHtml(s.version)}</span>
                    </div>
                `).join('');
        }

        // 自定义技能（可操作）
        if (custom.length === 0) {
            customEl.innerHTML = '<div style="text-align:center;padding:8px;color:var(--text-tertiary);font-size:12px">暂无自定义技能</div>';
        } else {
            customEl.innerHTML = '<div style="font-size:11px;color:var(--text-tertiary);margin-bottom:6px;padding-left:4px">自定义技能</div>' +
                custom.map(s => {
                    const enabled = s.enabled !== false;  // 默认开启
                    return `
                    <div style="display:flex;align-items:center;padding:8px 10px;border-radius:6px;margin-bottom:4px;background:rgba(255,255,255,0.02);gap:8px" data-skill="${escapeHtml(s.name)}">
                        <label class="settings-toggle" style="flex-shrink:0">
                            <input type="checkbox" class="skill-toggle-cb" data-skill="${escapeHtml(s.name)}" ${enabled ? 'checked' : ''}>
                            <span class="settings-toggle-slider"></span>
                        </label>
                        <span style="flex:1;font-size:13px">${escapeHtml(s.display_name || s.name)}</span>
                        <span style="font-size:11px;color:var(--text-tertiary)">v${escapeHtml(s.version)}</span>
                        <button class="skill-delete-btn" data-skill="${escapeHtml(s.name)}"
                                style="background:none;border:none;cursor:pointer;color:var(--text-tertiary);padding:2px 6px;font-size:14px"
                                title="删除">×</button>
                    </div>
                `}).join('');

            // 绑定 toggle 事件
            customEl.querySelectorAll('.skill-toggle-cb').forEach(cb => {
                cb.addEventListener('change', async () => {
                    const name = cb.dataset.skill;
                    const newEnabled = cb.checked;
                    try {
                        await window.settingsAPI.updateSkillStatus({ [name]: newEnabled });
                        _renderSkillSettings();
                    } catch (e) { showToast('操作失败', 'error'); }
                });
            });

            // 绑定删除事件
            customEl.querySelectorAll('.skill-delete-btn').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const name = btn.dataset.skill;
                    const confirmed = await window._showConfirm('删除技能', `确定要删除技能「${name}」吗？`);
                    if (!confirmed) return;
                    try {
                        await window.settingsAPI.deleteSkill(name);
                        showToast('已删除', 'success');
                        _renderSkillSettings();
                    } catch (e) {
                        showToast(e.response?.data?.detail || '删除失败', 'error');
                    }
                });
            });
        }
    } catch (e) {
        console.error('Failed to load skills:', e);
        builtinEl.innerHTML = '<div style="text-align:center;padding:8px;color:var(--text-tertiary);font-size:12px">加载失败</div>';
    }
}

// ========== MCP 服务配置 UI ==========

let _mcpConfigs = [];

async function _renderMcpSettings() {
    const container = document.getElementById('settingsMcpList');
    if (!container) return;

    try {
        const resp = await window.settingsAPI.getMcpConfigs();
        _mcpConfigs = resp.data?.data?.configs || resp.data?.configs || [];

        if (_mcpConfigs.length === 0) {
            container.innerHTML = '<div style="text-align:center;padding:8px;color:var(--text-tertiary);font-size:12px">暂无 MCP 服务配置</div>';
            return;
        }

        container.innerHTML = _mcpConfigs.map((c, i) => `
            <div style="display:flex;align-items:center;padding:8px 10px;border-radius:6px;margin-bottom:4px;background:var(--bg-secondary);gap:8px" data-mcp-idx="${i}">
                <button class="mcp-toggle-btn" data-mcp-idx="${i}" data-enabled="${c.enabled}"
                        style="width:36px;height:20px;border-radius:10px;border:none;cursor:pointer;flex-shrink:0;
                               background:${c.enabled ? 'var(--accent)' : '#555'};position:relative;transition:background 0.2s">
                    <span style="position:absolute;top:2px;left:${c.enabled ? '18px' : '2px'};width:16px;height:16px;border-radius:50%;background:#fff;transition:left 0.2s"></span>
                </button>
                <div style="flex:1;min-width:0">
                    <div style="font-size:13px">${escapeHtml(c.name)}</div>
                    <div style="font-size:11px;color:var(--text-tertiary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(c.type || 'stdio')} · ${escapeHtml(c.command || c.url || '')}</div>
                </div>
                <button class="mcp-edit-btn" data-mcp-idx="${i}" style="background:none;border:none;cursor:pointer;color:var(--accent);font-size:12px;padding:2px 6px">编辑</button>
                <button class="mcp-delete-btn" data-mcp-idx="${i}" style="background:none;border:none;cursor:pointer;color:var(--text-tertiary);font-size:14px;padding:2px 6px">×</button>
            </div>
        `).join('');

        container.querySelectorAll('.mcp-toggle-btn').forEach(btn => {
            btn.addEventListener('click', async () => {
                const idx = parseInt(btn.dataset.mcpIdx);
                _mcpConfigs[idx].enabled = !_mcpConfigs[idx].enabled;
                try {
                    await window.settingsAPI.updateMcpConfigs(_mcpConfigs);
                    _renderMcpSettings();
                } catch (e) { showToast('操作失败', 'error'); }
            });
        });

        container.querySelectorAll('.mcp-edit-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const idx = parseInt(btn.dataset.mcpIdx);
                _showMcpEditForm(idx);
            });
        });

        container.querySelectorAll('.mcp-delete-btn').forEach(btn => {
            btn.addEventListener('click', async () => {
                const idx = parseInt(btn.dataset.mcpIdx);
                const name = _mcpConfigs[idx]?.name || '';
                const confirmed = await window._showConfirm('删除 MCP 服务', `确定要删除 MCP 服务「${name}」吗？`);
                if (!confirmed) return;
                _mcpConfigs.splice(idx, 1);
                try {
                    await window.settingsAPI.updateMcpConfigs(_mcpConfigs);
                    _renderMcpSettings();
                } catch (e) { showToast('删除失败', 'error'); }
            });
        });
    } catch (e) {
        console.error('Failed to load MCP configs:', e);
        container.innerHTML = '<div style="text-align:center;padding:8px;color:var(--text-tertiary);font-size:12px">加载失败</div>';
    }
}

function _showMcpEditForm(editIdx) {
    const isEdit = editIdx !== null && editIdx !== undefined;
    const config = isEdit ? { ..._mcpConfigs[editIdx] } : { id: '', name: '', type: 'stdio', command: '', args: '', enabled: true, description: '' };

    const overlay = document.createElement('div');
    overlay.className = 'settings-overlay';
    overlay.innerHTML = `
        <div class="settings-panel" style="width:460px">
            <div class="settings-header">
                <div class="settings-title">${isEdit ? '编辑 MCP 服务' : '添加 MCP 服务'}</div>
                <button class="settings-close" id="mcpFormClose">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                        <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                    </svg>
                </button>
            </div>
            <div class="settings-body">
                <div class="settings-field">
                    <label class="settings-label">服务 ID</label>
                    <input type="text" class="settings-input" id="mcpFormId" value="${escapeHtml(config.id)}" placeholder="唯一标识，如 my-mcp-server" ${isEdit ? 'readonly style="opacity:0.6"' : ''}>
                </div>
                <div class="settings-field">
                    <label class="settings-label">服务名称</label>
                    <input type="text" class="settings-input" id="mcpFormName" value="${escapeHtml(config.name)}" placeholder="显示名称">
                </div>
                <div class="settings-field">
                    <label class="settings-label">类型</label>
                    <select class="settings-input" id="mcpFormType">
                        <option value="stdio" ${config.type === 'stdio' ? 'selected' : ''}>stdio</option>
                        <option value="sse" ${config.type === 'sse' ? 'selected' : ''}>sse</option>
                    </select>
                </div>
                <div class="settings-field">
                    <label class="settings-label">命令 / URL</label>
                    <input type="text" class="settings-input" id="mcpFormCommand" value="${escapeHtml(config.command || config.url || '')}" placeholder="stdio: 命令路径; sse: URL">
                </div>
                <div class="settings-field">
                    <label class="settings-label">参数（空格分隔）</label>
                    <input type="text" class="settings-input" id="mcpFormArgs" value="${escapeHtml(Array.isArray(config.args) ? config.args.join(' ') : (config.args || ''))}" placeholder="例如 -y @anthropic/some-mcp">
                </div>
                <div class="settings-field">
                    <label class="settings-label">描述</label>
                    <input type="text" class="settings-input" id="mcpFormDesc" value="${escapeHtml(config.description || '')}" placeholder="服务用途说明">
                </div>
            </div>
            <div class="settings-footer">
                <button class="settings-btn secondary" id="mcpFormCancel">取消</button>
                <button class="settings-btn primary" id="mcpFormSave">保存</button>
            </div>
        </div>
    `;

    document.body.appendChild(overlay);
    const close = () => overlay.remove();
    document.getElementById('mcpFormClose').addEventListener('click', close);
    document.getElementById('mcpFormCancel').addEventListener('click', close);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

    document.getElementById('mcpFormSave').addEventListener('click', async () => {
        const id = document.getElementById('mcpFormId').value.trim();
        const name = document.getElementById('mcpFormName').value.trim();
        const type = document.getElementById('mcpFormType').value;
        const command = document.getElementById('mcpFormCommand').value.trim();
        const argsStr = document.getElementById('mcpFormArgs').value.trim();
        const desc = document.getElementById('mcpFormDesc').value.trim();

        if (!id || !name) { showToast('ID 和名称不能为空', 'error'); return; }

        const args = argsStr ? argsStr.split(/\s+/) : [];
        const newConfig = { id, name, type, enabled: config.enabled, description: desc };
        if (type === 'stdio') {
            newConfig.command = command;
            newConfig.args = args;
        } else {
            newConfig.url = command;
        }

        if (isEdit) {
            _mcpConfigs[editIdx] = newConfig;
        } else {
            if (_mcpConfigs.some(c => c.id === id)) { showToast('ID 已存在', 'error'); return; }
            _mcpConfigs.push(newConfig);
        }

        try {
            await window.settingsAPI.updateMcpConfigs(_mcpConfigs);
            showToast('保存成功', 'success');
            close();
            _renderMcpSettings();
        } catch (e) { showToast('保存失败', 'error'); }
    });
}

// ========== 飞书多通道设置 UI ==========

async function _renderChannelSettings() {
    const container = document.getElementById('settingsChannelList');
    if (!container) return;

    try {
        const resp = await window.channelAPI.listChannels();
        const channels = resp.data?.channels || [];
        if (!channels.length) {
            container.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-tertiary)">暂无通道，点击上方"添加通道"新建</div>';
            return;
        }

        container.innerHTML = channels.map(ch => `
            <div class="channel-card" data-chid="${escapeHtml(ch.id)}" style="display:flex;align-items:center;justify-content:space-between;padding:10px 12px;margin-bottom:8px;background:rgba(255,255,255,0.04);border-radius:8px;border:1px solid rgba(255,255,255,0.08)">
                <div>
                    <div style="font-weight:600;font-size:13px">${escapeHtml(ch.name || ch.id)}</div>
                    <div style="font-size:11px;color:var(--text-tertiary)">${escapeHtml(ch.app_id || '')}</div>
                </div>
                <div style="display:flex;gap:6px;align-items:center">
                    <span class="monitor-status-badge ${ch.running ? 'completed' : ''}" style="font-size:10px">${ch.running ? '运行中' : '已停止'}</span>
                    <button class="channel-action-btn" data-action="toggle" data-chid="${escapeHtml(ch.id)}" data-running="${ch.running}" title="${ch.running ? '停止' : '启动'}" style="background:none;border:1px solid rgba(255,255,255,0.15);border-radius:4px;padding:4px 8px;color:var(--text-secondary);cursor:pointer;font-size:11px">${ch.running ? '停止' : '启动'}</button>
                    <button class="channel-action-btn" data-action="edit" data-chid="${escapeHtml(ch.id)}" style="background:none;border:1px solid rgba(255,255,255,0.15);border-radius:4px;padding:4px 8px;color:var(--text-secondary);cursor:pointer;font-size:11px">编辑</button>
                    <button class="channel-action-btn" data-action="delete" data-chid="${escapeHtml(ch.id)}" style="background:none;border:1px solid rgba(255,100,100,0.3);border-radius:4px;padding:4px 8px;color:#ef4444;cursor:pointer;font-size:11px">删除</button>
                </div>
            </div>
        `).join('');

        container.querySelectorAll('.channel-action-btn').forEach(btn => {
            btn.addEventListener('click', async () => {
                const action = btn.dataset.action;
                const chId = btn.dataset.chid;
                if (action === 'toggle') {
                    const running = btn.dataset.running === 'true';
                    try {
                        if (running) {
                            await window.channelAPI.stopChannel(chId);
                        } else {
                            await window.channelAPI.startChannel(chId);
                        }
                        await _renderChannelSettings();
                        await _loadChannelSelector();
                    } catch (e) { showToast('操作失败: ' + e.message, 'error'); }
                } else if (action === 'edit') {
                    const ch = channels.find(c => c.id === chId);
                    if (ch) _showChannelEditForm(ch);
                } else if (action === 'delete') {
                    if (!await window._showConfirm('删除通道', '确定删除此通道？')) return;
                    try {
                        await window.channelAPI.deleteChannel(chId);
                        await _renderChannelSettings();
                        await _loadChannelSelector();
                    } catch (e) { showToast('删除失败: ' + e.message, 'error'); }
                }
            });
        });
    } catch (e) {
        container.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-tertiary)">加载失败</div>';
    }
}

function _showChannelEditForm(channel) {
    const isNew = !channel;
    const name = channel?.name || '';
    const appId = channel?.app_id || '';
    const appSecret = channel?.app_secret || '';
    const token = channel?.verification_token || '';
    const encryptKey = channel?.encrypt_key || '';

    const dialog = document.createElement('div');
    dialog.className = 'settings-overlay';
    dialog.innerHTML = `
        <div class="settings-panel" style="width:460px">
            <div class="settings-header">
                <div class="settings-title">${isNew ? '添加飞书通道' : '编辑飞书通道'}</div>
                <button class="settings-close" id="chEditClose">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                        <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                    </svg>
                </button>
            </div>
            <div class="settings-body">
                <div class="settings-field">
                    <label class="settings-label">通道名称</label>
                    <input type="text" class="settings-input" id="chEditName" value="${escapeHtml(name)}" placeholder="例如：投研群、客服群">
                </div>
                <div class="settings-field">
                    <label class="settings-label">App ID</label>
                    <input type="text" class="settings-input" id="chEditAppId" value="${escapeHtml(appId)}" placeholder="飞书应用 App ID">
                </div>
                <div class="settings-field">
                    <label class="settings-label">App Secret</label>
                    <input type="password" class="settings-input" id="chEditAppSecret" value="${escapeHtml(appSecret)}" placeholder="${isNew ? '飞书应用 App Secret' : '****（留空保持不变）'}">
                </div>
                <div class="settings-field">
                    <label class="settings-label">Verification Token（可选）</label>
                    <input type="text" class="settings-input" id="chEditToken" value="${escapeHtml(token)}" placeholder="可选">
                </div>
                <div class="settings-field">
                    <label class="settings-label">Encrypt Key（可选）</label>
                    <input type="text" class="settings-input" id="chEditEncryptKey" value="${escapeHtml(encryptKey)}" placeholder="可选">
                </div>
                <div class="settings-field">
                    <label class="settings-label">推送目标（定时任务等场景的默认推送地址）</label>
                    <div id="chEditPushTargets" style="margin-top:4px"></div>
                    <div style="display:flex;gap:4px;margin-top:6px;align-items:center">
                        <select class="settings-input" id="chNewTargetType" style="width:80px;flex-shrink:0">
                            <option value="chat_id">群聊</option>
                            <option value="open_id">私聊</option>
                        </select>
                        <input type="text" class="settings-input" id="chNewTargetId" placeholder="chat_id 或 open_id" style="flex:1">
                        <input type="text" class="settings-input" id="chNewTargetLabel" placeholder="标签" style="width:80px;flex-shrink:0">
                        <button class="settings-btn primary" id="chAddTargetBtn" style="padding:4px 8px;font-size:11px;white-space:nowrap">添加</button>
                    </div>
                </div>
            </div>
            <div class="settings-footer">
                <button class="settings-btn secondary" id="chEditCancelBtn">取消</button>
                <button class="settings-btn primary" id="chEditSaveBtn">保存</button>
            </div>
        </div>
    `;
    document.body.appendChild(dialog);

    const closeDialog = () => dialog.remove();
    document.getElementById('chEditClose').addEventListener('click', closeDialog);
    document.getElementById('chEditCancelBtn').addEventListener('click', closeDialog);
    dialog.addEventListener('click', (e) => { if (e.target === dialog) closeDialog(); });

    // 推送目标管理
    let pushTargets = [...(channel?.push_targets || [])];
    const renderPushTargets = () => {
        const container = document.getElementById('chEditPushTargets');
        if (!container) return;
        if (!pushTargets.length) {
            container.innerHTML = '<div style="font-size:10px;color:var(--text-tertiary)">暂无推送目标</div>';
            return;
        }
        container.innerHTML = pushTargets.map((t, i) => {
            const typeLabel = t.receive_id_type === 'open_id' ? '私聊' : '群聊';
            return `<div class="settings-card" style="margin-bottom:4px">
                <div class="settings-card-info">
                    <span class="settings-card-name">${escapeHtml(t.label || typeLabel)}</span>
                    <span class="settings-card-detail">${typeLabel}: ${escapeHtml(t.receive_id)}</span>
                </div>
                <button class="channel-action-btn delete" data-idx="${i}" style="font-size:11px;color:#ef4444;border-color:rgba(217,74,74,0.3)">删除</button>
            </div>`;
        }).join('');
        container.querySelectorAll('[data-idx]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                pushTargets.splice(parseInt(btn.dataset.idx), 1);
                renderPushTargets();
            });
        });
    };
    renderPushTargets();

    document.getElementById('chAddTargetBtn').addEventListener('click', () => {
        const type = document.getElementById('chNewTargetType').value;
        const id = document.getElementById('chNewTargetId').value.trim();
        const label = document.getElementById('chNewTargetLabel').value.trim();
        if (!id) { showToast('请输入推送目标 ID', 'error'); return; }
        pushTargets.push({ receive_id: id, receive_id_type: type, label: label || (type === 'open_id' ? '私聊' : '群聊') });
        document.getElementById('chNewTargetId').value = '';
        document.getElementById('chNewTargetLabel').value = '';
        renderPushTargets();
    });

    document.getElementById('chEditSaveBtn').addEventListener('click', async () => {
        const data = {
            name: document.getElementById('chEditName').value.trim(),
            app_id: document.getElementById('chEditAppId').value.trim(),
            app_secret: document.getElementById('chEditAppSecret').value.trim(),
            verification_token: document.getElementById('chEditToken').value.trim(),
            encrypt_key: document.getElementById('chEditEncryptKey').value.trim(),
            push_targets: pushTargets,
        };
        try {
            if (isNew) {
                await window.channelAPI.addChannel(data);
            } else {
                await window.channelAPI.updateChannel(channel.id, data);
            }
            closeDialog();
            await _renderChannelSettings();
            await _loadChannelSelector();
            showToast(isNew ? '通道已添加' : '通道已更新', 'success');
        } catch (e) {
            showToast('保存失败: ' + e.message, 'error');
        }
    });
}

// ========== Backtest Panel ==========

const PARAM_LABELS = {
    short_window: '短期均线周期', long_window: '长期均线周期',
    fast_period: '快线周期', slow_period: '慢线周期', signal_period: '信号线周期',
    period: '计算周期', overbought: '超买阈值', oversold: '超卖阈值',
    num_std: '标准差倍数'
};

const STRATEGY_LABELS = {
    ma_crossover: '均线交叉', macd: 'MACD', rsi: 'RSI', bollinger_band: '布林带'
};

async function openBacktestPanel() {
    // Default dates: 1 year ago → today
    const today = new Date();
    const oneYearAgo = new Date(today);
    oneYearAgo.setFullYear(oneYearAgo.getFullYear() - 1);
    const fmt = d => d.toISOString().slice(0, 10);

    const currentSymbol = appState.currentSymbol || '';

    _showPageView('策略回测', `
            <div class="settings-group">
                <div class="settings-group-title">股票选择</div>
                <div class="settings-field">
                    <label class="settings-label">股票代码或名称</label>
                    <div style="position:relative">
                        <input type="text" class="settings-input" id="backtestSymbol"
                               value="${escapeHtml(currentSymbol)}" placeholder="例如 600519、600519.SH 或 贵州茅台" autocomplete="off">
                        <div class="symbol-suggest" id="backtestSymbolSuggest"></div>
                    </div>
                    <div class="backtest-hint">支持股票名称自动联想，输入纯数字自动补全市场后缀</div>
                </div>
            </div>

            <div class="settings-group">
                <div class="settings-group-title">策略配置</div>
                <div class="settings-field">
                    <label class="settings-label">选择策略</label>
                    <select class="settings-select" id="backtestStrategy">
                        <option value="">加载策略中...</option>
                    </select>
                </div>
                <div class="settings-field">
                    <label class="settings-label">策略参数</label>
                    <div id="backtestParamsContainer">
                        <div class="backtest-loading">选择策略后显示参数</div>
                    </div>
                </div>
            </div>

            <div class="settings-group">
                <div class="settings-group-title">回测设置</div>
                <div class="settings-field">
                    <label class="settings-label">日期范围</label>
                    <div class="backtest-date-row">
                        <input type="date" class="settings-input" id="backtestStartDate" value="${fmt(oneYearAgo)}">
                        <input type="date" class="settings-input" id="backtestEndDate" value="${fmt(today)}">
                    </div>
                </div>
                <div class="settings-field">
                    <label class="settings-label">初始资金（元）</label>
                    <input type="number" class="settings-input" id="backtestInitialCash"
                           value="100000" min="10000" step="10000">
                </div>
            </div>
        <div class="settings-footer" style="padding:10px 0;border-top:1px solid var(--border);margin-top:12px;display:flex;gap:6px;justify-content:flex-end">
            <button class="settings-btn secondary" id="backtestCancelBtn">取消</button>
            <button class="settings-btn primary" id="backtestRunBtn">运行回测</button>
        </div>
    `);

    document.getElementById('backtestCancelBtn').addEventListener('click', _hidePageView);

    // Load strategies
    const strategySelect = document.getElementById('backtestStrategy');
    try {
        const resp = await window.backtestAPI.listStrategies();
        if (resp.code === 0 && resp.data) {
            const strategies = resp.data;
            strategySelect.innerHTML = '';
            strategies.forEach(s => {
                const opt = document.createElement('option');
                opt.value = s.name;
                opt.textContent = STRATEGY_LABELS[s.name] || s.description || s.name;
                strategySelect.appendChild(opt);
            });
            // Load params for first strategy
            if (strategies.length > 0) {
                await loadStrategyParams(strategies[0].name);
            }
        }
    } catch (e) {
        strategySelect.innerHTML = '<option value="">加载失败</option>';
        console.error('[Backtest] Failed to load strategies:', e);
    }

    // Strategy change handler
    strategySelect.addEventListener('change', async () => {
        await loadStrategyParams(strategySelect.value);
    });

    // Run button
    document.getElementById('backtestRunBtn').addEventListener('click', () => runBacktestFromPanel());

    // 启用股票名称联想
    attachSymbolSuggest('backtestSymbol', 'backtestSymbolSuggest');
}

async function loadStrategyParams(strategyName) {
    const container = document.getElementById('backtestParamsContainer');
    if (!container || !strategyName) return;

    container.innerHTML = '<div class="backtest-loading">加载参数中...</div>';

    try {
        const resp = await window.backtestAPI.getParams(strategyName);
        if (resp.code === 0 && resp.data?.params) {
            const params = resp.data.params;
            const keys = Object.keys(params);
            if (keys.length === 0) {
                container.innerHTML = '<div class="backtest-hint">该策略无可配置参数</div>';
                return;
            }

            let html = '<div class="backtest-params-grid">';
            keys.forEach(key => {
                const label = PARAM_LABELS[key] || key;
                const val = params[key];
                const step = typeof val === 'number' && !Number.isInteger(val) ? '0.1' : '1';
                html += `
                    <div class="settings-field" style="margin-bottom:0">
                        <label class="settings-label">${escapeHtml(label)}</label>
                        <input type="number" class="settings-input" data-param="${key}"
                               value="${val}" step="${step}">
                    </div>`;
            });
            html += '</div>';
            container.innerHTML = html;
        } else {
            container.innerHTML = '<div class="backtest-hint">无法加载参数</div>';
        }
    } catch (e) {
        console.error('[Backtest] Failed to load params:', e);
        container.innerHTML = '<div class="backtest-error-hint">参数加载失败</div>';
    }
}

function collectBacktestParams() {
    const params = {};
    document.querySelectorAll('#backtestParamsContainer [data-param]').forEach(input => {
        const key = input.dataset.param;
        const val = parseFloat(input.value);
        if (!isNaN(val)) params[key] = val;
    });
    return params;
}

// 股票名称/代码自动联想
function attachSymbolSuggest(inputId, suggestId) {
    const input = document.getElementById(inputId);
    const suggest = document.getElementById(suggestId);
    if (!input || !suggest) return;

    let timer = null;
    let lastQuery = '';

    input.addEventListener('input', () => {
        const q = input.value.trim();
        if (timer) clearTimeout(timer);
        if (!q || q === lastQuery) {
            suggest.style.display = 'none';
            return;
        }
        // 至少 2 字符才触发（中文 1 字符也算）
        if (q.length < 2 && !/[一-龥]/.test(q)) {
            suggest.style.display = 'none';
            return;
        }
        timer = setTimeout(async () => {
            try {
                const resp = await window.tdxAPI.searchByName(q);
                const items = resp.data || [];
                if (!items.length) { suggest.style.display = 'none'; return; }
                suggest.innerHTML = items.map(it =>
                    `<div class="symbol-suggest-item" data-symbol="${escapeHtml(it.symbol)}" data-name="${escapeHtml(it.name)}">
                        <span class="symbol-suggest-name">${escapeHtml(it.name)}</span>
                        <span class="symbol-suggest-code">${escapeHtml(it.symbol)}</span>
                    </div>`
                ).join('');
                suggest.style.display = 'block';
                suggest.querySelectorAll('.symbol-suggest-item').forEach(el => {
                    el.addEventListener('mousedown', e => {
                        e.preventDefault();
                        input.value = el.dataset.symbol;
                        suggest.style.display = 'none';
                    });
                });
                lastQuery = q;
            } catch (_) { suggest.style.display = 'none'; }
        }, 150);
    });

    input.addEventListener('blur', () => {
        setTimeout(() => { suggest.style.display = 'none'; }, 200);
    });
}

async function runBacktestFromPanel() {
    let symbol = document.getElementById('backtestSymbol').value.trim();
    const strategy = document.getElementById('backtestStrategy').value;
    const startDate = document.getElementById('backtestStartDate').value;
    const endDate = document.getElementById('backtestEndDate').value;
    const initialCash = parseFloat(document.getElementById('backtestInitialCash').value) || 100000;

    // Auto-complete market suffix (600519 -> 600519.SH, 000001 -> 000001.SZ)
    if (symbol && !symbol.includes('.')) {
        const code = symbol.replace(/[^0-9]/g, '');
        if (code.startsWith('6') || code.startsWith('5')) {
            symbol = code + '.SH';
        } else {
            symbol = code + '.SZ';
        }
    }

    // Validate
    if (!symbol) {
        const field = document.getElementById('backtestSymbol');
        let hint = field.parentElement.querySelector('.backtest-error-hint');
        if (!hint) {
            hint = document.createElement('div');
            hint.className = 'backtest-error-hint';
            field.parentElement.appendChild(hint);
        }
        hint.textContent = '请输入股票代码';
        field.focus();
        return;
    }
    if (!strategy) {
        showToast('请选择策略', 'error');
        return;
    }

    const params = collectBacktestParams();
    const strategyLabel = STRATEGY_LABELS[strategy] || strategy;

    // Close page and switch to chat
    _hidePageView();
    hideWelcomeScreen();
    addUserMessage(`回测 ${symbol} 的 ${strategyLabel} 策略`);

    appState.isAnalyzing = true;
    const thinkingId = showThinkingIndicator();

    try {
        const response = await window.backtestAPI.runBacktest({
            symbol, strategy, params, startDate, endDate, initialCash
        });

        removeThinkingIndicator(thinkingId);

        if (response.code === 0 && response.data) {
            window.renderBacktestPanel(response.data);
            appState.currentSymbol = symbol;
        } else {
            addAssistantMessage('回测失败: ' + (response.message || '未知错误'));
        }
    } catch (error) {
        console.error('[Backtest] Run failed:', error);
        removeThinkingIndicator(thinkingId);
        addAssistantMessage(`回测失败：${error.message}`);
    } finally {
        appState.isAnalyzing = false;
    }
}

/**
 * 窗口控制按钮（Electron 无边框窗口）
 */
function setupWindowControls() {
    const api = window.electronAPI;
    if (!api) return;

    const minBtn = document.getElementById('winMinBtn');
    const maxBtn = document.getElementById('winMaxBtn');
    const closeBtn = document.getElementById('winCloseBtn');

    if (minBtn) {
        minBtn.addEventListener('click', () => api.windowMinimize());
    }

    if (maxBtn) {
        maxBtn.addEventListener('click', async () => {
            await api.windowMaximize();
            updateMaximizeIcon();
        });
    }

    if (closeBtn) {
        closeBtn.addEventListener('click', () => api.windowClose());
    }

    // 监听窗口状态变化
    if (api.on) {
        api.on('window-state-changed', (state) => {
            updateMaximizeIcon(state === 'maximized');
        });
    }

    // 初始状态
    api.windowIsMaximized().then(isMax => updateMaximizeIcon(isMax)).catch(() => {});
}

function updateMaximizeIcon(isMaximized) {
    const maxBtn = document.getElementById('winMaxBtn');
    if (!maxBtn) return;
    const iconMax = maxBtn.querySelector('.icon-maximize');
    const iconRestore = maxBtn.querySelector('.icon-restore');
    if (iconMax && iconRestore) {
        iconMax.style.display = isMaximized ? 'none' : '';
        iconRestore.style.display = isMaximized ? '' : 'none';
    }
}

// ==================== 选股结果渲染 ====================

function _renderScreeningTable(data) {
    if (!data || !data.rows || !data.rows.length) return;

    const chatMessages = document.getElementById('chatMessages');
    const maxShow = 20;
    const rows = data.rows;
    const showRows = rows.slice(0, maxShow);
    const hasMore = rows.length > maxShow;
    const sourceLabel = data.source === 'mx_api' ? '妙想API' :
                        data.source === 'tdx_local' ? '本地TDX' : data.source || '';

    const div = document.createElement('div');
    div.className = 'chat-message assistant';

    // 找到股票代码列名（可能不同来源列名不同）
    const codeCol = data.columns.find(c => c.includes('代码') || c === 'SECURITY_CODE') || data.columns[0];
    const nameCol = data.columns.find(c => c.includes('名称') || c === 'SECURITY_SHORT_NAME') || data.columns[1];

    const tableHtml = `
        <div class="screening-result-card">
            <div class="screening-header">
                <span class="screening-count">共 ${data.total} 只</span>
                <span class="screening-source">${escapeHtml(sourceLabel)}</span>
                ${hasMore ? '<button class="screening-view-all" id="screeningViewAllBtn">查看全部</button>' : ''}
            </div>
            <div class="screening-table-wrap">
                <table class="screening-table">
                    <thead><tr>${data.columns.map(c => `<th>${escapeHtml(c)}</th>`).join('')}</tr></thead>
                    <tbody>
                        ${showRows.map(row => `
                            <tr class="screening-row" data-symbol="${escapeHtml(String(row[codeCol] || ''))}" data-name="${escapeHtml(String(row[nameCol] || ''))}">
                                ${data.columns.map(c => `<td>${escapeHtml(String(row[c] ?? ''))}</td>`).join('')}
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        </div>
    `;

    div.innerHTML = `
        <div class="message-avatar"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg></div>
        <div class="message-content">
            <div class="message-name">灵智投研助手</div>
            ${tableHtml}
        </div>
    `;

    chatMessages.appendChild(div);

    // 行点击 → 分析该股票
    div.querySelectorAll('.screening-row').forEach(tr => {
        tr.addEventListener('click', () => {
            const symbol = tr.dataset.symbol;
            const name = tr.dataset.name;
            if (symbol) sendMessage(`分析 ${name || symbol}`);
        });
    });

    // 查看全部
    if (hasMore) {
        div.querySelector('#screeningViewAllBtn').addEventListener('click', () => {
            _openScreeningPanel(data);
        });
    }

    div.scrollIntoView({ behavior: 'smooth', block: 'end' });
}

function _openScreeningPanel(data) {
    const overlay = document.createElement('div');
    overlay.className = 'settings-overlay';
    overlay.id = 'screeningOverlay';

    const codeCol = data.columns.find(c => c.includes('代码') || c === 'SECURITY_CODE') || data.columns[0];
    const nameCol = data.columns.find(c => c.includes('名称') || c === 'SECURITY_SHORT_NAME') || data.columns[1];

    overlay.innerHTML = `
        <div class="monitor-panel" style="width:920px">
            <div class="settings-header">
                <div class="settings-title">选股结果 — ${escapeHtml(data.query || '')}</div>
                <button class="settings-close" id="screeningClose">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                        <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                    </svg>
                </button>
            </div>
            <div class="settings-body">
                <div class="monitor-stats-grid">
                    <div class="monitor-stat-card">
                        <div class="monitor-stat-value">${data.total}</div>
                        <div class="monitor-stat-label">筛选结果</div>
                    </div>
                    <div class="monitor-stat-card">
                        <div class="monitor-stat-value">${data.source === 'mx_api' ? '妙想API' : data.source}</div>
                        <div class="monitor-stat-label">数据来源</div>
                    </div>
                </div>
                <div class="monitor-table-wrap">
                    <table class="monitor-table">
                        <thead><tr>${data.columns.map(c => `<th>${escapeHtml(c)}</th>`).join('')}</tr></thead>
                        <tbody>
                            ${data.rows.map(row => `
                                <tr class="monitor-row-clickable" data-symbol="${escapeHtml(String(row[codeCol] || ''))}" data-name="${escapeHtml(String(row[nameCol] || ''))}">
                                    ${data.columns.map(c => `<td>${escapeHtml(String(row[c] ?? ''))}</td>`).join('')}
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    `;

    document.getElementById('app').appendChild(overlay);

    const close = () => overlay.remove();
    document.getElementById('screeningClose').addEventListener('click', close);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

    overlay.querySelectorAll('.monitor-row-clickable').forEach(tr => {
        tr.addEventListener('click', () => {
            const symbol = tr.dataset.symbol;
            const name = tr.dataset.name;
            overlay.remove();
            if (symbol) sendMessage(`分析 ${name || symbol}`);
        });
    });
}

console.log('[Harness AI] Application script loaded');

// ==================== 监控面板 ====================

let _monitorCurrentPage = 1;

async function openMonitorPanel() {
    _showPageView('对话监控', `
        <div id="monitorBody">
            <div style="text-align:center;padding:40px;color:var(--text-tertiary)">加载中...</div>
        </div>
    `);

    _monitorCurrentPage = 1;
    await _loadMonitorData(1);
}

async function _loadMonitorData(page) {
    _monitorCurrentPage = page;
    const body = document.getElementById('monitorBody');
    if (!body) return;

    try {
        const [statsResp, sessionsResp] = await Promise.all([
            window.monitorAPI.getStats(),
            window.monitorAPI.getSessions(page)
        ]);
        _renderMonitorView(body, statsResp.data, sessionsResp.data, page);
    } catch (e) {
        body.innerHTML = `<div style="text-align:center;padding:40px;color:var(--text-tertiary)">加载失败: ${escapeHtml(e.message)}</div>`;
    }
}

function _renderMonitorView(container, stats, sessions, page) {
    const total = sessions.total || 0;
    const totalPages = Math.ceil(total / (sessions.page_size || 20));

    container.innerHTML = `
        <div class="monitor-stats-grid">
            <div class="monitor-stat-card">
                <div class="monitor-stat-value">${stats.total_sessions || 0}</div>
                <div class="monitor-stat-label">总对话数</div>
            </div>
            <div class="monitor-stat-card">
                <div class="monitor-stat-value">${stats.success_rate || 0}%</div>
                <div class="monitor-stat-label">成功率</div>
            </div>
            <div class="monitor-stat-card">
                <div class="monitor-stat-value">${stats.avg_duration || 0}s</div>
                <div class="monitor-stat-label">平均耗时</div>
            </div>
            <div class="monitor-stat-card">
                <div class="monitor-stat-value">${(stats.top_tools && stats.top_tools[0]) ? stats.top_tools[0].display_name : '-'}</div>
                <div class="monitor-stat-label">最常用工具</div>
            </div>
        </div>

        <div class="monitor-batch-bar" id="monitorBatchBar" style="display:none">
            <span id="monitorBatchCount">已选 0 条</span>
            <button class="monitor-batch-delete-btn" id="monitorBatchDeleteBtn">删除选中</button>
        </div>

        <div class="monitor-table-wrap">
            <table class="monitor-table">
                <thead>
                    <tr>
                        <th style="width:30px"><input type="checkbox" id="monitorSelectAll"></th>
                        <th>时间</th>
                        <th>用户输入</th>
                        <th class="col-fixed">模式</th>
                        <th class="col-fixed">状态</th>
                        <th>耗时</th>
                        <th class="col-fixed">股票</th>
                        <th>操作</th>
                    </tr>
                </thead>
                <tbody id="monitorTableBody">
                    ${_renderSessionRows(sessions.items || [])}
                </tbody>
            </table>
        </div>

        <div class="monitor-pagination">
            <button class="monitor-page-btn" id="monitorPrevBtn" ${page <= 1 ? 'disabled' : ''}>上一页</button>
            <span class="monitor-page-info">第 ${page} / ${totalPages || 1} 页 (共 ${total} 条)</span>
            <button class="monitor-page-btn" id="monitorNextBtn" ${page >= totalPages ? 'disabled' : ''}>下一页</button>
        </div>
    `;

    document.getElementById('monitorPrevBtn').addEventListener('click', () => _loadMonitorData(page - 1));
    document.getElementById('monitorNextBtn').addEventListener('click', () => _loadMonitorData(page + 1));

    // 全选
    document.getElementById('monitorSelectAll').addEventListener('change', (e) => {
        container.querySelectorAll('.monitor-row-check').forEach(cb => { cb.checked = e.target.checked; });
        _updateBatchBar();
    });
    // 单行复选
    container.querySelectorAll('.monitor-row-check').forEach(cb => {
        cb.addEventListener('change', _updateBatchBar);
    });
    // 批量删除
    document.getElementById('monitorBatchDeleteBtn').addEventListener('click', async () => {
        const ids = [...container.querySelectorAll('.monitor-row-check:checked')].map(cb => parseInt(cb.dataset.id));
        if (!ids.length) return;
        if (!await window._showConfirm('删除记录', `确定删除选中的 ${ids.length} 条记录？`)) return;
        await window.monitorAPI.batchDelete(ids);
        await _loadMonitorData(_monitorCurrentPage);
    });

    // 绑定行点击事件
    container.querySelectorAll('.monitor-row-clickable').forEach(row => {
        row.addEventListener('click', (e) => {
            if (e.target.closest('.monitor-row-check') || e.target.closest('.monitor-delete-btn')) return;
            _showSessionDetail(row.dataset.sid);
        });
    });
    container.querySelectorAll('.monitor-delete-btn').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            if (await window._showConfirm('删除记录', '确定删除此记录？')) {
                await window.monitorAPI.deleteSession(btn.dataset.sid);
                await _loadMonitorData(_monitorCurrentPage);
            }
        });
    });
}

function _updateBatchBar() {
    const checked = document.querySelectorAll('#monitorTableBody .monitor-row-check:checked');
    const bar = document.getElementById('monitorBatchBar');
    if (!bar) return;
    bar.style.display = checked.length ? 'flex' : 'none';
    const countEl = document.getElementById('monitorBatchCount');
    if (countEl) countEl.textContent = `已选 ${checked.length} 条`;
}

function _renderSessionRows(items) {
    if (!items.length) return '<tr><td colspan="8" style="text-align:center;padding:20px;color:var(--text-tertiary)">暂无记录</td></tr>';

    return items.map(s => {
        const _utcD = s.created_at && !s.created_at.endsWith('Z') ? new Date(s.created_at.replace(' ', 'T') + 'Z') : (s.created_at ? new Date(s.created_at) : null);
        const _localTime = _utcD && !isNaN(_utcD) ? _utcD.toLocaleString('zh-CN', { month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit' }) : '';
        return `<tr class="monitor-row-clickable" data-sid="${escapeHtml(s.session_id)}">
            <td><input type="checkbox" class="monitor-row-check" data-id="${s.id}"></td>
            <td class="monitor-cell-time">${_localTime}</td>
            <td class="monitor-cell-prompt">${escapeHtml(s.prompt || '')}</td>
            <td class="col-fixed"><span class="monitor-mode-badge">${escapeHtml(s.response_mode_display || s.response_mode)}</span></td>
            <td class="col-fixed"><span class="monitor-status-badge ${s.status}">${s.status === 'completed' ? '成功' : '失败'}</span></td>
            <td>${s.duration ? s.duration.toFixed(1) + 's' : '-'}</td>
            <td class="col-fixed">${escapeHtml(s.stock_name || s.stock_symbol || '-')}</td>
            <td><button class="monitor-delete-btn" data-sid="${escapeHtml(s.session_id)}" title="删除">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                </svg>
            </button></td>
        </tr>
    `;
    }).join('');
}

async function _showSessionDetail(sessionId) {
    const body = document.getElementById('monitorBody');
    if (!body) return;

    try {
        const resp = await window.monitorAPI.getSessionDetail(sessionId);
        const d = resp.data;

        const actionsHtml = (d.actions || []).map(a => `
            <div class="monitor-timeline-item ${a.error ? 'has-error' : ''}">
                <div class="monitor-timeline-dot"></div>
                <div class="monitor-timeline-content">
                    <div class="monitor-timeline-header">
                        <span class="monitor-timeline-tool">${escapeHtml(a.tool_name || '')}</span>
                        <span class="monitor-timeline-time">${a.execution_time ? a.execution_time.toFixed(2) + 's' : ''}</span>
                    </div>
                    ${a.error ? `<div class="monitor-timeline-error">${escapeHtml(a.error)}</div>` : ''}
                    ${a.result_summary ? `<div class="monitor-timeline-result">${escapeHtml(a.result_summary).substring(0, 150)}</div>` : ''}
                </div>
            </div>
        `).join('') || '<div style="color:var(--text-tertiary);padding:12px">无工具调用</div>';

        const thoughtsHtml = (d.thoughts || []).map(t => `
            <div class="monitor-thought-item">
                <span class="monitor-thought-type">${escapeHtml(t.type || '')}</span>
                <span class="monitor-thought-content">${escapeHtml(t.content || '')}</span>
            </div>
        `).join('') || '<div style="color:var(--text-tertiary);padding:12px">无思维记录</div>';

        const llmCallsHtml = (d.llm_calls || []).map((lc, i) => `
            <div class="monitor-llm-call">
                <div class="monitor-llm-call-header">
                    <span class="monitor-llm-phase">${escapeHtml(lc.phase || '')}</span>
                    <span class="monitor-llm-model">${escapeHtml(lc.model || '')}</span>
                    <span class="monitor-llm-duration">${lc.duration_ms ? (lc.duration_ms / 1000).toFixed(1) + 's' : '-'}</span>
                    <span class="monitor-llm-time">${escapeHtml(lc.created_at || '')}</span>
                </div>
                <details class="monitor-llm-details">
                    <summary>输入 (${(lc.input_preview || '').length} 字符)</summary>
                    <pre class="monitor-llm-pre">${escapeHtml(lc.input_preview || '')}</pre>
                </details>
                <details class="monitor-llm-details">
                    <summary>输出 (${(lc.output_preview || '').length} 字符)</summary>
                    <pre class="monitor-llm-pre">${escapeHtml(lc.output_preview || '')}</pre>
                </details>
            </div>
        `).join('') || '<div style="color:var(--text-tertiary);padding:12px">无 LLM 调用记录</div>';

        body.innerHTML = `
            <div class="monitor-detail">
                <div class="monitor-detail-header">
                    <button class="monitor-back-btn" id="monitorBackBtn">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <polyline points="15 18 9 12 15 6"/>
                        </svg>
                        返回列表
                    </button>
                    <span class="monitor-status-badge ${d.status}">${d.status === 'completed' ? '成功' : '失败'}</span>
                </div>

                <div class="monitor-detail-meta">
                    <div class="monitor-meta-item"><strong>用户输入:</strong> ${escapeHtml(d.prompt)}</div>
                    <div class="monitor-meta-row">
                        <span><strong>时间:</strong> ${escapeHtml(d.created_at)}</span>
                        <span><strong>耗时:</strong> ${d.duration ? d.duration.toFixed(1) + 's' : '-'}</span>
                        <span><strong>模式:</strong> ${escapeHtml(d.response_mode_display || d.response_mode)}</span>
                        <span><strong>股票:</strong> ${escapeHtml(d.stock_name || d.stock_symbol || '-')}</span>
                    </div>
                    ${d.error_message ? `<div class="monitor-meta-error"><strong>错误:</strong> ${escapeHtml(d.error_message)}</div>` : ''}
                </div>

                <div class="monitor-section">
                    <div class="monitor-section-title">工具调用 (${d.tool_calls_count || 0} 次)</div>
                    <div class="monitor-timeline">${actionsHtml}</div>
                </div>

                <div class="monitor-section">
                    <div class="monitor-section-title">LLM 调用记录 (${(d.llm_calls || []).length} 次)</div>
                    <div class="monitor-llm-calls">${llmCallsHtml}</div>
                </div>

                <div class="monitor-section">
                    <div class="monitor-section-title">思维过程</div>
                    <div class="monitor-thoughts">${thoughtsHtml}</div>
                </div>

                ${d.report ? `
                <div class="monitor-section">
                    <div class="monitor-section-title">回复摘要</div>
                    <div class="monitor-report">${escapeHtml(d.report.substring(0, 500))}${d.report.length > 500 ? '...' : ''}</div>
                </div>
                ` : ''}
            </div>
        `;

        document.getElementById('monitorBackBtn').addEventListener('click', () => _loadMonitorData(_monitorCurrentPage));
    } catch (e) {
        body.innerHTML = `<div style="text-align:center;padding:40px;color:var(--text-tertiary)">加载失败: ${escapeHtml(e.message)}</div>`;
    }
}

// ==================== 飞书对话实时展示 ====================

let _feishuEventController = null;
const _feishuThinkingIds = {};

// 多视图切换：每个通道独立会话窗口
const _channelMessages = {};  // viewId -> innerHTML string
let _activeView = 'local';    // 'local' | channelId

function _saveCurrentMessages() {
    const cm = document.getElementById('chatMessages');
    if (cm) _channelMessages[_activeView] = cm.innerHTML;
}

function _appendToChannel(channelId, html) {
    if (!_channelMessages[channelId]) _channelMessages[channelId] = '';
    _channelMessages[channelId] += html;
}

function _renderView(viewId) {
    const cm = document.getElementById('chatMessages');
    if (_channelMessages[viewId]) {
        cm.innerHTML = _channelMessages[viewId];
    } else if (viewId === 'local') {
        startNewChat();
    } else {
        cm.innerHTML = '<div class="welcome-screen"><p class="welcome-subtitle">等待飞书消息...</p></div>';
    }
}

function _switchView(viewId) {
    if (viewId === _activeView) return;
    _saveCurrentMessages();
    _activeView = viewId;
    _renderView(viewId);
}

function _subscribeFeishuEvents() {
    if (!window.channelAPI || !window.channelAPI.subscribeFeishuEvents) return;

    // 取消旧订阅
    if (_feishuEventController) {
        _feishuEventController.abort();
    }

    _feishuEventController = window.channelAPI.subscribeFeishuEvents(
        (event) => {
            const { type, data } = event;
            const cid = data.channel_id;
            const isVisible = (_activeView === cid);

            if (type === 'feishu_message') {
                const html = _buildFeishuUserHtml(data.text, data.channel_name);
                _appendToChannel(cid, html);
                if (isVisible) _appendHtmlToChat(html);
            } else if (type === 'feishu_progress') {
                if (isVisible) _updateFeishuProgress(data.session_id, data.message, data.channel_name);
            } else if (type === 'feishu_reply') {
                _removeFeishuThinking(data.session_id);
                const html = _buildFeishuAssistantHtml(data.text, data.channel_name);
                _appendToChannel(cid, html);
                if (isVisible) _appendHtmlToChat(html);
            } else if (type === 'feishu_error') {
                _removeFeishuThinking(data.session_id);
                const html = _buildFeishuAssistantHtml(`分析失败：${data.message}`, data.channel_name);
                _appendToChannel(cid, html);
                if (isVisible) _appendHtmlToChat(html);
            }
        },
        () => {
            console.warn('[Feishu] Event stream error, retrying in 5s...');
            _feishuEventController = null;
            setTimeout(_subscribeFeishuEvents, 5000);
        },
        null  // 不筛选，收所有通道事件
    );
}

function _appendHtmlToChat(html) {
    const cm = document.getElementById('chatMessages');
    // 移除占位的 welcome-screen
    const welcome = cm.querySelector('.welcome-screen');
    if (welcome) welcome.remove();
    cm.insertAdjacentHTML('beforeend', html);
    scrollToBottom();
}

function _buildFeishuUserHtml(content, channelName) {
    const badge = channelName
        ? `<span class="message-source-badge feishu">${escapeHtml(channelName)}</span>`
        : '<span class="message-source-badge feishu">飞书</span>';
    return `<div class="chat-message user">
        <div class="message-content">
            <div class="message-name">飞书用户 ${badge}</div>
            <div class="message-bubble">${escapeHtml(content)}</div>
        </div>
        <div class="message-avatar">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
                <circle cx="12" cy="7" r="4"/>
            </svg>
        </div>
    </div>`;
}

function _buildFeishuAssistantHtml(content, channelName) {
    const badge = channelName
        ? `<span class="message-source-badge feishu">${escapeHtml(channelName)}</span>`
        : '<span class="message-source-badge feishu">飞书</span>';
    const avatarSvg = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M12 2L2 7l10 5 10-5-10-5z"/>
        <path d="M2 17l10 5 10-5"/>
        <path d="M2 12l10 5 10-5"/>
    </svg>`;
    const canParse = typeof marked !== 'undefined' && typeof marked.parse === 'function';
    const contentHtml = canParse ? marked.parse(_fixMarkdownTables(content)) : escapeHtml(content);
    return `<div class="chat-message assistant">
        <div class="message-avatar">${avatarSvg}</div>
        <div class="message-content">
            <div class="message-name">灵智投研助手 ${badge}</div>
            <div class="message-bubble markdown-content">${contentHtml}</div>
        </div>
    </div>`;
}

function _updateFeishuProgress(sessionId, message, channelName) {
    if (!_feishuThinkingIds[sessionId]) {
        _showFeishuThinking(sessionId, channelName);
    }
    const el = document.getElementById(_feishuThinkingIds[sessionId]);
    if (el) {
        const phaseEl = el.querySelector('.thinking-phase');
        if (phaseEl) phaseEl.textContent = message || '分析中...';
    }
    scrollToBottom();
}

function _showFeishuThinking(sessionId, channelName) {
    const chatMessages = document.getElementById('chatMessages');
    // 移除占位的 welcome-screen
    const welcome = chatMessages.querySelector('.welcome-screen');
    if (welcome) welcome.remove();
    const badge = channelName
        ? `<span class="message-source-badge feishu">${escapeHtml(channelName)}</span>`
        : '<span class="message-source-badge feishu">飞书</span>';
    const div = document.createElement('div');
    div.className = 'chat-message assistant';
    const id = 'feishu-thinking-' + Date.now();
    div.id = id;

    const avatarSvg = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M12 2L2 7l10 5 10-5-10-5z"/>
        <path d="M2 17l10 5 10-5"/>
        <path d="M2 12l10 5 10-5"/>
    </svg>`;

    div.innerHTML = `
        <div class="message-avatar">${avatarSvg}</div>
        <div class="message-content">
            <div class="message-name">灵智投研助手 ${badge || ''}</div>
            <div class="thinking-indicator">
                <span>正在分析</span>
                <div class="thinking-dots">
                    <div class="thinking-dot"></div>
                    <div class="thinking-dot"></div>
                    <div class="thinking-dot"></div>
                </div>
                <div class="thinking-phase">准备开始...</div>
            </div>
        </div>
    `;

    chatMessages.appendChild(div);
    _feishuThinkingIds[sessionId] = id;
    scrollToBottom();
}

function _removeFeishuThinking(sessionId) {
    const id = _feishuThinkingIds[sessionId];
    if (id) {
        const el = document.getElementById(id);
        if (el) el.remove();
        delete _feishuThinkingIds[sessionId];
    }
}

// ==================== 飞书通道视图切换 ====================

async function _loadChannelSelector() {
    try {
        const resp = await window.channelAPI.listChannels();
        const channels = resp.data?.channels || resp.data || [];
        if (typeof window._updateChannelList === 'function') {
            const currentChannel = _activeView === 'local' ? '' : _activeView;
            window._updateChannelList(channels, currentChannel);
        }
    } catch (_) {
        /* 静默忽略 */
    }
}

async function _onChannelSelectorChange() {
    await loadSessionList();
}

function _onChannelChange(channelId) {
    // Also update the hidden feishuChannelSelector if it still exists (for backward compat with other code)
    const oldSelector = document.getElementById('feishuChannelSelector');
    if (oldSelector) {
        oldSelector.value = channelId || '';
    }
    // 切换到对应视图
    _switchView(channelId || 'local');
}

function _initChannelDropdown() {
    const channelSelector = document.getElementById('channelSelector');
    const channelDropdown = document.getElementById('channelDropdown');
    const channelSearchInput = document.getElementById('channelSearchInput');
    const channelDropdownList = document.getElementById('channelDropdownList');

    if (!channelSelector || !channelDropdown) return;

    channelSelector.addEventListener('click', (e) => {
        e.stopPropagation();
        const isOpen = channelDropdown.classList.toggle('open');
        if (isOpen && channelSearchInput) {
            setTimeout(() => channelSearchInput.focus(), 50);
        }
    });

    document.addEventListener('click', (e) => {
        if (!channelSelector.contains(e.target)) {
            channelDropdown.classList.remove('open');
        }
    });

    if (channelSearchInput) {
        channelSearchInput.addEventListener('input', () => {
            const filter = channelSearchInput.value.toLowerCase();
            const items = channelDropdownList.querySelectorAll('.channel-dropdown-item:not(.ch-all)');
            items.forEach(item => {
                item.style.display = item.textContent.toLowerCase().includes(filter) ? '' : 'none';
            });
        });

        channelSearchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                channelDropdown.classList.remove('open');
                channelSelector.focus();
            }
        });
    }
}

// ==================== Session Sidebar ====================

function toggleSidebar() {
    appState.sidebarOpen = !appState.sidebarOpen;
    const sidebar = document.getElementById('sessionSidebar');
    if (sidebar) sidebar.classList.toggle('collapsed', !appState.sidebarOpen);
}

function enterEditMode() {
    appState.editMode = true;
    const sidebar = document.getElementById('sessionSidebar');
    if (sidebar) sidebar.classList.add('edit-mode');
    const editBar = document.getElementById('sidebarEditBar');
    if (editBar) editBar.style.display = 'flex';
    const editBtn = document.getElementById('editSessionsBtn');
    if (editBtn) editBtn.style.display = 'none';
    loadSessionList();
}

function exitEditMode() {
    appState.editMode = false;
    const sidebar = document.getElementById('sessionSidebar');
    if (sidebar) sidebar.classList.remove('edit-mode');
    const editBar = document.getElementById('sidebarEditBar');
    if (editBar) editBar.style.display = 'none';
    const editBtn = document.getElementById('editSessionsBtn');
    if (editBtn) editBtn.style.display = '';
    const selectAllCb = document.getElementById('selectAllSessionsCb');
    if (selectAllCb) selectAllCb.checked = false;
    loadSessionList();
}

function _updateDeleteSelectedBtn() {
    const btn = document.getElementById('deleteSelectedBtn');
    if (!btn) return;
    const checked = document.querySelectorAll('.session-item-checkbox:checked');
    btn.disabled = checked.length === 0;
    btn.textContent = checked.length > 0 ? `删除选中 (${checked.length})` : '删除选中';
}

async function deleteSelected() {
    const checked = document.querySelectorAll('.session-item-checkbox:checked');
    if (!checked.length) return;
    const ids = Array.from(checked).map(cb => cb.dataset.cid);
    if (!await window._showConfirm('删除对话', `确定删除选中的 ${ids.length} 条对话？`)) return;
    try {
        await Promise.all(ids.map(id => window.conversationAPI.deleteConversation(id)));
        exitEditMode();
        loadSessionList();
    } catch (e) {
        console.error('[Sessions] Batch delete failed:', e);
    }
}

let _sessionListTimer = null;
async function loadSessionList() {
    if (!window.conversationAPI) return;
    if (_sessionListTimer) clearTimeout(_sessionListTimer);
    _sessionListTimer = setTimeout(async () => {
        try {
            const resp = await window.conversationAPI.listConversations();
            if (resp.code === 0 && resp.data) {
                renderSessionList(resp.data.items || []);
            }
        } catch (e) {
            console.warn('[Sessions] Failed to load:', e);
        }
    }, 300);
}

function renderSessionList(sessions) {
    const container = document.getElementById('sessionList');
    if (!container) return;

    // 更新会话计数
    const countEl = document.getElementById('sessionCount');
    if (countEl) countEl.textContent = sessions.length;

    const isEditMode = appState.editMode;
    const groups = groupSessionsByDate(sessions);
    let html = '';

    for (const [label, items] of groups) {
        html += `<div class="session-group-title">${label}</div>`;
        for (const s of items) {
            const selected = s.conversation_id === appState.activeConversationId ? ' selected' : '';
            const _utcDate = s.created_at && !s.created_at.endsWith('Z') ? new Date(s.created_at.replace(' ', 'T') + 'Z') : new Date(s.created_at);
            const _timeStr = _utcDate.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
            const meta = [s.stock_symbol, _timeStr].filter(Boolean).join(' · ');
            const checkbox = isEditMode ? `<input type="checkbox" class="session-item-checkbox" data-cid="${s.conversation_id}">` : '';
            html += `
                <div class="session-item${selected}" data-cid="${s.conversation_id}">
                    ${checkbox}
                    <div class="session-item-info">
                        <div class="session-item-title">${escapeHtml(s.title || '对话')}</div>
                        <div class="session-item-meta">${meta}</div>
                    </div>
                    <button class="session-item-delete" data-cid="${s.conversation_id}" title="删除">&times;</button>
                </div>`;
        }
    }

    if (!sessions.length) {
        html = '<div style="padding:32px 16px;text-align:center;color:rgba(255,255,255,0.35);font-size:13px;">暂无对话记录</div>';
    }

    container.innerHTML = html;

    container.querySelectorAll('.session-item').forEach(el => {
        el.addEventListener('click', (e) => {
            if (e.target.classList.contains('session-item-delete') || e.target.classList.contains('session-item-checkbox')) return;
            if (appState.editMode) return;
            openConversation(el.dataset.cid);
        });
    });

    container.querySelectorAll('.session-item-delete').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            deleteConversation(btn.dataset.cid);
        });
    });

    container.querySelectorAll('.session-item-checkbox').forEach(cb => {
        cb.addEventListener('change', () => _updateDeleteSelectedBtn());
    });
}

function groupSessionsByDate(sessions) {
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const yesterday = new Date(today); yesterday.setDate(yesterday.getDate() - 1);
    const weekAgo = new Date(today); weekAgo.setDate(weekAgo.getDate() - 7);

    const groups = { '今天': [], '昨天': [], '最近7天': [], '更早': [] };
    for (const s of sessions) {
        const d = s.created_at && !s.created_at.endsWith('Z') ? new Date(s.created_at.replace(' ', 'T') + 'Z') : new Date(s.created_at);
        if (d >= today) groups['今天'].push(s);
        else if (d >= yesterday) groups['昨天'].push(s);
        else if (d >= weekAgo) groups['最近7天'].push(s);
        else groups['更早'].push(s);
    }
    return Object.entries(groups).filter(([, v]) => v.length > 0);
}

async function openConversation(conversationId) {
    if (appState.isAnalyzing) return;

    // 如果当前在页面视图（设置、自选股等），先切回对话视图
    const pageView = document.getElementById('pageView');
    if (pageView && pageView.style.display !== 'none') {
        _hidePageView();
    }

    try {
        const resp = await window.conversationAPI.getMessages(conversationId);
        if (resp.code !== 0 || !resp.data) return;

        hideWelcomeScreen();

        const chatMessages = document.getElementById('chatMessages');
        chatMessages.innerHTML = '';

        appState.messages = [];
        appState.sessionId = conversationId;
        appState.activeConversationId = conversationId;

        // Update header breadcrumb
        const sessionItem = document.querySelector(`.session-item[data-cid="${conversationId}"]`);
        const titleEl = sessionItem?.querySelector('.session-item-title');
        const headerSessionName = document.getElementById('headerSessionName');
        if (headerSessionName) {
            headerSessionName.textContent = titleEl ? titleEl.textContent : '新对话';
        }

        for (const msg of resp.data.messages) {
            if (msg.role === 'user') {
                addUserMessage(msg.content);
            } else if (msg.role === 'assistant') {
                addAssistantMessage(msg.content, true);
            }
        }

        _updateSidebarSelection();
    } catch (e) {
        console.error('[Sessions] Failed to open:', e);
    }
}

async function deleteConversation(conversationId) {
    if (!await window._showConfirm('删除对话', '确定删除此对话？')) return;
    try {
        await window.conversationAPI.deleteConversation(conversationId);
        if (appState.activeConversationId === conversationId) {
            appState.activeConversationId = null;
            appState.sessionId = null;
        }
        loadSessionList();
    } catch (e) {
        console.error('[Sessions] Failed to delete:', e);
    }
}

async function clearAllConversations() {
    if (!await window._showConfirm('清空所有对话', '确定清空所有对话记录？此操作不可恢复。')) return;
    try {
        await window.conversationAPI.clearAll();
        appState.activeConversationId = null;
        appState.sessionId = null;
        loadSessionList();
    } catch (e) {
        console.error('[Sessions] Failed to clear:', e);
    }
}

function _updateSidebarSelection() {
    const container = document.getElementById('sessionList');
    if (!container) return;
    container.querySelectorAll('.session-item').forEach(el => {
        el.classList.toggle('selected', el.dataset.cid === appState.activeConversationId);
    });
}

// ==================== 定时任务面板 ====================

const SCHEDULER_DAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];

function _scheduleLabel(task) {
    const cfg = task.schedule_config || {};
    const type = task.schedule_type;
    const time = cfg.time || '09:00';
    if (type === 'weekly') {
        const days = (cfg.weekdays || []).map(d => SCHEDULER_DAYS[d - 1]).filter(Boolean);
        return (days.length === 7 ? '每天' : days.join('、')) + ' ' + time;
    } else if (type === 'monthly') {
        return `每月${cfg.day_from || 1}~${cfg.day_to || 31}号 ${time}`;
    } else if (type === 'once') {
        return `${cfg.date || ''} ${time}（单次）`;
    } else if (type === 'interval') {
        const days = (cfg.weekdays || []).map(d => SCHEDULER_DAYS[d - 1]).filter(Boolean);
        const dayLabel = days.length === 5 && cfg.weekdays?.every(d => d >= 1 && d <= 5) ? '工作日' : (days.length === 7 ? '每天' : days.join('、'));
        return `${cfg.start_time || '09:30'}~${cfg.end_time || '11:30'} 每${cfg.interval_minutes || 30}分钟 (${dayLabel})`;
    }
    return task.cron_expression || '';
}

let _schedulerPollTimer = null;

async function openSchedulerPanel() {
    // 停止旧轮询
    if (_schedulerPollTimer) { clearInterval(_schedulerPollTimer); _schedulerPollTimer = null; }

    _showPageView('定时任务', `
        <div style="display:flex;gap:8px;align-items:center;margin-bottom:12px">
            <input type="text" class="settings-input" id="schedulerSearch" placeholder="搜索任务..." style="flex:1">
            <button class="settings-btn primary" id="schedulerAddBtn" style="white-space:nowrap">+ 新建任务</button>
        </div>
        <div id="schedulerBody">
            <div style="text-align:center;padding:40px;color:var(--text-tertiary)">加载中...</div>
        </div>
    `);

    // 注入 pulse 动画（只注入一次）
    if (!document.getElementById('schedulerPulseStyle')) {
        const style = document.createElement('style');
        style.id = 'schedulerPulseStyle';
        style.textContent = '@keyframes schedulerPulse{0%,100%{opacity:1}50%{opacity:.3}}';
        document.head.appendChild(style);
    }

    document.getElementById('schedulerAddBtn').addEventListener('click', () => _showTaskEditor(null, () => _loadSchedulerList()));

    let searchTimer = null;
    document.getElementById('schedulerSearch').addEventListener('input', () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => _loadSchedulerList(), 300);
    });

    await _loadSchedulerList();

    // 启动轮询实时状态（每 5 秒）
    _schedulerPollTimer = setInterval(() => _pollSchedulerStatus(), 5000);
}

async function _loadSchedulerList() {
    const body = document.getElementById('schedulerBody');
    const search = document.getElementById('schedulerSearch');
    if (!body) return;
    const keyword = search ? search.value.trim() : '';

    try {
        const resp = await window.schedulerAPI.listTasks(keyword || undefined);
        const tasks = resp.data?.tasks || [];
        if (!tasks.length) {
            body.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-tertiary)">暂无定时任务</div>';
            return;
        }

        body.innerHTML = tasks.map(t => {
            const statusBadge = t.last_run_status === 'success' ? '<span style="color:#22c55e;font-size:11px">成功</span>'
                : t.last_run_status === 'failed' ? '<span style="color:#ef4444;font-size:11px">失败</span>'
                : '<span style="color:var(--text-tertiary);font-size:11px">未执行</span>';
            const enabledBadge = t.enabled
                ? '<span style="color:#22c55e;font-size:11px">已启用</span>'
                : '<span style="color:var(--text-tertiary);font-size:11px">已禁用</span>';
            const lastRun = t.last_run_at
                ? new Date(t.last_run_at).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})
                : '-';
            const pushLabel = t.receive_id_type === 'open_id' ? '私聊' : '群聊';

            return `<div class="scheduler-task-card" data-tid="${t.id}" style="padding:12px 14px;margin-bottom:8px;background:rgba(255,255,255,0.04);border-radius:8px;border:1px solid rgba(255,255,255,0.08)">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
                    <div style="font-weight:600;font-size:13px">${escapeHtml(t.name || '未命名任务')}</div>
                    <div style="display:flex;gap:4px;align-items:center">${enabledBadge} ${statusBadge}</div>
                </div>
                <div style="font-size:12px;color:var(--text-secondary);margin-bottom:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(t.prompt)}</div>
                <div style="font-size:11px;color:var(--text-tertiary);display:flex;gap:12px;flex-wrap:wrap">
                    <span>${escapeHtml(_scheduleLabel(t))}</span>
                    <span>推送: ${pushLabel}</span>
                    <span>上次: ${lastRun}</span>
                    <span>执行 ${t.run_count || 0} 次</span>
                </div>
                <div class="scheduler-live-status" data-tid="${t.id}" style="display:none;margin-top:6px;padding:6px 8px;background:rgba(99,102,241,0.1);border-radius:4px;font-size:11px;color:#818cf8;border-left:2px solid #818cf8"></div>
                <div style="display:flex;gap:6px;margin-top:8px">
                    <button class="scheduler-action-btn" data-action="trigger" data-tid="${t.id}" style="background:none;border:1px solid var(--accent);border-radius:4px;padding:3px 10px;color:var(--accent);cursor:pointer;font-size:11px">测试</button>
                    <button class="scheduler-action-btn" data-action="edit" data-tid="${t.id}" style="background:none;border:1px solid rgba(255,255,255,0.15);border-radius:4px;padding:3px 10px;color:var(--text-secondary);cursor:pointer;font-size:11px">编辑</button>
                    <button class="scheduler-action-btn" data-action="toggle" data-tid="${t.id}" data-enabled="${t.enabled}" style="background:none;border:1px solid rgba(255,255,255,0.15);border-radius:4px;padding:3px 10px;color:var(--text-secondary);cursor:pointer;font-size:11px">${t.enabled ? '禁用' : '启用'}</button>
                    <button class="scheduler-action-btn" data-action="delete" data-tid="${t.id}" style="background:none;border:1px solid rgba(255,100,100,0.3);border-radius:4px;padding:3px 10px;color:#ef4444;cursor:pointer;font-size:11px">删除</button>
                </div>
                <div class="scheduler-trigger-result" data-tid="${t.id}" style="display:none;margin-top:8px;padding:8px;background:rgba(0,0,0,0.2);border-radius:6px;font-size:12px;max-height:200px;overflow:auto"></div>
            </div>`;
        }).join('');

        // 绑定事件
        body.querySelectorAll('.scheduler-action-btn').forEach(btn => {
            btn.addEventListener('click', async () => {
                const action = btn.dataset.action;
                const tid = parseInt(btn.dataset.tid);
                if (action === 'trigger') {
                    await _triggerSchedulerTask(tid, btn);
                } else if (action === 'edit') {
                    const task = tasks.find(t => t.id === tid);
                    if (task) _showTaskEditor(task, () => _loadSchedulerList());
                } else if (action === 'toggle') {
                    const newEnabled = btn.dataset.enabled !== 'true';
                    await window.schedulerAPI.toggleTask(tid, newEnabled);
                    await _loadSchedulerList();
                } else if (action === 'delete') {
                    if (!await window._showConfirm('删除任务', '确定删除此任务？')) return;
                    await window.schedulerAPI.deleteTask(tid);
                    await _loadSchedulerList();
                }
            });
        });
        // 加载完后立即拉一次实时状态
        _pollSchedulerStatus();
    } catch (e) {
        body.innerHTML = `<div style="text-align:center;padding:40px;color:var(--text-tertiary)">加载失败: ${escapeHtml(e.message)}</div>`;
    }
}

async function _pollSchedulerStatus() {
    try {
        const resp = await window.schedulerAPI.getTasksStatus();
        const statusMap = resp.data || {};
        document.querySelectorAll('.scheduler-live-status').forEach(el => {
            const tid = el.dataset.tid;
            const s = statusMap[tid];
            if (s) {
                const isTerminal = ['done', 'failed', 'skipped', 'feishu_done', 'feishu_failed'].includes(s.step);
                const color = s.step === 'failed' ? '#ef4444'
                    : s.step === 'done' || s.step === 'feishu_done' ? '#22c55e'
                    : '#818cf8';
                const pulseDot = !isTerminal ? '<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#818cf8;margin-right:6px;animation:schedulerPulse 1.5s infinite"></span>' : '';
                el.style.display = 'block';
                el.style.borderColor = color;
                el.style.color = color;
                el.style.background = color === '#ef4444' ? 'rgba(239,68,68,0.1)' : color === '#22c55e' ? 'rgba(34,197,94,0.1)' : 'rgba(99,102,241,0.1)';
                el.innerHTML = `${pulseDot}${escapeHtml(s.detail || s.step)}`;
            } else {
                el.style.display = 'none';
            }
        });
    } catch (e) {}
}

async function _triggerSchedulerTask(taskId, btn) {
    const resultEl = document.querySelector(`.scheduler-trigger-result[data-tid="${taskId}"]`);
    if (!resultEl) return;

    btn.disabled = true;
    btn.textContent = '执行中...';
    resultEl.style.display = 'block';
    resultEl.innerHTML = '<div style="color:var(--text-tertiary)">正在执行分析并推送飞书，请稍候...</div>';

    try {
        const resp = await window.schedulerAPI.triggerTask(taskId);
        const data = resp.data || {};
        // 先保存结果到变量，因为 _loadSchedulerList 会重建 DOM
        let resultHtml = '';
        if (data.status === 'success') {
            const canParse = typeof marked !== 'undefined' && typeof marked.parse === 'function';
            const reportHtml = canParse ? marked.parse(data.report || '分析完成') : escapeHtml(data.report || '分析完成');
            resultHtml = `<div style="color:#22c55e;margin-bottom:4px">执行成功 ${data.feishu_sent ? '| 飞书已推送' : '| 飞书未推送'}</div><div class="markdown-content" style="font-size:12px">${reportHtml}</div>`;
        } else {
            resultHtml = `<div style="color:#ef4444">执行失败: ${escapeHtml(data.message || '未知错误')}</div>`;
        }
        // 刷新列表（会重建 DOM）
        await _loadSchedulerList();
        // 刷新后重新查找 result 容器并写入结果
        const newResultEl = document.querySelector(`.scheduler-trigger-result[data-tid="${taskId}"]`);
        if (newResultEl) {
            newResultEl.style.display = 'block';
            newResultEl.innerHTML = resultHtml;
        }
    } catch (e) {
        // 刷新后 DOM 可能已变，重新查找
        const curResultEl = document.querySelector(`.scheduler-trigger-result[data-tid="${taskId}"]`);
        const target = curResultEl || resultEl;
        if (target) {
            target.style.display = 'block';
            target.innerHTML = `<div style="color:#ef4444">请求失败: ${escapeHtml(e.message)}</div>`;
        }
    } finally {
        // 刷新后按钮也是新的，不需要手动恢复
    }
}

function _showTaskEditor(task, onClose) {
    const isEdit = !!task;
    const name = task?.name || '';
    const prompt = task?.prompt || '';
    const scheduleType = task?.schedule_type || 'weekly';
    const cfg = task?.schedule_config || {};
    const receiveId = task?.receive_id || '';
    const receiveIdType = task?.receive_id_type || 'chat_id';
    const feishuChannelId = task?.feishu_channel_id || '';
    const startDate = task?.start_date || '';
    const endDate = task?.end_date || '';
    const enabled = task?.enabled !== false;

    const weekdays = cfg.weekdays || [1,2,3,4,5];
    const time = cfg.time || '09:00';
    const dayFrom = cfg.day_from || 1;
    const dayTo = cfg.day_to || 31;
    const onceDate = cfg.date || '';
    const intervalStart = cfg.start_time || '09:30';
    const intervalEnd = cfg.end_time || '11:30';
    const intervalMinutes = cfg.interval_minutes || 30;
    const skipHolidays = cfg.skip_holidays || false;

    const dialog = document.createElement('div');
    dialog.className = 'settings-overlay';
    dialog.innerHTML = `
        <div class="settings-panel" style="width:520px">
            <div class="settings-header">
                <div class="settings-title">${isEdit ? '编辑定时任务' : '新建定时任务'}</div>
                <button class="settings-close" id="taskEditClose">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                        <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                    </svg>
                </button>
            </div>
            <div class="settings-body">
                <div class="settings-field">
                    <label class="settings-label">任务名称</label>
                    <input type="text" class="settings-input" id="taskName" value="${escapeHtml(name)}" placeholder="如：每日茅台分析">
                </div>
                <div class="settings-field">
                    <label class="settings-label">任务内容（分析 prompt）</label>
                    <textarea class="settings-input" id="taskPrompt" rows="3" style="resize:vertical" placeholder="如：分析贵州茅台的投资价值">${escapeHtml(prompt)}</textarea>
                </div>
                <div class="settings-field">
                    <label class="settings-label">调度类型</label>
                    <select class="settings-input" id="taskScheduleType">
                        <option value="weekly" ${scheduleType === 'weekly' ? 'selected' : ''}>按星期</option>
                        <option value="monthly" ${scheduleType === 'monthly' ? 'selected' : ''}>按日期</option>
                        <option value="once" ${scheduleType === 'once' ? 'selected' : ''}>单次</option>
                        <option value="interval" ${scheduleType === 'interval' ? 'selected' : ''}>按频率循环</option>
                    </select>
                </div>
                <div id="taskScheduleConfig"></div>
                <div class="settings-field">
                    <label class="settings-label">有效日期范围</label>
                    <div style="display:flex;gap:8px">
                        <input type="date" class="settings-input" id="taskStartDate" value="${escapeHtml(startDate)}" placeholder="开始日期">
                        <input type="date" class="settings-input" id="taskEndDate" value="${escapeHtml(endDate)}" placeholder="结束日期">
                    </div>
                </div>
                <div class="settings-field">
                    <label class="settings-label">飞书通道</label>
                    <select class="settings-input" id="taskFeishuChannel">
                        <option value="">加载中...</option>
                    </select>
                    <div style="font-size:11px;color:var(--text-tertiary);margin-top:2px">选择用哪个飞书应用的凭证发送消息</div>
                </div>
                <div class="settings-field">
                    <label class="settings-label">推送模式</label>
                    <select class="settings-input" id="taskPushMode">
                        <option value="channel" ${task?.use_channel_push_targets ? 'selected' : ''}>使用通道默认推送目标</option>
                        <option value="custom" ${!task?.use_channel_push_targets ? 'selected' : ''}>自定义推送目标</option>
                    </select>
                </div>
                <div id="taskChannelPushInfo" style="font-size:11px;color:var(--text-tertiary);margin-bottom:6px;display:${task?.use_channel_push_targets ? 'block' : 'none'}"></div>
                <div id="taskCustomPushConfig" style="display:${task?.use_channel_push_targets ? 'none' : 'block'}">
                    <div class="settings-field">
                        <label class="settings-label">推送目标类型</label>
                        <select class="settings-input" id="taskReceiveType">
                            <option value="chat_id" ${receiveIdType === 'chat_id' ? 'selected' : ''}>群聊 (chat_id)</option>
                            <option value="open_id" ${receiveIdType === 'open_id' ? 'selected' : ''}>私聊 (open_id)</option>
                        </select>
                    </div>
                    <div class="settings-field">
                        <label class="settings-label">推送目标 ID</label>
                        <input type="text" class="settings-input" id="taskReceiveId" value="${escapeHtml(receiveId)}" placeholder="飞书群聊 chat_id 或用户 open_id">
                    </div>
                </div>
                <div id="taskTestResult" style="display:none;margin-top:8px;padding:8px;background:rgba(0,0,0,0.2);border-radius:6px;font-size:12px;max-height:200px;overflow:auto"></div>
            </div>
            <div class="settings-footer">
                <button class="settings-btn test" id="taskTestBtn">测试执行</button>
                <button class="settings-btn secondary" id="taskEditCancel">取消</button>
                <button class="settings-btn primary" id="taskEditSave">保存</button>
            </div>
        </div>
    `;
    document.body.appendChild(dialog);

    const closeDialog = () => { dialog.remove(); };
    document.getElementById('taskEditClose').addEventListener('click', closeDialog);
    document.getElementById('taskEditCancel').addEventListener('click', closeDialog);
    dialog.addEventListener('click', (e) => { if (e.target === dialog) closeDialog(); });

    // 加载飞书通道列表到下拉选择器
    (async () => {
        const sel = document.getElementById('taskFeishuChannel');
        if (!sel) return;
        try {
            const resp = await window.channelAPI.listChannels();
            const channels = resp.data?.channels || [];
            // 新建任务时默认选中第一个通道；编辑时保留原选择
            const defaultChId = feishuChannelId || (channels[0] && channels[0].id) || '';
            sel.innerHTML = channels.length
                ? ''
                : '<option value="">（暂无可用通道）</option>';
            channels.forEach(ch => {
                const status = ch.running ? ' (运行中)' : '';
                const selected = ch.id === defaultChId ? ' selected' : '';
                sel.innerHTML += `<option value="${escapeHtml(ch.id)}"${selected}>${escapeHtml(ch.name || ch.id)}${status}</option>`;
            });
        } catch (e) {
            sel.innerHTML = '<option value="">无可用通道</option>';
        }
    })();

    // 推送模式切换
    function updatePushModeUI() {
        const mode = document.getElementById('taskPushMode')?.value;
        const customEl = document.getElementById('taskCustomPushConfig');
        const infoEl = document.getElementById('taskChannelPushInfo');
        if (customEl) customEl.style.display = mode === 'custom' ? 'block' : 'none';
        if (infoEl) {
            infoEl.style.display = mode === 'channel' ? 'block' : 'none';
            if (mode === 'channel') {
                const chId = document.getElementById('taskFeishuChannel')?.value;
                if (chId) {
                    window.channelAPI.listChannels().then(resp => {
                        const ch = (resp.data?.channels || []).find(c => c.id === chId);
                        const targets = ch?.push_targets || [];
                        if (targets.length) {
                            infoEl.textContent = '通道推送目标: ' + targets.map(t => t.label || t.receive_id).join(', ');
                        } else {
                            infoEl.textContent = '该通道未配置推送目标，请在通道设置中添加';
                        }
                    }).catch(() => { infoEl.textContent = ''; });
                } else {
                    infoEl.textContent = '请先选择飞书通道';
                }
            }
        }
    }
    document.getElementById('taskPushMode')?.addEventListener('change', updatePushModeUI);
    document.getElementById('taskFeishuChannel')?.addEventListener('change', updatePushModeUI);
    updatePushModeUI();

    // 调度配置动态渲染
    function renderScheduleConfig() {
        const type = document.getElementById('taskScheduleType').value;
        const container = document.getElementById('taskScheduleConfig');
        let html = '';

        if (type === 'weekly') {
            html = `
                <div class="settings-field">
                    <label class="settings-label">选择日期</label>
                    <div style="display:flex;gap:6px;flex-wrap:wrap">
                        ${SCHEDULER_DAYS.map((d, i) => `<label style="display:flex;align-items:center;gap:3px;font-size:12px;cursor:pointer">
                            <input type="checkbox" class="task-weekday-cb" data-day="${i+1}" ${weekdays.includes(i+1) ? 'checked' : ''}> ${d}
                        </label>`).join('')}
                    </div>
                </div>
                <div class="settings-field">
                    <label class="settings-label">执行时间</label>
                    <input type="time" class="settings-input" id="taskTime" value="${time}">
                </div>`;
        } else if (type === 'monthly') {
            html = `
                <div class="settings-field">
                    <label class="settings-label">几号到几号</label>
                    <div style="display:flex;gap:8px;align-items:center">
                        <input type="number" class="settings-input" id="taskDayFrom" value="${dayFrom}" min="1" max="31" style="width:80px">
                        <span style="color:var(--text-tertiary)">~</span>
                        <input type="number" class="settings-input" id="taskDayTo" value="${dayTo}" min="1" max="31" style="width:80px">
                    </div>
                </div>
                <div class="settings-field">
                    <label class="settings-label">执行时间</label>
                    <input type="time" class="settings-input" id="taskTime" value="${time}">
                </div>`;
        } else if (type === 'interval') {
            html = `
                <div class="settings-field">
                    <label class="settings-label">时间段</label>
                    <div style="display:flex;gap:8px;align-items:center">
                        <input type="time" class="settings-input" id="taskIntervalStart" value="${intervalStart}">
                        <span style="color:var(--text-tertiary)">~</span>
                        <input type="time" class="settings-input" id="taskIntervalEnd" value="${intervalEnd}">
                    </div>
                </div>
                <div class="settings-field">
                    <label class="settings-label">间隔（分钟）</label>
                    <input type="number" class="settings-input" id="taskIntervalMin" value="${intervalMinutes}" min="5" max="480" style="width:100px">
                </div>
                <div class="settings-field">
                    <label class="settings-label">执行日</label>
                    <div style="display:flex;gap:6px;flex-wrap:wrap">
                        ${SCHEDULER_DAYS.map((d, i) => `<label style="display:flex;align-items:center;gap:3px;font-size:12px;cursor:pointer">
                            <input type="checkbox" class="task-weekday-cb" data-day="${i+1}" ${weekdays.includes(i+1) ? 'checked' : ''}> ${d}
                        </label>`).join('')}
                    </div>
                </div>`;
        } else {
            html = `
                <div class="settings-field">
                    <label class="settings-label">执行日期</label>
                    <input type="date" class="settings-input" id="taskOnceDate" value="${escapeHtml(onceDate)}">
                </div>
                <div class="settings-field">
                    <label class="settings-label">执行时间</label>
                    <input type="time" class="settings-input" id="taskTime" value="${time}">
                </div>`;
        }

        // 所有调度类型都加节假日开关
        html += `
            <div class="settings-field" style="margin-top:8px">
                <label style="display:flex;align-items:center;gap:6px;font-size:12px;cursor:pointer">
                    <input type="checkbox" id="taskSkipHolidays" ${skipHolidays ? 'checked' : ''}>
                    跳过法定节假日（仅交易日执行）
                </label>
            </div>`;

        container.innerHTML = html;
    }
    renderScheduleConfig();
    document.getElementById('taskScheduleType').addEventListener('change', renderScheduleConfig);

    // 收集表单数据
    function collectFormData() {
        const type = document.getElementById('taskScheduleType').value;
        const timeVal = document.getElementById('taskTime')?.value || '09:00';
        let scheduleConfig = {};
        if (type === 'weekly') {
            const days = [...document.querySelectorAll('.task-weekday-cb:checked')].map(cb => parseInt(cb.dataset.day));
            if (!days.length) return null;
            scheduleConfig = { time: timeVal, weekdays: days };
        } else if (type === 'monthly') {
            scheduleConfig = {
                time: timeVal,
                day_from: parseInt(document.getElementById('taskDayFrom')?.value) || 1,
                day_to: parseInt(document.getElementById('taskDayTo')?.value) || 31,
            };
        } else if (type === 'interval') {
            const days = [...document.querySelectorAll('.task-weekday-cb:checked')].map(cb => parseInt(cb.dataset.day));
            if (!days.length) return null;
            scheduleConfig = {
                start_time: document.getElementById('taskIntervalStart')?.value || '09:30',
                end_time: document.getElementById('taskIntervalEnd')?.value || '11:30',
                interval_minutes: parseInt(document.getElementById('taskIntervalMin')?.value) || 30,
                weekdays: days,
            };
        } else {
            scheduleConfig = { time: timeVal, date: document.getElementById('taskOnceDate')?.value || '' };
            if (!scheduleConfig.date) return null;
        }
        // 节假日开关
        if (document.getElementById('taskSkipHolidays')?.checked) {
            scheduleConfig.skip_holidays = true;
        }
        const pushMode = document.getElementById('taskPushMode')?.value || 'custom';
        return {
            name: document.getElementById('taskName').value.trim(),
            prompt: document.getElementById('taskPrompt').value.trim(),
            schedule_type: type,
            schedule_config: scheduleConfig,
            receive_id: pushMode === 'custom' ? (document.getElementById('taskReceiveId')?.value.trim() || '') : '',
            receive_id_type: document.getElementById('taskReceiveType')?.value || 'chat_id',
            feishu_channel_id: document.getElementById('taskFeishuChannel')?.value || null,
            use_channel_push_targets: pushMode === 'channel',
            start_date: document.getElementById('taskStartDate').value || null,
            end_date: document.getElementById('taskEndDate').value || null,
            enabled: enabled,
        };
    }

    // 保存
    document.getElementById('taskEditSave').addEventListener('click', async () => {
        const data = collectFormData();
        if (!data) { showToast('请填写调度配置', 'error'); return; }
        if (!data.prompt) { showToast('请填写任务内容', 'error'); return; }
        try {
            if (isEdit) {
                await window.schedulerAPI.updateTask(task.id, data);
            } else {
                await window.schedulerAPI.createTask(data);
            }
            showToast('保存成功', 'success');
            closeDialog();
            if (onClose) await onClose();
        } catch (e) {
            showToast('保存失败: ' + e.message, 'error');
        }
    });

    // 测试执行
    document.getElementById('taskTestBtn').addEventListener('click', async () => {
        const data = collectFormData();
        if (!data || !data.prompt) { showToast('请先填写任务内容', 'error'); return; }
        // 先保存（如果是新建的）
        if (!isEdit) {
            showToast('请先保存任务再测试', 'error');
            return;
        }
        const testBtn = document.getElementById('taskTestBtn');
        const resultEl = document.getElementById('taskTestResult');
        testBtn.disabled = true;
        testBtn.textContent = '执行中...';
        resultEl.style.display = 'block';
        resultEl.innerHTML = '<div style="color:var(--text-tertiary)">正在执行分析并推送飞书...</div>';
        try {
            const resp = await window.schedulerAPI.triggerTask(task.id);
            const result = resp.data || {};
            if (result.status === 'success') {
                const canParse = typeof marked !== 'undefined' && typeof marked.parse === 'function';
                const reportHtml = canParse ? marked.parse(result.report || '分析完成') : escapeHtml(result.report || '分析完成');
                resultEl.innerHTML = `<div style="color:#22c55e;margin-bottom:4px">执行成功 ${result.feishu_sent ? '| 飞书已推送' : '| 飞书未推送'}</div><div class="markdown-content" style="font-size:12px">${reportHtml}</div>`;
            } else {
                resultEl.innerHTML = `<div style="color:#ef4444">执行失败: ${escapeHtml(result.message || '未知错误')}</div>`;
            }
            if (onClose) await onClose();
        } catch (e) {
            resultEl.innerHTML = `<div style="color:#ef4444">请求失败: ${escapeHtml(e.message)}</div>`;
        } finally {
            testBtn.disabled = false;
            testBtn.textContent = '测试执行';
        }
    });

// ==================== 日志查看器 ====================

let _logPollTimer = null;
let _logAutoRefresh = true;

function openLogViewer() {
  document.getElementById('logViewerOverlay').style.display = 'flex';
  _logAutoRefresh = true;
  _fetchLogs();

  if (_logPollTimer) clearInterval(_logPollTimer);
  _logPollTimer = setInterval(() => {
    if (_logAutoRefresh) _fetchLogs();
  }, 3000);
}

function closeLogViewer() {
  document.getElementById('logViewerOverlay').style.display = 'none';
  _logAutoRefresh = false;
  if (_logPollTimer) { clearInterval(_logPollTimer); _logPollTimer = null; }
}

async function _fetchLogs() {
  try {
    const source = document.getElementById('logSourceFilter').value;
    const level = document.getElementById('logLevelFilter').value;
    const search = document.getElementById('logSearchInput').value.trim().toLowerCase();

    const res = await window.apiClient.get(`/api/v1/settings/logs?source=${source}&level=${level}&lines=500`);
    if (res.data.code !== 0) return;

    let text = res.data.data.logs || '';

    if (search) {
      text = text.split('\n').filter(l => l.toLowerCase().includes(search)).join('\n');
    }

    const el = document.getElementById('logViewerContent');
    el.innerHTML = _highlightLogLines(text);
    el.scrollTop = el.scrollHeight;

    const matchCount = search ? text.split('\n').filter(Boolean).length : 0;
    document.getElementById('logViewerStatus').textContent =
      `共 ${res.data.data.total} 行` + (search ? `，匹配 ${matchCount} 行` : '') +
      (_logAutoRefresh ? ' · 自动刷新中（每3秒）' : '');
  } catch (e) {
    document.getElementById('logViewerStatus').textContent = '获取日志失败: ' + e.message;
  }
}

function _highlightLogLines(text) {
  return text.split('\n').map(line => {
    let cls = 'log-info';
    if (line.includes('[ERROR]')) cls = 'log-error';
    else if (line.includes('[WARN]') || line.includes('[WARNING]')) cls = 'log-warn';
    const escaped = line.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    return `<span class="${cls}">${escaped}</span>`;
  }).join('\n');
}

async function _downloadLogs() {
  try {
    const res = await window.apiClient.get('/api/v1/settings/logs/download', { responseType: 'blob' });
    const url = URL.createObjectURL(new Blob([res.data]));
    const a = document.createElement('a');
    a.href = url;
    a.download = 'harness-logs.zip';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch (e) {
    alert('导出失败: ' + e.message);
  }
}

// 日志查看器事件绑定
document.getElementById('logCloseBtn').addEventListener('click', closeLogViewer);
document.getElementById('logRefreshBtn').addEventListener('click', () => _fetchLogs());
document.getElementById('logSourceFilter').addEventListener('change', () => _fetchLogs());
document.getElementById('logLevelFilter').addEventListener('change', () => _fetchLogs());
document.getElementById('logSearchInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') _fetchLogs();
});
document.getElementById('logDownloadBtn').addEventListener('click', _downloadLogs);
document.getElementById('logOpenDirBtn').addEventListener('click', () => {
  window.electronAPI.openLogDir();
});
document.getElementById('logViewerOverlay').addEventListener('click', (e) => {
  if (e.target === document.getElementById('logViewerOverlay')) closeLogViewer();
});
}