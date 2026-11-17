// 项目视图：项目列表、工作区（知识库 + 对话）、成员与设置
import { api, qs, download, state } from '../api.js?v=19';
import { icon } from '../icons.js?v=19';
import * as chatView from './chat.js?v=19';
import {
  $, $$, esc, fmtTime, fmtDateTime, toast, openModal, confirmDialog, promptDialog,
  emptyState, skeletonCards, skeletonRows, avatarHtml, track, showMenu,
} from '../ui.js?v=19';

const ICONS = ['📁', '🚀', '📊', '🧠', '💡', '📚', '🛠️', '🎯', '🏥', '💰'];
const COLORS = ['#1A5CC8', '#0F766E', '#6D28D9', '#9F1239', '#B45309', '#334155'];
const ROLE_LABEL = { owner: '拥有者', editor: '编辑者', viewer: '查看者' };

let listState = null;
let workspace = null;

export async function render(page, { id }) {
  if (id) {
    await renderWorkspace(page, id);
    return;
  }
  await renderList(page);
}

export function destroy() {
  if (workspace?.chat) {
    try { workspace.chat.destroy(); } catch (err) { /* ignore */ }
  }
  workspace = null;
}

// ------------------------------------------------------------------ 项目列表
async function renderList(page) {
  listState = { q: '', archived: false, items: [] };
  page.innerHTML = `<div class="page-inner">
    <div class="page-head">
      <div>
        <h1>项目</h1>
        <p>把资料、对话与成员放在同一个空间，项目内对话会自动引用知识库文件。</p>
      </div>
      <div class="actions">
        <button class="btn ghost" id="prj-trash">${icon('trash')} 回收站</button>
        <button class="btn primary" id="prj-new">${icon('plus')} 新建项目</button>
      </div>
    </div>
    <div class="row" style="margin-bottom:16px;align-items:center;flex-wrap:wrap">
      <div class="search-inline" style="display:flex;align-items:center;gap:6px;border:1px solid var(--line);border-radius:9px;padding:0 10px;height:36px;background:#fff;max-width:360px">
        ${icon('search')}<input id="prj-q" placeholder="搜索项目名称或描述…" style="border:none;outline:none;flex:1;font-size:12.5px">
      </div>
      <div class="news-sort" style="margin-left:auto">
        <button data-arch="0" class="active">进行中</button>
        <button data-arch="1">已归档</button>
      </div>
    </div>
    <div id="prj-grid">${skeletonCards(6, 90)}</div>
  </div>`;

  $('#prj-new').onclick = () => openProjectModal();
  $('#prj-trash').onclick = openTrash;
  $('#prj-q').oninput = (() => {
    let timer = null;
    return (e) => {
      clearTimeout(timer);
      timer = setTimeout(() => { listState.q = e.target.value.trim(); loadProjects(); }, 280);
    };
  })();
  $$('[data-arch]').forEach((btn) => {
    btn.onclick = () => {
      $$('[data-arch]').forEach((b) => b.classList.toggle('active', b === btn));
      listState.archived = btn.dataset.arch === '1';
      loadProjects();
    };
  });
  await loadProjects();
}

