/**
 * ECharts图表工具
 */

/**
 * K线图表实例
 */
let klineChart = null;

/**
 * 初始化K线图表
 * @param {string} containerId - 容器ID
 */
function initKlineChart(containerId = 'klineChart') {
  const container = document.getElementById(containerId);
  if (!container) {
    console.error('[Chart] Container not found:', containerId);
    return null;
  }

  // 如果图表已存在，先销毁
  if (klineChart && !klineChart.isDisposed()) {
    klineChart.dispose();
  }

  klineChart = echarts.init(container);

  // 立即resize确保正确尺寸
  setTimeout(() => {
    if (klineChart && !klineChart.isDisposed()) {
      klineChart.resize();
    }
  }, 0);

  // 配置初始空状态
  const option = {
    title: {
      text: '请先搜索股票',
      left: 'center',
      top: 'center',
      textStyle: {
        color: '#8b8da3',
        fontSize: 14
      }
    },
    grid: {
      left: '10%',
      right: '10%',
      bottom: '15%'
    },
    xAxis: {
      type: 'category',
      data: [],
      axisLine: { lineStyle: { color: '#e0dff0' } },
      axisLabel: { color: '#5b5873' }
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLine: { lineStyle: { color: '#e0dff0' } },
      axisLabel: { color: '#5b5873' },
      splitLine: { lineStyle: { color: '#f0eff8' } }
    },
    series: []
  };

  klineChart.setOption(option);

  // 响应式调整
  window.addEventListener('resize', () => {
    if (klineChart && !klineChart.isDisposed()) {
      klineChart.resize();
    }
  });

  console.log('[Chart] Chart initialized');
  return klineChart;
}

/**
 * 更新K线图表数据
 * @param {Array} data - K线数据
 * @param {string} symbol - 股票代码
 * @param {string} period - K线周期
 */
function updateKlineChart(data, symbol, period = 'daily') {
  console.log('[Chart] updateKlineChart called with:', { symbol, period, dataLength: data?.length });

  // 先隐藏loading
  if (klineChart && !klineChart.isDisposed()) {
    klineChart.hideLoading();
  }

  if (!klineChart || klineChart.isDisposed()) {
    console.warn('[Chart] Chart not initialized, creating new one');
    klineChart = initKlineChart();
  }

  if (!data || data.length === 0) {
    console.warn('[Chart] No data to display');
    return;
  }

  // 提取数据
  const dates = data.map(item => item.date);
  const values = data.map(item => [
    item.open,
    item.close,
    item.low,
    item.high
  ]);

  // 计算均线
  const ma5 = calculateMA(5, values);
  const ma10 = calculateMA(10, values);
  const ma20 = calculateMA(20, values);

  // 周期名称映射
  const periodNames = {
    '1min': '1分钟',
    '5min': '5分钟',
    '15min': '15分钟',
    '30min': '30分钟',
    '60min': '60分钟',
    'daily': '日K',
    'weekly': '周K',
    'monthly': '月K'
  };
  const periodName = periodNames[period] || '日K';

  const option = {
    title: {
      text: `${symbol} ${periodName}图`,
      left: '2%',
      textStyle: {
        color: '#1e1b2e',
        fontSize: 16
      }
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: {
        type: 'cross'
      },
      formatter: function (params) {
        if (!params || params.length === 0) {
          return '';
        }

        const klineData = params[0].data;
        const date = params[0].name;

        return `
          <div style="padding: 8px;">
            <div style="margin-bottom: 4px; font-weight: 500;">${date}</div>
            <div>开盘: ${window.formatPrice(klineData[1])}</div>
            <div>收盘: ${window.formatPrice(klineData[2])}</div>
            <div>最低: ${window.formatPrice(klineData[3])}</div>
            <div>最高: ${window.formatPrice(klineData[4])}</div>
          </div>
        `;
      }
    },
    legend: {
      data: [periodName, 'MA5', 'MA10', 'MA20'],
      top: '5%',
      left: 'center'
    },
    grid: {
      left: '10%',
      right: '10%',
      bottom: '15%'
    },
    xAxis: {
      type: 'category',
      data: dates,
      axisLine: { lineStyle: { color: '#E5E6EB' } },
      axisLabel: {
        color: '#4E5969',
        formatter: function (value) {
          return window.formatDate(value);
        }
      }
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLine: { lineStyle: { color: '#e0dff0' } },
      axisLabel: { color: '#5b5873' },
      splitLine: { lineStyle: { color: '#f0eff8' } }
    },
    dataZoom: [
      {
        type: 'inside',
        start: 50,
        end: 100
      },
      {
        show: true,
        type: 'slider',
        top: '90%',
        start: 50,
        end: 100
      }
    ],
    series: [
      {
        name: periodName,
        type: 'candlestick',
        data: values,
        itemStyle: {
          color: '#ef4444',
          color0: '#22c55e',
          borderColor: '#ef4444',
          borderColor0: '#22c55e'
        }
      },
      {
        name: 'MA5',
        type: 'line',
        data: ma5,
        smooth: true,
        lineStyle: {
          opacity: 0.8,
          width: 1
        },
        itemStyle: {
          color: '#f59e0b'
        }
      },
      {
        name: 'MA10',
        type: 'line',
        data: ma10,
        smooth: true,
        lineStyle: {
          opacity: 0.8,
          width: 1
        },
        itemStyle: {
          color: '#6366f1'
        }
      },
      {
        name: 'MA20',
        type: 'line',
        data: ma20,
        smooth: true,
        lineStyle: {
          opacity: 0.8,
          width: 1
        },
        itemStyle: {
          color: '#8b5cf6'
        }
      }
    ]
  };

  klineChart.setOption(option, true);

  // 确保图表正确渲染尺寸
  setTimeout(() => {
    if (klineChart && !klineChart.isDisposed()) {
      klineChart.resize();
    }
  }, 0);

  console.log('[Chart] Chart updated with', data.length, 'bars');
}

