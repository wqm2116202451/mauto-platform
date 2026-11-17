// UI 基础能力：DOM、Toast、Modal、菜单、时间格式化
import { icon } from './icons.js?v=19';
import { api } from './api.js?v=19';

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export function esc(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return '刚刚';
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 2) return '昨天';
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`;
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

export function fmtDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

export function fmtNum(n) {
  const v = Number(n) || 0;
  if (v >= 10000) return `${(v / 10000).toFixed(1).replace('.0', '')}万`;
  if (v >= 1000) return `${(v / 1000).toFixed(1).replace('.0', '')}k`;
  return String(v);
}

export function debounce(fn, wait = 300) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

export function toast(message, type = 'info', duration = 2600) {
  const root = $('#toast-root');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.innerHTML = `<span>${esc(message)}</span>`;
  root.appendChild(el);
  setTimeout(() => {
    el.classList.add('hide');
    setTimeout(() => el.remove(), 220);
  }, duration);
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast('已复制到剪贴板', 'success');
  } catch (err) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand('copy');
      toast('已复制到剪贴板', 'success');
    } catch (e) {
      toast('复制失败，请手动选择', 'error');
    }
    ta.remove();
  }
}

let modalSeq = 0;
const modalStack = [];
export function openModal({
  title = '', subtitle = '', body = '', footer = '', className = '',
  onMount, onClose, closeOnMask = true,
} = {}) {
  closePopovers();
  const root = $('#modal-root');
  const mask = document.createElement('div');
  mask.className = 'modal-mask';
  const id = `modal-${++modalSeq}`;
  mask.innerHTML = `
    <div class="modal ${className}" role="dialog" aria-modal="true" id="${id}">
      <div class="modal-head">
        <div>${title ? `<h3>${esc(title)}</h3>` : ''}${subtitle ? `<p>${esc(subtitle)}</p>` : ''}</div>
        <button class="modal-close" data-close aria-label="关闭">${icon('close')}</button>
      </div>
      <div class="modal-body">${body}</div>
      ${footer ? `<div class="modal-foot">${footer}</div>` : ''}
    </div>`;
  root.appendChild(mask);
  modalStack.push(mask);
  const el = mask.querySelector('.modal');
  const close = () => {
    const i = modalStack.indexOf(mask);
    if (i >= 0) modalStack.splice(i, 1);
    mask.remove();
    document.removeEventListener('keydown', onKey);
    if (onClose) onClose();
  };
  const onKey = (e) => {
    // 多层弹窗时只关闭最上层，避免一次 Esc 全部消失
    if (e.key === 'Escape' && modalStack[modalStack.length - 1] === mask) close();
  };
  mask.addEventListener('click', (e) => {
    if (e.target === mask && closeOnMask) close();
    if (e.target.closest('[data-close]')) close();
  });
  document.addEventListener('keydown', onKey);
  if (onMount) onMount(el, close);
  return { el, close };
}

export function confirmDialog({
  title = '确认操作', message = '', confirmText = '确认', cancelText = '取消', danger = false,
}) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (val) => {
      if (settled) return;
      settled = true;
      resolve(val);
    };
    const { close } = openModal({
      title,
      className: 'slim',
      body: `<p style="font-size:13px;line-height:1.75;color:var(--ink-2)">${esc(message)}</p>`,
      footer: `<button class="btn ghost" data-cancel>${esc(cancelText)}</button>
               <button class="btn ${danger ? 'danger' : 'primary'}" data-ok>${esc(confirmText)}</button>`,
      onMount(el, closeFn) {
        // 先 settle 再关闭：关闭会触发 onClose，避免其抢先 resolve(false)
        el.querySelector('[data-cancel]').onclick = () => { finish(false); closeFn(); };
        el.querySelector('[data-ok]').onclick = () => { finish(true); closeFn(); };
      },
      onClose: () => finish(false),
    });
    void close;
  });
}

export function promptDialog({ title, message = '', placeholder = '', value = '', confirmText = '确定' }) {
  return new Promise((resolve) => {
    let done = false;
    const m = openModal({
      title,
      className: 'slim',
      body: `${message ? `<p style="font-size:12.5px;color:var(--ink-2);margin-bottom:10px">${esc(message)}</p>` : ''}
             <input class="input" data-input placeholder="${esc(placeholder)}" value="${esc(value)}" />`,
      footer: `<button class="btn ghost" data-cancel>取消</button>
               <button class="btn primary" data-ok>${esc(confirmText)}</button>`,
      onMount(el, close) {
        const input = el.querySelector('[data-input]');
        input.focus();
        input.select();
        const submit = () => {
          done = true;
          const v = input.value.trim();
          close();
          resolve(v);
        };
        el.querySelector('[data-cancel]').onclick = () => { done = true; close(); resolve(null); };
        el.querySelector('[data-ok]').onclick = submit;
        input.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
      },
      onClose: () => { if (!done) resolve(null); },
    });
    void m;
  });
}

export function closeMenus() {
  $$('.menu').forEach((m) => m.remove());
}

// 关闭所有页级浮层（下拉菜单、通知面板等），打开弹窗/菜单前统一清理
export function closePopovers() {
  closeMenus();
  document.dispatchEvent(new CustomEvent('mauto:close-popovers'));
}
let menuGuard = false;
document.addEventListener('click', (e) => {
  if (menuGuard) return;
  if (!e.target.closest('.menu')) closeMenus();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeMenus();
});

export function showMenu(anchor, items) {
  closePopovers();
  // 打开菜单的这次点击仍会冒泡到 document，加一个瞬时守卫避免刚打开就被关闭
  menuGuard = true;
  setTimeout(() => { menuGuard = false; }, 0);
  const rect = anchor.getBoundingClientRect();
  const menu = document.createElement('div');
  menu.className = 'menu';
  menu.innerHTML = items.map((it, i) => (
    it.sep ? '<div class="sep"></div>'
      : `<button class="${it.danger ? 'danger' : ''}" data-i="${i}">${it.icon ? icon(it.icon) : ''}<span>${esc(it.label)}</span></button>`
  )).join('');
  document.querySelector('#menu-root').appendChild(menu);
  const mw = menu.offsetWidth;
  const mh = menu.offsetHeight;
  let left = rect.right - mw;
  let top = rect.bottom + 6;
  if (left < 8) left = 8;
  if (top + mh > window.innerHeight - 8) top = Math.max(8, rect.top - mh - 6);
  menu.style.left = `${left}px`;
  menu.style.top = `${top}px`;
  menu.addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-i]');
    if (!btn) return;
    const item = items[Number(btn.dataset.i)];
    closeMenus();
    if (item && item.onClick) item.onClick();
  });
  return menu;
}

export function emptyState({ iconName = 'spark', title, desc = '', action = '' }) {
  return `<div class="empty">
    <div class="art">${icon(iconName)}</div>
    <h3>${esc(title)}</h3>
    ${desc ? `<p>${esc(desc)}</p>` : ''}
    ${action}
  </div>`;
}

export function skeletonCards(count = 6, height = 110) {
  return `<div class="plugin-grid">${Array.from({ length: count }).map(() => (
    `<div class="card" style="padding:16px"><div class="skeleton" style="height:${height}px"></div></div>`
  )).join('')}</div>`;
}

export function skeletonRows(count = 4) {
  return Array.from({ length: count }).map(() => (
    `<div class="skeleton" style="height:64px;margin-bottom:10px;border-radius:12px"></div>`
  )).join('');
}

export function avatarHtml(name, avatar = '', size = 30) {
  if (avatar) return `<span class="avatar" style="width:${size}px;height:${size}px"><img src="${esc(avatar)}" alt=""></span>`;
  const ch = (name || 'M').trim().slice(0, 1).toUpperCase();
  return `<span class="avatar" style="width:${size}px;height:${size}px">${esc(ch)}</span>`;
}

export function track(event, payload = {}) {
  api('/events', { method: 'POST', body: { event, payload } }).catch(() => {});
}

export function navigate(hash) {
  if (location.hash === hash) window.dispatchEvent(new HashChangeEvent('hashchange'));
  else location.hash = hash;
}

export function statusTag(status, map = {}) {
  const conf = map[status] || { text: status, cls: '' };
  return `<span class="tag ${conf.cls || ''}">${esc(conf.text)}</span>`;
}

export function fileIcon(name = '') {
  const ext = name.split('.').pop().toLowerCase();
  if (['png', 'jpg', 'jpeg', 'webp'].includes(ext)) return 'IMG';
  if (ext === 'pdf') return 'PDF';
  if (['docx', 'doc'].includes(ext)) return 'DOC';
  if (['xlsx', 'xls', 'csv'].includes(ext)) return 'XLS';
  if (['pptx', 'ppt'].includes(ext)) return 'PPT';
  if (['md', 'txt'].includes(ext)) return 'TXT';
  return 'FILE';
}
