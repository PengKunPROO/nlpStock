// Equity curve + drawdown canvas renderer with crosshair
export function renderEquity(canvas, points, opts = {}) {
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || canvas.parentElement.clientWidth;
  const cssH = canvas.clientHeight || 260;
  canvas.width = cssW * dpr;
  canvas.height = cssH * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  const styles = getComputedStyle(document.documentElement);
  const cSep = styles.getPropertyValue('--sep').trim() || 'rgba(120,120,128,0.2)';
  const cText3 = styles.getPropertyValue('--text-3').trim() || '#8E8E93';
  const accent = styles.getPropertyValue('--accent').trim() || '#007AFF';
  const danger = styles.getPropertyValue('--up').trim() || '#FA5151';

  const padL = 6, padR = 58, padT = 10, padB = 18;
  const ddH = Math.round((cssH - padT - padB) * 0.22);
  const gap = 8;
  const mainH = cssH - padT - padB - ddH - gap;
  const plotW = cssW - padL - padR;
  const n = points.length;
  if (!n) return () => {};

  let hi = -Infinity, lo = Infinity;
  for (const p of points) { hi = Math.max(hi, p.value); lo = Math.min(lo, p.value); }
  if (hi === lo) { hi *= 1.01; lo *= 0.99; }
  const pad = (hi - lo) * 0.06;
  hi += pad; lo -= pad;
  const X = (i) => padL + (i / (n - 1 || 1)) * plotW;
  const Y = (v) => padT + (1 - (v - lo) / (hi - lo)) * mainH;

  ctx.strokeStyle = cSep; ctx.fillStyle = cText3; ctx.lineWidth = 0.5;
  ctx.font = '10px -apple-system, sans-serif'; ctx.textAlign = 'left';
  for (let i = 0; i <= 3; i++) {
    const v = lo + ((hi - lo) * i) / 3;
    const y = Y(v);
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(padL + plotW, y); ctx.stroke();
    ctx.fillText(shortNum(v), padL + plotW + 4, y + 3);
  }
  ctx.textAlign = 'center';
  const ticks = Math.min(4, n);
  for (let i = 0; i < ticks; i++) {
    const idx = Math.round((i * (n - 1)) / (ticks - 1 || 1));
    ctx.fillText((points[idx].date || '').slice(2), X(idx), cssH - 4);
  }

  // drawdown area
  ctx.fillStyle = danger;
  ctx.globalAlpha = 0.18;
  ctx.beginPath();
  ctx.moveTo(X(0), padT + mainH + gap + ddH);
  for (let i = 0; i < n; i++) {
    const dd = Math.min(0, points[i].drawdown_pct || 0);
    const y = padT + mainH + gap + (1 - dd / -30) * ddH; // -30% 满幅
    ctx.lineTo(X(i), Math.min(padT + mainH + gap + ddH, y));
  }
  ctx.lineTo(X(n - 1), padT + mainH + gap + ddH);
  ctx.closePath(); ctx.fill();
  ctx.globalAlpha = 1;

  // equity line + area
  const grad = ctx.createLinearGradient(0, padT, 0, padT + mainH);
  grad.addColorStop(0, accent + '33');
  grad.addColorStop(1, accent + '00');
  ctx.beginPath();
  ctx.moveTo(X(0), Y(points[0].value));
  for (let i = 1; i < n; i++) ctx.lineTo(X(i), Y(points[i].value));
  ctx.lineTo(X(n - 1), padT + mainH);
  ctx.lineTo(X(0), padT + mainH);
  ctx.closePath();
  ctx.fillStyle = grad; ctx.fill();
  ctx.beginPath();
  ctx.moveTo(X(0), Y(points[0].value));
  for (let i = 1; i < n; i++) ctx.lineTo(X(i), Y(points[i].value));
  ctx.strokeStyle = accent; ctx.lineWidth = 1.8; ctx.stroke();

  // crosshair
  const handler = (ev) => {
    const rect = canvas.getBoundingClientRect();
    const x = ev.clientX - rect.left - padL;
    const idx = Math.max(0, Math.min(n - 1, Math.round((x / plotW) * (n - 1 || 1))));
    renderEquity(canvas, points, { ...opts, _hl: idx });
    if (opts.onPoint) opts.onPoint(points[idx], idx, { x: ev.clientX - rect.left, y: ev.clientY - rect.top }, rect.width);
  };
  canvas.addEventListener('pointerdown', handler);
  if (opts._hl !== undefined) {
    const i = opts._hl;
    ctx.strokeStyle = cText3; ctx.globalAlpha = 0.5; ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(X(i), padT); ctx.lineTo(X(i), padT + mainH); ctx.stroke();
    ctx.setLineDash([]); ctx.globalAlpha = 1;
    ctx.fillStyle = accent;
    ctx.beginPath(); ctx.arc(X(i), Y(points[i].value), 3, 0, Math.PI * 2); ctx.fill();
  }
  return () => canvas.removeEventListener('pointerdown', handler);
}

function shortNum(v) {
  if (Math.abs(v) >= 1e8) return (v / 1e8).toFixed(1) + '亿';
  if (Math.abs(v) >= 1e4) return (v / 1e4).toFixed(0) + '万';
  return v.toFixed(0);
}
