// 回测 view: params → job → report (metrics + equity curve + trades)
import { api, pollJob } from '../api.js';
import state from '../store.js';
import { renderEquity } from '../chart/equity.js';
import { EXIT_LABEL, esc, fmtMoney, fmtPct, fmtPrice, h, pctClass, toast } from '../util.js';

export async function renderBacktestView(view) {
  let strategies = [];
  try {
    strategies = (await api.listStrategies()).items;
  } catch (e) {
    view.innerHTML = `<div class="card"><div class="empty">加载失败：${esc(e.message)}</div></div>`;
    return;
  }
  if (!strategies.length) {
    view.innerHTML = `<div class="card"><div class="empty">还没有策略。<br>请先到「策略」页创建。</div></div>`;
    return;
  }
  const first = strategies[0];
  const defaults = state.settings || {};
  view.innerHTML = `
    <div class="card">
      <div class="field">
        <label>策略</label>
        <select id="bt-strategy">
          ${strategies.map((s) => `<option value="${s.id}" ${s.id === first.id ? 'selected' : ''}>${esc(s.name)}（v${s.version}）</option>`).join('')}
        </select>
      </div>
      <div class="field-row">
        <div class="field"><label>开始日期</label><input id="bt-start" type="date"></div>
        <div class="field"><label>结束日期</label><input id="bt-end" type="date"></div>
      </div>
      <div class="field-row">
        <div class="field"><label>初始资金</label><input id="bt-cash" type="number" value="1000000" step="100000"></div>
        <div class="field"><label>单仓 %</label><input id="bt-pos" type="number" value="20" step="5"></div>
        <div class="field"><label>最大持仓</label><input id="bt-maxpos" type="number" value="5"></div>
      </div>
      <button class="btn" id="bt-run">运行回测</button>
    </div>
    <div id="bt-progress"></div>
    <div id="bt-report"></div>
  `;

  // prefill dates from strategy defaults when selection changes
  const sel = document.getElementById('bt-strategy');
  const applyDefaults = async () => {
    try {
      const d = await api.getStrategy(Number(sel.value));
      const bd = d.current.backtest_defaults || {};
      document.getElementById('bt-start').value = bd.start || '';
      document.getElementById('bt-end').value = bd.end || '';
      document.getElementById('bt-cash').value = bd.initial_cash || 1000000;
      document.getElementById('bt-pos').value = bd.position_pct || 20;
      document.getElementById('bt-maxpos').value = bd.max_positions || 5;
    } catch { /* keep current values */ }
  };
  sel.onchange = applyDefaults;
  await applyDefaults();

  document.getElementById('bt-run').onclick = async (ev) => {
    ev.target.disabled = true;
    ev.target.textContent = '回测中…';
    const prog = document.getElementById('bt-progress');
    prog.innerHTML = `<div class="card"><div class="spinner" style="margin:14px auto"></div>
      <div class="muted" style="text-align:center;font-size:13px" id="bt-pg-label">加载K线与指标…</div></div>`;
    try {
      const { job_id } = await api.backtest({
        strategy_id: Number(sel.value),
        start: document.getElementById('bt-start').value || undefined,
        end: document.getElementById('bt-end').value || undefined,
        initial_cash: Number(document.getElementById('bt-cash').value) || undefined,
        position_pct: Number(document.getElementById('bt-pos').value) || undefined,
        max_positions: Number(document.getElementById('bt-maxpos').value) || undefined,
      });
      const result = await pollJob(job_id, (job) => {
        const p = job.progress || { done: 0, total: 0 };
        const lbl = document.getElementById('bt-pg-label');
        if (lbl) lbl.textContent = p.total ? `加载K线 ${p.done}/${p.total}（${p.current || ''}）` : '加载K线与指标…';
      }, 1000);
      state.lastBacktestResult = result;
      prog.innerHTML = '';
      renderReport(document.getElementById('bt-report'), result);
    } catch (e) {
      prog.innerHTML = '';
      toast(`回测失败：${e.message}`, true);
    } finally {
      ev.target.disabled = false;
      ev.target.textContent = '运行回测';
    }
  };

  if (state.lastBacktestResult) renderReport(document.getElementById('bt-report'), state.lastBacktestResult);
}

