// 策略 view: list / chat alignment / review editor / version history
import { api } from '../api.js';
import state from '../store.js';
import { esc, h, toast } from '../util.js';

const EXAMPLE_TEXT = '1，阴跌之后等急跌，\n2，急跌之后等止跌，(均线拧到一块是止跌信号)\n3，止跌之后等反转(底部放量，阳线实体越来越大，价格站上关键均线才是反转信号)，\n4，反转之后等进场(拉一波再缩量回踩不破前期箱体上沿，确认支撑有效，说明主力锁仓，这时候才可以进)，\n5，力竭出现，因为量能跟不上，出现上影线，越来越短的阳线，都是力竭信号\n6，力竭后的离场，不舍得卖啊，这时落袋才是利润，\n7，离场之后等待回落，千万别追，\n8，回调支撑如果被击穿就不要进了。';

const alignMessages = { screening: [], trading: [] }; // 对齐对话历史（按策略类型分存，切 tab/type 均保留）
const chatDrafts = { screening: '', trading: '' }; // 输入草稿（按类型分存，切 tab 保留）
const subByType = { screening: 'list', trading: 'list' }; // 各类型的子视图状态（list/new/review/detail）

const TYPE_TABS = [
  { v: 'screening', label: '选股策略' },
  { v: 'trading', label: '交易策略' },
];
const typeLabel = (t) => (t === 'trading' ? '交易' : '选股');

function currentType(view) {
  const t = view.dataset.stratType;
  return t === 'trading' ? 'trading' : 'screening';
}

function renderTypeSeg(view) {
  const seg = document.getElementById('type-seg');
  if (!seg) return;
  seg.innerHTML = '';
  const cur = currentType(view);
  for (const t of TYPE_TABS) {
    const b = h(`<button class="${t.v === cur ? 'active' : ''}">${t.label}</button>`);
    b.onclick = () => {
      if (t.v === cur) return;
      view.dataset.stratType = t.v;
      const prev = subByType[t.v] || 'list';
      // review/detail 依赖当前类型的 draftConfig/sid，切换类型后不适用，回退到 list
      go(prev === 'review' || prev === 'detail' ? 'list' : prev);
    };
    seg.appendChild(b);
  }
}

export async function renderStrategyView(view) {
  const sub = view.dataset.sub || 'list';
  view.classList.toggle('chat-mode', sub === 'new');
  if (sub === 'list') await renderList(view);
  else if (sub === 'new') renderChat(view);
  else if (sub === 'review') renderReview(view);
  else if (sub === 'detail') await renderDetail(view, Number(view.dataset.sid));
}

function go(sub, extra = {}) {
  const v = document.getElementById('view');
  v.dataset.sub = sub;
  subByType[currentType(v)] = sub; // 记录各类型的子视图状态
  for (const [k, val] of Object.entries(extra)) v.dataset[k] = val;
  v.dispatchEvent(new CustomEvent('rerender'));
}

// ---------------- list ----------------
async function renderList(view) {
  const type = currentType(view);
  const tLabel = typeLabel(type);
  view.innerHTML = '<div class="spinner"></div>';
  let items = [];
  try {
    items = (await api.listStrategies(type)).items;
  } catch (e) {
    view.innerHTML = `<div class="seg" id="type-seg"></div><div class="card"><div class="empty">加载失败：${esc(e.message)}</div></div>`;
    renderTypeSeg(view);
    return;
  }
  const cards = items.map((s) => `
    <div class="card" data-sid="${s.id}" style="cursor:pointer">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px">
        <div style="min-width:0">
          <div style="font-size:16px;font-weight:700;margin-bottom:3px">${esc(s.name)}</div>
          <div class="muted" style="font-size:13px;overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical">${esc(s.description || '—')}</div>
        </div>
        <span class="chip accent" style="flex-shrink:0">v${s.version}</span>
      </div>
      <div style="display:flex;gap:6px;margin-top:10px;flex-wrap:wrap">
        ${s.type === 'trading'
          ? `<span class="chip">买 ${s.entry_count} 条</span><span class="chip">卖 ${s.exit_count} 条</span>`
          : `<span class="chip">入场 ${s.entry_count} 条</span>`}
        <span class="chip">${esc((s.updated_at || '').slice(0, 10))}</span>
      </div>
    </div>`).join('');
  view.innerHTML = `
    <div class="seg" id="type-seg"></div>
    <button class="btn" id="new-strategy" style="margin-bottom:14px">＋ 新建${tLabel}策略（自然语言）</button>
    ${items.length ? cards : `<div class="card"><div class="empty">还没有${tLabel}策略。<br>用一句自然语言描述你的${tLabel === '选股' ? '选股思路' : '交易规则'}，AI 会帮你量化。</div></div>`}`;
  renderTypeSeg(view);
  document.getElementById('new-strategy').onclick = () => { alignMessages[currentType(view)] = []; chatDrafts[currentType(view)] = ''; go('new'); };
  view.querySelectorAll('[data-sid]').forEach((el) => {
    el.onclick = () => go('detail', { sid: el.dataset.sid });
  });
}

