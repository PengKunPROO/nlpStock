// 回测 view: 勾选池独立回测（每票独立账户，不轮动）→ job → 报告（组合汇总 + 合并净值 + 每票明细）
import { api } from '../api.js';
import { runJob, getActiveJob } from '../jobs.js';
import state from '../store.js';
import { renderEquity } from '../chart/equity.js';
import { renderKline } from '../chart/kline.js';
import { EXIT_LABEL, esc, fmtMoney, fmtPct, fmtPrice, h, paginate, pctClass, toast, dateToday, daysAgo } from '../util.js';

const metric = (k, v, cls = '') => `<div class="metric"><div class="k">${k}</div><div class="v ${cls}">${v ?? '—'}</div></div>`;

export async function renderBacktestView(view) {
  let strategies = [];
  try {
    strategies = (await api.listStrategies('trading')).items;
  } catch (e) {
    view.innerHTML = `<div class="card"><div class="empty">加载失败：${esc(e.message)}</div></div>`;
    return;
  }
  if (!strategies.length) {
    view.innerHTML = `<div class="card"><div class="empty">还没有交易策略。<br>请先到「策略」页创建交易策略。</div></div>`;
    return;
  }
  const first = strategies[0];
  view.innerHTML = `
    <div class="card">
      <div class="field">
        <label>交易策略</label>
        <select id="bt-strategy">
          ${strategies.map((s) => `<option value="${s.id}" ${s.id === first.id ? 'selected' : ''}>${esc(s.name)}（v${s.version}）</option>`).join('')}
        </select>
      </div>
      <div class="field-row">
        <div class="field"><label>开始日期</label><input id="bt-start" type="date" value="${daysAgo(90)}"></div>
        <div class="field"><label>结束日期</label><input id="bt-end" type="date" value="${dateToday()}"></div>
      </div>
      <div class="field-row">
        <div class="field"><label>初始资金（每票）</label><input id="bt-cash" type="number" value="1000000" step="100000"></div>
        <div class="field"><label>单仓 %</label><input id="bt-pos" type="number" value="20" step="5"></div>
      </div>
    </div>
    <div class="card" id="bt-pool-card"></div>
    <button class="btn" id="bt-run" ${(state.screenPicks || []).length ? '' : 'disabled'}>运行回测</button>
    <div id="bt-progress"></div>
    <div id="bt-report"></div>
  `;

  // prefill dates from strategy defaults when selection changes
  const sel = document.getElementById('bt-strategy');
  const applyDefaults = async () => {
    try {
      const d = await api.getStrategy(Number(sel.value));
      const bd = d.current.backtest_defaults || {};
      // 日期默认：今天往前 3 个月（不用策略里的固定历史区间）
      document.getElementById('bt-start').value = daysAgo(90);
      document.getElementById('bt-end').value = dateToday();
      document.getElementById('bt-cash').value = bd.initial_cash || 1000000;
      document.getElementById('bt-pos').value = bd.position_pct || 20;
    } catch { /* keep current values */ }
  };
  sel.onchange = applyDefaults;

  renderPoolCard(document.getElementById('bt-pool-card'));

  document.getElementById('bt-run').onclick = async (ev) => {
    const pool = state.screenPicks || [];
    if (!pool.length) {
      toast('回测池为空，请先到「选股」页勾选股票', true);
      location.hash = '#/screen';
      return;
    }
    ev.target.disabled = true;
    ev.target.textContent = '回测中…';
    try {
      // 全局任务：后台轮询，切页不中断；完成后 state.lastBacktestResult 更新 + 自动刷新
      await runJob('backtest', () => api.backtest({
        trading_strategy_id: Number(sel.value),
        pool: state.screenPicks,
        start: document.getElementById('bt-start').value || undefined,
        end: document.getElementById('bt-end').value || undefined,
        initial_cash: Number(document.getElementById('bt-cash').value) || undefined,
        position_pct: Number(document.getElementById('bt-pos').value) || undefined,
      }));
    } catch (e) {
      toast(`回测失败：${e.message}`, true);
    } finally {
      ev.target.disabled = false;
      ev.target.textContent = '运行回测';
    }
  };

  await applyDefaults();

  // 恢复：running 任务显示进度，done 显示报告
  const job = getActiveJob('backtest');
  if (job && job.status === 'running') {
    renderProgress(document.getElementById('bt-progress'), job);
  } else if (state.lastBacktestResult) {
    renderReport(document.getElementById('bt-report'), state.lastBacktestResult);
  }
}

