// 接口访问层
const TOKEN_KEY = 'mauto_token';

export const state = {
  token: localStorage.getItem(TOKEN_KEY) || '',
  user: null,
  config: null,
  onUnauthorized: null,
};

export function setToken(token) {
  state.token = token || '';
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

function headers(extra = {}) {
  const h = { ...extra };
  if (state.token) h.Authorization = `Bearer ${state.token}`;
  return h;
}

async function parse(res) {
  let payload = null;
  try {
    payload = await res.json();
  } catch (err) {
    payload = null;
  }
  if (res.status === 401) {
    setToken('');
    if (state.onUnauthorized) state.onUnauthorized();
    throw new Error((payload && payload.message) || '登录已过期，请重新登录');
  }
  if (!res.ok) throw new Error((payload && payload.message) || `请求失败（${res.status}）`);
  if (payload && typeof payload.code === 'number' && payload.code !== 0) {
    throw new Error(payload.message || '请求失败');
  }
  return payload ? payload.data : null;
}

export async function api(path, { method = 'GET', body, form } = {}) {
  const opts = { method, headers: headers() };
  if (form) opts.body = form;
  else if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(`/api/v1${path}`, opts);
  return parse(res);
}

export function qs(params = {}) {
  const sp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v === undefined || v === null || v === '') return;
    sp.append(k, v);
  });
  const s = sp.toString();
  return s ? `?${s}` : '';
}

export async function download(path, fallbackName) {
  const res = await fetch(`/api/v1${path}`, { headers: headers() });
  if (!res.ok) {
    let msg = '下载失败';
    try {
      const data = await res.json();
      msg = data.message || msg;
    } catch (err) { /* ignore */ }
    throw new Error(msg);
  }
  const blob = await res.blob();
  const cd = res.headers.get('Content-Disposition') || '';
  let name = fallbackName || 'download';
  const utf = /filename\*=UTF-8''([^;]+)/i.exec(cd);
  const plain = /filename="?([^";]+)"?/i.exec(cd);
  if (utf) name = decodeURIComponent(utf[1]);
  else if (plain) name = plain[1];
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
  return name;
}

// 流式对话：解析 SSE
export async function streamChat(payload, handlers = {}, signal) {
  const res = await fetch('/api/v1/chat/completions', {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(payload),
    signal,
  });
  if (res.status === 401) {
    setToken('');
    if (state.onUnauthorized) state.onUnauthorized();
    throw new Error('登录已过期，请重新登录');
  }
  if (!res.ok || !res.body) {
    let msg = `请求失败（${res.status}）`;
    try {
      const data = await res.json();
      msg = data.message || msg;
    } catch (err) { /* ignore */ }
    throw new Error(msg);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  function dispatch(block) {
    const lines = block.split('\n');
    let event = 'message';
    let data = '';
    for (const line of lines) {
      if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (line.startsWith('data:')) data += line.slice(5).trim();
    }
    if (!data) return;
    let parsed = null;
    try {
      parsed = JSON.parse(data);
    } catch (err) {
      return;
    }
    if (handlers[event]) handlers[event](parsed);
  }

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split('\n\n');
    buffer = blocks.pop() || '';
    for (const block of blocks) {
      if (block.trim()) dispatch(block);
    }
  }
  buffer += decoder.decode();
  if (buffer.trim()) dispatch(buffer);
}