async function loadProjects() {
  const grid = $('#prj-grid');
  if (!grid) return;
  try {
    const data = await api(`/projects${qs({ q: listState.q, archived: listState.archived ? 1 : 0, page_size: 50 })}`);
    listState.items = data.list || [];
    if (!listState.items.length) {
      grid.innerHTML = emptyState({
        iconName: 'projects',
        title: listState.archived ? '没有已归档项目' : '还没有项目',
        desc: '创建一个项目，上传知识库文件，让对话基于你的资料回答。',
        action: listState.archived ? '' : '<button class="btn primary" id="prj-empty-new">新建项目</button>',
      });
      const btn = $('#prj-empty-new', grid);
      if (btn) btn.onclick = () => openProjectModal();
      return;
    }
    grid.innerHTML = `<div class="project-grid">${listState.items.map((p) => `
      <div class="project-card" data-id="${p.id}">
        <div class="pc-head">
          <span class="project-ico" style="background:${esc(p.color)}">${esc(p.icon)}</span>
          <div style="flex:1;min-width:0">
            <h4>${esc(p.name)} ${p.is_archived ? '<span class="tag">已归档</span>' : ''}</h4>
            <span class="small muted">${ROLE_LABEL[p.role] || '成员'} · ${p.visibility === 'team' ? '团队可见' : '私有'}</span>
          </div>
          <button class="icon-btn" data-more>${icon('more')}</button>
        </div>
        <p>${esc(p.description || '暂无项目描述')}</p>
        <div class="pc-foot">
          <span>${icon('file')} ${p.file_count} 个文件</span>
          <span>${icon('chat')} ${p.conversation_count} 个会话</span>
          <span style="margin-left:auto">${fmtTime(p.updated_at)}</span>
        </div>
      </div>`).join('')}</div>`;
    grid.querySelectorAll('.project-card').forEach((card) => {
      const p = listState.items.find((x) => x.id === card.dataset.id);
      card.onclick = (e) => {
        if (e.target.closest('[data-more]')) return;
        location.hash = `#/projects/${p.id}`;
      };
      card.querySelector('[data-more]').onclick = (e) => {
        e.stopPropagation();
        showMenu(e.currentTarget, [
          { label: '打开项目', icon: 'external', onClick: () => { location.hash = `#/projects/${p.id}`; } },
          {
            label: p.is_archived ? '取消归档' : '归档项目', icon: 'archive', onClick: async () => {
              if (p.role !== 'owner' && p.role !== 'editor') { toast('无编辑权限', 'error'); return; }
              await api(`/projects/${p.id}`, { method: 'PATCH', body: { is_archived: !p.is_archived } });
              toast(p.is_archived ? '已取消归档' : '已归档', 'success');
              loadProjects();
            },
          },
          { sep: true },
          {
            label: '删除项目', icon: 'trash', danger: true, onClick: () => deleteProjectFlow(p, () => loadProjects()),
          },
        ]);
      };
    });
  } catch (err) {
    grid.innerHTML = `<div class="empty"><p>${esc(err.message)}</p></div>`;
  }
}

function openProjectModal(project = null) {
  const isEdit = !!project;
  let selectedIcon = project?.icon || ICONS[0];
  let selectedColor = project?.color || COLORS[0];
  const body = `
    <div class="field"><label>项目图标</label>
      <div style="display:flex;gap:5px;flex-wrap:wrap" id="pm-icons">
        ${ICONS.map((i) => `<button class="chip ${i === selectedIcon ? 'active' : ''}" data-ico="${i}" style="font-size:16px;width:34px;height:34px;padding:0;justify-content:center">${i}</button>`).join('')}
      </div>
    </div>
    <div class="field"><label>项目名称（≤50 字）</label>
      <input class="input" id="pm-name" maxlength="50" placeholder="例如：2026 产品规划" value="${esc(project?.name || '')}"></div>
    <div class="field"><label>项目描述（≤200 字）</label>
      <textarea class="textarea" id="pm-desc" rows="2" maxlength="200" placeholder="一句话说明项目目标">${esc(project?.description || '')}</textarea></div>
    <div class="row">
      <div class="field"><label>主题色</label>
        <div style="display:flex;gap:6px" id="pm-colors">
          ${COLORS.map((c) => `<button data-color="${c}" style="width:26px;height:26px;border-radius:7px;background:${c};border:2px solid ${c === selectedColor ? '#1F2A44' : 'transparent'}"></button>`).join('')}
        </div>
      </div>
      <div class="field"><label>可见范围</label>
        <select class="select" id="pm-vis">
          <option value="private" ${project?.visibility !== 'team' ? 'selected' : ''}>私有（仅成员）</option>
          <option value="team" ${project?.visibility === 'team' ? 'selected' : ''}>团队可见</option>
        </select>
      </div>
    </div>
    <div class="field"><label>项目指令（可选）</label>
      <textarea class="textarea" id="pm-ins" rows="3" placeholder="例如：回答时优先引用项目文件，并标注文件名与页码">${esc(project?.instructions || '')}</textarea></div>`;

  openModal({
    title: isEdit ? '编辑项目' : '新建项目',
    subtitle: '项目内对话会自动绑定知识库文件',
    body,
    footer: `<button class="btn ghost" data-close>取消</button>
             <button class="btn primary" id="pm-save">${isEdit ? '保存' : '创建项目'}</button>`,
    onMount(el, close) {
      el.querySelectorAll('[data-ico]').forEach((btn) => {
        btn.onclick = () => {
          selectedIcon = btn.dataset.ico;
          el.querySelectorAll('[data-ico]').forEach((b) => b.classList.toggle('active', b === btn));
        };
      });
      el.querySelectorAll('[data-color]').forEach((btn) => {
        btn.onclick = () => {
          selectedColor = btn.dataset.color;
          el.querySelectorAll('[data-color]').forEach((b) => {
            b.style.border = `2px solid ${b.dataset.color === selectedColor ? '#1F2A44' : 'transparent'}`;
          });
        };
      });
      el.querySelector('#pm-save').onclick = async () => {
        const name = el.querySelector('#pm-name').value.trim();
        const description = el.querySelector('#pm-desc').value.trim();
        if (!name) { toast('请输入项目名称', 'error'); return; }
        if (name.length > 50) { toast('名称不能超过 50 字', 'error'); return; }
        if (description.length > 200) { toast('描述不能超过 200 字', 'error'); return; }
        const payload = {
          name, description, icon: selectedIcon, color: selectedColor,
          visibility: el.querySelector('#pm-vis').value,
          instructions: el.querySelector('#pm-ins').value.trim(),
        };
        try {
          if (isEdit) {
            await api(`/projects/${project.id}`, { method: 'PATCH', body: payload });
            toast('项目已更新', 'success');
            close();
            workspace?.reload?.();
          } else {
            const created = await api('/projects', { method: 'POST', body: payload });
            track('create_project', { project_id: created.id });
            toast('项目已创建', 'success');
            close();
            location.hash = `#/projects/${created.id}`;
          }
        } catch (err) { toast(err.message, 'error'); }
      };
    },
  });
}

