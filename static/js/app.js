// 应用入口：登录、侧边栏、最近列表、通知、全局搜索、设置与路由
import { api, state, setToken, qs, download } from './api.js?v=19';
import { icon, hydrateIcons } from './icons.js?v=19';
import {
  $, $$, esc, fmtTime, fmtDateTime, toast, openModal, confirmDialog, promptDialog,
  showMenu, emptyState, avatarHtml, debounce, track, closeMenus, closePopovers,
} from './ui.js?v=19';
import * as chatView from './views/chat.js?v=19';
import * as tasksView from './views/tasks.js?v=19';
import * as newsView from './views/news.js?v=19';
import * as pluginsView from './views/plugins.js?v=19';
import * as pptView from './views/ppt.js?v=19';
import * as projectsView from './views/projects.js?v=19';

const NAV_ITEMS = [
  { key: 'chat', name: '新聊天', icon: 'chat', route: '#/chat' },
  { key: 'tasks', name: '任务', icon: 'tasks', route: '#/tasks' },
  { key: 'news', name: '咨询', icon: 'news', route: '#/news' },
  { key: 'plugins', name: '插件', icon: 'plugins', route: '#/plugins' },
  { key: 'ppt', name: 'PPT', icon: 'ppt', route: '#/ppt' },
  { key: 'projects', name: '项目', icon: 'projects', route: '#/projects' },
];

const VIEWS = {
  chat: chatView, tasks: tasksView, news: newsView,
  plugins: pluginsView, ppt: pptView, projects: projectsView,
};

let activeView = null;
let currentRoute = { name: 'chat', id: null };
let collapsePref = localStorage.getItem('mauto_collapsed') === '1';

// ------------------------------------------------------------------ 启动
async function boot() {
  hydrateIcons();
  try {
    state.config = await api('/meta/config');
  } catch (err) {
    state.config = { recommend_questions: [], models: [] };
  }
  state.onUnauthorized = () => {
    $('#app').classList.add('hidden');
    $('#boot').classList.add('hidden');
    showLogin();
  };
  if (!state.token) {
    $('#boot').classList.add('hidden');
    showLogin();
    return;
  }
  try {
    state.user = await api('/auth/me');
    startApp();
  } catch (err) {
    setToken('');
    $('#boot').classList.add('hidden');
    showLogin();
  }
}

function startApp() {
  $('#boot').classList.add('hidden');
  $('#login').classList.add('hidden');
  $('#app').classList.remove('hidden');
  applyUser();
  renderNav();
  applyCollapse(collapsePref);
  bindShell();
  refreshRecents();
  refreshUnread();
  if (!location.hash || location.hash === '#/' || location.hash === '#') location.hash = '#/chat';
  route();
  setInterval(refreshUnread, 60000);
}

function applyUser() {
  const u = state.user || {};
  $('#sb-avatar').outerHTML = avatarHtml(u.nickname, u.avatar, 30).replace('class="avatar"', 'class="avatar" id="sb-avatar"');
  $('#sb-nickname').textContent = u.nickname || '未登录';
  $('#sb-email').textContent = u.email || '';
}

// ------------------------------------------------------------------ 登录
function showLogin() {
  $('#login').classList.remove('hidden');
  $('#boot').classList.add('hidden');
  hydrateIcons();
  const form = $('#login-form');
  const tabs = $$('.login-tabs button');
  let mode = 'login';
  tabs.forEach((btn) => {
    btn.onclick = () => {
      tabs.forEach((b) => b.classList.toggle('active', b === btn));
      mode = btn.dataset.mode;
      $$('.reg-only').forEach((el) => el.classList.toggle('hidden', mode !== 'register'));
      $('#login-submit').textContent = mode === 'login' ? '登录' : '注册并登录';
      const pwd = form.querySelector('[name=password]');
      pwd.autocomplete = mode === 'login' ? 'current-password' : 'new-password';
    };
  });
  form.onsubmit = async (e) => {
    e.preventDefault();
    const btn = $('#login-submit');
    btn.disabled = true;
    const data = Object.fromEntries(new FormData(form));
    try {
      const res = await api(mode === 'login' ? '/auth/login' : '/auth/register', {
        method: 'POST', body: { email: data.email, password: data.password, nickname: data.nickname || '' },
      });
      setToken(res.token);
      state.user = res.user;
      startApp();
      toast('欢迎回来，' + res.user.nickname, 'success');
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      btn.disabled = false;
    }
  };
  $('#demo-login').onclick = async () => {
    try {
      const res = await api('/auth/login', {
        method: 'POST', body: { email: 'demo@mauto.ai', password: 'demo1234' },
      });
      setToken(res.token);
      state.user = res.user;
      startApp();
      toast('已进入演示账号', 'success');
    } catch (err) {
      toast(err.message, 'error');
    }
  };
}

