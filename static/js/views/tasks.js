// 任务视图：定时任务列表、模板创建、编辑、立即执行与执行历史
import { api, qs } from '../api.js?v=19';
import { icon } from '../icons.js?v=19';
import { renderMarkdown } from '../markdown.js?v=19';
import {
  $, $$, esc, fmtDateTime, fmtTime, toast, openModal, confirmDialog, showMenu,
  emptyState, skeletonRows, track,
} from '../ui.js?v=19';

const FREQ_LABELS = { daily: '每天', workdays: '工作日', weekly: '每周', monthly: '每月' };
const WEEK_NAMES = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
const TZ_OPTIONS = [
  ['Asia/Shanghai', '中国标准时间（UTC+8）'],
  ['Asia/Tokyo', '日本标准时间（UTC+9）'],
  ['UTC', '协调世界时（UTC）'],
  ['Europe/London', '伦敦时间'],
  ['America/New_York', '纽约时间'],
];
const NOTIFY = [['inbox', '站内通知'], ['email', '邮件'], ['wechat', '微信']];
const NOTIFY_LABEL = Object.fromEntries(NOTIFY);

let state = { status: '', tasks: [], enabled: 0, max: 20, total: 0 };

export async function render(page) {
  state = { status: '', tasks: [], enabled: 0, max: 20, total: 0 };
  page.innerHTML = `<div class="page-inner">
    <div class="page-head">
      <div>
        <h1>任务</h1>
        <p>用自然语言设定周期性指令，MAuto 会按时执行并把结果推送到通知中心。</p>
      </div>
      <div class="actions">
        <button class="btn ghost" id="task-tpl">${icon('grid')} 模板库</button>
        <button class="btn primary" id="task-new">${icon('plus')} 新建任务</button>
      </div>
    </div>
    <div class="task-stats" id="task-stats">${'<div class="stat-card"><div class="skeleton" style="height:44px"></div></div>'.repeat(4)}</div>
    <div class="tabs" id="task-tabs">
      <button class="active" data-status="">全部</button>
      <button data-status="enabled">启用中</button>
      <button data-status="paused">已暂停</button>
    </div>
    <div class="card" id="task-list">${skeletonRows(3)}</div>
  </div>`;

  $('#task-new').onclick = () => openTaskModal(null, refresh);
  $('#task-tpl').onclick = () => openTemplates();
  $$('#task-tabs button').forEach((btn) => {
    btn.onclick = () => {
      $$('#task-tabs button').forEach((b) => b.classList.toggle('active', b === btn));
      state.status = btn.dataset.status;
      refresh();
    };
  });
  await refresh();
}

async function refresh() {
  const list = $('#task-list');
  if (!list) return;
  try {
    const data = await api(`/tasks${qs({ status: state.status, page_size: 100 })}`);
    state.tasks = data.list || [];
    state.enabled = data.enabled_count;
    state.max = data.max_enabled;
    state.total = data.total;
    renderStats();
    renderList();
  } catch (err) {
    list.innerHTML = `<div class="empty"><p>${esc(err.message)}</p></div>`;
  }
}

function renderStats() {
  const box = $('#task-stats');
  if (!box) return;
  const paused = state.tasks.filter((t) => t.status === 'paused').length;
  const today = new Date().toDateString();
  const todayRuns = state.tasks.filter((t) => t.last_run_at && new Date(t.last_run_at).toDateString() === today).length;
  box.innerHTML = `
    <div class="stat-card"><span>启用中任务</span><b>${state.enabled}<small class="muted" style="font-size:12px;font-weight:400"> / ${state.max}</small></b></div>
    <div class="stat-card"><span>全部任务</span><b>${state.total}</b></div>
    <div class="stat-card"><span>今日已执行</span><b>${todayRuns}</b></div>
    <div class="stat-card"><span>暂停中</span><b>${paused}</b></div>`;
}

function statusTagOf(t) {
  if (t.status === 'paused') return '<span class="tag">已暂停</span>';
  if (t.last_status === 'failed') return '<span class="tag danger">执行失败</span>';
  if (t.last_status === 'success') return '<span class="tag success">运行正常</span>';
  return '<span class="tag brand">等待首次执行</span>';
}

