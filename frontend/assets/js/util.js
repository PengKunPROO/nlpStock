// Formatting helpers + tiny DOM utils + toast
export function fmtPrice(v, dash = '—') {
  if (v === null || v === undefined || Number.isNaN(v)) return dash;
  return Number(v).toFixed(2);
}
export function fmtPct(v, sign = true, dash = '—') {
  if (v === null || v === undefined || Number.isNaN(v)) return dash;
  const s = sign && v > 0 ? '+' : '';
  return `${s}${Number(v).toFixed(2)}%`;
}
export function pctClass(v) {
  if (v === null || v === undefined) return 'muted';
  return v > 0 ? 'up' : v < 0 ? 'down' : 'muted';
}
export function fmtVolume(shares, dash = '—') {
  if (shares === null || shares === undefined || Number.isNaN(shares)) return dash;
  const hands = shares / 100;
  if (hands >= 1e8) return `${(hands / 1e8).toFixed(2)}亿手`;
  if (hands >= 1e4) return `${(hands / 1e4).toFixed(2)}万手`;
  return `${hands.toFixed(0)}手`;
}
export function fmtTurnover(yuan, dash = '—') {
  if (yuan === null || yuan === undefined || Number.isNaN(yuan)) return dash;
  if (yuan >= 1e8) return `${(yuan / 1e8).toFixed(2)}亿`;
  if (yuan >= 1e4) return `${(yuan / 1e4).toFixed(2)}万`;
  return yuan.toFixed(0);
}
export function fmtMoney(v, dash = '—') {
  if (v === null || v === undefined || Number.isNaN(v)) return dash;
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(2)}亿`;
  if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(2)}万`;
  return Number(v).toFixed(0);
}
export const VOL_STATE = {
  surge: { label: '显著放量', cls: 'chip up' },
  incremental: { label: '增量放量', cls: 'chip up' },
  flat: { label: '量能平稳', cls: 'chip' },
  shrink: { label: '缩量', cls: 'chip down' },
};
export const EXIT_LABEL = {
  signal: '信号离场', stop_loss: '止损', max_hold: '超时平仓', take_profit: '止盈', end_of_data: '期末估值',
};

// 内部字段 → 中文（持仓状态字段 + 基础价格字段），供条件表达式/依据中文化
export const FIELD_LABELS = {
  pnl_pct: '浮盈亏%', hold_days: '持仓天数', dd_from_peak: '距高点回撤%', cost: '成本价',
  open: '开盘价', high: '最高价', low: '最低价', close: '收盘价', volume: '成交量',
};

// 泛化：把条件表达式（如 "pnl_pct <= -5.0"）里的内部字段名翻译成中文
// 覆盖所有内部字段 + lag/right_lag 时序标记，指标 id（ma5/dif 等）保持原样（相对可读）
export function localizeExpr(expr) {
  if (!expr) return expr;
  let s = String(expr);
  // 字段名 → 中文（按 key 长度降序，避免短名误匹配长名子串）
  for (const k of Object.keys(FIELD_LABELS).sort((a, b) => b.length - a.length)) {
    s = s.replace(new RegExp(`\\b${k}\\b`, 'g'), FIELD_LABELS[k]);
  }
  // 时序标记
  s = s.replace(/right_lag=(\d+)/g, '右移$1日');
  s = s.replace(/lag=(\d+)/g, '前移$1日');
  return s;
}
export function h(html) {
  const t = document.createElement('template');
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}
export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
// 触觉反馈（Android WebView navigator.vibrate；不支持时静默忽略）
export function haptic(pattern = 12) {
  try { if (navigator.vibrate) navigator.vibrate(pattern); } catch { /* 忽略 */ }
}
export function toast(msg, isErr = false) {
  haptic(isErr ? [30, 40, 30] : 12); // 错误双重短震，成功轻震
  const el = h(`<div class="toast${isErr ? ' err' : ''}">${esc(msg)}</div>`);
  document.getElementById('toast-root').appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .25s'; setTimeout(() => el.remove(), 260); }, isErr ? 3500 : 2200);
}
export function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}
export function dateToday() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
export function daysAgo(n) {
  const d = new Date(Date.now() - n * 86400000);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

// 通用分页：传入容器+数组+渲染函数，自动渲染页码控件
export function paginate(container, items, renderPage, pageSize = 20) {
  if (!items || items.length === 0) return;
  const totalPages = Math.ceil(items.length / pageSize);
  let current = 1;
  const render = () => {
    container.innerHTML = '';
    const start = (current - 1) * pageSize;
    const pageItems = items.slice(start, start + pageSize);
    renderPage(container, pageItems, current, totalPages);
    if (totalPages > 1) {
      const nav = document.createElement('div');
      nav.className = 'pager';
      const btn = (label, page, disabled) => {
        const b = document.createElement('button');
        b.textContent = label;
        b.className = 'page-btn' + (disabled ? ' disabled' : '');
        b.disabled = disabled;
        b.onclick = () => { if (!disabled) { current = page; render(); container.scrollIntoView({ block: 'start' }); } };
        return b;
      };
      nav.appendChild(btn('‹', current - 1, current === 1));
      const maxShow = 7;
      let s = Math.max(1, current - Math.floor(maxShow / 2));
      let e = Math.min(totalPages, s + maxShow - 1);
      s = Math.max(1, e - maxShow + 1);
      if (s > 1) { nav.appendChild(btn('1', 1, false)); if (s > 2) nav.appendChild(btn('…', 0, true)); }
      for (let p = s; p <= e; p++) nav.appendChild(btn(String(p), p, p === current));
      if (e < totalPages) { if (e < totalPages - 1) nav.appendChild(btn('…', 0, true)); nav.appendChild(btn(String(totalPages), totalPages, false)); }
      nav.appendChild(btn('›', current + 1, current === totalPages));
      const info = document.createElement('span');
      info.className = 'page-info';
      info.textContent = `${items.length} 条 · 第 ${current}/${totalPages} 页`;
      nav.appendChild(info);
      container.appendChild(nav);
    }
  };
  render();
}