// ------------------------------------------------------------------ 侧边栏
function renderNav() {
  $('#sb-nav').innerHTML = NAV_ITEMS.map((it) => `
    <a class="nav-item" href="${it.route}" data-key="${it.key}" data-tip="${it.name}">
      ${icon(it.icon)}<span>${it.name}</span>
    </a>`).join('');
  $$('#sb-nav .nav-item').forEach((el) => {
    el.addEventListener('mouseenter', () => showTip(el, el.dataset.tip));
    el.addEventListener('mouseleave', hideTip);
  });
}

let tipEl = null;
function showTip(anchor, text) {
  if (!document.getElementById('sidebar').classList.contains('collapsed')) return;
  const tip = $('#sb-tooltip');
  const rect = anchor.getBoundingClientRect();
  tip.textContent = text;
  tip.style.left = `${rect.right + 10}px`;
  tip.style.top = `${rect.top + rect.height / 2}px`;
  tip.classList.add('show');
  tipEl = anchor;
}
function hideTip() {
  $('#sb-tooltip').classList.remove('show');
  tipEl = null;
}

function applyCollapse(collapsed) {
  collapsePref = collapsed;
  localStorage.setItem('mauto_collapsed', collapsed ? '1' : '0');
  $('#sidebar').classList.toggle('collapsed', collapsed);
  if (collapsed) hideTip();
  if (state.user) {
    state.user.prefs = { ...(state.user.prefs || {}), sidebar_collapsed: collapsed };
    api('/settings', { method: 'PATCH', body: { sidebar_collapsed: collapsed } }).catch(() => {});
  }
}

async function refreshRecents() {
  const box = $('#sb-recent');
  if (!box) return;
  try {
    const data = await api(`/conversations${qs({ recent_only: 1, archived: 0 })}`);
    const items = data.list || [];
    const activeId = currentRoute.name === 'chat' ? currentRoute.id : null;
    if (!items.length) {
      box.innerHTML = '';
      box.style.display = 'none';
      return;
    }
    box.style.display = '';
    const head = `<div class="recent-head"><span>最近</span><span title="仅展示最近 15 条">${items.length}</span></div>`;
    const renderItem = (it) => `
      <div class="recent-item ${it.is_pinned ? 'pinned' : ''} ${it.id === activeId ? 'active' : ''}"
           data-id="${it.id}" title="${esc(it.title)}">
        <span class="r-title">${esc(it.title)}</span>
        <span class="r-time">${fmtTime(it.updated_at)}</span>
        <button class="r-more" data-more="${it.id}">${icon('more')}</button>
      </div>`;
    let html = head;
    if (data.groups && data.groups.length) {
      html += data.groups.map((g) => `<div class="recent-group">${esc(g.name)}</div>${g.items.map(renderItem).join('')}`).join('');
    } else {
      html += items.map(renderItem).join('');
    }
    html += `<div class="recent-head" style="padding-top:8px"><a href="#/chat?all=1" style="font-size:11.5px;color:var(--ink-3)">查看全部会话</a></div>`;
    box.innerHTML = html;
    box.querySelectorAll('.recent-item').forEach((row) => {
      row.addEventListener('click', (e) => {
        if (e.target.closest('[data-more]')) return;
        location.hash = `#/chat/${row.dataset.id}`;
        if (window.innerWidth <= 1024) toggleMobileSidebar(false);
      });
      row.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        openRecentMenu(row.querySelector('[data-more]'), row.dataset.id, items.find((x) => x.id === row.dataset.id));
      });
    });
    box.querySelectorAll('[data-more]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const item = items.find((x) => x.id === btn.dataset.more);
        openRecentMenu(btn, btn.dataset.more, item);
      });
    });
  } catch (err) {
    box.innerHTML = '';
  }
}

