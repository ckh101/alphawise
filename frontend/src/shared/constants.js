/**
 * 共享常量定义
 */

// 应用信息
export const APP_NAME = '灵智投研助手';
export const APP_VERSION = '0.1.0';

// API端点
export const API_ENDPOINTS = {
    HEALTH: '/health',
    INFO: '/api/v1/info',
    QUOTE: '/api/v1/quote',
    KLINE: '/api/v1/kline',
};

// 颜色常量
export const COLORS = {
    PRIMARY: '#165DFF',
    UP: '#00B42A',
    DOWN: '#F53F3F',
    WARNING: '#FF7D00',
    SUCCESS: '#00B42A',
};

// 技术指标类型
export const INDICATOR_TYPES = {
    MA: 'ma',
    MACD: 'macd',
    KDJ: 'kdj',
    RSI: 'rsi',
    BOLL: 'boll',
};

// 周期类型
export const PERIOD_TYPES = {
    MIN_1: '1min',
    MIN_5: '5min',
    MIN_15: '15min',
    MIN_30: '30min',
    MIN_60: '60min',
    DAY: 'daily',
    WEEK: 'weekly',
    MONTH: 'monthly',
};

console.log('[constants] Constants loaded');
