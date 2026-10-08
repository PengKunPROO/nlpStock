// 收益率曲线 + 回撤 canvas 渲染，交互对齐 K线：拖动跟随 + 点击切换 + 空白关闭
export function renderEquity(canvas, points, opts = {}) {
  const ctx = canvas.getContext('2d');
  const n = points.length;
  if (!n) return () => {};
  let highlightIdx = opts._hl;  // 点击高亮（无十字光标时）
  let crosshair = null;        // 拖动十字光标索引

  const padL = 6, padR = 62, padT = 10, padB = 18, gap = 8;

  const draw = () => {
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

    const ddH = Math.round((cssH - padT - padB) * 0.22);
    const mainH = cssH - padT - padB - ddH - gap;
    const plotW = cssW - padL - padR;

    let hi = -Infinity, lo = Infinity;
    for (const p of points) { hi = Math.max(hi, p.value); lo = Math.min(lo, p.value); }
    if (hi === lo) { hi += 1; lo -= 1; }
    const pad = (hi - lo) * 0.08 || 1;
    hi += pad; lo -= pad;
    const X = (i) => padL + (i / (n - 1 || 1)) * plotW;
    const Y = (v) => padT + (1 - (v - lo) / (hi - lo)) * mainH;

    // grid + 收益率标签
    ctx.strokeStyle = cSep; ctx.fillStyle = cText3; ctx.lineWidth = 0.5;
    ctx.font = '10px -apple-system, sans-serif'; ctx.textAlign = 'left';
    for (let i = 0; i <= 3; i++) {
      const v = lo + ((hi - lo) * i) / 3;
      const y = Y(v);
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(padL + plotW, y); ctx.stroke();
      ctx.fillText(fmtRet(v), padL + plotW + 4, y + 3);
    }
    // 日期标签
    ctx.textAlign = 'center';
    const ticks = Math.min(4, n);
    for (let i = 0; i < ticks; i++) {
      const idx = Math.round((i * (n - 1)) / (ticks - 1 || 1));
      ctx.fillText((points[idx].date || '').slice(2), X(idx), cssH - 4);
    }

    // 零轴（0% 基准线）
    if (lo < 0 && hi > 0) {
      ctx.strokeStyle = cText3; ctx.globalAlpha = 0.35; ctx.setLineDash([2, 2]); ctx.lineWidth = 0.5;
      ctx.beginPath(); ctx.moveTo(padL, Y(0)); ctx.lineTo(padL + plotW, Y(0)); ctx.stroke();
      ctx.setLineDash([]); ctx.globalAlpha = 1;
    }

    // drawdown 区域（百分点，负值；-30% 满幅）
    ctx.fillStyle = danger;
    ctx.globalAlpha = 0.18;
    ctx.beginPath();
    ctx.moveTo(X(0), padT + mainH + gap + ddH);
    for (let i = 0; i < n; i++) {
      const dd = Math.min(0, points[i].drawdown_pct || 0);
      const y = padT + mainH + gap + (1 - dd / -30) * ddH;
      ctx.lineTo(X(i), Math.min(padT + mainH + gap + ddH, y));
    }
    ctx.lineTo(X(n - 1), padT + mainH + gap + ddH);
    ctx.closePath(); ctx.fill();
    ctx.globalAlpha = 1;

    // 收益率曲线 + 面积
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

    // 十字光标（拖动）或高亮虚线（点击）
    const hiIdx = crosshair !== null && crosshair >= 0 && crosshair < n ? crosshair
      : (highlightIdx !== undefined && highlightIdx >= 0 && highlightIdx < n ? highlightIdx : null);
    if (hiIdx !== null) {
      const i = hiIdx;
      ctx.strokeStyle = cText3; ctx.globalAlpha = crosshair !== null ? 0.6 : 0.5;
      ctx.setLineDash([3, 3]); ctx.lineWidth = 0.5;
      ctx.beginPath(); ctx.moveTo(X(i), padT); ctx.lineTo(X(i), padT + mainH + gap + ddH); ctx.stroke();
      ctx.setLineDash([]); ctx.globalAlpha = 1;
      ctx.fillStyle = accent;
      ctx.beginPath(); ctx.arc(X(i), Y(points[i].value), 3, 0, Math.PI * 2); ctx.fill();
    }
  };

  draw();

  // ---- 交互：拖动跟随 + 点击切换 + 关闭 ----
  let dragStartX = null, dragStartY = null, moved = false;

  const toIdx = (evX) => {
    const plotW = (canvas.clientWidth || canvas.parentElement.clientWidth) - padL - padR;
    const x = evX - canvas.getBoundingClientRect().left - padL;
    return Math.max(0, Math.min(n - 1, Math.round((x / plotW) * (n - 1 || 1))));
  };
  const emit = (idx, clientX) => {
    if (!opts.onPoint) return;
    const rect = canvas.getBoundingClientRect();
    if (idx === null) opts.onPoint(null, -1, null, rect.width);
    else opts.onPoint(points[idx], idx, { x: clientX - rect.left, y: 0 }, rect.width);
  };

  const onPointerDown = (ev) => {
    dragStartX = ev.clientX;
    dragStartY = ev.clientY;
    moved = false;
    canvas.setPointerCapture(ev.pointerId);
  };
  const onPointerMove = (ev) => {
    if (dragStartX === null) return;
    const dx = ev.clientX - dragStartX;
    const dy = ev.clientY - dragStartY;
    if (!moved && (Math.abs(dx) > 8 || Math.abs(dy) > 8)) moved = true;
    if (!moved) return;
    crosshair = toIdx(ev.clientX);
    highlightIdx = undefined;
    draw();
    emit(crosshair, ev.clientX);
  };
  const onPointerUp = (ev) => {
    if (dragStartX === null) return;
    const wasMoved = moved;
    dragStartX = null;
    if (wasMoved) return;  // 滑动结束：保留十字光标 + 浮层
    // 点击：同一位置再点 → 关闭；否则显示
    const idx = toIdx(ev.clientX);
    if (crosshair === null && highlightIdx !== undefined && highlightIdx === idx) {
      highlightIdx = undefined;
      draw();
      emit(null, ev.clientX);
    } else {
      crosshair = null;
      highlightIdx = idx;
      draw();
      emit(idx, ev.clientX);
    }
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

function fmtRet(v) {
  return (v >= 0 ? '+' : '') + v.toFixed(1) + '%';
}