/**
 * 计算移动平均线
 * @param {number} dayCount - 天数
 * @param {Array} data - 数据数组
 */
function calculateMA(dayCount, data) {
  const result = [];
  for (let i = 0, len = data.length; i < len; i++) {
    if (i < dayCount - 1) {
      result.push('-');
      continue;
    }
    let sum = 0;
    for (let j = 0; j < dayCount; j++) {
      const price = (data[i - j][1] + data[i - j][2]) / 2;
      sum += price;
    }
    result.push((sum / dayCount).toFixed(2));
  }
  return result;
}

/**
 * 显示图表加载状态
 */
function showChartLoading() {
  if (klineChart && !klineChart.isDisposed()) {
    klineChart.showLoading({
      text: '加载中...',
      color: '#6366f1',
      textColor: '#1e1b2e',
      maskColor: 'rgba(248, 247, 252, 0.8)'
    });
    console.log('[Chart] Loading shown');
  } else {
    console.warn('[Chart] Cannot show loading - chart not ready');
  }
}

/**
 * 隐藏图表加载状态
 */
function hideChartLoading() {
  if (klineChart && !klineChart.isDisposed()) {
    klineChart.hideLoading();
    console.log('[Chart] Loading hidden');
  }
}

/**
 * 显示空数据提示
 */
function showEmptyDataMessage() {
  if (klineChart && !klineChart.isDisposed()) {
    klineChart.hideLoading();
    klineChart.setOption({
      title: {
        text: '暂无K线数据',
        left: 'center',
        top: 'center',
        textStyle: {
          color: '#86909C',
          fontSize: 14
        }
      },
      series: []
    });
  }
}

/**
 * 显示错误信息
 * @param {string} message - 错误信息
 */
function showErrorMessage(message) {
  if (klineChart && !klineChart.isDisposed()) {
    klineChart.hideLoading();
    klineChart.setOption({
      title: {
        text: message || '加载失败',
        left: 'center',
        top: 'center',
        textStyle: {
          color: '#ef4444',
          fontSize: 14
        }
      },
      series: []
    });
  }
}

/**
 * 销毁图表
 */
function disposeChart() {
  if (klineChart && !klineChart.isDisposed()) {
    klineChart.dispose();
  }
  klineChart = null;
  console.log('[Chart] Chart disposed');
}

/**
 * 检查图表是否已初始化
 */
function isChartReady() {
  return klineChart !== null && !klineChart.isDisposed();
}

// 导出为全局对象
window.initKlineChart = initKlineChart;
window.updateKlineChart = updateKlineChart;
window.showChartLoading = showChartLoading;
window.hideChartLoading = hideChartLoading;
window.disposeChart = disposeChart;
window.isChartReady = isChartReady;
window.showEmptyDataMessage = showEmptyDataMessage;
window.showErrorMessage = showErrorMessage;

console.log('[Chart] Chart utils loaded');
