// 插件视图：市场、我的插件、自建与调试、审核与调用日志
import { api, qs } from '../api.js?v=19';
import { icon } from '../icons.js?v=19';
import {
  $, $$, esc, fmtTime, fmtDateTime, toast, openModal, confirmDialog,
  emptyState, skeletonCards, track, avatarHtml,
} from '../ui.js?v=19';

const SORTS = [['hot', '最热'], ['rating', '评分'], ['new', '最新']];
const AUTH_LABELS = { none: '无需鉴权', api_key: 'API Key', oauth: 'OAuth 2.0', bearer: 'Bearer Token' };
const STATUS_MAP = {
  approved: ['已上架', 'success'], draft: ['草稿', ''], pending: ['待审核', 'warn'],
  rejected: ['已驳回', 'danger'], offline: ['已下架', ''], enabled: ['已启用', 'success'],
  disabled: ['已停用', ''], success: ['成功', 'success'], failed: ['失败', 'danger'],
};
const CATEGORIES_FALLBACK = ['全部', '办公效率', '数据分析', '内容创作', '生活服务', '开发工具'];

let st = { tab: 'market', category: '全部', sort: 'hot', q: '', page: 1, categories: CATEGORIES_FALLBACK };

export async function render(page) {
  st = { ...st, tab: 'market', category: '全部', sort: 'hot', q: '', page: 1 };
  page.innerHTML = `<div class="page-inner">
    <div class="page-head">
      <div>
        <h1>插件</h1>
        <p>安装官方插件，或把你的能力封装成插件；在对话中勾选后即可调用。</p>
      </div>
      <div class="actions">
        <button class="btn ghost" id="pl-calls">${icon('history')} 调用日志</button>
        <button class="btn primary" id="pl-create">${icon('plus')} 创建插件</button>
      </div>
    </div>
    <div class="tabs" id="pl-tabs">
      <button class="active" data-tab="market">插件市场</button>
      <button data-tab="mine">我的插件</button>
    </div>
    <div id="pl-body"></div>
  </div>`;
  $('#pl-create').onclick = () => openCreate();
  $('#pl-calls').onclick = openCallLogs;
  $$('#pl-tabs button').forEach((btn) => {
    btn.onclick = () => {
      $$('#pl-tabs button').forEach((b) => b.classList.toggle('active', b === btn));
      st.tab = btn.dataset.tab;
      renderBody();
    };
  });
  try {
    st.categories = await api('/plugin-categories');
  } catch (err) { /* 使用默认分类 */ }
  await renderBody();
}

async function renderBody() {
  const box = $('#pl-body');
  if (!box) return;
  if (st.tab === 'mine') {
    await renderMine(box);
    return;
  }
  box.innerHTML = `
    <div class="row" style="margin-bottom:14px;align-items:center;flex-wrap:wrap">
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        ${st.categories.map((c) => `<button class="chip ${st.category === c ? 'active' : ''}" data-cat="${esc(c)}">${esc(c)}</button>`).join('')}
      </div>
      <div style="display:flex;gap:8px;flex:none;margin-left:auto">
        <div class="search-inline" style="display:flex;align-items:center;gap:6px;border:1px solid var(--line);border-radius:9px;padding:0 10px;height:34px;background:#fff;width:220px">
          ${icon('search')}<input id="pl-q" placeholder="搜索插件…" style="border:none;outline:none;flex:1;font-size:12.5px">
        </div>
        <div class="news-sort">${SORTS.map(([k, v]) => `<button data-sort="${k}" class="${st.sort === k ? 'active' : ''}">${v}</button>`).join('')}</div>
      </div>
    </div>
    <div id="pl-grid">${skeletonCards(6)}</div>`;

  $$('[data-cat]', box).forEach((btn) => {
    btn.onclick = () => { st.category = btn.dataset.cat; st.page = 1; renderBody(); };
  });
  $$('[data-sort]', box).forEach((btn) => {
    btn.onclick = () => { st.sort = btn.dataset.sort; st.page = 1; renderBody(); };
  });
  $('#pl-q').oninput = (() => {
    let timer = null;
    return (e) => {
      clearTimeout(timer);
      timer = setTimeout(() => { st.q = e.target.value.trim(); st.page = 1; loadMarket(); }, 280);
    };
  })();
  await loadMarket();
}