function openRecentMenu(anchor, id, item) {
  showMenu(anchor, [
    {
      label: '重命名', icon: 'edit', onClick: async () => {
        const name = await promptDialog({ title: '重命名会话', value: item?.title || '', placeholder: '会话标题' });
        if (!name) return;
        await api(`/conversations/${id}`, { method: 'PATCH', body: { title: name } });
        toast('已重命名', 'success');
        refreshRecents();
        if (currentRoute.id === id) {
          $('#page-title').textContent = name;
          $('#crumb').innerHTML = `会话 · ${esc(name)}`;
        }
      },
    },
    {
      label: item?.is_pinned ? '取消置顶' : '置顶', icon: 'pin', onClick: async () => {
        await api(`/conversations/${id}`, { method: 'PATCH', body: { is_pinned: !item?.is_pinned } });
        toast(item?.is_pinned ? '已取消置顶' : '已置顶', 'success');
        refreshRecents();
      },
    },
    {
      label: '归档', icon: 'archive', onClick: async () => {
        await api(`/conversations/${id}`, { method: 'PATCH', body: { is_archived: true } });
        toast('已归档', 'success');
        if (currentRoute.id === id) location.hash = '#/chat';
        refreshRecents();
      },
    },
    { sep: true },
    {
      label: '删除会话', icon: 'trash', danger: true, onClick: async () => {
        const ok = await confirmDialog({
          title: '删除会话', message: '删除后不可恢复，确定要删除该会话吗？', confirmText: '删除', danger: true,
        });
        if (!ok) return;
        await api(`/conversations/${id}`, { method: 'DELETE' });
        toast('会话已删除', 'success');
        if (currentRoute.id === id) location.hash = '#/chat';
        refreshRecents();
      },
    },
  ]);
}

function bindShell() {
  $('#collapse-btn').onclick = () => applyCollapse(!collapsePref);
  $('#new-chat-btn').onclick = () => {
    location.hash = '#/chat';
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    setTimeout(() => $('#composer-input')?.focus(), 60);
  };
  $('#mobile-menu').onclick = () => toggleMobileSidebar(true);
  $('#sidebar-mask').onclick = () => toggleMobileSidebar(false);
  $('#search-trigger').onclick = openGlobalSearch;
  $('#user-btn').onclick = () => {
    showMenu($('#user-btn'), [
      { label: '账号设置', icon: 'settings', onClick: openSettings },
      { label: '数据导出', icon: 'download', onClick: () => downloadData() },
      { sep: true },
      {
        label: '退出登录', icon: 'logout', danger: true, onClick: async () => {
          setToken('');
          state.user = null;
          location.hash = '#/chat';
          $('#app').classList.add('hidden');
          showLogin();
        },
      },
    ]);
  };
  $('#settings-btn').onclick = openSettings;
  $('#help-btn').onclick = openHelp;
  $('#theme-btn').onclick = openHelp;
  $('#bell-btn').onclick = (e) => {
    e.stopPropagation();
    toggleNotifications();
  };
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.bell-wrap')) $('#notif-dropdown').classList.add('hidden');
  });
  document.addEventListener('mauto:close-popovers', () => {
    $('#notif-dropdown').classList.add('hidden');
  });
  document.addEventListener('keydown', (e) => {
    const meta = e.metaKey || e.ctrlKey;
    if (meta && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      openGlobalSearch();
    }
    if (meta && e.shiftKey && e.key.toLowerCase() === 'o') {
      e.preventDefault();
      location.hash = '#/chat';
      window.dispatchEvent(new HashChangeEvent('hashchange'));
    }
  });
  window.addEventListener('mauto:refresh-recents', () => refreshRecents());
  window.addEventListener('mauto:notify', () => refreshUnread());
  window.addEventListener('hashchange', route);
}

function toggleMobileSidebar(open) {
  if (open) closePopovers();
  $('#sidebar').classList.toggle('mobile-open', open);
  $('#sidebar-mask').classList.toggle('show', open);
}

// ------------------------------------------------------------------ 通知
async function refreshUnread() {
  try {
    const data = await api('/notifications/unread-count');
    const badge = $('#bell-badge');
    badge.textContent = data.unread > 99 ? '99+' : data.unread;
    badge.classList.toggle('hidden', !data.unread);
  } catch (err) { /* ignore */ }
}

