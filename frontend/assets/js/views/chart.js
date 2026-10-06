// 图表 view: search + period switch + candlestick + volume analysis + tap tooltip
import { api } from '../api.js';
import state from '../store.js';
import { MA_LEGEND, renderKline } from '../chart/kline.js';
import { VOL_STATE, debounce, esc, fmtPct, fmtPrice, fmtTurnover, fmtVolume, h, pctClass, toast } from '../util.js';

const PERIODS = [
  { v: '5d', label: '5日' }, { v: '1d', label: '日' }, { v: '1w', label: '周' }, { v: '1M', label: '月' }, { v: '1y', label: '年' },
];
const maVisible = { ma5: true, ma10: true, ma20: true, ma60: true };
let cleanupFn = null;
let selectedIdx = null; // 当前浮层对应的 K 线索引（null = 浮层关闭）
const VISIBLE_COUNT = 60; // 可见 K 线根数（固定 → 左右滑动是平移而非缩放）
const TOTAL_LOAD = 500; // 一次加载的 K 线根数（后端上限）
let allBars = []; // 已加载的全部 K 线
let startIdx = 0; // 视窗起点（offset 语义：0=最早，allBars.length-VISIBLE_COUNT=最新）
let allMarkers = []; // 信号点标注（全局索引）

export async function renderChartView(view) {
  const code = state.chartCode || '600519.SH';
  view.innerHTML = `
    <div class="search-box">
      <input id="chart-search" type="text" placeholder="搜索个股 / 指数 / 板块（代码或名称）" autocomplete="off">
      <div id="chart-drop" class="search-drop" style="display:none"></div>
    </div>
    <div class="card" id="chart-head"></div>
    <div class="seg" id="period-seg"></div>
    <div class="card kline-wrap">
      <div id="vol-chip"></div>
      <div id="ma-legend" style="display:flex;gap:10px;margin:6px 0 4px;flex-wrap:wrap"></div>
      <canvas id="kline-canvas" class="kline" style="height:440px"></canvas>
      <div id="kline-tip" class="kline-tip" style="display:none"></div>
    </div>
    <div style="display:flex;justify-content:space-between;align-items:center;margin:6px 4px 0">
      <div style="display:flex;gap:10px;align-items:center">
        <button class="btn sm secondary" id="shift-prev" style="padding:7px 16px">‹ 更早</button>
        <button class="btn sm secondary" id="shift-next" style="padding:7px 16px">更新 ›</button>
      </div>
      <span class="muted" style="font-size:12px">短按翻半屏 · 长按连续翻</span>
    </div>
    <div class="hint">按住 K 线左右滑动，十字光标跟随显示该日开高低收；用「‹更早 / 更新›」按钮翻历史；点「更多」展开量能详情。</div>
  `;
  bindSearch(view);
  renderPeriodSeg();
  renderLegend();
  bindTimeShift();
  await loadChart(code, view.dataset.period || '1d');
}

// 左右按钮翻历史：短按翻半屏，长按（400ms 后）连续翻
function bindTimeShift() {
  const STEP = Math.round(VISIBLE_COUNT / 2); // 短按平移半屏（30 根）
  const shift = (dir) => {
    const next = Math.max(0, Math.min(allBars.length - VISIBLE_COUNT, startIdx + dir * STEP));
    if (next !== startIdx) { startIdx = next; renderVisible(); }
  };
  const bind = (btn, dir) => {
    if (!btn) return;
    let pressTimer = null, holdInterval = null, isLong = false;
    btn.addEventListener('pointerdown', () => {
      isLong = false;
      pressTimer = setTimeout(() => {
        isLong = true;
        shift(dir);
        holdInterval = setInterval(() => shift(dir), 120);
      }, 400);
    });
    const cleanup = () => {
      if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
      if (holdInterval) { clearInterval(holdInterval); holdInterval = null; }
    };
    btn.addEventListener('pointerup', cleanup);
    btn.addEventListener('pointercancel', cleanup);
    btn.addEventListener('pointerleave', cleanup);
    btn.addEventListener('click', () => { if (!isLong) shift(dir); });
  };
  bind(document.getElementById('shift-prev'), -1); // 更早
  bind(document.getElementById('shift-next'), +1); // 更新
}

function renderPeriodSeg() {
  const seg = document.getElementById('period-seg');
  seg.innerHTML = '';
  const cur = document.getElementById('view').dataset.period || '1d';
  for (const p of PERIODS) {
    const b = h(`<button class="${p.v === cur ? 'active' : ''}">${p.label}</button>`);
    b.onclick = () => {
      document.getElementById('view').dataset.period = p.v;
      renderPeriodSeg();
      startIdx = 0; // 切换周期重置窗口
      loadChart(state.chartCode || '600519.SH', p.v);
    };
    seg.appendChild(b);
  }
}