async function loadMarket() {
  const grid = $('#pl-grid');
  if (!grid) return;
  try {
    const data = await api(`/plugins${qs({
      category: st.category, sort: st.sort, q: st.q, page: st.page, page_size: 24,
    })}`);
    const items = data.list || [];
    if (!items.length) {
      grid.innerHTML = emptyState({ iconName: 'plugins', title: '没有找到插件', desc: '换个分类或关键词试试，也可以创建自己的插件。' });
      return;
    }
    grid.innerHTML = `<div class="plugin-grid">${items.map(pluginCard).join('')}</div>`;
    grid.querySelectorAll('.plugin-card').forEach((card) => {
      const item = items.find((p) => p.id === card.dataset.id);
      card.onclick = () => openDetail(item);
      const btn = card.querySelector('[data-install]');
      if (btn) {
        btn.onclick = async (e) => {
          e.stopPropagation();
          await installPlugin(item);
        };
      }
    });
  } catch (err) {
    grid.innerHTML = `<div class="empty"><p>${esc(err.message)}</p></div>`;
  }
}

function pluginCard(p) {
  return `<div class="plugin-card" data-id="${p.id}">
    <div class="plugin-top">
      <div class="plugin-ico">${p.icon || '🧩'}</div>
      <div style="flex:1;min-width:0">
        <b>${esc(p.name)}</b>
        <p>${esc(p.description || '暂无简介')}</p>
      </div>
    </div>
    <div style="display:flex;gap:5px;flex-wrap:wrap">
      <span class="tag brand">${esc(p.category)}</span>
      ${p.installed ? `<span class="tag ${p.installed_status === 'enabled' ? 'success' : ''}">${p.installed_status === 'enabled' ? '已启用' : '已停用'}</span>` : ''}
      ${p.is_mine ? '<span class="tag warn">我创建的</span>' : ''}
    </div>
    <div class="plugin-foot">
      <div class="plugin-stats"><span>${icon('download')} ${p.install_count}</span><span>${icon('star')} ${p.rating}</span><span>v${esc(p.version)}</span></div>
      ${p.installed
        ? `<span class="small muted">已安装</span>`
        : `<button class="btn sm soft" data-install>${icon('plus')} 安装</button>`}
    </div>
  </div>`;
}

async function installPlugin(p) {
  try {
    const res = await api(`/plugins/${p.id}/install`, { method: 'POST' });
    track('install_plugin', { plugin_id: p.id });
    if (res.need_authorize) {
      const ok = await confirmDialog({
        title: '授权确认', message: `「${p.name}」申请访问权限，是否同意并启用？`, confirmText: '同意并启用',
      });
      if (ok) await api(`/plugins/${p.id}/install`, { method: 'PATCH', body: { authorized: true, status: 'enabled' } });
    }
    toast('插件已安装并启用', 'success');
    if (st.tab === 'mine') renderBody();
    else loadMarket();
  } catch (err) { toast(err.message, 'error'); }
}

