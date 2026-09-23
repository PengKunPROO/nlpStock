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
    <div class="hint">点击 / 触摸 K 线查看当日开高低收、量比与量能状态。量比 = 当期成交量 ÷ 前5期均量。</div>
  `;
  bindSearch(view);
  renderPeriodSeg();
  renderLegend();
  await loadChart(code, view.dataset.period || '1d');
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
      loadChart(state.chartCode || '600519.SH', document.getElementById('view').dataset.period || '1d', true);
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

async function loadChart(code, period, silent = false) {
  const head = document.getElementById('chart-head');
  const canvas = document.getElementById('kline-canvas');
  const tip = document.getElementById('kline-tip');
  if (!head || !canvas) return;
  if (!silent) head.innerHTML = '<div class="skeleton" style="height:52px"></div>';
  try {
    const count = period === '1d' ? 120 : 60;
    const data = await api.kline(code, period, count);
    state.chartCode = code;
    document.title = `${data.name} · 策略选股`;
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
    if (cleanupFn) cleanupFn();
    cleanupFn = renderKline(canvas, data.bars, {
      ...maVisible,
      onBarTap: (bar, idx, pos, width) => showTip(tip, bar, pos, width),
    });
  } catch (e) {
    head.innerHTML = `<div class="empty" style="padding:18px">加载失败：${esc(e.message || String(e))}
      <div style="margin-top:10px"><button class="btn sm secondary" id="retry-kline">重试</button></div></div>`;
    document.getElementById('retry-kline').onclick = () => loadChart(code, period);
  }
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
    <div class="t-row"><span>开盘</span><span class="mono ${pctClass(bar.close - bar.open)}">${fmtPrice(bar.open)}</span></div>
    <div class="t-row"><span>最高</span><span class="mono up">${fmtPrice(bar.high)}</span></div>
    <div class="t-row"><span>最低</span><span class="mono down">${fmtPrice(bar.low)}</span></div>
    <div class="t-row"><span>收盘</span><span class="mono ${pctClass(bar.close - bar.open)}">${fmtPrice(bar.close)}</span></div>
    <div class="t-row"><span>涨跌</span><span class="mono ${pctClass(chg)}">${fmtPct(chg)}</span></div>
    <div class="t-row"><span>成交量</span><span class="mono">${fmtVolume(bar.volume)}</span></div>
    <div class="t-row"><span>成交额</span><span class="mono">${fmtTurnover(bar.turnover)}</span></div>
    <div class="t-row"><span>量比</span><span class="mono">${bar.vratio === null || bar.vratio === undefined ? '—' : bar.vratio.toFixed(2)}</span></div>
    <div class="t-row"><span>量能</span><span><span class="${vs.cls}">${vs.label}</span></span></div>
    ${mas ? `<div style="margin-top:4px;color:var(--text-3)">${mas}</div>` : ''}
  `;
  tip.style.display = 'block';
  const flip = pos.x > width - 190;
  tip.style.left = flip ? 'auto' : `${Math.max(4, pos.x + 14)}px`;
  tip.style.right = flip ? `${Math.max(4, width - pos.x + 14)}px` : 'auto';
}
