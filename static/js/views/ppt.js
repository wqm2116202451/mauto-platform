// PPT 视图：模板库、AI 生成向导、在线编辑、版本与导出
import { api, qs, download, state as authState } from '../api.js?v=19';
import { icon } from '../icons.js?v=19';
import {
  $, $$, esc, fmtTime, fmtDateTime, fmtNum, toast, openModal, confirmDialog,
  promptDialog, emptyState, skeletonCards, track, showMenu,
} from '../ui.js?v=19';

const CATEGORIES = ['全部', '商务汇报', '教育教学', '科技互联网', '医疗健康', '政府政务', '简约通用'];
const STYLES = ['全部', '简约', '科技', '国潮', '扁平'];
const PAGE_TYPE_LABELS = {
  cover: '封面', agenda: '目录 / 议程', section: '章节过渡', bullets: '要点列表',
  metrics: '数据 / 指标', chart: '图表', quote: '金句 / 引用', compare: '对比', end: '结尾',
};

let home = null;
let editor = null;

export async function render(page, { id }) {
  if (id) {
    await renderEditor(page, id);
    return;
  }
  await renderHome(page);
}

export function destroy() {
  if (editor) {
    const ed = editor;
    editor = null;
    ed.destroyed = true;
    if (ed.timer) clearInterval(ed.timer);
    if (ed.previewTimer) clearTimeout(ed.previewTimer);
    Object.values(ed.thumbUrls || {}).forEach((u) => URL.revokeObjectURL(u));
    if (ed.canvasUrl) URL.revokeObjectURL(ed.canvasUrl);
    if (ed.dirty && ed.saveNow) ed.saveNow(true);
  }
}

// ------------------------------------------------------------------ 首页
async function renderHome(page) {
  home = {
    tab: 'templates', category: '全部', style: '全部', q: '', templates: [], myDocs: [],
  };
  page.innerHTML = `<div class="page-inner">
    <div class="page-head">
      <div>
        <h1>PPT</h1>
        <p>选模板输入主题，AI 生成大纲与成稿；支持在线编辑、版本回滚与多格式导出。</p>
      </div>
      <div class="actions">
        <span class="tag" id="ppt-quota">${icon('zap')} 配额加载中…</span>
        <button class="btn ghost" id="ppt-blank">${icon('file')} 空白创建</button>
        <button class="btn primary" id="ppt-gen">${icon('wand')} AI 生成 PPT</button>
      </div>
    </div>
    <div class="tabs" id="ppt-tabs">
      <button class="active" data-tab="templates">模板库</button>
      <button data-tab="mine">我的 PPT</button>
    </div>
    <div id="ppt-body"></div>
  </div>`;

  $('#ppt-gen').onclick = () => openGenerate();
  $('#ppt-blank').onclick = openBlank;
  $$('#ppt-tabs button').forEach((btn) => {
    btn.onclick = () => {
      $$('#ppt-tabs button').forEach((b) => b.classList.toggle('active', b === btn));
      home.tab = btn.dataset.tab;
      renderBody();
    };
  });
  try {
    const q = await api('/ppt/quota');
    $('#ppt-quota').innerHTML = `${icon('zap')} 本月 ${q.used}/${q.limit} 次`;
  } catch (err) { /* ignore */ }
  try {
    const data = await api('/ppt/templates?page_size=60');
    home.templates = data.list || [];
  } catch (err) {
    toast(err.message, 'error');
  }
  await renderBody();
}

async function renderBody() {
  const box = $('#ppt-body');
  if (!box) return;
  if (home.tab === 'mine') {
    await renderMine(box);
    return;
  }
  box.innerHTML = `
    <div class="row" style="margin-bottom:14px;align-items:center;flex-wrap:wrap">
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        ${CATEGORIES.map((c) => `<button class="chip ${home.category === c ? 'active' : ''}" data-cat="${c}">${c}</button>`).join('')}
      </div>
      <div style="display:flex;gap:8px;flex:none;margin-left:auto">
        <input class="input" id="ppt-q" placeholder="搜索模板…" style="width:170px;height:34px">
        <select class="select" id="ppt-style" style="width:110px;height:34px">
          ${STYLES.map((s) => `<option ${home.style === s ? 'selected' : ''}>${s}</option>`).join('')}
        </select>
      </div>
    </div>
    <div class="tpl-grid" id="ppt-grid">${skeletonCards(8, 120)}</div>`;
  $('#ppt-q').oninput = (() => {
    let timer = null;
    return (e) => {
      clearTimeout(timer);
      timer = setTimeout(() => { home.q = e.target.value.trim(); loadTemplates(); }, 260);
    };
  })();
  $('#ppt-style').onchange = (e) => { home.style = e.target.value; loadTemplates(); };
  $$('[data-cat]', box).forEach((btn) => {
    btn.onclick = () => { home.category = btn.dataset.cat; loadTemplates(); };
  });
  loadTemplates();
}

function filteredTemplates() {
  return home.templates.filter((t) => (
    (home.category === '全部' || t.category === home.category)
    && (home.style === '全部' || t.style === home.style)
    && (!home.q || t.name.includes(home.q))
  ));
}