function renderLegend() {
  const el = document.getElementById('ma-legend');
  el.innerHTML = '';
  for (const m of MA_LEGEND) {
    const chip = h(`<span class="chip" style="cursor:pointer;color:${m.color}">${m.label}</span>`);
    chip.onclick = () => {
      maVisible[m.key] = !maVisible[m.key];
      chip.style.opacity = maVisible[m.key] ? '1' : '0.35';
      renderVisible(); // MA 切换只重绘，不重新请求
    };
    if (!maVisible[m.key]) chip.style.opacity = '0.35';
    el.appendChild(chip);
  }
}

function bindSearch(view) {
  const input = document.getElementById('chart-search');
  const drop = document.getElementById('chart-drop');
  const doSearch = debounce(async () => {
    const q = input.value.trim();
    if (!q) { drop.style.display = 'none'; return; }
    try {
      const { items } = await api.search(q);
      drop.innerHTML = '';
      if (!items.length) drop.innerHTML = '<div class="s-item muted">无结果</div>';
      for (const it of items) {
        const typeLabel = it.asset_type === 'a-share' ? '个股' : it.asset_type === 'ths-index' ? '板块' : '指数';
        const row = h(`<div class="s-item"><span><b>${esc(it.name)}</b> <span class="muted" style="font-size:12px">${esc(it.thscode)}</span></span><span class="chip">${typeLabel}</span></div>`);
        row.onclick = () => {
          state.chartCode = it.thscode;
          drop.style.display = 'none';
          input.value = `${it.name} ${it.thscode}`;
          loadChart(it.thscode, document.getElementById('view').dataset.period || '1d');
        };
        drop.appendChild(row);
      }
      drop.style.display = 'block';
    } catch (e) {
      toast(`搜索失败：${e.message}`, true);
    }
  }, 300);
  input.addEventListener('input', doSearch);
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.search-box')) drop.style.display = 'none';
  });
}