async function toggleNotifications() {
  const box = $('#notif-dropdown');
  if (!box.classList.contains('hidden')) {
    box.classList.add('hidden');
    return;
  }
  closePopovers();
  box.classList.remove('hidden');
  box.innerHTML = '<div class="notif-head"><b>通知中心</b></div><div style="padding:16px"><div class="skeleton" style="height:60px;margin-bottom:8px"></div><div class="skeleton" style="height:60px"></div></div>';
  try {
    const data = await api('/notifications?page_size=12');
    const items = data.list || [];
    box.innerHTML = `
      <div class="notif-head">
        <b>通知中心 <span class="muted small">${data.unread ? `${data.unread} 条未读` : '全部已读'}</span></b>
        <button class="btn sm soft" id="read-all">全部已读</button>
      </div>
      ${items.length ? items.map((n) => `
        <div class="notif-item ${n.is_read ? '' : 'unread'}" data-type="${esc(n.type)}" data-ref="${esc(n.ref_id)}" data-ref-type="${esc(n.ref_type || '')}">
          <b>${esc(n.title)}</b>
          <p>${esc(n.body)}</p>
          <time>${fmtDateTime(n.created_at)}</time>
        </div>`).join('') : '<div class="empty" style="padding:32px"><p>暂无通知</p></div>'}`;
    $('#read-all').onclick = async (e) => {
      e.stopPropagation();
      await api('/notifications/read-all', { method: 'POST' });
      refreshUnread();
      toggleNotifications();
      toast('已全部标记为已读', 'success');
    };
    box.querySelectorAll('.notif-item').forEach((el) => {
      el.onclick = () => {
        const { type, ref, refType } = el.dataset;
        $('#notif-dropdown').classList.add('hidden');
        if (type === 'task') {
          location.hash = refType === 'conversation' && ref ? `#/chat/${ref}` : '#/tasks';
        } else if (type === 'plugin') location.hash = '#/plugins';
      };
    });
  } catch (err) {
    box.innerHTML = `<div class="empty" style="padding:26px"><p>${esc(err.message)}</p></div>`;
  }
}

// ------------------------------------------------------------------ 全局搜索
function openGlobalSearch() {
  const { el, close } = openModal({
    className: 'search-modal',
    body: `<div class="search-input-wrap">${icon('search')}
        <input id="gs-input" placeholder="搜索会话、消息、任务、资讯、插件、PPT、项目…" autocomplete="off" />
        <kbd style="font-size:10px;color:var(--ink-3);border:1px solid var(--line);border-radius:4px;padding:1px 5px">ESC</kbd>
      </div>
      <div class="search-results" id="gs-results"><div class="sr-empty">输入关键词开始搜索</div></div>
      <div class="search-foot"><span><kbd>↑</kbd><kbd>↓</kbd> 选择</span><span><kbd>Enter</kbd> 打开</span><span>聚合搜索 7 类内容</span></div>`,
    onMount(modal, closeFn) {
      const input = modal.querySelector('#gs-input');
      const box = modal.querySelector('#gs-results');
      let flat = [];
      let sel = 0;
      input.focus();
      const render = (groups) => {
        flat = [];
        if (!groups.length) {
          box.innerHTML = '<div class="sr-empty">没有找到相关内容</div>';
          return;
        }
        const typeIcon = {
          conversation: 'chat', task: 'tasks', news: 'news', plugin: 'plugins', ppt: 'ppt', project: 'projects',
        };
        box.innerHTML = groups.map((g) => `
          <div class="sr-group"><h5>${esc(g.name)}</h5>
          ${g.items.map((it) => {
            const idx = flat.length;
            flat.push(it);
            return `<div class="sr-item ${idx === sel ? 'sel' : ''}" data-idx="${idx}">
              <span class="sri">${icon(typeIcon[g.type] || 'search')}</span>
              <div style="min-width:0"><b>${esc(it.title)}</b><span>${esc(it.sub || '')}</span></div>
            </div>`;
          }).join('')}</div>`).join('');
        box.querySelectorAll('.sr-item').forEach((row) => {
          row.onclick = () => { closeFn(); location.hash = flat[Number(row.dataset.idx)].route; };
        });
      };
      const run = debounce(async () => {
        const q = input.value.trim();
        if (!q) {
          box.innerHTML = '<div class="sr-empty">输入关键词开始搜索</div>';
          flat = [];
          return;
        }
        box.innerHTML = '<div style="padding:14px"><div class="skeleton" style="height:40px;margin-bottom:8px"></div><div class="skeleton" style="height:40px"></div></div>';
        try {
          const data = await api(`/search${qs({ q })}`);
          sel = 0;
          render(data.groups || []);
        } catch (err) {
          box.innerHTML = `<div class="sr-empty">${esc(err.message)}</div>`;
        }
      }, 220);
      input.oninput = run;
      modal.addEventListener('keydown', (e) => {
        if (!flat.length) return;
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          e.preventDefault();
          sel = (sel + (e.key === 'ArrowDown' ? 1 : -1) + flat.length) % flat.length;
          $$('.sr-item', box).forEach((row) => row.classList.toggle('sel', Number(row.dataset.idx) === sel));
          $$('.sr-item', box)[sel]?.scrollIntoView({ block: 'nearest' });
        }
        if (e.key === 'Enter' && flat[sel]) {
          closeFn();
          location.hash = flat[sel].route;
        }
      });
    },
  });
  void el; void close;
}