function loadTemplates() {
  const grid = $('#ppt-grid');
  if (!grid) return;
  const items = filteredTemplates();
  if (!items.length) {
    grid.innerHTML = emptyState({ iconName: 'ppt', title: '没有匹配的模板', desc: '换个分类或风格试试，也可以直接 AI 生成。' });
    return;
  }
  grid.innerHTML = items.map((t) => `
    <div class="tpl-card" data-id="${t.id}">
      <div class="tpl-thumb">
        <img src="${esc(t.thumb)}" alt="${esc(t.name)} 封面" loading="lazy">
        <span class="tpl-badge">${esc(t.category)}</span>
      </div>
      <div class="tpl-strip">
        ${(t.strip || []).map((s) => `<img src="${esc(s.url)}" alt="${esc(s.label)}" title="${esc(s.label)}" loading="lazy">`).join('')}
      </div>
      <div class="tpl-info">
        <b>${esc(t.name)}</b>
        <p class="tpl-desc">${esc(t.layout_desc || '')}</p>
        <div><span>${esc(t.style)} · ${esc(t.ratio)} · ${t.page_count} 页</span><span>${fmtNum(t.use_count)} 次使用</span></div>
      </div>
    </div>`).join('');
  grid.querySelectorAll('.tpl-card').forEach((card) => {
    card.onclick = () => {
      const tpl = home.templates.find((t) => t.id === card.dataset.id);
      openTemplate(tpl);
    };
  });
}

function openTemplate(tpl) {
  let detail = tpl;
  const body = `<div id="tpl-preview">${skeletonCards(1, 180)}</div>`;
  openModal({
    title: tpl.name,
    subtitle: `${tpl.category} · ${tpl.style} · ${tpl.ratio}`,
    className: 'wide',
    body,
    footer: `<button class="btn ghost" data-close>关闭</button>
             <button class="btn primary" id="tpl-use">用此模板生成</button>`,
    onMount(el, close) {
      const box = el.querySelector('#tpl-preview');
      api(`/ppt/templates/${tpl.id}`).then((data) => {
        detail = data;
        const cover = `/api/v1/ppt/templates/${encodeURIComponent(detail.id)}/preview?page=cover&width=1100`;
        box.innerHTML = `
          <div class="tpl-detail-hero">
            <img src="${esc(cover)}" alt="${esc(detail.name)} 封面">
            <div class="tpl-detail-meta">
              <div style="display:flex;gap:6px;flex-wrap:wrap">
                <span class="tag brand">${esc(detail.style)}</span>
                <span class="tag">${esc(detail.category)}</span>
                <span class="tag">${esc(detail.ratio)}</span>
                <span class="tag">${detail.page_count} 页</span>
              </div>
              <p>${esc(detail.layout_desc || '')}</p>
              <span class="small muted">${fmtNum(detail.use_count)} 次使用 · 编辑器预览与导出共用同一版式引擎</span>
            </div>
          </div>
          <div class="section-title" style="margin-top:18px"><div><h3 style="font-size:13px">版式预览</h3>
            <p>覆盖 ${(detail.previews || []).length} 种页面类型，均按此模板真实排版</p></div></div>
          <div class="tpl-preview-grid">
            ${(detail.previews || []).map((p) => `
              <figure>
                <img src="${esc(p.url)}" alt="${esc(p.label)}" loading="lazy">
                <figcaption>${esc(p.label)}</figcaption>
              </figure>`).join('')}
          </div>`;
      }).catch((err) => {
        box.innerHTML = `<div class="ws-empty">${esc(err.message)}</div>`;
      });
      el.querySelector('#tpl-use').onclick = () => {
        close();
        openGenerate(detail.id);
      };
    },
  });
}

// ------------------------------------------------------------------ 我的 PPT
async function renderMine(box) {
  box.innerHTML = '<div class="tpl-grid">' + skeletonCards(6, 120) + '</div>';
  try {
    const data = await api('/ppt/documents?page_size=50');
    home.myDocs = data.list || [];
    if (data.quota) {
      const q = $('#ppt-quota');
      if (q) q.innerHTML = `${icon('zap')} 本月 ${data.quota.used}/${data.quota.limit} 次`;
    }
  } catch (err) {
    box.innerHTML = `<div class="empty"><p>${esc(err.message)}</p></div>`;
    return;
  }
  if (!home.myDocs.length) {
    box.innerHTML = emptyState({
      iconName: 'ppt', title: '还没有 PPT', desc: '从模板生成，或创建一个空白演示文稿。',
      action: '<button class="btn primary" id="mine-gen">AI 生成 PPT</button>',
    });
    $('#mine-gen').onclick = () => openGenerate();
    return;
  }
  box.innerHTML = `<div class="tpl-grid">${home.myDocs.map((d) => {
    const tpl = home.templates.find((t) => t.id === d.template_id);
    const thumb = tpl?.thumb || '';
    return `<div class="tpl-card" data-id="${d.id}">
      <div class="tpl-thumb">
        ${thumb ? `<img src="${esc(thumb)}" alt="${esc(d.title)}" loading="lazy">` : ''}
        <span class="tpl-badge">v${d.version} · ${d.page_count} 页</span>
      </div>
      <div class="tpl-info">
        <b>${esc(d.title)}</b>
        <div><span>${esc(d.ratio)} · ${fmtTime(d.updated_at)}</span>
          <span><button class="btn sm ghost" data-dup style="height:22px;padding:0 7px">复制</button>
          <button class="btn sm ghost-danger" data-del style="height:22px;padding:0 7px;margin-left:4px">删除</button></span></div>
      </div>
    </div>`;
  }).join('')}</div>`;
  box.querySelectorAll('.tpl-card').forEach((card) => {
    const doc = home.myDocs.find((d) => d.id === card.dataset.id);
    card.onclick = (e) => {
      if (e.target.closest('[data-dup]') || e.target.closest('[data-del]')) return;
      location.hash = `#/ppt/${doc.id}`;
    };
    card.querySelector('[data-dup]').onclick = async (e) => {
      e.stopPropagation();
      try {
        await api(`/ppt/documents/${doc.id}/duplicate`, { method: 'POST' });
        toast('已创建副本', 'success');
        renderBody();
      } catch (err) { toast(err.message, 'error'); }
    };
    card.querySelector('[data-del]').onclick = async (e) => {
      e.stopPropagation();
      const ok = await confirmDialog({ title: '删除 PPT', message: `确定删除「${doc.title}」吗？`, confirmText: '删除', danger: true });
      if (!ok) return;
      await api(`/ppt/documents/${doc.id}`, { method: 'DELETE' });
      toast('已删除', 'success');
      renderBody();
    };
  });
}

