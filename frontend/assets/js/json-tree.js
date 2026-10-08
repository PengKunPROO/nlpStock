// 可编辑 JSON 树组件：递归渲染对象/数组/叶子，类型感知输入 + 枚举下拉 + 折叠（渐进披露）+ 中文标签。
// 用于策略配置详情页：把「原始 JSON」变成每个人都能看懂、可逐 key 编辑的交互式界面。
import { h, esc } from './util.js';

// key → 中文标签（超长/英文 key 统一映射为短中文，无映射则显示原始 key 并截断）
const KEY_LABELS = {
  // 顶层
  name: '策略名称', description: '描述', parse_engine: '解析引擎',
  universe: '股票池', indicators: '指标', entry: '入场条件', exit: '离场条件',
  rules: '交易规则', risk: '风控', backtest_defaults: '回测默认参数',
  // universe
  type: '类型', code: '代码', board: '板块', codes: '代码列表',
  // indicator
  id: '指标名', kind: '指标类型', of: '基于', n: '周期', mas: '均线组',
  fast: '快线', slow: '慢线', signal: '信号线', m1: 'M1', m2: 'M2', k: '标准差倍数',
  // condition
  logic: '逻辑', conditions: '条件', left: '左值', op: '运算符', right: '右值', note: '说明',
  lag: '前移(日)', right_lag: '右值前移(日)', within: '回看(日)', factor: '系数', right_factor: '右值系数',
  // rule
  when: '触发条件', action: '动作', size_pct: '仓位%', max_times: '最大次数',
  // risk
  stop_loss_pct: '止损%', trailing_stop_pct: '移动止损%', take_profit_pct: '止盈%', max_hold_days: '最长持仓(日)',
  // backtest_defaults
  start: '开始', end: '结束', initial_cash: '初始资金', position_pct: '单仓%', max_positions: '最大持仓数',
};

// 某些 key 的值用枚举下拉（更友好、防拼写错误）；值不在枚举内则回退为文本输入
const VALUE_ENUMS = {
  action: { buy: '买入/补仓', sell: '卖出' },
  logic: { all: '全部满足', any: '任一满足' },
  op: { '>': '大于 >', '>=': '大于等于 ≥', '<': '小于 <', '<=': '小于等于 ≤', '==': '等于 =' },
  of: { open: '开盘价', high: '最高价', low: '最低价', close: '收盘价', volume: '成交量' },
  type: { index: '指数成分', sector: '行业板块', board: '上市板块', custom: '自选', all: '全市场' },
  kind: {
    MA: '均线', EMA: '指数均线', PCT_CHANGE: '涨跌幅%', VRATIO: '量比',
    BODY_RATIO: '实体强度', UPPER_SHADOW_RATIO: '上影比例', MA_CONVERGE: '均线粘合', BOX_TOP: '箱体上沿',
    MACD_DIF: 'MACD快线', MACD_DEA: 'MACD慢线', MACD_HIST: 'MACD柱',
    KDJ_K: 'KDJ·K', KDJ_D: 'KDJ·D', KDJ_J: 'KDJ·J', RSI: 'RSI',
    BOLL_UP: '布林上轨', BOLL_MID: '布林中轨', BOLL_LOW: '布林下轨',
  },
};

export function renderJsonTree(el, value, opts = {}) {
  const labels = { ...KEY_LABELS, ...(opts.labels || {}) };
  const enums = { ...VALUE_ENUMS, ...(opts.enums || {}) };
  el.innerHTML = '';
  if (value === null || typeof value !== 'object') {
    el.appendChild(renderLeaf('', value, null, enums, labels));
    return;
  }
  el.appendChild(renderObject(value, labels, enums, 0));
}

function labelText(key, labels) {
  return labels[key] || key;
}

// 元数据字段（非策略配置，编辑树中过滤，避免用户误改版本号/时间戳）
const META_KEYS = new Set(['id', 'version', 'updated_at', 'created_at']);

function renderObject(obj, labels, enums, depth) {
  const wrap = h('<div class="jt-obj"></div>');
  for (const [k, v] of Object.entries(obj)) {
    if (META_KEYS.has(k)) continue;
    if (v !== null && typeof v === 'object') {
      // 对象/数组 → 独立折叠 section
      wrap.appendChild(renderSection(k, v, obj, labels, enums, depth));
    } else {
      wrap.appendChild(renderLeafRow(k, v, obj, labels, enums));
    }
  }
  return wrap;
}

function renderLeafRow(key, value, parent, labels, enums) {
  const row = h('<div class="jt-row"></div>');
  row.appendChild(h(`<span class="jt-key" title="${esc(key)}">${esc(labelText(key, labels))}</span>`));
  row.appendChild(renderLeaf(key, value, parent, enums, labels));
  return row;
}