function renderList() {
  const box = $('#task-list');
  if (!box) return;
  if (!state.tasks.length) {
    box.innerHTML = emptyState({
      iconName: 'tasks',
      title: state.status ? '该状态下暂无任务' : '还没有定时任务',
      desc: '从模板快速开始，或新建一个属于你的自动化任务。',
      action: '<button class="btn primary" id="empty-new">新建任务</button>',
    });
    const btn = $('#empty-new', box);
    if (btn) btn.onclick = () => openTaskModal(null, refresh);
    return;
  }
  box.innerHTML = state.tasks.map((t) => `
    <div class="task-card" data-id="${t.id}">
      <div class="task-icon">${icon('clock')}</div>
      <div class="task-main">
        <h4>${esc(t.name)} ${statusTagOf(t)}</h4>
        <div class="task-desc">${esc(t.prompt)}</div>
        <div class="task-meta">
          <span>${icon('calendar')} <b>${esc(t.freq_text)}</b></span>
          <span>下次执行：<b>${t.status === 'enabled' ? fmtDateTime(t.next_run_at) : '—'}</b></span>
          <span>通知：<b>${(t.notify_types || []).map((n) => NOTIFY_LABEL[n] || n).join(' / ') || '站内通知'}</b></span>
        </div>
      </div>
      <div class="task-actions">
        <button class="btn sm ghost" data-run>${icon('play')} 立即执行</button>
        <button class="btn sm ghost" data-toggle>${t.status === 'enabled' ? '暂停' : '启用'}</button>
        <button class="icon-btn" data-more>${icon('more')}</button>
      </div>
    </div>`).join('');

  box.querySelectorAll('.task-card').forEach((row) => {
    const task = state.tasks.find((t) => t.id === row.dataset.id);
    row.querySelector('[data-run]').onclick = async (e) => {
      e.stopPropagation();
      await runNow(task);
    };
    row.querySelector('[data-toggle]').onclick = async (e) => {
      e.stopPropagation();
      try {
        await api(`/tasks/${task.id}`, {
          method: 'PATCH',
          body: { status: task.status === 'enabled' ? 'paused' : 'enabled' },
        });
        toast(task.status === 'enabled' ? '任务已暂停' : '任务已启用', 'success');
        refresh();
      } catch (err) { toast(err.message, 'error'); }
    };
    row.querySelector('[data-more]').onclick = (e) => {
      e.stopPropagation();
      showMenu(e.currentTarget, [
        { label: '查看详情', icon: 'eye', onClick: () => openTaskDetail(task) },
        { label: '编辑任务', icon: 'edit', onClick: () => openTaskModal(task, refresh) },
        { sep: true },
        {
          label: '删除任务', icon: 'trash', danger: true, onClick: async () => {
            const ok = await confirmDialog({
              title: '删除任务', message: `确定删除「${task.name}」吗？执行历史也会一并删除。`,
              confirmText: '删除', danger: true,
            });
            if (!ok) return;
            await api(`/tasks/${task.id}`, { method: 'DELETE' });
            toast('任务已删除', 'success');
            refresh();
          },
        },
      ]);
    };
    row.onclick = () => openTaskDetail(task);
  });
}

async function runNow(task) {
  const t = toast('正在执行任务…', 'info', 60000);
  try {
    const res = await api(`/tasks/${task.id}/run`, { method: 'POST' });
    track('run_task', { task_id: task.id, status: res.status });
    if (res.status === 'success') {
      task.output_conversation_id = res.conversation_id || task.output_conversation_id;
      toast('执行完成，产出已生成', 'success');
      showRunOutput(task, res);
    } else {
      toast(res.error_msg || '执行失败', 'error');
    }
    refresh();
  } catch (err) {
    toast(err.message, 'error');
  }
  void t;
}

// 产出详情：摘要 + 完整内容（Markdown 渲染），并提供进入会话的入口
function showRunOutput(task, run) {
  const summary = run.summary || '';
  const full = run.result || run.result_content || '';
  openModal({
    title: '任务产出',
    subtitle: `${task.name} · ${fmtDateTime(run.run_at || new Date().toISOString())}`,
    className: 'wide',
    body: `
      ${summary ? `<div class="output-summary"><b>本次摘要</b><p>${esc(summary)}</p></div>` : ''}
      <div class="md output-doc">${renderMarkdown(full || '（无输出）')}</div>`,
    footer: `<button class="btn ghost" data-close>关闭</button>
             ${run.conversation_id ? `<button class="btn primary" id="out-chat">${icon('chat')} 在对话中查看</button>` : ''}`,
    onMount(el, close) {
      const chatBtn = el.querySelector('#out-chat');
      if (chatBtn) {
        chatBtn.onclick = () => {
          close();
          location.hash = `#/chat/${run.conversation_id}`;
        };
      }
    },
  });
}

