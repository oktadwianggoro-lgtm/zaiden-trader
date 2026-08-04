(() => {
  'use strict';

  const $ = (sel) => document.querySelector(sel);
  const esc = (v = '') => String(v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const idNum = new Intl.NumberFormat('id-ID');
  const idCmpct = new Intl.NumberFormat('id-ID', { notation: 'compact', maximumFractionDigits: 2 });
  const fmt = (v) => idNum.format(Number(v || 0));
  const fmtAuto = (v) => {
    const n = Number(v);
    if (isNaN(n)) return String(v ?? '—');
    if (Math.abs(n) >= 1e12) return `Rp ${idCmpct.format(n / 1e12)} T`;
    if (Math.abs(n) >= 1e9)  return `Rp ${idCmpct.format(n / 1e9)} M`;
    if (Math.abs(n) >= 1e6)  return `Rp ${idCmpct.format(n / 1e6)} jt`;
    if (Number.isInteger(n) && Math.abs(n) > 999) return fmt(n);
    if (!Number.isInteger(n)) return n.toLocaleString('id-ID', {minimumFractionDigits: 2, maximumFractionDigits: 4});
    return String(n);
  };

  // ── State ─────────────────────────────────────────────────────────────────
  let history = [];        // [{role:'user'|'ai', content:'', ...}]
  let isLoading = false;
  let apiConfigured = false;
  let msgCounter = 0;

  const QUICK_QUESTIONS = [
    '🏆 Top 10 gainer hari ini?',
    '📉 Top 10 loser minggu ini?',
    '🌍 Saham dengan net buy asing terbesar bulan ini?',
    '🏦 Broker terbesar berdasarkan nilai transaksi kemarin?',
    '📊 Volume perdagangan tertinggi hari ini?',
    '💰 Siapa pemegang saham terbesar BBCA?',
    '🔥 Emiten dengan frekuensi transaksi terbanyak minggu ini?',
    '📈 Tren nilai pasar 3 bulan terakhir?',
    '🏢 Saham yang paling banyak dipegang asing?',
    '⚡ Broker mana yang paling aktif di pasar?',
  ];

  // ── API ───────────────────────────────────────────────────────────────────
  async function checkStatus() {
    try {
      const r = await fetch('/api/chat/status', { cache: 'no-store' });
      const d = await r.json();
      apiConfigured = d.configured === true;
      renderApiStatus();
    } catch { apiConfigured = false; renderApiStatus(); }
  }

  async function saveApiKey(key) {
    const r = await fetch('/api/chat/key', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key}),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'Gagal menyimpan API key.');
    return d;
  }

  async function sendChatRequest(question) {
    const r = await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        question,
        history: history.slice(-6).map(h => ({ role: h.role === 'ai' ? 'model' : 'user', content: h.text })),
      }),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
    return d;
  }

  // ── UI Rendering ──────────────────────────────────────────────────────────
  function renderApiStatus() {
    const badge = $('#chatStatusBadge');
    const setup = $('#chatSetupCard');
    const inputBar = $('#chatInputBar');
    if (!badge) return;

    if (apiConfigured) {
      badge.className = 'chat-status-badge connected';
      badge.innerHTML = '<span class="chat-status-dot"></span> Terhubung · Gemini AI';
      if (setup) setup.hidden = true;
      if (inputBar) inputBar.removeAttribute('data-disabled');
      if (!history.length) renderQuickQuestions();
    } else {
      badge.className = 'chat-status-badge disconnected';
      badge.innerHTML = '<span class="chat-status-dot"></span> Belum Dikonfigurasi';
      if (setup) setup.hidden = false;
      if (inputBar) inputBar.setAttribute('data-disabled', '');
    }
  }

  function renderQuickQuestions() {
    const area = $('#chatMessages');
    if (!area || area.querySelector('.chat-quick')) return;

    const div = document.createElement('div');
    div.className = 'chat-quick';
    div.innerHTML = `
      <div class="chat-quick-header">
        <span class="chat-ai-avatar">✨</span>
        <div>
          <b>Zaiden AI siap membantu!</b>
          <p>Tanyakan apa saja tentang data saham Anda. Beberapa contoh pertanyaan:</p>
        </div>
      </div>
      <div class="chat-chips">
        ${QUICK_QUESTIONS.map(q => `<button class="chat-chip" data-q="${esc(q.replace(/^[^ ]+ /, ''))}">${esc(q)}</button>`).join('')}
      </div>`;
    area.appendChild(div);

    div.querySelectorAll('.chat-chip').forEach(btn => {
      btn.addEventListener('click', () => {
        const q = btn.dataset.q;
        submitQuestion(q);
      });
    });
  }

  function addUserBubble(text) {
    const area = $('#chatMessages');
    // Remove quick questions on first message
    area.querySelector('.chat-quick')?.remove();

    const id = ++msgCounter;
    const div = document.createElement('div');
    div.className = 'chat-bubble-wrap user';
    div.id = `msg-${id}`;
    div.innerHTML = `
      <div class="chat-bubble user">
        <p>${esc(text)}</p>
      </div>`;
    area.appendChild(div);
    scrollToBottom();
    return id;
  }

  function addTypingIndicator() {
    const area = $('#chatMessages');
    const div = document.createElement('div');
    div.className = 'chat-bubble-wrap ai';
    div.id = 'chat-typing';
    div.innerHTML = `
      <span class="chat-ai-avatar">✨</span>
      <div class="chat-bubble ai typing-bubble">
        <div class="chat-typing-dots"><span></span><span></span><span></span></div>
        <small class="chat-thinking-label">AI sedang menganalisis data…</small>
      </div>`;
    area.appendChild(div);
    scrollToBottom();
  }

  function removeTypingIndicator() {
    $('#chat-typing')?.remove();
  }

  function addAiBubble(result, question) {
    const area = $('#chatMessages');
    const id = ++msgCounter;
    const div = document.createElement('div');
    div.className = 'chat-bubble-wrap ai';
    div.id = `msg-${id}`;

    const hasData = result.has_data && result.columns?.length && result.rows?.length;
    const hasChart = hasData && result.chart_type !== 'none' && result.chart_x_col && result.chart_y_col;

    div.innerHTML = `
      <span class="chat-ai-avatar">✨</span>
      <div class="chat-bubble ai">
        ${result.summary ? `<div class="chat-summary">${esc(result.summary)}</div>` : ''}
        ${result.insight ? `<div class="chat-insight">${renderInsight(result.insight)}</div>` : ''}
        ${hasChart ? `<div class="chat-chart-wrap" id="chart-${id}"></div>` : ''}
        ${hasData ? renderTable(result.columns, result.rows) : (!result.has_data && result.columns?.length === 0 ? '<p class="chat-nodata">Tidak ada data yang ditemukan.</p>' : '')}
        ${result.sql ? `<details class="chat-sql-detail"><summary>🔍 Lihat Query SQL</summary><pre class="chat-sql-code">${esc(result.sql)}</pre></details>` : ''}
        ${result.suggestions?.length ? renderSuggestions(result.suggestions) : ''}
      </div>`;

    area.appendChild(div);

    if (hasChart) {
      requestAnimationFrame(() => {
        renderChart(`chart-${id}`, result, result.columns, result.rows);
      });
    }

    scrollToBottom();
    history.push({ role: 'ai', text: result.summary + ' ' + result.insight });

    // Bind suggestion chips
    div.querySelectorAll('.chat-suggestion').forEach(btn => {
      btn.addEventListener('click', () => submitQuestion(btn.dataset.q));
    });
  }

  function addErrorBubble(errorMsg) {
    const area = $('#chatMessages');
    const div = document.createElement('div');
    div.className = 'chat-bubble-wrap ai';
    div.innerHTML = `
      <span class="chat-ai-avatar">⚠️</span>
      <div class="chat-bubble ai error-bubble">
        <b>Terjadi kesalahan</b>
        <p>${esc(errorMsg)}</p>
        <small>Periksa API key Anda atau coba pertanyaan yang berbeda.</small>
      </div>`;
    area.appendChild(div);
    scrollToBottom();
  }

  function renderInsight(text) {
    // Convert double newlines to paragraphs
    return text.split(/\n\n+/).map(p => `<p>${esc(p.trim())}</p>`).join('');
  }

  function renderTable(cols, rows) {
    if (!cols?.length || !rows?.length) return '';
    const maxRows = 50;
    const displayRows = rows.slice(0, maxRows);
    const extra = rows.length > maxRows ? `<tr><td colspan="${cols.length}" class="chat-table-more">… ${rows.length - maxRows} baris lagi tersembunyi</td></tr>` : '';

    return `
      <div class="chat-table-wrap">
        <table class="chat-table">
          <thead><tr>${cols.map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead>
          <tbody>
            ${displayRows.map(row => `<tr>${row.map(v => `<td>${v != null ? esc(String(v)) : '—'}</td>`).join('')}</tr>`).join('')}
            ${extra}
          </tbody>
        </table>
      </div>`;
  }

  function renderSuggestions(suggestions) {
    return `
      <div class="chat-suggestions">
        <small>Pertanyaan lanjutan:</small>
        <div class="chat-suggestion-chips">
          ${suggestions.slice(0,3).map(s => `<button class="chat-suggestion" data-q="${esc(s)}">${esc(s)}</button>`).join('')}
        </div>
      </div>`;
  }

  function renderChart(containerId, result, cols, rows) {
    const container = $(`#${containerId}`);
    if (!container) return;

    const xCol = result.chart_x_col;
    const yCol = result.chart_y_col;
    const xIdx = cols.indexOf(xCol);
    const yIdx = cols.indexOf(yCol);
    if (xIdx === -1 || yIdx === -1) return;

    const data = rows.slice(0, 20).map(r => ({
      label: String(r[xIdx] ?? ''),
      value: Number(r[yIdx]) || 0,
    }));
    if (!data.length) return;

    const title = result.chart_title || `${yCol} per ${xCol}`;
    const type = result.chart_type === 'line' ? 'line' : 'bar';

    const width = container.clientWidth || 520;
    const height = 220;
    const pad = { top: 24, right: 16, bottom: 52, left: 70 };
    const cW = width - pad.left - pad.right;
    const cH = height - pad.top - pad.bottom;
    const maxV = Math.max(...data.map(d => d.value)) || 1;
    const minV = Math.min(0, ...data.map(d => d.value));
    const range = maxV - minV || 1;

    const getX = (i) => pad.left + (i / Math.max(1, data.length - 1)) * cW;
    const getXBar = (i) => pad.left + (i / data.length) * cW + (cW / data.length) * 0.15;
    const barW = Math.max(4, (cW / data.length) * 0.7);
    const getY = (v) => pad.top + cH - ((v - minV) / range) * cH;

    let bars = '', path = '', xLabels = '', yLabels = '';
    const interval = Math.max(1, Math.ceil(data.length / 8));

    const COLORS = ['#2f68e8','#7658d6','#18a06f','#dc9624','#d65463','#3b91a4'];

    data.forEach((d, i) => {
      const color = COLORS[i % COLORS.length];
      if (type === 'bar') {
        const x = getXBar(i), y = getY(Math.max(0, d.value)), h = getY(0) - y;
        bars += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${Math.max(1, h).toFixed(1)}" fill="${color}" fill-opacity="0.82" rx="3" data-val="${esc(String(d.value))}">
          <title>${esc(d.label)}: ${esc(String(d.value))}</title></rect>`;
      } else {
        path += `${i === 0 ? 'M' : 'L'}${getX(i).toFixed(1)},${getY(d.value).toFixed(1)} `;
      }
      if (i % interval === 0 || i === data.length - 1) {
        const lx = type === 'bar' ? getXBar(i) + barW / 2 : getX(i);
        const lbl = d.label.length > 12 ? d.label.slice(0, 11) + '…' : d.label;
        xLabels += `<text x="${lx.toFixed(1)}" y="${(pad.top + cH + 16).toFixed(1)}" text-anchor="middle" class="chat-svg-axis" transform="rotate(-30,${lx.toFixed(1)},${(pad.top + cH + 14).toFixed(1)})">${esc(lbl)}</text>`;
      }
    });

    for (let i = 0; i <= 4; i++) {
      const v = minV + (range / 4) * i;
      const y = getY(v);
      const lbl = Math.abs(v) >= 1e9 ? `${(v/1e9).toFixed(1)}M` : Math.abs(v) >= 1e6 ? `${(v/1e6).toFixed(1)}jt` : v.toLocaleString('id-ID', {maximumFractionDigits: 1});
      yLabels += `<line x1="${pad.left}" y1="${y.toFixed(1)}" x2="${width - pad.right}" y2="${y.toFixed(1)}" stroke="#e8ecf2" stroke-width="1"/>`;
      yLabels += `<text x="${(pad.left - 6).toFixed(1)}" y="${(y + 3).toFixed(1)}" text-anchor="end" class="chat-svg-axis">${esc(lbl)}</text>`;
    }

    if (type === 'line' && data.length > 1) {
      // Area fill
      const areaPath = `${path}L${getX(data.length-1).toFixed(1)},${getY(minV).toFixed(1)} L${getX(0).toFixed(1)},${getY(minV).toFixed(1)} Z`;
      bars = `<path d="${areaPath}" fill="#2f68e8" fill-opacity="0.08"/>
              <path d="${path}" fill="none" stroke="#2f68e8" stroke-width="2.5" stroke-linejoin="round"/>`;
      // Dots
      data.forEach((d, i) => {
        bars += `<circle cx="${getX(i).toFixed(1)}" cy="${getY(d.value).toFixed(1)}" r="3" fill="#2f68e8"><title>${esc(d.label)}: ${esc(String(d.value))}</title></circle>`;
      });
    }

    // Zero line if needed
    const zeroLine = minV < 0 ? `<line x1="${pad.left}" y1="${getY(0).toFixed(1)}" x2="${width - pad.right}" y2="${getY(0).toFixed(1)}" stroke="#aab3c0" stroke-width="1" stroke-dasharray="3,3"/>` : '';

    container.innerHTML = `
      <div class="chat-chart-title">${esc(title)}</div>
      <svg viewBox="0 0 ${width} ${height}" style="width:100%;height:auto;overflow:visible">
        ${yLabels}${zeroLine}${bars}${xLabels}
        <text x="${(pad.left + cW / 2).toFixed(1)}" y="${height}" text-anchor="middle" class="chat-svg-axis" style="font-size:9px;opacity:.6">${esc(xCol)}</text>
      </svg>`;
  }

  function scrollToBottom() {
    const area = $('#chatMessages');
    if (area) area.scrollTop = area.scrollHeight;
  }

  // ── Core flow ─────────────────────────────────────────────────────────────
  async function submitQuestion(text) {
    text = text.trim();
    if (!text || isLoading || !apiConfigured) return;

    isLoading = true;
    history.push({ role: 'user', text });

    addUserBubble(text);
    addTypingIndicator();

    // Clear input
    const inp = $('#chatInput');
    if (inp) { inp.value = ''; inp.style.height = 'auto'; }
    updateSendBtn();

    try {
      const result = await sendChatRequest(text);
      removeTypingIndicator();
      addAiBubble(result, text);
    } catch (e) {
      removeTypingIndicator();
      addErrorBubble(e.message);
    } finally {
      isLoading = false;
      updateSendBtn();
    }
  }

  function updateSendBtn() {
    const btn = $('#chatSendBtn');
    const inp = $('#chatInput');
    if (!btn) return;
    btn.disabled = isLoading || !inp?.value.trim() || !apiConfigured;
    btn.innerHTML = isLoading
      ? '<span class="chat-send-spinner"></span>'
      : '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>';
  }

  // ── API Key setup card ─────────────────────────────────────────────────────
  function bindSetupCard() {
    const form = $('#chatSetupForm');
    if (!form) return;
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const key = $('#chatApiKeyInput')?.value.trim();
      if (!key) return;
      const btn = $('#chatSetupSubmitBtn');
      btn.disabled = true; btn.textContent = 'Menyimpan…';
      try {
        await saveApiKey(key);
        apiConfigured = true;
        renderApiStatus();
        renderQuickQuestions();
      } catch (err) {
        alert('Gagal: ' + err.message);
      } finally {
        btn.disabled = false; btn.textContent = 'Sambungkan';
      }
    });

    // Toggle visibility
    $('#chatApiKeyToggle')?.addEventListener('click', () => {
      const inp = $('#chatApiKeyInput');
      if (!inp) return;
      inp.type = inp.type === 'password' ? 'text' : 'password';
    });
  }

  // ── Event bindings ─────────────────────────────────────────────────────────
  function bindEvents() {
    const inp = $('#chatInput');
    const sendBtn = $('#chatSendBtn');
    const form = $('#chatForm');

    if (!form) return;

    form.addEventListener('submit', (e) => {
      e.preventDefault();
      submitQuestion(inp?.value || '');
    });

    inp?.addEventListener('input', () => {
      // Auto-resize textarea
      inp.style.height = 'auto';
      inp.style.height = Math.min(inp.scrollHeight, 120) + 'px';
      updateSendBtn();
    });

    inp?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        submitQuestion(inp.value);
      }
    });

    // Clear chat
    $('#chatClearBtn')?.addEventListener('click', () => {
      const area = $('#chatMessages');
      if (area) area.innerHTML = '';
      history = [];
      if (apiConfigured) renderQuickQuestions();
    });

    bindSetupCard();
  }

  // ── Router integration ────────────────────────────────────────────────────
  let initialized = false;
  document.addEventListener('zaiden:viewchange', (e) => {
    if (e.detail.view !== 'chat') return;
    if (!initialized) {
      initialized = true;
      checkStatus();
      bindEvents();
    }
  });
})();
