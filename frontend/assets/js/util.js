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
export function h(html) {
  const t = document.createElement('template');
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}
export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
export function toast(msg, isErr = false) {
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