function renderProgress(el, job) {
  const p = job.progress || { done: 0, total: 0 };
  el.innerHTML = `<div class="card"><div class="spinner" style="margin:14px auto"></div>
    <div class="muted" style="text-align:center;font-size:13px">${p.total ? `独立回测 ${p.done}/${p.total}（${esc(p.current || '')}）` : '独立回测中…'}（切页后后台继续）</div></div>`;
}

// 局部刷新进度条（不重渲染整个 view，避免闪烁/重置用户交互）
export function refreshBacktestProgress() {
  const job = getActiveJob('backtest');
  const el = document.getElementById('bt-progress');
  if (job && job.status === 'running' && el) renderProgress(el, job);
}

// 回测池卡片：chips（可删除）+ 空态（提示去选股页勾选 + 从自选股添加）
function renderPoolCard(el) {
  const pool = state.screenPicks || [];
  el.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <b style="font-size:15px">回测池 <span class="chip accent" id="bt-pool-count">${pool.length}</span></b>
      <span class="muted" style="font-size:12px">${pool.length ? '每票独立账户回测，不轮动' : ''}</span>
    </div>
    ${pool.length ? `<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px">
      ${pool.map((pk, i) => `<span class="chip" style="display:inline-flex;align-items:center;gap:6px;padding:7px 10px">
        <b>${esc(pk.name)}</b>
        <span class="muted" style="font-size:11px">${esc(pk.thscode)}${pk.signal_date ? ' · 信号 ' + esc(pk.signal_date) : ''}</span>
        <button class="bt-pick-x" data-i="${i}" aria-label="移除 ${esc(pk.name)}" style="border:none;background:none;cursor:pointer;font-size:15px;line-height:1;padding:0 2px;color:var(--muted)">✕</button>
      </span>`).join('')}
    </div>
    <button class="btn sm secondary" id="bt-add-watch" style="margin-bottom:6px">＋ 从自选股添加</button>` : `<div class="empty" style="padding:16px 0">
        回测池为空。可从选股结果勾选，或从自选股添加。<br>
        <button class="btn sm secondary" id="bt-go-screen" style="margin-top:12px">去选股页勾选</button>
        <button class="btn sm secondary" id="bt-add-watch" style="margin-top:8px">＋ 从自选股添加</button>
      </div>`}
  `;
  const go = el.querySelector('#bt-go-screen');
  if (go) go.onclick = () => { location.hash = '#/screen'; };
  const addWatch = el.querySelector('#bt-add-watch');
  if (addWatch) addWatch.onclick = () => openWatchlistPicker(el);
  el.querySelectorAll('.bt-pick-x').forEach((x) => {
    x.onclick = () => {
      state.screenPicks.splice(Number(x.dataset.i), 1);
      renderPoolCard(el);
      const run = document.getElementById('bt-run');
      if (run) run.disabled = !state.screenPicks.length;
    };
  });
}

// 自选股选择器：从自选股分组勾选加入回测池（无 signal_date，回测用交易策略 entry 入场）
function openWatchlistPicker(el) {
  api.watchlistGroups().then(({ items: groups }) => {
    if (!groups.length) {
      toast('还没有自选股分组，请先到「自选」页创建', true);
      return;
    }
    const overlay = h(`<div id="wl-picker" class="replay-overlay">
      <div class="replay-head">
        <b style="font-size:16px">从自选股添加</b>
        <button class="btn sm secondary" id="wl-picker-close">关闭</button>
      </div>
      <div class="card" id="wl-picker-body"></div>
    </div>`);
    document.body.appendChild(overlay);
    overlay.querySelector('#wl-picker-close').onclick = () => overlay.remove();
    overlay.addEventListener('pointerdown', (e) => { if (e.target === overlay) overlay.remove(); });
    let gid = groups[0].id;
    const body = overlay.querySelector('#wl-picker-body');
    const renderBody = () => {
      body.innerHTML = `<div style="display:flex;gap:8px;overflow-x:auto;padding-bottom:8px;margin-bottom:10px">
        ${groups.map((g) => `<button class="seg-btn ${g.id === gid ? 'active' : ''}" data-gid="${g.id}">${esc(g.name)}</button>`).join('')}
      </div><div id="wl-picker-items"></div>`;
      body.querySelectorAll('[data-gid]').forEach((b) => b.onclick = () => { gid = Number(b.dataset.gid); renderBody(); });
      api.watchlistItems(gid).then(({ items }) => {
        const listEl = body.querySelector('#wl-picker-items');
        listEl.innerHTML = '';
        if (!items.length) { listEl.innerHTML = '<div class="empty" style="padding:16px 0">该分组无股票，请到「自选」页添加</div>'; return; }
        for (const it of items) {
          const added = state.screenPicks.some((p) => p.thscode === it.thscode);
          const row = h(`<div class="card" style="display:flex;justify-content:space-between;align-items:center;padding:12px 16px;cursor:pointer">
            <div><b>${esc(it.name)}</b> <span class="muted" style="font-size:12px">${esc(it.thscode)}</span></div>
            <span class="chip ${added ? 'accent' : ''}">${added ? '已加入' : '＋添加'}</span>
          </div>`);
          row.onclick = () => {
            if (!added) state.screenPicks.push({ thscode: it.thscode, name: it.name, signal_date: null });
            else state.screenPicks = state.screenPicks.filter((p) => p.thscode !== it.thscode);
            renderBody();
            renderPoolCard(el);
            const run = document.getElementById('bt-run');
            if (run) run.disabled = !state.screenPicks.length;
          };
          listEl.appendChild(row);
        }
      }).catch((e) => { body.querySelector('#wl-picker-items').innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`; });
    };
    renderBody();
  }).catch((e) => toast(`加载自选股失败：${e.message}`, true));
}

