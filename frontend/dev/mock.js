// Mock API for standalone visual QA (?mock=1) — classic script, no build step.
// Simulates every backend endpoint per docs/api-contract.md with deterministic data.
(function () {
  const REF_SCREENING = {
    name: '阴跌急跌·止跌反转·回踩进场',
    description: '阴跌急跌洗出空间，均线粘合止跌，底部放量站上20日线确认反转，缩量回踩不破箱体上沿进场。',
    parse_engine: 'llm',
    universe: { type: 'index', code: '000300.SH' },
    indicators: [
      { id: 'ma5', kind: 'MA', of: 'close', n: 5 }, { id: 'ma10', kind: 'MA', of: 'close', n: 10 },
      { id: 'ma20', kind: 'MA', of: 'close', n: 20 }, { id: 'vma5', kind: 'MA', of: 'volume', n: 5 },
      { id: 'vr', kind: 'VRATIO', n: 5 }, { id: 'body', kind: 'BODY_RATIO' },
      { id: 'ush', kind: 'UPPER_SHADOW_RATIO' }, { id: 'conv', kind: 'MA_CONVERGE', mas: ['ma5', 'ma10', 'ma20'] },
      { id: 'box20', kind: 'BOX_TOP', n: 20 }, { id: 'chg5', kind: 'PCT_CHANGE', of: 'close', n: 5 },
      { id: 'chg10', kind: 'PCT_CHANGE', of: 'close', n: 10 }, { id: 'chg20', kind: 'PCT_CHANGE', of: 'close', n: 20 },
    ],
    entry: {
      logic: 'all',
      conditions: [
        { left: 'chg20', op: '<=', right: -8, within: 60, note: '①阴跌：近期出现过20日累计跌幅≥8%' },
        { left: 'chg5', op: '<=', right: -4, within: 40, note: '②急跌：近期出现过5日急跌≥4%' },
        { left: 'conv', op: '<=', right: 2.5, within: 15, note: '③止跌：均线拧到一块（粘合度≤2.5%）' },
        { left: 'vr', op: '>=', right: 1.8, within: 10, note: '④反转：底部放量（量比≥1.8）' },
        { left: 'close', op: '>', right: 'ma20', note: '⑤站上关键均线20日线' },
        { left: 'chg10', op: '>=', right: 5, within: 15, note: '⑥拉一波：出现过10日涨幅≥5%' },
        { left: 'close', op: '>=', right: 'box20', right_factor: 0.98, note: '⑦回踩不破前期箱体上沿（容差2%）' },
        { left: 'vr', op: '<=', right: 1.1, note: '⑧回踩缩量（当下量比≤1.1，主力锁仓）' },
      ],
    },
  };

  const REF_TRADING = {
    name: '回踩进场·分批补仓·移动止盈',
    description: '缩量回踩箱体上沿建仓，浮亏5%补仓摊薄（最多2次），浮盈8%分批止盈一半，破位或浮亏8%止损离场。',
    parse_engine: 'llm',
    indicators: [
      { id: 'ma5', kind: 'MA', of: 'close', n: 5 }, { id: 'ma10', kind: 'MA', of: 'close', n: 10 },
      { id: 'ma20', kind: 'MA', of: 'close', n: 20 }, { id: 'vr', kind: 'VRATIO', n: 5 },
      { id: 'box20', kind: 'BOX_TOP', n: 20 }, { id: 'chg5', kind: 'PCT_CHANGE', of: 'close', n: 5 },
    ],
    rules: [
      {
        when: {
          logic: 'all',
          conditions: [
            { left: 'close', op: '>=', right: 'box20', right_factor: 0.98, note: '回踩不破箱体上沿（容差2%）' },
            { left: 'vr', op: '<=', right: 1.1, note: '回踩缩量（主力锁仓）' },
          ],
        },
        action: 'buy', size_pct: 20, max_times: 1, note: '初始建仓',
      },
      {
        when: {
          logic: 'all',
          conditions: [
            { left: 'pnl_pct', op: '<=', right: -5, note: '浮亏超5%' },
            { left: 'hold_days', op: '>=', right: 3, note: '建仓后至少3天' },
          ],
        },
        action: 'buy', size_pct: 10, max_times: 2, note: '补仓摊薄',
      },
      {
        when: {
          logic: 'all',
          conditions: [{ left: 'pnl_pct', op: '>=', right: 8, note: '浮盈超8%' }],
        },
        action: 'sell', size_pct: 50, max_times: null, note: '分批止盈一半',
      },
      {
        when: {
          logic: 'all',
          conditions: [{ left: 'pnl_pct', op: '<=', right: -8, note: '浮亏8%止损' }],
        },
        action: 'sell', size_pct: 100, max_times: null, note: '止损离场',
      },
    ],
    risk: { stop_loss_pct: 8.0, trailing_stop_pct: 5.0, max_hold_days: 30, take_profit_pct: null },
    backtest_defaults: { start: '2025-01-01', end: '2026-09-18', initial_cash: 1000000, position_pct: 20, max_positions: 5, fee_bps: 2.5, stamp_tax_bps: 5.0 },
  };

  function countConds(g) {
    if (!g || !Array.isArray(g.conditions)) return 0;
    return g.conditions.reduce((n, c) => n + (c.conditions ? countConds(c) : 1), 0);
  }
  function entryCount(s) {
    if (s.type === 'trading') return (s.rules || []).filter((r) => r.action === 'buy').length;
    return countConds(s.entry);
  }
  function exitCount(s) {
    if (s.type === 'trading') return (s.rules || []).filter((r) => r.action === 'sell').length;
    return 0;
  }

  let parseCalls = 0;
  let nextId = 1;
  const strategies = [];
  const jobs = {};
  let wlGroups = [];
  let wlItems = [];
  let wlNextId = 1;

  function makeBars(n, seed) {
    const bars = [];
    let price = 20 + (seed % 5) * 3;
    const today = Date.now();
    for (let i = 0; i < n; i++) {
      const phase = i / n;
      if (phase < 0.5) price *= 0.9985;              // 阴跌
      else if (phase < 0.62) price *= 1.0;            // 粘合
      else price *= 1.004 + 0.002 * Math.sin(i / 6);  // 反转上行
      const open = price * (0.998 + 0.004 * ((i * 7 + seed) % 10) / 10);
      const close = price;
      const high = Math.max(open, close) * 1.006;
      const low = Math.min(open, close) * 0.994;
      let vol = 800 + ((i * 13 + seed * 31) % 400);
      if (i > n * 0.6 && i < n * 0.7) vol *= 2.4;     // 阶段放量
      if (i > n - 6) vol *= 0.72;                     // 近期缩量回踩
      bars.push({
        date_ms: today - (n - i) * 86400000, date: dateStr(today - (n - i) * 86400000),
        open: r2(open), high: r2(high), low: r2(low), close: r2(close),
        volume: Math.round(vol * 100), turnover: Math.round(vol * 100 * close),
      });
    }
    // vratio + vol_state + MAs
    for (let i = 0; i < n; i++) {
      const vr = i >= 5 ? bars[i].volume / (bars.slice(i - 5, i).reduce((s, b) => s + b.volume, 0) / 5) : null;
      bars[i].vratio = vr === null ? null : Math.round(vr * 1000) / 1000;
      bars[i].vol_state = vr === null ? null : vr >= 2 ? 'surge' : vr >= 1.5 ? 'incremental' : vr >= 0.7 ? 'flat' : 'shrink';
      for (const [ma, k] of [['ma5', 5], ['ma10', 10], ['ma20', 20], ['ma60', 60]]) {
        bars[i][ma] = i >= k - 1 ? r2(bars.slice(i - k + 1, i + 1).reduce((s, b) => s + b.close, 0) / k) : null;
      }
    }
    return bars;
  }
  function r2(v) { return Math.round(v * 100) / 100; }
  function dateStr(ms) { const d = new Date(ms); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; }
  function addDays(dstr, n) { const d = new Date(dstr + 'T00:00:00'); d.setDate(d.getDate() + n); return dateStr(d.getTime()); }

  function resample(bars, period) {
    if (period === '1d') return bars;
    const size = period === '5d' ? 5 : 1;
    if (period === '5d') {
      const out = [];
      for (let i = 0; i < bars.length; i += 5) {
        const g = bars.slice(i, i + 5);
        out.push(agg(g));
      }
      return out;
    }
    const keyOf = (b) => period === '1w' ? b.date.slice(0, 4) + '-w' + weekOf(new Date(b.date_ms))
      : period === '1M' ? b.date.slice(0, 7) : b.date.slice(0, 4);
    const out = [];
    let curKey = null, g = [];
    for (const b of bars) {
      const k = keyOf(b);
      if (k !== curKey) { if (g.length) out.push(agg(g)); g = []; curKey = k; }
      g.push(b);
    }
    if (g.length) out.push(agg(g));
    return out;
  }
  function weekOf(d) { const t = new Date(d.getFullYear(), d.getMonth(), d.getDate()); const day = (t.getDay() + 6) % 7; t.setDate(t.getDate() - day + 3); const f = new Date(t.getFullYear(), 0, 4); return 1 + Math.round(((t - f) / 86400000 - 3 + ((f.getDay() + 6) % 7)) / 7); }
  function agg(g) {
    return {
      date_ms: g[g.length - 1].date_ms, date: g[g.length - 1].date,
      open: g[0].open, high: Math.max(...g.map((b) => b.high)), low: Math.min(...g.map((b) => b.low)),
      close: g[g.length - 1].close, volume: g.reduce((s, b) => s + b.volume, 0), turnover: g.reduce((s, b) => s + b.turnover, 0),
      vratio: null, vol_state: null, ma5: null, ma10: null, ma20: null, ma60: null,
    };
  }

  function finishJob(id, result) {
    const job = jobs[id];
    const total = job.progress.total;
    let done = 0;
    const timer = setInterval(() => {
      done = Math.min(total, done + Math.ceil(total / 8));
      job.progress.done = done;
      job.progress.current = '6005' + String(19 + (done % 9)) + '.SH';
      if (done >= total) {
        clearInterval(timer);
        job.status = 'done';
        job.result = result;
      }
    }, 180);
  }

  // ---- 独立回测模拟：signal_date+1 开盘买入，卖出即结束，每票独立账户 ----
  function tradingDays(fromStr, n) {
    const out = [];
    let t = new Date(fromStr + 'T00:00:00').getTime();
    while (out.length < n) {
      const d = new Date(t);
      if (d.getDay() !== 0 && d.getDay() !== 6) out.push(t);
      t += 86400000;
    }
    return out;
  }
  function mulberry32(seed) {
    return function () {
      seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function genStock(item, idx, params) {
    const code = item.thscode || '600001.SH';
    const name = item.name || code;
    const sig = item.signal_date || dateStr(Date.now() - 30 * 86400000);
    const rnd = mulberry32(code.length * 131 + (code.charCodeAt(0) % 7) + idx * 17);
    const dates = tradingDays(sig, 150); // bar 0 = 信号日
    const feeRate = params.fee_bps / 10000;
    const taxRate = params.stamp_tax_bps / 10000;
    // 价格基座与 /api/kline 的 makeBars 一致（20 + seed%5*3），保证回放标注落在 K 线可视区间内
    const base = 20 + ((code.length + (code.charCodeAt(0) % 7)) % 5) * 3;
    let price = base * (0.88 + rnd() * 0.24);
    const drift = (rnd() - 0.5) * 0.0012;
    let cash = params.initial_cash, shares = 0, cost = 0, stopPrice = 0;
    let nextBuyAt = 1, holdUntil = -1, tradeStart = null;
    let peak = 0;
    const trades = [];
    const curve = [];
    const closeTrade = (i, date, exitPrice, reason) => {
      const gross = shares * exitPrice;
      const fee = gross * feeRate + gross * taxRate;
      const pnl = gross - fee - shares * cost;
      trades.push({
        code, name,
        entry_date: tradeStart.date, entry_idx: tradeStart.i,
        entry_price: tradeStart.bp,
        exit_date: date, exit_idx: i,
        exit_price: r2(exitPrice), shares,
        pnl: r2(pnl), pnl_pct: r2(pnl / (shares * cost) * 100),
        holding_days: i - tradeStart.i,
        exit_reason: reason,
        entry_evidence: { signal_date: sig, label: '入场', logic: 'screener', conditions: [
          { expr: 'chg20 ≤ -8', left: -10.2, right: -8, passed: true, note: '①阴跌：20日累计跌幅≥8%' },
          { expr: 'vr ≥ 1.8', left: 2.1, right: 1.8, passed: true, note: '④反转：底部放量' },
          { expr: 'close > ma20', left: r2(price), right: r2(price * 0.98), passed: true, note: '⑤站上20日线' },
        ], passed: true },
        exit_evidence: reason === 'stop_loss' ? { trigger: `盘中最低 ${r2(exitPrice * 0.996)} ≤ 止损价 ${r2(stopPrice)}` }
          : reason === 'end_of_data' ? { trigger: '回测期末，按最后收盘价估值平仓' }
          : { trigger: '收盘满足离场条件（力竭/破位）' },
      });
      cash += gross - fee;
      shares = 0; cost = 0; stopPrice = 0; holdUntil = -1; tradeStart = null;
    };

    for (let i = 0; i < dates.length; i++) {
      price *= 1 + drift + Math.sin((i + idx * 7) / 9) * 0.004;
      const date = dateStr(dates[i]);
      if (shares > 0 && stopPrice > 0 && price * 0.98 <= stopPrice) {
        closeTrade(i, date, stopPrice, 'stop_loss');
      }
      if (shares === 0 && nextBuyAt > 0 && i >= nextBuyAt) {
        const bp = r2(price);
        const sh = Math.floor(cash * params.position_pct / 100 / bp / 100) * 100;
        if (sh >= 100) {
          shares = sh; cash -= sh * bp; cost = bp;
          stopPrice = r2(bp * 0.92);
          holdUntil = i + 15 + Math.floor(rnd() * 26);
          tradeStart = { i, date, bp };
        }
        nextBuyAt = -1;
      }
      if (shares > 0 && holdUntil > 0 && i >= holdUntil) {
        const win = rnd() > 0.35;
        const exitP = price * (win ? 1 + rnd() * 0.12 : 1 - rnd() * 0.1);
        closeTrade(i, date, exitP, win ? (rnd() > 0.5 ? 'signal' : 'take_profit') : 'signal');
        if (trades.length < 2 && rnd() > 0.45) nextBuyAt = i + 8 + Math.floor(rnd() * 22);
      }
      const v = cash + shares * price;
      peak = Math.max(peak, v);
      const dd = peak > 0 ? (v / peak - 1) * 100 : 0;
      curve.push({ date, value: Math.round(v), drawdown_pct: Math.round(dd * 10) / 10 });
    }
    if (shares > 0) closeTrade(dates.length - 1, dateStr(dates[dates.length - 1]), price, 'end_of_data');
    const final = curve.length ? curve[curve.length - 1].value : params.initial_cash;
    const closed = trades.filter((t) => t.exit_reason !== 'end_of_data');
    const winN = closed.filter((t) => t.pnl > 0).length;
    const grossWin = closed.reduce((s, t) => s + Math.max(t.pnl, 0), 0);
    const grossLoss = closed.reduce((s, t) => s + Math.max(-t.pnl, 0), 0);
    return {
      code, name, signal_date: sig,
      metrics: {
        total_return_pct: r2((final / params.initial_cash - 1) * 100),
        max_drawdown_pct: r2(Math.min(0, ...curve.map((e) => e.drawdown_pct))),
        win_rate_pct: closed.length ? r2(winN / closed.length * 100) : null,
        profit_factor: grossLoss > 0 ? r2(grossWin / grossLoss) : null,
        trade_count: closed.length,
        win_count: winN,
        loss_count: closed.length - winN,
        final_equity: r2(final),
      },
      trades,
      equity_curve: curve,
      audit: { daily: [] },
    };
  }

  // 合并收益率：按日期对齐，等权平均收益率%（未入场票按 0% 计），回撤为相对峰值收益率的百分点
  function mergeCurves(perStock, initialCash) {
    const allDates = [...new Set(perStock.flatMap((r) => r.equity_curve.map((e) => e.date)))].sort();
    const n = perStock.length || 1;
    let peak = 0;
    return allDates.map((date) => {
      let totalRet = 0;
      for (const r of perStock) {
        let v = initialCash;
        for (const e of r.equity_curve) {
          if (e.date <= date) v = e.value; else break;
        }
        totalRet += (v / initialCash - 1) * 100;
      }
      const avg = totalRet / n;
      peak = Math.max(peak, avg);
      return { date, value: Math.round(avg * 100) / 100, drawdown_pct: Math.round((avg - peak) * 100) / 100 };
    });
  }

  window.__MOCK_IMPL__ = async function (method, path, body) {
    await new Promise((r) => setTimeout(r, 120 + Math.random() * 180));
    const [p, qs] = path.split('?');
    const q = new URLSearchParams(qs || '');

    if (p === '/api/health') return { ok: true, version: '1.0.0-mock', fuyao_ok: true, db_ok: true, time: new Date().toISOString() };
    if (p === '/api/settings' && method === 'GET') {
      return { fuyao_api_key: 'sk-fu****PM', fuyao_api_key_set: true, llm_base_url: 'https://api.deepseek.com', llm_api_key_set: true, llm_model: 'deepseek-chat', llm_available: true, default_universe: { type: 'index', code: '000300.SH' } };
    }
    if (p === '/api/settings' && method === 'PUT') return window.__MOCK_IMPL__('GET', '/api/settings');

    if (p === '/api/parse-strategy' && method === 'POST') {
      parseCalls++;
      const type = body.strategy_type === 'trading' ? 'trading' : 'screening';
      const first = body.messages.find((m) => m.role === 'user');
      if (parseCalls % 2 === 1) {
        return type === 'screening'
          ? { type: 'clarify', understanding: '你希望捕捉「超跌之后止跌反转」的票：先有一段阴跌和急跌，均线粘合视为止跌，底部放量站上关键均线确认反转，然后缩量回踩不破箱体上沿时进场。', questions: ['「关键均线」具体指哪条？20日线还是60日线？', '止跌与反转的量化阈值有偏好吗？'], round: Math.ceil(parseCalls / 2) }
          : { type: 'clarify', understanding: '你希望把交易规则量化：回踩不破进场建仓，浮亏到一定幅度补仓摊薄，盈利后分批止盈，破位止损离场。', questions: ['初始仓位和补仓比例大概多少？补仓最多几次？', '止盈、止损幅度有偏好吗？'], round: Math.ceil(parseCalls / 2) };
      }
      const cfg = JSON.parse(JSON.stringify(type === 'screening' ? REF_SCREENING : REF_TRADING));
      cfg.source_text = first ? first.content : '';
      return type === 'screening'
        ? { type: 'config', config: cfg, summary: '八步叙事已量化为 12 项指标、8 条入场条件。', warnings: ['股票池默认沪深300', '阈值均可在审查页修改'] }
        : { type: 'config', config: cfg, summary: '交易规则已量化为 6 项指标、4 条规则（建仓/补仓/止盈/止损）。', warnings: ['止损默认8%，可在审查页修改', '补仓最多触发2次，可在规则里调整'] };
    }

    if (p === '/api/strategies' && method === 'GET') {
      const st = q.get('type');
      const items = (st ? strategies.filter((s) => s.type === st) : strategies)
        .map((s) => ({ id: s.id, name: s.name, description: s.description, type: s.type, version: s.version, updated_at: s.updated_at, parse_engine: 'llm', entry_count: entryCount(s), exit_count: exitCount(s) }));
      return { items };
    }
    if (p === '/api/strategies' && method === 'POST') {
      const cfg = JSON.parse(JSON.stringify(body.config));
      const type = body.type === 'screening' || body.type === 'trading' ? body.type : (cfg.entry ? 'screening' : 'trading');
      const s = { id: nextId++, version: 1, type, updated_at: new Date().toISOString(), ...cfg };
      strategies.unshift(s);
      return { id: s.id, version: 1, type, created_at: s.updated_at, ...cfg };
    }
    const mSid = p.match(/^\/api\/strategies\/(\d+)$/);
    if (mSid) {
      const s = strategies.find((x) => x.id === Number(mSid[1]));
      if (!s) throw httpError(404, 'not_found', '策略不存在');
      if (method === 'GET') return { id: s.id, version: s.version, type: s.type, current: s, versions: [{ version: 1, created_at: s.updated_at }, ...(s.version > 1 ? [{ version: 2, created_at: s.updated_at }] : [])] };
      if (method === 'PUT') { Object.assign(s, body.config); if (body.type) s.type = body.type; s.version++; return { id: s.id, version: s.version, type: s.type, ...body.config }; }
      if (method === 'DELETE') { strategies.splice(strategies.indexOf(s), 1); return { ok: true }; }
    }
    if (p.startsWith('/api/strategies/') && p.includes('/versions/')) {
      const [, sid, , v] = p.split('/');
      const s = strategies.find((x) => x.id === Number(sid));
      return { id: Number(sid), version: Number(v), config: s };
    }
    if (p.startsWith('/api/strategies/') && p.includes('/restore/')) {
      const [, sid] = p.split('/');
      const s = strategies.find((x) => x.id === Number(sid));
      s.version++;
      return { id: s.id, version: s.version, ...s };
    }

    if (p === '/api/screen' && method === 'POST') {
      const id = 'j_' + Math.random().toString(36).slice(2, 10);
      jobs[id] = { id, type: 'screen', status: 'running', progress: { done: 0, total: 42, current: '' }, result: null, error: null };
      const start = body.start || dateStr(Date.now() - 90 * 86400000);
      const end = body.end || dateStr(Date.now() - 86400000);
      const matched = [
        { thscode: '600519.SH', name: '贵州茅台', signal_date: start, last_close: 1253.8, change_pct: 0.098, signals: ['①阴跌：近期出现过20日累计跌幅≥8%', '②急跌：近期出现过5日急跌≥4%', '⑦回踩不破前期箱体上沿（容差2%）'], snapshot: { close: 1253.8, vr: 0.92, ma20: 1240.1 } },
        { thscode: '300750.SZ', name: '宁德时代', signal_date: addDays(start, 6), last_close: 188.42, change_pct: 1.86, signals: ['③止跌：均线拧到一块（粘合度≤2.5%）', '④反转：底部放量（量比≥1.8）'], snapshot: { close: 188.42, vr: 1.05, ma20: 182.3 } },
        { thscode: '000858.SZ', name: '五粮液', signal_date: addDays(start, 13), last_close: 122.66, change_pct: -0.42, signals: ['④反转：底部放量（量比≥1.8）', '⑤站上关键均线20日线'], snapshot: { close: 122.66, vr: 1.32, ma20: 121.9 } },
      ];
      finishJob(id, { start, end, evaluated: 42, failed: 1, matched_count: matched.length, duration_ms: 4231, universe: { type: 'index', code: '000300.SH', name: '沪深300' }, matched });
      return { job_id: id };
    }

    if (p === '/api/backtest' && method === 'POST') {
      const id = 'j_' + Math.random().toString(36).slice(2, 10);
      const pool = (body.pool || []).slice(0, 3);
      jobs[id] = { id, type: 'backtest', status: 'running', progress: { done: 0, total: Math.max(pool.length, 1), current: '' }, result: null, error: null };
      const params = {
        start: body.start || '2025-01-01',
        end: body.end || '2026-09-18',
        initial_cash: Number(body.initial_cash || 1000000),
        position_pct: Number(body.position_pct || 20),
        fee_bps: Number(body.fee_bps || 2.5),
        stamp_tax_bps: Number(body.stamp_tax_bps || 5),
        stock_count: pool.length,
      };
      const perStock = pool.map((it, i) => genStock(it, i, params));
      const allTrades = perStock.flatMap((r) => r.trades);
      const mergedCurve = mergeCurves(perStock, params.initial_cash);
      const rets = perStock.map((r) => r.metrics.total_return_pct);
      const winRates = perStock.map((r) => r.metrics.win_rate_pct).filter((v) => v !== null && v !== undefined);
      const closed = allTrades.filter((t) => t.exit_reason !== 'end_of_data');
      const winN = closed.filter((t) => t.pnl > 0).length;
      const finalRet = mergedCurve.length ? mergedCurve[mergedCurve.length - 1].value : 0;  // 平均收益率%
      const finalEq = perStock.reduce((a, r) => a + (r.metrics.final_equity || 0), 0);  // 总市值（供参考）
      const strategy = strategies.find((s) => s.id === Number(body.trading_strategy_id));
      finishJob(id, {
        params: {
          ...params,
          strategy_id: Number(body.trading_strategy_id) || null,
          strategy_name: strategy ? strategy.name : null,
          strategy_version: strategy ? strategy.version : null,
        },
        metrics: {
          start: params.start, end: params.end,
          avg_total_return_pct: rets.length ? r2(rets.reduce((a, b) => a + b, 0) / rets.length) : 0,
          avg_win_rate_pct: winRates.length ? r2(winRates.reduce((a, b) => a + b, 0) / winRates.length) : null,
          total_return_pct: r2(finalRet),
          max_drawdown_pct: r2(Math.min(0, ...mergedCurve.map((e) => e.drawdown_pct))),
          trade_count: closed.length,
          win_count: winN,
          loss_count: closed.length - winN,
          stock_count: perStock.length,
          final_equity: r2(finalEq),
        },
        per_stock: perStock,
        equity_curve: mergedCurve,
        trades: allTrades.slice().sort((a, b) => a.exit_date.localeCompare(b.exit_date)),
        audit: { daily: [] },
      });
      return { job_id: id };
    }

    if (p.startsWith('/api/jobs/')) {
      const job = jobs[p.split('/')[3]];
      if (!job) throw httpError(404, 'not_found', '任务不存在');
      return job;
    }

    if (p === '/api/kline') {
      const code = q.get('thscode') || '600519.SH';
      const period = q.get('period') || '1d';
      const count = Math.min(Number(q.get('count') || 120), 120);
      const names = { '600519.SH': '贵州茅台', '000001.SH': '上证指数', '300750.SZ': '宁德时代', '881101.TI': '种植业' };
      let bars = makeBars(count, code.length + (code.charCodeAt(0) % 7));
      if (period !== '1d') {
        bars = resample(bars, period);
        for (let i = 0; i < bars.length; i++) {
          const vr = i >= 5 ? bars[i].volume / (bars.slice(i - 5, i).reduce((s, b) => s + b.volume, 0) / 5) : null;
          bars[i].vratio = vr === null ? null : Math.round(vr * 100) / 1000 * 1;
          bars[i].vratio = vr === null ? null : Math.round(vr * 1000) / 1000;
          bars[i].vol_state = vr === null ? null : vr >= 2 ? 'surge' : vr >= 1.5 ? 'incremental' : vr >= 0.7 ? 'flat' : 'shrink';
        }
      }
      const last = bars[bars.length - 1], prev = bars[bars.length - 2] || last;
      return {
        thscode: code, name: names[code] || code, asset_type: code.endsWith('.TI') ? 'ths-index' : code.endsWith('.SH') && code.startsWith('000') ? 'a-share-index' : 'a-share',
        period, last_close: last.close, change_pct: Math.round((last.close / prev.close - 1) * 1000) / 10,
        bars,
        volume_summary: { latest_vratio: last.vratio, latest_vol_state: last.vol_state, trend: '增量放量', note: '近3个周期量比≥1.5，区间价格上涨4.2%' },
      };
    }

    if (p === '/api/search') {
      const qq = q.get('q') || '';
      const all = [
        { thscode: '600519.SH', name: '贵州茅台', asset_type: 'a-share', exchange: 'SH' },
        { thscode: '000858.SZ', name: '五粮液', asset_type: 'a-share', exchange: 'SZ' },
        { thscode: '300750.SZ', name: '宁德时代', asset_type: 'a-share', exchange: 'SZ' },
        { thscode: '000001.SH', name: '上证指数', asset_type: 'a-share-index', exchange: 'SH' },
        { thscode: '000300.SH', name: '沪深300', asset_type: 'a-share-index', exchange: 'SH' },
        { thscode: '881101.TI', name: '种植业', asset_type: 'ths-index', exchange: null },
      ];
      return { items: all.filter((x) => x.thscode.includes(qq) || x.name.includes(qq)).map((x) => ({ ...x, kline_available: true })) };
    }

    if (p === '/api/universe/options') {
      return {
        indices: [
          { code: '000300.SH', name: '沪深300', count: null }, { code: '000905.SH', name: '中证500', count: null },
          { code: '000852.SH', name: '中证1000', count: null }, { code: '000016.SH', name: '上证50', count: null },
        ],
        sectors: [
          { code: '881101.TI', name: '种植业', count: null }, { code: '881102.TI', name: '农林牧渔', count: null },
          { code: '886042.TI', name: '白酒概念', count: null },
        ],
      };
    }

    // ---- 自选股 mock ----
    if (p === '/api/watchlist/groups' && method === 'GET') {
      return { items: wlGroups.map((g) => ({ id: g.id, name: g.name, count: wlItems.filter((i) => i.group_id === g.id).length })) };
    }
    if (p === '/api/watchlist/groups' && method === 'POST') {
      const g = { id: ++wlNextId, name: body.name };
      wlGroups.push(g);
      return { id: g.id };
    }
    let wm = p.match(/^\/api\/watchlist\/groups\/(\d+)$/);
    if (wm && method === 'PUT') {
      const g = wlGroups.find((x) => x.id === Number(wm[1]));
      if (!g) throw httpError(404, 'not_found', '分组不存在');
      g.name = body.name;
      return { ok: true };
    }
    if (wm && method === 'DELETE') {
      const gid = Number(wm[1]);
      wlGroups = wlGroups.filter((x) => x.id !== gid);
      wlItems = wlItems.filter((x) => x.group_id !== gid);
      return { ok: true };
    }
    wm = p.match(/^\/api\/watchlist\/groups\/(\d+)\/items$/);
    if (wm && method === 'GET') {
      return { items: wlItems.filter((i) => i.group_id === Number(wm[1])).map((i) => ({ id: i.id, thscode: i.thscode, name: i.name })) };
    }
    if (p === '/api/watchlist/items' && method === 'POST') {
      const exist = wlItems.find((i) => i.group_id === body.group_id && i.thscode === body.thscode);
      if (!exist) wlItems.push({ id: ++wlNextId, group_id: body.group_id, thscode: body.thscode, name: body.name });
      return { ok: true };
    }
    wm = p.match(/^\/api\/watchlist\/items\/(\d+)$/);
    if (wm && method === 'DELETE') {
      wlItems = wlItems.filter((i) => i.id !== Number(wm[1]));
      return { ok: true };
    }
    throw httpError(404, 'not_found', 'mock 未实现: ' + method + ' ' + path);
  };

  function httpError(status, code, message) {
    const e = new Error(message);
    e.status = status; e.code = code;
    return e;
  }

  // 预置策略便于查看列表：1 只选股（screening）+ 1 只交易（trading）
  strategies.push({ id: nextId++, version: 2, type: 'screening', updated_at: new Date().toISOString(), ...JSON.parse(JSON.stringify(REF_SCREENING)) });
  strategies.push({ id: nextId++, version: 1, type: 'trading', updated_at: new Date().toISOString(), ...JSON.parse(JSON.stringify(REF_TRADING)) });
})();
