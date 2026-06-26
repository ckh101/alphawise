/**
 * 数据格式化工具
 */

/**
 * 格式化价格
 * @param {number} price - 价格
 * @param {number} decimals - 小数位数
 */
function formatPrice(price, decimals = 2) {
  if (price == null || price === undefined || price === '--') {
    return '--';
  }
  return Number(price).toFixed(decimals);
}

/**
 * 格式化涨跌幅
 * @param {number} change - 涨跌额
 * @param {number} changePercent - 涨跌幅百分比
 */
function formatChange(change, changePercent) {
  if (change == null || changePercent == null) {
    return { text: '--', class: '' };
  }

  const sign = change >= 0 ? '+' : '';
  const percentSign = changePercent >= 0 ? '+' : '';

  return {
    text: `${sign}${formatPrice(change)} (${percentSign}${changePercent.toFixed(2)}%)`,
    class: change >= 0 ? 'up' : 'down'
  };
}

/**
 * 格式化成交量
 * @param {number} volume - 成交量（手）
 */
function formatVolume(volume) {
  if (volume == null || volume === undefined) {
    return '--';
  }

  if (volume >= 100000000) {
    return (volume / 100000000).toFixed(2) + '亿';
  } else if (volume >= 10000) {
    return (volume / 10000).toFixed(2) + '万';
  }
  return volume.toString();
}

/**
 * 格式化成交额
 * @param {number} amount - 成交额（元）
 */
function formatAmount(amount) {
  if (amount == null || amount === undefined) {
    return '--';
  }

  if (amount >= 100000000) {
    return (amount / 100000000).toFixed(2) + '亿';
  } else if (amount >= 10000) {
    return (amount / 10000).toFixed(2) + '万';
  }
  return amount.toFixed(2);
}

/**
 * 格式化日期
 * @param {string} dateStr - 日期字符串
 */
function formatDate(dateStr) {
  if (!dateStr) {
    return '--';
  }

  try {
    const date = new Date(dateStr);
    const now = new Date();
    const diff = now - date;

    // 如果是今天，只显示时间
    if (date.toDateString() === now.toDateString()) {
      return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    }

    // 如果是今年，显示月-日
    if (date.getFullYear() === now.getFullYear()) {
      return date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
    }

    // 否则显示完整日期
    return date.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });
  } catch (e) {
    return dateStr;
  }
}

/**
 * 格式化时间
 * @param {string} timeStr - 时间字符串
 */
function formatTime(timeStr) {
  if (!timeStr) {
    return '--:--:--';
  }

  try {
    const date = new Date(timeStr);
    return date.toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false
    });
  } catch (e) {
    return timeStr;
  }
}

/**
 * 获取股票代码显示名称
 * @param {string} symbol - 股票代码
 */
function getSymbolDisplay(symbol) {
  if (!symbol) {
    return '--';
  }

  // 移除后缀显示
  const match = symbol.match(/(\d+)(\.[A-Z]+)?/);
  if (match) {
    return match[1];
  }
  return symbol;
}

/**
 * 判断涨跌
 * @param {number} value - 数值
 */
function isUp(value) {
  return value > 0;
}

/**
 * 判断跌
 * @param {number} value - 数值
 */
function isDown(value) {
  return value < 0;
}

/**
 * 判断平
 * @param {number} value - 数值
 */
function isFlat(value) {
  return value === 0 || value === 0.0;
}

/**
 * 获取涨跌颜色类名
 * @param {number} value - 数值
 */
function getColorClass(value) {
  if (isUp(value)) return 'text-up';
  if (isDown(value)) return 'text-down';
  return '';
}

/**
 * 解析Markdown为HTML（增强实现，支持投研报告）
 * @param {string} markdown - Markdown文本
 */
function parseMarkdown(markdown) {
  if (!markdown) {
    return '';
  }

  let html = markdown
    // 标题（从大到小匹配，避免冲突）
    .replace(/^#### (.*$)/gim, '<h4>$1</h4>')
    .replace(/^### (.*$)/gim, '<h3>$1</h3>')
    .replace(/^## (.*$)/gim, '<h2>$1</h2>')
    .replace(/^# (.*$)/gim, '<h1>$1</h1>')
    // 加粗
    .replace(/\*\*(.*?)\*\*/gim, '<strong>$1</strong>')
    // 斜体
    .replace(/\*(.*?)\*/gim, '<em>$1</em>')
    // 代码
    .replace(/`(.*?)`/gim, '<code>$1</code>')
    // 链接
    .replace(/\[(.*?)\]\((.*?)\)/gim, '<a href="$2" target="_blank">$1</a>')
    // 无序列表
    .replace(/^\- (.*$)/gim, '<li>$1</li>')
    // 分隔线
    .replace(/^---$/gim, '<hr>')
    // 段落（双换行）
    .replace(/\n\n/gim, '</p><p>')
    // 单换行
    .replace(/\n/gim, '<br>');

  // 包裹段落
  html = '<p>' + html + '</p>';

  // 修复列表标签
  html = html.replace(/<\/p><li>/gim, '<li>').replace(/<\/li><br><p>/gim, '</li>');

  return html;
}

// 导出为全局对象
window.formatPrice = formatPrice;
window.formatVolume = formatVolume;
window.formatAmount = formatAmount;
window.formatTime = formatTime;
window.formatChange = formatChange;
window.formatDate = formatDate;
window.getSymbolDisplay = getSymbolDisplay;
window.isUp = isUp;
window.isDown = isDown;
window.isFlat = isFlat;
window.getColorClass = getColorClass;
window.parseMarkdown = parseMarkdown;

console.log('[Formatter] Formatter utils loaded');