// ---------------- chat alignment ----------------
function renderChat(view) {
  const type = currentType(view);
  const tLabel = typeLabel(type);
  view.innerHTML = `
    <div class="seg" id="type-seg"></div>
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
        <span class="chip accent">AI 对齐 · ${tLabel}</span>
        <span class="link" style="font-size:13px" id="back-list">← 返回列表</span>
      </div>
      <div class="hint">${type === 'screening'
        ? '用自然语言描述你的选股策略。AI 会先复述它的量化理解，并向你追问模糊点（均线、阈值、止损等），对齐完成后生成可审查的量化配置。'
        : '用自然语言描述你的交易规则。AI 会先复述它的量化理解，并向你追问模糊点（仓位、补仓、止盈止损等），对齐完成后生成可审查的规则配置。'}</div>
    </div>
    <div class="chat" id="chat-list"></div>
    <div class="chat-input">
      <textarea id="chat-input" placeholder="${type === 'screening' ? '描述你的策略，例如：阴跌之后等急跌……' : '描述交易规则，例如：回踩不破买，浮亏5%补一次，涨8%卖一半……'}"></textarea>
      <button class="send-btn" id="chat-send" title="发送">↑</button>
    </div>
    ${type === 'screening' ? '<div style="text-align:center;margin-top:8px"><span class="chip" style="cursor:pointer" id="fill-example">填入示例策略</span></div>' : ''}
  `;
  renderTypeSeg(view);
  document.getElementById('back-list').onclick = () => go('list');
  const fillBtn = document.getElementById('fill-example');
  if (fillBtn) {
    fillBtn.onclick = () => {
      document.getElementById('chat-input').value = EXAMPLE_TEXT;
    };
  }
  const list = document.getElementById('chat-list');
  const input = document.getElementById('chat-input');
  const sendBtn = document.getElementById('chat-send');
  input.value = chatDrafts[type] || ''; // 恢复草稿（切 tab 后保留）
  input.addEventListener('input', () => { chatDrafts[type] = input.value; });

  const appendBubble = (role, html) => {
    list.appendChild(h(`<div class="bubble ${role}">${html}</div>`));
    list.scrollTop = list.scrollHeight;
  };

  const showTyping = () => {
    const el = h('<div class="typing"><span class="dot"></span><span class="dot"></span><span class="dot"></span><span>AI 正在思考</span></div>');
    list.appendChild(el);
    list.scrollTop = list.scrollHeight;
    return el;
  };

  const renderHistory = () => {
    list.innerHTML = '';
    for (const m of alignMessages[type]) {
      if (m.role === 'user') appendBubble('me', esc(m.content));
      else appendBubble('ai', m.html || esc(m.content));
    }
    if (!alignMessages[type].length) {
      appendBubble('ai', type === 'trading'
        ? '你好，我是交易策略量化助手。<br>请描述你的交易规则 —— 建仓条件、仓位管理（补仓/加仓）、卖出条件（止盈/止损/移动止损）等，越具体越好。'
        : '你好，我是选股策略量化助手。<br>请描述你的选股策略 —— 越具体越好（哪些均线、大致幅度、止损偏好等）。也可以直接点下方「填入示例策略」。');
    }
  };
  renderHistory();

  const send = async () => {
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    chatDrafts[type] = ''; // 发送后清空草稿
    alignMessages[type].push({ role: 'user', content: text });
    appendBubble('me', esc(text));
    sendBtn.disabled = true;
    let typing;
    try {
      typing = showTyping();
      const resp = await api.parseStrategy(alignMessages[type].map(({ role, content }) => ({ role, content })), type);
      if (typing) typing.remove();
      if (resp.type === 'clarify') {
        const qHtml = `<div class="q-title">当前量化理解</div>${esc(resp.understanding || '')}<div class="q-title" style="margin-top:8px">请确认 ${resp.round}/4</div><ol>${resp.questions.map((q) => `<li>${esc(q)}</li>`).join('')}</ol>`;
        alignMessages[type].push({ role: 'assistant', content: `${resp.understanding}\n${resp.questions.join('\n')}`, html: qHtml });
        appendBubble('ai', qHtml);
        input.placeholder = '回答 AI 的问题（可一次性回答多个）…';
      } else {
        state.draftConfig = resp.config;
        const wHtml = `<b>量化完成</b><br>${esc(resp.summary || '')}${resp.warnings && resp.warnings.length ? `<div class="q-title" style="margin-top:6px">默认值提醒</div><ul>${resp.warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul>` : ''}<div class="muted" style="margin-top:6px">正在进入审查页，请逐条确认指标与阈值 →</div>`;
        appendBubble('ai', wHtml);
        setTimeout(() => go('review'), 700);
      }
    } catch (e) {
      if (typing) typing.remove();
      if (e.code === 'llm_not_configured') {
        appendBubble('ai', `<span class="up">尚未配置 AI 解析（DeepSeek）API Key。</span><br>请到「设置」页填写后再来。`);
      } else {
        appendBubble('ai', `<span class="up">解析失败：${esc(e.message || String(e))}</span><br>请换个说法重试，或补充更多细节。`);
      }
      alignMessages[type].pop(); // 移除本轮用户消息以便重发
      input.value = text;
      chatDrafts[type] = text; // 恢复草稿
    } finally {
      sendBtn.disabled = false;
    }
  };
  sendBtn.onclick = send;
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
  });
}

