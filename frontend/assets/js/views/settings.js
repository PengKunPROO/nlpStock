// 设置 view: 扶摇 key / DeepSeek LLM / 默认股票池 / 缓存状态
import { api } from '../api.js';
import state from '../store.js';
import { esc, h, toast } from '../util.js';

export async function renderSettingsView(view) {
  const s = state.settings || (await api.getSettings());
  state.settings = s;
  const opts = await api.universeOptions().catch(() => ({ indices: [], sectors: [] }));

  view.innerHTML = `
    <div class="group">
      <div class="row"><span class="lbl">服务状态</span><span class="val" id="health-line">检查中…</span></div>
      <div class="row"><span class="lbl">标的缓存</span><span class="val" id="cache-line">—</span></div>
    </div>

    <div class="section-title">数据源 · 扶摇（同花顺）</div>
    <div class="card">
      <div class="field">
        <label>API Key ${s.fuyao_api_key_set ? '<span class="chip accent" style="margin-left:6px">已配置</span>' : '<span class="chip warn" style="margin-left:6px">未配置</span>'}</label>
        <input id="set-fuyao-key" type="password" placeholder="${s.fuyao_api_key_set ? '已配置（输入以更换）' : 'sk-...'}" autocomplete="off">
      </div>
      <div class="hint">行情、K线与板块数据来源。已在 fuyao.aicubes.cn 签发的 Key。</div>
    </div>

    <div class="section-title">AI 策略解析 · DeepSeek</div>
    <div class="card">
      <div class="field">
        <label>Base URL（任意 OpenAI 兼容接口）</label>
        <input id="set-llm-url" type="text" value="${esc(s.llm_base_url || '')}" placeholder="https://api.deepseek.com">
      </div>
      <div class="field">
        <label>API Key ${s.llm_api_key_set ? '<span class="chip accent" style="margin-left:6px">已配置</span>' : '<span class="chip warn" style="margin-left:6px">未配置</span>'}</label>
        <input id="set-llm-key" type="password" placeholder="${s.llm_api_key_set ? '已配置（输入以更换）' : 'sk-...'}" autocomplete="off">
      </div>
      <div class="field">
        <label>模型</label>
        <input id="set-llm-model" type="text" value="${esc(s.llm_model || 'deepseek-chat')}" placeholder="deepseek-chat">
      </div>
      <div class="hint">自然语言策略 → 量化配置的解析引擎。支持 DeepSeek 或任意 OpenAI 兼容端点（智谱 / Kimi / 自建均可）。未配置时策略解析不可用，其余功能不受影响。</div>
    </div>

    <div class="section-title">默认股票池</div>
    <div class="card">
      <div class="field">
        <label>类型</label>
        <select id="set-uni-type">
          <option value="index">指数成分</option>
          <option value="sector">行业板块</option>
          <option value="custom">自选</option>
          <option value="all">全市场</option>
        </select>
      </div>
      <div class="field" id="set-uni-code-field">
        <label>范围</label>
        <select id="set-uni-code"></select>
      </div>
      <div class="field" id="set-uni-codes-field" style="display:none">
        <label>自选代码（逗号分隔 thscode）</label>
        <input id="set-uni-codes" type="text" placeholder="600519.SH,000001.SZ">
      </div>
    </div>

    <button class="btn" id="save-settings">保存设置</button>
  `;

  api.health().then((hh) => {
    const line = document.getElementById('health-line');
    if (line) line.innerHTML = hh.ok ? '<span class="chip down">运行中</span>' : '<span class="chip up">异常</span>';
    const cache = document.getElementById('cache-line');
    if (cache) cache.textContent = hh.time ? `已同步 · ${String(hh.time).slice(0, 16).replace('T', ' ')}` : '未同步';
  }).catch(() => {
    const line = document.getElementById('health-line');
    if (line) line.textContent = '不可用';
  });

  const typeSel = document.getElementById('set-uni-type');
  const codeSel = document.getElementById('set-uni-code');
  const codeField = document.getElementById('set-uni-code-field');
  const codesField = document.getElementById('set-uni-codes-field');
  const uni = s.default_universe || { type: 'index' };
  typeSel.value = uni.type;
  const fillCodes = () => {
    const list = typeSel.value === 'sector' ? opts.sectors : opts.indices;
    codeSel.innerHTML = '';
    for (const it of list) {
      const o = h(`<option value="${esc(it.code)}">${esc(it.name)}（${esc(it.code)}）</option>`);
      codeSel.appendChild(o);
    }
    if (uni.code && [...codeSel.options].some((o) => o.value === uni.code)) codeSel.value = uni.code;
  };
  fillCodes();
  const syncFields = () => {
    const t = typeSel.value;
    codeField.style.display = t === 'index' || t === 'sector' ? '' : 'none';
    codesField.style.display = t === 'custom' ? '' : 'none';
    if (t === 'sector' || t === 'index') fillCodes();
  };
  typeSel.onchange = syncFields;
  syncFields();
  if (uni.type === 'custom' && uni.codes) document.getElementById('set-uni-codes').value = uni.codes.join(',');

  document.getElementById('save-settings').onclick = async () => {
    const payload = {};
    const fk = document.getElementById('set-fuyao-key').value.trim();
    if (fk) payload.fuyao_api_key = fk;
    const lk = document.getElementById('set-llm-key').value.trim();
    if (lk) payload.llm_api_key = lk;
    payload.llm_base_url = document.getElementById('set-llm-url').value.trim();
    payload.llm_model = document.getElementById('set-llm-model').value.trim();
    const t = typeSel.value;
    if (t === 'custom') {
      const codes = document.getElementById('set-uni-codes').value.split(/[,，\s]+/).filter(Boolean);
      payload.default_universe = { type: 'custom', codes };
    } else if (t === 'all') {
      payload.default_universe = { type: 'all' };
    } else {
      payload.default_universe = { type: t, code: codeSel.value };
    }
    try {
      state.settings = await api.updateSettings(payload);
      toast('已保存');
    } catch (e) {
      toast(`保存失败：${e.message}`, true);
    }
  };
}
