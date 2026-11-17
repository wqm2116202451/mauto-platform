// 资讯视图：真实 RSS 资讯、行业分类、推荐/最新/热榜、筛选、详情 AI 摘录与收藏
import { api, qs } from '../api.js?v=19';
import { icon } from '../icons.js?v=19';
import {
  $, $$, esc, fmtTime, fmtDateTime, fmtNum, toast, openModal, emptyState,
  skeletonRows, track, copyText,
} from '../ui.js?v=19';

const SORTS = [['recommend', '推荐'], ['latest', '最新'], ['hot', '热榜']];
const RANGES = [['all', '全部时间'], ['today', '今天'], ['3d', '近 3 天'], ['7d', '近 7 天'], ['30d', '近 30 天']];

let st = {};
let listState = null;

export async function render(page, { id }) {
  if (id) {
    await renderDetail(page, id);
    return;
  }
  st = {
    industry: 'all', sort: 'recommend', q: '', timeRange: 'all', source: '',
    page: 1, pageSize: 10, items: [], sources: [], categories: [], total: 0,
    loading: false, sync: null,
  };
  page.innerHTML = `<div class="page-inner">
    <div class="page-head">
      <div>
        <h1>咨询</h1>
        <p>聚合 36氪、IT之家、中新网等公开 RSS 的真实资讯，支持按时间与来源筛选，AI 摘录要点。</p>
      </div>
      <div class="actions">
        <button class="btn ghost" id="news-refresh">${icon('refresh')} 刷新</button>
        <button class="btn ghost" id="news-fav">${icon('star')} 我的收藏</button>
        <button class="btn ghost" id="news-sub">${icon('bell')} 订阅行业</button>
      </div>
    </div>
    <div class="news-sync-note" id="news-sync-note"></div>
    <div class="news-layout">
      <aside class="news-side" id="news-side"></aside>
      <div>
        <div class="news-toolbar">
          <div class="search-inline">${icon('search')}<input id="news-q" placeholder="搜索资讯标题、摘要或正文…"></div>
          <div class="news-sort" id="news-sort">${SORTS.map(([k, v]) => `<button data-sort="${k}" class="${k === 'recommend' ? 'active' : ''}">${v}</button>`).join('')}</div>
          <select class="select" id="news-range" style="width:auto">${RANGES.map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select>
          <select class="select" id="news-source" style="width:auto"><option value="">全部来源</option></select>
        </div>
        <div class="news-list" id="news-list">${skeletonRows(4)}</div>
        <div style="display:flex;justify-content:center;margin-top:16px">
          <button class="btn ghost hidden" id="news-more">加载更多</button>
        </div>
      </div>
    </div>
  </div>`;

  $('#news-fav').onclick = openFavorites;
  $('#news-sub').onclick = openSubscribe;
  $('#news-refresh').onclick = refreshNews;
  $('#news-q').oninput = (() => {
    let timer = null;
    return (e) => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        st.q = e.target.value.trim();
        st.page = 1;
        loadList(true);
      }, 300);
    };
  })();
  $$('#news-sort button').forEach((btn) => {
    btn.onclick = () => {
      $$('#news-sort button').forEach((b) => b.classList.toggle('active', b === btn));
      st.sort = btn.dataset.sort;
      st.page = 1;
      loadList(true);
    };
  });
  $('#news-range').onchange = (e) => { st.timeRange = e.target.value; st.page = 1; loadList(true); };
  $('#news-source').onchange = (e) => { st.source = e.target.value; st.page = 1; loadList(true); };
  $('#news-more').onclick = () => { st.page += 1; loadList(false); };

  await loadCategories();
  await loadList(true);
}

async function loadCategories() {
  try {
    const data = await api('/news/categories');
    st.categories = data.items || [];
    st.subscriptions = data.subscriptions || [];
    renderSidebar();
  } catch (err) {
    toast(err.message, 'error');
  }
}