// ---------------- review editor ----------------
function renderReview(view) {
  const cfg = state.draftConfig;
  if (!cfg) { go('list'); return; }
  const type = currentType(view);
  const tLabel = typeLabel(type);
  let jsonMode = false;
  view.innerHTML = `
    <div class="seg" id="type-seg"></div>
    <div class="card" style="padding:10px 16px"><span class="link" style="font-size:14px" id="review-back">‹ 返回列表（放弃本次编辑）</span></div>
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <span class="chip accent">审查 · ${tLabel} · ${cfg.parse_engine === 'llm' ? 'AI 生成' : '草稿'}</span>
        <span class="link" style="font-size:13px" id="review-json-toggle">JSON 模式</span>
      </div>
      <div class="hint">${type === 'screening'
        ? '以下是 AI 量化的选股策略配置。<b>每条条件旁的灰字是对应你的原话</b>，数字均可修改；确认无误后保存。保存后仍可继续修改（自动存版本）。'
        : '以下是 AI 量化的交易策略配置。<b>每条规则旁的灰字是对应你的原话</b>，仓位、阈值与风控参数均可修改；确认无误后保存。保存后仍可继续修改（自动存版本）。'}</div>
    </div>
    <div id="review-body"></div>
    <button class="btn" id="save-draft" style="margin-top:4px">保存策略</button>
  `;
  renderTypeSeg(view);
  document.getElementById('review-back').onclick = () => { state.draftConfig = null; go('list'); };

  const body = document.getElementById('review-body');
  const renderBody = () => {
    if (jsonMode) {
      body.innerHTML = `<div class="card"><textarea id="json-editor" style="min-height:340px;font-family:Consolas,monospace;font-size:12px"></textarea>
        <div style="margin-top:10px"><button class="btn sm secondary" id="json-apply">应用并回到表单</button></div></div>`;
      const ta = document.getElementById('json-editor');
      ta.value = JSON.stringify(cfg, null, 2);
      document.getElementById('json-apply').onclick = () => {
        try {
          const parsed = JSON.parse(ta.value);
          state.draftConfig = parsed;
          jsonMode = false;
          renderBody();
        } catch (e) {
          toast(`JSON 无效：${e.message}`, true);
        }
      };
      return;
    }
    body.innerHTML = '';
    body.appendChild(buildForm(cfg, type));
  };
  document.getElementById('review-json-toggle').onclick = () => { jsonMode = !jsonMode; renderBody(); };
  renderBody();

  document.getElementById('save-draft').onclick = async (ev) => {
    ev.target.disabled = true;
    try {
      await api.createStrategy(state.draftConfig, type);
      toast('策略已保存');
      state.draftConfig = null;
      go('list');
    } catch (e) {
      toast(`保存失败：${e.message}${e.raw ? '（' + String(e.raw).slice(0, 120) + '）' : ''}`, true);
      ev.target.disabled = false;
    }
  };
}

function buildForm(cfg, type) {
  const wrap = h('<div></div>');
  wrap.appendChild(h(`<div class="card">
    <div class="field"><label>策略名称</label><input id="f-name" type="text" value="${esc(cfg.name)}"></div>
    <div class="field"><label>描述</label><textarea id="f-desc">${esc(cfg.description || '')}</textarea></div>
  </div>`));

  const indicatorsSection = () => {
    wrap.appendChild(h(`<div class="section-title">指标（${cfg.indicators.length}）</div>`));
    const indCard = h('<div class="card" id="ind-list"></div>');
    for (const ind of cfg.indicators) {
      indCard.appendChild(indRow(ind, cfg));
    }
    wrap.appendChild(indCard);
  };

  if (type === 'screening') {
    if (!cfg.entry) cfg.entry = { logic: 'all', conditions: [] };
    wrap.appendChild(h(`<div class="section-title">股票池</div>`));
    wrap.appendChild(universeCard(cfg));
    indicatorsSection();
    wrap.appendChild(h(`<div class="section-title">入场条件（${cfg.entry.logic === 'all' ? '全部满足' : '任一满足'}才出信号）</div>`));
    wrap.appendChild(entryCard(cfg));
  } else {
    indicatorsSection();
    // 入场条件（可选）：自己指定股票回测时决定何时买入
    if (!cfg.entry) cfg.entry = { logic: 'all', conditions: [] };
    wrap.appendChild(h(`<div class="section-title">入场条件（可选：自己指定股票回测时决定何时买入，从选股池回测可留空）</div>`));
    wrap.appendChild(entryCard(cfg));
    wrap.appendChild(h(`<div class="section-title">交易规则（${(cfg.rules || []).length} 条）</div>`));
    wrap.appendChild(ruleCard(cfg));

    wrap.appendChild(h(`<div class="section-title">风控</div>`));
    const r = cfg.risk || {};
    wrap.appendChild(h(`<div class="card">
    <div class="field-row">
      <div class="field"><label>止损%</label><input id="f-stop" type="number" step="0.5" placeholder="空=禁用" value="${r.stop_loss_pct ?? ''}"></div>
      <div class="field"><label>移动止损%</label><input id="f-trail" type="number" step="0.5" placeholder="自最高点" value="${r.trailing_stop_pct ?? ''}"></div>
    </div>
    <div class="field-row">
      <div class="field"><label>止盈%</label><input id="f-tp" type="number" step="0.5" placeholder="空=禁用" value="${r.take_profit_pct ?? ''}"></div>
      <div class="field"><label>最长持仓(日)</label><input id="f-hold" type="number" step="1" placeholder="空=禁用" value="${r.max_hold_days ?? ''}"></div>
    </div>
  </div>`));

    const bd = cfg.backtest_defaults || {};
    wrap.appendChild(h(`<div class="section-title">回测默认参数</div>`));
    wrap.appendChild(h(`<div class="card">
    <div class="field-row">
      <div class="field"><label>开始</label><input id="f-bt-start" type="date" value="${esc(bd.start || '')}"></div>
      <div class="field"><label>结束</label><input id="f-bt-end" type="date" value="${esc(bd.end || '')}"></div>
    </div>
    <div class="field-row">
      <div class="field"><label>初始资金</label><input id="f-cash" type="number" value="${bd.initial_cash ?? 1000000}"></div>
      <div class="field"><label>单仓 %</label><input id="f-pos" type="number" value="${bd.position_pct ?? 20}"></div>
      <div class="field"><label>最大持仓数</label><input id="f-maxpos" type="number" value="${bd.max_positions ?? 5}"></div>
    </div>
  </div>`));
  }

  // gather on save-draft click: bind a collector
  const saveBtn = document.getElementById('save-draft');
  const origOnclick = saveBtn.onclick;
  saveBtn.onclick = async (ev) => {
    collectForm(cfg, type);
    await origOnclick(ev);
  };
  return wrap;
}