// ------------------------------------------------------------------ 设置
function openSettings(tab = 'profile') {
  const u = state.user || {};
  const body = `
    <div class="settings-layout">
      <div class="settings-nav">
        <button data-tab="profile" class="${tab === 'profile' ? 'active' : ''}">个人资料</button>
        <button data-tab="security" class="${tab === 'security' ? 'active' : ''}">安全设置</button>
        <button data-tab="prefs" class="${tab === 'prefs' ? 'active' : ''}">偏好设置</button>
        <button data-tab="data" class="${tab === 'data' ? 'active' : ''}">数据管理</button>
      </div>
      <div class="settings-body" id="settings-body"></div>
    </div>`;
  const modal = openModal({ title: '设置', subtitle: '账号、安全与偏好配置', className: 'wide', body });
  const box = modal.el.querySelector('#settings-body');

  const panels = {
    profile: () => `
      <div style="display:flex;gap:16px;align-items:center;margin-bottom:20px">
        ${avatarHtml(u.nickname, u.avatar, 56)}
        <div><b style="font-size:15px">${esc(u.nickname || '')}</b>
        <div class="small muted">${esc(u.email || '')}</div></div>
      </div>
      <div class="field"><label>昵称</label><input class="input" id="pf-nickname" value="${esc(u.nickname || '')}" maxlength="32"></div>
      <div class="field"><label>头像地址（可选）</label><input class="input" id="pf-avatar" placeholder="https://…" value="${esc(u.avatar || '')}"></div>
      <button class="btn primary" id="pf-save">保存资料</button>`,
    security: () => `
      <div class="field"><label>原密码</label><input class="input" type="password" id="sec-old" placeholder="请输入原密码"></div>
      <div class="field"><label>新密码</label><input class="input" type="password" id="sec-new" placeholder="至少 6 位"></div>
      <button class="btn primary" id="sec-save">更新密码</button>
      <div class="divider"></div>
      <div class="section-title"><div><h3 style="font-size:13.5px">登录设备</h3><p>当前仅本机 Web 端</p></div>
      <span class="tag success">安全</span></div>
      <p class="small muted" style="line-height:1.8">演示环境使用 Bearer Token 鉴权，密码以 PBKDF2 加盐存储。</p>`,
    prefs: () => `
      <div class="field"><label>默认模型</label>
        <select class="select" id="pf-model">
          ${(state.config?.models || []).map((m) => `<option value="${m.id}" ${((u.prefs || {}).model || 'MAuto-Air') === m.id ? 'selected' : ''}>${m.name} · ${m.desc}</option>`).join('')}
        </select>
      </div>
      <div class="field"><label>联网搜索</label>
        <div style="display:flex;align-items:center;gap:10px">
          <div class="switch ${u.prefs?.web_search ? 'on' : ''}" id="pf-web"></div>
          <span class="small muted">默认开启联网检索并附带引用来源</span>
        </div>
      </div>
      <div class="field"><label>深度思考</label>
        <div style="display:flex;align-items:center;gap:10px">
          <div class="switch ${u.prefs?.deep_think ? 'on' : ''}" id="pf-think"></div>
          <span class="small muted">回答前展示推理过程</span>
        </div>
      </div>
      <div class="field"><label>侧边栏</label>
        <div style="display:flex;align-items:center;gap:10px">
          <div class="switch ${collapsePref ? 'on' : ''}" id="pf-collapse"></div>
          <span class="small muted">默认折叠为图标栏</span>
        </div>
      </div>
      <button class="btn primary" id="pref-save">保存偏好</button>`,
    data: () => `
      <div class="section-title"><div><h3 style="font-size:13.5px">导出个人数据</h3><p>包含会话、消息、任务与项目元数据（JSON）</p></div></div>
      <button class="btn ghost" id="data-export">导出数据</button>
      <div class="divider"></div>
      <div class="section-title"><div><h3 style="font-size:13.5px">清空会话</h3><p>删除全部会话与消息，此操作不可恢复</p></div></div>
      <button class="btn ghost-danger" id="data-clear">清空全部会话</button>
      <div class="divider"></div>
      <p class="small muted" style="line-height:1.8">隐私说明：项目文件仅用于本次问答上下文，不用于模型训练；演示环境数据存储于本机 data/mauto.db。</p>`,
  };

  const renderPanel = (key) => {
    box.innerHTML = panels[key]();
    if (key === 'profile') {
      box.querySelector('#pf-save').onclick = async () => {
        const nickname = box.querySelector('#pf-nickname').value.trim();
        const avatar = box.querySelector('#pf-avatar').value.trim();
        try {
          state.user = await api('/users/me', { method: 'PATCH', body: { nickname, avatar } });
          applyUser();
          toast('资料已更新', 'success');
        } catch (err) { toast(err.message, 'error'); }
      };
    }
    if (key === 'security') {
      box.querySelector('#sec-save').onclick = async () => {
        try {
          await api('/users/me/password', {
            method: 'POST',
            body: { old_password: box.querySelector('#sec-old').value, new_password: box.querySelector('#sec-new').value },
          });
          toast('密码已更新', 'success');
        } catch (err) { toast(err.message, 'error'); }
      };
    }
    if (key === 'prefs') {
      const toggles = ['pf-web', 'pf-think', 'pf-collapse'];
      toggles.forEach((id) => {
        const el = box.querySelector(`#${id}`);
        el.onclick = () => el.classList.toggle('on');
      });
      box.querySelector('#pref-save').onclick = async () => {
        const prefs = {
          model: box.querySelector('#pf-model').value,
          web_search: box.querySelector('#pf-web').classList.contains('on'),
          deep_think: box.querySelector('#pf-think').classList.contains('on'),
          sidebar_collapsed: box.querySelector('#pf-collapse').classList.contains('on'),
        };
        try {
          state.user = await api('/users/me', { method: 'PATCH', body: { prefs } });
          applyCollapse(prefs.sidebar_collapsed);
          toast('偏好已保存', 'success');
        } catch (err) { toast(err.message, 'error'); }
      };
    }
    if (key === 'data') {
      box.querySelector('#data-export').onclick = () => downloadData();
      box.querySelector('#data-clear').onclick = async () => {
        const ok = await confirmDialog({
          title: '清空全部会话', message: '将删除你的全部会话与消息，删除后不可恢复。', confirmText: '确认清空', danger: true,
        });
        if (!ok) return;
        await api('/data/clear-conversations', { method: 'POST' });
        toast('已清空全部会话', 'success');
        refreshRecents();
        location.hash = '#/chat';
        window.dispatchEvent(new HashChangeEvent('hashchange'));
        modal.close();
      };
    }
  };

  modal.el.querySelectorAll('.settings-nav button').forEach((btn) => {
    btn.onclick = () => {
      modal.el.querySelectorAll('.settings-nav button').forEach((b) => b.classList.toggle('active', b === btn));
      renderPanel(btn.dataset.tab);
    };
  });
  renderPanel(tab);
}