// ------------------------------------------------------------------ 模板库
async function openTemplates() {
  let templates = [];
  try {
    templates = await api('/task-templates');
  } catch (err) {
    toast(err.message, 'error');
    return;
  }
  openModal({
    title: '任务模板',
    subtitle: '选择一个模板，快速创建自动化任务',
    body: `<div class="plugin-grid">${templates.map((t) => `
      <button class="task-template-card" data-tpl="${t.id}">
        <b>${esc(t.name)}</b>
        <span>${esc(t.desc)}</span>
        <div style="margin-top:8px"><span class="tag brand">${FREQ_LABELS[t.freq_type] || t.freq_type} ${esc(t.run_time)}</span></div>
      </button>`).join('')}</div>`,
    onMount(el, close) {
      el.querySelectorAll('[data-tpl]').forEach((btn) => {
        btn.onclick = () => {
          const tpl = templates.find((t) => t.id === btn.dataset.tpl);
          close();
          openTaskModal(null, refresh, tpl);
        };
      });
    },
  });
}

// ------------------------------------------------------------------ 新建 / 编辑
async function openTaskModal(task, onSaved, template = null) {
  let templates = [];
  try {
    templates = await api('/task-templates');
  } catch (err) { /* 模板不是必须的 */ }

  const isEdit = !!task;
  const base = task || template || {};
  const freqType = base.freq_type || 'daily';
  const freqValue = (base.freq_value || '').split(',').filter(Boolean);
  const notify = task?.notify_types || ['inbox'];

  const body = `
    ${!isEdit && templates.length ? `
    <div style="margin-bottom:16px">
      <div class="field"><label>从模板开始（可选）</label></div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px">
        ${templates.map((t) => `<button class="task-template-card" data-tpl="${t.id}">
          <b>${esc(t.name)}</b><span>${esc(t.desc)}</span></button>`).join('')}
      </div>
    </div>
    <div class="divider"></div>` : ''}
    <div class="field"><label>任务名称（≤30 字）</label>
      <input class="input" id="t-name" maxlength="30" placeholder="例如：每日行业早报" value="${esc(base.name || '')}"></div>
    <div class="field"><label>执行指令（≤2000 字）</label>
      <textarea class="textarea" id="t-prompt" rows="4" maxlength="2000" placeholder="描述希望 MAuto 每次执行什么任务…">${esc(base.prompt || '')}</textarea></div>
    <div class="row">
      <div class="field"><label>执行频率</label>
        <select class="select" id="t-freq">
          ${Object.entries(FREQ_LABELS).map(([k, v]) => `<option value="${k}" ${freqType === k ? 'selected' : ''}>${v}</option>`).join('')}
        </select>
      </div>
      <div class="field"><label>执行时间</label>
        <input class="input" id="t-time" type="time" value="${esc(base.run_time || '08:00')}"></div>
    </div>
    <div class="field" id="t-week-wrap"><label>每周执行日</label>
      <div style="display:flex;gap:6px;flex-wrap:wrap" id="t-week">
        ${WEEK_NAMES.map((name, i) => `<button class="chip ${freqValue.includes(String(i)) ? 'active' : ''}" data-week="${i}">${name}</button>`).join('')}
      </div>
    </div>
    <div class="field hidden" id="t-month-wrap"><label>每月执行日（1-28）</label>
      <input class="input" id="t-month" type="number" min="1" max="28" value="${esc(freqValue[0] || '1')}"></div>
    <div class="row">
      <div class="field"><label>时区</label>
        <select class="select" id="t-tz">
          ${TZ_OPTIONS.map(([v, label]) => `<option value="${v}" ${(base.timezone || 'Asia/Shanghai') === v ? 'selected' : ''}>${label}</option>`).join('')}
        </select>
      </div>
      <div class="field"><label>输出格式</label>
        <select class="select" id="t-format">
          <option value="chat" ${(base.output_format || 'chat') === 'chat' ? 'selected' : ''}>对话卡片</option>
          <option value="markdown" ${base.output_format === 'markdown' ? 'selected' : ''}>Markdown 文档</option>
        </select>
      </div>
    </div>
    <div class="field"><label>通知方式（可多选）</label>
      <div style="display:flex;gap:6px;flex-wrap:wrap" id="t-notify">
        ${NOTIFY.map(([v, label]) => `<button class="chip ${notify.includes(v) ? 'active' : ''}" data-notify="${v}">${label}</button>`).join('')}
      </div>
    </div>
    <p class="small muted">提示：同一账号下，任务名称 + 频率 + 时间相同的任务不可重复创建；最多 20 个启用中任务。</p>`;

  openModal({
    title: isEdit ? '编辑任务' : '新建任务',
    subtitle: isEdit ? '修改后立即生效，下次执行时间会自动重算' : '设定指令与时间，到点自动执行',
    className: 'wide',
    body,
    footer: `<button class="btn ghost" data-close>取消</button>
             <button class="btn primary" id="t-save">${isEdit ? '保存修改' : '创建任务'}</button>`,
    onMount(el, close) {
      const freqEl = el.querySelector('#t-freq');
      const weekWrap = el.querySelector('#t-week-wrap');
      const monthWrap = el.querySelector('#t-month-wrap');
      const syncFreq = () => {
        weekWrap.classList.toggle('hidden', freqEl.value !== 'weekly');
        monthWrap.classList.toggle('hidden', freqEl.value !== 'monthly');
      };
      freqEl.onchange = syncFreq;
      syncFreq();
      el.querySelectorAll('[data-week]').forEach((chip) => {
        chip.onclick = () => chip.classList.toggle('active');
      });
      el.querySelectorAll('[data-notify]').forEach((chip) => {
        chip.onclick = () => chip.classList.toggle('active');
      });
      el.querySelectorAll('[data-tpl]').forEach((btn) => {
        btn.onclick = () => {
          const tpl = templates.find((t) => t.id === btn.dataset.tpl);
          if (!tpl) return;
          el.querySelector('#t-name').value = tpl.name;
          el.querySelector('#t-prompt').value = tpl.prompt;
          el.querySelector('#t-freq').value = tpl.freq_type;
          el.querySelector('#t-time').value = tpl.run_time;
          freqEl.dispatchEvent(new Event('change'));
          if (tpl.freq_type === 'weekly') {
            el.querySelectorAll('[data-week]').forEach((c) => c.classList.toggle('active', c.dataset.week === tpl.freq_value));
          }
          el.querySelectorAll('[data-tpl]').forEach((b) => b.classList.toggle('active', b === btn));
        };
      });
      el.querySelector('#t-save').onclick = async () => {
        const name = el.querySelector('#t-name').value.trim();
        const prompt = el.querySelector('#t-prompt').value.trim();
        const ft = freqEl.value;
        let fv = '';
        if (ft === 'weekly') {
          fv = $$('[data-week].active', el).map((c) => c.dataset.week).join(',');
        } else if (ft === 'monthly') {
          fv = el.querySelector('#t-month').value;
        }
        const notifyTypes = $$('[data-notify].active', el).map((c) => c.dataset.notify);
        if (!name) { toast('请输入任务名称', 'error'); return; }
        if (!prompt) { toast('请输入执行指令', 'error'); return; }
        if (!notifyTypes.length) { toast('请至少选择一种通知方式', 'error'); return; }
        const payload = {
          name, prompt, freq_type: ft, freq_value: fv,
          run_time: el.querySelector('#t-time').value || '08:00',
          timezone: el.querySelector('#t-tz').value,
          notify_types: notifyTypes,
          output_format: el.querySelector('#t-format').value,
        };
        try {
          if (isEdit) await api(`/tasks/${task.id}`, { method: 'PATCH', body: payload });
          else await api('/tasks', { method: 'POST', body: payload });
          track(isEdit ? 'run_task' : 'create_task', { freq: ft });
          toast(isEdit ? '任务已更新' : '任务已创建', 'success');
          close();
          if (onSaved) onSaved();
        } catch (err) {
          toast(err.message, 'error');
        }
      };
    },
  });
}

