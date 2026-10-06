// Candlestick + volume canvas renderer with drag-pan + crosshair (A-share colors: 红涨绿跌)
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
  const total = bars.length;
  if (!total) return () => {};
  // 可见视窗：visibleCount 固定（K 线宽度不变），offset 平移（0=最早，total-visibleCount=最新）
  const visibleCount = Math.max(1, Math.min(opts.visibleCount || total, total));
  let offset = opts.offset != null
    ? Math.max(0, Math.min(opts.offset, total - visibleCount))
    : Math.max(0, total - visibleCount);
  let highlightIdx = opts._highlight;
  // 买卖点标注：{ idx, type:'buy'|'sell'|'signal', price, detail }，idx 为全局索引
  const markers = (opts.markers || []).filter((m) => m && Number.isInteger(m.idx) && m.idx >= 0 && m.idx < total);
  const markerPts = []; // 每次 draw 重算的屏幕坐标，供命中检测用

  const shownMas = Object.keys(MA_STYLE).filter((k) => opts[k] !== false);
  // 十字光标（拖动跟随）：{ idx(可见), y }
  let crosshair = null;

  const padL = 6, padR = 52, padT = 8, padB = 18, gap = 10;
  // 布局几何（draw 时更新，交互读取）
  let step = 1, plotW = 0, bodyBottom = 0;

  const draw = () => {
    const visible = bars.slice(offset, offset + visibleCount);
    const n = visible.length;
    if (!n) return;

    // 价格范围基于可见子集（平移时随视窗滚动）
    let hi = -Infinity, lo = Infinity;
    for (const b of visible) {
      if (b.high > hi) hi = b.high;
      if (b.low < lo) lo = b.low;
      for (const k of shownMas) if (b[k] !== null && b[k] !== undefined) { hi = Math.max(hi, b[k]); lo = Math.min(lo, b[k]); }
    }
    if (!isFinite(hi) || !isFinite(lo)) return;
    const range = (hi - lo) || hi * 0.01 || 1;
    hi += range * 0.05; lo -= range * 0.05;

    let volMax = 0;
    for (const b of visible) volMax = Math.max(volMax, b.volume || 0);

    const dpr = window.devicePixelRatio || 1;
    const cssW = canvas.clientWidth || canvas.parentElement.clientWidth;
    const cssH = canvas.clientHeight || 440;
    canvas.width = cssW * dpr;
    canvas.height = cssH * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    plotW = cssW - padL - padR;
    step = plotW / visibleCount;
    const barW = Math.max(1.5, Math.min(13, step * 0.68));
    const volH = Math.round((cssH - padT - padB) * 0.24);
    const mainH = cssH - padT - padB - volH - gap;
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
      ctx.fillText((visible[idx].date || '').slice(5), padL + idx * step + step / 2, cssH - 4);
    }

    // volume bars
    for (let i = 0; i < n; i++) {
      const b = visible[i];
      const up = b.close >= b.open;
      ctx.fillStyle = up ? UP : DOWN;
      const y = yVol(b.volume || 0);
      ctx.fillRect(padL + i * step + (step - barW) / 2, y, barW, Math.max(1, padT + mainH + gap + volH - y));
    }

    // candles
    for (let i = 0; i < n; i++) {
      const b = visible[i];
      const up = b.close >= b.open;
      const color = up ? UP : DOWN;
      const x = padL + i * step + step / 2;
      ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, yMain(b.high)); ctx.lineTo(x, yMain(b.low)); ctx.stroke();
      const yo = yMain(b.open), yc = yMain(b.close);
      const top = Math.min(yo, yc), hgt = Math.max(1, Math.abs(yc - yo));
      if (up) { ctx.strokeRect(x - barW / 2, top, barW, hgt); }
      else { ctx.fillRect(x - barW / 2, top, barW, hgt); }
    }

    // MA lines
    ctx.lineWidth = 1.2;
    for (const k of shownMas) {
      ctx.strokeStyle = MA_STYLE[k];
      ctx.beginPath();
      let started = false;
      for (let i = 0; i < n; i++) {
        const v = visible[i][k];
        if (v === null || v === undefined) { started = false; continue; }
        const x = padL + i * step + step / 2;
        const y = yMain(v);
        if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
      }
      ctx.stroke();
    }

    // trade markers（全局 idx → 可见 idx）
    markerPts.length = 0;
    for (const m of markers) {
      const vidx = m.idx - offset;
      if (vidx < 0 || vidx >= visibleCount) continue;
      const x = padL + vidx * step + step / 2;
      const py = yMain(m.price ?? visible[vidx]?.close);
      let y = py + (m.type === 'buy' ? MARK_OFF : -MARK_OFF);
      if (m.type === 'signal') y = py + MARK_OFF;
      if (y < padT + 2) y = padT + 2;
      else if (y > bodyBottom - 2) y = bodyBottom - 2;
      if (m.type === 'signal') {
        ctx.fillStyle = '#FF9500';
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
    const last = visible[n - 1];
    const prev = visible[n - 2] || last;
    const lastUp = last.close >= prev.close;
    const yLast = yMain(last.close);
    ctx.fillStyle = lastUp ? UP : DOWN;
    roundRect(ctx, padL + plotW + 1, yLast - 8, padR - 3, 16, 3);
    ctx.fill();
    ctx.fillStyle = '#fff'; ctx.textAlign = 'center'; ctx.font = '600 10px -apple-system, sans-serif';
    ctx.fillText(fmt(last.close), padL + plotW + 1 + (padR - 3) / 2, yLast + 3.5);

    // 十字光标（拖动跟随）：垂直线 + 水平线 + 顶部日期标签
    if (crosshair && crosshair.idx >= 0 && crosshair.idx < n) {
      const x = padL + crosshair.idx * step + step / 2;
      const b = visible[crosshair.idx];
      const y = yMain(b.close);
      ctx.strokeStyle = cText3; ctx.globalAlpha = 0.6; ctx.setLineDash([3, 3]); ctx.lineWidth = 0.5;
      ctx.beginPath(); ctx.moveTo(x, padT); ctx.lineTo(x, padT + mainH + gap + volH); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(padL + plotW, y); ctx.stroke();
      ctx.setLineDash([]); ctx.globalAlpha = 1;
      ctx.fillStyle = cText3; ctx.textAlign = 'center'; ctx.font = '600 11px -apple-system, sans-serif';
      const label = (b.date || '').slice(5);
      const lw = ctx.measureText(label).width + 12;
      const lx = Math.min(Math.max(padL, x - lw / 2), padL + plotW - lw);
      roundRect(ctx, lx, padT + 1, lw, 16, 3);
      ctx.fillStyle = cText3; ctx.fill();
      ctx.fillStyle = '#fff'; ctx.fillText(label, lx + lw / 2, padT + 13);
    } else if (highlightIdx !== undefined) {
      // 点击高亮虚线
      const x = padL + highlightIdx * step + step / 2;
      ctx.strokeStyle = cText3; ctx.globalAlpha = 0.5; ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(x, padT); ctx.lineTo(x, padT + mainH + gap + volH); ctx.stroke();
      ctx.setLineDash([]); ctx.globalAlpha = 1;
    }
  };

  draw();

  // pointer interaction：拖动平移 + 十字光标；点击（位移小）命中 marker/bar
  const toIdx = (evX, evY) => {
    const rect = canvas.getBoundingClientRect();
    const x = evX - rect.left - padL;
    const y = evY - rect.top;
    if (x < 0 || x > plotW || y < padT || y > bodyBottom) return null;
    return Math.max(0, Math.min(visibleCount - 1, Math.floor(x / step)));
  };
  const barAtGlobalIdx = (vidx) => (vidx === null || vidx < 0 || offset + vidx >= total ? null : bars[offset + vidx]);

  let dragStartX = null, dragStartOffset = 0, moved = false;
  const onPointerDown = (ev) => {
    const rect = canvas.getBoundingClientRect();
    const pos = { x: ev.clientX - rect.left, y: ev.clientY - rect.top };
    // 买卖点 marker 命中优先（点击查看详情）
    let hitM = null;
    for (const p of markerPts) {
      if (Math.abs(pos.x - p.x) <= MARK_HIT && Math.abs(pos.y - p.y) <= MARK_HIT) {
        if (!hitM || Math.abs(pos.x - p.x) + Math.abs(pos.y - p.y) < Math.abs(pos.x - hitM.x) + Math.abs(pos.y - hitM.y)) hitM = p;
      }
    }
    if (hitM) {
      const keep = opts.onMarkerTap ? opts.onMarkerTap(hitM.m, pos, rect.width) : true;
      highlightIdx = keep === false ? undefined : hitM.m.idx - offset;
      draw();
      return;
    }
    // 开始拖动 + 十字光标
    dragStartX = ev.clientX;
    dragStartOffset = offset;
    moved = false;
    const vidx = toIdx(ev.clientX, ev.clientY);
    crosshair = { idx: vidx === null ? -1 : vidx };
    if (opts.onCrosshair) opts.onCrosshair(barAtGlobalIdx(vidx), pos, rect.width);
    canvas.setPointerCapture(ev.pointerId);
    draw();
  };
  const onPointerMove = (ev) => {
    if (dragStartX === null) return;
    const rect = canvas.getBoundingClientRect();
    const pos = { x: ev.clientX - rect.left, y: ev.clientY - rect.top };
    const dx = ev.clientX - dragStartX;
    if (Math.abs(dx) > 6) moved = true;
    const delta = Math.round(dx / step);
    if (delta !== 0) {
      const newOffset = Math.max(0, Math.min(total - visibleCount, dragStartOffset - delta));
      if (newOffset !== offset) {
        offset = newOffset;
        if (opts.onOffsetChange) opts.onOffsetChange(offset);
      }
    }
    const vidx = toIdx(ev.clientX, ev.clientY);
    crosshair = { idx: vidx === null ? -1 : vidx };
    if (opts.onCrosshair) opts.onCrosshair(barAtGlobalIdx(vidx), pos, rect.width);
    draw();
  };
  const onPointerUp = (ev) => {
    if (dragStartX === null) return;
    const rect = canvas.getBoundingClientRect();
    const pos = { x: ev.clientX - rect.left, y: ev.clientY - rect.top };
    dragStartX = null;
    if (!moved) {
      // 位移小 → 视为点击
      const vidx = toIdx(ev.clientX, ev.clientY);
      crosshair = null;
      if (opts.onCrosshair) opts.onCrosshair(null, pos, rect.width);
      if (vidx === null) {
        if (highlightIdx !== undefined) { highlightIdx = undefined; draw(); }
        if (opts.onBlankTap) opts.onBlankTap(pos, rect.width);
        return;
      }
      const keep = opts.onBarTap ? opts.onBarTap(bars[offset + vidx], offset + vidx, pos, rect.width) : true;
      highlightIdx = keep === false ? undefined : vidx;
      draw();
      return;
    }
    // 拖动结束：隐藏十字光标
    crosshair = null;
    if (opts.onCrosshair) opts.onCrosshair(null, pos, rect.width);
    draw();
  };
  canvas.addEventListener('pointerdown', onPointerDown);
  canvas.addEventListener('pointermove', onPointerMove);
  canvas.addEventListener('pointerup', onPointerUp);
  canvas.addEventListener('pointercancel', onPointerUp);
  return () => {
    canvas.removeEventListener('pointerdown', onPointerDown);
    canvas.removeEventListener('pointermove', onPointerMove);
    canvas.removeEventListener('pointerup', onPointerUp);
    canvas.removeEventListener('pointercancel', onPointerUp);
  };
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
