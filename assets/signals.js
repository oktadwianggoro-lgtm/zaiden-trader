/* =========================================================================
   SIGNALS.JS — Technical Screener: Prediksi Sinyal Beli
   Engine: SMA/EMA/MACD/RSI/Stoch/ADX/BB/OBV/ATR + Fibonacci TP/SL
   7 horizon: 1H · 1W · 1M · 3M · 6M · 1Y · >1Y
   ========================================================================= */
(() => {
  'use strict';

  const $  = (s, ctx) => (ctx || document).querySelector(s);
  const $$ = (s, ctx) => [...(ctx || document).querySelectorAll(s)];
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c =>
    ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));

  const fmtN = v => new Intl.NumberFormat('id-ID').format(Number(v || 0));
  const fmtC = v => {
    const n = Number(v || 0);
    if (Math.abs(n) >= 1e12) return `${(n/1e12).toFixed(1)}T`;
    if (Math.abs(n) >= 1e9)  return `${(n/1e9).toFixed(1)}M`;
    if (Math.abs(n) >= 1e6)  return `${(n/1e6).toFixed(1)}jt`;
    return fmtN(n);
  };
  const fmtPct = (v, d=2) => v == null ? '—' : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(d)}%`;
  const fmtPrice = v => `Rp ${fmtN(v)}`;

  const HORIZONS = [
    { id: '1d',   label: '1 Hari',    icon: '⚡', desc: 'Sinyal intraday / carry overnight' },
    { id: '1w',   label: '1 Minggu',  icon: '📅', desc: 'Potensi kenaikan dalam 5 hari trading' },
    { id: '1m',   label: '1 Bulan',   icon: '📆', desc: 'Potensi kenaikan dalam ~20 sesi' },
    { id: '3m',   label: '3 Bulan',   icon: '📊', desc: 'Potensi kenaikan dalam ~60 sesi' },
    { id: '6m',   label: '6 Bulan',   icon: '📈', desc: 'Potensi kenaikan dalam ~120 sesi' },
    { id: '1y',   label: '1 Tahun',   icon: '🌟', desc: 'Potensi kenaikan dalam ~250 sesi' },
    { id: 'gt1y', label: '> 1 Tahun', icon: '💎', desc: 'Sinyal jangka panjang multi-tahun' },
  ];

  const SIGNAL_COLORS = {
    'STRONG BUY':     { bg: '#0d9488', text: '#fff', border: '#0d9488' },
    'BUY':            { bg: '#22c55e', text: '#fff', border: '#22c55e' },
    'SPECULATIVE BUY':{ bg: '#f59e0b', text: '#fff', border: '#f59e0b' },
    'WATCH':          { bg: '#6366f1', text: '#fff', border: '#6366f1' },
  };

  const TREND_ICONS = {
    'strong uptrend': '🚀', 'uptrend': '📈', 'sideways': '↔️',
    'downtrend': '📉', 'below MA200': '⚠️',
  };

  const state = {
    horizon: '1w',
    data: null,
    loading: false,
    sortBy: 'score',
    sortDir: 'desc',
    filterSignal: 'all',
  };

  /* ── API ─────────────────────────────────────────────────────────── */
  async function api(url) {
    const r = await fetch(url, { cache: 'no-store' });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  }

  /* ── Load signals ────────────────────────────────────────────────── */
  async function loadSignals(horizon) {
    if (state.loading) return;
    state.horizon = horizon || state.horizon;
    state.loading = true;
    renderLoading();
    try {
      const data = await api(`/api/screener/signals?horizon=${state.horizon}&limit=30`);
      state.data = data;
      renderResults(data);
    } catch (e) {
      renderError(e.message);
    } finally {
      state.loading = false;
    }
  }

  /* ── Render loading ──────────────────────────────────────────────── */
  function renderLoading() {
    const el = $('#signalsGrid');
    if (!el) return;
    el.innerHTML = `
      <div class="signals-loading">
        <div class="signals-spinner"></div>
        <div>
          <strong>Menganalisis ${state.horizon === '1d' ? 'intraday momentum' : 'technical signals'}…</strong>
          <p style="margin:4px 0 0;opacity:.6;font-size:13px">Menghitung ${HORIZONS.find(h=>h.id===state.horizon)?.label || ''} signals untuk ~989 saham IDX</p>
        </div>
      </div>`;
    const meta = $('#signalsMeta');
    if (meta) meta.innerHTML = '';
  }

  /* ── Render error ────────────────────────────────────────────────── */
  function renderError(msg) {
    const el = $('#signalsGrid');
    if (el) el.innerHTML = `<div class="signals-error">⚠️ ${esc(msg)}</div>`;
  }

  /* ── Score bar ───────────────────────────────────────────────────── */
  function scoreBar(score) {
    const pct = score;
    let color = '#6366f1';
    if (score >= 78) color = '#0d9488';
    else if (score >= 63) color = '#22c55e';
    else if (score >= 50) color = '#f59e0b';
    return `
      <div class="sig-score-wrap" title="Kekuatan sinyal: ${score}/100">
        <div class="sig-score-bar">
          <div class="sig-score-fill" style="width:${pct}%;background:${color}"></div>
        </div>
        <span class="sig-score-num">${score}</span>
      </div>`;
  }

  /* ── Indicator chips ─────────────────────────────────────────────── */
  function indicatorChips(item) {
    const chips = [];

    if (item.rsi14 != null) {
      const rsiCls = item.rsi14 < 35 ? 'chip-green' : item.rsi14 > 70 ? 'chip-red' : 'chip-blue';
      chips.push(`<span class="sig-chip ${rsiCls}" title="RSI 14">RSI ${item.rsi14}</span>`);
    }
    if (item.adx14 != null) {
      const adxCls = item.adx14 > 35 ? 'chip-green' : item.adx14 > 25 ? 'chip-blue' : 'chip-gray';
      chips.push(`<span class="sig-chip ${adxCls}" title="ADX 14">ADX ${item.adx14}</span>`);
    }
    if (item.macd_signal) {
      const mc = item.macd_signal === 'bullish' ? 'chip-green' : 'chip-red';
      chips.push(`<span class="sig-chip ${mc}" title="MACD signal">MACD ↑</span>`);
    }
    const trendIcon = TREND_ICONS[item.trend] || '';
    if (item.trend) {
      chips.push(`<span class="sig-chip chip-blue" title="Trend: ${item.trend}">${trendIcon} ${item.trend}</span>`);
    }
    if (item.foreign_flow && item.foreign_flow !== 'neutral') {
      const fc = item.foreign_flow.includes('accum') ? 'chip-green' : item.foreign_flow.includes('dist') ? 'chip-red' : 'chip-blue';
      chips.push(`<span class="sig-chip ${fc}" title="Foreign flow">🌍 ${esc(item.foreign_flow)}</span>`);
    }
    if (item.volume_trend && item.volume_trend !== 'normal') {
      const vc = item.volume_trend.includes('high') ? 'chip-green' : 'chip-gray';
      chips.push(`<span class="sig-chip ${vc}" title="Volume ratio: ${item.vol_ratio}x">Vol ${esc(item.volume_trend)}</span>`);
    }
    if (item.bb_position && item.bb_position !== 'middle') {
      chips.push(`<span class="sig-chip chip-blue" title="Bollinger Bands">BB: ${esc(item.bb_position)}</span>`);
    }

    return chips.slice(0, 5).join('');
  }

  /* ── TP/SL price block ───────────────────────────────────────────── */
  function tpSlBlock(item) {
    if (!item.tp || !item.sl || !item.close) return '';
    const range = item.close - item.sl + (item.tp - item.close);
    const closePct = range > 0 ? ((item.close - item.sl) / range * 100) : 50;

    return `
      <div class="sig-tp-sl">
        <div class="sig-tp-sl-row">
          <span class="sig-tp-label">Target Profit</span>
          <span class="sig-tp-val tp">${fmtPrice(item.tp)}</span>
          <span class="sig-tp-pct tp">+${item.upside_pct?.toFixed(1)}%</span>
        </div>
        <div class="sig-tp-sl-bar">
          <div class="sig-tp-sl-track">
            <div class="sig-tp-sl-sl-zone" style="width:${closePct.toFixed(1)}%"></div>
            <div class="sig-tp-sl-marker" style="left:${closePct.toFixed(1)}%" title="Harga sekarang: ${fmtPrice(item.close)}"></div>
          </div>
          <div class="sig-tp-sl-labels">
            <span class="sl-label">SL ${fmtPrice(item.sl)}</span>
            <span class="price-label">${fmtPrice(item.close)}</span>
            <span class="tp-label">TP ${fmtPrice(item.tp)}</span>
          </div>
        </div>
        <div class="sig-tp-sl-row">
          <span class="sig-tp-label">Stop Loss</span>
          <span class="sig-tp-val sl">${fmtPrice(item.sl)}</span>
          <span class="sig-tp-pct sl">-${item.downside_pct?.toFixed(1)}%</span>
        </div>
        <div class="sig-rr">
          <span>Risk/Reward</span>
          <span class="sig-rr-val ${item.rr_ratio >= 2 ? 'good' : 'ok'}">${item.rr_ratio}:1</span>
        </div>
      </div>`;
  }

  /* ── Render one card ─────────────────────────────────────────────── */
  function cardHTML(item) {
    const sc = SIGNAL_COLORS[item.signal] || SIGNAL_COLORS['WATCH'];
    const retColor = n => Number(n) >= 0 ? '#22c55e' : '#f87171';

    return `
      <div class="sig-card" data-code="${esc(item.code)}" data-rank="${item.rank}">
        <div class="sig-card-head">
          <div class="sig-rank">#${item.rank}</div>
          <div class="sig-identity">
            <span class="sig-code">${esc(item.code)}</span>
            <span class="sig-name">${esc(item.name)}</span>
          </div>
          <div class="sig-badge" style="background:${sc.bg};color:${sc.text}">${esc(item.signal)}</div>
        </div>

        <div class="sig-price-row">
          <div class="sig-price-main">
            <span class="sig-price-label">Harga</span>
            <span class="sig-price-value">${fmtPrice(item.close)}</span>
          </div>
          <div class="sig-returns">
            <span style="color:${retColor(item.ret_1d)}">${fmtPct(item.ret_1d,2)} <span class="ret-label">1D</span></span>
            <span style="color:${retColor(item.ret_5d)}">${fmtPct(item.ret_5d,2)} <span class="ret-label">5D</span></span>
            <span style="color:${retColor(item.ret_20d)}">${fmtPct(item.ret_20d,2)} <span class="ret-label">20D</span></span>
          </div>
        </div>

        ${scoreBar(item.score)}

        <div class="sig-52w">
          <span title="Jarak dari 52-week high">📉 ${item.pct_from_52w_high != null ? Math.abs(item.pct_from_52w_high).toFixed(1) + '%' : '—'} dari 52w High</span>
          <span title="Jarak dari 52-week low">📈 ${item.pct_from_52w_low != null ? '+' + item.pct_from_52w_low.toFixed(1) + '%' : '—'} dari 52w Low</span>
        </div>

        ${tpSlBlock(item)}

        <div class="sig-chips">${indicatorChips(item)}</div>

        <div class="sig-reasons">
          <div class="sig-reasons-title">📋 Alasan Sinyal</div>
          <ul class="sig-reasons-list">
            ${(item.reasons || []).map(r => `<li>${esc(r)}</li>`).join('')}
          </ul>
        </div>
      </div>`;
  }

  /* ── Render all results ──────────────────────────────────────────── */
  function renderResults(data) {
    const grid = $('#signalsGrid');
    const meta = $('#signalsMeta');
    if (!grid) return;

    const hor = HORIZONS.find(h => h.id === data.horizon) || {};

    if (meta) {
      meta.innerHTML = `
        <div class="signals-meta-bar">
          <span class="smb-item">🔍 <b>${data.total_screened}</b> saham di-screen</span>
          <span class="smb-item smb-green">✅ <b>${data.total_signals}</b> sinyal ditemukan</span>
          <span class="smb-item">📅 Data per <b>${data.as_of}</b></span>
          <span class="smb-item smb-blue">${hor.icon || ''} <b>${esc(data.horizon_label)}</b> — ${esc(data.horizon_description || '')}</span>
        </div>`;
    }

    // Filter
    let items = [...(data.items || [])];
    if (state.filterSignal !== 'all') {
      items = items.filter(x => x.signal === state.filterSignal);
    }

    // Sort
    items.sort((a, b) => {
      const av = a[state.sortBy] ?? 0, bv = b[state.sortBy] ?? 0;
      return state.sortDir === 'asc' ? av - bv : bv - av;
    });

    if (items.length === 0) {
      grid.innerHTML = `
        <div class="signals-empty">
          <div style="font-size:3rem">🔍</div>
          <div>Tidak ada sinyal <b>${esc(data.horizon_label)}</b> yang memenuhi kriteria saat ini.</div>
          <div style="opacity:.6;font-size:13px;margin-top:8px">Coba horizon lain atau periksa kembali esok hari.</div>
        </div>`;
      return;
    }

    grid.innerHTML = items.map(cardHTML).join('');

    // Animate in
    grid.querySelectorAll('.sig-card').forEach((card, i) => {
      card.style.animationDelay = `${i * 40}ms`;
      card.classList.add('sig-card-animate');
    });
  }

  /* ── Build toolbar controls ──────────────────────────────────────── */
  function buildControls() {
    const wrap = $('#signalsControls');
    if (!wrap) return;

    wrap.innerHTML = `
      <div class="signals-controls-row">
        <div class="sig-sort-group">
          <label style="font-size:12px;opacity:.6">Urutkan:</label>
          <select id="sigSortBy" class="sig-select">
            <option value="score">Kekuatan Sinyal</option>
            <option value="rr_ratio">Risk/Reward</option>
            <option value="upside_pct">Potensi Naik %</option>
            <option value="ret_5d">Return 5 Hari</option>
            <option value="ret_20d">Return 20 Hari</option>
          </select>
        </div>
        <div class="sig-filter-group">
          <label style="font-size:12px;opacity:.6">Filter sinyal:</label>
          <select id="sigFilterSignal" class="sig-select">
            <option value="all">Semua</option>
            <option value="STRONG BUY">STRONG BUY</option>
            <option value="BUY">BUY</option>
            <option value="SPECULATIVE BUY">SPECULATIVE BUY</option>
            <option value="WATCH">WATCH</option>
          </select>
        </div>
        <button id="sigRefreshBtn" class="sig-refresh-btn" title="Refresh data">
          <span>↻</span> Refresh
        </button>
        <button id="sigExportBtn" class="sig-export-btn" title="Export CSV">
          ⬇ CSV
        </button>
      </div>`;

    $('#sigSortBy')?.addEventListener('change', e => {
      state.sortBy = e.target.value;
      if (state.data) renderResults(state.data);
    });
    $('#sigFilterSignal')?.addEventListener('change', e => {
      state.filterSignal = e.target.value;
      if (state.data) renderResults(state.data);
    });
    $('#sigRefreshBtn')?.addEventListener('click', () => {
      state.data = null;
      loadSignals(state.horizon);
    });
    $('#sigExportBtn')?.addEventListener('click', exportCsv);
  }

  /* ── Export CSV ──────────────────────────────────────────────────── */
  function exportCsv() {
    if (!state.data?.items?.length) return;
    const headers = ['Rank','Kode','Nama','Sinyal','Skor','Harga','TP','SL','R/R','Potensi%','Risiko%','RSI14','ADX14','Trend','Foreign Flow','Return 1D%','Return 5D%','Return 20D%'];
    const rows = state.data.items.map(x => [
      x.rank, x.code, x.name, x.signal, x.score, x.close, x.tp, x.sl, x.rr_ratio,
      x.upside_pct, x.downside_pct, x.rsi14, x.adx14, x.trend, x.foreign_flow,
      x.ret_1d, x.ret_5d, x.ret_20d,
    ]);
    const qs = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const csv = '\ufeff' + [headers, ...rows].map(r => r.map(qs).join(',')).join('\r\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `signals_${state.horizon}_${state.data.as_of}.csv`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  /* ── Init ─────────────────────────────────────────────────────── */
  let _initialized = false;

  function onActivate() {
    if (!_initialized) {
      _initialized = true;
      buildControls();

      // Horizon tab clicks — bind once DOM is visible
      $$('#signalsHorizonTabs .sig-horizon-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          $$('#signalsHorizonTabs .sig-horizon-btn').forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          state.data = null;
          loadSignals(btn.dataset.horizon);
        });
      });
    }

    // Load data if not already loaded
    if (!state.data && !state.loading) {
      loadSignals(state.horizon);
    }
  }

  function init() {
    // Listen for view activation
    document.addEventListener('zaiden:viewchange', e => {
      if (e.detail?.view === 'signals') onActivate();
    });

    // Auto-refresh every 5 minutes
    setInterval(() => {
      const view = document.getElementById('signalsView');
      if (view && !view.hidden && state.data && !state.loading) {
        state.data = null;
        loadSignals(state.horizon);
      }
    }, 5 * 60 * 1000);
  }

  init();
})();