function collectForm(cfg, type) {
  const num = (id) => {
    const el = document.getElementById(id);
    if (!el) return undefined;
    const v = el.value.trim();
    return v === '' ? null : Number(v);
  };
  cfg.name = document.getElementById('f-name')?.value?.trim() || cfg.name;
  cfg.description = document.getElementById('f-desc')?.value ?? cfg.description;
  if (type === 'screening') {
    // 选股策略：{name, universe, indicators, entry}，entry 保留
    delete cfg.rules;
    delete cfg.exit;
    delete cfg.risk;
    delete cfg.backtest_defaults;
    const t = document.getElementById('f-uni-type');
    if (t) {
      if (t.value === 'custom') cfg.universe = { type: 'custom', codes: (document.getElementById('f-uni-codes').value.match(/[0-9A-Z]+\.(SH|SZ|BJ|TI)/g)) || [] };
      else if (t.value === 'all') cfg.universe = { type: 'all' };
      else if (t.value === 'board') cfg.universe = { type: 'board', board: document.getElementById('f-uni-board').value };
      else cfg.universe = { type: t.value, code: document.getElementById('f-uni-code').value };
    }
  } else {
    // 交易策略：{name, indicators, entry(可选), rules, risk, backtest_defaults}
    delete cfg.exit;
    delete cfg.universe;
    // entry 可选：无任何条件则删除（=None，从选股池回测）
    if (cfg.entry && !(cfg.entry.conditions || []).length) {
      delete cfg.entry;
    }
    if (cfg.risk) {
      cfg.risk.stop_loss_pct = num('f-stop') ?? null;
      cfg.risk.trailing_stop_pct = num('f-trail') ?? null;
      cfg.risk.take_profit_pct = num('f-tp') ?? null;
      cfg.risk.max_hold_days = num('f-hold') ?? null;
    }
    if (cfg.backtest_defaults) {
      const bd = cfg.backtest_defaults;
      if (document.getElementById('f-bt-start')) bd.start = document.getElementById('f-bt-start').value;
      if (document.getElementById('f-bt-end')) bd.end = document.getElementById('f-bt-end').value;
      bd.initial_cash = num('f-cash') ?? bd.initial_cash;
      bd.position_pct = num('f-pos') ?? bd.position_pct;
      bd.max_positions = num('f-maxpos') ?? bd.max_positions;
    }
  }
}

// 入场条件组编辑器：选股策略的 entry（ConditionGroup，可嵌套分组）
function entryCard(cfg) {
  if (!cfg.entry) cfg.entry = { logic: 'all', conditions: [] };
  const card = h('<div class="card" id="entry-list"></div>');
  const render = () => {
    card.innerHTML = '';
    const g = cfg.entry;
    const logicRow = h(`<div class="parts" style="margin-bottom:8px">
      <select data-k="logic">
        <option value="all" ${g.logic === 'all' ? 'selected' : ''}>全部满足</option>
        <option value="any" ${g.logic === 'any' ? 'selected' : ''}>任一满足</option>
      </select>
      <span class="muted" style="font-size:12px">入场信号：${g.logic === 'all' ? '全部条件' : '任一条件'}成立即命中</span>
    </div>`);
    card.appendChild(logicRow);
    const condsEl = h('<div class="entry-conds"></div>');
    card.appendChild(condsEl);
    renderGroupConditions(condsEl, g, cfg);
    logicRow.querySelector('[data-k="logic"]').onchange = (e) => {
      g.logic = e.target.value;
      logicRow.querySelector('.muted').textContent = `入场信号：${g.logic === 'all' ? '全部条件' : '任一条件'}成立即命中`;
    };
  };
  render();
  return card;
}

