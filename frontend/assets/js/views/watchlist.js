// 自选股管理：分组（tab 切换/新建/重命名/删除）+ 股票（搜索添加/删除）
import { api } from '../api.js';
import state from '../store.js';
import { esc, h, toast, debounce } from '../util.js';

let currentGroupId = null;

export async function renderWatchlistView(view) {
  let groups = [];
  try {
    groups = (await api.watchlistGroups()).items;
  } catch (e) {
    view.innerHTML = `<div class="card"><div class="empty">加载失败：${esc(e.message)}</div></div>`;
    return;
  }
  if (!groups.length) {
    view.innerHTML = `
      <div class="card"><div class="empty">还没有自选股分组。<br>创建一个分组，然后搜索添加股票。</div></div>
      <button class="btn" id="wl-new-group">＋ 新建分组</button>`;
    document.getElementById('wl-new-group').onclick = () => promptName(view, '新建分组', '').then((name) => {
      if (!name) return;
      api.createWatchlistGroup(name).then(() => renderWatchlistView(view)).catch((e) => toast(`创建失败：${e.message}`, true));
    });
    return;
  }
  if (currentGroupId === null || !groups.some((g) => g.id === currentGroupId)) {
    currentGroupId = groups[0].id;
  }
  renderBody(view, groups);
}

function renderBody(view, groups) {
  const gid = currentGroupId;
  const group = groups.find((g) => g.id === gid);
  view.innerHTML = `
    <div class="seg-wrap" style="display:flex;gap:8px;overflow-x:auto;padding-bottom:8px;margin-bottom:10px">
      ${groups.map((g) => `<button class="seg-btn ${g.id === gid ? 'active' : ''}" data-gid="${g.id}">${esc(g.name)}<span class="muted" style="margin-left:4px">${g.count}</span></button>`).join('')}
      <button class="seg-btn" id="wl-add-group" title="新建分组">＋</button>
    </div>
    <div class="card" style="margin-bottom:10px;position:relative">
      <input id="wl-search" type="text" placeholder="搜索股票添加到本分组" autocomplete="off">
      <div id="wl-drop" class="search-drop" style="display:none"></div>
      <div style="display:flex;gap:10px;margin-top:10px">
        <button class="btn sm secondary" id="wl-rename" style="flex:1">重命名分组</button>
        <button class="btn sm" id="wl-del-group" style="flex:1;color:var(--danger)">删除分组</button>
      </div>
    </div>
    <div id="wl-items"></div>
  `;
  view.querySelectorAll('[data-gid]').forEach((b) => {
    b.onclick = () => { currentGroupId = Number(b.dataset.gid); renderBody(view, groups); };
  });
  document.getElementById('wl-add-group').onclick = () => promptName(view, '新建分组', '').then((name) => {
    if (!name) return;
    api.createWatchlistGroup(name).then(() => { currentGroupId = null; renderWatchlistView(view); }).catch((e) => toast(`创建失败：${e.message}`, true));
  });
  document.getElementById('wl-rename').onclick = () => promptName(view, '重命名分组', group.name).then((name) => {
    if (!name || name === group.name) return;
    api.renameWatchlistGroup(gid, name).then(() => renderWatchlistView(view)).catch((e) => toast(`重命名失败：${e.message}`, true));
  });
  document.getElementById('wl-del-group').onclick = () => {
    if (!window.confirm(`删除分组「${group.name}」及其中的股票？`)) return;
    api.deleteWatchlistGroup(gid).then(() => { currentGroupId = null; renderWatchlistView(view); }).catch((e) => toast(`删除失败：${e.message}`, true));
  };

  // 搜索添加
  const searchInput = document.getElementById('wl-search');
  const drop = document.getElementById('wl-drop');
  const doSearch = debounce(async () => {
    const q = searchInput.value.trim();
    if (!q) { drop.style.display = 'none'; return; }
    try {
      const { items } = await api.search(q);
      drop.innerHTML = '';
      if (!items.length) drop.innerHTML = '<div class="s-item muted">无结果</div>';
      for (const it of items) {
        const row = h(`<div class="s-item"><span><b>${esc(it.name)}</b> <span class="muted" style="font-size:12px">${esc(it.thscode)}</span></span><span class="chip">＋自选</span></div>`);
        row.onclick = () => {
          api.addWatchlistItem(gid, it.thscode, it.name).then(() => {
            toast(`已加入「${group.name}」`);
            searchInput.value = '';
            drop.style.display = 'none';
            loadItems(view, gid, groups);
          }).catch((e) => toast(`添加失败：${e.message}`, true));
        };
        drop.appendChild(row);
      }
      drop.style.display = 'block';
    } catch (e) {
      toast(`搜索失败：${e.message}`, true);
    }
  }, 300);
  searchInput.addEventListener('input', doSearch);

  loadItems(view, gid, groups);
}

function loadItems(view, gid, groups) {
  const el = document.getElementById('wl-items');
  api.watchlistItems(gid).then(({ items }) => {
    if (!items.length) {
      el.innerHTML = '<div class="card"><div class="empty">该分组还没有股票。<br>用上方搜索框添加。</div></div>';
      return;
    }
    el.innerHTML = '';
    for (const it of items) {
      const row = h(`<div class="card clickable" data-code="${esc(it.thscode)}" style="display:flex;justify-content:space-between;align-items:center;padding:12px 16px">
        <div><b>${esc(it.name)}</b> <span class="muted" style="font-size:12px">${esc(it.thscode)}</span></div>
        <button class="del" title="移出自选">✕</button>
      </div>`);
      row.onclick = () => {
        state.chartCode = it.thscode;
        const v = document.getElementById('view');
        v.dataset.sub = 'kline';
        v.dispatchEvent(new CustomEvent('rerender'));
      };
      row.querySelector('.del').onclick = (ev) => {
        ev.stopPropagation();
        api.deleteWatchlistItem(it.id).then(() => loadItems(view, gid, groups)).catch((e) => toast(`删除失败：${e.message}`, true));
      };
      el.appendChild(row);
    }
  }).catch((e) => {
    el.innerHTML = `<div class="card"><div class="empty">加载失败：${esc(e.message)}</div></div>`;
  });
}

function promptName(view, title, initial) {
  return new Promise((resolve) => {
    view.innerHTML = `
      <div class="card">
        <div class="field"><label>${esc(title)}</label><input id="wl-name" type="text" value="${esc(initial)}" placeholder="分组名"></div>
        <div style="display:flex;gap:10px">
          <button class="btn" id="wl-name-ok" style="flex:1">确定</button>
          <button class="btn secondary" id="wl-name-cancel" style="flex:1">取消</button>
        </div>
      </div>`;
    document.getElementById('wl-name-ok').onclick = () => resolve(document.getElementById('wl-name').value.trim());
    document.getElementById('wl-name-cancel').onclick = () => resolve('');
  });
}