function evHtml(ev) {
  if (!ev) return '';
  if (ev.trigger) {
    return `<div class="ev-box"><div class="ev-row"><span class="ev-val">${esc(ev.trigger)}</span></div></div>`;
  }
  const rows = (ev.conditions || []).map((c) => {
    const cls = c.passed ? 'ev-pass' : 'ev-fail';
    const lv = c.left === null || c.left === undefined ? '—' : Number(c.left).toFixed(4);
    const rv = c.right === null || c.right === undefined ? '—' : Number(c.right).toFixed(4);
    return `<div class="ev-row"><span class="${cls}">${c.passed ? '✓' : '✗'}</span><code>${esc(c.expr)}</code><span class="ev-val">${lv} vs ${rv}</span>${c.note ? `<span class="muted">${esc(c.note)}</span>` : ''}</div>`;
  }).join('');
  const dateStr = ev.signal_date ? ` · 信号日 ${esc(ev.signal_date)}` : '';
  return `<div class="ev-box">${rows}${dateStr ? `<div class="muted" style="margin-top:4px">${dateStr}</div>` : ''}</div>`;
}

function renderReport(el, result) {
  const m = result.metrics || {};
  const p = result.params || {};
  const perStock = result.per_stock || [];
  el.innerHTML = `
    <div class="card" style="padding:12px 16px">
      <div style="font-size:14px;font-weight:600;margin-bottom:4px">${esc(p.strategy_name || '交易策略')}${p.strategy_version ? '<span class="chip accent" style="margin-left:6px">v' + p.strategy_version + '</span>' : ''}</div>
      <div style="display:flex;justify-content:space-between;font-size:13px" class="muted">
        <span>${esc(p.start)} → ${esc(p.end)}</span>
        <span>${p.stock_count ? p.stock_count + '只 · 每票独立账户' : ''}</span>
      </div>
    </div>
    <div class="metrics">
      ${metric('最大回撤', fmtPct(m.max_drawdown_pct, false), 'down')}
      ${metric('交易次数', m.trade_count + '（盈' + (m.win_count ?? 0) + ' 亏' + (m.loss_count ?? 0) + '）')}
      ${metric('期末资产', fmtMoney(m.final_equity))}
    </div>
    <div class="card">
      <h3>合并净值曲线与回撤</h3>
      <canvas id="equity-canvas" class="equity" style="height:250px"></canvas>
      <div class="muted" style="font-size:11px;margin-top:6px">合并净值 ${fmtMoney(m.final_equity)} · 红色区域为回撤（-30%满幅）· 触摸查看逐日数值</div>
    </div>
    <div id="bt-per-stock"></div>
    <div class="hint">回测口径：每票独立账户（各 ${fmtMoney(p.initial_cash)}），信号日 T 收盘 → T+1 开盘买入（单仓 ${p.position_pct}%），卖出即结束、不轮动；含佣金（${p.fee_bps}bp/边）与印花税（${p.stamp_tax_bps}bp/卖出）。期末持仓按最后收盘估值，不计入胜率。</div>
  `;
  const canvas = document.getElementById('equity-canvas');
  let tipEl = null;
  renderEquity(canvas, result.equity_curve || [], {
    onPoint: (pt, idx, pos, width) => {
      if (!tipEl) {
        tipEl = h('<div class="kline-tip" style="position:absolute"></div>');
        canvas.parentElement.appendChild(tipEl);
        canvas.parentElement.style.position = 'relative';
      }
      tipEl.style.display = 'block';
      tipEl.innerHTML = `<div style="font-weight:700">${esc(pt.date)}</div>
        <div class="t-row"><span>净值</span><span class="mono">${fmtMoney(pt.value)}</span></div>
        <div class="t-row"><span>回撤</span><span class="mono down">${fmtPct(pt.drawdown_pct, false)}</span></div>`;
      const flip = pos.x > width - 170;
      tipEl.style.left = flip ? 'auto' : `${pos.x + 12}px`;
      tipEl.style.right = flip ? '12px' : 'auto';
      tipEl.style.top = '26px';
    },
  });

  // 每票明细卡
  const perDiv = document.getElementById('bt-per-stock');
  for (const s of perStock) perDiv.appendChild(renderPerStockCard(s));
}