function universeCard(cfg) {
  const u = cfg.universe || { type: 'index' };
  const card = h(`<div class="card">
    <div class="field"><label>类型</label>
      <select id="f-uni-type">
        <option value="index" ${u.type === 'index' ? 'selected' : ''}>指数成分（如沪深300）</option>
        <option value="sector" ${u.type === 'sector' ? 'selected' : ''}>行业板块成分</option>
        <option value="board" ${u.type === 'board' ? 'selected' : ''}>上市板块（创业板/科创板）</option>
        <option value="custom" ${u.type === 'custom' ? 'selected' : ''}>自选</option>
        <option value="all" ${u.type === 'all' ? 'selected' : ''}>全市场（首次较慢）</option>
      </select>
    </div>
    <div class="field" id="f-uni-code-wrap"><label>范围</label>
      <input id="f-uni-code" type="text" value="${esc(u.code || '000300.SH')}" placeholder="000300.SH">
    </div>
    <div class="field" id="f-uni-board-wrap" style="display:none"><label>上市板块</label>
      <select id="f-uni-board">
        <option value="创业板" ${u.board === '创业板' ? 'selected' : ''}>创业板（全部股票）</option>
        <option value="科创板" ${u.board === '科创板' ? 'selected' : ''}>科创板（全部股票）</option>
      </select>
    </div>
    <div class="field" id="f-uni-codes-wrap" style="display:none"><label>自选代码（逗号分隔）</label>
      <input id="f-uni-codes" type="text" value="${esc((u.codes || []).join(','))}" placeholder="600519.SH,000001.SZ">
    </div>
  </div>`);
  const typeSel = card.querySelector('#f-uni-type');
  const codeWrap = card.querySelector('#f-uni-code-wrap');
  const boardWrap = card.querySelector('#f-uni-board-wrap');
  const codesWrap = card.querySelector('#f-uni-codes-wrap');
  const sync = () => {
    const t = typeSel.value;
    codeWrap.style.display = t === 'index' || t === 'sector' ? '' : 'none';
    boardWrap.style.display = t === 'board' ? '' : 'none';
    codesWrap.style.display = t === 'custom' ? '' : 'none';
  };
  typeSel.onchange = sync;
  sync();
  return card;
}

const FIELD_OPTS = ['open', 'high', 'low', 'close', 'volume'];
const HOLD_FIELDS = ['pnl_pct', 'hold_days', 'dd_from_peak', 'cost'];

// 用户可见的中文标签映射（option 的 value 仍用原始字段名，仅显示文案中文）
const FIELD_LABELS = {
  pnl_pct: '浮盈亏%', hold_days: '持仓天数', dd_from_peak: '距高点回撤%', cost: '成本价',
  open: '开盘价', high: '最高价', low: '最低价', close: '收盘价', volume: '成交量',
};
const HOLD_FIELD_HINTS = {
  pnl_pct: '相对成本价浮盈亏%，"跌5%"即 ≤ -5',
  hold_days: '持仓交易日数',
  dd_from_peak: '距持仓期最高收盘价回撤%（≤0）',
  cost: '加权成本价',
};
const KIND_LABELS = {
  MA: '均线', EMA: '指数均线', PCT_CHANGE: '涨跌幅%', VRATIO: '量比',
  BODY_RATIO: '实体强度', UPPER_SHADOW_RATIO: '上影比例', MA_CONVERGE: '均线粘合', BOX_TOP: '箱体上沿',
  MACD_DIF: 'MACD快线', MACD_DEA: 'MACD慢线', MACD_HIST: 'MACD柱',
  KDJ_K: 'KDJ·K', KDJ_D: 'KDJ·D', KDJ_J: 'KDJ·J', RSI: 'RSI',
  BOLL_UP: '布林上轨', BOLL_MID: '布林中轨', BOLL_LOW: '布林下轨',
};
const PARAM_LABELS = {
  of: '基于', n: '周期', mas: '均线组', fast: '快线', slow: '慢线', signal: '信号线', m1: 'M1', m2: 'M2', k: '标准差倍数',
};
const KIND_OPTS = ['MA', 'PCT_CHANGE', 'VRATIO', 'BODY_RATIO', 'UPPER_SHADOW_RATIO', 'MA_CONVERGE', 'BOX_TOP',
  'EMA', 'MACD_DIF', 'MACD_DEA', 'MACD_HIST', 'KDJ_K', 'KDJ_D', 'KDJ_J', 'RSI', 'BOLL_UP', 'BOLL_MID', 'BOLL_LOW'];
const KIND_PARAMS = {
  MA: ['of', 'n'], PCT_CHANGE: ['of', 'n'], VRATIO: ['n'], BOX_TOP: ['n'],
  BODY_RATIO: [], UPPER_SHADOW_RATIO: [], MA_CONVERGE: ['mas'],
  EMA: ['of', 'n'], RSI: ['of', 'n'],
  MACD_DIF: ['of', 'fast', 'slow', 'signal'], MACD_DEA: ['of', 'fast', 'slow', 'signal'], MACD_HIST: ['of', 'fast', 'slow', 'signal'],
  KDJ_K: ['n', 'm1', 'm2'], KDJ_D: ['n', 'm1', 'm2'], KDJ_J: ['n', 'm1', 'm2'],
  BOLL_UP: ['of', 'n', 'k'], BOLL_MID: ['of', 'n', 'k'], BOLL_LOW: ['of', 'n', 'k'],
};
const IND_PARAM_FIELDS = ['of', 'n', 'mas', 'fast', 'slow', 'signal', 'm1', 'm2', 'k'];