async function deleteProjectFlow(project, done) {
  const name = await promptDialog({
    title: '删除项目',
    message: `删除后进入回收站保留 30 天。请输入项目名称「${project.name}」以确认。`,
    placeholder: project.name, confirmText: '删除项目',
  });
  if (name === null) return;
  if (name.trim() !== project.name) { toast('项目名称不匹配', 'error'); return; }
  try {
    await api(`/projects/${project.id}${qs({ confirm_name: name.trim() })}`, { method: 'DELETE' });
    toast('项目已删除，可在回收站恢复', 'success');
    if (done) done();
  } catch (err) { toast(err.message, 'error'); }
}

async function openTrash() {
  openModal({
    title: '回收站',
    subtitle: '删除的项目保留 30 天，可随时恢复',
    className: 'wide',
    body: '<div id="trash-box">' + skeletonRows(3) + '</div>',
    onMount(el) {
      const box = el.querySelector('#trash-box');
      const load = () => api('/projects/trash').then((rows) => {
        if (!rows.length) {
          box.innerHTML = emptyState({ iconName: 'trash', title: '回收站是空的', desc: '删除的项目会在这里保留 30 天。' });
          return;
        }
        box.innerHTML = rows.map((p) => `
          <div class="mine-row">
            <span class="project-ico" style="width:34px;height:34px;font-size:17px;background:#64748b">${esc(p.icon)}</span>
            <div style="flex:1"><b style="font-size:13px">${esc(p.name)}</b>
              <span class="small muted">删除于 ${fmtDateTime(p.deleted_at)} · 剩余 ${p.days_left} 天</span></div>
            <button class="btn sm primary" data-restore="${p.id}">恢复</button>
          </div>`).join('');
        box.querySelectorAll('[data-restore]').forEach((btn) => {
          btn.onclick = async () => {
            try {
              await api(`/projects/${btn.dataset.restore}/restore`, { method: 'POST' });
              toast('项目已恢复', 'success');
              load();
              loadProjects();
            } catch (err) { toast(err.message, 'error'); }
          };
        });
      }).catch((err) => { box.innerHTML = `<div class="ws-empty">${esc(err.message)}</div>`; });
      load();
    },
  });
}

