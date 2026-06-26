/**
 * 回测面板渲染工具
 * 用于在聊天中展示策略回测结果
 */

/**
 * 渲染回测结果面板
 * @param {Object} data - 回测结果数据
 */
function renderBacktestPanel(data) {
    const chatMessages = document.getElementById('chatMessages');
    if (!chatMessages) return;

    const panelDiv = document.createElement('div');
    panelDiv.className = 'chat-message assistant';

    const metrics = data.metrics || {};
    const totalReturn = metrics.total_return || 0;
    const isProfit = totalReturn > 0;
    const returnColor = isProfit ? '#F53F3F' : '#00B42A';  // 中国股市红涨绿跌

    const chartId = 'equity-chart-' + Date.now();

    panelDiv.innerHTML = `
        <div class="message-avatar">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>
            </svg>
        </div>
        <div class="message-content">
            <div class="message-name">Harness AI</div>
            <div class="backtest-panel">
                <div class="backtest-header">
                    <div class="backtest-title">
                        <span class="backtest-badge">${data.strategy || ''}</span>
                        ${data.symbol || ''} 策略回测
                    </div>
                    <div class="backtest-period">${data.start_date || ''} ~ ${data.end_date || ''}</div>
                </div>

                <div class="backtest-summary">
                    <div class="backtest-summary-main">
                        <div class="backtest-summary-label">总收益率</div>
                        <div class="backtest-summary-value" style="color: ${returnColor}">
                            ${totalReturn > 0 ? '+' : ''}${totalReturn.toFixed(2)}%
                        </div>
                    </div>
                    <div class="backtest-summary-main">
                        <div class="backtest-summary-label">最终权益</div>
                        <div class="backtest-summary-value">
                            ¥${(data.final_equity || 0).toLocaleString('zh-CN', {minimumFractionDigits: 2})}
                        </div>
                    </div>
                </div>

                <div class="backtest-metrics">
                    <div class="backtest-metric-card">
                        <div class="backtest-metric-value">${(metrics.annualized_return || 0).toFixed(2)}%</div>
                        <div class="backtest-metric-label">年化收益率</div>
                    </div>
                    <div class="backtest-metric-card">
                        <div class="backtest-metric-value" style="color: ${metrics.max_drawdown < -10 ? '#F53F3F' : 'inherit'}">
                            ${(metrics.max_drawdown || 0).toFixed(2)}%
                        </div>
                        <div class="backtest-metric-label">最大回撤</div>
                    </div>
                    <div class="backtest-metric-card">
                        <div class="backtest-metric-value">${(metrics.sharpe_ratio || 0).toFixed(2)}</div>
                        <div class="backtest-metric-label">夏普比率</div>
                    </div>
                    <div class="backtest-metric-card">
                        <div class="backtest-metric-value">${(metrics.win_rate || 0).toFixed(1)}%</div>
                        <div class="backtest-metric-label">胜率</div>
                    </div>
                    <div class="backtest-metric-card">
                        <div class="backtest-metric-value">${metrics.total_trades || 0}</div>
                        <div class="backtest-metric-label">交易次数</div>
                    </div>
                    <div class="backtest-metric-card">
                        <div class="backtest-metric-value">${metrics.profit_trades || 0} / ${metrics.loss_trades || 0}</div>
                        <div class="backtest-metric-label">盈 / 亏</div>
                    </div>
                </div>

                <div class="backtest-chart" id="${chartId}"></div>

                <details class="backtest-trades-section">
                    <summary>交易明细 (${(data.trades || []).length}笔)</summary>
                    <div class="backtest-trades">
                        <table>
                            <thead>
                                <tr>
                                    <th>#</th>
                                    <th>买入日期</th>
                                    <th>买入价</th>
                                    <th>卖出日期</th>
                                    <th>卖出价</th>
                                    <th>收益率</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${(data.trades || []).map((t, i) => {
                                    const ret = t.return_pct || 0;
                                    const color = ret > 0 ? '#F53F3F' : '#00B42A';
                                    return `<tr>
                                        <td>${i + 1}</td>
                                        <td>${t.entry_date || ''}</td>
                                        <td>${(t.entry_price || 0).toFixed(2)}</td>
                                        <td>${t.exit_date || ''}</td>
                                        <td>${(t.exit_price || 0).toFixed(2)}</td>
                                        <td style="color:${color}">${ret > 0 ? '+' : ''}${ret.toFixed(2)}%</td>
                                    </tr>`;
                                }).join('')}
                            </tbody>
                        </table>
                    </div>
                </details>
            </div>
        </div>
    `;

    chatMessages.appendChild(panelDiv);

    // 初始化资金曲线图
    if (data.equity_curve && data.equity_curve.length > 0 && window.echarts) {
        setTimeout(() => initEquityCurveChart(chartId, data.equity_curve, data.initial_cash), 100);
    }

    // 滚动到底部
    const chatContainer = document.getElementById('chatContainer');
    if (chatContainer) {
        chatContainer.scrollTop = chatContainer.scrollHeight;
    }
}