function paramInput(ind, p) {
  if (p === 'of') {
    return `<select data-p="of">${FIELD_OPTS.map((f) => `<option value="${f}" ${f === (ind.of || 'close') ? 'selected' : ''}>${FIELD_LABELS[f] || f}</option>`).join('')}</select>`;
  }
  if (p === 'mas') {
    return `<input type="text" placeholder="ma5,ma10" value="${esc((ind.mas || []).join(','))}" data-p="mas">`;
  }
  const v = ind[p];
  if (p === 'k') {
    return `<input type="number" step="0.5" placeholder="默认2" value="${v ?? ''}" data-p="k" title="标准差倍数，默认 2">`;
  }
  return `<input type="number" placeholder="数值" value="${v ?? ''}" data-p="${p}">`;
}

function indRow(ind, cfg) {
  const row = h(`<div class="cond">
    <div class="ind-head">
      <select data-k="kind" class="ind-kind">${KIND_OPTS.map((k) => `<option value="${k}" ${k === ind.kind ? 'selected' : ''}>${KIND_LABELS[k] || k}</option>`).join('')}</select>
      <button class="del" title="删除此指标">✕</button>
    </div>
    <div class="cond-extras">
      <label class="cond-field"><span>指标名</span><input type="text" value="${esc(ind.id)}" data-k="id" title="指标 id（小写英文，供条件引用）"></label>
    </div>
    <div class="cond-extras" id="ind-param-list"></div>
  </div>`);
  const paramsEl = row.querySelector('#ind-param-list');
  const renderParams = () => {
    paramsEl.innerHTML = '';
    for (const p of KIND_PARAMS[ind.kind] || []) {
      paramsEl.appendChild(h(`<label class="cond-field"><span>${PARAM_LABELS[p] || p}</span>${paramInput(ind, p)}</label>`));
    }
  };
  renderParams();
  row.querySelector('.del').onclick = () => {
    cfg.indicators = cfg.indicators.filter((i) => i !== ind);
    row.remove();
  };
  row.querySelector('[data-k="id"]').onchange = (e) => { ind.id = e.target.value.trim() || ind.id; };
  row.querySelector('[data-k="kind"]').onchange = (e) => {
    ind.kind = e.target.value;
    const keep = new Set(KIND_PARAMS[ind.kind] || []);
    for (const p of IND_PARAM_FIELDS) {
      if (!keep.has(p)) delete ind[p];
    }
    renderParams();
  };
  paramsEl.addEventListener('change', (e) => {
    const p = e.target.dataset.p;
    if (!p) return;
    if (p === 'of') ind.of = e.target.value;
    else if (p === 'mas') ind.mas = e.target.value.split(/[,，\s]+/).filter(Boolean);
    else if (p === 'k') ind.k = e.target.value === '' ? null : Number(e.target.value);
    else ind[p] = e.target.value === '' ? null : Number(e.target.value);
  });
  return row;
}

function ruleCard(cfg) {
  const card = h('<div class="card" id="rule-list"></div>');
  const render = () => {
    card.innerHTML = '';
    const rules = cfg.rules || [];
    rules.forEach((rule) => card.appendChild(ruleRow(rule, cfg, rules)));
    const add = h('<button class="btn sm secondary" style="margin-top:4px">＋ 添加规则</button>');
    add.onclick = () => {
      rules.push({ when: { logic: 'all', conditions: [{ left: 'close', op: '>', right: 0, note: '新条件' }] }, action: 'buy', size_pct: null, max_times: null, note: '' });
      render();
    };
    card.appendChild(add);
  };
  render();
  return card;
}

// 条件组编辑器（可嵌套分组）：ruleRow 的 when 与选股策略的 entry 共用
function renderGroupConditions(condsEl, g, cfg) {
  condsEl.innerHTML = '';
  for (const c of g.conditions) {
    if (c.conditions) {
      const grp = h(`<div class="cond cond-group"><div class="note">组合（${c.logic === 'all' ? '全部满足' : '任一满足'}）${c.note ? ' · ' + esc(c.note) : ''}</div></div>`);
      for (const leaf of c.conditions) grp.appendChild(leafRow(leaf, cfg, c.conditions));
      const delg = h('<button class="del" style="float:right">✕ 删除组</button>');
      delg.onclick = () => { g.conditions = g.conditions.filter((x) => x !== c); renderGroupConditions(condsEl, g, cfg); };
      grp.appendChild(delg);
      condsEl.appendChild(grp);
    } else {
      condsEl.appendChild(leafRow(c, cfg, g.conditions));
    }
  }
  const add = h('<button class="btn sm secondary" style="margin-top:4px">＋ 条件</button>');
  add.onclick = () => { g.conditions.push({ left: 'close', op: '>', right: 0, note: '新条件' }); renderGroupConditions(condsEl, g, cfg); };
  condsEl.appendChild(add);
}

