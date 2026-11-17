// 聊天视图：流式对话、停止/重新生成、附件、插件、引用与导出
import { api, state, qs, streamChat, download } from '../api.js?v=19';
import { icon } from '../icons.js?v=19';
import { renderMarkdown, bindCopyButtons } from '../markdown.js?v=19';
import {
  $, $$, esc, fmtTime, fmtDateTime, toast, openModal, confirmDialog, promptDialog,
  showMenu, emptyState, copyText, debounce, track, fileIcon,
} from '../ui.js?v=19';

let uid = 0;
const nextId = (prefix = 'tmp') => `${prefix}_${Date.now()}_${++uid}`;

const MODEL_FALLBACK = [
  { id: 'MAuto-Air', name: 'MAuto-Air' },
  { id: 'MAuto-Pro', name: 'MAuto-Pro' },
  { id: 'MAuto-Mini', name: 'MAuto-Mini' },
];

export async function render(page, { id, params }) {
  if (params && params.all === '1') {
    await renderAllConversations(page);
    return;
  }
  if (id) {
    page.innerHTML = '<div class="page-inner narrow">' + skeletonChat() + '</div>';
    let conv;
    try {
      conv = await api(`/conversations/${id}`);
    } catch (err) {
      page.innerHTML = `<div class="page-inner narrow">${emptyState({
        iconName: 'chat', title: '会话不存在', desc: err.message,
        action: '<button class="btn primary" onclick="location.hash=\'#/chat\'">开始新对话</button>',
      })}</div>`;
      return;
    }
    page.innerHTML = '';
    const mounted = mountChat(page, { conversationId: conv.id, conversation: conv });
    activeChat = mounted;
  } else {
    page.innerHTML = '';
    const mounted = mountChat(page, { projectId: params?.project || null, initialAsk: params?.ask || '' });
    activeChat = mounted;
  }
}

let activeChat = null;

export function destroy() {
  if (activeChat) activeChat.destroy();
  activeChat = null;
}

function skeletonChat() {
  return `<div style="padding:24px 0">
    <div class="skeleton" style="height:34px;width:42%;margin-bottom:26px"></div>
    <div class="skeleton" style="height:18px;margin-bottom:10px"></div>
    <div class="skeleton" style="height:18px;width:86%;margin-bottom:10px"></div>
    <div class="skeleton" style="height:18px;width:64%;margin-bottom:30px"></div>
    <div class="skeleton" style="height:90px;border-radius:16px"></div>
  </div>`;
}