// ------------------------------------------------------------------ 空白创建
function openBlank() {
  openModal({
    title: '空白创建',
    subtitle: '创建后可在编辑器中自由添加页面',
    className: 'slim',
    body: `<div class="field"><label>演示文稿标题</label><input class="input" id="blank-title" value="未命名演示文稿" maxlength="60"></div>
      <div class="row">
        <div class="field"><label>页面比例</label><select class="select" id="blank-ratio"><option>16:9</option><option>4:3</option></select></div>
        <div class="field"><label>基础模板</label><select class="select" id="blank-tpl">
          ${home.templates.map((t) => `<option value="${t.id}" ${t.id === 'tpl_min_gray' ? 'selected' : ''}>${esc(t.name)}</option>`).join('')}
        </select></div>
      </div>`,
    footer: `<button class="btn ghost" data-close>取消</button><button class="btn primary" id="blank-create">创建并编辑</button>`,
    onMount(el, close) {
      el.querySelector('#blank-create').onclick = async () => {
        const title = el.querySelector('#blank-title').value.trim() || '未命名演示文稿';
        try {
          const doc = await api('/ppt/documents', {
            method: 'POST',
            body: {
              title,
              ratio: el.querySelector('#blank-ratio').value,
              template_id: el.querySelector('#blank-tpl').value,
            },
          });
          close();
          location.hash = `#/ppt/${doc.id}`;
        } catch (err) { toast(err.message, 'error'); }
      };
    },
  });
}