// ------------------------------------------------------------------ 详情与执行历史
async function openTaskDetail(task) {
  const body = `
    <div style="display:flex;gap:10px;align-items:center;margin-bottom:14px">
      <div class="task-icon">${icon('clock')}</div>
      <div><b style="font-size:15px">${esc(task.name)}</b>
        <div class="small muted">${esc(task.freq_text)} · 下次执行 ${task.status === 'enabled' ? fmtDateTime(task.next_run_at) : '—'}</div></div>
    </div>
    <div class="field"><label>执行指令</label>
      <div class="code-block" style="white-space:pre-wrap">${esc(task.prompt)}</div></div>
    <div class="task-meta" style="margin-bottom:14px">
      <span>状态：<b>${task.status === 'enabled' ? '启用中' : '已暂停'}</b></span>
      <span>通知：<b>${(task.notify_types || []).map((n) => NOTIFY_LABEL[n] || n).join(' / ')}</b></span>
      <span>输出：<b>${task.output_format === 'markdown' ? 'Markdown 文档' : '对话卡片'}</b></span>
      <span>连续失败：<b>${task.fail_count || 0} 次</b></span>
    </div>
    <div class="task-output-bar">
      <span class="small muted">${task.output_conversation_id
        ? '每次执行的完整内容都会写入任务产出会话，可随时回看。'
        : '首次执行成功后会自动生成产出会话。'}</span>
      ${task.output_conversation_id
        ? `<button class="btn sm soft" id="d-output">${icon('chat')} 打开产出会话</button>` : ''}
    </div>
    <div class="divider"></div>
    <div class="section-title"><div><h3 style="font-size:13.5px">执行历史</h3><p>保留最近 30 条执行记录</p></div></div>
    <div class="runs-list" id="runs-list"><div class="skeleton" style="height:60px"></div></div>`;

  openModal({
    title: '任务详情',
    className: 'wide',
    body,
    footer: `<button class="btn ghost" id="d-runs">刷新</button>
             <button class="btn ghost" id="d-edit">编辑</button>
             <button class="btn primary" id="d-run">立即执行一次</button>`,
    onMount(el, close) {
      const box = el.querySelector('#runs-list');
      const loadRuns = async () => {
        box.innerHTML = '<div class="skeleton" style="height:60px"></div>';
        try {
          const data = await api(`/tasks/${task.id}/runs${qs({ page_size: 30 })}`);
          if (!data.list.length) {
            box.innerHTML = '<div class="ws-empty">暂无执行记录，点击「立即执行一次」试试。</div>';
            return;
          }
          box.innerHTML = data.list.map((r) => `
            <div class="run-item">
              <div class="run-head">
                <span>${fmtDateTime(r.run_at)} · 第 ${r.attempt} 次尝试</span>
                <span class="tag ${r.status === 'success' ? 'success' : 'danger'}">${r.status === 'success' ? '成功' : '失败'} · ${r.duration_ms}ms</span>
              </div>
              <div class="run-body">${esc(r.error_msg || r.summary || (r.result_content || '').slice(0, 300) || '（无输出）')}</div>
              ${r.status === 'success' ? `<div class="run-actions">
                <button class="btn sm ghost" data-view="${r.id}">${icon('eye')} 查看完整产出</button>
                ${r.conversation_id ? `<button class="btn sm soft" data-chat="${esc(r.conversation_id)}">${icon('chat')} 在对话中查看</button>` : ''}
              </div>` : ''}
            </div>`).join('');
          box.querySelectorAll('[data-view]').forEach((btn) => {
            btn.onclick = () => {
              const run = data.list.find((r) => r.id === btn.dataset.view);
              if (run) showRunOutput(task, run);
            };
          });
          box.querySelectorAll('[data-chat]').forEach((btn) => {
            btn.onclick = () => {
              close();
              location.hash = `#/chat/${btn.dataset.chat}`;
            };
          });
        } catch (err) {
          box.innerHTML = `<div class="ws-empty">${esc(err.message)}</div>`;
        }
      };
      el.querySelector('#d-run').onclick = async () => {
        await runNow(task);
        setTimeout(loadRuns, 300);
      };
      const outputBtn = el.querySelector('#d-output');
      if (outputBtn) {
        outputBtn.onclick = () => {
          close();
          location.hash = `#/chat/${task.output_conversation_id}`;
        };
      }
      el.querySelector('#d-runs').onclick = loadRuns;
      el.querySelector('#d-edit').onclick = () => {
        close();
        openTaskModal(task, refresh);
      };
      loadRuns();
    },
  });
}

void fmtTime;