function renderPerStockCard(s) {
  const sm = s.metrics || {};
  const card = h(`<div class="card">
    <div style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:6px;margin-bottom:10px">
      <div>
        <b style="font-size:16px">${esc(s.name)}</b>
        <span class="muted" style="font-size:12px;margin-left:6px">${esc(s.code)}</span>
        ${s.signal_date ? `<span class="chip" style="margin-left:8px;font-size:11px">信号日 ${esc(s.signal_date)}</span>` : ''}
      </div>
      <span class="mono ${pctClass(sm.total_return_pct)}" style="font-size:14px;font-weight:700">${fmtPct(sm.total_return_pct)}</span>
    </div>
    <div class="metrics">
      ${metric('收益', fmtPct(sm.total_return_pct), pctClass(sm.total_return_pct))}
      ${metric('胜率', sm.win_rate_pct === null || sm.win_rate_pct === undefined ? '—' : sm.win_rate_pct.toFixed(1) + '%', (sm.win_rate_pct ?? 0) >= 50 ? 'up' : '')}
      ${metric('最大回撤', fmtPct(sm.max_drawdown_pct, false), 'down')}
      ${metric('交易次数', sm.trade_count + '（盈' + (sm.win_count ?? 0) + ' 亏' + (sm.loss_count ?? 0) + '）')}
      ${metric('盈亏比', sm.profit_factor)}
      ${metric('期末资产', fmtMoney(sm.final_equity))}
    </div>
    <div class="stock-trades"></div>
  </div>`);
  const tDiv = card.querySelector('.stock-trades');
  const trades = s.trades || [];
  if (trades.length) {
    tDiv.innerHTML = `<div class="muted" style="font-size:12px;margin:2px 0 6px">交易明细 · 点击行展开买卖依据 · K线回放标注买卖点</div>`;
    renderTradesTable(tDiv, trades);
  } else {
    tDiv.innerHTML = `<div class="muted" style="font-size:12px;margin-top:6px">无交易（信号日后无足够数据或未成交）</div>`;
  }
  return card;
}