/**
 * 初始化资金曲线ECharts图表
 */
function initEquityCurveChart(containerId, equityCurve, initialCash) {
    const container = document.getElementById(containerId);
    if (!container || !window.echarts) return;

    const chart = window.echarts.init(container);

    const dates = equityCurve.map(p => p.date);
    const values = equityCurve.map(p => p.equity);
    const baseLine = equityCurve.map(() => initialCash || values[0]);

    chart.setOption({
        title: {
            text: '资金曲线',
            left: '2%',
            textStyle: { fontSize: 13, fontWeight: 600, color: '#1d1d1f', fontFamily: '-apple-system, sans-serif', letterSpacing: '-0.12px' }
        },
        tooltip: {
            trigger: 'axis',
            formatter: function(params) {
                const date = params[0].axisValue;
                let html = `<div style="font-size:12px">${date}</div>`;
                params.forEach(p => {
                    html += `<div style="font-size:12px;color:${p.color}">${p.seriesName}: ¥${p.value.toLocaleString()}</div>`;
                });
                return html;
            }
        },
        grid: { left: '12%', right: '5%', top: '18%', bottom: '22%' },
        xAxis: {
            type: 'category',
            data: dates,
            axisLabel: { fontSize: 10, rotate: 30, color: 'rgba(0,0,0,0.48)' },
            axisLine: { lineStyle: { color: '#f5f5f7' } }
        },
        yAxis: {
            type: 'value',
            scale: true,
            axisLabel: {
                fontSize: 10,
                color: 'rgba(0,0,0,0.48)',
                formatter: v => (v / 10000).toFixed(1) + '万'
            },
            splitLine: { lineStyle: { color: '#f5f5f7' } }
        },
        dataZoom: [
            { type: 'inside' },
            { type: 'slider', bottom: '5%', height: 20, borderColor: '#f5f5f7', fillerColor: 'rgba(0,113,227,0.08)', handleStyle: { color: '#0071e3' } }
        ],
        series: [
            {
                name: '策略净值',
                type: 'line',
                data: values,
                lineStyle: { color: '#0071e3', width: 2 },
                areaStyle: {
                    color: {
                        type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                        colorStops: [
                            { offset: 0, color: 'rgba(0,113,227,0.12)' },
                            { offset: 1, color: 'rgba(0,113,227,0.01)' }
                        ]
                    }
                },
                smooth: true,
                symbol: 'none'
            },
            {
                name: '初始资金',
                type: 'line',
                data: baseLine,
                lineStyle: { color: '#f5f5f7', width: 1, type: 'dashed' },
                symbol: 'none'
            }
        ]
    });

    // 响应窗口大小变化
    const resizeHandler = () => {
        if (!chart.isDisposed()) chart.resize();
    };
    window.addEventListener('resize', resizeHandler);
}

// 导出为全局函数
window.renderBacktestPanel = renderBacktestPanel;

console.log('[Backtest] Backtest panel module loaded');