async function downloadData() {
  try {
    const data = await api('/data/export');
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `mauto-export-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 3000);
    toast('个人数据已导出', 'success');
  } catch (err) { toast(err.message, 'error'); }
}

function openHelp() {
  openModal({
    title: '帮助与反馈',
    subtitle: 'MAuto 智能助手平台 V1.0',
    body: `
      <div style="font-size:13px;line-height:1.9;color:var(--ink-2)">
        <p><b>快速上手</b></p>
        <ul style="padding-left:20px;margin:6px 0 14px">
          <li>在「新聊天」直接提问，支持联网搜索、深度思考与插件调用；</li>
          <li>「任务」可配置每天 / 工作日 / 每周 / 每月的自动执行指令；</li>
          <li>「咨询」按行业浏览资讯，详情页提供 AI 摘要与一键追问；</li>
          <li>「插件」可安装公共插件或自建插件，并在对话中勾选调用；</li>
          <li>「PPT」选模板生成大纲与成稿，可在线编辑并导出 .pptx；</li>
          <li>「项目」上传资料建立知识库，在项目内对话会引用文件。</li>
        </ul>
        <p><b>快捷键</b></p>
        <div class="small" style="margin:6px 0 14px">⌘/Ctrl + K 全局搜索 · ⌘/Ctrl + Shift + O 新建会话 · Esc 关闭弹层</div>
        <p><b>演示说明</b></p>
        <p class="small muted">本环境使用内置演示模型引擎，回答由规则化模板生成，用于完整体验产品流程；配置 MAUTO_LLM_API_KEY 后可切换真实模型。</p>
        <div class="divider"></div>
        <p class="small">遇到问题？可将反馈发送至 support@mauto.ai（演示地址）。</p>
      </div>`,
  });
}

// ------------------------------------------------------------------ 路由
const TITLES = {
  chat: ['新聊天', '对话'],
  tasks: ['任务', '自动化'],
  news: ['咨询', '行业资讯'],
  plugins: ['插件', '能力扩展'],
  ppt: ['PPT', '内容生产'],
  projects: ['项目', '协作空间'],
};

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [pathPart, queryPart] = raw.split('?');
  const parts = pathPart.split('/').filter(Boolean);
  const name = parts[0] || 'chat';
  const id = parts[1] || null;
  const params = Object.fromEntries(new URLSearchParams(queryPart || ''));
  return { name: VIEWS[name] ? name : 'chat', id, params };
}

async function route() {
  const next = parseHash();
  currentRoute = next;
  const view = VIEWS[next.name];
  const page = $('#page');

  if (activeView && activeView.destroy) {
    try { activeView.destroy(); } catch (err) { /* ignore */ }
  }

  $$('#sb-nav .nav-item').forEach((el) => el.classList.toggle('active', el.dataset.key === next.name));
  refreshRecents();
  track('page_view', { page: next.name });

  if (!view) return;
  const meta = TITLES[next.name] || ['', ''];
  $('#page-title').textContent = next.name === 'chat' && next.id ? '会话' : meta[0];
  $('#crumb').innerHTML = `<span>${esc(meta[1])}</span>${next.id ? `<span>/</span><span class="muted">详情</span>` : ''}`;

  page.innerHTML = '';
  activeView = view;
  try {
    await view.render(page, { id: next.id, params: next.params });
  } catch (err) {
    page.innerHTML = `<div class="page-inner">${emptyState({ iconName: 'help', title: '页面加载失败', desc: err.message, action: '<button class="btn primary" onclick="location.reload()">重新加载</button>' })}</div>`;
  }
  if (next.name === 'chat') refreshRecents();
}

// 供视图调用：更新标题 / 刷新最近
export function setPageTitle(title, crumb) {
  $('#page-title').textContent = title;
  if (crumb !== undefined) $('#crumb').innerHTML = `<span>${esc(crumb)}</span>`;
}

export function refreshRecentList() {
  refreshRecents();
}

export function getConfig() { return state.config; }
export function getUser() { return state.user; }
export function currentRouteInfo() { return currentRoute; }

export function setCurrentConversationId(id) {
  if (!id) return;
  if (currentRoute) currentRoute = { ...currentRoute, name: 'chat', id };
  try { history.replaceState(null, '', `#/chat/${id}`); } catch (err) { /* ignore */ }
  refreshRecents();
  $('#page-title').textContent = '会话';
}

window.MAUTO = {
  setPageTitle, refreshRecentList, openSettings, setCurrentConversationId,
  getConfig, getUser, currentRouteInfo,
};

boot();
void closeMenus;
