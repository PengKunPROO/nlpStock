// 选股 view: strategy + universe picker → job progress → results
import { api, pollJob } from '../api.js';
import state from '../store.js';
import { esc, fmtPct, fmtPrice, h, pctClass, toast } from '../util.js';

export async function renderScreenView(view) {
  let strategies = [];
  try {
    strategies = (await api.listStrategies()).items;
  } catch (e) {
    view.innerHTML = `<div class="card"><div class="empty">加载失败：${esc(e.message)}</div></div>`;
    return;
  }
  if (!strategies.length) {
    view.innerHTML = `<div class="card"><div class="empty">还没有策略。<br>请先到「策略」页用自然语言创建。</div></div>`;
    return;
  }
  let opts = { indices: [], sectors: [] };
  try { opts = await api.universeOptions(); } catch { /* upstream may be down */ }
  const uni = state.settings?.default_universe || { type: 'index', code: '000300.SH' };
  const pre = state.screenStrategy;

  view.innerHTML = `
    <div class="card">
      <div class="field">
        <label>策略</label>
        <select id="sc-strategy">
          ${strategies.map((s) => `<option value="${s.id}" ${pre && pre.id === s.id ? 'selected' : ''}>${esc(s.name)}（v${s.version}）</option>`).join('')}
        </select>
      </div>
      <div class="field">
        <label>股票池</label>
        <select id="sc-uni-type">
          <option value="index" ${uni.type === 'index' ? 'selected' : ''}>指数成分</option>
          <option value="sector" ${uni.type === 'sector' ? 'selected' : ''}>行业板块</option>
          <option value="custom" ${uni.type === 'custom' ? 'selected' : ''}>自选</option>
          <option value="all" ${uni.type === 'all' ? 'selected' : ''}>全市场（首次拉取较慢，约15-30分钟）</option>
        </select>
      </div>
      <div class="field" id="sc-code-field"><label>范围</label><select id="sc-uni-code"></select></div>
      <div class="field" id="sc-codes-field" style="display:none"><label>自选代码（逗号分隔）</label><input id="sc-codes" type="text" placeholder="600519.SH,000001.SZ"></div>
      <div class="field"><label>按历史日期选股（可选，回看验证）</label><input id="sc-asof" type="date"></div>
      <button class="btn" id="sc-run">开始选股</button>
    </div>
    <div id="sc-progress"></div>
    <div id="sc-results"></div>
  `;

  const typeSel = document.getElementById('sc-uni-type');
  const codeSel = document.getElementById('sc-uni-code');
  const codeField = document.getElementById('sc-code-field');
  const codesField = document.getElementById('sc-codes-field');
  const fillCodes = () => {
    const list = typeSel.value === 'sector' ? opts.sectors : opts.indices;
    codeSel.innerHTML = list.map((it) => `<option value="${esc(it.code)}">${esc(it.name)}${it.count ? '（' + it.count + '）' : ''}</option>`).join('') || '<option value="">（无选项）</option>';
    if (uni.code && typeSel.value === uni.type) codeSel.value = uni.code;
  };
  fillCodes();
  typeSel.onchange = () => {
    const t = typeSel.value;
    codeField.style.display = t === 'index' || t === 'sector' ? '' : 'none';
    codesField.style.display = t === 'custom' ? '' : 'none';
    if (t === 'sector' || t === 'index') fillCodes();
  };
  typeSel.onchange();

  document.getElementById('sc-run').onclick = async (ev) => {
    const sid = Number(document.getElementById('sc-strategy').value);
    const t = typeSel.value;
    let universe = null;
    if (t === 'custom') {
      const codes = document.getElementById('sc-codes').value.match(/[0-9A-Z]+\.(SH|SZ|BJ|TI)/g) || [];
      if (!codes.length) { toast('请填写自选代码', true); return; }
      universe = { type: 'custom', codes };
    } else if (t !== 'all') {
      universe = { type: t, code: codeSel.value };
    } else {
      if (!confirm('全市场约5400只，首次需拉取历史数据（15-30分钟，有进度），缓存后秒级。继续？')) return;
      universe = { type: 'all' };
    }
    const asOf = document.getElementById('sc-asof').value || null;
    ev.target.disabled = true;
    ev.target.textContent = '选股中…';
    const prog = document.getElementById('sc-progress');
    prog.innerHTML = `
      <div class="card">
        <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:8px">
          <span id="pg-label">提交任务…</span><span class="muted mono" id="pg-count"></span>
        </div>
        <div class="progress"><div id="pg-bar" style="width:0%"></div></div>
        <div class="muted" style="font-size:12px;margin-top:6px">正在拉取/读取K线并评估入场条件</div>
      </div>`;
    try {
      const { job_id } = await api.screen({ strategy_id: sid, universe, as_of: asOf });
      const result = await pollJob(job_id, (job) => {
        const p = job.progress || { done: 0, total: 0 };
        document.getElementById('pg-label').textContent = `扫描 ${p.current || ''}`;
        document.getElementById('pg-count').textContent = p.total ? `${p.done}/${p.total}` : '';
        document.getElementById('pg-bar').style.width = p.total ? `${Math.round((p.done / p.total) * 100)}%` : '0%';
      }, 1000);
      state.lastScreenResult = result;
      prog.innerHTML = '';
      renderResults(document.getElementById('sc-results'), result);
    } catch (e) {
      prog.innerHTML = '';
      toast(`选股失败：${e.message}`, true);
    } finally {
      ev.target.disabled = false;
      ev.target.textContent = '开始选股';
    }
  };

  if (state.lastScreenResult) renderResults(document.getElementById('sc-results'), state.lastScreenResult, true);
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
        <div class="muted" style="font-size:12px">${esc(result.universe?.name || '')} · ${esc(result.as_of || '')} · ${(result.duration_ms / 1000).toFixed(1)}s${isCached ? ' · 上次结果' : ''}</div>
      </div>
    </div>
    ${matched.length ? matched.map((m) => `
      <div class="card" data-code="${esc(m.thscode)}" style="cursor:pointer;padding:13px 16px">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <div style="min-width:0">
            <b>${esc(m.name)}</b> <span class="muted" style="font-size:12px">${esc(m.thscode)}</span>
            <div style="margin-top:6px;display:flex;gap:5px;flex-wrap:wrap">
              ${(m.signals || []).slice(0, 2).map((s) => `<span class="chip accent" style="font-size:11px">${esc(s)}</span>`).join('')}
              ${(m.signals || []).length > 2 ? `<span class="chip" style="font-size:11px">+${m.signals.length - 2}</span>` : ''}
            </div>
          </div>
          <div style="text-align:right;flex-shrink:0">
            <div class="mono" style="font-weight:700">${fmtPrice(m.last_close)}</div>
            <div class="mono ${pctClass(m.change_pct)}" style="font-size:13px;font-weight:600">${fmtPct(m.change_pct)}</div>
          </div>
        </div>
      </div>`).join('') : '<div class="card"><div class="empty">今日无个股满足全部入场条件。<br>可放宽阈值或更换股票池再试。</div></div>'}
    <div class="hint">点击结果查看K线与量能详情</div>
  `;
  el.querySelectorAll('[data-code]').forEach((card) => {
    card.onclick = () => {
      state.chartCode = card.dataset.code;
      location.hash = '#/chart';
    };
  });
}
