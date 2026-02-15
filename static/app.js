'use strict';

// ── State ─────────────────────────────────────────────────────────────────────
const state = {
  q:        '',
  offset:   0,
  total:    0,
  activeId: null,
  loading:  false,
};

// ── DOM refs ──────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

const searchEl      = $('search');
const resultCount   = $('result-count');
const convList      = $('conv-list');
const loadMoreWrap  = $('load-more-wrap');
const loadMoreBtn   = $('load-more');
const emptyState    = $('empty-state');
const thread        = $('thread');
const threadTitle   = $('thread-title');
const threadMeta    = $('thread-meta');
const messagesEl    = $('messages');

// ── Citation / artifact stripping ────────────────────────────────────────────
// ChatGPT embeds inline citation markers that its UI renders as numbered
// superscripts but are meaningless noise in raw text.
function sanitize(text) {
  return text
    // Private-use citation group: \ue200cite\ue202turn0search0\ue202turn0search1\ue201
    .replace(/\ue200[\s\S]*?\ue201/g, '')
    // Orphaned private-use citation chars (\ue200 open, \ue201 close, \ue202 sep)
    .replace(/[\ue200-\ue202]/g, '')
    // 【4†source】 style Unicode bracket citations (older export format)
    .replace(/\u3010[^\u3011]*\u3011/g, '')
    // Clean up any double spaces left behind
    .replace(/  +/g, ' ')
    .replace(/ ([.,;!?])/g, '$1');
}

// ── Markdown renderer ─────────────────────────────────────────────────────────
// Lightweight inline renderer — no external deps, works offline.