async function loadChart
(code, period, silent = false) {
  const head = document.getElementById('chart-head');
  const canvas = document.getElementById('kline-canvas');
  const tip = document.getElementById('kline-tip');
  if (!head || !canvas) return;
  if (!silent) head.innerHTML = '<div class="skeleton" style="height:52px"></div>';
  try {
    const count = TOTAL_LOAD;
    const data = await api.kline(code, period, count);
    state.chartCode = code;
    document.title = `${data.name} · 策略选股`;
    allBars = data.bars;
    startIdx = Math.max(0, allBars.length - VISIBLE_COUNT); // 默认定位到最新
    allMarkers = [];
    if (state.chartSignalDate) {
      const si = allBars.findIndex((b) => b.date === state.chartSignalDate);
      if (si !== -1) allMarkers.push({ idx: si, type: 'signal', price: allBars[si].close, detail: { date: state.chartSignalDate, close: allBars[si].close } });
    }
    const chg = data.change_pct;
    head.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <div>
          <div style="font-size:19px;font-weight:800">${esc(data.name)} <span class="muted" style="font-size:13px;font-weight:400">${esc(data.thscode)}</span></div>
          <div style="font-size:13px" class="muted">${data.asset_type === 'ths-index' ? '板块指数' : data.asset_type === 'a-share' ? 'A股' : '指数'} · ${PERIODS.find((p) => p.v === period).label}K</div>
        </div>
        <div style="text-align:right">
          <div class="mono ${pctClass(chg)}" style="font-size:26px;font-weight:800">${fmtPrice(data.last_close)}</div>
          <div class="mono ${pctClass(chg)}" style="font-size:14px;font-weight:600">${fmtPct(chg)}</div>
        </div>
      </div>`;
    const vs = data.volume_summary || {};
    const trendChip = vs.trend === '增量放量' ? 'chip up' : vs.trend === '持续缩量' ? 'chip down' : 'chip';
    document.getElementById('vol-chip').innerHTML = `
      <span class="${trendChip}" style="font-weight:600">${esc(vs.trend || '量能平稳')}</span>
      <span class="muted" style="font-size:12px;margin-left:8px">${esc(vs.note || '')}</span>`;
    renderVisible();
  } catch (e) {
    head.innerHTML = `<div class="empty" style="padding:18px">加载失败：${esc(e.message || String(e))}
      <div style="margin-top:10px"><button class="btn sm secondary" id="retry-kline">重试</button></div></div>`;
    document.getElementById('retry-kline').onclick = () => loadChart(code, period);
  }
}

// 渲染视窗：固定根数 + 内部 offset，拖动平移（renderKline 内部重绘，不重建实例）
function renderVisible() {
  const canvas = document.getElementById('kline-canvas');
  const tip = document.getElementById('kline-tip');
  if (!canvas || !allBars.length) return;
  if (cleanupFn) cleanupFn();
  hideTip();
  selectedIdx = null;
  cleanupFn = renderKline(canvas, allBars, {
    ...maVisible,
    visibleCount: VISIBLE_COUNT,
    offset: startIdx,
    onOffsetChange: (newOffset) => { startIdx = newOffset; },
    markers: allMarkers,
    onCrosshair: (bar, pos, width) => {
      // 拖动时实时跟随显示对应 K 线信息
      if (bar) showTip(tip, bar, pos, width);
      else hideTip();
    },
    onBarTap: (bar, idx, pos, width) => {
      if (idx === selectedIdx) {
        // 再次点击同一根 K 线：切换关闭浮层
        selectedIdx = null;
        hideTip();
        return false; // 通知 kline 不要绘制高亮虚线
      }
      selectedIdx = idx;
      showTip(tip, bar, pos, width);
    },
    onMarkerTap: (m, pos, width) => {
      // 信号点点击：显示信号日
      const d = m.detail || {};
      tip.innerHTML = `<div style="font-weight:700;margin-bottom:2px"><span class="chip accent" style="font-size:11px">选股信号</span> ${esc(d.date)}</div>
        <div class="t-row"><span>收盘</span><span class="mono">${fmtPrice(d.close ?? m.price)}</span></div>`;
      placeTip(tip, pos, width);
    },
    onBlankTap: () => {
      // 点击 K 线 canvas 的空白区域（图身之外）
      selectedIdx = null;
      hideTip();
    },
  });
}

function placeTip(tip, pos, width) {
  const flip = pos.x > width - 190;
  tip.style.left = flip ? 'auto' : `${Math.max(4, pos.x + 14)}px`;
  tip.style.right = flip ? `${Math.max(4, width - pos.x + 14)}px` : 'auto';
  tip.style.display = 'block';
}

function showTip(tip, bar, pos, width) {
  const vs = VOL_STATE[bar.vol_state] || { label: '—', cls: 'chip' };
  const chg = bar.open ? (bar.close / bar.open - 1) * 100 : null;
  const mas = [['MA5', bar.ma5], ['MA10', bar.ma10], ['MA20', bar.ma20], ['MA60', bar.ma60]]
    .filter(([, v]) => v !== null && v !== undefined)
    .map(([k, v]) => `${k} ${fmtPrice(v)}`)
    .join(' · ');
  tip.innerHTML = `
    <div style="font-weight:700;margin-bottom:2px">${esc(bar.date)}</div>
    <div class="t-row"><span>开</span><span class="mono ${pctClass(bar.close - bar.open)}">${fmtPrice(bar.open)}</span></div>
    <div class="t-row"><span>高</span><span class="mono up">${fmtPrice(bar.high)}</span></div>
    <div class="t-row"><span>低</span><span class="mono down">${fmtPrice(bar.low)}</span></div>
    <div class="t-row"><span>收</span><span class="mono ${pctClass(bar.close - bar.open)}">${fmtPrice(bar.close)}</span></div>
    <div class="t-row"><span>涨跌</span><span class="mono ${pctClass(chg)}">${fmtPct(chg)}</span></div>
    <div id="tip-extra" style="display:none;margin-top:4px">
      <div class="t-row"><span>成交量</span><span class="mono">${fmtVolume(bar.volume)}</span></div>
      <div class="t-row"><span>成交额</span><span class="mono">${fmtTurnover(bar.turnover)}</span></div>
      <div class="t-row"><span>量比</span><span class="mono">${bar.vratio === null || bar.vratio === undefined ? '—' : bar.vratio.toFixed(2)}</span></div>
      <div class="t-row"><span>量能</span><span><span class="${vs.cls}">${vs.label}</span></span></div>
      ${mas ? `<div style="margin-top:4px;color:var(--text-3)">${mas}</div>` : ''}
    </div>
    <div id="tip-more" class="link" style="font-size:12px;margin-top:4px">更多 ▾</div>
  `;
  placeTip(tip, pos, width);
  const more = tip.querySelector('#tip-more');
  const extraEl = tip.querySelector('#tip-extra');
  more.onclick = (e) => {
    e.stopPropagation();
    const shown = extraEl.style.display !== 'none';
    extraEl.style.display = shown ? 'none' : '';
    more.textContent = shown ? '更多 ▾' : '收起 ▴';
  };
}

function hideTip() {
  const tip = document.getElementById('kline-tip');
  if (tip) tip.style.display = 'none';
}

// 点击浮层外任意位置关闭（浮层内部点击、K 线 canvas 点击除外——后者由 kline 的 onBarTap/onBlankTap 处理）
document.addEventListener('pointerdown', (e) => {
  const tip = document.getElementById('kline-tip');
  if (!tip || tip.style.display === 'none') return;
  if (tip.contains(e.target)) return;
  if (e.target instanceof Element && e.target.closest('#kline-canvas')) return;
  selectedIdx = null;
  hideTip();
});