// ------------------------------------------------------------------ 我的插件
async function renderMine(box) {
  box.innerHTML = skeletonCards(3);
  let data;
  try {
    data = await api('/plugins/mine');
  } catch (err) {
    box.innerHTML = `<div class="empty"><p>${esc(err.message)}</p></div>`;
    return;
  }
  const installed = data.installed || [];
  const created = data.created || [];
  box.innerHTML = `
    <div class="section-title"><div><h3>已安装（${installed.length}）</h3><p>可启用、停用或设为对话默认插件</p></div></div>
    <div class="card" style="margin-bottom:22px">
      ${installed.length ? installed.map((p) => `
        <div class="mine-row" data-id="${p.id}">
          <div class="plugin-ico" style="width:34px;height:34px;font-size:18px">${p.icon}</div>
          <div style="flex:1;min-width:0">
            <b style="font-size:13px">${esc(p.name)} ${p.is_default ? '<span class="tag brand">默认</span>' : ''}</b>
            <span class="small muted" style="display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.description)}</span>
          </div>
          <span class="tag ${p.installed_status === 'enabled' ? 'success' : ''}">${p.installed_status === 'enabled' ? '已启用' : '已停用'}</span>
          <button class="btn sm ghost" data-toggle>${p.installed_status === 'enabled' ? '停用' : '启用'}</button>
          <button class="btn sm ghost" data-default ${p.is_default ? 'disabled' : ''}>设为默认</button>
          <button class="icon-btn" data-uninstall>${icon('trash')}</button>
        </div>`).join('') : '<div class="ws-empty">还没有安装插件，去插件市场逛逛吧。</div>'}
    </div>
    <div class="section-title"><div><h3>我创建的（${created.length}）</h3><p>支持草稿编辑、提交审核与沙箱调试</p></div>
      <button class="btn sm primary" id="mine-create">${icon('plus')} 创建插件</button></div>
    <div class="card">
      ${created.length ? created.map((p) => `
        <div class="mine-row" data-id="${p.id}">
          <div class="plugin-ico" style="width:34px;height:34px;font-size:18px">${p.icon}</div>
          <div style="flex:1;min-width:0">
            <b style="font-size:13px">${esc(p.name)}</b>
            <span class="small muted">v${esc(p.version)} · 调用 ${p.install_count} 次</span>
          </div>
          <span class="tag ${STATUS_MAP[p.status]?.[1] || ''}">${STATUS_MAP[p.status]?.[0] || p.status}</span>
          <button class="btn sm ghost" data-open>查看</button>
          <button class="btn sm ghost" data-edit>编辑</button>
          ${p.status === 'draft' || p.status === 'rejected'
            ? '<button class="btn sm soft" data-publish>提交审核</button>' : ''}
          ${p.status === 'pending' ? '<button class="btn sm soft" data-review>模拟审核</button>' : ''}
        </div>`).join('') : '<div class="ws-empty">还没有创建插件，试试把你的能力接入 MAuto。</div>'}
    </div>`;

  $('#mine-create').onclick = () => openCreate();
  $$('.mine-row', box).forEach((row) => {
    const p = [...installed, ...created].find((x) => x.id === row.dataset.id);
    if (!p) return;
    const toggle = row.querySelector('[data-toggle]');
    if (toggle) {
      toggle.onclick = async () => {
        try {
          await api(`/plugins/${p.id}/install`, {
            method: 'PATCH',
            body: { status: p.installed_status === 'enabled' ? 'disabled' : 'enabled' },
          });
          toast('状态已更新', 'success');
          renderBody();
        } catch (err) { toast(err.message, 'error'); }
      };
    }
    const def = row.querySelector('[data-default]');
    if (def && !p.is_default) {
      def.onclick = async () => {
        try {
          await api(`/plugins/${p.id}/install`, { method: 'PATCH', body: { is_default: true } });
          toast('已设为默认插件', 'success');
          renderBody();
        } catch (err) { toast(err.message, 'error'); }
      };
    }
    const un = row.querySelector('[data-uninstall]');
    if (un) {
      un.onclick = async () => {
        const ok = await confirmDialog({
          title: '卸载插件', message: `确定卸载「${p.name}」吗？`, confirmText: '卸载', danger: true,
        });
        if (!ok) return;
        await api(`/plugins/${p.id}/install`, { method: 'DELETE' });
        toast('已卸载', 'success');
        renderBody();
      };
    }
    if (row.querySelector('[data-open]')) {
      row.querySelector('[data-open]').onclick = () => openDetail(p);
    }
    if (row.querySelector('[data-edit]')) {
      row.querySelector('[data-edit]').onclick = () => openCreate(p);
    }
    if (row.querySelector('[data-publish]')) {
      row.querySelector('[data-publish]').onclick = async () => {
        try {
          await api(`/plugins/${p.id}/publish`, { method: 'POST' });
          toast('已提交审核，等待平台通过', 'success');
          renderBody();
        } catch (err) { toast(err.message, 'error'); }
      };
    }
    if (row.querySelector('[data-review]')) {
      row.querySelector('[data-review]').onclick = () => openReview(p);
    }
  });
}

function openReview(p) {
  openModal({
    title: '模拟平台审核',
    subtitle: '演示环境下可模拟审核结果，通过后插件将出现在市场',
    className: 'slim',
    body: `<p class="small muted" style="line-height:1.8">插件「${esc(p.name)}」当前为待审核状态。真实环境由运营同学审核，演示环境可直接模拟。</p>`,
    footer: `<button class="btn ghost-danger" data-reject>驳回</button>
             <button class="btn primary" data-approve>通过并上架</button>`,
    onMount(el, close) {
      const review = async (action) => {
        try {
          await api(`/plugins/${p.id}/review`, {
            method: 'POST',
            body: { action, reason: action === 'reject' ? '演示：信息不完整，请补充参数说明' : '' },
          });
          toast(action === 'approve' ? '插件已上架' : '已驳回', 'success');
          close();
          renderBody();
        } catch (err) { toast(err.message, 'error'); }
      };
      el.querySelector('[data-approve]').onclick = () => review('approve');
      el.querySelector('[data-reject]').onclick = () => review('reject');
    },
  });
}