function renderSection(key, value, parent, labels, enums, depth) {
  const isArr = Array.isArray(value);
  const title = labelText(key, labels);
  const summary = isArr ? `${value.length} 项` : '';
  // 对象默认展开（字段少），数组默认折叠（元素可能多）
  const defaultOpen = !isArr;
  const sec = h(`<div class="jt-sec">
    <div class="jt-head"><span class="jt-caret">${defaultOpen ? '▾' : '▸'}</span><b>${esc(title)}</b>${summary ? `<span class="muted" style="font-size:12px">${summary}</span>` : ''}</div>
    <div class="jt-body" style="${defaultOpen ? '' : 'display:none'}"></div>
  </div>`);
  const head = sec.querySelector('.jt-head');
  const body = sec.querySelector('.jt-body');
  const caret = sec.querySelector('.jt-caret');
  head.onclick = () => {
    const open = body.style.display !== 'none';
    body.style.display = open ? 'none' : '';
    caret.textContent = open ? '▸' : '▾';
  };
  if (isArr) {
    value.forEach((item, i) => {
      if (item !== null && typeof item === 'object') {
        body.appendChild(renderArrayItem(item, i, value, labels, enums, depth + 1));
      } else {
        const row = h('<div class="jt-row"></div>');
        row.appendChild(h(`<span class="jt-key">#${i + 1}</span>`));
        row.appendChild(renderLeaf(String(i), item, value, enums, labels));
        body.appendChild(row);
      }
    });
  } else {
    body.appendChild(renderObject(value, labels, enums, depth + 1));
  }
  return sec;
}

function renderArrayItem(item, i, parent, labels, enums, depth) {
  const title = summarize(item, i);
  const sec = h(`<div class="jt-sec jt-item">
    <div class="jt-head"><span class="jt-caret">▸</span><span>${esc(title)}</span></div>
    <div class="jt-body" style="display:none"></div>
  </div>`);
  const head = sec.querySelector('.jt-head');
  const body = sec.querySelector('.jt-body');
  const caret = sec.querySelector('.jt-caret');
  head.onclick = () => {
    const open = body.style.display !== 'none';
    body.style.display = open ? 'none' : '';
    caret.textContent = open ? '▸' : '▾';
  };
  body.appendChild(renderObject(item, labels, enums, depth + 1));
  return sec;
}

// 数组元素摘要：提取可辨识字段做标题，让折叠态也能看懂每一项
function summarize(item, i) {
  const parts = [];
  if (item.kind) parts.push(item.kind);
  if (item.id) parts.push(item.id);
  if (item.name) parts.push(item.name);
  if (item.action) parts.push(item.action === 'buy' ? '买入' : '卖出');
  if (item.left) parts.push(`${item.left ?? ''} ${item.op ?? ''} ${item.right ?? ''}`.trim());
  return parts.join(' · ') || `第 ${i + 1} 项`;
}

function renderLeaf(key, value, parent, enums, labels) {
  // 1) 枚举下拉
  if (key && enums[key] && typeof value === 'string') {
    const opts = Object.entries(enums[key]);
    const known = opts.some(([v]) => v === value);
    if (known) {
      const sel = h(`<select>${opts.map(([v, l]) => `<option value="${esc(v)}" ${v === value ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select>`);
      sel.onchange = () => { parent[key] = sel.value; };
      return sel;
    }
    // 未知值（如 LLM 生成的非标枚举）：下拉 + 自定义选项保留原值
    const sel = h(`<select><option value="${esc(value)}" selected>${esc(value)}</option>${opts.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('')}</select>`);
    sel.onchange = () => { parent[key] = sel.value; };
    return sel;
  }
  // 2) 布尔 → 开关
  if (typeof value === 'boolean') {
    const lab = h(`<label class="jt-toggle"><input type="checkbox" ${value ? 'checked' : ''}><span class="jt-slider"></span></label>`);
    lab.querySelector('input').onchange = (e) => { parent[key] = e.target.checked; };
    return lab;
  }
  // 3) 数字
  if (typeof value === 'number') {
    const input = h(`<input type="number" value="${value}">`);
    input.onchange = () => { parent[key] = input.value === '' ? null : Number(input.value); };
    return input;
  }
  // 4) null → 空标签，点击变输入
  if (value === null) {
    const tag = h('<span class="jt-null">空</span>');
    tag.onclick = () => {
      const input = h('<input type="text" placeholder="输入值，留空=空">');
      const commit = () => {
        const v = input.value.trim();
        parent[key] = v === '' ? null : (/^-?\d+(\.\d+)?$/.test(v) ? Number(v) : v);
        tag.replaceWith(renderLeaf(key, parent[key], parent, enums, labels));
      };
      input.onblur = commit;
      input.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); input.blur(); } };
      tag.replaceWith(input);
      input.focus();
    };
    return tag;
  }
  // 5) 字符串
  const input = h(`<input type="text" value="${esc(value)}">`);
  input.onchange = () => { parent[key] = input.value; };
  return input;
}