const md = (() => {
  const esc = s =>
    String(s)
      .replace(/&/g,  '&amp;')
      .replace(/</g,  '&lt;')
      .replace(/>/g,  '&gt;')
      .replace(/"/g,  '&quot;');

  // Inline formatting applied after HTML-escaping the raw text
  function inline(raw) {
    let s = esc(raw);
    s = s.replace(/`([^`\n]+)`/g,            (_, c) => `<code>${c}</code>`);
    s = s.replace(/\*\*\*(.+?)\*\*\*/g,      '<strong><em>$1</em></strong>');
    s = s.replace(/\*\*(.+?)\*\*/g,          '<strong>$1</strong>');
    s = s.replace(/__(.+?)__/g,              '<strong>$1</strong>');
    s = s.replace(/\*([^*\n]+)\*/g,          '<em>$1</em>');
    s = s.replace(/_([^_\n]+)_/g,            '<em>$1</em>');
    s = s.replace(/~~(.+?)~~/g,              '<del>$1</del>');
    s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g,
        (_, text, href) => `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${text}</a>`);
    return s;
  }

  function processBlock(src) {
    if (!src.trim()) return '';
    const html  = [];
    const lines = src.split('\n');
    let i = 0;

    while (i < lines.length) {
      const line = lines[i];

      // Blank line
      if (!line.trim()) { html.push(''); i++; continue; }

      // ATX heading
      const hm = line.match(/^(#{1,6})\s+(.*)/);
      if (hm) {
        html.push(`<h${hm[1].length}>${inline(hm[2])}</h${hm[1].length}>`);
        i++; continue;
      }

      // Horizontal rule
      if (/^[-*_]{3,}\s*$/.test(line)) {
        html.push('<hr>'); i++; continue;
      }

      // Blockquote
      if (line.startsWith('> ')) {
        const lines2 = [];
        while (i < lines.length && lines[i].startsWith('> ')) {
          lines2.push(inline(lines[i].slice(2)));
          i++;
        }
        html.push(`<blockquote><p>${lines2.join('<br>')}</p></blockquote>`);
        continue;
      }

      // Unordered list
      if (/^[*\-+] /.test(line)) {
        const items = [];
        while (i < lines.length && /^[*\-+] /.test(lines[i])) {
          items.push(`<li>${inline(lines[i].replace(/^[*\-+]\s+/, ''))}</li>`);
          i++;
        }
        html.push(`<ul>${items.join('')}</ul>`);
        continue;
      }

      // Ordered list
      if (/^\d+[.)]\s/.test(line)) {
        const items = [];
        while (i < lines.length && /^\d+[.)]\s/.test(lines[i])) {
          items.push(`<li>${inline(lines[i].replace(/^\d+[.)]\s+/, ''))}</li>`);
          i++;
        }
        html.push(`<ol>${items.join('')}</ol>`);
        continue;
      }

      // Paragraph — collect consecutive "normal" lines
      const para = [];
      while (
        i < lines.length &&
        lines[i].trim() &&
        !/^[*\-+] /.test(lines[i]) &&
        !/^\d+[.)]\s/.test(lines[i]) &&
        !/^#{1,6}\s/.test(lines[i]) &&
        !lines[i].startsWith('> ') &&
        !/^[-*_]{3,}\s*$/.test(lines[i])
      ) {
        para.push(inline(lines[i]));
        i++;
      }
      if (para.length) html.push(`<p>${para.join('<br>')}</p>`);
    }
    return html.join('\n');
  }

  return function render(src) {
    if (!src) return '';
    const out  = [];
    const FENCE = /```(\w*)\n?([\s\S]*?)```/g;
    let last = 0, m;

    while ((m = FENCE.exec(src)) !== null) {
      out.push(processBlock(src.slice(last, m.index)));
      const lang = m[1].trim();
      const code = m[2].trimEnd();
      out.push(
        `<pre${lang ? ` data-lang="${esc(lang)}"` : ''}><code>${esc(code)}</code></pre>`
      );
      last = m.index + m[0].length;
    }
    out.push(processBlock(src.slice(last)));
    return out.join('\n');
  };
})();

// ── Utilities ─────────────────────────────────────────────────────────────────

function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function formatDate(ts) {
  if (!ts) return '';
  const d       = new Date(ts * 1000);
  const now     = new Date();
  const diffMs  = now - d;
  const diffDays = diffMs / 86_400_000;

  if (diffDays < 1)   return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  if (diffDays < 7)   return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
  if (diffDays < 365) return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  return d.toLocaleDateString([], { year: 'numeric', month: 'short', day: 'numeric' });
}

// ── API calls ─────────────────────────────────────────────────────────────────

async function apiConversations(q, offset) {
  const p = new URLSearchParams({ limit: 50, offset });
  if (q) p.set('q', q);
  const r = await fetch(`/api/conversations?${p}`);
  return r.json();
}

async function apiConversation(id) {
  const r = await fetch(`/api/conversation/${encodeURIComponent(id)}`);
  return r.json();
}

// ── Render conversation list items ────────────────────────────────────────────

function appendListItems(convs) {
  for (const c of convs) {
    const el  = document.createElement('div');
    el.className  = 'conv-item' + (c.id === state.activeId ? ' active' : '');
    el.dataset.id = c.id;

    const snippet = (c.snippet || '').trim();

    el.innerHTML = `
      <div class="conv-title">${escHtml(c.title)}</div>
      ${snippet ? `<div class="conv-snippet">${snippet}</div>` : ''}
      <div class="conv-footer">
        <span>${formatDate(c.update_time || c.create_time)}</span>
        <span>${c.message_count} msg${c.message_count !== 1 ? 's' : ''}</span>
      </div>`;

    el.addEventListener('click', () => openConversation(c.id, el));
    convList.appendChild(el);
  }
}

// ── Load / refresh conversation list ─────────────────────────────────────────

async function loadConversations(append = false) {
  if (state.loading) return;
  state.loading = true;

  if (!append) {
    convList.innerHTML = '<div class="loading">Loading…</div>';
    state.offset = 0;
  }

  try {
    const data = await apiConversations(state.q, state.offset);
    state.total   = data.total;
    state.offset += (data.conversations || []).length;

    if (!append) convList.innerHTML = '';

    if (!data.conversations?.length && !append) {
      convList.innerHTML = '<div class="no-results">No conversations found.</div>';
    } else {
      appendListItems(data.conversations || []);
    }

    const n = state.total.toLocaleString();
    resultCount.textContent = state.q
      ? `${n} result${state.total !== 1 ? 's' : ''}`
      : `${n} conversation${state.total !== 1 ? 's' : ''}`;

    loadMoreWrap.hidden = state.offset >= state.total;

  } finally {
    state.loading = false;
  }
}

// ── Open a conversation ───────────────────────────────────────────────────────

async function openConversation(id, clickedEl) {
  // Update sidebar selection
  document.querySelectorAll('.conv-item.active')
    .forEach(el => el.classList.remove('active'));
  if (clickedEl) clickedEl.classList.add('active');
  state.activeId = id;

  // Show thread panel, clear previous content
  emptyState.hidden = true;
  thread.hidden     = false;
  messagesEl.innerHTML = '<div class="loading">Loading…</div>';
  threadTitle.textContent = '';
  threadMeta.textContent  = '';

  const data = await apiConversation(id);
  if (data.error) {
    messagesEl.innerHTML = `<div class="no-results">Error: ${escHtml(data.error)}</div>`;
    return;
  }

  const { conversation: conv, messages } = data;

  threadTitle.textContent = conv.title;
  const ts = formatDate(conv.update_time || conv.create_time);
  threadMeta.textContent  = `${ts} · ${conv.message_count} messages`;

  messagesEl.innerHTML = '';
  for (const msg of messages) {
    const div  = document.createElement('div');
    div.className = `message ${msg.role}`;

    const label = msg.role === 'user'      ? 'You'
                : msg.role === 'assistant' ? 'ChatGPT'
                : msg.role;

    const bodyEl = document.createElement('div');
    bodyEl.className   = 'message-body';
    bodyEl.innerHTML   = md(sanitize(msg.content));

    div.innerHTML = `<div class="message-role">${escHtml(label)}</div>`;
    div.appendChild(bodyEl);
    messagesEl.appendChild(div);
  }

  // Scroll thread to top
  messagesEl.scrollTop = 0;
}

// ── Search (debounced) ────────────────────────────────────────────────────────

let debounce;
searchEl.addEventListener('input', () => {
  clearTimeout(debounce);
  debounce = setTimeout(() => {
    state.q = searchEl.value.trim();
    loadConversations(false);
  }, 280);
});

searchEl.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    searchEl.value = '';
    state.q = '';
    loadConversations(false);
    searchEl.blur();
  }
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    const first = convList.querySelector('.conv-item');
    if (first) { first.click(); first.scrollIntoView({ block: 'nearest' }); }
  }
});

// ── Load more ─────────────────────────────────────────────────────────────────

loadMoreBtn.addEventListener('click', () => loadConversations(true));

// ── Keyboard navigation ───────────────────────────────────────────────────────

document.addEventListener('keydown', e => {
  // Don't intercept when typing in the search box
  if (e.target === searchEl) return;

  if (e.key === '/') {
    e.preventDefault();
    searchEl.focus();
    searchEl.select();
    return;
  }

  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    const items = [...convList.querySelectorAll('.conv-item')];
    const idx   = items.findIndex(el => el.classList.contains('active'));
    const next  = e.key === 'ArrowDown' ? items[idx + 1] : items[idx - 1];
    if (next) {
      next.click();
      next.scrollIntoView({ block: 'nearest' });
    }
  }
});

// ── Init ──────────────────────────────────────────────────────────────────────

loadConversations(false);