// ------------------------------------------------------------------ 详情 & 调试
function openDetail(p) {
  const params = p.params || [];
  const body = `
    <div class="plugin-detail-head">
      <div class="plugin-ico" style="width:52px;height:52px;font-size:26px">${p.icon}</div>
      <div style="flex:1;min-width:0">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <b style="font-size:16px">${esc(p.name)}</b>
          <span class="tag ${STATUS_MAP[p.status]?.[1] || ''}">${STATUS_MAP[p.status]?.[0] || p.status}</span>
          <span class="tag">v${esc(p.version)}</span>
        </div>
        <p class="small muted" style="margin-top:5px;line-height:1.6">${esc(p.description)}</p>
        <div class="plugin-stats" style="margin-top:7px">
          <span>${icon('download')} ${p.install_count} 次安装</span>
          <span>${icon('star')} ${p.rating}（${p.rating_count}）</span>
          <span>作者：${esc(p.author)}</span>
        </div>
      </div>
    </div>
    <div class="divider"></div>
    <p style="font-size:12.5px;line-height:1.8;color:var(--ink-2)">${esc(p.detail || p.description || '暂无详细说明')}</p>
    <div class="section-title" style="margin-top:16px"><div><h3 style="font-size:13px">调用参数</h3></div></div>
    ${params.length ? `<table class="param-table">
      <thead><tr><th>参数</th><th>类型</th><th>必填</th><th>说明</th></tr></thead>
      <tbody>${params.map((x) => `<tr><td class="mono">${esc(x.name)}</td><td>${esc(x.type || 'string')}</td>
        <td>${x.required ? '是' : '否'}</td><td>${esc(x.desc || x.description || '')}</td></tr>`).join('')}</tbody>
    </table>` : '<p class="small muted">该插件无需额外参数。</p>'}
    <div class="row" style="margin-top:16px">
      <div><div class="field"><label>鉴权方式</label><div class="small">${AUTH_LABELS[p.auth_type] || esc(p.auth_type)}</div></div></div>
      <div><div class="field"><label>权限</label><div class="plugin-perms">${(p.permissions || []).map((x) => `<span class="tag">${esc(x)}</span>`).join('') || '<span class="small muted">无敏感权限</span>'}</div></div></div>
    </div>
    <div class="field" style="margin-top:4px"><label>更新日志</label><div class="small muted">${esc(p.changelog || '首个版本')}</div></div>
    <div class="divider"></div>
    <div class="section-title"><div><h3 style="font-size:13px">调试沙箱</h3><p>参数会被校验后模拟执行，不会产生真实外部调用</p></div></div>
    <div id="dbg-params">${params.map((x) => `
      <div class="field"><label>${esc(x.name)} ${x.required ? '<span style="color:var(--danger)">*</span>' : ''}</label>
        <input class="input" data-param="${esc(x.name)}" placeholder="${esc(x.desc || x.description || '')}" value="${esc(x.example ?? '')}">
      </div>`).join('')}</div>
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:10px">
      <button class="btn primary" id="dbg-run">${icon('play')} 运行调试</button>
      <span class="small muted" id="dbg-meta"></span>
    </div>
    <pre class="code-block" id="dbg-out">（调试输出会显示在这里）</pre>`;

  openModal({
    title: '插件详情',
    className: 'wide',
    body,
    footer: p.installed
      ? `<button class="btn ghost" id="dt-uninstall">卸载</button>
         <button class="btn ghost" id="dt-default" ${p.is_default ? 'disabled' : ''}>设为默认</button>
         <button class="btn primary" id="dt-toggle">${p.installed_status === 'enabled' ? '停用插件' : '启用插件'}</button>`
      : `<button class="btn primary" id="dt-install">${icon('plus')} 安装插件</button>`,
    onMount(el, close) {
      const out = el.querySelector('#dbg-out');
      el.querySelector('#dbg-run').onclick = async () => {
        const payload = {};
        el.querySelectorAll('[data-param]').forEach((input) => { payload[input.dataset.param] = input.value; });
        out.textContent = '运行中…';
        try {
          const res = await api(`/plugins/${p.id}/debug`, { method: 'POST', body: { params: payload } });
          out.textContent = typeof res.output === 'string' ? res.output : JSON.stringify(res.output, null, 2);
          el.querySelector('#dbg-meta').textContent = `${res.status} · ${res.duration_ms}ms · ${res.endpoint}`;
        } catch (err) {
          out.textContent = `调用失败：${err.message}`;
        }
      };
      const installBtn = el.querySelector('#dt-install');
      if (installBtn) {
        installBtn.onclick = async () => {
          await installPlugin(p);
          close();
        };
        return;
      }
      el.querySelector('#dt-toggle').onclick = async () => {
        await api(`/plugins/${p.id}/install`, {
          method: 'PATCH',
          body: { status: p.installed_status === 'enabled' ? 'disabled' : 'enabled' },
        });
        toast('状态已更新', 'success');
        close();
        if (st.tab === 'mine') renderBody(); else loadMarket();
      };
      const def = el.querySelector('#dt-default');
      if (def && !p.is_default) {
        def.onclick = async () => {
          await api(`/plugins/${p.id}/install`, { method: 'PATCH', body: { is_default: true } });
          toast('已设为默认插件', 'success');
          close();
        };
      }
      el.querySelector('#dt-uninstall').onclick = async () => {
        const ok = await confirmDialog({ title: '卸载插件', message: `确定卸载「${p.name}」吗？`, confirmText: '卸载', danger: true });
        if (!ok) return;
        await api(`/plugins/${p.id}/install`, { method: 'DELETE' });
        toast('已卸载', 'success');
        close();
        if (st.tab === 'mine') renderBody(); else loadMarket();
      };
    },
  });
}