function ruleRow(rule, cfg, rules) {
  const row = h(`<div class="cond cond-group">
    <div class="ind-head">
      <select data-k="action" class="ind-kind">
        <option value="buy" ${rule.action === 'buy' ? 'selected' : ''}>买入（补仓/加仓）</option>
        <option value="sell" ${rule.action === 'sell' ? 'selected' : ''}>卖出（减仓/清仓）</option>
      </select>
      <button class="del" title="删除此规则">✕</button>
    </div>
    <div class="cond-extras">
      <label class="cond-field"><span>仓位%</span><input type="number" placeholder="买=权益% 卖=持仓%" value="${rule.size_pct ?? ''}" data-k="size_pct" title="买=占当前总权益%，卖=占当前持仓%（100=清仓，50=卖一半）"></label>
      <label class="cond-field"><span>最多触发</span><input type="number" placeholder="不限" value="${rule.max_times ?? ''}" data-k="max_times" title="单只股票单次持仓内最多触发次数，空=不限"></label>
    </div>
    <div class="rule-logic">触发条件（${rule.when.logic === 'all' ? '全部满足' : '任一满足'}才执行）</div>
    <div class="rule-conds"></div>
    <div class="cond-note-input"><input type="text" placeholder="备注：对应你的原话（可选）" value="${esc(rule.note || '')}" data-k="note"></div>
  </div>`);
  const condsEl = row.querySelector('.rule-conds');
  renderGroupConditions(condsEl, rule.when, cfg);
  row.querySelector('.del').onclick = () => {
    const i = rules.indexOf(rule);
    if (i >= 0) rules.splice(i, 1);
    row.remove();
  };
  row.querySelector('[data-k="action"]').onchange = (e) => { rule.action = e.target.value; };
  row.querySelector('[data-k="size_pct"]').onchange = (e) => { rule.size_pct = e.target.value === '' ? null : Number(e.target.value); };
  row.querySelector('[data-k="max_times"]').onchange = (e) => { rule.max_times = e.target.value === '' ? null : Number(e.target.value); };
  row.querySelector('[data-k="note"]').onchange = (e) => { rule.note = e.target.value; };
  return row;
}

function leafRow(leaf, cfg, siblings) {
  const ids = [...FIELD_OPTS, ...HOLD_FIELDS, ...cfg.indicators.map((i) => i.id)];
  const labelFor = (id) => {
    if (FIELD_LABELS[id]) return FIELD_LABELS[id];
    const ind = cfg.indicators.find((i) => i.id === id);
    return ind ? `${id}（${KIND_LABELS[ind.kind] || ind.kind}）` : id;
  };
  const titleFor = (id) => HOLD_FIELD_HINTS[id] || '';
  const idOpts = (sel) => ids.map((i) => `<option value="${i}" ${i === sel ? 'selected' : ''} title="${esc(titleFor(i))}">${esc(labelFor(i))}</option>`).join('');
  const opOpts = ['>', '>=', '<', '<=', '=='].map((o) => `<option ${o === leaf.op ? 'selected' : ''}>${o}</option>`).join('');
  const isNumRight = typeof leaf.right === 'number';
  const row = h(`<div class="cond">
    <div class="cond-sentence">
      <span class="cond-word">当</span>
      <select data-k="left">${idOpts(leaf.left)}</select>
      <select data-k="op">${opOpts}</select>
      ${isNumRight
        ? `<input type="number" value="${leaf.right}" data-k="right-num">`
        : `<select data-k="right-sel">${idOpts(leaf.right)}</select>`}
      <button class="del" title="删除此条件">✕</button>
    </div>
    <div class="cond-extras">
      <label class="cond-field"><span>×系数</span><input type="number" value="${leaf.right_factor ?? ''}" data-k="rf" title="右值 × 该系数后再比较"></label>
      <label class="cond-field"><span>最近N日内</span><input type="number" value="${leaf.within ?? ''}" data-k="within" title="最近 N 个交易日内任一天成立"></label>
      <label class="cond-field"><span>前移N日</span><input type="number" value="${leaf.right_lag || ''}" data-k="rlag" title="取值取 N 个交易日前的值"></label>
    </div>
    <div class="cond-note-input"><input type="text" placeholder="备注：对应你的原话（可选）" value="${esc(leaf.note || '')}" data-k="note"></div>
  </div>`);
  row.querySelector('.del').onclick = () => {
    const i = siblings.indexOf(leaf);
    if (i >= 0) siblings.splice(i, 1);
    row.remove();
  };
  row.querySelectorAll('[data-k]').forEach((el) => {
    el.onchange = () => {
      const k = el.dataset.k;
      if (k === 'left') leaf.left = el.value;
      else if (k === 'op') leaf.op = el.value;
      else if (k === 'right-num') leaf.right = el.value === '' ? 0 : Number(el.value);
      else if (k === 'right-sel') leaf.right = el.value;
      else if (k === 'rf') leaf.right_factor = el.value === '' ? null : Number(el.value);
      else if (k === 'within') leaf.within = el.value === '' ? null : Number(el.value);
      else if (k === 'rlag') leaf.right_lag = el.value === '' ? 0 : Number(el.value);
      else if (k === 'note') leaf.note = el.value;
    };
  });
  return row;
}