// 交易明细表（分页 + 展开买卖依据 + K线回放）
function renderTradesTable(container, trades) {
  paginate(container, trades, (c, pageItems) => {
    const tb = h(`<div style="overflow-x:auto"><table class="trade-table">
      <thead><tr><th>股票</th><th>买入</th><th>卖出</th><th>天数</th><th>盈亏%</th><th>原因</th><th>回放</th></tr></thead>
      <tbody></tbody></table></div>`);
    const tbody = tb.querySelector('tbody');
    for (const t of pageItems) {
      const tr = h(`<tr data-code="${esc(t.code)}">
        <td><b>${esc(t.name)}</b><div class="muted" style="font-size:11px">${esc(t.code)}</div></td>
        <td>${esc(t.entry_date)}<div class="muted" style="font-size:11px">@${fmtPrice(t.entry_price)}</div></td>
        <td>${esc(t.exit_date)}<div class="muted" style="font-size:11px">@${fmtPrice(t.exit_price)}</div></td>
        <td>${t.holding_days}</td>
        <td class="${pctClass(t.pnl_pct)}">${fmtPct(t.pnl_pct)}<div class="muted" style="font-size:11px">${fmtMoney(t.pnl)}</div></td>
        <td><span class="badge ${esc(t.exit_reason)}">${EXIT_LABEL[t.exit_reason] || esc(t.exit_reason)}</span></td>
        <td><button class="btn sm replay-btn" data-code="${esc(t.code)}">K线回放</button></td>
      </tr>`);
      const detail = h(`<tr class="trade-detail" style="display:none"><td colspan="7" style="padding:0">
        <div style="padding:4px 8px">
          <div class="muted" style="font-size:11px;margin:4px 0">买入依据</div>${evHtml(t.entry_evidence)}
          <div class="muted" style="font-size:11px;margin:4px 0">卖出依据</div>${evHtml(t.exit_evidence)}
        </div></td></tr>`);
      tr.onclick = () => {
        const visible = detail.style.display !== 'none';
        detail.style.display = visible ? 'none' : '';
        if (!visible) tr.insertAdjacentElement('afterend', detail);
        else detail.remove();
      };
      const rb = tr.querySelector('.replay-btn');
      rb.onclick = (e) => {
        e.stopPropagation(); // 不触发行展开/收起
        openReplay(t.code, t.name, trades.filter((x) => x.code === t.code));
      };
      tbody.appendChild(tr);
    }
    c.appendChild(tb);
  }, 20);
}

// ---------- K线回放（买卖点标注） ----------

// 把 trade 的 bar 索引映射到当前 K 线窗口：优先按日期精确匹配（回测全区间 bars 与
// 250 根回放窗口可能不对齐），日期在窗口外时退回 entry_idx/exit_idx（若在窗口内）。
function resolveIdx(bars, date, idx) {
  if (!bars.length) return null;
  if (date) {
    const i = bars.findIndex((b) => b.date >= date);
    if (i !== -1 && bars[i].date === date) return i;
    if (i === -1 || (i === 0 && bars[0].date > date)) return null; // 日期在窗口外
  }
  if (Number.isInteger(idx) && idx >= 0 && idx < bars.length) return idx;
  return null;
}

let replayCleanup = null;
function closeReplay() {
  if (replayCleanup) { replayCleanup(); replayCleanup = null; }
  const o = document.getElementById('replay-overlay');
  if (o) o.remove();
}

