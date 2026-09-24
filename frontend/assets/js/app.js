// Router + tab bar + view mounting
import { api } from './api.js';
import state, { ensureSettings } from './store.js';
import { toast } from './util.js';
import { renderStrategyView } from './views/strategy.js';
import { renderScreenView } from './views/screen.js';
import { renderBacktestView } from './views/backtest.js';
import { renderChartView } from './views/chart.js';
import { renderSettingsView } from './views/settings.js';

const TABS = [
  { id: 'strategy', label: '策略', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h16M4 12h10M4 19h7"/><circle cx="18" cy="17.5" r="3"/><path d="m20.5 20 1.5 1.5"/></svg>' },
  { id: 'screen', label: '选股', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>' },
  { id: 'backtest', label: '回测', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 20h18"/><path d="M4 16l5-6 4 3 6-8"/></svg>' },
  { id: 'chart', label: '图表', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 6v12M8 6l-3 3M8 6l3 3"/><rect x="13" y="4" width="7" height="7" rx="1"/><rect x="13" y="13" width="7" height="7" rx="1"/></svg>' },
  { id: 'settings', label: '设置', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1 1.55V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-1-1.55 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.7 1.7 0 0 0 .34-1.87 1.7 1.7 0 0 0-1.55-1H3a2 2 0 1 1 0-4h.09a1.7 1.7 0 0 0 1.55-1 1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 1.87.34h.09a1.7 1.7 0 0 0 1-1.55V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1 1.55h.09a1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87v.09a1.7 1.7 0 0 0 1.55 1H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.55 1Z"/></svg>' },
];

const TITLES = {
  strategy: ['策略', '自然语言 · 量化配置 · 版本审查'],
  screen: ['选股', '按策略扫描股票池'],
  backtest: ['回测', '胜率 · 回撤 · 净值曲线'],
  chart: ['图表', 'K线 · 量能分析'],
  settings: ['设置', '数据源与 AI 配置'],
};

function currentTab() {
  const hash = location.hash.replace(/^#\/?/, '') || 'strategy';
  return hash.split('?')[0].split('/')[0] || 'strategy';
}

function buildTabbar() {
  const bar = document.getElementById('tabbar');
  bar.innerHTML = '';
  for (const t of TABS) {
    const b = document.createElement('button');
    b.dataset.tab = t.id;
    b.innerHTML = `${t.icon}<span>${t.label}</span>`;
    b.onclick = () => { location.hash = `#/${t.id}`; };
    bar.appendChild(b);
  }
}

function setActive() {
  const tab = currentTab();
  document.querySelectorAll('.tabbar button').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
  const [title, sub] = TITLES[tab] || TITLES.strategy;
  const header = document.getElementById('nav-header');
  header.innerHTML = `<h1>${title}</h1><div class="sub">${sub}</div>`;
}

const routes = {
  strategy: renderStrategyView,
  screen: renderScreenView,
  backtest: renderBacktestView,
  chart: renderChartView,
  settings: renderSettingsView,
};

async function render() {
  setActive();
  const tab = currentTab();
  const view = document.getElementById('view');
  view.classList.remove('chat-mode');
  view.innerHTML = '<div class="spinner"></div>';
  try {
    await ensureSettings(api);
    await routes[tab](view);
  } catch (e) {
    view.innerHTML = `<div class="card"><div class="empty"><p>加载失败：${e.message || e}</p><p style="margin-top:12px"><button class="btn sm secondary" onclick="location.reload()">重新加载</button></p></div></div>`;
  }
}

document.getElementById('view').addEventListener('rerender', () => render());

export function navigate(tab, query = '') {
  location.hash = `#/${tab}${query}`;
}

export function refresh() { render(); }

window.addEventListener('hashchange', render);
window.addEventListener('error', (e) => { if (e.message) console.warn(e.message); });

buildTabbar();
render();