function refreshReview() {
  const v = document.getElementById('view');
  if (v.dataset.sub === 'review') renderReview(v);
}

function countConds(g) {
  if (!g || !Array.isArray(g.conditions)) return 0;
  return g.conditions.reduce((n, c) => n + (c.conditions ? countConds(c) : 1), 0);
}

// ---------------- detail + versions ----------------
async function renderDetail(view, sid) {
  view.innerHTML = '<div class="spinner"></div>';
  let detail;
  try {
    detail = await api.getStrategy(sid);
  } catch (e) {
    view.innerHTML = `<div class="card"><div class="empty">加载失败：${esc(e.message)}</div></div>`;
    return;
  }
  const cfg = detail.current;
  const isTrading = (detail.type || (cfg.rules && !cfg.entry ? 'trading' : 'screening')) === 'trading';
  const metaChips = isTrading
    ? `<span class="chip">规则 ${(cfg.rules || []).length} 条</span>`
    : `<span class="chip">${esc(cfg.universe?.type === 'all' ? '全市场' : cfg.universe?.code || '自选')}</span><span class="chip">入场 ${countConds(cfg.entry)} 条</span>`;
  view.innerHTML = `
    <div class="seg" id="type-seg"></div>
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <span class="link" style="font-size:14px" id="d-back">‹ 返回列表</span>
        <span class="chip accent">v${detail.version}</span>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:flex-start">
        <div>
          <div style="font-size:18px;font-weight:800">${esc(cfg.name)}</div>
          <div class="muted" style="font-size:13px;margin-top:3px">${esc(cfg.description || '')}</div>
        </div>
      </div>
      <div style="display:flex;gap:6px;margin-top:10px;flex-wrap:wrap">
        <span class="chip">${isTrading ? '交易' : '选股'}</span>
        ${metaChips}
      </div>
      <div style="display:flex;gap:10px;margin-top:14px">
        <button class="btn sm secondary" id="d-edit">编辑（存新版本）</button>
        ${isTrading ? '' : '<button class="btn sm secondary" id="d-screen">去选股</button>'}
        <button class="btn sm danger" id="d-del">删除</button>
      </div>
    </div>
    <div class="section-title">版本历史（可审查 / 恢复）</div>
    <div class="group" id="ver-list"></div>
    <div class="section-title">当前配置 JSON</div>
    <div class="card"><code class="json-box">${esc(JSON.stringify(cfg, null, 2))}</code></div>
  `;
  document.getElementById('d-back').onclick = () => go('list');
  document.getElementById('d-edit').onclick = () => {
    state.draftConfig = JSON.parse(JSON.stringify(cfg));
    view.dataset.stratType = isTrading ? 'trading' : 'screening';
    go('review');
  };
  const screenBtn = document.getElementById('d-screen');
  if (screenBtn) {
    screenBtn.onclick = () => {
      state.screenStrategy = { id: sid, name: cfg.name };
      location.hash = '#/screen';
    };
  }
  document.getElementById('d-del').onclick = async () => {
    if (!confirm(`删除策略「${cfg.name}」？此操作不可恢复。`)) return;
    try {
      await api.deleteStrategy(sid);
      toast('已删除');
      go('list');
    } catch (e) {
      toast(`删除失败：${e.message}`, true);
    }
  };
  const verList = document.getElementById('ver-list');
  for (const v of [...detail.versions].reverse()) {
    const row = h(`<div class="row">
      <span class="lbl">版本 v${v.version} <span class="muted" style="font-size:12px">${esc((v.created_at || '').replace('T', ' '))}</span></span>
      <span style="display:flex;gap:14px">
        <span class="link v-view" style="font-size:14px">查看</span>
        ${v.version !== detail.version ? '<span class="link v-restore" style="font-size:14px">恢复</span>' : ''}
      </span>
    </div>`);
    row.querySelector('.v-view').onclick = async () => {
      const r = await api.getStrategyVersion(sid, v.version);
      showJsonSheet(`版本 v${v.version} 配置`, r.config);
    };
    const restoreBtn = row.querySelector('.v-restore');
    if (restoreBtn) {
      restoreBtn.onclick = async () => {
        if (!confirm(`恢复到 v${v.version}？（将生成新版本）`)) return;
        try {
          const r = await api.restoreStrategyVersion(sid, v.version);
          toast(`已恢复为 v${r.version}`);
          renderDetail(view, sid);
        } catch (e) {
          toast(`恢复失败：${e.message}`, true);
        }
      };
    }
    verList.appendChild(row);
  }
}

export function showJsonSheet(title, obj) {
  const mask = h(`<div class="sheet-mask"><div class="sheet">
    <div class="grabber"></div>
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
      <b>${esc(title)}</b><span class="link" id="sheet-close">关闭</span>
    </div>
    <code class="json-box" style="max-height:52vh;overflow:auto">${esc(JSON.stringify(obj, null, 2))}</code>
  </div></div>`);
  mask.onclick = (e) => { if (e.target === mask) mask.remove(); };
  mask.querySelector('#sheet-close').onclick = () => mask.remove();
  document.body.appendChild(mask);
}