async function refreshNews() {
  const btn = $('#news-refresh');
  if (!btn || btn.disabled) return;
  const label = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '抓取中…';
  try {
    const res = await api('/news/refresh', { method: 'POST' });
    st.sync = res;
    if (res.error) toast(res.error, 'error', 4000);
    else toast(`已同步 ${res.items || 0} 条真实资讯`, 'success');
    await loadCategories();
    st.page = 1;
    await loadList(true);
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = label;
  }
}

function renderSyncNote() {
  const box = $('#news-sync-note');
  if (!box) return;
  const s = st.sync || {};
  const sources = (s.sources || []).slice(0, 6).join('、');
  const time = s.last_sync ? fmtDateTime(s.last_sync) : '尚未同步';
  const warn = s.error ? `<span class="sync-err">${esc(s.error)}</span>` : '';
  box.innerHTML = `<span class="sync-dot"></span>真实数据：${esc(sources || '公开 RSS 源')}${
    (s.sources || []).length > 6 ? ' 等' : ''} · 最近同步 ${esc(time)} · 共 ${s.items || 0} 条${warn}`;
}

function renderSidebar() {
  const box = $('#news-side');
  if (!box) return;
  box.innerHTML = st.categories.map((c) => `
    <button data-cat="${c.id}" class="${st.industry === c.id ? 'active' : ''}">
      <span>${esc(c.icon || '')}</span><span>${esc(c.name)}</span><span class="count">${c.count}</span>
    </button>`).join('');
  box.querySelectorAll('[data-cat]').forEach((btn) => {
    btn.onclick = () => {
      st.industry = btn.dataset.cat;
      st.page = 1;
      renderSidebar();
      loadList(true);
    };
  });
}

async function loadList(reset) {
  const box = $('#news-list');
  if (!box || st.loading) return;
  st.loading = true;
  if (reset) box.innerHTML = skeletonRows(3);
  try {
    const data = await api(`/news${qs({
      industry: st.industry, sort: st.sort, q: st.q, time_range: st.timeRange,
      source: st.source, page: st.page, page_size: st.pageSize,
    })}`);
    st.total = data.total;
    st.sources = data.sources || st.sources;
    st.sync = data.sync || st.sync;
    renderSyncNote();
    const srcSel = $('#news-source');
    if (srcSel && srcSel.options.length <= 1) {
      st.sources.forEach((s) => srcSel.insertAdjacentHTML('beforeend', `<option value="${esc(s)}">${esc(s)}</option>`));
    }
    st.items = reset ? data.list : [...st.items, ...data.list];
    renderList();
    const more = $('#news-more');
    if (more) more.classList.toggle('hidden', st.items.length >= st.total);
  } catch (err) {
    box.innerHTML = `<div class="empty"><p>${esc(err.message)}</p></div>`;
  } finally {
    st.loading = false;
  }
}

function renderList() {
  const box = $('#news-list');
  if (!box) return;
  if (!st.items.length) {
    const err = st.sync?.error;
    box.innerHTML = emptyState({
      iconName: 'news',
      title: err ? '真实资讯暂不可用' : '暂无匹配的资讯',
      desc: err ? `抓取失败：${err}。请点击右上角「刷新」重试，或稍后再试。`
        : '试试切换行业、放宽时间范围，或更换关键词。',
    });
    return;
  }
  box.innerHTML = st.items.map((n) => `
    <article class="news-card ${n.is_read ? 'read' : ''}" data-id="${n.id}">
      <button class="news-fav ${n.is_favorite ? 'on' : ''}" data-fav="${n.id}" title="收藏">${icon('star')}</button>
      <h3>${esc(n.title)}</h3>
      <p>${esc(n.summary)}</p>
      <div class="news-meta">
        <span class="tag brand">${esc(n.industry_icon || '')} ${esc(n.industry_name || '')}</span>
        <span class="src">${esc(n.source)}</span>
        <span>${fmtTime(n.publish_at)}</span>
        <span>${icon('eye')} ${fmtNum(n.read_count)}</span>
        <span class="news-ai-flag">${icon('spark')} AI 摘要</span>
        ${n.is_read ? '<span class="muted">已读</span>' : '<span class="dot-ok"></span>'}
      </div>
    </article>`).join('');
  box.querySelectorAll('.news-card').forEach((card) => {
    card.onclick = (e) => {
      if (e.target.closest('[data-fav]')) return;
      location.hash = `#/news/${card.dataset.id}`;
    };
    card.querySelector('[data-fav]').onclick = async (e) => {
      e.stopPropagation();
      try {
        const res = await api(`/news/${card.dataset.id}/favorite`, { method: 'POST' });
        const item = st.items.find((x) => x.id === card.dataset.id);
        if (item) item.is_favorite = res.is_favorite;
        card.querySelector('[data-fav]').classList.toggle('on', res.is_favorite);
        toast(res.is_favorite ? '已加入收藏' : '已取消收藏', 'success', 1500);
      } catch (err) { toast(err.message, 'error'); }
    };
  });
}