// ------------------------------------------------------------------ 创建 / 编辑
function openCreate(plugin = null) {
  const isEdit = !!plugin;
  const params = plugin?.params?.length ? plugin.params : [{ name: 'query', type: 'string', required: true, desc: '检索关键词' }];
  const body = `
    <div class="row">
      <div class="field" style="max-width:90px"><label>图标</label><input class="input" id="cp-icon" maxlength="4" value="${esc(plugin?.icon || '🧩')}"></div>
      <div class="field"><label>插件名称（≤20 字）</label><input class="input" id="cp-name" maxlength="20" value="${esc(plugin?.name || '')}" placeholder="例如：财报速读"></div>
    </div>
    <div class="row">
      <div class="field"><label>分类</label><select class="select" id="cp-cat">
        ${st.categories.filter((c) => c !== '全部').map((c) => `<option ${plugin?.category === c ? 'selected' : ''}>${esc(c)}</option>`).join('')}
      </select></div>
      <div class="field"><label>鉴权方式</label><select class="select" id="cp-auth">
        ${Object.entries(AUTH_LABELS).map(([v, label]) => `<option value="${v}" ${plugin?.auth_type === v ? 'selected' : ''}>${label}</option>`).join('')}
      </select></div>
    </div>
    <div class="field"><label>简介（一句话说明能力）</label>
      <input class="input" id="cp-desc" value="${esc(plugin?.description || '')}" placeholder="例如：输入公司名，输出最新财报要点"></div>
    <div class="field"><label>详细说明</label>
      <textarea class="textarea" id="cp-detail" rows="3" placeholder="使用场景、输入输出示例…">${esc(plugin?.detail || '')}</textarea></div>
    <div class="field"><label>接口地址（可选，留空使用内置沙箱）</label>
      <input class="input" id="cp-url" value="${esc(plugin?.api_url || '')}" placeholder="https://api.example.com/v1/run"></div>
    <div class="field"><label>参数 Schema</label>
      <div id="cp-params" style="display:flex;flex-direction:column;gap:8px"></div>
      <button class="btn sm ghost" id="cp-add" style="margin-top:8px;align-self:flex-start">${icon('plus')} 添加参数</button>
    </div>
    <div class="field"><label>所需权限（逗号分隔）</label>
      <input class="input" id="cp-perms" value="${esc((plugin?.permissions || []).join(', '))}" placeholder="读取用户公开资料, 访问网络"></div>`;

  openModal({
    title: isEdit ? '编辑插件' : '创建插件',
    subtitle: '定义参数与鉴权后，可在沙箱里调试并提交审核',
    className: 'wide',
    body,
    footer: `<button class="btn ghost" data-close>取消</button>
             <button class="btn primary" id="cp-save">${isEdit ? '保存' : '创建'}</button>`,
    onMount(el, close) {
      const box = el.querySelector('#cp-params');
      const addRow = (p = {}) => {
        const row = document.createElement('div');
        row.className = 'row';
        row.style.alignItems = 'center';
        row.innerHTML = `
          <input class="input" data-k="name" placeholder="参数名" value="${esc(p.name || '')}" style="max-width:130px">
          <select class="select" data-k="type" style="max-width:110px">
            ${['string', 'number', 'boolean', 'object'].map((t) => `<option ${p.type === t ? 'selected' : ''}>${t}</option>`).join('')}
          </select>
          <label style="display:flex;align-items:center;gap:5px;font-size:11.5px;flex:none">
            <input type="checkbox" data-k="required" ${p.required ? 'checked' : ''}> 必填</label>
          <input class="input" data-k="desc" placeholder="说明" value="${esc(p.desc || p.description || '')}">
          <button class="icon-btn" data-del>${icon('close')}</button>`;
        row.querySelector('[data-del]').onclick = () => row.remove();
        box.appendChild(row);
      };
      params.forEach(addRow);
      el.querySelector('#cp-add').onclick = () => addRow();
      el.querySelector('#cp-save').onclick = async () => {
        const name = el.querySelector('#cp-name').value.trim();
        if (!name) { toast('请输入插件名称', 'error'); return; }
        const paramList = $$('.row', box).map((row) => ({
          name: row.querySelector('[data-k=name]').value.trim(),
          type: row.querySelector('[data-k=type]').value,
          required: row.querySelector('[data-k=required]').checked,
          desc: row.querySelector('[data-k=desc]').value.trim(),
        })).filter((x) => x.name);
        const payload = {
          name,
          icon: el.querySelector('#cp-icon').value.trim() || '🧩',
          category: el.querySelector('#cp-cat').value,
          auth_type: el.querySelector('#cp-auth').value,
          description: el.querySelector('#cp-desc').value.trim(),
          detail: el.querySelector('#cp-detail').value.trim(),
          api_url: el.querySelector('#cp-url').value.trim(),
          params: paramList,
          permissions: el.querySelector('#cp-perms').value.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
        };
        try {
          if (isEdit) await api(`/plugins/${plugin.id}`, { method: 'PATCH', body: payload });
          else await api('/plugins', { method: 'POST', body: payload });
          toast(isEdit ? '插件已更新' : '插件已创建，可调试后提交审核', 'success');
          close();
          renderBody();
        } catch (err) { toast(err.message, 'error'); }
      };
    },
  });
}

