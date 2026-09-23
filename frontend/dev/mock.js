// Mock API for standalone visual QA (?mock=1) — classic script, no build step.
// Simulates every backend endpoint per docs/api-contract.md with deterministic data.
(function () {
  const REF = {
    name: '阴跌急跌·止跌反转·回踩进场',
    description: '阴跌急跌洗出空间，均线粘合止跌，底部放量站上20日线确认反转，缩量回踩不破箱体上沿进场；上影线/缩量力竭或破位离场。',
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
    exit: {
      logic: 'any',
      conditions: [
        { left: 'ush', op: '>', right: 0.4, note: '力竭：长上影线' },
        { left: 'vr', op: '<', right: 0.5, within: 3, note: '力竭：量能跟不上（近3日出现过极端缩量）' },
        { logic: 'all', conditions: [
          { left: 'body', op: '<', right: 'body', right_lag: 1, note: '力竭：阳线实体越来越短' },
          { left: 'body', op: '>', right: 0 },
        ], note: '阳线但实体连续收窄' },
        { left: 'close', op: '<', right: 'ma10', note: '离场：跌破10日线' },
        { left: 'close', op: '<', right: 'box20', right_factor: 0.95, note: '离场：击穿箱体上沿5%（支撑失效）' },
      ],
    },
    risk: { stop_loss_pct: 8.0, max_hold_days: 30, take_profit_pct: null },
    backtest_defaults: { start: '2025-01-01', end: '2026-09-18', initial_cash: 1000000, position_pct: 20, max_positions: 5, fee_bps: 2.5, stamp_tax_bps: 5.0 },
  };

  let parseCalls = 0;
  let nextId = 1;
  const strategies = [];
  const jobs = {};

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
      const first = body.messages.find((m) => m.role === 'user');
      if (parseCalls % 2 === 1) {
        return { type: 'clarify', understanding: '你希望捕捉「超跌之后止跌反转」的票：先有一段阴跌和急跌，均线粘合视为止跌，底部放量站上关键均线确认反转，然后缩量回踩不破箱体上沿时进场；力竭（长上影/缩量/实体收窄）或破位时离场。', questions: ['「关键均线」具体指哪条？20日线还是60日线？', '止损幅度和最长持仓天数有偏好吗？'], round: Math.ceil(parseCalls / 2) };
      }
      const cfg = JSON.parse(JSON.stringify(REF));
      cfg.source_text = first ? first.content : '';
      return { type: 'config', config: cfg, summary: '八步叙事已量化为 12 项指标、8 条入场、5 条离场条件。', warnings: ['未提及止损，默认8%，请在审查页确认', '股票池默认沪深300'] };
    }

    if (p === '/api/strategies' && method === 'GET') {
      return { items: strategies.map((s) => ({ id: s.id, name: s.name, description: s.description, version: s.version, updated_at: s.updated_at, parse_engine: 'llm', entry_count: s.entry.conditions.length, exit_count: s.exit.conditions.length })) };
    }
    if (p === '/api/strategies' && method === 'POST') {
      const s = { id: nextId++, version: 1, updated_at: new Date().toISOString(), ...JSON.parse(JSON.stringify(body.config)) };
      strategies.unshift(s);
      return { id: s.id, version: 1, created_at: s.updated_at, ...body.config };
    }
    const mSid = p.match(/^\/api\/strategies\/(\d+)$/);
    if (mSid) {
      const s = strategies.find((x) => x.id === Number(mSid[1]));
      if (!s) throw httpError(404, 'not_found', '策略不存在');
      if (method === 'GET') return { id: s.id, version: s.version, current: s, versions: [{ version: 1, created_at: s.updated_at }, ...(s.version > 1 ? [{ version: 2, created_at: s.updated_at }] : [])] };
      if (method === 'PUT') { Object.assign(s, body.config); s.version++; return { id: s.id, version: s.version, ...body.config }; }
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
      const uni = body.universe || { type: 'index', code: '000300.SH' };
      const matched = [
        { thscode: '600519.SH', name: '贵州茅台', last_close: 1253.8, change_pct: 0.098, signals: ['①阴跌：近期出现过20日累计跌幅≥8%', '②急跌：近期出现过5日急跌≥4%', '⑦回踩不破前期箱体上沿（容差2%）'], snapshot: { close: 1253.8, vr: 0.92, ma20: 1240.1 } },
        { thscode: '300750.SZ', name: '宁德时代', last_close: 188.42, change_pct: 1.86, signals: ['③止跌：均线拧到一块（粘合度≤2.5%）', '④反转：底部放量（量比≥1.8）'], snapshot: { close: 188.42, vr: 1.05, ma20: 182.3 } },
      ];
      finishJob(id, { as_of: dateStr(Date.now() - 86400000), evaluated: 42, failed: 1, matched_count: matched.length, duration_ms: 4231, universe: { ...uni, name: '沪深300' }, matched });
      return { job_id: id };
    }

    if (p === '/api/backtest' && method === 'POST') {
      const id = 'j_' + Math.random().toString(36).slice(2, 10);
      jobs[id] = { id, type: 'backtest', status: 'running', progress: { done: 0, total: 8, current: '' }, result: null, error: null };
      const days = 180;
      const curve = [];
      let v = 1000000, peak = v, dd = 0;
      for (let i = 0; i < days; i++) {
        v *= 1 + (Math.sin(i / 11) * 0.006 + (i > 90 && i < 110 ? -0.004 : 0.0022));
        peak = Math.max(peak, v);
        dd = (v / peak - 1) * 100;
        curve.push({ date: dateStr(Date.now() - (days - i) * 86400000), value: Math.round(v), drawdown_pct: Math.round(dd * 10) / 10 });
      }
      finishJob(id, {
        params: { start: '2025-01-01', end: '2026-09-18', initial_cash: 1000000, position_pct: 20, max_positions: 5, fee_bps: 2.5, stamp_tax_bps: 5, universe_name: '沪深300', stock_count: 8 },
        metrics: { total_return_pct: 23.4, annual_return_pct: 11.7, max_drawdown_pct: -12.6, sharpe: 0.85, win_rate_pct: 58.3, profit_factor: 1.72, trade_count: 24, win_count: 14, loss_count: 10, avg_win_pct: 9.8, avg_loss_pct: -4.2, avg_hold_days: 11.3, final_equity: Math.round(v) },
        equity_curve: curve,
        trades: [
          { code: '600519.SH', name: '贵州茅台', entry_date: '2025-03-04', entry_price: 1480.2, exit_date: '2025-03-28', exit_price: 1560.5, shares: 1300, pnl: 103690, pnl_pct: 5.42, holding_days: 18, exit_reason: 'signal' },
          { code: '300750.SZ', name: '宁德时代', entry_date: '2025-04-15', entry_price: 168.3, exit_date: '2025-04-22', exit_price: 154.8, shares: 11000, pnl: -148290, pnl_pct: -8.0, holding_days: 5, exit_reason: 'stop_loss' },
          { code: '000858.SZ', name: '五粮液', entry_date: '2025-06-02', entry_price: 122.6, exit_date: '2025-07-15', exit_price: 139.9, shares: 15000, pnl: 258510, pnl_pct: 14.06, holding_days: 30, exit_reason: 'max_hold' },
          { code: '601318.SH', name: '中国平安', entry_date: '2025-08-20', entry_price: 51.2, exit_date: '2025-12-31', exit_price: 55.8, shares: 38000, pnl: 172860, pnl_pct: 8.86, holding_days: 92, exit_reason: 'end_of_data' },
        ],
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
    throw httpError(404, 'not_found', 'mock 未实现: ' + method + ' ' + path);
  };

  function httpError(status, code, message) {
    const e = new Error(message);
    e.status = status; e.code = code;
    return e;
  }

  // 预置一个策略便于查看列表
  strategies.push({ id: nextId++, version: 2, updated_at: new Date().toISOString(), ...JSON.parse(JSON.stringify(REF)) });
})();