// ------------------------------------------------------------------ 详情
async function renderDetail(page, id) {
  page.innerHTML = `<div class="page-inner"><div class="news-detail">${skeletonRows(5)}</div></div>`;
  let news;
  try {
    news = await api(`/news/${id}`);
  } catch (err) {
    page.innerHTML = `<div class="page-inner">${emptyState({
      iconName: 'news', title: '资讯不存在', desc: err.message,
      action: '<button class="btn primary" onclick="location.hash=\'#/news\'">返回资讯列表</button>',
    })}</div>`;
    return;
  }
  track('click_news', { news_id: id });
  const paragraphs = String(news.content || '').split(/\n+/).map((p) => p.trim()).filter(Boolean);
  const sourceLink = news.url
    ? `<a class="source-link" href="${esc(news.url)}" target="_blank" rel="noopener noreferrer">${esc(news.source)} ${icon('external')}</a>`
    : `<span>${esc(news.source)}</span>`;
  page.innerHTML = `<div class="page-inner">
    <div class="news-detail">
      <button class="btn sm ghost" id="nd-back" style="margin-bottom:14px">${icon('arrowLeft')} 返回资讯</button>
      <h1>${esc(news.title)}</h1>
      <div class="detail-meta">
        <span class="tag brand">${esc(news.industry_icon || '')} ${esc(news.industry_name || '')}</span>
        ${sourceLink}
        <span>${fmtDateTime(news.publish_at)}</span>
        <span>${icon('eye')} ${fmtNum(news.read_count)}</span>
      </div>
      <div class="ai-brief">
        <div class="ai-head">${icon('spark')} AI 摘要 <span class="tag">${esc(news.ai?.model || 'MAuto-Air·摘录')}</span></div>
        <p>${esc(news.ai?.summary || news.summary || '暂无可用摘要')}</p>
        ${(news.ai?.points || []).length ? `<ul>${news.ai.points.map((p) => `<li>${esc(p)}</li>`).join('')}</ul>` : ''}
      </div>
      <div class="news-body">${paragraphs.length
        ? paragraphs.map((p) => `<p>${esc(p)}</p>`).join('')
        : '<p class="muted">原文正文暂时无法读取，请点击下方「阅读原文」查看完整内容。</p>'}</div>
      <div style="display:flex;gap:8px;margin-top:22px;flex-wrap:wrap">
        ${news.url ? `<a class="btn primary" href="${esc(news.url)}" target="_blank" rel="noopener noreferrer">${icon('external')} 阅读原文</a>` : ''}
        <button class="btn ghost" id="nd-ask">${icon('chat')} 一键追问</button>
        <button class="btn ghost" id="nd-fav">${icon('star')} ${news.is_favorite ? '取消收藏' : '收藏'}</button>
        <button class="btn ghost" id="nd-copy">${icon('copy')} 复制链接</button>
      </div>
      <div class="divider"></div>
      <div class="section-title"><div><h3 style="font-size:14px">相关资讯</h3><p>同行业最新发布的 5 条</p></div></div>
      <div class="related-list">
        ${(news.related || []).map((r) => `<div class="related-item" data-rel="${r.id}">
          <span style="color:var(--ink);font-size:12.5px">${esc(r.title)}</span>
          <span>${esc(r.source)} · ${fmtTime(r.publish_at)}</span>
        </div>`).join('') || '<div class="ws-empty">暂无相关资讯</div>'}
      </div>
    </div>
  </div>`;

  $('#nd-back').onclick = () => { location.hash = '#/news'; };
  $('#nd-ask').onclick = () => {
    const prompt = `请针对这条资讯继续深挖：\n《${news.title}》\n来源：${news.source}\n\n请分析它对行业的短期与中期影响，并给出 3 条可执行建议。`;
    location.hash = `#/chat?ask=${encodeURIComponent(prompt)}`;
  };
  $('#nd-copy').onclick = () => copyText(`${location.origin}/#/news/${news.id}`);
  $('#nd-fav').onclick = async (e) => {
    try {
      const res = await api(`/news/${news.id}/favorite`, { method: 'POST' });
      toast(res.is_favorite ? '已加入收藏' : '已取消收藏', 'success');
      $('#nd-fav').innerHTML = `${icon('star')} ${res.is_favorite ? '取消收藏' : '收藏'}`;
    } catch (err) { toast(err.message, 'error'); }
  };
  page.querySelectorAll('[data-rel]').forEach((row) => {
    row.onclick = () => { location.hash = `#/news/${row.dataset.rel}`; };
  });
}