async function openReplay(code, name, trades) {
  closeReplay();
  const overlay = h(`<div id="replay-overlay" class="replay-overlay">
    <div class="replay-head">
      <div>
        <b style="font-size:16px">${esc(name)}</b>
        <span class="muted" style="font-size:12px;margin-left:6px">${esc(code)} · ${trades.length} 笔交易</span>
      </div>
      <button class="btn sm secondary" id="replay-close">关闭</button>
    </div>
    <div class="replay-legend">
      <span class="replay-lg"><i class="replay-tri buy"></i>买入点</span>
      <span class="replay-lg"><i class="replay-tri sell"></i>卖出点</span>
      <span class="muted" style="font-size:12px">点击 ▲ / ▼ 查看操作详情</span>
    </div>
    <div class="card replay-canvas-wrap">
      <canvas id="replay-canvas" class="kline" style="height:520px"></canvas>
      <div id="replay-tip" class="kline-tip" style="display:none"></div>
    </div>
    <div class="hint" style="margin-top:8px">红▲=买入 · 绿▼=卖出；点击标注查看方向/日期/价格/股数/原因，点击 K 线查看当日行情。</div>
  </div>`);
  document.body.appendChild(overlay);
  const closeBtn = overlay.querySelector('#replay-close');
  closeBtn.onclick = closeReplay;
  overlay.addEventListener('pointerdown', (e) => { if (e.target === overlay) closeReplay(); });

  const tip = overlay.querySelector('#replay-tip');
  const placeTip = (pos, width) => {
    const flip = pos.x > width - 200;
    tip.style.left = flip ? 'auto' : `${Math.max(4, pos.x + 14)}px`;
    tip.style.right = flip ? `${Math.max(4, width - pos.x + 14)}px` : 'auto';
    tip.style.display = 'block';
  };
  const showBarTip = (bar, pos, width) => {
    const chg = bar.open ? (bar.close / bar.open - 1) * 100 : null;
    tip.innerHTML = `
      <div style="font-weight:700;margin-bottom:2px">${esc(bar.date)}</div>
      <div class="t-row"><span>开盘</span><span class="mono ${pctClass(bar.close - bar.open)}">${fmtPrice(bar.open)}</span></div>
      <div class="t-row"><span>最高</span><span class="mono up">${fmtPrice(bar.high)}</span></div>
      <div class="t-row"><span>最低</span><span class="mono down">${fmtPrice(bar.low)}</span></div>
      <div class="t-row"><span>收盘</span><span class="mono ${pctClass(bar.close - bar.open)}">${fmtPrice(bar.close)}</span></div>
      <div class="t-row"><span>涨跌</span><span class="mono ${pctClass(chg)}">${fmtPct(chg)}</span></div>`;
    placeTip(pos, width);
  };

  try {
    const data = await api.kline(code, '1d', 250);
    if (!document.getElementById('replay-overlay')) return; // 加载期间用户已关闭
    const bars = data.bars || [];
    const markers = [];
    for (const t of trades) {
      const ei = resolveIdx(bars, t.entry_date, t.entry_idx);
      const xi = resolveIdx(bars, t.exit_date, t.exit_idx);
      if (ei !== null) markers.push({ idx: ei, type: 'buy', price: t.entry_price, detail: { dir: '买入', date: t.entry_date, price: t.entry_price, shares: t.shares, reason: '入场信号' } });
      if (xi !== null) markers.push({ idx: xi, type: 'sell', price: t.exit_price, detail: { dir: '卖出', date: t.exit_date, price: t.exit_price, shares: t.shares, reason: EXIT_LABEL[t.exit_reason] || t.exit_reason || '—' } });
    }
    const canvas = overlay.querySelector('#replay-canvas');
    replayCleanup = renderKline(canvas, bars, {
      visibleCount: 60,
      markers,
      onCrosshair: (bar, pos, width) => {
        // 拖动时实时跟随显示对应 K 线信息
        if (bar) showBarTip(bar, pos, width);
        else tip.style.display = 'none';
      },
      onMarkerTap: (m, pos, width) => {
        const d = m.detail || {};
        tip.innerHTML = `
          <div style="font-weight:700;margin-bottom:2px"><span class="badge ${m.type === 'buy' ? 'signal' : 'stop_loss'}">${esc(d.dir)}</span> ${esc(d.date)}</div>
          <div class="t-row"><span>价格</span><span class="mono">${fmtPrice(d.price)}</span></div>
          <div class="t-row"><span>股数</span><span class="mono">${d.shares ?? '—'}</span></div>
          <div class="t-row"><span>原因</span><span>${esc(d.reason)}</span></div>`;
        placeTip(pos, width);
      },
      onBarTap: (bar, idx, pos, width) => {
        showBarTip(bar, pos, width);
        return true;
      },
      onBlankTap: () => { tip.style.display = 'none'; },
    });
  } catch (e) {
    replayCleanup = () => {};
    overlay.querySelector('.replay-canvas-wrap').innerHTML =
      `<div class="empty">K线加载失败：${esc(e.message || String(e))}</div>`;
  }
}
