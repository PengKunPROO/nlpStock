// 选股 view: screening 策略 + 区间 [start,end] → job progress → 结果（勾选 → 带去回测）
import { api } from '../api.js';
import { runJob, getActiveJob } from '../jobs.js';
import { navigate } from '../app.js';
import state from '../store.js';
import { dateToday, daysAgo, esc, fmtPct, fmtPrice, h, paginate, pctClass, toast } from '../util.js';

export async function renderScreenView(view) {
  let strategies = [];
  try {
    strategies = (await api.listStrategies('screening')).items;
  } catch (e) {
    view.innerHTML = `<div class="card"><div class="empty">加载失败：${esc(e.message)}</div></div>`;
    return;
  }
  if (!strategies.length) {
    view.innerHTML = `<div class="card"><div class="empty">还没有选股策略（screening）。<br>请先到「策略」页用自然语言创建。</div></div>`;
    return;
  }
  const pre = state.screenStrategy;

  view.innerHTML = `
    <div class="card">
      <div class="field">
        <label>选股策略</label>
        <select id="sc-strategy">
          ${strategies.map((s) => `<option value="${s.id}" ${pre && pre.id === s.id ? 'selected' : ''}>${esc(s.name)}（v${s.version}）</option>`).join('')}
        </select>
      </div>
      <div class="field">
        <label>股票池（覆盖策略默认）</label>
        <select id="sc-universe">
          <option value="">跟随策略默认</option>
          <option value="index:000300.SH">沪深300（300只）</option>
          <option value="index:000905.SH">中证500（500只）</option>
          <option value="index:399006.SZ">创业板指（100只成分股）</option>
          <option value="index:000016.SH">上证50（50只）</option>
          <option value="board:创业板">创业板（全部股票）</option>
          <option value="board:科创板">科创板（全部股票）</option>
          <option value="all">全市场</option>
        </select>
      </div>
      <div class="field-row">
        <div class="field"><label>开始日期</label><input id="sc-start" type="date" value="${daysAgo(90)}"></div>
        <div class="field"><label>结束日期</label><input id="sc-end" type="date" value="${dateToday()}"></div>
      </div>
      <button class="btn" id="sc-run">开始选股</button>
    </div>
    <div id="sc-progress"></div>
    <div id="sc-results"></div>
  `;

  document.getElementById('sc-run').onclick = async (ev) => {
    const sid = Number(document.getElementById('sc-strategy').value);
    const start = document.getElementById('sc-start').value;
    const end = document.getElementById('sc-end').value;
    if (!start || !end) { toast('请选择选股区间（开始/结束日期）', true); return; }
    if (start > end) { toast('开始日期不能晚于结束日期', true); return; }
    ev.target.disabled = true;
    ev.target.textContent = '选股中…';
    try {
      const uniVal = document.getElementById('sc-universe').value;
      const payload = { strategy_id: sid, start, end };
      if (uniVal === 'all') payload.universe = { type: 'all' };
      else if (uniVal) {
        const [utype, ucode] = uniVal.split(':');
        if (utype === 'board') payload.universe = { type: 'board', board: ucode };
        else payload.universe = { type: utype, code: ucode };
      }
      // 全局任务：后台轮询，切页不中断；完成后 state.lastScreenResult 更新 + 自动刷新
      await runJob('screen', () => api.screen(payload));
    } catch (e) {
      toast(`选股失败：${e.message}`, true);
    } finally {
      ev.target.disabled = false;
      ev.target.textContent = '开始选股';
    }
  };

  // 恢复：running 任务显示进度条（后台继续），done 显示结果
  const job = getActiveJob('screen');
  if (job && job.status === 'running') {
    renderProgress(document.getElementById('sc-progress'), job);
  } else if (state.lastScreenResult) {
    renderResults(document.getElementById('sc-results'), state.lastScreenResult, true);
  }
}

function renderProgress(el, job) {
  const p = job.progress || { done: 0, total: 0, current: '' };
  const pct = p.total ? Math.round((p.done / p.total) * 100) : 0;
  el.innerHTML = `
    <div class="card">
      <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:8px">
        <span>扫描 ${esc(p.current || '')}</span><span class="muted mono">${p.total ? `${p.done}/${p.total}` : ''}</span>
      </div>
      <div class="progress"><div style="width:${pct}%"></div></div>
      <div class="muted" style="font-size:12px;margin-top:6px">正在按区间扫描K线并评估入场条件（切页后后台继续）</div>
    </div>`;
}

// 局部刷新进度条（不重渲染整个 view，避免闪烁/重置用户交互）
export function refreshScreenProgress() {
  const job = getActiveJob('screen');
  const el = document.getElementById('sc-progress');
  if (job && job.status === 'running' && el) renderProgress(el, job);
}

function picked(code) {
  return state.screenPicks.some((p) => p.thscode === code);
}

function rangeLabel(result) {
  if (result.start && result.end) return `${result.start} → ${result.end}`;
  return result.start || result.as_of || '';
}

