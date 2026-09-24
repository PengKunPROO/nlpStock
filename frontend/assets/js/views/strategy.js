// 策略 view: list / chat alignment / review editor / version history
import { api } from '../api.js';
import state from '../store.js';
import { esc, h, toast } from '../util.js';

const EXAMPLE_TEXT = '1，阴跌之后等急跌，\n2，急跌之后等止跌，(均线拧到一块是止跌信号)\n3，止跌之后等反转(底部放量，阳线实体越来越大，价格站上关键均线才是反转信号)，\n4，反转之后等进场(拉一波再缩量回踩不破前期箱体上沿，确认支撑有效，说明主力锁仓，这时候才可以进)，\n5，力竭出现，因为量能跟不上，出现上影线，越来越短的阳线，都是力竭信号\n6，力竭后的离场，不舍得卖啊，这时落袋才是利润，\n7，离场之后等待回落，千万别追，\n8，回调支撑如果被击穿就不要进了。';

let alignMessages = []; // 对齐对话历史（模块级，切tab保留）

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
  for (const [k, val] of Object.entries(extra)) v.dataset[k] = val;
  v.dispatchEvent(new CustomEvent('rerender'));
}

// ---------------- list ----------------
async function renderList(view) {
  view.innerHTML = '<div class="spinner"></div>';
  let items = [];
  try {
    items = (await api.listStrategies()).items;
  } catch (e) {
    view.innerHTML = `<div class="card"><div class="empty">加载失败：${esc(e.message)}</div></div>`;
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
        <span class="chip">入场 ${s.entry_count} 条</span>
        <span class="chip">离场 ${s.exit_count} 条</span>
        <span class="chip">${esc((s.updated_at || '').slice(0, 10))}</span>
      </div>
    </div>`).join('');
  view.innerHTML = `
    <button class="btn" id="new-strategy" style="margin-bottom:14px">＋ 新建策略（自然语言）</button>
    ${items.length ? cards : '<div class="card"><div class="empty">还没有策略。<br>用一句自然语言描述你的交易思路，AI 会帮你量化。</div></div>'}`;
  document.getElementById('new-strategy').onclick = () => { alignMessages = []; go('new'); };
  view.querySelectorAll('[data-sid]').forEach((el) => {
    el.onclick = () => go('detail', { sid: el.dataset.sid });
  });
}

// ---------------- chat alignment ----------------
function renderChat(view) {
  view.innerHTML = `
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
        <span class="chip accent">AI 对齐</span>
        <span class="link" style="font-size:13px" id="back-list">← 返回列表</span>
      </div>
      <div class="hint">用自然语言描述你的策略。AI 会先复述它的量化理解，并向你追问模糊点（均线、阈值、止损等），对齐完成后生成可审查的量化配置。</div>
    </div>
    <div class="chat" id="chat-list"></div>
    <div class="chat-input">
      <textarea id="chat-input" placeholder="描述你的策略，例如：阴跌之后等急跌……"></textarea>
      <button class="send-btn" id="chat-send" title="发送">↑</button>
    </div>
    <div style="text-align:center;margin-top:8px"><span class="chip" style="cursor:pointer" id="fill-example">填入示例策略</span></div>
  `;
  document.getElementById('back-list').onclick = () => go('list');
  document.getElementById('fill-example').onclick = () => {
    document.getElementById('chat-input').value = EXAMPLE_TEXT;
  };
  const list = document.getElementById('chat-list');
  const input = document.getElementById('chat-input');
  const sendBtn = document.getElementById('chat-send');

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
    for (const m of alignMessages) {
      if (m.role === 'user') appendBubble('me', esc(m.content));
      else appendBubble('ai', m.html || esc(m.content));
    }
    if (!alignMessages.length) {
      appendBubble('ai', '你好，我是策略量化助手。<br>请描述你的交易策略 —— 越具体越好（哪些均线、大致幅度、止损偏好等）。也可以直接点下方「填入示例策略」。');
    }
  };
  renderHistory();

  const send = async () => {
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    alignMessages.push({ role: 'user', content: text });
    appendBubble('me', esc(text));
    sendBtn.disabled = true;
    let typing;
    try {
      typing = showTyping();
      const resp = await api.parseStrategy(alignMessages.map(({ role, content }) => ({ role, content })));
      if (typing) typing.remove();
      if (resp.type === 'clarify') {
        const qHtml = `<div class="q-title">当前量化理解</div>${esc(resp.understanding || '')}<div class="q-title" style="margin-top:8px">请确认 ${resp.round}/4</div><ol>${resp.questions.map((q) => `<li>${esc(q)}</li>`).join('')}</ol>`;
        alignMessages.push({ role: 'assistant', content: `${resp.understanding}\n${resp.questions.join('\n')}`, html: qHtml });
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
      alignMessages.pop(); // 移除本轮用户消息以便重发
      input.value = text;
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
  let jsonMode = false;
  view.innerHTML = `
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <span class="chip accent">审查 · ${cfg.parse_engine === 'llm' ? 'AI 生成' : '草稿'}</span>
        <span class="link" style="font-size:13px" id="review-json-toggle">JSON 模式</span>
      </div>
      <div class="hint">以下是 AI 量化的策略配置。<b>每条条件旁的灰字是对应你的原话</b>，数字均可修改；确认无误后保存。保存后仍可继续修改（自动存版本）。</div>
    </div>
    <div id="review-body"></div>
    <button class="btn" id="save-draft" style="margin-top:4px">保存策略</button>
  `;

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
    body.appendChild(buildForm(cfg));
  };
  document.getElementById('review-json-toggle').onclick = () => { jsonMode = !jsonMode; renderBody(); };
  renderBody();

  document.getElementById('save-draft').onclick = async (ev) => {
    ev.target.disabled = true;
    try {
      await api.createStrategy(state.draftConfig);
      toast('策略已保存');
      state.draftConfig = null;
      go('list');
    } catch (e) {
      toast(`保存失败：${e.message}${e.raw ? '（' + String(e.raw).slice(0, 120) + '）' : ''}`, true);
      ev.target.disabled = false;
    }
  };
}

function buildForm(cfg) {
  const wrap = h('<div></div>');
  const info = h(`<div class="card">
    <div class="field"><label>策略名称</label><input id="f-name" type="text" value="${esc(cfg.name)}"></div>
    <div class="field"><label>描述</label><textarea id="f-desc">${esc(cfg.description || '')}</textarea></div>
  </div>`);
  wrap.appendChild(info);

  wrap.appendChild(h(`<div class="section-title">股票池</div>`));
  wrap.appendChild(universeCard(cfg));

  wrap.appendChild(h(`<div class="section-title">指标（${cfg.indicators.length}）</div>`));
  const indCard = h('<div class="card" id="ind-list"></div>');
  for (const ind of cfg.indicators) {
    indCard.appendChild(indRow(ind, cfg));
  }
  wrap.appendChild(indCard);

  wrap.appendChild(h(`<div class="section-title">入场条件（全部满足才选出）</div>`));
  wrap.appendChild(condCard(cfg, 'entry'));
  wrap.appendChild(h(`<div class="section-title">离场条件（任一满足即离场）</div>`));
  wrap.appendChild(condCard(cfg, 'exit'));

  wrap.appendChild(h(`<div class="section-title">风控</div>`));
  const r = cfg.risk || {};
  wrap.appendChild(h(`<div class="card">
    <div class="field-row">
      <div class="field"><label>止损 %（空=禁用）</label><input id="f-stop" type="number" step="0.5" value="${r.stop_loss_pct ?? ''}"></div>
      <div class="field"><label>止盈 %（空=禁用）</label><input id="f-tp" type="number" step="0.5" value="${r.take_profit_pct ?? ''}"></div>
      <div class="field"><label>最长持仓（日，空=禁用）</label><input id="f-hold" type="number" step="1" value="${r.max_hold_days ?? ''}"></div>
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

  // gather on save-draft click: bind a collector
  const saveBtn = document.getElementById('save-draft');
  const origOnclick = saveBtn.onclick;
  saveBtn.onclick = async (ev) => {
    collectForm(cfg);
    await origOnclick(ev);
  };
  return wrap;
}

function collectForm(cfg) {
  const num = (id) => {
    const el = document.getElementById(id);
    if (!el) return undefined;
    const v = el.value.trim();
    return v === '' ? null : Number(v);
  };
  cfg.name = document.getElementById('f-name')?.value?.trim() || cfg.name;
  cfg.description = document.getElementById('f-desc')?.value ?? cfg.description;
  const t = document.getElementById('f-uni-type');
  if (t) {
    if (t.value === 'custom') cfg.universe = { type: 'custom', codes: (document.getElementById('f-uni-codes').value.match(/[0-9A-Z]+\.(SH|SZ|BJ|TI)/g)) || [] };
    else if (t.value === 'all') cfg.universe = { type: 'all' };
    else cfg.universe = { type: t.value, code: document.getElementById('f-uni-code').value };
  }
  if (cfg.risk) {
    cfg.risk.stop_loss_pct = num('f-stop') ?? null;
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

function universeCard(cfg) {
  const u = cfg.universe || { type: 'index' };
  const card = h(`<div class="card">
    <div class="field"><label>类型</label>
      <select id="f-uni-type">
        <option value="index" ${u.type === 'index' ? 'selected' : ''}>指数成分（如沪深300）</option>
        <option value="sector" ${u.type === 'sector' ? 'selected' : ''}>行业板块成分</option>
        <option value="custom" ${u.type === 'custom' ? 'selected' : ''}>自选</option>
        <option value="all" ${u.type === 'all' ? 'selected' : ''}>全市场（首次较慢）</option>
      </select>
    </div>
    <div class="field" id="f-uni-code-wrap"><label>范围</label>
      <input id="f-uni-code" type="text" value="${esc(u.code || '000300.SH')}" placeholder="000300.SH">
    </div>
    <div class="field" id="f-uni-codes-wrap" style="display:none"><label>自选代码（逗号分隔）</label>
      <input id="f-uni-codes" type="text" value="${esc((u.codes || []).join(','))}" placeholder="600519.SH,000001.SZ">
    </div>
  </div>`);
  const typeSel = card.querySelector('#f-uni-type');
  const codeWrap = card.querySelector('#f-uni-code-wrap');
  const codesWrap = card.querySelector('#f-uni-codes-wrap');
  const sync = () => {
    const t = typeSel.value;
    codeWrap.style.display = t === 'index' || t === 'sector' ? '' : 'none';
    codesWrap.style.display = t === 'custom' ? '' : 'none';
  };
  typeSel.onchange = sync;
  sync();
  return card;
}

const FIELD_OPTS = ['open', 'high', 'low', 'close', 'volume'];
const KIND_OPTS = ['MA', 'PCT_CHANGE', 'VRATIO', 'BODY_RATIO', 'UPPER_SHADOW_RATIO', 'MA_CONVERGE', 'BOX_TOP'];

function indRow(ind, cfg) {
  const ids = ['open', 'high', 'low', 'close', 'volume', ...cfg.indicators.map((i) => i.id)];
  const row = h(`<div class="cond">
    <div class="parts">
      <input type="text" value="${esc(ind.id)}" style="min-width:70px" data-k="id" title="指标id">
      <select data-k="kind">${KIND_OPTS.map((k) => `<option ${k === ind.kind ? 'selected' : ''}>${k}</option>`).join('')}</select>
      <select data-k="of">${FIELD_OPTS.map((f) => `<option ${f === ind.of ? 'selected' : ''}>${f}</option>`).join('')}</select>
      <input type="number" placeholder="n" value="${ind.n ?? ''}" style="min-width:52px" data-k="n">
      <input type="text" placeholder="mas,逗号分隔" value="${esc((ind.mas || []).join(','))}" style="min-width:90px" data-k="mas" ${ind.kind === 'MA_CONVERGE' ? '' : 'style="min-width:90px;display:none"'}>
      <button class="del" title="删除">✕</button>
    </div>
  </div>`);
  row.querySelector('.del').onclick = () => {
    cfg.indicators = cfg.indicators.filter((i) => i !== ind);
    row.remove();
  };
  row.querySelectorAll('[data-k]').forEach((el) => {
    el.onchange = () => {
      const k = el.dataset.k;
      if (k === 'n') ind.n = el.value === '' ? null : Number(el.value);
      else if (k === 'mas') ind.mas = el.value.split(/[,，\s]+/).filter(Boolean);
      else ind[k] = el.value;
      if (k === 'kind') { refreshReview(); }
    };
  });
  return row;
}

function condCard(cfg, key) {
  const card = h('<div class="card" id="cond-card"></div>');
  const renderConds = () => {
    card.innerHTML = '';
    const group = cfg[key];
    for (const c of group.conditions) {
      if (c.conditions) {
        // nested group — read-only block with editable note + children leaves
        const g = h(`<div class="cond cond-group">
          <div class="note">组合（${c.logic === 'all' ? '全部满足' : '任一满足'}）${c.note ? ' · ' + esc(c.note) : ''}</div>
        </div>`);
        for (const leaf of c.conditions) g.appendChild(leafRow(leaf, cfg, c.conditions));
        const del = h('<button class="del" style="float:right">✕ 删除组</button>');
        del.onclick = () => { group.conditions = group.conditions.filter((x) => x !== c); renderConds(); };
        g.appendChild(del);
        card.appendChild(g);
      } else {
        card.appendChild(leafRow(c, cfg, group.conditions));
      }
    }
    const add = h('<button class="btn sm secondary" style="margin-top:4px">＋ 添加条件</button>');
    add.onclick = () => {
      group.conditions.push({ left: 'close', op: '>', right: 0, note: '新条件' });
      renderConds();
    };
    card.appendChild(add);
  };
  renderConds();
  return card;
}

function leafRow(leaf, cfg, siblings) {
  const ids = [...FIELD_OPTS, ...cfg.indicators.map((i) => i.id)];
  const idOpts = (sel) => ids.map((i) => `<option value="${i}" ${i === sel ? 'selected' : ''}>${i}</option>`).join('');
  const opOpts = ['>', '>=', '<', '<=', '=='].map((o) => `<option ${o === leaf.op ? 'selected' : ''}>${o}</option>`).join('');
  const isNumRight = typeof leaf.right === 'number';
  const row = h(`<div class="cond">
    <div class="note">${esc(leaf.note || '（无原文标注）')}</div>
    <div class="parts">
      <select data-k="left">${idOpts(leaf.left)}</select>
      <select data-k="op">${opOpts}</select>
      ${isNumRight
        ? `<input type="number" value="${leaf.right}" data-k="right-num" style="min-width:70px">`
        : `<select data-k="right-sel">${idOpts(leaf.right)}</select>`}
      <input type="number" placeholder="×系数" value="${leaf.right_factor ?? ''}" style="min-width:56px" data-k="rf" title="right×该系数">
      <input type="number" placeholder="回看" value="${leaf.within ?? ''}" style="min-width:56px" data-k="within" title="最近N日内任一天成立">
      <input type="number" placeholder="lag" value="${leaf.right_lag || ''}" style="min-width:46px" data-k="rlag" title="右值取N日前">
      <button class="del">✕</button>
    </div>
    <div style="margin-top:6px"><input type="text" placeholder="备注（原文依据）" value="${esc(leaf.note || '')}" data-k="note" style="font-size:13px"></div>
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
  view.innerHTML = `
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:flex-start">
        <div>
          <div style="font-size:18px;font-weight:800">${esc(cfg.name)}</div>
          <div class="muted" style="font-size:13px;margin-top:3px">${esc(cfg.description || '')}</div>
        </div>
        <span class="chip accent">v${detail.version}</span>
      </div>
      <div style="display:flex;gap:6px;margin-top:10px;flex-wrap:wrap">
        <span class="chip">${esc(cfg.universe.type === 'all' ? '全市场' : cfg.universe.code || '自选')}</span>
        <span class="chip">入场 ${cfg.entry.conditions.length}</span>
        <span class="chip">离场 ${cfg.exit.conditions.length}</span>
      </div>
      <div style="display:flex;gap:10px;margin-top:14px">
        <button class="btn sm secondary" id="d-edit">编辑（存新版本）</button>
        <button class="btn sm secondary" id="d-screen">去选股</button>
        <button class="btn sm danger" id="d-del">删除</button>
      </div>
    </div>
    <div class="section-title">版本历史（可审查 / 恢复）</div>
    <div class="group" id="ver-list"></div>
    <div class="section-title">当前配置 JSON</div>
    <div class="card"><code class="json-box">${esc(JSON.stringify(cfg, null, 2))}</code></div>
  `;
  document.getElementById('d-edit').onclick = () => {
    state.draftConfig = JSON.parse(JSON.stringify(cfg));
    go('review');
  };
  document.getElementById('d-screen').onclick = () => {
    state.screenStrategy = { id: sid, name: cfg.name };
    location.hash = '#/screen';
  };
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