// ------------------------------------------------------------------ AI 生成向导
function openGenerate(presetTemplate) {
  const state = {
    step: 1, templateId: presetTemplate || home.templates[0]?.id || 'tpl_biz_blue',
    topic: '', pages: 10, source: '', outline: null, jobId: null, timer: null, resultDoc: null,
  };
  const body = `
    <div class="gen-steps" id="gen-steps">
      ${['选择模板', '输入主题', '编辑大纲', '生成成稿'].map((label, i) => `
        <div class="gen-step ${i === 0 ? 'active' : ''}" data-step="${i + 1}"><span class="n">${i + 1}</span><span>${label}</span></div>
        ${i < 3 ? '<span class="gen-line"></span>' : ''}`).join('')}
    </div>
    <div id="gen-panel"></div>`;
  openModal({
    title: 'AI 生成 PPT',
    subtitle: '模板 → 主题 → 大纲 → 成稿，全程可调整',
    className: 'xwide',
    body,
    footer: `<button class="btn ghost" id="gen-prev">上一步</button>
             <button class="btn primary" id="gen-next">下一步</button>`,
    onClose() { if (state.timer) clearInterval(state.timer); },
    onMount(el, close) {
      const panel = el.querySelector('#gen-panel');
      const prev = el.querySelector('#gen-prev');
      const next = el.querySelector('#gen-next');
      const setStep = (n) => {
        state.step = n;
        el.querySelectorAll('.gen-step').forEach((s) => {
          const sn = Number(s.dataset.step);
          s.classList.toggle('active', sn === n);
          s.classList.toggle('done', sn < n);
        });
        prev.style.visibility = n === 1 ? 'hidden' : '';
        next.textContent = n === 3 ? '开始生成' : n === 4 ? '完成' : '下一步';
        renderPanel();
      };

      const renderPanel = () => {
        if (state.step === 1) {
          panel.innerHTML = `<div class="plugin-grid" style="grid-template-columns:repeat(4,1fr);max-height:440px;overflow:auto">
            ${home.templates.map((t) => `<button class="tpl-card" data-tpl="${t.id}" style="text-align:left;${state.templateId === t.id ? 'outline:2px solid var(--brand)' : ''}">
              <div class="tpl-thumb">
                <img src="${esc(t.thumb)}" alt="${esc(t.name)}" loading="lazy">
                <span class="tpl-badge">${esc(t.ratio)}</span>
              </div>
              <div class="tpl-info"><b>${esc(t.name)}</b><div><span>${esc(t.category)}</span><span>${esc(t.style)}</span></div></div>
            </button>`).join('')}
          </div>`;
          panel.querySelectorAll('[data-tpl]').forEach((btn) => {
            btn.onclick = () => { state.templateId = btn.dataset.tpl; renderPanel(); };
          });
          return;
        }
        if (state.step === 2) {
          panel.innerHTML = `
            <div class="field"><label>PPT 主题</label>
              <input class="input" id="gen-topic" placeholder="例如：AI 如何提升团队效率" value="${esc(state.topic)}"></div>
            <div class="row">
              <div class="field"><label>页数（5~30）</label>
                <input class="input" id="gen-pages" type="number" min="5" max="30" value="${state.pages}"></div>
              <div class="field"><label>模板</label>
                <select class="select" id="gen-tpl">${home.templates.map((t) => `<option value="${t.id}" ${t.id === state.templateId ? 'selected' : ''}>${esc(t.name)}</option>`).join('')}</select></div>
            </div>
            <div class="field"><label>补充参考资料（可选）</label>
              <textarea class="textarea" id="gen-source" rows="4" placeholder="粘贴背景资料、数据或已有文案，AI 会尽量融入大纲…">${esc(state.source)}</textarea></div>
            <p class="small muted">生成大纲后可逐页修改标题与要点，再生成成稿；每月免费 20 次。</p>`;
          return;
        }
        if (state.step === 3) {
          panel.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
              <span class="small muted">共 ${state.outline.length} 页 · 可调整顺序与文案</span>
              <button class="btn sm ghost" id="gen-add">${icon('plus')} 添加一页</button>
            </div>
            <div style="max-height:400px;overflow:auto" id="gen-outline">
              ${state.outline.map((s, i) => {
                const special = ['chart', 'metrics', 'quote', 'compare'].includes(s.layout);
                return `<div class="outline-item" data-i="${i}">
                <span class="idx">${i + 1}</span>
                <div style="flex:1;min-width:0;display:flex;flex-direction:column;gap:5px">
                  <div style="display:flex;align-items:center;gap:6px">
                    <input class="input" data-title placeholder="页面标题" value="${esc(s.title || '')}">
                    <span class="tag" style="flex:none">${esc(PAGE_TYPE_LABELS[s.layout] || '要点列表')}</span>
                  </div>
                  ${special
                    ? '<span class="small muted">结构化页面：数据由生成结果提供，可在编辑器中调整</span>'
                    : `<textarea class="textarea" data-bullets rows="2" placeholder="每行一条要点">${esc((s.bullets || []).join('\n'))}</textarea>`}
                </div>
                <div style="display:flex;flex-direction:column;gap:2px">
                  <button class="icon-btn" data-up title="上移">${icon('chevronDown', 'up')}</button>
                  <button class="icon-btn" data-down title="下移">${icon('chevronDown')}</button>
                  <button class="icon-btn" data-remove title="删除">${icon('trash')}</button>
                </div>
              </div>`; }).join('')}
            </div>`;
          panel.querySelectorAll('.outline-item').forEach((row) => {
            const i = Number(row.dataset.i);
            row.querySelector('[data-up]').onclick = () => {
              if (i === 0) return;
              collectOutline();
              [state.outline[i - 1], state.outline[i]] = [state.outline[i], state.outline[i - 1]];
              renderPanel();
            };
            row.querySelector('[data-down]').onclick = () => {
              if (i >= state.outline.length - 1) return;
              collectOutline();
              [state.outline[i + 1], state.outline[i]] = [state.outline[i], state.outline[i + 1]];
              renderPanel();
            };
            row.querySelector('[data-remove]').onclick = () => {
              collectOutline();
              if (state.outline.length <= 5) { toast('至少保留 5 页', 'error'); return; }
              state.outline.splice(i, 1);
              renderPanel();
            };
          });
          panel.querySelector('#gen-add').onclick = () => {
            collectOutline();
            if (state.outline.length >= 30) { toast('最多 30 页', 'error'); return; }
            state.outline.push({ title: '新页面', bullets: ['要点一', '要点二'], layout: 'content', notes: '' });
            renderPanel();
          };
          return;
        }
        panel.innerHTML = `
          <div style="padding:8px 0">
            <div class="section-title"><div><h3 style="font-size:14px">${esc(state.topic || '正在生成')}</h3>
              <p id="gen-msg">${state.resultDoc ? '生成完成，可进入编辑器继续修改' : '正在排队生成…'}</p></div>
              <span class="tag brand">${state.pages} 页</span></div>
            <div class="progress"><i id="gen-bar" style="width:0%"></i></div>
            <div class="small muted" style="margin-top:10px" id="gen-progress-text">准备中</div>
            <div id="gen-done" style="margin-top:18px"></div>
          </div>`;
        if (state.resultDoc) showDone();
      };

      const collectOutline = () => {
        const box = el.querySelector('#gen-outline');
        if (!box) return;
        box.querySelectorAll('.outline-item').forEach((row) => {
          const i = Number(row.dataset.i);
          state.outline[i].title = row.querySelector('[data-title]').value.trim() || `第 ${i + 1} 页`;
          const bulletsEl = row.querySelector('[data-bullets]');
          if (bulletsEl) {
            state.outline[i].bullets = bulletsEl.value.split('\n').map((s) => s.trim()).filter(Boolean);
          }
        });
      };

      const showDone = () => {
        const done = el.querySelector('#gen-done');
        if (!done || !state.resultDoc) return;
        done.innerHTML = `<div style="display:flex;align-items:center;gap:12px;padding:14px;border:1px solid #c9e8d3;background:#f5fcf7;border-radius:12px">
          <span class="task-icon" style="background:var(--success-50);color:var(--success)">${icon('check')}</span>
          <div style="flex:1"><b style="font-size:13px">${esc(state.resultDoc.title)}</b>
            <div class="small muted">${state.resultDoc.page_count} 页 · ${esc(state.resultDoc.ratio)}</div></div>
          <button class="btn primary" id="gen-open">进入编辑器</button></div>`;
        done.querySelector('#gen-open').onclick = () => {
          close();
          location.hash = `#/ppt/${state.resultDoc.id}`;
        };
      };

      const startGenerate = async () => {
        setStep(4);
        track('generate_ppt', { topic: state.topic, pages: state.pages });
        try {
          const res = await api('/ppt/generate', {
            method: 'POST',
            body: {
              topic: state.topic, pages: state.outline.length, template_id: state.templateId,
              outline: state.outline,
            },
          });
          state.jobId = res.job_id;
          state.timer = setInterval(async () => {
            try {
              const job = await api(`/ppt/jobs/${state.jobId}`);
              const bar = el.querySelector('#gen-bar');
              const txt = el.querySelector('#gen-progress-text');
              const msg = el.querySelector('#gen-msg');
              if (bar) bar.style.width = `${job.progress}%`;
              if (txt) txt.textContent = `${job.progress}% · ${job.message}`;
              if (msg && job.message) msg.textContent = job.message;
              if (job.status === 'success') {
                clearInterval(state.timer);
                state.timer = null;
                state.resultDoc = await api(`/ppt/documents/${job.doc_id}`);
                showDone();
              } else if (job.status === 'failed') {
                clearInterval(state.timer);
                state.timer = null;
                if (msg) msg.textContent = job.message || '生成失败';
                toast('生成失败，请重试', 'error');
              }
            } catch (err) {
              clearInterval(state.timer);
              state.timer = null;
              toast(err.message, 'error');
            }
          }, 700);
        } catch (err) {
          toast(err.message, 'error');
          setStep(3);
        }
      };

      prev.onclick = () => {
        if (state.step === 1 || state.step === 4) return;
        if (state.step === 3) collectOutline();
        setStep(state.step - 1);
      };
      next.onclick = async () => {
        if (state.step === 1) { setStep(2); return; }
        if (state.step === 2) {
          const topic = el.querySelector('#gen-topic').value.trim();
          const pages = Number(el.querySelector('#gen-pages').value) || 10;
          if (!topic) { toast('请输入 PPT 主题', 'error'); return; }
          if (pages < 5 || pages > 30) { toast('页数需在 5~30 页之间', 'error'); return; }
          state.topic = topic;
          state.pages = pages;
          state.source = el.querySelector('#gen-source').value;
          state.templateId = el.querySelector('#gen-tpl').value;
          next.disabled = true;
          next.textContent = '生成大纲中…';
          try {
            const res = await api('/ppt/outline', {
              method: 'POST',
              body: { topic, pages, template_id: state.templateId, source_text: state.source },
            });
            state.outline = res.slides;
            next.disabled = false;
            setStep(3);
          } catch (err) {
            next.disabled = false;
            next.textContent = '下一步';
            toast(err.message, 'error');
          }
          return;
        }
        if (state.step === 3) {
          collectOutline();
          startGenerate();
          return;
        }
        if (state.step === 4 && state.resultDoc) {
          close();
          location.hash = `#/ppt/${state.resultDoc.id}`;
        }
      };
      setStep(1);
    },
  });
}

