// 轻量 Markdown 渲染器（标题 / 列表 / 表格 / 代码块 / 引用 / 链接 / 公式）
function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function highlight(code, lang) {
  let out = escapeHtml(code);
  out = out.replace(/(&quot;[^&\n]*?&quot;|&#39;[^&\n]*?&#39;|`[^`\n]*`)/g, '<span class="tk-str">$1</span>');
  out = out.replace(/\b(def|class|return|if|elif|else|for|while|import|from|as|try|except|finally|with|lambda|await|async|yield|const|let|var|function|new|throw|catch|int|float|str|bool|None|True|False|null|true|false|public|private|void)\b/g,
    '<span class="tk-kw">$1</span>');
  out = out.replace(/(#[^\n<]*)/g, '<span class="tk-com">$1</span>');
  out = out.replace(/\b(\d+\.?\d*)\b/g, '<span class="tk-num">$1</span>');
  void lang;
  return out;
}

function inline(text) {
  let s = text;
  s = s.replace(/`([^`]+)`/g, (_, code) => `<code class="inline">${escapeHtml(code)}</code>`);
  s = s.replace(/\$([^$\n]+)\$/g, (_, formula) => `<span class="math">${escapeHtml(formula)}</span>`);
  s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (_, alt, src) => `<img src="${src}" alt="${escapeHtml(alt)}">`);
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, href) => {
    const url = /^(https?:|\/|#)/.test(href) ? href : `https://${href}`;
    return `<a href="${url}" target="_blank" rel="noopener">${label}</a>`;
  });
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
  s = s.replace(/~~([^~]+)~~/g, '<del>$1</del>');
  return s;
}

function splitRow(line) {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());
}

function isTableSep(line) {
  return /^\s*\|?[\s:-]+\|[\s:|-]*$/.test(line) && line.includes('-');
}

export function renderMarkdown(text = '') {
  const lines = String(text).replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let i = 0;

  const flushParagraph = (buf) => {
    if (!buf.length) return;
    const content = buf.map((l) => inline(escapeHtml(l))).join('<br>');
    out.push(`<p>${content}</p>`);
    buf.length = 0;
  };

  const para = [];
  while (i < lines.length) {
    const line = lines[i];

    if (/^\s*```/.test(line)) {
      flushParagraph(para);
      const lang = line.trim().replace(/^```/, '').trim() || 'code';
      const code = [];
      i += 1;
      while (i < lines.length && !/^\s*```/.test(lines[i])) {
        code.push(lines[i]);
        i += 1;
      }
      i += 1;
      const raw = code.join('\n');
      out.push(`<pre><div class="code-head"><span>${escapeHtml(lang)}</span><button data-copy-code>复制</button></div><code>${highlight(raw, lang)}</code></pre>`);
      continue;
    }

    if (!line.trim()) {
      flushParagraph(para);
      i += 1;
      continue;
    }

    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      flushParagraph(para);
      const level = heading[1].length;
      out.push(`<h${level}>${inline(escapeHtml(heading[2]))}</h${level}>`);
      i += 1;
      continue;
    }

    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) {
      flushParagraph(para);
      out.push('<hr>');
      i += 1;
      continue;
    }

    if (/^\s*>/.test(line)) {
      flushParagraph(para);
      const quote = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) {
        quote.push(lines[i].replace(/^\s*>\s?/, ''));
        i += 1;
      }
      out.push(`<blockquote>${quote.map((l) => `<p>${inline(escapeHtml(l))}</p>`).join('')}</blockquote>`);
      continue;
    }

    if (/^\s*\|/.test(line) && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      flushParagraph(para);
      const head = splitRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) {
        rows.push(splitRow(lines[i]));
        i += 1;
      }
      const thead = `<thead><tr>${head.map((c) => `<th>${inline(escapeHtml(c))}</th>`).join('')}</tr></thead>`;
      const tbody = `<tbody>${rows.map((r) => `<tr>${head.map((_, idx) => `<td>${inline(escapeHtml(r[idx] ?? ''))}</td>`).join('')}</tr>`).join('')}</tbody>`;
      out.push(`<table>${thead}${tbody}</table>`);
      continue;
    }

    if (/^\s*(?:[-*+]|\d+\.)\s+/.test(line)) {
      flushParagraph(para);
      const ordered = /^\s*\d+\./.test(line);
      const items = [];
      while (i < lines.length && /^\s*(?:[-*+]|\d+\.)\s+/.test(lines[i])) {
        const indent = lines[i].match(/^\s*/)[0].length;
        const content = lines[i].replace(/^\s*(?:[-*+]|\d+\.)\s+/, '');
        // 支持一级缩进嵌套
        if (indent >= 2 && items.length) {
          items[items.length - 1].children = items[items.length - 1].children || [];
          items[items.length - 1].children.push(content);
        } else {
          items.push({ content, children: [] });
        }
        i += 1;
      }
      const tag = ordered ? 'ol' : 'ul';
      const body = items.map((it) => {
        const sub = it.children.length
          ? `<ul>${it.children.map((c) => `<li>${inline(escapeHtml(c))}</li>`).join('')}</ul>` : '';
        return `<li>${inline(escapeHtml(it.content))}${sub}</li>`;
      }).join('');
      out.push(`<${tag}>${body}</${tag}>`);
      continue;
    }

    para.push(line);
    i += 1;
  }
  flushParagraph(para);
  return out.join('\n');
}

export function bindCopyButtons(root) {
  root.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-copy-code]');
    if (!btn) return;
    const code = btn.closest('pre').querySelector('code');
    navigator.clipboard.writeText(code.innerText).then(() => {
      btn.textContent = '已复制';
      setTimeout(() => { btn.textContent = '复制'; }, 1500);
    }).catch(() => {
      btn.textContent = '复制失败';
      setTimeout(() => { btn.textContent = '复制'; }, 1500);
    });
  });
}

export default renderMarkdown;
