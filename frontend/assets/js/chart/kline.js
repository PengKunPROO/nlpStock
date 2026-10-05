// Candlestick + volume canvas renderer with tap-to-inspect (A-share colors: 红涨绿跌)
const MA_STYLE = {
  ma5: '#FF9500', ma10: '#007AFF', ma20: '#AF52DE', ma60: '#8E8E93',
};
const UP = '#FA5151';
const DOWN = '#07C160';

const MARK_SIZE = 8; // 买卖点三角标尺寸（px）
const MARK_OFF = 12; // 距价格点的垂直偏移（buy 在下方 / sell 在上方）
const MARK_HIT = 14; // 命中判定半径（px）

export function renderKline(canvas, bars, opts = {}) {
  const ctx = canvas.getContext('2d');
  const n = bars.length;
  if (!n) return () => {};
  let highlightIdx = opts._highlight;
  // 买卖点标注：{ idx, type:'buy'|'sell', price, detail }，仅保留窗口内的
  const markers = (opts.markers || []).filter((m) => m && Number.isInteger(m.idx) && m.idx >= 0 && m.idx < n);
  const markerPts = []; // 每次 draw 重算的屏幕坐标，供命中检测用

  const shownMas = Object.keys(MA_STYLE).filter((k) => opts[k] !== false);
  let hi = -Infinity, lo = Infinity;
  for (const b of bars) {
    if (b.high > hi) hi = b.high;
    if (b.low < lo) lo = b.low;
    for (const k of shownMas) if (b[k] !== null && b[k] !== undefined) { hi = Math.max(hi, b[k]); lo = Math.min(lo, b[k]); }
  }
  if (!isFinite(hi) || !isFinite(lo)) return () => {};
  const range = (hi - lo) || hi * 0.01 || 1;
  hi += range * 0.05; lo -= range * 0.05;

  let volMax = 0;
  for (const b of bars) volMax = Math.max(volMax, b.volume || 0);

  // layout geometry（每次 draw 时按最新尺寸重算，旋转/缩放后仍准确）
  const padL = 6, padR = 52, padT = 8, padB = 18, gap = 10;
  let plotW, step, barW, mainH, volH, bodyBottom;

  const draw = () => {
    const dpr = window.devicePixelRatio || 1;
    const cssW = canvas.clientWidth || canvas.parentElement.clientWidth;
    const cssH = canvas.clientHeight || 440;
    canvas.width = cssW * dpr;
    canvas.height = cssH * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    plotW = cssW - padL - padR;
    step = plotW / n;
    barW = Math.max(1.5, Math.min(13, step * 0.68));
    volH = Math.round((cssH - padT - padB) * 0.24);
    mainH = cssH - padT - padB - volH - gap;
    bodyBottom = padT + mainH + gap + volH;

    const styles = getComputedStyle(document.documentElement);
    const cSep = styles.getPropertyValue('--sep').trim() || 'rgba(120,120,128,0.2)';
    const cText3 = styles.getPropertyValue('--text-3').trim() || '#8E8E93';

    const yMain = (v) => padT + (1 - (v - lo) / (hi - lo)) * mainH;
    const yVol = (v) => (volMax ? padT + mainH + gap + (1 - v / volMax) * volH : padT + mainH + gap + volH);

    // grid + price labels
    ctx.strokeStyle = cSep; ctx.fillStyle = cText3;
    ctx.lineWidth = 0.5; ctx.font = '10px -apple-system, sans-serif'; ctx.textAlign = 'left';
    for (let i = 0; i <= 4; i++) {
      const v = lo + ((hi - lo) * i) / 4;
      const y = yMain(v);
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(padL + plotW, y); ctx.stroke();
      ctx.fillText(fmt(v), padL + plotW + 4, y + 3);
    }
    // date labels
    ctx.textAlign = 'center';
    const tickCount = Math.min(5, n);
    for (let i = 0; i < tickCount; i++) {
      const idx = Math.round((i * (n - 1)) / (tickCount - 1 || 1));
      ctx.fillText((bars[idx].date || '').slice(5), padL + idx * step + step / 2, cssH - 4);
    }

    // volume bars
    for (let i = 0; i < n; i++) {
      const b = bars[i];
      const up = b.close >= b.open;
      ctx.fillStyle = up ? UP : DOWN;
      const y = yVol(b.volume || 0);
      ctx.fillRect(padL + i * step + (step - barW) / 2, y, barW, Math.max(1, padT + mainH + gap + volH - y));
    }

    // candles
    for (let i = 0; i < n; i++) {
      const b = bars[i];
      const up = b.close >= b.open;
      const color = up ? UP : DOWN;
      const x = padL + i * step + step / 2;
      ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, yMain(b.high)); ctx.lineTo(x, yMain(b.low)); ctx.stroke();
      const yo = yMain(b.open), yc = yMain(b.close);
      const top = Math.min(yo, yc), hgt = Math.max(1, Math.abs(yc - yo));
      if (up) { ctx.strokeRect(x - barW / 2, top, barW, hgt); } // 阳线空心
      else { ctx.fillRect(x - barW / 2, top, barW, hgt); }
    }

    // MA lines
    ctx.lineWidth = 1.2;
    for (const k of shownMas) {
      ctx.strokeStyle = MA_STYLE[k];
      ctx.beginPath();
      let started = false;
      for (let i = 0; i < n; i++) {
        const v = bars[i][k];
        if (v === null || v === undefined) { started = false; continue; }
        const x = padL + i * step + step / 2;
        const y = yMain(v);
        if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
      }
      ctx.stroke();
    }

    // trade markers（买卖点三角标：buy=下方红▲ / sell=上方绿▼ / signal=信号点圆圈）
    markerPts.length = 0;
    for (const m of markers) {
      const x = padL + m.idx * step + step / 2;
      const py = yMain(m.price ?? bars[m.idx]?.close);
      let y = py + (m.type === 'buy' ? MARK_OFF : -MARK_OFF);
      if (m.type === 'signal') y = py + MARK_OFF;  // 信号点：价格下方圆圈
      // 价格超出可视区间时贴边显示，保证标注始终可见（真实成交价恒在图内，不会触发）
      if (y < padT + 2) y = padT + 2;
      else if (y > bodyBottom - 2) y = bodyBottom - 2;
      if (m.type === 'signal') {
        ctx.fillStyle = '#FF9500';  // 橙色圆圈，区别于红▲绿▼
        ctx.beginPath();
        ctx.arc(x, y, 4, 0, Math.PI * 2);
        ctx.fill();
      } else {
        ctx.fillStyle = m.type === 'buy' ? UP : DOWN;
        ctx.beginPath();
        if (m.type === 'buy') {
          ctx.moveTo(x, y - MARK_SIZE / 2);
          ctx.lineTo(x - MARK_SIZE / 2, y + MARK_SIZE / 2);
          ctx.lineTo(x + MARK_SIZE / 2, y + MARK_SIZE / 2);
        } else {
          ctx.moveTo(x, y + MARK_SIZE / 2);
          ctx.lineTo(x - MARK_SIZE / 2, y - MARK_SIZE / 2);
          ctx.lineTo(x + MARK_SIZE / 2, y - MARK_SIZE / 2);
        }
        ctx.closePath();
        ctx.fill();
      }
      markerPts.push({ m, x, y });
    }

    // last price tag
    const last = bars[n - 1];
    const prev = bars[n - 2] || last;
    const lastUp = last.close >= prev.close;
    const yLast = yMain(last.close);
    ctx.fillStyle = lastUp ? UP : DOWN;
    roundRect(ctx, padL + plotW + 1, yLast - 8, padR - 3, 16, 3);
    ctx.fill();
    ctx.fillStyle = '#fff'; ctx.textAlign = 'center'; ctx.font = '600 10px -apple-system, sans-serif';
    ctx.fillText(fmt(last.close), padL + plotW + 1 + (padR - 3) / 2, yLast + 3.5);

    // highlight crosshair
    if (highlightIdx !== undefined) {
      const x = padL + highlightIdx * step + step / 2;
      ctx.strokeStyle = cText3; ctx.globalAlpha = 0.5; ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(x, padT); ctx.lineTo(x, padT + mainH + gap + volH); ctx.stroke();
      ctx.setLineDash([]); ctx.globalAlpha = 1;
    }
  };

  draw();

  // pointer interaction: 仅 K 线图身（主图+成交量，Y 在 padT~bodyBottom 之间）内的点击命中
  const toIdx = (evX, evY) => {
    const rect = canvas.getBoundingClientRect();
    const x = evX - rect.left - padL;
    const y = evY - rect.top;
    if (x < 0 || x > plotW || y < padT || y > bodyBottom) return null;
    return Math.max(0, Math.min(n - 1, Math.floor(x / step)));
  };
  const handler = (ev) => {
    const rect = canvas.getBoundingClientRect();
    const pos = { x: ev.clientX - rect.left, y: ev.clientY - rect.top };
    // 买卖点 marker 命中优先（±14px，取最近者），否则走 onBarTap
    let hitM = null;
    for (const p of markerPts) {
      if (Math.abs(pos.x - p.x) <= MARK_HIT && Math.abs(pos.y - p.y) <= MARK_HIT) {
        if (!hitM || Math.abs(pos.x - p.x) + Math.abs(pos.y - p.y) < Math.abs(pos.x - hitM.x) + Math.abs(pos.y - hitM.y)) hitM = p;
      }
    }
    if (hitM) {
      const keep = opts.onMarkerTap ? opts.onMarkerTap(hitM.m, pos, rect.width) : true;
      highlightIdx = keep === false ? undefined : hitM.m.idx;
      draw();
      return;
    }
    const idx = toIdx(ev.clientX, ev.clientY);
    if (idx === null) {
      // 空白区域：清除高亮虚线，并通知外层关闭浮层
      if (highlightIdx !== undefined) { highlightIdx = undefined; draw(); }
      if (opts.onBlankTap) opts.onBlankTap(pos, rect.width);
      return;
    }
    // onBarTap 返回 false 表示“同根 K 线再次点击 = 关闭浮层”，此时不绘制高亮虚线
    const keep = opts.onBarTap ? opts.onBarTap(bars[idx], idx, pos, rect.width) : true;
    highlightIdx = keep === false ? undefined : idx;
    draw();
  };
  canvas.addEventListener('pointerdown', handler);
  return () => canvas.removeEventListener('pointerdown', handler);
}

function fmt(v) {
  if (v >= 1000) return v.toFixed(0);
  if (v >= 10) return v.toFixed(1);
  return v.toFixed(2);
}

function roundRect(ctx, x, y, w, hh, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + hh, r);
  ctx.arcTo(x + w, y + hh, x, y + hh, r);
  ctx.arcTo(x, y + hh, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

export const MA_LEGEND = [
  { key: 'ma5', label: 'MA5', color: MA_STYLE.ma5 },
  { key: 'ma10', label: 'MA10', color: MA_STYLE.ma10 },
  { key: 'ma20', label: 'MA20', color: MA_STYLE.ma20 },
  { key: 'ma60', label: 'MA60', color: MA_STYLE.ma60 },
];