// ------------------------------------------------------------------ 编辑器
async function renderEditor(page, id) {
  page.innerHTML = '<div class="page-inner"><div style="height:300px" class="skeleton"></div></div>';
  let doc;
  try {
    doc = await api(`/ppt/documents/${id}`);
  } catch (err) {
    page.innerHTML = `<div class="page-inner">${emptyState({
      iconName: 'ppt', title: 'PPT 不存在', desc: err.message,
      action: '<button class="btn primary" onclick="location.hash=\'#/ppt\'">返回 PPT 首页</button>',
    })}</div>`;
    return;
  }
  let templates = [];
  try {
    templates = (await api('/ppt/templates?page_size=60')).list || [];
  } catch (err) { /* ignore */ }

  editor = {
    doc, templates, selected: 0, dirty: false, saving: false, timer: null,
    destroyed: false, tpl: doc.template, thumbUrls: {}, canvasUrl: '',
    previewTimer: null, previewSeq: 0,
  };
  const ed = editor;

  page.innerHTML = `<div class="ppt-layout">
    <aside class="ppt-slides" id="ppt-thumbs"></aside>
    <div class="ppt-editor">
      <div class="ppt-actions-bar">
        <div style="display:flex;align-items:center;gap:8px;min-width:0">
          <button class="icon-btn" id="ed-back" title="返回">${icon('arrowLeft')}</button>
          <input class="input" id="ed-title" value="${esc(ed.doc.title)}" style="max-width:300px;height:32px;font-weight:600">
          <span class="small muted" id="ed-save-state">已保存</span>
        </div>
        <div style="display:flex;gap:6px;align-items:center">
          <button class="btn sm ghost ppt-props-toggle" id="ed-props-btn">${icon('edit')} 属性</button>
          <button class="btn sm ghost" id="ed-tpl">${icon('grid')} 换模板</button>
          <button class="btn sm ghost" id="ed-version">${icon('history')} 版本</button>
          <button class="btn sm ghost" id="ed-export">${icon('download')} 导出</button>
          <button class="btn sm primary" id="ed-save">保存</button>
        </div>
      </div>
      <div class="ppt-canvas-wrap"><div class="ppt-canvas" id="ed-canvas"></div></div>
      <div class="small muted" style="padding:6px 16px 10px;text-align:right" id="ed-hint">每 30 秒自动保存 · ${ed.doc.ratio}</div>
    </div>
    <aside class="ppt-props" id="ed-props"></aside>
  </div>`;

  function current() { return ed.doc.slides[ed.selected] || ed.doc.slides[0]; }

  // 预览与导出共用后端版式引擎：编辑器画布、缩略图都是真实渲染图
  async function fetchSlidePreview(index, width) {
    const headers = { 'Content-Type': 'application/json' };
    if (authState.token) headers.Authorization = `Bearer ${authState.token}`;
    const res = await fetch('/api/v1/ppt/preview', {
      method: 'POST',
      headers,
      body: JSON.stringify({
        slides: ed.doc.slides,
        template_id: ed.doc.template_id,
        ratio: ed.doc.ratio,
        index,
        width,
        accent: ed.doc.cover_color || null,
      }),
    });
    if (!res.ok) {
      let message = `预览渲染失败（${res.status}）`;
      try { const data = await res.json(); message = data.message || message; } catch (err) { /* ignore */ }
      throw new Error(message);
    }
    return URL.createObjectURL(await res.blob());
  }

  function invalidateThumb(i) {
    if (ed.thumbUrls[i]) {
      URL.revokeObjectURL(ed.thumbUrls[i]);
      delete ed.thumbUrls[i];
    }
  }

  function invalidateAllThumbs() {
    Object.keys(ed.thumbUrls).forEach((k) => {
      URL.revokeObjectURL(ed.thumbUrls[k]);
      delete ed.thumbUrls[k];
    });
  }

  async function ensureThumb(i) {
    try {
      const url = await fetchSlidePreview(i, 320);
      if (ed.destroyed) { URL.revokeObjectURL(url); return; }
      invalidateThumb(i);
      ed.thumbUrls[i] = url;
      const holder = document.querySelector(`[data-thumb-box="${i}"]`);
      if (holder) holder.innerHTML = `<img class="thumb-img" src="${url}" alt="">`;
    } catch (err) {
      const holder = document.querySelector(`[data-thumb-box="${i}"]`);
      if (holder) holder.innerHTML = '<span class="thumb-err">预览失败</span>';
    }
  }

  function renderThumbs() {
    const box = $('#ppt-thumbs');
    if (!box) return;
    box.innerHTML = ed.doc.slides.map((s, i) => `
      <div class="ppt-slide-thumb ${i === ed.selected ? 'active' : ''}" data-i="${i}">
        <span class="num">${i + 1}</span>
        <div class="thumb-box" data-thumb-box="${i}">
          ${ed.thumbUrls[i]
            ? `<img class="thumb-img" src="${ed.thumbUrls[i]}" alt="">`
            : '<span class="thumb-sk"></span>'}
        </div>
      </div>`).join('');
    box.querySelectorAll('.ppt-slide-thumb').forEach((el) => {
      el.onclick = () => { ed.selected = Number(el.dataset.i); renderAll(); };
    });
    ed.doc.slides.forEach((s, i) => { if (!ed.thumbUrls[i]) ensureThumb(i); });
  }

  function renderCanvas(delay = 160) {
    const box = $('#ed-canvas');
    if (!box) return;
    box.className = `ppt-canvas ${ed.doc.ratio === '4:3' ? 'ratio-4-3' : ''}`;
    if (!box.querySelector('#ed-preview-img')) {
      box.innerHTML = `<img class="ppt-preview-img" id="ed-preview-img" alt="">
        <span class="canvas-loading" id="ed-preview-loading">排版渲染中…</span>`;
      delay = 0;
    }
    clearTimeout(ed.previewTimer);
    ed.previewTimer = setTimeout(async () => {
      const seq = ++ed.previewSeq;
      const index = ed.selected;
      const loading = $('#ed-preview-loading');
      if (loading) { loading.style.display = ''; loading.textContent = '排版渲染中…'; }
      try {
        const url = await fetchSlidePreview(index, 1500);
        if (ed.destroyed || seq !== ed.previewSeq || index !== ed.selected) {
          URL.revokeObjectURL(url);
          return;
        }
        if (ed.canvasUrl) URL.revokeObjectURL(ed.canvasUrl);
        ed.canvasUrl = url;
        const img = $('#ed-preview-img');
        if (img) img.src = url;
        if (loading) loading.style.display = 'none';
      } catch (err) {
        if (loading) loading.textContent = err.message;
      }
    }, delay);
  }

  function renderProps() {
    const box = $('#ed-props');
    if (!box) return;
    const s = current();
    const special = ['chart', 'metrics', 'quote', 'compare'].includes(s.layout);
    const typeLabel = PAGE_TYPE_LABELS[s.layout] || '要点列表';
    box.innerHTML = `
      <h4>第 ${ed.selected + 1} / ${ed.doc.slides.length} 页
        <span class="tag" style="margin-left:6px">${esc(typeLabel)}</span></h4>
      <div class="field"><label>页面标题</label><input class="input" id="pr-title" value="${esc(s.title || '')}"></div>
      ${s.layout === 'cover' || s.layout === 'end' ? `<div class="field"><label>副标题</label><input class="input" id="pr-subtitle" value="${esc(s.subtitle || '')}"></div>` : ''}
      ${special ? `<div class="field"><label>结构化数据（JSON）</label>
        <textarea class="textarea" id="pr-data" rows="8" spellcheck="false">${esc(JSON.stringify(s[s.layout], null, 2))}</textarea>
        <p class="small muted" style="margin-top:4px">修改后按此版式重新排版，与导出文件一致。</p></div>` : (s.layout === 'cover' || s.layout === 'end' ? '' : `
      <div class="field"><label>要点（每行一条）</label><textarea class="textarea" id="pr-bullets" rows="7">${esc((s.bullets || []).join('\n'))}</textarea></div>`)}
      <div class="field"><label>配图地址（可选）</label><input class="input" id="pr-image" placeholder="https://…" value="${esc(s.image || '')}"></div>
      <div class="field"><label>备注</label><textarea class="textarea" id="pr-notes" rows="2" placeholder="演讲备注">${esc(s.notes || '')}</textarea></div>
      <div class="divider"></div>
      <div style="display:flex;flex-direction:column;gap:7px">
        <button class="btn sm ghost" id="pr-regen">${icon('refresh')} 单页重新生成</button>
        <div class="row" style="gap:6px">
          <button class="btn sm ghost" id="pr-up">${icon('chevronDown', 'up')} 上移</button>
          <button class="btn sm ghost" id="pr-down">${icon('chevronDown')} 下移</button>
        </div>
        <button class="btn sm ghost" id="pr-add">${icon('plus')} 在后面添加一页</button>
        <button class="btn sm ghost-danger" id="pr-del">${icon('trash')} 删除本页</button>
      </div>
      <div class="divider"></div>
      <div class="field"><label>主题色</label>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          ${['#1A5CC8', '#0F766E', '#6D28D9', '#9F1239', '#334155', '#F97316'].map((c) => `
            <button data-color="${c}" style="width:24px;height:24px;border-radius:6px;background:${c};border:2px solid ${ed.doc.cover_color === c ? '#1F2A44' : 'transparent'}"></button>`).join('')}
        </div>
      </div>`;

    const touchCurrent = () => {
      markDirty();
      invalidateThumb(ed.selected);
      renderThumbs();
      renderCanvas();
    };
    const titleEl = box.querySelector('#pr-title');
    titleEl.oninput = () => { s.title = titleEl.value; touchCurrent(); };
    const sub = box.querySelector('#pr-subtitle');
    if (sub) sub.oninput = () => { s.subtitle = sub.value; touchCurrent(); };
    const bullets = box.querySelector('#pr-bullets');
    if (bullets) bullets.oninput = () => {
      s.bullets = bullets.value.split('\n').map((x) => x.trim()).filter(Boolean);
      touchCurrent();
    };
    const dataEl = box.querySelector('#pr-data');
    if (dataEl) dataEl.onchange = () => {
      try {
        s[s.layout] = JSON.parse(dataEl.value);
        touchCurrent();
        toast('数据已更新，版式已重新排版', 'success', 1400);
      } catch (err) {
        toast('数据结构不是合法 JSON，请检查后再试', 'error');
      }
    };
    const img = box.querySelector('#pr-image');
    img.onchange = () => { s.image = img.value.trim(); touchCurrent(); };
    const notes = box.querySelector('#pr-notes');
    notes.oninput = () => { s.notes = notes.value; markDirty(); };
    box.querySelectorAll('[data-color]').forEach((btn) => {
      btn.onclick = () => {
        ed.doc.cover_color = btn.dataset.color;
        markDirty();
        invalidateAllThumbs();
        renderProps();
        renderAll();
      };
    });
    box.querySelector('#pr-regen').onclick = async () => {
      const old = current();
      try {
        toast('正在重新生成本页…', 'info', 1400);
        const res = await api('/ppt/outline', {
          method: 'POST',
          body: { topic: `${ed.doc.title}｜${old.title}`, pages: 5, template_id: ed.doc.template_id },
        });
        const reuse = res.slides.find((x) => x.layout !== 'cover') || res.slides[1];
        if (reuse) {
          old.bullets = reuse.bullets;
          if (old.layout !== 'cover') old.title = reuse.title;
        }
        markDirty();
        invalidateAllThumbs();
        renderAll();
        toast('本页已重新生成', 'success');
      } catch (err) { toast(err.message, 'error'); }
    };
    box.querySelector('#pr-up').onclick = () => moveSlide(-1);
    box.querySelector('#pr-down').onclick = () => moveSlide(1);
    box.querySelector('#pr-add').onclick = () => {
      ed.doc.slides.splice(ed.selected + 1, 0, { title: '新页面', bullets: ['要点一', '要点二'], notes: '', layout: 'bullets' });
      ed.selected += 1;
      markDirty();
      invalidateAllThumbs();
      renderAll();
    };
    box.querySelector('#pr-del').onclick = async () => {
      if (ed.doc.slides.length <= 1) { toast('至少保留一页', 'error'); return; }
      const ok = await confirmDialog({ title: '删除本页', message: '删除后可通过版本历史找回。', danger: true, confirmText: '删除' });
      if (!ok) return;
      ed.doc.slides.splice(ed.selected, 1);
      ed.selected = Math.max(0, ed.selected - 1);
      markDirty();
      invalidateAllThumbs();
      renderAll();
    };
  }

  function moveSlide(delta) {
    const to = ed.selected + delta;
    if (to < 0 || to >= ed.doc.slides.length) return;
    const [s] = ed.doc.slides.splice(ed.selected, 1);
    ed.doc.slides.splice(to, 0, s);
    ed.selected = to;
    markDirty();
    invalidateAllThumbs();
    renderAll();
  }

  function renderAll() {
    renderThumbs();
    renderCanvas();
    renderProps();
  }

  function markDirty() {
    ed.dirty = true;
    const el = $('#ed-save-state');
    if (el) el.textContent = '未保存';
  }

  async function saveNow(silent = false) {
    if (ed.saving) return;
    ed.saving = true;
    const el = $('#ed-save-state');
    if (el) el.textContent = '保存中…';
    try {
      const updated = await api(`/ppt/documents/${ed.doc.id}`, {
        method: 'PATCH',
        body: { title: ed.doc.title, slides: ed.doc.slides, cover_color: ed.doc.cover_color },
      });
      ed.doc = updated;
      ed.dirty = false;
      if (el) el.textContent = '已保存';
      if (!silent) toast('已保存', 'success', 1400);
    } catch (err) {
      if (el) el.textContent = '保存失败';
      if (!silent) toast(err.message, 'error');
    } finally {
      ed.saving = false;
    }
  }
  ed.saveNow = saveNow;

  $('#ed-back').onclick = async () => {
    if (ed.dirty) await saveNow(true);
    location.hash = '#/ppt';
  };
  $('#ed-save').onclick = () => saveNow();
  const titleInput = $('#ed-title');
  titleInput.oninput = () => {
    ed.doc.title = titleInput.value;
    markDirty();
  };
  titleInput.onchange = () => {
    if (window.MAUTO?.setPageTitle) window.MAUTO.setPageTitle(ed.doc.title, 'PPT 编辑');
  };
  $('#ed-export').onclick = (e) => {
    showMenu(e.currentTarget, [
      { label: '导出 PPTX', icon: 'ppt', onClick: () => doExport('pptx') },
      { label: '导出 PDF', icon: 'file', onClick: () => doExport('pdf') },
      { label: '导出 PNG 长图', icon: 'image', onClick: () => doExport('png') },
    ]);
  };
  $('#ed-version').onclick = openVersions;
  $('#ed-tpl').onclick = openChangeTemplate;
  $('#ed-props-btn').onclick = () => {
    page.querySelector('.ppt-layout')?.classList.toggle('props-open');
  };
  autosave: ed.timer = setInterval(() => { if (ed.dirty) saveNow(true); }, 30000);
  window.MAUTO?.setPageTitle(ed.doc.title, 'PPT 编辑');
  renderAll();

  async function doExport(fmt) {
    try {
      toast('正在导出，请稍候…', 'info', 1600);
      await download(`/ppt/documents/${ed.doc.id}/export?format=${fmt}`, `${ed.doc.title}.${fmt}`);
      track('export_ppt', { doc_id: ed.doc.id, format: fmt });
      toast('导出完成', 'success');
    } catch (err) { toast(err.message, 'error'); }
  }

  function openVersions() {
    openModal({
      title: '版本历史',
      subtitle: '保留最近 10 个版本，可回滚到任意版本',
      className: 'slim',
      body: '<div id="ver-box">' + skeletonCards(2).replace(/plugin-grid/g, '') + '</div>',
      onMount(el, close) {
        const box = el.querySelector('#ver-box');
        const load = () => api(`/ppt/documents/${ed.doc.id}/versions`).then((rows) => {
          if (!rows.length) {
            box.innerHTML = '<div class="ws-empty">暂无历史版本，编辑保存后会自动生成。</div>';
            return;
          }
          box.innerHTML = rows.map((v) => `
            <div class="mine-row">
              <span class="task-icon" style="width:30px;height:30px;border-radius:8px">${icon('history')}</span>
              <div style="flex:1"><b style="font-size:12.5px">版本 v${v.version}</b>
                <span class="small muted">${fmtDateTime(v.created_at)}</span></div>
              <button class="btn sm ghost" data-v="${v.id}">回滚</button>
            </div>`).join('');
          box.querySelectorAll('[data-v]').forEach((btn) => {
            btn.onclick = async () => {
              const ok = await confirmDialog({ title: '回滚版本', message: '当前内容会被新版本覆盖，是否继续？', confirmText: '回滚' });
              if (!ok) return;
              try {
                ed.doc = await api(`/ppt/documents/${ed.doc.id}/rollback/${btn.dataset.v}`, { method: 'POST' });
                ed.selected = 0;
                ed.dirty = false;
                invalidateAllThumbs();
                renderAll();
                close();
                toast('已回滚', 'success');
              } catch (err) { toast(err.message, 'error'); }
            };
          });
        }).catch((err) => { box.innerHTML = `<div class="ws-empty">${esc(err.message)}</div>`; });
        load();
      },
    });
  }

  function openChangeTemplate() {
    openModal({
      title: '更换模板',
      subtitle: '版式与配色会一起切换，页面内容保持不变',
      className: 'wide',
      body: `<div class="plugin-grid" style="grid-template-columns:repeat(3,1fr)">${ed.templates.map((t) => `
        <button class="tpl-card" data-t="${t.id}" style="text-align:left;${t.id === ed.doc.template_id ? 'outline:2px solid var(--brand)' : ''}">
          <div class="tpl-thumb">
            <img src="${esc(t.thumb)}" alt="${esc(t.name)}" loading="lazy">
            <span class="tpl-badge">${esc(t.ratio)}</span>
          </div>
          <div class="tpl-info"><b>${esc(t.name)}</b><div><span>${esc(t.category)}</span><span>${esc(t.style)}</span></div></div>
        </button>`).join('')}</div>`,
      onMount(el, close) {
        el.querySelectorAll('[data-t]').forEach((btn) => {
          btn.onclick = async () => {
            const t = ed.templates.find((x) => x.id === btn.dataset.t);
            try {
              const updated = await api(`/ppt/documents/${ed.doc.id}`, {
                method: 'PATCH',
                body: { slides: ed.doc.slides, cover_color: t.color_a, template_id: t.id },
              });
              ed.doc = updated;
              ed.doc.ratio = updated.ratio || t.ratio;
              ed.dirty = false;
              invalidateAllThumbs();
              renderAll();
              close();
              toast('模板已更换，版式已重新排版', 'success');
            } catch (err) { toast(err.message, 'error'); }
          };
        });
      },
    });
  }
}

void qs;
void promptDialog;