// ------------------------------------------------------------------ 调用日志
async function openCallLogs() {
  openModal({
    title: '插件调用日志',
    subtitle: '最近 50 次调试与对话内调用记录',
    className: 'wide',
    body: '<div id="log-box">' + skeletonCards(2).replace(/plugin-grid/g, '') + '</div>',
    onMount(el) {
      const box = el.querySelector('#log-box');
      api('/plugin-calls?page_size=50').then((data) => {
        if (!data.list.length) {
          box.innerHTML = emptyState({ iconName: 'history', title: '暂无调用记录', desc: '在插件详情中运行一次调试，或在对话中勾选插件。' });
          return;
        }
        box.innerHTML = data.list.map((r) => `
          <div class="mine-row">
            <span class="plugin-ico" style="width:32px;height:32px;font-size:16px">${r.icon || '🧩'}</span>
            <div style="flex:1;min-width:0">
              <b style="font-size:12.5px">${esc(r.name || '未知插件')}</b>
              <span class="small muted">${fmtDateTime(r.created_at)} · ${r.duration_ms}ms</span>
            </div>
            <span class="tag ${r.status === 'success' ? 'success' : 'danger'}">${r.status === 'success' ? '成功' : '失败'}</span>
          </div>`).join('');
      }).catch((err) => {
        box.innerHTML = `<div class="ws-empty">${esc(err.message)}</div>`;
      });
    },
  });
}

void fmtTime;
void avatarHtml;