// ------------------------------------------------------------------ 收藏 / 订阅
async function openFavorites() {
  openModal({
    title: '我的收藏',
    subtitle: '收藏的资讯会保持可读状态',
    className: 'wide',
    body: '<div id="fav-box">' + skeletonRows(3) + '</div>',
    onMount(el) {
      const box = el.querySelector('#fav-box');
      api('/news/favorites/mine?page_size=50').then((data) => {
        if (!data.list.length) {
          box.innerHTML = emptyState({ iconName: 'star', title: '还没有收藏', desc: '在资讯卡片或详情页点击星标即可收藏。' });
          return;
        }
        box.innerHTML = data.list.map((n) => `
          <div class="mine-row" data-id="${n.id}" style="cursor:pointer">
            <span class="task-icon" style="width:32px;height:32px;border-radius:9px">${icon('news')}</span>
            <div style="flex:1;min-width:0">
              <b style="font-size:12.5px;display:block">${esc(n.title)}</b>
              <span class="small muted">${esc(n.industry_name || '')} · ${esc(n.source)} · ${fmtTime(n.publish_at)}</span>
            </div>
            <button class="btn sm ghost" data-rm>取消收藏</button>
          </div>`).join('');
        box.querySelectorAll('.mine-row').forEach((row) => {
          row.onclick = (e) => {
            if (e.target.closest('[data-rm]')) return;
            location.hash = `#/news/${row.dataset.id}`;
          };
          row.querySelector('[data-rm]').onclick = async (e) => {
            e.stopPropagation();
            await api(`/news/${row.dataset.id}/favorite`, { method: 'POST' });
            row.remove();
            toast('已取消收藏', 'success');
          };
        });
      }).catch((err) => {
        box.innerHTML = `<div class="ws-empty">${esc(err.message)}</div>`;
      });
    },
  });
}

function openSubscribe() {
  const items = st.categories.filter((c) => c.id !== 'all');
  openModal({
    title: '订阅行业资讯',
    subtitle: '订阅后行业更新会推送到通知中心',
    className: 'slim',
    body: `<div style="display:flex;flex-direction:column;gap:6px">${items.map((c) => `
      <button class="chip" data-sub="${c.id}" style="justify-content:flex-start;height:36px;border-radius:8px">
        <span>${esc(c.icon || '')}</span><span>${esc(c.name)}</span>
        <span class="muted small" style="margin-left:auto">${c.count} 条</span>
      </button>`).join('')}</div>`,
    onMount(el, close) {
      el.querySelectorAll('[data-sub]').forEach((btn) => {
        btn.onclick = async () => {
          try {
            await api('/news-subscriptions', { method: 'POST', body: { type: 'industry', target_id: btn.dataset.sub } });
            toast('已订阅该行业', 'success');
            close();
          } catch (err) { toast(err.message, 'error'); }
        };
      });
    },
  });
}

void listState;