// ------------------------------------------------------------------ 主聊天挂载
export function mountChat(container, opts = {}) {
  const cfg = state.config || {};
  const prefs = state.user?.prefs || {};
  const st = {
    convId: opts.conversationId || null,
    projectId: opts.projectId || null,
    projectName: opts.projectName || '',
    messages: opts.conversation?.messages ? [...opts.conversation.messages] : [],
    streaming: false,
    abort: null,
    attachments: [],
    plugins: [],
    installed: [],
    model: prefs.model || 'MAuto-Air',
    web: !!prefs.web_search,
    think: !!prefs.deep_think,
    autoScroll: true,
    destroyed: false,
    reasoningOpen: false,
    initialAsk: opts.initialAsk || '',
  };

  container.innerHTML = `
    <div class="chat-wrap">
      <div class="chat-scroll" data-scroll>
        <div class="chat-body" data-body></div>
      </div>
      <button class="scroll-bottom hidden" data-scroll-bottom>${icon('chevronDown')} 回到底部</button>
      <div class="composer-wrap">
        <div class="composer">
          <div class="composer-toolbar" data-toolbar></div>
          <div class="composer-box">
            <div class="attach-chips hidden" data-attach></div>
            <textarea data-input rows="1" placeholder="输入你的问题，Enter 发送 / Shift+Enter 换行"></textarea>
            <div class="composer-foot">
              <div class="composer-left">
                <button class="icon-btn" data-attach-btn title="上传文件（pdf/docx/xlsx/pptx/txt/md/png/jpg，≤50MB）">${icon('paperclip')}</button>
                <span class="composer-hint">内容由 AI 生成，请谨慎核实</span>
              </div>
              <button class="send-btn" data-send title="发送">${icon('send')}</button>
            </div>
          </div>
          <input type="file" data-file hidden multiple accept=".pdf,.docx,.xlsx,.pptx,.txt,.md,.png,.jpg,.jpeg" />
        </div>
      </div>
    </div>`;

  const scrollEl = $('[data-scroll]', container);
  const bodyEl = $('[data-body]', container);
  const input = $('[data-input]', container);
  const sendBtn = $('[data-send]', container);
  const attachBox = $('[data-attach]', container);
  const toolbar = $('[data-toolbar]', container);
  bindCopyButtons(bodyEl);

  // -------------------------------------------------- 渲染
  function heroHtml() {
    const tips = cfg.recommend_questions || [];
    const title = st.projectId
      ? `在「${esc(st.projectName || '当前项目')}」内提问`
      : '有什么可以帮你的？';
    const desc = st.projectId
      ? '回答会结合项目知识库文件，并在末尾标注引用来源。'
      : '支持联网检索、深度思考、插件调用与文件理解；也可以直接拖入文档。';
    return `<div class="chat-hero">
      <h1>${title}</h1>
      <p>${desc}</p>
      <div class="suggest-grid">
        ${tips.slice(0, 6).map((t) => `
          <button class="suggest-card" data-suggest="${esc(t.prompt)}">
            <span class="si">${esc(t.icon || '💡')}</span>
            <span><b>${esc(t.title)}</b><span>${esc(t.desc || '')}</span></span>
          </button>`).join('')}
      </div>
    </div>`;
  }

  function linkCitations(html, citations) {
  if (!citations || !citations.length) return html;
  return String(html).split(/(<pre[\s\S]*?<\/pre>|<code[\s\S]*?<\/code>)/g).map((part) => {
    if (part.startsWith('<pre') || part.startsWith('<code')) return part;
    return part.replace(/\[(\d{1,2})\]/g, (raw, num) => {
      const cite = citations[Number(num) - 1];
      if (!cite || !cite.url) return raw;
      return `<a class="cite-ref" href="${esc(cite.url)}" target="_blank" rel="noopener"
        title="${esc(cite.title || '')}">${num}</a>`;
    });
  }).join('');
}

function messageHtml(m) {
    if (m.role === 'user') {
      const atts = (m.attachments || []).map((a) => (
        `<span class="tag" style="margin-right:4px">${esc(a.filename || '附件')}</span>`)).join('');
      return `<div class="msg user" data-mid="${m.id}">
        <div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px;max-width:76%">
          ${atts ? `<div>${atts}</div>` : ''}
          <div class="bubble-user">${esc(m.content)}</div>
          <div class="msg-actions visible" style="justify-content:flex-end">
            <button data-act="edit" data-id="${m.id}" title="编辑并重新发送">${icon('edit')}编辑</button>
            <button data-act="copy-user" data-id="${m.id}" title="复制">${icon('copy')}</button>
          </div>
        </div>
      </div>`;
    }
    const model = m.model || st.model;
    const citations = (m.citations || []).length ? `<div class="citations">${m.citations.map((c, i) => `
      <a class="citation" href="${esc(c.url || '#')}" target="_blank" rel="noopener">
        <span class="num">${i + 1}</span><b>${esc(c.title)}</b>
        <span>${esc(c.site)} · ${esc(c.time || '')}</span>
      </a>`).join('')}</div>` : '';
    const plugins = (m.plugin_calls || []).length ? m.plugin_calls.map((p) => `
      <div class="plugin-run">${p.icon || '🧩'} 已调用 ${esc(p.name)} · ${p.duration_ms}ms</div>`).join('') : '';
    const reasoning = m.reasoning ? reasoningHtml(m) : '';
    const streaming = m.streaming;
    return `<div class="msg" data-mid="${m.id}">
      <div class="msg-avatar">M</div>
      <div class="msg-body">
        <div class="msg-head"><b>MAuto</b><span>${esc(model)}</span><span>·</span><span>${streaming ? esc(m.progress || '生成中…') : fmtTime(m.created_at)}</span></div>
        ${reasoning}
        ${plugins}
        <div class="md ${streaming ? 'typing-cursor' : ''}" data-md>${linkCitations(renderMarkdown(m.content || ''), m.citations)}</div>
        ${citations}
        ${streaming ? '' : actionsHtml(m)}
      </div>
    </div>`;
  }

  function reasoningHtml(m) {
    const open = m.streaming || st.reasoningOpen;
    return `<div class="reasoning ${open ? '' : 'collapsed'}" data-reasoning>
      <div class="reasoning-head" data-toggle-reasoning>${m.streaming ? '<span class="dot"></span>' : '🧠'}
        <span>${m.streaming ? '正在深度思考…' : '查看思考过程'}</span>
        <span style="margin-left:auto;font-size:10px;color:var(--ink-3)">${open ? '收起' : '展开'}</span>
      </div>
      <div class="reasoning-body">${esc(m.reasoning)}</div>
    </div>`;
  }

  function actionsHtml(m) {
    return `<div class="msg-actions">
      <button data-act="copy" data-id="${m.id}">${icon('copy')}复制</button>
      <button data-act="regen" data-id="${m.id}">${icon('refresh')}重新生成</button>
      <button data-act="like" data-id="${m.id}" class="${m.feedback === 1 ? 'active' : ''}">${icon('thumbup')}</button>
      <button data-act="dislike" data-id="${m.id}" class="${m.feedback === -1 ? 'active' : ''}">${icon('thumbdown')}</button>
      <button data-act="quote" data-id="${m.id}">${icon('chat')}引用追问</button>
      <button data-act="share" data-id="${m.id}">${icon('share')}分享</button>
      <button data-act="export" data-id="${m.id}">${icon('download')}导出</button>
    </div>`;
  }

  function renderBody() {
    if (!st.messages.length) {
      bodyEl.innerHTML = heroHtml();
      bindHero();
      return;
    }
    bodyEl.innerHTML = `<div class="chat-inner">${st.messages.map(messageHtml).join('')}</div>`;
    bindMessages();
  }

  function bindHero() {
    $$('[data-suggest]', bodyEl).forEach((btn) => {
      btn.onclick = () => {
        input.value = btn.dataset.suggest;
        resize();
        send();
      };
    });
  }

  function bindMessages() {
    $$('.msg', bodyEl).forEach((row) => {
      const mid = row.dataset.mid;
      const msg = st.messages.find((m) => m.id === mid);
      if (!msg) return;
      row.querySelectorAll('[data-act]').forEach((btn) => {
        btn.onclick = () => handleAction(btn.dataset.act, msg, btn);
      });
      const toggle = row.querySelector('[data-toggle-reasoning]');
      if (toggle) {
        toggle.onclick = () => {
          const box = row.querySelector('[data-reasoning]');
          box.classList.toggle('collapsed');
          st.reasoningOpen = !box.classList.contains('collapsed');
        };
      }
    });
  }

  // -------------------------------------------------- 工具栏
  function renderToolbar() {
    const models = cfg.models || MODEL_FALLBACK;
    const cur = models.find((m) => m.id === st.model) || models[0];
    const pluginCount = st.plugins.length;
    toolbar.innerHTML = `
      <button class="tool-pill" data-tb="model">${icon('spark')} ${esc(cur.name)} ${icon('chevronDown')}</button>
      <button class="tool-pill ${st.web ? 'active' : ''}" data-tb="web">${icon('globe')} 联网搜索</button>
      <button class="tool-pill ${st.think ? 'active' : ''}" data-tb="think">${icon('brain')} 深度思考</button>
      <button class="tool-pill ${pluginCount ? 'active' : ''}" data-tb="plugin">${icon('plugins')} 插件${pluginCount ? ` · ${pluginCount}` : ''}</button>`;
    toolbar.querySelector('[data-tb=model]').onclick = (e) => {
      showMenu(e.currentTarget, models.map((m) => ({
        label: `${m.name}${m.id === st.model ? ' ✓' : ''}`,
        onClick: () => { st.model = m.id; renderToolbar(); savePrefs(); },
      })));
    };
    toolbar.querySelector('[data-tb=web]').onclick = () => { st.web = !st.web; renderToolbar(); savePrefs(); };
    toolbar.querySelector('[data-tb=think]').onclick = () => { st.think = !st.think; renderToolbar(); savePrefs(); };
    toolbar.querySelector('[data-tb=plugin]').onclick = (e) => {
      const items = st.installed.length ? st.installed.map((p) => ({
        label: `${p.icon} ${p.name}${st.plugins.includes(p.id) ? ' ✓' : ''}`,
        onClick: () => {
          st.plugins = st.plugins.includes(p.id)
            ? st.plugins.filter((x) => x !== p.id)
            : [...st.plugins, p.id];
          renderToolbar();
        },
      })) : [{ label: '暂无已安装插件，去插件市场安装', onClick: () => { location.hash = '#/plugins'; } }];
      showMenu(e.currentTarget, items);
    };
  }

  const savePrefs = debounce(() => {
    api('/users/me', { method: 'PATCH', body: { prefs: { model: st.model, web_search: st.web, deep_think: st.think } } })
      .then((u) => { state.user = u; }).catch(() => {});
  }, 400);

  async function loadInstalled() {
    try {
      const data = await api('/plugins/mine');
      st.installed = (data.installed || []).filter((p) => p.installed_status === 'enabled');
    } catch (err) { st.installed = []; }
    renderToolbar();
  }

  // -------------------------------------------------- 输入框
  function resize() {
    input.style.height = 'auto';
    input.style.height = `${Math.min(input.scrollHeight, 168)}px`;
  }
  input.addEventListener('input', resize);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
    if (e.key === 'ArrowUp' && !input.value.trim() && !st.streaming) {
      const lastUser = [...st.messages].reverse().find((m) => m.role === 'user');
      if (lastUser) {
        e.preventDefault();
        input.value = lastUser.content;
        resize();
      }
    }
  });
  sendBtn.onclick = () => {
    if (st.streaming) stopGeneration();
    else send();
  };

  // 附件
  $('[data-attach-btn]', container).onclick = () => $('[data-file]', container).click();
  $('[data-file]', container).onchange = async (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = '';
    if (!files.length) return;
    if (st.attachments.length + files.length > 5) {
      toast('单次最多上传 5 个文件', 'error');
      return;
    }
    for (const file of files.slice(0, 5)) {
      if (file.size > 50 * 1024 * 1024) {
        toast(`${file.name} 超过 50MB`, 'error');
        continue;
      }
      const chip = { id: nextId('att'), filename: file.name, status: 'uploading' };
      st.attachments.push(chip);
      renderAttachments();
      const form = new FormData();
      form.append('file', file);
      try {
        const res = await api('/files', { method: 'POST', form });
        chip.id = res.id;
        chip.status = res.parse_status === 'failed' ? 'failed' : 'done';
        if (res.parse_status === 'failed') toast(`${file.name} 解析失败，请更换文件`, 'error');
      } catch (err) {
        chip.status = 'failed';
        toast(`${file.name}：${err.message}`, 'error');
      }
      renderAttachments();
    }
  };

  function renderAttachments() {
    if (!st.attachments.length) {
      attachBox.classList.add('hidden');
      attachBox.innerHTML = '';
      return;
    }
    attachBox.classList.remove('hidden');
    attachBox.innerHTML = st.attachments.map((a) => `
      <span class="attach-chip ${a.status === 'failed' ? 'failed' : ''}">
        ${fileIcon(a.filename)} ${esc(a.filename)}
        ${a.status === 'uploading' ? '· 解析中…' : a.status === 'failed' ? '· 解析失败' : ''}
        <button data-rm="${a.id}">${icon('close')}</button>
      </span>`).join('');
    attachBox.querySelectorAll('[data-rm]').forEach((btn) => {
      btn.onclick = () => {
        st.attachments = st.attachments.filter((x) => x.id !== btn.dataset.rm);
        renderAttachments();
      };
    });
  }

  // -------------------------------------------------- 滚动
  function scrollToBottom(smooth = false) {
    scrollEl.scrollTo({ top: scrollEl.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
    st.autoScroll = true;
    $('[data-scroll-bottom]', container).classList.add('hidden');
  }
  scrollEl.addEventListener('scroll', () => {
    const gap = scrollEl.scrollHeight - scrollEl.scrollTop - scrollEl.clientHeight;
    st.autoScroll = gap < 120;
    $('[data-scroll-bottom]', container).classList.toggle('hidden', gap < 220);
  });
  $('[data-scroll-bottom]', container).onclick = () => scrollToBottom(true);

  // -------------------------------------------------- 发送 / 流式
  async function ensureConversation() {
    if (st.convId) return st.convId;
    const payload = {};
    if (st.projectId) payload.project_id = st.projectId;
    payload.model = st.model;
    const conv = await api('/conversations', { method: 'POST', body: payload });
    st.convId = conv.id;
    if (window.MAUTO?.setCurrentConversationId) window.MAUTO.setCurrentConversationId(conv.id);
    history.replaceState(null, '', `#/chat/${conv.id}`);
    return conv.id;
  }

  async function send({ regenerate = false, assistantId = null, editMessageId = null, presetText = null } = {}) {
    if (st.streaming) return;
    let text = presetText !== null ? presetText : input.value.trim();
    if (!text && !regenerate && !editMessageId && !st.attachments.length) return;
    const convId = await ensureConversation().catch((err) => {
      toast(err.message, 'error');
      return null;
    });
    if (!convId) return;

    const attachments = st.attachments.filter((a) => a.status === 'done')
      .map((a) => ({ id: a.id, filename: a.filename }));

    if (regenerate && assistantId) {
      const idx = st.messages.findIndex((m) => m.id === assistantId);
      st.messages = idx >= 0 ? st.messages.slice(0, idx) : st.messages;
    }
    if (editMessageId) {
      const idx = st.messages.findIndex((m) => m.id === editMessageId);
      if (idx >= 0) {
        st.messages[idx].content = text;
        st.messages = st.messages.slice(0, idx + 1);
      }
    } else if (!regenerate) {
      st.messages.push({
        id: nextId('m'), role: 'user', content: text, attachments,
        created_at: new Date().toISOString(),
      });
      if (text && !_titleSet && !opts.conversation) {
        window.MAUTO?.setPageTitle(text.slice(0, 20));
        _titleSet = true;
      }
    } else {
      // 重新生成：移除末尾助手消息
      while (st.messages.length && st.messages[st.messages.length - 1].role === 'assistant') st.messages.pop();
    }

    const temp = {
      id: nextId('m'), role: 'assistant', content: '', reasoning: '', citations: [],
      plugin_calls: [], model: st.model, streaming: true, created_at: new Date().toISOString(),
    };
    st.messages.push(temp);
    input.value = '';
    st.attachments = [];
    renderAttachments();
    resize();
    renderBody();
    scrollToBottom();
    setStreaming(true);
    track('send_message', { conversation_id: convId, model: st.model, web: st.web, think: st.think });

    st.abort = new AbortController();
    const body = {
      conversation_id: convId, content: text, model: st.model,
      web_search: st.web, deep_think: st.think, plugin_ids: st.plugins,
      attachments, regenerate, message_id: assistantId, edit_message_id: editMessageId,
    };
    let raf = null;
    let idleTimer = null;
    const flush = () => {
      if (raf) return;
      raf = setTimeout(() => {
        raf = null;
        paintStreaming(temp);
      }, 20);
    };
    const finalizeIdle = () => {
      if (!temp.streaming) return;
      temp.clientFinalize = true;
      temp.streaming = false;
      finishStreaming(temp);
      setStreaming(false);
      if (st.abort) st.abort.abort();
    };
    const touch = () => {
      clearTimeout(idleTimer);
      idleTimer = setTimeout(finalizeIdle, 2500);
    };
    idleTimer = setTimeout(finalizeIdle, 45000);
    try {
      await streamChat(body, {
        meta(data) {
          temp.realId = data.message_id;
          if (data.title) window.MAUTO?.setPageTitle(data.title);
          touch();
        },
        reasoning(data) {
          temp.reasoning += data.text;
          flush();
          touch();
        },
        plugin(data) {
          temp.plugin_calls = [...(temp.plugin_calls || []), data];
          flush();
          touch();
        },
        progress(data) {
          temp.progress = data.text || '正在联网检索…';
          flush();
          touch();
        },
        delta(data) {
          temp.content += data.text;
          flush();
          touch();
        },
        citation(data) {
          temp.citations = data.citations || [];
          flush();
          touch();
        },
        done(data) {
          temp.completed = true;
          temp.streaming = false;
          temp.realId = data.message_id;
          temp.status = data.status;
          temp.citations = data.citations || temp.citations;
          temp.created_at = new Date().toISOString();
          finishStreaming(temp);
          if (st.abort) st.abort.abort();
        },
        error(data) {
          temp.completed = true;
          toast(data.message || '生成失败', 'error');
          temp.streaming = false;
          finishStreaming(temp);
        },
      }, st.abort.signal);
    } catch (err) {
      if (err.name === 'AbortError') {
        if (temp.completed) {
          // done 已处理，主动关闭连接
        } else if (temp.clientFinalize) {
          temp.streaming = false;
          finishStreaming(temp);
        } else {
          temp.streaming = false;
          temp.status = 'stopped';
          finishStreaming(temp, true);
        }
      } else {
        toast(err.message || '网络异常，请检查网络后重试', 'error');
        temp.streaming = false;
        finishStreaming(temp);
      }
    } finally {
      clearTimeout(idleTimer);
      clearTimeout(raf);
      if (temp.streaming) {
        temp.streaming = false;
        finishStreaming(temp);
      }
      setStreaming(false);
      st.abort = null;
      if (window.MAUTO?.refreshRecentList) window.MAUTO.refreshRecentList();
    }
  }

  let _titleSet = false;

  function paintStreaming(temp) {
    if (!temp.streaming) return;
    const row = bodyEl.querySelector(`[data-mid="${temp.id}"]`) || bodyEl.querySelector(`[data-mid="${temp.realId}"]`);
    if (!row) return;
    const md = row.querySelector('[data-md]');
    if (md) md.innerHTML = renderMarkdown(temp.content || '');
    const head = row.querySelector('.msg-head span:last-child');
    if (head && temp.streaming) {
      head.textContent = (!temp.content && temp.progress) ? temp.progress : '生成中…';
    }
    if (temp.reasoning) {
      let box = row.querySelector('[data-reasoning]');
      if (!box) {
        row.querySelector('.msg-head').insertAdjacentHTML('afterend', reasoningHtml(temp));
      } else {
        box.querySelector('.reasoning-body').textContent = temp.reasoning;
      }
    }
    if (st.autoScroll) scrollEl.scrollTop = scrollEl.scrollHeight;
  }

  function finishStreaming(temp, stopped = false) {
    temp.streaming = false;
    temp.completed = true;
    if (!temp.created_at) temp.created_at = new Date().toISOString();
    // 用真实消息 id 替换临时消息
    const idx = st.messages.findIndex((m) => m === temp);
    if (idx >= 0 && temp.realId) st.messages[idx].id = temp.realId;
    const targetId = temp.realId || temp.id;
    const row = bodyEl.querySelector(`[data-mid="${temp.id}"]`) || bodyEl.querySelector(`[data-mid="${targetId}"]`);
    if (row) {
      row.dataset.mid = targetId;
      let ok = false;
      try {
        row.innerHTML = messageHtml({ ...temp, id: targetId, streaming: false });
        ok = true;
      } catch (err) {
        window.__MAUTO_ERR = String(err && err.stack ? err.stack : err);
      }
      // 双重保障：无论走哪条分支，都确保头部时间与操作区正确
      row.querySelectorAll('.typing-cursor').forEach((el) => el.classList.remove('typing-cursor'));
      const head = row.querySelector('.msg-head');
      if (head) {
        head.querySelectorAll('span').forEach((s) => {
          if ((s.textContent || '').includes('生成中')) s.textContent = fmtTime(temp.created_at);
        });
      }
      const bodyCell = row.querySelector('.msg-body');
      if (!ok && bodyCell) {
        const md = bodyCell.querySelector('[data-md]');
        if (md) md.innerHTML = renderMarkdown(temp.content || '');
        if (!bodyCell.querySelector('.msg-actions')) {
          bodyCell.insertAdjacentHTML('beforeend', actionsHtml({ ...temp, id: targetId }));
        }
      }
      try { bindMessages(); } catch (err) {
        window.__MAUTO_ERR = String(err && err.stack ? err.stack : err);
      }
    } else {
      // 找不到对应行（例如流式期间被重绘）时全量重绘，保证完成态一致
      renderBody();
    }
    if (stopped) toast('已停止生成', 'info', 1600);
    if (st.autoScroll) scrollEl.scrollTop = scrollEl.scrollHeight;
  }

  function setStreaming(on) {
    st.streaming = on;
    sendBtn.className = `send-btn ${on ? 'stop' : ''}`;
    sendBtn.innerHTML = on ? icon('stop') : icon('send');
    sendBtn.title = on ? '停止生成' : '发送';
    input.disabled = on;
    if (!on) input.focus();
  }

  async function stopGeneration() {
    if (st.abort) st.abort.abort();
    try {
      if (st.convId) await api('/chat/stop', { method: 'POST', body: { conversation_id: st.convId } });
    } catch (err) { /* ignore */ }
    track('stop_generate', { conversation_id: st.convId });
  }

  // -------------------------------------------------- 消息操作
  async function handleAction(act, msg, btn) {
    if (act === 'copy' || act === 'copy-user') {
      copyText(msg.content);
      return;
    }
    if (act === 'like' || act === 'dislike') {
      const val = act === 'like' ? 1 : -1;
      const next = msg.feedback === val ? 0 : val;
      try {
        await api(`/messages/${msg.id}/feedback`, { method: 'POST', body: { feedback: next } });
        msg.feedback = next;
        btn.classList.toggle('active', next === val);
        toast(next === val ? (act === 'like' ? '感谢反馈' : '已记录，我们会改进') : '已取消', 'success', 1500);
      } catch (err) { toast(err.message, 'error'); }
      return;
    }
    if (act === 'regen') {
      await send({ regenerate: true, assistantId: msg.id });
      return;
    }
    if (act === 'quote') {
      input.value = `关于以下内容，我想继续追问：\n> ${msg.content.replace(/\n+/g, ' ').slice(0, 180)}\n\n`;
      resize();
      input.focus();
      return;
    }
    if (act === 'share') {
      copyText(`${location.origin}/#/chat/${st.convId}`);
      return;
    }
    if (act === 'export') {
      showMenu(btn, [
        { label: '导出 Markdown', icon: 'download', onClick: () => doExport('md') },
        { label: '导出 Word', icon: 'file', onClick: () => doExport('docx') },
        { label: '复制全文', icon: 'copy', onClick: () => copyText(msg.content) },
      ]);
      return;
    }
    if (act === 'edit') {
      const text = await promptDialog({
        title: '编辑提问', message: '保存后将从此条开始重新生成回答。', value: msg.content, confirmText: '重新发送',
      });
      if (text === null) return;
      if (!text.trim()) { toast('内容不能为空', 'error'); return; }
      await send({ editMessageId: msg.id, presetText: text });
    }
  }

  async function doExport(format) {
    try {
      await download(`/conversations/${st.convId}/export?format=${format}`, `conversation.${format}`);
      toast('导出完成', 'success');
    } catch (err) { toast(err.message, 'error'); }
  }

  // -------------------------------------------------- 初始化
  renderToolbar();
  renderBody();
  loadInstalled();
  if (st.messages.length) {
    setTimeout(() => scrollToBottom(), 60);
    const conv = opts.conversation;
    if (conv && window.MAUTO?.setPageTitle) window.MAUTO.setPageTitle(conv.title, '对话');
  } else {
    setTimeout(() => input.focus(), 80);
  }
  if (st.initialAsk) {
    input.value = st.initialAsk;
    resize();
    setTimeout(() => send(), 120);
  }
  if (opts.conversation) _titleSet = true;

  return {
    destroy() {
      st.destroyed = true;
      if (st.abort) {
        try { st.abort.abort(); } catch (err) { /* ignore */ }
      }
      if (st.convId) {
        api('/chat/stop', { method: 'POST', body: { conversation_id: st.convId } }).catch(() => {});
      }
    },
    getConversationId() { return st.convId; },
    refresh() {
      if (st.convId) {
        api(`/conversations/${st.convId}`).then((conv) => {
          st.messages = conv.messages || [];
          renderBody();
          scrollToBottom();
        }).catch(() => {});
      }
    },
  };
}

// ------------------------------------------------------------------ 全部会话
async function renderAllConversations(page) {
  const st = { page: 1, q: '', archived: false };
  page.innerHTML = `<div class="page-inner">
    <div class="page-head">
      <div><h1>全部会话</h1><p>按最后活跃时间排序，置顶会话始终在最前。</p></div>
      <div class="actions"><button class="btn primary" id="new-conv">${icon('plus')} 新建会话</button></div>
    </div>
    <div class="card" style="padding:14px">
      <div class="row" style="margin-bottom:10px">
        <input class="input" id="conv-q" placeholder="搜索会话标题或消息内容…">
        <select class="select" id="conv-archive" style="max-width:150px">
          <option value="0">未归档</option><option value="1">已归档</option>
        </select>
      </div>
      <div id="conv-list">${'<div class="skeleton" style="height:56px;margin-bottom:8px"></div>'.repeat(4)}</div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:12px">
        <span class="small muted" id="conv-total"></span>
        <div style="display:flex;gap:6px">
          <button class="btn sm ghost" id="conv-prev">上一页</button>
          <button class="btn sm ghost" id="conv-next">下一页</button>
        </div>
      </div>
    </div>
  </div>`;

  const load = async () => {
    const list = $('#conv-list');
    try {
      const data = await api(`/conversations${qs({ page: st.page, page_size: 20, q: st.q, archived: st.archived })}`);
      $('#conv-total').textContent = `共 ${data.total} 条`;
      if (!data.list.length) {
        list.innerHTML = emptyState({ iconName: 'chat', title: '暂无会话', desc: '开始一次新的对话，历史记录会出现在这里。' });
        return;
      }
      list.innerHTML = data.list.map((c) => `
        <div class="mine-row" data-id="${c.id}" style="cursor:pointer">
          <span class="task-icon" style="width:32px;height:32px;border-radius:9px">${icon('chat')}</span>
          <div style="flex:1;min-width:0">
            <b style="font-size:13px;display:block">${esc(c.title)} ${c.is_pinned ? '📌' : ''}</b>
            <span class="small muted">${esc(c.model || '')} · ${fmtDateTime(c.updated_at)}</span>
          </div>
          <button class="btn sm ghost" data-open>打开</button>
          <button class="icon-btn" data-del>${icon('trash')}</button>
        </div>`).join('');
      list.querySelectorAll('.mine-row').forEach((row) => {
        row.onclick = (e) => {
          if (e.target.closest('[data-del]')) return;
          location.hash = `#/chat/${row.dataset.id}`;
        };
        row.querySelector('[data-del]').onclick = async () => {
          const ok = await confirmDialog({ title: '删除会话', message: '删除后不可恢复。', danger: true, confirmText: '删除' });
          if (!ok) return;
          await api(`/conversations/${row.dataset.id}`, { method: 'DELETE' });
          toast('已删除', 'success');
          load();
          window.dispatchEvent(new Event('mauto:refresh-recents'));
        };
      });
    } catch (err) {
      list.innerHTML = `<div class="empty"><p>${esc(err.message)}</p></div>`;
    }
  };
  $('#new-conv').onclick = () => { location.hash = '#/chat'; };
  $('#conv-q').oninput = debounce((e) => { st.q = e.target.value.trim(); st.page = 1; load(); }, 300);
  $('#conv-archive').onchange = (e) => { st.archived = e.target.value === '1'; st.page = 1; load(); };
  $('#conv-prev').onclick = () => { if (st.page > 1) { st.page -= 1; load(); } };
  $('#conv-next').onclick = () => { st.page += 1; load(); };
  await load();
}