function renderReport(el, result) {
  const m = result.metrics;
  const p = result.params;
  const metric = (k, v, cls = '') => `<div class="metric"><div class="k">${k}</div><div class="v ${cls}">${v ?? '—'}</div></div>`;
  el.innerHTML = `
    <div class="card" style="padding:12px 16px">
      <div style="display:flex;justify-content:space-between;font-size:13px" class="muted">
        <span>${esc(p.start)} → ${esc(p.end)}</span>
        <span>${esc(p.universe_name || '')}${p.stock_count ? ' · ' + p.stock_count + '只' : ''}</span>
      </div>
    </div>
    <div class="metrics">
      ${metric('总收益', fmtPct(m.total_return_pct), pctClass(m.total_return_pct))}
      ${metric('年化收益', fmtPct(m.annual_return_pct), pctClass(m.annual_return_pct))}
      ${metric('最大回撤', fmtPct(m.max_drawdown_pct, false), 'down')}
      ${metric('夏普比率', m.sharpe)}
      ${metric('胜率', m.win_rate_pct === null || m.win_rate_pct === undefined ? '—' : m.win_rate_pct.toFixed(1) + '%', m.win_rate_pct >= 50 ? 'up' : '')}
      ${metric('盈亏比', m.profit_factor)}
      ${metric('交易次数', m.trade_count + '（盈' + (m.win_count ?? 0) + ' 亏' + (m.loss_count ?? 0) + '）')}
      ${metric('平均盈/亏', (m.avg_win_pct === null || m.avg_win_pct === undefined ? '—' : '+' + m.avg_win_pct.toFixed(1) + '%') + ' / ' + (m.avg_loss_pct === null || m.avg_loss_pct === undefined ? '—' : m.avg_loss_pct.toFixed(1) + '%'))}
    </div>
    <div class="card">
      <h3>净值曲线与回撤</h3>
      <canvas id="equity-canvas" class="equity" style="height:250px"></canvas>
      <div class="muted" style="font-size:11px;margin-top:6px">期末净值 ${fmtMoney(m.final_equity)} · 红色区域为回撤（-30%满幅）· 触摸查看逐日数值</div>
    </div>
    <div class="card">
      <h3>交易明细（${result.trades.length}）</h3>
      <div style="overflow-x:auto">
        <table class="trade-table">
          <thead><tr><th>股票</th><th>买入</th><th>卖出</th><th>天数</th><th>盈亏%</th><th>原因</th></tr></thead>
          <tbody>
            ${result.trades.map((t) => `
              <tr data-code="${esc(t.code)}">
                <td><b>${esc(t.name)}</b><div class="muted" style="font-size:11px">${esc(t.code)}</div></td>
                <td>${esc(t.entry_date)}<div class="muted" style="font-size:11px">@${fmtPrice(t.entry_price)}</div></td>
                <td>${esc(t.exit_date)}<div class="muted" style="font-size:11px">@${fmtPrice(t.exit_price)}</div></td>
                <td>${t.holding_days}</td>
                <td class="${pctClass(t.pnl_pct)}">${fmtPct(t.pnl_pct)}<div class="muted" style="font-size:11px">${fmtMoney(t.pnl)}</div></td>
                <td><span class="badge ${esc(t.exit_reason)}">${EXIT_LABEL[t.exit_reason] || esc(t.exit_reason)}</span></td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>
    </div>
    <div class="hint">回测口径：T日收盘出信号 → T+1开盘价成交；买入按当前权益×单仓%开仓；止损盘中触发按止损价成交（跳空按开盘价）；含佣金（${p.fee_bps}bp/边）与印花税（${p.stamp_tax_bps}bp/卖出）。期末持仓按最后收盘估值，不计入胜率。</div>
  `;
  const canvas = document.getElementById('equity-canvas');
  let tipEl = null;
  renderEquity(canvas, result.equity_curve, {
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
  el.querySelectorAll('tr[data-code]').forEach((tr) => {
    tr.onclick = () => {
      state.chartCode = tr.dataset.code;
      location.hash = '#/chart';
    };
  });
}