function renderResults(el, result, isCached = false) {
  const matched = result.matched || [];
  el.innerHTML = `
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:6px">
        <div>
          <b style="font-size:17px">命中 ${result.matched_count}</b>
          <span class="muted" style="font-size:13px;margin-left:8px">共评估 ${result.evaluated} 只${result.failed ? ` · 失败 ${result.failed}` : ''}</span>
        </div>
        <div class="muted" style="font-size:12px">${esc(rangeLabel(result))} · ${(result.duration_ms / 1000).toFixed(1)}s${isCached ? ' · 上次结果' : ''}</div>
      </div>
      <div style="display:flex;align-items:center;gap:8px;margin-top:10px;flex-wrap:wrap">
        <button class="btn sm secondary" id="pick-all" ${matched.length ? '' : 'disabled'}>全选</button>
        <button class="btn sm secondary" id="pick-clear" ${matched.length ? '' : 'disabled'}>清除</button>
        <span class="muted" id="pick-count" style="font-size:13px">已选 ${state.screenPicks.length} 只</span>
        <button class="btn sm" id="pick-backtest">带 ${state.screenPicks.length} 只去回测</button>
      </div>
    </div>
    <div id="screen-matched"></div>
    <div class="hint">勾选命中的票可「带 N 只去回测」；点击结果行查看K线与量能详情</div>
  `;

  const syncPickUI = () => {
    const cnt = state.screenPicks.length;
    const c = el.querySelector('#pick-count');
    if (c) c.textContent = `已选 ${cnt} 只`;
    const b = el.querySelector('#pick-backtest');
    if (b) b.textContent = `带 ${cnt} 只去回测`;
  };

  el.querySelector('#pick-all').onclick = () => {
    for (const m of matched) {
      if (!picked(m.thscode)) state.screenPicks.push({ thscode: m.thscode, name: m.name, signal_date: m.signal_date });
    }
    renderResults(el, result, isCached);
  };
  el.querySelector('#pick-clear').onclick = () => {
    state.screenPicks = [];
    renderResults(el, result, isCached);
  };
  el.querySelector('#pick-backtest').onclick = () => {
    if (!state.screenPicks.length) { toast('请先勾选要回测的票', true); return; }
    navigate('backtest');
  };

  const listDiv = document.getElementById('screen-matched');
  if (!matched.length) {
    listDiv.innerHTML = '<div class="card"><div class="empty">区间内无个股满足全部入场条件。<br>可放宽阈值或更换区间再试。</div></div>';
    return;
  }
  paginate(listDiv, matched, (container, pageItems) => {
    for (const m of pageItems) {
      const checked = picked(m.thscode) ? 'checked' : '';
      const sigTrack = (m.chg_5d !== null && m.chg_5d !== undefined) || (m.chg_20d !== null && m.chg_20d !== undefined)
        ? `<div style="display:flex;gap:10px;margin-top:8px;font-size:12px;flex-wrap:wrap">
            <span class="muted">信号后:</span>
            <span class="mono ${pctClass(m.chg_5d)}">5日 ${fmtPct(m.chg_5d)}</span>
            <span class="muted">/ 大盘 <span class="mono ${pctClass(m.bench_5d)}">${fmtPct(m.bench_5d)}</span></span>
            <span class="mono ${pctClass(m.chg_20d)}">20日 ${fmtPct(m.chg_20d)}</span>
            <span class="muted">/ 大盘 <span class="mono ${pctClass(m.bench_20d)}">${fmtPct(m.bench_20d)}</span></span>
          </div>`
        : '';
      const card = h(`<div class="card clickable" data-code="${esc(m.thscode)}" style="padding:13px 16px">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px">
          <input type="checkbox" data-pick="${esc(m.thscode)}" ${checked} style="width:18px;height:18px;flex-shrink:0;accent-color:var(--accent)">
          <div style="min-width:0;flex:1">
            <div style="display:flex;align-items:baseline;gap:6px;flex-wrap:wrap">
              <b>${esc(m.name)}</b> <span class="muted" style="font-size:12px">${esc(m.thscode)}</span>
              <span class="chip" style="font-size:11px">信号日 ${esc(m.signal_date || '—')}</span>
            </div>
            <div style="margin-top:6px;display:flex;gap:5px;flex-wrap:wrap">
              ${(m.signals || []).slice(0, 2).map((s) => `<span class="chip accent" style="font-size:11px">${esc(s)}</span>`).join('')}
              ${(m.signals || []).length > 2 ? `<span class="chip" style="font-size:11px">+${m.signals.length - 2}</span>` : ''}
            </div>
            ${sigTrack}
          </div>
          <div style="text-align:right;flex-shrink:0">
            <div class="mono" style="font-weight:700">${fmtPrice(m.last_close)}</div>
            <div class="mono ${pctClass(m.change_pct)}" style="font-size:13px;font-weight:600">${fmtPct(m.change_pct)}</div>
          </div>
        </div>
      </div>`);
      card.onclick = (ev) => {
        if (ev.target.tagName === 'INPUT') return; // 勾选不跳转
        state.chartCode = card.dataset.code;
        state.chartSignalDate = m.signal_date || null; // 带上信号日，图表页标注信号点
        location.hash = '#/chart';
      };
      card.querySelector('input[data-pick]').onchange = () => {
        const box = card.querySelector('input[data-pick]');
        if (box.checked) {
          if (!picked(m.thscode)) state.screenPicks.push({ thscode: m.thscode, name: m.name, signal_date: m.signal_date });
        } else {
          state.screenPicks = state.screenPicks.filter((p) => p.thscode !== m.thscode);
        }
        syncPickUI();
      };
      container.appendChild(card);
    }
  }, 20);
}