// ------------------------------------------------------------------ 工作区
async function renderWorkspace(page, id) {
  page.innerHTML = '<div class="page-inner"><div style="height:320px" class="skeleton"></div></div>';
  let project;
  try {
    project = await api(`/projects/${id}`);
  } catch (err) {
    page.innerHTML = `<div class="page-inner">${emptyState({
      iconName: 'projects', title: '项目不存在', desc: err.message,
      action: '<button class="btn primary" onclick="location.hash=\'#/projects\'">返回项目列表</button>',
    })}</div>`;
    return;
  }

  workspace = {
    id, project, tab: 'chat', folder: '', convId: project.conversations?.[0]?.id || null,
    chat: null,
  };
  const ws = workspace;
  if (window.MAUTO?.setPageTitle) window.MAUTO.setPageTitle(project.name, '项目工作区');

  page.innerHTML = `<div class="workspace">
    <aside class="ws-files">
      <div class="ws-files-head">
        <div style="display:flex;align-items:center;gap:7px">
          <button class="icon-btn" id="ws-back" title="返回项目列表" style="width:26px;height:26px">${icon('arrowLeft')}</button>
          <b style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(project.name)}</b>
        </div>
        <button class="icon-btn" id="ws-upload" title="上传文件" style="width:26px;height:26px">${icon('upload')}</button>
      </div>
      <div style="padding:0 10px 8px;display:flex;gap:4px">
        <input class="input" id="ws-new-folder" placeholder="新建文件夹…" style="height:28px;font-size:11.5px">
        <button class="icon-btn" id="ws-add-folder" style="width:28px;height:28px">${icon('folderPlus')}</button>
      </div>
      <div class="ws-file-list" id="ws-file-list"></div>
      <div style="padding:8px 10px;border-top:1px solid var(--line);font-size:10.5px;color:var(--ink-3)">
        ${project.file_count} 个文件 · 单文件 ≤100MB
      </div>
    </aside>
    <div class="ws-main">
      <div class="ws-tabs">
        <button class="active" data-tab="chat">${icon('chat')} 对话</button>
        <button data-tab="kb">${icon('layers')} 知识库</button>
        <button data-tab="members">${icon('users')} 成员</button>
        <button data-tab="settings">${icon('settings')} 设置</button>
        <span style="margin-left:auto;display:flex;align-items:center;gap:6px">
          ${project.is_archived ? '<span class="tag">已归档</span>' : ''}
          <span class="tag ${project.role === 'owner' ? 'brand' : ''}">${ROLE_LABEL[project.role] || project.role}</span>
        </span>
      </div>
      <div class="ws-body" id="ws-body"></div>
      <input type="file" id="ws-file" hidden multiple accept=".pdf,.docx,.xlsx,.pptx,.txt,.md,.png,.jpg,.jpeg" />
    </div>
  </div>`;

  $('#ws-back').onclick = () => { location.hash = '#/projects'; };
  $('#ws-file').onchange = () => {
    const input = $('#ws-file');
    uploadFiles(input.files);
    input.value = '';
  };
  $('#ws-upload').onclick = () => $('#ws-file').click();
  $('#ws-add-folder').onclick = () => {
    const input = $('#ws-new-folder');
    const name = input.value.trim();
    if (!name) { toast('请输入文件夹名称', 'error'); return; }
    ws.folder = name;
    input.value = '';
    toast(`文件夹「${name}」已创建，上传文件将放入该文件夹`, 'success');
    renderFiles();
    if (ws.tab === 'kb') renderTab();
  };
  $$('.ws-tabs button').forEach((btn) => {
    btn.onclick = () => {
      $$('.ws-tabs button').forEach((b) => b.classList.toggle('active', b === btn));
      ws.tab = btn.dataset.tab;
      renderTab();
    };
  });
  ws.reload = reloadProject;
  ws.renderFiles = renderFiles;

  renderFiles();
  renderTab();

  async function reloadProject() {
    try {
      const fresh = await api(`/projects/${id}`);
      ws.project = fresh;
      renderFiles();
      if (ws.tab === 'members' || ws.tab === 'settings') renderTab();
    } catch (err) { /* ignore */ }
  }

  function renderFiles() {
    const box = $('#ws-file-list');
    if (!box) return;
    const files = ws.project.files || [];
    const folders = ws.project.folders || ['根目录'];
    if (!files.length) {
      box.innerHTML = `<div class="ws-empty" style="padding:24px 12px">还没有文件<br>点击右上角上传</div>`;
      return;
    }
    const groups = {};
    files.forEach((f) => {
      const folder = f.folder || '根目录';
      if (ws.folder && folder !== ws.folder) return;
      (groups[folder] = groups[folder] || []).push(f);
    });
    let html = `<div style="display:flex;gap:4px;flex-wrap:wrap;padding:0 4px 6px">
      <button class="chip ${ws.folder === '' ? 'active' : ''}" data-folder="" style="height:23px;font-size:10.5px">全部</button>
      ${folders.map((f) => `<button class="chip ${ws.folder === f ? 'active' : ''}" data-folder="${esc(f)}" style="height:23px;font-size:10.5px">${esc(f)}</button>`).join('')}
    </div>`;
    if (!Object.keys(groups).length) {
      html += '<div class="ws-empty">该文件夹暂无文件</div>';
    }
    Object.entries(groups).forEach(([folder, list]) => {
      html += `<div class="recent-group" style="padding-left:6px">${esc(folder)}</div>`;
      html += list.map((f) => `<div class="ws-file" data-fid="${f.id}">
        <span class="fi">${extLabel(f.file_name)}</span>
        <span class="fn" title="${esc(f.file_name)}">${esc(f.file_name)}</span>
        <span class="fs">${esc(f.size_text)}</span>
        <button class="icon-btn" data-fmore style="width:22px;height:22px">${icon('more')}</button>
      </div>`).join('');
    });
    box.innerHTML = html;
    box.querySelectorAll('[data-folder]').forEach((btn) => {
      btn.onclick = () => { ws.folder = btn.dataset.folder; renderFiles(); };
    });
    box.querySelectorAll('.ws-file').forEach((row) => {
      const file = files.find((f) => f.id === row.dataset.fid);
      row.querySelector('[data-fmore]').onclick = (e) => {
        e.stopPropagation();
        showMenu(e.currentTarget, [
          { label: '下载', icon: 'download', onClick: () => download(`/project-files/${file.id}`, file.file_name) },
          {
            label: '重命名', icon: 'edit', onClick: async () => {
              const name = await promptDialog({ title: '重命名文件', value: file.file_name });
              if (!name) return;
              await api(`/projects/${id}/files/${file.id}`, { method: 'PATCH', body: { file_name: name } });
              toast('已重命名', 'success');
              reloadProject();
            },
          },
          {
            label: '移动文件夹', icon: 'folderPlus', onClick: async () => {
              const folder = await promptDialog({ title: '移动到文件夹', value: file.folder || '根目录' });
              if (!folder) return;
              await api(`/projects/${id}/files/${file.id}`, { method: 'PATCH', body: { folder } });
              toast('已移动', 'success');
              reloadProject();
            },
          },
          {
            label: '重新解析', icon: 'refresh', onClick: async () => {
              try {
                const res = await api(`/projects/${id}/files/${file.id}/reparse`, { method: 'POST' });
                toast(res.parse_status === 'parsed' ? `解析完成，提取 ${res.text_len} 字` : '解析失败', res.parse_status === 'parsed' ? 'success' : 'error');
                reloadProject();
              } catch (err) { toast(err.message, 'error'); }
            },
          },
          { sep: true },
          {
            label: '删除文件', icon: 'trash', danger: true, onClick: async () => {
              const ok = await confirmDialog({ title: '删除文件', message: `确定删除「${file.file_name}」吗？`, danger: true, confirmText: '删除' });
              if (!ok) return;
              await api(`/projects/${id}/files/${file.id}`, { method: 'DELETE' });
              toast('已删除', 'success');
              reloadProject();
            },
          },
        ]);
      };
    });
  }

  async function uploadFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    for (const file of files) {
      const form = new FormData();
      form.append('file', file);
      form.append('folder', ws.folder || '根目录');
      try {
        await api(`/projects/${id}/files`, { method: 'POST', form });
        toast(`${file.name} 已上传并解析`, 'success', 1600);
      } catch (err) {
        toast(`${file.name}：${err.message}`, 'error');
      }
    }
    await reloadProject();
    if (ws.tab === 'kb') renderTab();
  }

  function renderTab() {
    if (ws.chat) { try { ws.chat.destroy(); } catch (err) { /* ignore */ } ws.chat = null; }
    const box = $('#ws-body');
    if (!box) return;
    box.style.overflow = ws.tab === 'chat' ? 'hidden' : 'auto';
    if (ws.tab === 'chat') renderChatTab(box);
    else if (ws.tab === 'kb') renderKbTab(box);
    else if (ws.tab === 'members') renderMembersTab(box);
    else renderSettingsTab(box);
  }

  function renderChatTab(box) {
    const convs = ws.project.conversations || [];
    box.innerHTML = `
      <div style="display:flex;align-items:center;gap:8px;padding:8px 16px;border-bottom:1px solid var(--line-2);background:#fff">
        <span class="small muted">项目会话</span>
        <select class="select" id="ws-conv" style="max-width:260px;height:30px;font-size:12px">
          <option value="">新会话</option>
          ${convs.map((c) => `<option value="${c.id}" ${ws.convId === c.id ? 'selected' : ''}>${esc(c.title)}</option>`).join('')}
        </select>
        <button class="btn sm ghost" id="ws-conv-new">${icon('plus')} 新建会话</button>
        <span class="small muted" style="margin-left:auto">回答会引用左侧知识库并在末尾标注来源</span>
      </div>
      <div id="ws-chat" style="flex:1;min-height:0;display:flex"></div>`;
    const mount = async (convId) => {
      let conv = null;
      if (convId) {
        try { conv = await api(`/conversations/${convId}`); } catch (err) { toast(err.message, 'error'); }
      }
      const holder = $('#ws-chat');
      if (!holder) return;
      holder.innerHTML = '';
      if (ws.chat) { try { ws.chat.destroy(); } catch (err) { /* ignore */ } }
      ws.chat = chatView.mountChat(holder, {
        conversationId: convId || null, conversation: conv, projectId: id, projectName: ws.project.name,
      });
      ws.convId = convId || null;
    };
    $('#ws-conv').onchange = (e) => mount(e.target.value || null);
    $('#ws-conv-new').onclick = async () => {
      try {
        const conv = await api(`/projects/${id}/conversations`, { method: 'POST' });
        ws.project.conversations = [conv, ...(ws.project.conversations || [])];
        $('#ws-conv').insertAdjacentHTML('afterbegin', `<option value="${conv.id}">${esc(conv.title)}</option>`);
        $('#ws-conv').value = conv.id;
        await mount(conv.id);
      } catch (err) { toast(err.message, 'error'); }
    };
    mount(ws.convId);
  }

  function renderKbTab(box) {
    const files = ws.project.files || [];
    box.style.overflow = 'auto';
    box.innerHTML = `<div style="padding:18px">
      <div class="dropzone" id="kb-drop">${icon('upload')}<div style="margin-top:8px">点击或拖拽文件到此处上传</div>
        <div class="small muted" style="margin-top:4px">支持 PDF / Word / Excel / PPT / TXT / Markdown / 图片，单文件 ≤100MB</div>
      </div>
      <div class="section-title" style="margin-top:20px"><div><h3 style="font-size:13.5px">知识库文件（${files.length}/500）</h3>
        <p>解析后的文本会用于项目内问答，并在回答末尾标注文件名</p></div></div>
      <div class="card">${files.length ? files.map((f) => `<div class="mine-row" data-fid="${f.id}">
        <span class="fi" style="width:26px;height:26px;border-radius:6px;background:#eaf0fa;display:flex;align-items:center;justify-content:center;font-size:10px;color:var(--brand)">${extLabel(f.file_name)}</span>
        <div style="flex:1;min-width:0">
          <b style="font-size:12.5px">${esc(f.file_name)}</b>
          <span class="small muted">${esc(f.folder || '根目录')} · ${esc(f.size_text)} · ${f.parse_status === 'parsed' ? `已解析 ${f.text_len} 字` : '解析失败'}</span>
        </div>
        <span class="tag ${f.parse_status === 'parsed' ? 'success' : 'danger'}">${f.parse_status === 'parsed' ? '已解析' : '失败'}</span>
        <button class="btn sm ghost" data-dl>下载</button>
        <button class="btn sm ghost" data-re>重新解析</button>
        <button class="icon-btn" data-del>${icon('trash')}</button>
      </div>`).join('') : '<div class="ws-empty">还没有文件，上传后即可开始项目对话。</div>'}</div>
    </div>`;
    box.querySelector('#kb-drop').onclick = () => $('#ws-file').click();
    const drop = box.querySelector('#kb-drop');
    drop.ondragover = (e) => { e.preventDefault(); drop.classList.add('drag'); };
    drop.ondragleave = () => drop.classList.remove('drag');
    drop.ondrop = async (e) => {
      e.preventDefault();
      drop.classList.remove('drag');
      await uploadFiles(e.dataTransfer.files);
    };
    box.querySelectorAll('.mine-row[data-fid]').forEach((row) => {
      const f = files.find((x) => x.id === row.dataset.fid);
      if (!f) return;
      row.querySelector('[data-dl]').onclick = () => download(`/project-files/${f.id}`, f.file_name);
      row.querySelector('[data-re]').onclick = async () => {
        try {
          const res = await api(`/projects/${id}/files/${f.id}/reparse`, { method: 'POST' });
          toast(res.parse_status === 'parsed' ? '重新解析完成' : '解析失败', res.parse_status === 'parsed' ? 'success' : 'error');
          reloadProject();
          renderTab();
        } catch (err) { toast(err.message, 'error'); }
      };
      row.querySelector('[data-del]').onclick = async () => {
        const ok = await confirmDialog({ title: '删除文件', message: `确定删除「${f.file_name}」吗？`, danger: true, confirmText: '删除' });
        if (!ok) return;
        await api(`/projects/${id}/files/${f.id}`, { method: 'DELETE' });
        toast('已删除', 'success');
        reloadProject();
        renderTab();
      };
    });
  }

  function renderMembersTab(box) {
    box.style.overflow = 'auto';
    const members = ws.project.members || [];
    const owner = ws.project.owner || {};
    box.innerHTML = `<div style="padding:18px;max-width:720px">
      <div class="section-title"><div><h3 style="font-size:13.5px">项目成员</h3><p>拥有者可管理成员权限，编辑者可上传与对话，查看者只读</p></div></div>
      <div class="card" style="padding:4px 16px;margin-bottom:18px">
        <div class="member-row">
          ${avatarHtml(owner.nickname, '', 28)}
          <div class="mm"><b>${esc(owner.nickname || '')}</b><small>${esc(owner.email || '')}</small></div>
          <span class="tag brand">拥有者</span>
        </div>
        ${members.filter((m) => m.user_id !== owner.id).map((m) => `
          <div class="member-row" data-mid="${m.id}">
            ${avatarHtml(m.nickname, '', 28)}
            <div class="mm"><b>${esc(m.nickname)}</b><small>${esc(m.email)}</small></div>
            ${ws.project.role === 'owner' ? `<select class="select" data-role style="width:100px;height:28px;font-size:11.5px">
              <option value="editor" ${m.role === 'editor' ? 'selected' : ''}>编辑者</option>
              <option value="viewer" ${m.role === 'viewer' ? 'selected' : ''}>查看者</option>
            </select>` : `<span class="tag">${ROLE_LABEL[m.role] || m.role}</span>`}
            ${ws.project.role === 'owner' ? `<button class="icon-btn" data-rm>${icon('close')}</button>` : ''}
          </div>`).join('')}
      </div>
      ${ws.project.role === 'owner' ? `<div class="section-title"><div><h3 style="font-size:13.5px">邀请成员</h3></div></div>
      <div class="row" style="max-width:520px">
        <input class="input" id="inv-email" placeholder="成员注册邮箱">
        <select class="select" id="inv-role" style="max-width:120px"><option value="viewer">查看者</option><option value="editor">编辑者</option></select>
        <button class="btn primary" id="inv-btn" style="flex:none">邀请</button>
      </div>` : '<p class="small muted">仅拥有者可以邀请成员。</p>'}
    </div>`;
    if (ws.project.role !== 'owner') return;
    $('#inv-btn').onclick = async () => {
      const email = $('#inv-email').value.trim();
      if (!email) { toast('请输入邮箱', 'error'); return; }
      try {
        await api(`/projects/${id}/members`, { method: 'POST', body: { email, role: $('#inv-role').value } });
        toast('成员已加入项目', 'success');
        reloadProject();
        renderTab();
      } catch (err) { toast(err.message, 'error'); }
    };
    box.querySelectorAll('[data-mid]').forEach((row) => {
      const mid = row.dataset.mid;
      const roleSel = row.querySelector('[data-role]');
      if (roleSel) {
        roleSel.onchange = async () => {
          await api(`/projects/${id}/members/${mid}`, { method: 'PATCH', body: { role: roleSel.value } });
          toast('权限已更新', 'success');
          reloadProject();
        };
      }
      const rm = row.querySelector('[data-rm]');
      if (rm) {
        rm.onclick = async () => {
          const ok = await confirmDialog({ title: '移除成员', message: '该成员将无法再访问项目。', danger: true, confirmText: '移除' });
          if (!ok) return;
          await api(`/projects/${id}/members/${mid}`, { method: 'DELETE' });
          toast('已移除成员', 'success');
          reloadProject();
          renderTab();
        };
      }
    });
  }

  function renderSettingsTab(box) {
    box.style.overflow = 'auto';
    const p = ws.project;
    box.innerHTML = `<div style="padding:18px;max-width:720px">
      <div class="section-title"><div><h3 style="font-size:13.5px">项目信息</h3><p>名称、描述与可见范围</p></div>
        <button class="btn sm ghost" id="set-edit">编辑信息</button></div>
      <div class="card" style="padding:16px;margin-bottom:18px">
        <div style="display:flex;gap:12px;align-items:center;margin-bottom:10px">
          <span class="project-ico" style="background:${esc(p.color)}">${esc(p.icon)}</span>
          <div><b style="font-size:14px">${esc(p.name)}</b>
            <div class="small muted">${p.visibility === 'team' ? '团队可见' : '私有'} · ${ROLE_LABEL[p.role]}</div></div>
        </div>
        <p class="small" style="line-height:1.7;color:var(--ink-2)">${esc(p.description || '暂无描述')}</p>
      </div>
      <div class="section-title"><div><h3 style="font-size:13.5px">项目指令</h3><p>项目内对话会遵循以下补充说明</p></div></div>
      <textarea class="textarea" id="set-ins" rows="4" placeholder="例如：回答时优先引用项目文件，并标注文件名与页码">${esc(p.instructions || '')}</textarea>
      <button class="btn primary" id="set-save" style="margin-top:10px">保存项目指令</button>
      <div class="divider"></div>
      <div class="section-title"><div><h3 style="font-size:13.5px">归档与删除</h3><p>归档后不在进行中列表展示；删除保留 30 天</p></div></div>
      <div style="display:flex;gap:8px">
        <button class="btn ghost" id="set-arch">${p.is_archived ? '取消归档' : '归档项目'}</button>
        ${p.role === 'owner' ? '<button class="btn ghost-danger" id="set-del">删除项目</button>' : ''}
      </div>
    </div>`;
    $('#set-edit').onclick = () => openProjectModal(p);
    $('#set-save').onclick = async () => {
      try {
        await api(`/projects/${id}`, { method: 'PATCH', body: { instructions: $('#set-ins').value } });
        toast('项目指令已保存', 'success');
        reloadProject();
      } catch (err) { toast(err.message, 'error'); }
    };
    $('#set-arch').onclick = async () => {
      await api(`/projects/${id}`, { method: 'PATCH', body: { is_archived: !p.is_archived } });
      toast(p.is_archived ? '已取消归档' : '项目已归档', 'success');
      reloadProject();
      renderTab();
    };
    const del = $('#set-del');
    if (del) del.onclick = () => deleteProjectFlow(p, () => { location.hash = '#/projects'; });
  }
}

function extLabel(name = '') {
  const ext = name.split('.').pop().toUpperCase();
  if (ext.length <= 4) return ext;
  return 'FILE';
}

void state;
