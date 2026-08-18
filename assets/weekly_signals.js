/**
 * IDX Weekly High-Confidence — Frontend JavaScript
 * Provides UI for signals, detail, validation, backtest, data quality, settings.
 */

// Mirrors ml_weekly.api_handlers.DECISION_STATUS_LABELS — keep in sync.
const DECISION_LABELS = {
  QUALIFIED: { label: 'Qualified', color: '#34d399' },
  WATCHLIST: { label: 'Watchlist', color: '#fbbf24' },
  NO_TRADE: { label: 'No Trade', color: '#f87171' },
  ABSTAIN: { label: 'Abstain', color: '#94a3b8' },
  DATA_INVALID: { label: 'Data Invalid', color: '#f87171' },
  MODEL_UNAVAILABLE: { label: 'Model Unavailable', color: '#f87171' },
  SIGNAL_EXPIRED: { label: 'Expired', color: '#94a3b8' },
  BELUM_DIEVALUASI: { label: 'Belum Dievaluasi', color: '#94a3b8' },
};

// ── State ──────────────────────────────────────────────────────────────────
const WeeklyState = {
  tab: 'signals',
  status: null,
  signals: null,
  signalDate: null,
  validation: null,
  backtest: null,
  dataQuality: null,
  settings: null,
  trainPolling: null,
  predictPolling: null,
  evaluationPolling: null,
  chart: null,
  evaluationChart: null,
  ihsgChart: null,
  settingsPolling: null,
};

// ── Initialize ─────────────────────────────────────────────────────────────
function initWeekly() {
  loadWeeklyStatus();
}

// ── Tab Navigation ─────────────────────────────────────────────────────────
function switchWeeklyTab(tab) {
  WeeklyState.tab = tab;
  document.querySelectorAll('.weekly-tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tab);
  });
  document.querySelectorAll('.weekly-tab-pane').forEach(pane => {
    pane.classList.toggle('active', pane.id === `weekly-tab-${tab}`);
  });

  switch (tab) {
    case 'signals': loadWeeklySignals(); break;
    case 'model-lab': loadWeeklyModelLab(); break;
    case 'validation': loadWeeklyValidation(); break;
    case 'backtest': loadWeeklyBacktest(); break;
    case 'data-quality': loadWeeklyDataQuality(); break;
    case 'evaluation': loadWeeklyEvaluation(); break;
    case 'ihsg': loadIhsgAnalysis(); break;
    case 'settings': loadWeeklySettings(); break;
  }
}

// ── Status Banner ──────────────────────────────────────────────────────────
async function loadWeeklyStatus(retryCount = 0) {
  try {
    const res = await fetch('/api/weekly/status');
    if (!res.ok) {
      const errText = await res.text().catch(() => '');
      let msg = '';
      try { msg = JSON.parse(errText).error || errText; } catch { msg = errText; }
      throw new Error(`HTTP ${res.status}: ${msg || 'Server error'}`);
    }
    const data = await res.json();
    WeeklyState.status = data;
    renderWeeklyStatusBanner(data);
    // Also kick off signal load
    loadWeeklySignals();
  } catch (e) {
    console.error('Status load failed:', e);
    if (retryCount < 2) {
      // Auto-retry up to 2 times with delay
      const delay = (retryCount + 1) * 1500;
      const el = document.getElementById('weekly-status-banner');
      if (el) el.innerHTML = `<div class="weekly-status-error">⏳ Menghubungkan ke server... (percobaan ${retryCount + 2}/3)</div>`;
      setTimeout(() => loadWeeklyStatus(retryCount + 1), delay);
    } else {
      renderWeeklyStatusBanner(null, e.message);
    }
  }
}


function renderWeeklyStatusBanner(status, errMsg = '') {
  const el = document.getElementById('weekly-status-banner');
  if (!el) return;

  if (!status) {
    el.innerHTML = `
      <div class="weekly-status-error">
        ❌ Gagal memuat status sistem.
        <br><small style="color:#64748b;font-size:10px">Pastikan server sudah di-restart setelah update kode. Tekan <strong>F5</strong> untuk reload, atau jalankan ulang BUKA-APLIKASI.bat.</small>
        <br><button onclick="loadWeeklyStatus()" style="margin-top:8px;padding:5px 14px;background:#1e293b;border:1px solid #334155;color:#94a3b8;border-radius:5px;cursor:pointer;font-size:11px">🔄 Coba Lagi</button>
      </div>`;
    return;
  }


  const holdoutClass = {
    'VERIFIED_ABOVE_90': 'status-verified',
    'PROVISIONAL_ABOVE_90': 'status-provisional',
    'NOT_VERIFIED': 'status-warning',
    'INSUFFICIENT_EVIDENCE': 'status-info',
    'NOT_EVALUATED': 'status-neutral',
    'ENSEMBLE_NOT_EVALUATED': 'status-warning',
    'NOT_TRAINED': 'status-neutral',
    'DEPENDENCY_UNAVAILABLE': 'status-warning',
    'QUALITY_GATE_FAILED': 'status-warning'
  }[status.holdout_status] || 'status-neutral';

  const holdoutIcon = {
    'VERIFIED_ABOVE_90': '✅',
    'PROVISIONAL_ABOVE_90': '🔶',
    'NOT_VERIFIED': '❌',
    'INSUFFICIENT_EVIDENCE': 'ℹ️',
    'NOT_EVALUATED': '⏳',
    'ENSEMBLE_NOT_EVALUATED': '⚠️',
    'NOT_TRAINED': '🔧',
    'DEPENDENCY_UNAVAILABLE': '🔌',
    'QUALITY_GATE_FAILED': '🚨'
  }[status.holdout_status] || '—';

  // Build ensemble badge
  const em = status.ensemble_members || [];
  const emLabels = {
    hist_gradient_boosting: 'HGB', random_forest: 'RF', logistic: 'LR',
    gradient_boosting: 'GB', extra_trees: 'ET', xgboost: 'XGB', lightgbm: 'LGBM',
  };
  const emBadges = em.map(m =>
    `<span style="background:#1e3a5f;border:1px solid #3b82f6;border-radius:4px;padding:2px 6px;font-size:10px;color:#93c5fd;margin-right:3px">${emLabels[m.model_name] || m.model_name}</span>`
  ).join('');
  const ensembleLabel = em.length >= 2
    ? `<span style="color:#34d399;font-size:11px">Ensemble (${em.length} model)</span>`
    : (em.length === 1 ? `<span style="color:#fbbf24;font-size:11px">1 Model</span>` : '');

  el.innerHTML = `
    <div class="weekly-status-grid">
      <div class="weekly-status-card" style="min-width:160px">
        <div class="wsc-label">Model</div>
        <div class="wsc-value ${status.model_active ? 'text-success' : 'text-warning'}">
          ${em.length > 0 ? ensembleLabel : 'Belum dilatih'}
        </div>
        ${emBadges ? `<div style="margin-top:4px">${emBadges}</div>` : ''}
      </div>
      <div class="weekly-status-card">
        <div class="wsc-label">Status Holdout</div>
        <div class="wsc-value ${holdoutClass}">
          ${holdoutIcon} ${status.holdout_status || 'N/A'}
        </div>
      </div>
      <div class="weekly-status-card">
        <div class="wsc-label">Threshold</div>
        <div class="wsc-value">${status.threshold ? (status.threshold * 100).toFixed(0) + '%' : '—'}</div>
      </div>
      <div class="weekly-status-card">
        <div class="wsc-label">Data Terkini</div>
        <div class="wsc-value ${status.data_fresh ? 'text-success' : 'text-warning'}">
          ${status.source_max_date || '—'}
          ${status.data_fresh ? '' : ` <span class="text-warning">(${status.data_freshness_days}d lama)</span>`}
        </div>
      </div>
      <div class="weekly-status-card">
        <div class="wsc-label">Sinyal HC Terbaru</div>
        <div class="wsc-value text-highlight">${status.today_hc_signals ?? '—'}</div>
      </div>
      <div class="weekly-status-card" style="min-width: 200px">
        <div class="wsc-label">Horizon Target</div>
        <div class="wsc-value" style="font-size:12px; white-space:normal; line-height:1.2; padding-top:4px">
          Prediksi untuk <b>${status.target_definition ? (status.target_definition.startsWith('close') ? status.target_definition.replace('close','').split('_')[0] + ' hari' : status.target_definition) : '5 hari'}</b> ke depan.
        </div>
      </div>
      <div class="weekly-status-card">
        <div class="wsc-label">Total Prediksi</div>
        <div class="wsc-value">${(status.total_predictions || 0).toLocaleString()}</div>
      </div>
    </div>
    ${status.train_status === 'running' ? `<div class="weekly-training-indicator">
      <span class="spinner"></span> Training sedang berjalan...
    </div>` : ''}
  `;

}

// ── Signals Tab ────────────────────────────────────────────────────────────
// Default view is QUALIFIED — decision_status computed server-side from
// validation status, model coverage, feature coverage, and net
// risk-reward (see predict.compute_decision_status). This can legitimately
// be empty: an empty QUALIFIED list is the honest outcome when nothing
// clears every gate this week, not a bug to work around. "Semua (Peringkat)"
// is kept as a separate, clearly-labeled ranked view for browsing
// candidates that didn't qualify.
async function loadWeeklySignals(filterStatus, limit) {
  filterStatus = filterStatus || WeeklyState.filterStatus || 'QUALIFIED';
  limit = limit || WeeklyState.filterLimit || 10;
  WeeklyState.filterStatus = filterStatus;
  WeeklyState.filterLimit = limit;

  const el = document.getElementById('weekly-signals-content');
  if (!el) return;
  el.innerHTML = '<div class="loading-spinner"><span class="spinner"></span> Memuat sinyal...</div>';

  try {
    const params = new URLSearchParams({ status: filterStatus, limit: String(limit) });
    if (WeeklyState.signalDate) params.set('date', WeeklyState.signalDate);

    const res = await fetch(`/api/weekly/signals?${params}`);
    const data = await res.json();
    WeeklyState.signals = data;

    if (data.error) {
      el.innerHTML = renderNoModelAlert(data.error);
      return;
    }

    renderSignalsTable(el, data);
  } catch (e) {
    el.innerHTML = `<div class="error-msg">Error: ${e.message}</div>`;
  }
}

function renderNoModelAlert(msg) {
  return `
    <div class="weekly-no-model">
      <div class="wnm-icon">🤖</div>
      <div class="wnm-title">Model Belum Tersedia</div>
      <div class="wnm-msg">${msg}</div>
      <button class="btn-primary" onclick="switchWeeklyTab('settings')">
        Ke Pengaturan & Training
      </button>
    </div>
  `;
}

function renderSignalsTable(el, data) {
  const { signals, prediction_date, total_signals, available_dates, horizon_days,
          validated_holdout_precision, validated_holdout_status } = data;
  const isTopView = WeeklyState.filterStatus === 'ALL' && WeeklyState.filterLimit <= 10;

  let targetDateText = "?";
  if (prediction_date && horizon_days) {
    const d = new Date(prediction_date);
    let added = 0;
    while (added < horizon_days) {
      d.setDate(d.getDate() + 1);
      if (d.getDay() !== 0 && d.getDay() !== 6) added++;
    }
    const tgt = d.toISOString().split('T')[0];
    targetDateText = `${prediction_date} s.d. ${tgt} (${horizon_days} hari bursa, ±1 minggu)`;
  }

  // Date selector
  const dateSelectorHtml = available_dates && available_dates.length > 0
    ? `<div class="weekly-date-bar">
        <label>Tanggal Prediksi:</label>
        <select id="weekly-date-select" onchange="onSignalDateChange(this.value)">
          ${available_dates.map(d => `<option value="${d}" ${d === prediction_date ? 'selected' : ''}>${d}</option>`).join('')}
        </select>
        <div class="signal-count-badge" style="margin-left:auto; background:var(--bg-card); color:var(--text-bright); border:1px solid var(--border-color); font-weight: bold; padding: 4px 8px;">Target: ${targetDateText}</div>
        <div class="signal-count-badge">${total_signals} ditampilkan</div>
      </div>`
    : `<div class="weekly-date-bar">
        <span class="text-muted">Tanggal: ${prediction_date || '?'}</span>
        <div class="signal-count-badge" style="margin-left:auto; background:var(--bg-card); color:var(--text-bright); border:1px solid var(--border-color); font-weight: bold; padding: 4px 8px;">Target: ${targetDateText}</div>
        <div class="signal-count-badge">${total_signals || 0} ditampilkan</div>
      </div>`;

  const dateClarifyHtml = prediction_date ? `<div class="text-muted" style="font-size:11px;margin:-4px 0 8px 2px">
    "Tanggal Prediksi" = tanggal harga <strong>penutupan</strong> yang dipakai model (bukan tanggal beli). Entry disarankan di <strong>pembukaan hari bursa berikutnya</strong> setelah ${prediction_date}.
  </div>` : '';

  // Filter buttons — QUALIFIED (decision_status computed server-side) is
  // the default/primary view and can legitimately be empty. "Semua
  // (Peringkat)" is a separate, explicitly-labeled ranked browsing view —
  // it must never be presented as if every row qualified.
  const isActive = (status, lim) => WeeklyState.filterStatus === status && WeeklyState.filterLimit === lim;
  const filterHtml = `
    <div class="weekly-filter-bar">
      <button class="wfb-btn ${isActive('QUALIFIED', 10) ? 'active' : ''}" onclick="loadWeeklySignals('QUALIFIED', 10); setWFBActive(this)">
        ✅ Qualified
      </button>
      <button class="wfb-btn ${isActive('DECISION_WATCHLIST', 20) ? 'active' : ''}" onclick="loadWeeklySignals('DECISION_WATCHLIST', 20); setWFBActive(this)">
        👁️ Watchlist
      </button>
      <button class="wfb-btn ${isActive('ALL', 10) ? 'active' : ''}" onclick="loadWeeklySignals('ALL', 10); setWFBActive(this)">
        🏆 Peringkat 10 Teratas
      </button>
      <button class="wfb-btn ${isActive('ALL', 50) ? 'active' : ''}" onclick="loadWeeklySignals('ALL', 50); setWFBActive(this)">
        📋 Semua (Peringkat, belum tentu lolos)
      </button>
      <button class="wfb-refresh" onclick="loadWeeklySignals()" title="Refresh">
        🔄 Refresh
      </button>
      <button class="wfb-refresh" id="weekly-pdf-btn" style="background:#1e3a5f;border-color:#3b82f6;color:#93c5fd;margin-left:auto"
        onclick="downloadDigestPdf(this)" title="Unduh laporan PDF berisi daftar saham yang sedang ditampilkan, lengkap dengan grafik dan analisis per saham"
        ${(signals || []).length === 0 ? 'disabled' : ''}>
        📥 Unduh PDF (${(signals || []).length} saham)
      </button>
    </div>
  `;

  // Honest accuracy context — shown once, above the list, instead of
  // implying every row meets the 90% precision target.
  const precPct = validated_holdout_precision !== null && validated_holdout_precision !== undefined
    ? `${(validated_holdout_precision * 100).toFixed(0)}%` : null;
  const accuracyNote = precPct ? `
    <div class="weekly-disclaimer" style="border-color:${validated_holdout_precision >= 0.65 ? '#10b981' : '#f59e0b'}">
      📐 <strong>Akurasi tervalidasi (holdout, belum pernah dipakai untuk melatih model):</strong> ${precPct}
      ${validated_holdout_status ? ` — status <strong>${validated_holdout_status}</strong>` : ''}.
      Ranking di bawah tetap berguna untuk membandingkan saham, tapi anggap ini <em>salah satu input</em>, bukan jaminan profit.
    </div>` : `
    <div class="weekly-disclaimer">
      ⚠️ <strong>Disclaimer:</strong> Sinyal ini adalah output model statistik, bukan rekomendasi investasi. Selalu lakukan analisis mandiri.
    </div>`;

  if (!signals || signals.length === 0) {
    const isQualifiedView = WeeklyState.filterStatus === 'QUALIFIED' || WeeklyState.filterStatus === 'DECISION_WATCHLIST';
    el.innerHTML = dateSelectorHtml + filterHtml + `
      <div class="weekly-empty-state">
        <div class="wes-icon">${isQualifiedView ? '⚖️' : '📭'}</div>
        <div class="wes-msg">${isQualifiedView
          ? 'Tidak ada sinyal yang memenuhi standar risiko dan validasi minggu ini.'
          : 'Belum ada prediksi untuk tanggal ini.'}</div>
        <div class="wes-sub">${isQualifiedView
          ? 'Ini hasil yang jujur, bukan kegagalan sistem — coba tab "Semua (Peringkat)" untuk melihat kandidat yang belum lolos syarat penuh.'
          : 'Jalankan "Perbarui Sinyal" dari tab Training &amp; Jobs, atau pilih tanggal lain.'}</div>
      </div>`;
    return;
  }

  const rows = signals.map(s => {
    const tier = s.confidence_tier || { label: '—', color: '#94a3b8' };
    const decision = DECISION_LABELS[s.decision_status] || { label: s.decision_status || 'Belum Dievaluasi', color: '#94a3b8' };
    const outcomes = { HIT: '✅ Hit', MISS: '❌ Miss', PENDING: '⏳ Pending' };
    const outClass = { HIT: 'text-success', MISS: 'text-danger', PENDING: 'text-muted' };

    const flags = s.risk_flags || [];
    const flagHtml = flags.length > 0
      ? `<span class="risk-flag-badge" title="${flags.join('\n')}">⚠️ ${flags.length}</span>`
      : '';

    return `
      <tr class="signals-row" onclick="loadSignalDetail('${s.ticker}', '${s.prediction_date}')">
        <td class="ticker-cell">
          <div style="display:flex;align-items:center;gap:6px">
            ${isTopView ? `<span style="flex:0 0 auto;width:20px;height:20px;border-radius:50%;background:#1e293b;border:1px solid #334155;display:flex;align-items:center;justify-content:center;font-size:10px;color:#94a3b8;font-weight:700">${s.rank}</span>` : ''}
            <div>
              <div class="ticker-name">${s.ticker}</div>
              <div class="company-name">${s.company_name || ''}</div>
            </div>
          </div>
          <div style="display:flex;gap:4px;align-items:center;flex-wrap:wrap">
            <span class="board-badge board-${(s.board || '').toLowerCase()}">${s.board || ''}</span>
            <span title="${s.decision_reason || ''}" style="font-size:9px;padding:1px 6px;border-radius:8px;background:${decision.color}22;border:1px solid ${decision.color};color:${decision.color};font-weight:700">${decision.label}</span>
          </div>
        </td>
        <td class="prob-cell">
          <div style="display:flex;align-items:center;gap:6px">
            <div class="prob-value">${s.probability_pct}</div>
            <span style="font-size:9px;padding:1px 6px;border-radius:8px;border:1px solid ${tier.color};color:${tier.color}">${tier.label}</span>
          </div>
          <div class="prob-bar">
            <div class="prob-fill" style="width:${s.probability * 100}%;background:${tier.color}"></div>
          </div>
        </td>
        <td class="price-cell">
          <div class="price-current">${fmtPrice(s.current_close)}</div>
          <div class="price-sub">Entry: ${fmtPrice(s.next_open)}</div>
        </td>
        <td class="tp-sl-cell">
          <div class="tp-value text-success">TP: ${fmtPrice(s.target_price)}</div>
          <div class="sl-value text-danger">SL: ${fmtPrice(s.stop_price)}</div>
        </td>
        <td class="regime-cell">
          <div class="market-regime regime-${(s.market_regime||'').toLowerCase()}">${s.market_regime || '—'}</div>
          <div class="sector-name">${(s.fundamental && s.fundamental.sektor) || s.sector || '—'}</div>
          ${s.fundamental && (s.fundamental.per !== null || s.fundamental.pbv !== null) ? `
          <div class="sector-name" style="opacity:0.75; font-size:10px;" title="Dari Zaiden Fundamental Review">
            PER ${s.fundamental.per ?? '—'} · PBV ${s.fundamental.pbv ?? '—'} · ROE ${s.fundamental.roe ?? '—'}%
          </div>` : ''}
        </td>
        <td class="reason-cell">
          ${(s.reason_codes || []).slice(0, 2).map(r => `<div class="reason-badge">✓ ${r}</div>`).join('')}
          ${flagHtml}
        </td>
        <td class="outcome-cell ${outClass[s.outcome_status] || ''}">
          ${outcomes[s.outcome_status] || s.outcome_status}
          ${s.realized_return !== null ? `<div class="realized-ret ${s.realized_return >= 0 ? 'text-success' : 'text-danger'}">${(s.realized_return * 100).toFixed(1)}%</div>` : ''}
        </td>
      </tr>
    `;
  }).join('');

  el.innerHTML = `
    ${dateSelectorHtml}
    ${dateClarifyHtml}
    ${filterHtml}
    ${accuracyNote}
    <div class="signals-table-wrap">
      <table class="signals-table">
        <thead>
          <tr>
            <th>Saham</th>
            <th>Probabilitas</th>
            <th>Harga</th>
            <th>TP / SL</th>
            <th>Regime</th>
            <th>Alasan</th>
            <th>Outcome</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="signals-footer">
      Klik baris untuk detail saham. Data per ${prediction_date}.
    </div>
  `;

  // Init detail panel if needed
  const detail = document.getElementById('weekly-signal-detail');
  if (detail) detail.style.display = 'none';
}

function setWFBActive(btn) {
  document.querySelectorAll('.wfb-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}

function onSignalDateChange(d) {
  WeeklyState.signalDate = d;
  loadWeeklySignals();
}

// ── PDF export ─────────────────────────────────────────────────────────────
async function _fetchPdfAndDownload(url, filename, btn, busyText) {
  const originalHtml = btn ? btn.innerHTML : null;
  if (btn) { btn.disabled = true; btn.innerHTML = `⏳ ${busyText}`; }
  try {
    const res = await fetch(url);
    if (!res.ok) {
      let msg = `HTTP ${res.status}`;
      try { msg = (await res.json()).error || msg; } catch {}
      throw new Error(msg);
    }
    const blob = await res.blob();

    // Guard against a truncated transfer (e.g. server restarted mid-download)
    // slipping through as a "successful" fetch — a cut-off response can still
    // resolve here with res.ok=true but a body that's shorter than promised
    // or missing its PDF trailer, which browsers then report as "file
    // damaged" with no indication of why. Verify the size matches what the
    // server declared, and that it actually starts with the PDF magic bytes,
    // BEFORE handing the user a file that looks downloaded but won't open.
    const declaredLength = Number(res.headers.get('Content-Length'));
    if (declaredLength && blob.size !== declaredLength) {
      throw new Error(
        `Unduhan terputus (${blob.size} dari ${declaredLength} byte diterima) — kemungkinan koneksi ke server sempat terputus. Coba unduh ulang.`
      );
    }
    const head = new Uint8Array(await blob.slice(0, 5).arrayBuffer());
    const headText = String.fromCharCode(...head);
    if (headText !== '%PDF-') {
      throw new Error('File yang diterima bukan PDF valid (kemungkinan respons error tersembunyi). Coba unduh ulang.');
    }

    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = blobUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(blobUrl), 5000);
  } catch (e) {
    alert('Gagal membuat PDF: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = originalHtml; }
  }
}

function downloadDigestPdf(btn) {
  const status = WeeklyState.filterStatus || 'ALL';
  const limit = WeeklyState.filterLimit || 10;
  const params = new URLSearchParams({ status, limit: String(limit) });
  if (WeeklyState.signalDate) params.set('date', WeeklyState.signalDate);
  const dateLabel = WeeklyState.signalDate || (WeeklyState.signals && WeeklyState.signals.prediction_date) || 'terbaru';
  _fetchPdfAndDownload(
    `/api/weekly/signals/pdf?${params}`,
    `Laporan_Sinyal_Mingguan_IDX_${dateLabel}.pdf`,
    btn,
    'Membuat PDF (bisa 10-30 detik)...'
  );
}

function downloadSignalPdf(ticker, predDate, btn) {
  const params = new URLSearchParams();
  if (predDate) params.set('date', predDate);
  _fetchPdfAndDownload(
    `/api/weekly/signals/${ticker}/pdf?${params}`,
    `Sinyal_${ticker}_${predDate || 'terbaru'}.pdf`,
    btn,
    'Membuat PDF...'
  );
}

// ── Signal Detail Panel ────────────────────────────────────────────────────
async function loadSignalDetail(ticker, predDate) {
  const panel = document.getElementById('weekly-signal-detail');
  if (!panel) return;
  panel.style.display = 'block';
  panel.innerHTML = `<div class="loading-spinner"><span class="spinner"></span> Memuat detail ${ticker}...</div>`;

  try {
    const params = new URLSearchParams({ date: predDate });
    const res = await fetch(`/api/weekly/signals/${ticker}?${params}`);
    const data = await res.json();
    if (data.error) {
      panel.innerHTML = `<div class="error-msg">${data.error}</div>`;
      return;
    }
    renderSignalDetail(panel, data);
  } catch (e) {
    panel.innerHTML = `<div class="error-msg">Error: ${e.message}</div>`;
  }
}

function renderSignalDetail(panel, d) {
  const reasons = Array.isArray(d.reason_codes_json) ? d.reason_codes_json : (d.reason_codes_json || []);
  const flags = Array.isArray(d.risk_flags_json) ? d.risk_flags_json : (d.risk_flags_json || []);

  // Parse ensemble breakdown
  let ensembleProbs = {};
  try {
    ensembleProbs = d.ensemble_probs_json
      ? (typeof d.ensemble_probs_json === 'string' ? JSON.parse(d.ensemble_probs_json) : d.ensemble_probs_json)
      : {};
  } catch(e) { ensembleProbs = {}; }

  const emLabels = { hist_gradient_boosting: 'HistGB', random_forest: 'Random Forest', logistic: 'Logistic' };
  const ensembleEntries = Object.entries(ensembleProbs);
  const ensembleHtml = ensembleEntries.length > 0 ? `
    <div class="detail-section">
      <div class="ds-title">🤖 Ensemble Breakdown</div>
      <div style="font-size:11px;color:#94a3b8;margin-bottom:6px">Probabilitas dari masing-masing model:</div>
      ${ensembleEntries.map(([model, prob]) => {
        const pct = (prob * 100).toFixed(1);
        const color = prob >= 0.90 ? '#34d399' : prob >= 0.80 ? '#fbbf24' : '#f87171';
        return `<div class="ds-row" style="margin-bottom:4px">
          <span style="color:#cbd5e1">${emLabels[model] || model}</span>
          <div style="display:flex;align-items:center;gap:6px">
            <div style="width:80px;height:6px;background:#1e293b;border-radius:3px;overflow:hidden">
              <div style="width:${pct}%;height:100%;background:${color};border-radius:3px"></div>
            </div>
            <strong style="color:${color}">${pct}%</strong>
          </div>
        </div>`;
      }).join('')}
      <div class="ds-row" style="border-top:1px solid #1e3a5f;margin-top:6px;padding-top:6px">
        <span style="color:#e2e8f0;font-weight:600">Rata-rata Ensemble</span>
        <strong style="color:${d.calibrated_probability >= 0.90 ? '#34d399' : '#fbbf24'}">${(d.calibrated_probability * 100).toFixed(1)}%</strong>
      </div>
    </div>
  ` : '';

  const histRows = (d.history || []).map(h => `
    <tr>
      <td>${h.date}</td>
      <td>${(h.probability * 100).toFixed(1)}%</td>
      <td class="${h.signal_status === 'HIGH_CONFIDENCE' ? 'text-success' : 'text-muted'}">${h.signal_status}</td>
      <td class="${h.outcome === 'HIT' ? 'text-success' : h.outcome === 'MISS' ? 'text-danger' : 'text-muted'}">${h.outcome || '—'}</td>
      <td class="${(h.realized_return || 0) >= 0 ? 'text-success' : 'text-danger'}">${h.realized_return !== null ? (h.realized_return * 100).toFixed(1) + '%' : '—'}</td>
    </tr>
  `).join('');

  panel.innerHTML = `
    <div class="detail-header">
      <div class="detail-ticker">${d.ticker}</div>
      <div class="detail-prob ${d.calibrated_probability >= 0.90 ? 'prob-high' : 'prob-med'}">
        ${(d.calibrated_probability * 100).toFixed(1)}%
      </div>
      <button class="wfb-refresh" style="background:#1e3a5f;border-color:#3b82f6;color:#93c5fd"
        onclick="downloadSignalPdf('${d.ticker}', '${d.prediction_date}', this)" title="Unduh laporan PDF saham ini">
        📥 PDF
      </button>
      <button class="detail-close" onclick="document.getElementById('weekly-signal-detail').style.display='none'">✕</button>
    </div>
    <div class="detail-grid">
      <div class="detail-section">
        <div class="ds-title">Harga & Target</div>
        <div class="ds-row"><span>Harga Saat Ini</span><strong>${fmtPrice(d.current_close)}</strong></div>
        <div class="ds-row"><span>Entry (open +1)</span><strong>${fmtPrice(d.next_open)}</strong></div>
        <div class="ds-row"><span>Target Price (TP)</span><strong class="text-success">${fmtPrice(d.target_price)}</strong></div>
        <div class="ds-row"><span>Stop Price (SL)</span><strong class="text-danger">${fmtPrice(d.stop_price)}</strong></div>
        <div class="ds-row"><span>Market Regime</span><strong class="regime-${(d.market_regime||'').toLowerCase()}">${d.market_regime || '—'}</strong></div>
      </div>
      ${ensembleHtml}
      <div class="detail-section">
        <div class="ds-title">Alasan Sinyal</div>
        ${reasons.map(r => `<div class="reason-item">✓ ${r}</div>`).join('') || '<div class="text-muted">Tidak ada</div>'}
        ${flags.length > 0 ? `
          <div class="ds-title mt-2">⚠️ Risiko</div>
          ${flags.map(f => `<div class="flag-item">⚠ ${f}</div>`).join('')}
        ` : ''}
      </div>
    </div>
    ${d.history && d.history.length > 0 ? `
      <div class="detail-section mt-2">
        <div class="ds-title">Riwayat Prediksi ${d.ticker}</div>
        <table class="mini-table">
          <thead><tr><th>Tanggal</th><th>Prob</th><th>Status</th><th>Outcome</th><th>Return</th></tr></thead>
          <tbody>${histRows}</tbody>
        </table>
      </div>
    ` : ''}
    <div class="detail-disclaimer">
      ℹ️ Probabilitas dihitung oleh model ML. Bukan rekomendasi beli/jual.
    </div>
  `;
}

// ── Model Lab Tab ────────────────────────────────────────────────────────────
async function loadWeeklyModelLab() {
  const el = document.getElementById('weekly-model-lab-content');
  if (!el) return;
  el.innerHTML = '<div class="loading-spinner"><span class="spinner"></span> Memuat data lab...</div>';

  try {
    const res = await fetch('/api/weekly/status');
    const status = await res.json();
    
    // For now we use the status endpoint to show ensemble info.
    // In the future this can fetch a dedicated /api/weekly/model-lab endpoint
    renderModelLab(el, status);
  } catch (e) {
    el.innerHTML = `<div class="error-msg">Error: ${e.message}</div>`;
  }
}

function renderModelLab(el, status) {
  if (!status || !status.model_active) {
    el.innerHTML = renderNoModelAlert('Model belum ditraining. Silakan jalankan training terlebih dahulu.');
    return;
  }

  const em = status.ensemble_members || [];
  const emLabels = { hist_gradient_boosting: 'HistGradientBoosting', random_forest: 'Random Forest', logistic: 'Logistic Regression' };
  
  const memberHtml = em.map(m => `
    <div class="metric-card">
      <div class="mc-title">🧠 ${emLabels[m.model_name] || m.model_name}</div>
      <div class="mc-grid">
        <div class="mc-item">
          <div class="mc-label">Model Type</div>
          <div class="mc-value" style="font-size:12px">${m.model_name}</div>
        </div>
        <div class="mc-item">
          <div class="mc-label">Status</div>
          <div class="mc-value text-success">Active</div>
        </div>
      </div>
    </div>
  `).join('');

  el.innerHTML = `
    <div class="validation-header">
      <div class="vh-title">Model Lab & Ensemble</div>
      <div class="vh-subtitle">Detail arsitektur model dan bobot (PR-AUC) dari ensemble aktif.</div>
    </div>
    
    <div class="validation-integrity-box">
      <div class="vib-title">🎯 Ensemble Architecture</div>
      <div class="vib-items">
        <div class="vib-item">🔹 Sistem menggunakan soft-voting ensemble berbasis probabilitas.</div>
        <div class="vib-item">🔹 Bobot masing-masing model ditentukan oleh metrik PR-AUC pada validasi out-of-sample.</div>
        <div class="vib-item">🔹 Kalibrasi Isotonic Regression diaplikasikan pada level ensemble untuk probabilitas absolut.</div>
      </div>
    </div>

    <div class="metrics-grid">
      ${memberHtml}
    </div>
    
    <div class="model-history">
      <div class="mh-title">Feature Importance (Top 10)</div>
      <div class="text-muted" style="font-size:12px; margin-top:8px; padding: 12px; background: rgba(255,255,255,0.02); border: 1px dashed #2a3050; border-radius: 8px; text-align: center;">
        <span style="font-size:24px; display:block; margin-bottom:8px;">📊</span>
        Feature Importance dan SHAP values sedang dalam proses kalkulasi. Akan tersedia pada update berikutnya.
      </div>
    </div>
  `;
}

// ── Walk-Forward Tab (Validation) ──────────────────────────────────────────
async function loadWeeklyValidation() {
  const el = document.getElementById('weekly-validation-content');
  if (!el) return;
  el.innerHTML = '<div class="loading-spinner"><span class="spinner"></span> Memuat validasi...</div>';

  try {
    const res = await fetch('/api/weekly/validation');
    const data = await res.json();
    WeeklyState.validation = data;
    renderValidation(el, data);
  } catch (e) {
    el.innerHTML = `<div class="error-msg">Error: ${e.message}</div>`;
  }
}

function renderValidation(el, data) {
  if (data.error) {
    el.innerHTML = renderNoModelAlert(data.error);
    return;
  }

  const metrics = data.metrics || {};
  const val = metrics['validation'] || {};
  const holdout = metrics['holdout'] || {};
  const ensVal = metrics['ensemble_validation'] || {};
  const ensHoldout = metrics['ensemble_holdout'] || {};
  const hasEnsembleEval = !!(ensVal.signal_count || ensHoldout.signal_count);

  const renderMetricCard = (title, m, isHoldout = false) => {
    if (!m || !m.signal_count) return `<div class="metric-card"><div class="mc-title">${title}</div><div class="mc-empty">Tidak ada data</div></div>`;

    const precClass = (m.precision >= 0.90) ? 'text-success' : (m.precision >= 0.80 ? 'text-warning' : 'text-danger');
    const statusIcon = isHoldout
      ? (m.holdout_status === 'VERIFIED_ABOVE_90' ? '✅' : m.holdout_status === 'NOT_VERIFIED' ? '❌' : '🔶')
      : '';

    return `
      <div class="metric-card ${isHoldout ? 'metric-holdout' : ''}">
        <div class="mc-title">${statusIcon} ${title}</div>
        <div class="mc-grid">
          <div class="mc-item">
            <div class="mc-label">Precision</div>
            <div class="mc-value ${precClass}">${fmtPct(m.precision)}</div>
            <div class="mc-ci">CI: [${fmtPct(m.precision_ci_lower)} – ${fmtPct(m.precision_ci_upper)}]</div>
          </div>
          <div class="mc-item">
            <div class="mc-label">Coverage</div>
            <div class="mc-value">${fmtPct(m.coverage)}</div>
          </div>
          <div class="mc-item">
            <div class="mc-label">Recall</div>
            <div class="mc-value">${fmtPct(m.recall)}</div>
          </div>
          <div class="mc-item">
            <div class="mc-label">Sinyal</div>
            <div class="mc-value">${m.signal_count}</div>
          </div>
          <div class="mc-item">
            <div class="mc-label">Brier Score</div>
            <div class="mc-value">${m.brier_score !== null ? m.brier_score?.toFixed(3) : '—'}</div>
          </div>
          <div class="mc-item">
            <div class="mc-label">PR-AUC</div>
            <div class="mc-value">${m.pr_auc !== null ? m.pr_auc?.toFixed(3) : '—'}</div>
          </div>
        </div>
        <div class="mc-period">Periode: ${m.period_start || '—'} – ${m.period_end || '—'}</div>
        <div class="mc-threshold">Threshold: ${m.threshold != null ? (m.threshold * 100).toFixed(0) + '%' : '—'}</div>
      </div>
    `;
  };

  el.innerHTML = `
    <div class="validation-header">
      <div class="vh-title">Validasi Model</div>
      <div class="vh-subtitle">Semua metrik dihitung SETELAH threshold dipilih.</div>
    </div>

    <div class="validation-integrity-box">
      <div class="vib-title">🔒 Integritas Evaluasi</div>
      <div class="vib-items">
        <div class="vib-item">✅ Threshold dipilih pada validation set (bukan holdout)</div>
        <div class="vib-item">✅ Holdout period tidak pernah digunakan untuk tuning</div>
        <div class="vib-item">✅ Walk-forward validation mencegah temporal leakage</div>
        <div class="vib-item">✅ Wilson Confidence Interval dihitung untuk setiap precision</div>
        <div class="vib-item">✅ Dataset built with point-in-time universe (no survivorship bias)</div>
      </div>
    </div>

    ${hasEnsembleEval ? `
    <div class="validation-integrity-box" style="border-color:#3b82f6">
      <div class="vib-title">🤖 Ensemble Gabungan (yang benar-benar memfilter sinyal live)</div>
      <div class="vib-items">
        <div class="vib-item">Metrik di bawah ini adalah hasil kombinasi SEMUA model ensemble (bukan satu model saja) — inilah metode nyata yang dipakai tab Sinyal.</div>
      </div>
    </div>
    <div class="metrics-grid">
      ${renderMetricCard('Ensemble — Validation', ensVal)}
      ${renderMetricCard('Ensemble — Holdout (Untouched)', ensHoldout, true)}
    </div>
    ` : `
    <div class="validation-integrity-box" style="border-color:#f59e0b">
      <div class="vib-title">⚠️ Ensemble gabungan belum pernah dievaluasi</div>
      <div class="vib-items">
        <div class="vib-item">Metrik di bawah ini adalah milik masing-masing model SATU PERSATU. Sinyal live dihasilkan dari KOMBINASI semua model — kombinasi itu sendiri belum pernah di-backtest. Jalankan evaluasi ensemble dari tab Training & Jobs.</div>
      </div>
    </div>
    `}

    <div class="metrics-grid">
      ${renderMetricCard('Validation Set (model terbaru saja)', val)}
      ${renderMetricCard('Holdout Set (model terbaru saja)', holdout, true)}
    </div>

    <div class="target-line">
      <div class="tl-target">Target Precision: <strong>≥ 90%</strong></div>
      <div class="tl-note">Precision < 90% pada holdout → Status = NOT_VERIFIED. Model tetap menampilkan sinyal, namun harus dievaluasi ulang.</div>
    </div>

    <div class="model-history">
      <div class="mh-title">Riwayat Model Runs</div>
      <table class="mini-table">
        <thead><tr><th>ID</th><th>Versi</th><th>Model</th><th>Threshold</th><th>Val Status</th><th>Holdout Status</th><th>Dibuat</th></tr></thead>
        <tbody>
          ${(data.model_runs || []).map(r => `
            <tr>
              <td>${r.id}</td>
              <td>${r.version}</td>
              <td>${r.name}</td>
              <td>${(r.threshold * 100).toFixed(0)}%</td>
              <td class="${r.validation_status === 'VERIFIED' ? 'text-success' : 'text-warning'}">${r.validation_status}</td>
              <td class="${r.holdout_status === 'VERIFIED_ABOVE_90' ? 'text-success' : r.holdout_status === 'NOT_VERIFIED' ? 'text-danger' : 'text-warning'}">${r.holdout_status}</td>
              <td>${r.created_at ? r.created_at.split('T')[0] : '—'}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
}

// ── Backtest Tab ───────────────────────────────────────────────────────────
async function loadWeeklyBacktest() {
  const el = document.getElementById('weekly-backtest-content');
  if (!el) return;
  el.innerHTML = '<div class="loading-spinner"><span class="spinner"></span> Memuat backtest...</div>';

  try {
    const res = await fetch('/api/weekly/backtest');
    const data = await res.json();
    WeeklyState.backtest = data;
    renderBacktest(el, data);
  } catch (e) {
    el.innerHTML = `<div class="error-msg">Error: ${e.message}</div>`;
  }
}

function renderBacktest(el, data) {
  if (data.error) {
    el.innerHTML = renderNoModelAlert(data.error);
    return;
  }

  const metrics = data.metrics || [];
  const equity = data.equity_curve || [];

  // Metrics table
  const metricRows = metrics.map(m => `
    <tr>
      <td>${m.split}</td>
      <td>${m.period_start} — ${m.period_end}</td>
      <td>${m.signal_count}</td>
      <td class="${(m.precision || 0) >= 0.90 ? 'text-success' : 'text-danger'}">${fmtPct(m.precision)}</td>
      <td>[${fmtPct(m.precision_ci_lower)} – ${fmtPct(m.precision_ci_upper)}]</td>
      <td>${fmtPct(m.coverage)}</td>
      <td class="${(m.net_return || 0) >= 0 ? 'text-success' : 'text-danger'}">${m.net_return !== null ? fmtPct(m.net_return) : '—'}</td>
      <td>${m.win_rate !== null ? fmtPct(m.win_rate) : '—'}</td>
      <td>${m.profit_factor !== null ? m.profit_factor?.toFixed(2) : '—'}</td>
    </tr>
  `).join('');

  // Equity chart placeholder
  const chartHtml = equity.length > 0 ? `
    <div class="equity-chart-wrap">
      <canvas id="equity-curve-chart" height="200"></canvas>
    </div>
  ` : `<div class="text-muted mt-2">Belum ada data equity curve (prediksi belum matured).</div>`;

  el.innerHTML = `
    <div class="backtest-header">
      <div class="bt-title">Backtest Konservatif</div>
      <div class="bt-subtitle">Entry: open[t+1] | Exit: close[t+5] | Termasuk biaya transaksi 0.41%</div>
    </div>

    <div class="bt-cost-notice">
      <strong>Asumsi Biaya:</strong> Beli 0.155% + Jual 0.255% + Slippage 0.2% = total 0.61% per round-trip
    </div>

    <div class="backtest-table-wrap">
      <table class="signals-table">
        <thead>
          <tr>
            <th>Split</th><th>Periode</th><th>Sinyal</th>
            <th>Precision</th><th>95% CI</th><th>Coverage</th>
            <th>Net Return</th><th>Win Rate</th><th>Profit Factor</th>
          </tr>
        </thead>
        <tbody>${metricRows}</tbody>
      </table>
    </div>

    ${chartHtml}

    <div class="bt-disclaimer">
      ⚠️ <strong>Backtest Disclaimer:</strong> Backtest tidak mencerminkan hasil nyata di masa depan.
      Slippage aktual bisa lebih tinggi terutama untuk saham tidak likuid.
      Tidak ada market impact modeling. Entry diasumsikan selalu terpenuhi.
    </div>
  `;

  // Render chart if data available
  if (equity.length > 0) {
    setTimeout(() => renderEquityChart(equity), 100);
  }
}

function renderEquityChart(equity) {
  const canvas = document.getElementById('equity-curve-chart');
  if (!canvas || typeof Chart === 'undefined') return;

  const labels = equity.map(e => e.date);
  const values = equity.map(e => (e.cumulative - 1) * 100);
  const colors = equity.map(e => e.outcome === 'HIT' ? 'rgba(16,185,129,0.8)' : 'rgba(239,68,68,0.8)');

  if (WeeklyState.chart) WeeklyState.chart.destroy();

  WeeklyState.chart = new Chart(canvas, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Cumulative Return (%)',
        data: values,
        borderColor: '#10b981',
        backgroundColor: 'rgba(16,185,129,0.08)',
        borderWidth: 2,
        pointRadius: 3,
        pointBackgroundColor: colors,
        fill: true,
        tension: 0.2,
      }]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => `Return: ${ctx.raw.toFixed(2)}%`,
          }
        }
      },
      scales: {
        x: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#94a3b8', maxTicksLimit: 12 } },
        y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#94a3b8', callback: v => v.toFixed(1) + '%' } }
      }
    }
  });
}

// ── Data Quality Tab ───────────────────────────────────────────────────────
async function loadWeeklyDataQuality() {
  const el = document.getElementById('weekly-data-quality-content');
  if (!el) return;
  el.innerHTML = '<div class="loading-spinner"><span class="spinner"></span> Memuat data quality...</div>';

  try {
    const res = await fetch('/api/weekly/data-quality');
    const data = await res.json();
    WeeklyState.dataQuality = data;
    renderDataQuality(el, data);
  } catch (e) {
    el.innerHTML = `<div class="error-msg">Error: ${e.message}</div>`;
  }
}

function renderDataQuality(el, data) {
  const main = data.main_table || {};
  const byYear = data.by_year || [];
  const broker = data.broker_table || {};
  const own = data.ownership_table || {};
  const limits = data.limitations || [];
  const jobs = data.recent_jobs || [];

  const yearRows = byYear.map(y => `
    <tr>
      <td>${y.year}</td>
      <td>${y.rows.toLocaleString()}</td>
      <td>${y.stocks}</td>
      <td>${y.trading_days}</td>
    </tr>
  `).join('');

  const jobRows = jobs.map(j => `
    <tr>
      <td>${j.job}</td>
      <td>${j.started ? j.started.split('T')[0] : '—'}</td>
      <td class="${j.status === 'success' ? 'text-success' : j.status === 'failed' ? 'text-danger' : 'text-muted'}">${j.status}</td>
      <td class="text-muted small">${j.error || ''}</td>
    </tr>
  `).join('');

  el.innerHTML = `
    <div class="dq-header">
      <div class="dq-title">Kualitas Data — Ringkasan</div>
    </div>

    <div class="dq-stat-grid">
      <div class="dq-stat-card">
        <div class="dsc-label">Total Baris OHLCV</div>
        <div class="dsc-value">${(main.rows || 0).toLocaleString()}</div>
      </div>
      <div class="dq-stat-card">
        <div class="dsc-label">Saham Unik</div>
        <div class="dsc-value">${main.stocks || 0}</div>
      </div>
      <div class="dq-stat-card">
        <div class="dsc-label">Rentang Tanggal</div>
        <div class="dsc-value">${main.min_date || '—'} → ${main.max_date || '—'}</div>
      </div>
      <div class="dq-stat-card">
        <div class="dsc-label">Saham Aktif 60 Hari</div>
        <div class="dsc-value text-success">${main.active_stocks_60d || 0}</div>
      </div>
      <div class="dq-stat-card ${main.null_open_pct > 0.5 ? 'dq-warning' : ''}">
        <div class="dsc-label">Open Price Missing</div>
        <div class="dsc-value">${main.null_open_pct !== undefined ? (main.null_open_pct * 100).toFixed(1) + '%' : '—'}</div>
      </div>
      <div class="dq-stat-card">
        <div class="dsc-label">Zero Volume Rate</div>
        <div class="dsc-value">${main.zero_vol_pct !== undefined ? (main.zero_vol_pct * 100).toFixed(1) + '%' : '—'}</div>
      </div>
    </div>

    <div class="dq-section">
      <div class="dqs-title">Data per Tahun</div>
      <table class="mini-table">
        <thead><tr><th>Tahun</th><th>Baris</th><th>Saham</th><th>Hari Bursa</th></tr></thead>
        <tbody>${yearRows}</tbody>
      </table>
    </div>

    <div class="dq-section">
      <div class="dqs-title">Tabel Broker (Ringkasan Market)</div>
      <div class="dq-table-info">
        <span>Baris: ${broker.rows?.toLocaleString() || 0}</span>
        <span>Tanggal: ${broker.min_date} – ${broker.max_date}</span>
        <span class="text-warning">${broker.note}</span>
      </div>
    </div>

    <div class="dq-section">
      <div class="dqs-title">Tabel Kepemilikan</div>
      <div class="dq-table-info">
        <span>Baris: ${own.rows?.toLocaleString() || 0}</span>
        <span>Saham: ${own.stocks}</span>
        <span>Tanggal: ${own.dates} (${own.min_date} – ${own.max_date})</span>
        <span class="text-warning">${own.note}</span>
      </div>
    </div>

    <div class="dq-section">
      <div class="dqs-title">⚠️ Keterbatasan Data (Diketahui)</div>
      <div class="dq-limitations">
        ${limits.map(l => `<div class="dq-limit-item">• ${l}</div>`).join('')}
      </div>
    </div>

    ${jobs.length > 0 ? `
      <div class="dq-section">
        <div class="dqs-title">Log Pipeline Terbaru</div>
        <table class="mini-table">
          <thead><tr><th>Job</th><th>Mulai</th><th>Status</th><th>Error</th></tr></thead>
          <tbody>${jobRows}</tbody>
        </table>
      </div>
    ` : ''}
  `;
}

// ── Evaluasi & Pembelajaran Tab ───────────────────────────────────────────
const EVAL_TIER_META = {
  SANGAT_KUAT: { label: 'Sangat Kuat', color: '#34d399' },
  KUAT:        { label: 'Kuat',        color: '#60a5fa' },
  CUKUP:       { label: 'Cukup',       color: '#fbbf24' },
  LEMAH:       { label: 'Lemah',       color: '#f87171' },
  BELUM_DIEVALUASI: { label: 'Belum Dievaluasi (data lama)', color: '#94a3b8' },
};

function evalPct(v) { return v === null || v === undefined ? '—' : (v * 100).toFixed(1) + '%'; }
function evalRet(v) { return v === null || v === undefined ? '—' : ((v >= 0 ? '+' : '') + (v * 100).toFixed(2) + '%'); }
function evalHitRateColor(v) {
  if (v === null || v === undefined) return '#94a3b8';
  if (v >= 0.6) return '#34d399';
  if (v >= 0.45) return '#fbbf24';
  return '#f87171';
}

async function loadWeeklyEvaluation() {
  const el = document.getElementById('weekly-evaluation-content');
  if (!el) return;
  el.innerHTML = '<div class="loading-spinner"><span class="spinner"></span> Memuat evaluasi...</div>';

  try {
    const res = await fetch('/api/weekly/evaluation');
    const data = await res.json();
    WeeklyState.evaluation = data;
    renderWeeklyEvaluation(el, data);
    // Pending-progress can have data even with zero matured signals yet
    // (a brand-new batch pulled today), so load it unconditionally.
    loadPendingSignalProgress();
    if (data.overall && data.overall.n > 0) {
      WeeklyState.evalSignalOffset = 0;
      WeeklyState.evalSignalTicker = '';
      loadEvaluationSignalHistory();
      loadTopConfidenceSignals();
    }
  } catch (e) {
    el.innerHTML = `<div class="error-msg">Error: ${e.message}</div>`;
  }
}

const EVAL_SIGNAL_PAGE_SIZE = 25;

async function loadEvaluationSignalHistory(append = false) {
  const el = document.getElementById('weekly-evaluation-signals-content');
  if (!el) return;
  if (!append) {
    el.innerHTML = '<div class="loading-spinner"><span class="spinner"></span> Memuat riwayat sinyal...</div>';
  }

  const offset = append ? (WeeklyState.evalSignalOffset || 0) : 0;
  const ticker = WeeklyState.evalSignalTicker || '';
  const params = new URLSearchParams({ limit: EVAL_SIGNAL_PAGE_SIZE, offset });
  if (ticker) params.set('ticker', ticker);

  try {
    const res = await fetch(`/api/weekly/evaluation/signals?${params.toString()}`);
    const data = await res.json();
    if (append) {
      WeeklyState.evalSignalRows = (WeeklyState.evalSignalRows || []).concat(data.signals);
    } else {
      WeeklyState.evalSignalRows = data.signals;
    }
    WeeklyState.evalSignalTotal = data.total;
    WeeklyState.evalSignalOffset = offset + data.signals.length;
    renderEvaluationSignalHistory(el);
  } catch (e) {
    el.innerHTML = `<div class="error-msg">Error: ${e.message}</div>`;
  }
}

function searchEvalSignalHistory(reset = false) {
  const input = document.getElementById('eval-signal-ticker-filter');
  if (reset && input) input.value = '';
  WeeklyState.evalSignalTicker = reset ? '' : (input ? input.value.trim() : '');
  WeeklyState.evalSignalOffset = 0;
  loadEvaluationSignalHistory(false);
}

function renderSignalHistoryRow(s, { showRank = false, rank = null } = {}) {
  const pct = s.pct_change;
  const pctColor = pct === null || pct === undefined ? '#94a3b8' : (pct >= 0 ? '#34d399' : '#f87171');
  const pctText = pct === null || pct === undefined ? '—' : `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
  const outcomeColor = s.outcome_status === 'HIT' ? '#34d399' : '#f87171';
  const probText = s.signal_probability !== null && s.signal_probability !== undefined ? `${s.signal_probability.toFixed(1)}%` : '—';
  return `
    <tr>
      ${showRank ? `<td class="text-muted">#${rank}</td>` : ''}
      <td>${s.prediction_date}</td>
      <td><strong>${s.ticker}</strong></td>
      <td style="font-weight:600">${probText}</td>
      <td>${s.horizon_days} hari</td>
      <td>${s.entry_price !== null ? s.entry_price.toLocaleString('id-ID') : '—'}</td>
      <td>${s.exit_price !== null ? s.exit_price.toLocaleString('id-ID') : '—'}</td>
      <td style="color:${pctColor}; font-weight:600">${pctText}</td>
      <td style="color:${outcomeColor}; font-weight:600">${s.outcome_status}</td>
      <td class="text-muted small">${EVAL_TIER_META[s.confidence_tier]?.label || s.confidence_tier || '—'}</td>
    </tr>
  `;
}

function renderEvaluationSignalHistory(el) {
  const rows = WeeklyState.evalSignalRows || [];
  const total = WeeklyState.evalSignalTotal || 0;
  const shown = rows.length;

  if (rows.length === 0) {
    el.innerHTML = '<div class="text-muted small">Tidak ada sinyal yang cocok.</div>';
    return;
  }

  const body = rows.map(s => renderSignalHistoryRow(s)).join('');

  el.innerHTML = `
    <table class="mini-table">
      <thead>
        <tr>
          <th>Tanggal Sinyal</th><th>Saham</th><th>Probabilitas</th><th>Horizon</th>
          <th>Harga Awal</th><th>Harga Akhir</th><th>Perubahan</th><th>Hasil</th><th>Tier</th>
        </tr>
      </thead>
      <tbody>${body}</tbody>
    </table>
    <div class="dq-table-info" style="margin-top:8px">
      <span>Menampilkan ${shown} dari ${total} sinyal yang sudah matang${WeeklyState.evalSignalTicker ? ` (filter: ${WeeklyState.evalSignalTicker})` : ''}</span>
      ${shown < total ? `<button class="btn-secondary" style="padding:4px 10px;font-size:12px" onclick="loadEvaluationSignalHistory(true)">Muat ${Math.min(EVAL_SIGNAL_PAGE_SIZE, total - shown)} Lagi</button>` : ''}
    </div>
  `;
}

const TOP_SIGNALS_BATCH_PAGE_SIZE = 1;

// Pagination inside a .dq-boxed-panel should feel like updating a widget,
// not reloading a page: keep whatever's already shown on screen (dimmed via
// [aria-busy]) instead of wiping it to a spinner first, which was causing
// the panel to collapse then re-expand on every page change. Only fall back
// to a spinner when the panel is genuinely empty (first load).
async function _loadIntoBoxedPanel(el, fetchAndRender) {
  if (!el) return;
  const panel = el.closest('.dq-boxed-panel');
  if (panel) {
    panel.setAttribute('aria-busy', 'true');
  }
  if (!el.innerHTML.trim()) {
    el.innerHTML = '<div class="loading-spinner"><span class="spinner"></span> Memuat...</div>';
  }
  try {
    await fetchAndRender();
  } catch (e) {
    el.innerHTML = `<div class="error-msg">Error: ${e.message}</div>`;
  } finally {
    if (panel) panel.removeAttribute('aria-busy');
  }
}

async function loadTopConfidenceSignals(batchOffset = 0) {
  const el = document.getElementById('weekly-evaluation-top10-content');
  await _loadIntoBoxedPanel(el, async () => {
    const res = await fetch(`/api/weekly/evaluation/top-signals?top_n=10&batch_limit=${TOP_SIGNALS_BATCH_PAGE_SIZE}&batch_offset=${batchOffset}`);
    const data = await res.json();
    WeeklyState.topSignalsBatchOffset = batchOffset;
    renderTopConfidenceSignals(el, data);
  });
}

function renderTopConfidenceSignals(el, data) {
  const batches = data.batches || [];
  const agg = data.aggregate || {};
  const totalBatches = data.total_batches || 0;
  const offset = data.batch_offset || 0;

  if (batches.length === 0) {
    el.innerHTML = '<div class="text-muted small">Belum ada tarikan sinyal untuk dirangking.</div>';
    return;
  }

  const aggColor = agg.hit_rate === null || agg.hit_rate === undefined ? '#94a3b8'
    : agg.hit_rate >= 0.6 ? '#34d399' : agg.hit_rate >= 0.4 ? '#fbbf24' : '#f87171';
  const aggSummary = `
    <div class="bt-cost-notice" style="margin-bottom:10px">
      📊 <strong>Akumulasi seluruh tarikan:</strong> ${agg.total_batches} tarikan × sampai 10 sinyal teratas
      = <strong>${agg.total_signals} sinyal</strong>, <strong style="color:${aggColor}">${agg.total_hits} berhasil (${evalPct(agg.hit_rate)})</strong>
      ${agg.hit_rate_ci_lower !== null && agg.hit_rate_ci_lower !== undefined ? ` · 95% CI ${evalPct(agg.hit_rate_ci_lower)}–${evalPct(agg.hit_rate_ci_upper)}` : ''}.
      Angka ini terus bertambah setiap kali ada tarikan (prediksi) baru yang sinyalnya sudah matang.
    </div>
  `;

  const batchBlocks = batches.map(b => {
    const hitColor = b.hit_rate === null ? '#94a3b8' : b.hit_rate >= 0.7 ? '#34d399' : b.hit_rate >= 0.4 ? '#fbbf24' : '#f87171';
    const rows = b.signals.map((s, i) => {
      const pct = s.pct_change;
      const pctColor = pct === null || pct === undefined ? '#94a3b8' : (pct >= 0 ? '#34d399' : '#f87171');
      const pctPrefix = s.is_pending ? '(sementara) ' : '';
      const pctText = pct === null || pct === undefined ? '—' : `${pctPrefix}${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
      const probText = s.signal_probability !== null && s.signal_probability !== undefined ? `${s.signal_probability.toFixed(1)}%` : '—';
      const priceLabel = s.exit_price !== null ? s.exit_price.toLocaleString('id-ID') : '—';
      let outcomeColor, outcomeText;
      if (s.is_pending) {
        outcomeColor = '#fbbf24';
        outcomeText = s.days_elapsed === null ? '🔄 Berjalan' : `🔄 Berjalan (${s.days_elapsed}/${s.horizon_days} hari)`;
      } else {
        outcomeColor = s.outcome_status === 'HIT' ? '#34d399' : '#f87171';
        outcomeText = s.outcome_status;
      }
      return `
        <tr${s.is_pending ? ' style="opacity:0.9"' : ''}>
          <td class="text-muted">#${i + 1}</td>
          <td><strong>${s.ticker}</strong></td>
          <td style="font-weight:600">${probText}</td>
          <td>${s.horizon_days} hari</td>
          <td>${s.entry_price !== null ? s.entry_price.toLocaleString('id-ID') : '—'}</td>
          <td>${priceLabel}</td>
          <td style="color:${pctColor}; font-weight:600">${pctText}</td>
          <td style="color:${outcomeColor}; font-weight:600">${outcomeText}</td>
          <td class="text-muted small">${EVAL_TIER_META[s.confidence_tier]?.label || s.confidence_tier || '—'}</td>
        </tr>
      `;
    }).join('');

    const statusLine = b.n_pending > 0
      ? (b.n_matured > 0
          ? `<span style="color:${hitColor}; font-weight:600">${b.hits}/${b.n_matured} HIT</span> · <span style="color:#fbbf24; font-weight:600">🔄 ${b.n_pending} masih berjalan</span>`
          : `<span style="color:#fbbf24; font-weight:600">🔄 ${b.n_pending} masih berjalan — belum ada yang matang</span>`)
      : `<span style="color:${hitColor}; font-weight:600">${b.hits}/${b.n} HIT (${evalPct(b.hit_rate)})</span>`;

    return `
      <div class="dq-section" style="margin-top:10px; padding-top:10px; border-top:1px dashed var(--border-color, #2a3050)">
        <div class="dq-table-info" style="margin-bottom:6px">
          <span><strong>Tarikan ${b.prediction_date}</strong>${pendingProgressPullTimeLabel(b.pulled_at)}</span>
          <span>${statusLine}</span>
        </div>
        <table class="mini-table">
          <thead>
            <tr>
              <th>#</th><th>Saham</th><th>Probabilitas</th><th>Horizon</th>
              <th>Harga Awal</th><th>Harga Terkini/Akhir</th><th>Perubahan</th><th>Hasil</th><th>Tier</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }).join('');

  const shownEnd = offset + batches.length;
  const currentPage = Math.floor(offset / TOP_SIGNALS_BATCH_PAGE_SIZE) + 1;
  const totalPages = Math.max(1, Math.ceil(totalBatches / TOP_SIGNALS_BATCH_PAGE_SIZE));
  const pagination = `
    <div class="dq-table-info" style="margin-top:10px">
      <span>Halaman ${currentPage} dari ${totalPages} — Tarikan ${offset + 1}–${shownEnd} dari ${totalBatches} (terbaru dulu)</span>
      <span>
        <button class="btn-secondary" style="padding:4px 10px;font-size:12px" ${offset <= 0 ? 'disabled' : ''}
          onclick="loadTopConfidenceSignals(${Math.max(0, offset - TOP_SIGNALS_BATCH_PAGE_SIZE)})">← Tarikan Lebih Baru</button>
        <button class="btn-secondary" style="padding:4px 10px;font-size:12px" ${shownEnd >= totalBatches ? 'disabled' : ''}
          onclick="loadTopConfidenceSignals(${offset + TOP_SIGNALS_BATCH_PAGE_SIZE})">Tarikan Lebih Lama →</button>
      </span>
    </div>
  `;

  el.innerHTML = `
    <div class="text-muted small mb-1">
      Tiap tarikan (satu kali pull prediksi, bisa lebih dari sekali per hari) diambil 10 sinyal dengan probabilitas
      TERTINGGI-nya sendiri saat itu — bukan dicampur jadi satu daftar global. Ini pengujian paling ketat per-tarikan:
      kalau model benar-benar yakin di hari itu, sinyal-sinyal inilah yang seharusnya paling sering benar.
      Tarikan yang belum genap 5 hari bursa tetap ditampilkan dengan progres SEMENTARA (🔄 Berjalan) —
      baru dihitung ke statistik hit rate setelah benar-benar matang.
    </div>
    ${aggSummary}
    ${batchBlocks}
    ${pagination}
  `;
}

// ── Progres sinyal yang belum matang (in-flight, outcome_status='PENDING') ──
const PENDING_PROGRESS_STATE_META = {
  MENUJU_TP: { label: 'Menuju TP', color: '#34d399' },
  MENDEKATI_SL: { label: 'Mendekati SL', color: '#f87171' },
  POSITIF: { label: 'Positif (di tengah)', color: '#4ade80' },
  NEGATIF: { label: 'Negatif (di tengah)', color: '#fb923c' },
  BELUM_ADA_DATA: { label: 'Belum ada data hari ini', color: '#94a3b8' },
};

async function loadPendingSignalProgress(batchOffset = 0) {
  const el = document.getElementById('weekly-pending-progress-content');
  await _loadIntoBoxedPanel(el, async () => {
    const params = new URLSearchParams({ batch_limit: '1', batch_offset: String(batchOffset) });
    const res = await fetch(`/api/weekly/evaluation/pending-progress?${params}`);
    const data = await res.json();
    WeeklyState.pendingProgressOffset = batchOffset;
    renderPendingSignalProgress(el, data);
  });
}

function pendingProgressPullTimeLabel(pulledAt) {
  if (!pulledAt) return '';
  // pulled_at is a SQLite CURRENT_TIMESTAMP string "YYYY-MM-DD HH:MM:SS" (UTC) —
  // just show the time part, enough to tell same-day pulls apart.
  const parts = String(pulledAt).split(' ');
  return parts[1] ? ` · ditarik jam ${parts[1].slice(0, 5)}` : '';
}

function renderPendingSignalProgress(el, data) {
  const batches = data.batches || [];
  const totalBatches = data.total_batches || 0;
  const offset = data.batch_offset || 0;

  if (batches.length === 0 || !batches[0].signals || batches[0].signals.length === 0) {
    el.innerHTML = `
      <div class="text-muted small">Tidak ada sinyal yang sedang berjalan (belum matang) saat ini. Setiap tarikan baru akan otomatis muncul di sini, terbagi per halaman, sampai genap horizon-nya.</div>
    `;
    return;
  }

  const batch = batches[0];
  const rows = batch.signals.map(s => {
    const meta = PENDING_PROGRESS_STATE_META[s.progress_state] || { label: s.progress_state, color: '#94a3b8' };
    const retColor = s.running_return === null || s.running_return === undefined ? '#94a3b8' : (s.running_return >= 0 ? '#34d399' : '#f87171');
    const retText = s.running_return === null || s.running_return === undefined ? '—' : `${s.running_return >= 0 ? '+' : ''}${(s.running_return * 100).toFixed(2)}%`;
    const probText = s.signal_probability !== null && s.signal_probability !== undefined ? `${s.signal_probability.toFixed(1)}%` : '—';
    return `
      <tr>
        <td><strong>${s.ticker}</strong></td>
        <td>${probText}</td>
        <td>Hari ${s.days_elapsed}/${s.horizon_days} (sisa ${s.days_remaining})</td>
        <td>${s.entry_price !== null ? s.entry_price.toLocaleString('id-ID') : '—'}</td>
        <td>${s.current_price !== null ? s.current_price.toLocaleString('id-ID') : '—'}${s.last_price_date ? ` <span class="text-muted small">(${s.last_price_date})</span>` : ''}</td>
        <td style="color:${retColor}; font-weight:600">${retText}</td>
        <td>${s.target_price !== null ? s.target_price.toLocaleString('id-ID') : '—'}</td>
        <td>${s.stop_price !== null ? s.stop_price.toLocaleString('id-ID') : '—'}</td>
        <td style="color:${meta.color}; font-weight:600">${meta.label}</td>
      </tr>
    `;
  }).join('');

  const currentPage = offset + 1;
  const pagination = `
    <div class="dq-table-info" style="margin-top:10px">
      <span>Halaman ${currentPage} dari ${totalBatches} tarikan yang masih berjalan (terbaru dulu)</span>
      <span>
        <button class="btn-secondary" style="padding:4px 10px;font-size:12px" ${offset <= 0 ? 'disabled' : ''}
          onclick="loadPendingSignalProgress(${Math.max(0, offset - 1)})">← Lebih Baru</button>
        <button class="btn-secondary" style="padding:4px 10px;font-size:12px" ${offset + 1 >= totalBatches ? 'disabled' : ''}
          onclick="loadPendingSignalProgress(${offset + 1})">Lebih Lama →</button>
      </span>
    </div>
  `;

  el.innerHTML = `
    <div class="dq-table-info" style="margin-bottom:6px">
      <span><strong>Tarikan ${batch.prediction_date}${pendingProgressPullTimeLabel(batch.pulled_at)}</strong></span>
      <span>${batch.n} sinyal berjalan · <span style="color:#34d399">${batch.menuju_tp} menuju TP</span> · <span style="color:#f87171">${batch.mendekati_sl} mendekati SL</span></span>
    </div>
    <table class="mini-table">
      <thead>
        <tr>
          <th>Saham</th><th>Probabilitas</th><th>Progres Hari</th><th>Harga Masuk</th>
          <th>Harga Terkini</th><th>Perubahan Sementara</th><th>Target TP</th><th>Stop Loss</th><th>Status</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
    ${pagination}
  `;
}

function renderWeeklyEvaluation(el, data) {
  const dm = data.data_maturity || {};
  const overall = data.overall || {};
  const byTier = data.by_confidence_tier || [];
  const byStatus = data.by_decision_status || [];
  const rolling = data.rolling || [];
  const topReasons = data.top_reason_codes || [];
  const topFlags = data.top_risk_flags || [];
  const byRegime = data.by_market_regime || [];
  const timeline = data.timeline || [];

  const rateRow = (label, r, extraCols = '') => `
    <tr>
      <td>${label}</td>
      <td>${r.n}</td>
      <td>${r.hits}</td>
      <td>${r.misses}</td>
      <td style="color:${evalHitRateColor(r.hit_rate)}; font-weight:600">${evalPct(r.hit_rate)}</td>
      <td class="text-muted small">${r.hit_rate_ci_lower !== null && r.hit_rate_ci_lower !== undefined ? `${evalPct(r.hit_rate_ci_lower)} – ${evalPct(r.hit_rate_ci_upper)}` : '—'}</td>
      <td>${evalRet(r.avg_return)}</td>
      ${extraCols}
    </tr>
  `;

  const tierRows = byTier.map(r => {
    const meta = EVAL_TIER_META[r.tier] || { label: r.label || r.tier, color: '#94a3b8' };
    return rateRow(`<span style="color:${meta.color}; font-weight:600">● ${meta.label}</span>`, r);
  }).join('');

  const statusRows = byStatus.map(r => {
    const meta = DECISION_LABELS[r.status] || { label: r.label || r.status, color: '#94a3b8' };
    return rateRow(`<span style="color:${meta.color}; font-weight:600">● ${meta.label}</span>`, r);
  }).join('');

  const rollingRows = rolling.map(r => rateRow(`Terakhir ${r.n < r.window ? r.n : r.window} sinyal`, r)).join('');

  const regimeRows = byRegime.map(r => rateRow(r.regime, r)).join('');

  const reasonRows = topReasons.map(r => `
    <tr>
      <td>${r.text}</td>
      <td>${r.n}</td>
      <td style="color:${evalHitRateColor(r.hit_rate)}; font-weight:600">${evalPct(r.hit_rate)}</td>
    </tr>
  `).join('');

  const flagRows = topFlags.map(r => `
    <tr>
      <td>${r.text}</td>
      <td>${r.n}</td>
      <td style="color:${evalHitRateColor(r.hit_rate)}; font-weight:600">${evalPct(r.hit_rate)}</td>
    </tr>
  `).join('');

  const maturityBanner = dm.note ? `
    <div class="bt-cost-notice" style="border-color:#fbbf24">
      ⏳ <strong>Belum cukup data:</strong> ${dm.note}
    </div>
  ` : '';

  const staleModelBanner = dm.stale_model_warning ? `
    <div class="bt-cost-notice" style="border-color:#f87171; background:rgba(248,113,113,0.08)">
      🔄 <strong>Data di bawah dari model versi LAMA:</strong> ${dm.stale_model_warning}
    </div>
  ` : '';

  const hasData = overall.n > 0;

  el.innerHTML = `
    <div class="dq-header">
      <div class="dq-title">Evaluasi & Pembelajaran — Rekam Jejak Sinyal</div>
      <button class="btn-primary sog-btn" id="weekly-eval-run-btn" onclick="triggerEvaluationRun()">🔄 Cek Sinyal Matang Sekarang</button>
    </div>
    <div id="weekly-eval-run-log" class="sog-log"></div>

    <div class="dq-table-info" style="margin-top:8px">
      <span>Total prediksi: ${dm.total_predictions || 0}</span>
      <span>Sudah dievaluasi: ${dm.evaluated_count || 0}</span>
      <span>Masih pending: ${dm.pending_count || 0}</span>
      <span>Rentang: ${dm.oldest_prediction_date || '—'} → ${dm.newest_prediction_date || '—'}</span>
    </div>

    ${maturityBanner}
    ${staleModelBanner}

    <div class="dq-section">
      <div class="dqs-title">⏱️ Progres Sinyal Berjalan (Belum Matang)</div>
      <div class="dq-boxed-panel">
        <div class="text-muted small mb-1">Sinyal yang sudah ditarik tapi belum genap ${dm.min_horizon_days || 5} hari bursa — harga TERKINI dibanding harga masuk. Ini progres SEMENTARA, bukan hasil akhir (baru final HIT/MISS setelah cukup umur).</div>
        <div id="weekly-pending-progress-content"><div class="loading-spinner"><span class="spinner"></span> Memuat...</div></div>
      </div>
    </div>

    ${!hasData ? `
      <div class="dq-section">
        <div class="text-muted mt-2">Belum ada sinyal yang matang dan terverifikasi. Dashboard ini otomatis terisi setiap hari
        seiring sinyal-sinyal lama mencapai umur minimal ${dm.min_horizon_days || 5} hari bursa dan hasilnya bisa diverifikasi
        terhadap harga aktual. Sistem tidak menampilkan rekam jejak palsu — jujur menunggu data nyata.</div>
      </div>
    ` : `
      <div class="dq-section">
        <div class="dqs-title">Rekam Jejak Keseluruhan</div>
        <div class="dq-stat-grid">
          <div class="dq-stat-card">
            <div class="dsc-label">Total Sinyal Dievaluasi</div>
            <div class="dsc-value">${overall.n}</div>
          </div>
          <div class="dq-stat-card">
            <div class="dsc-label">Hit Rate</div>
            <div class="dsc-value" style="color:${evalHitRateColor(overall.hit_rate)}">${evalPct(overall.hit_rate)}</div>
          </div>
          <div class="dq-stat-card">
            <div class="dsc-label">95% CI</div>
            <div class="dsc-value small">${evalPct(overall.hit_rate_ci_lower)} – ${evalPct(overall.hit_rate_ci_upper)}</div>
          </div>
          <div class="dq-stat-card">
            <div class="dsc-label">Rata-rata Return</div>
            <div class="dsc-value">${evalRet(overall.avg_return)}</div>
          </div>
        </div>
      </div>

      <div class="dq-section">
        <div class="dqs-title">🏆 Top 10 Sinyal — Probabilitas Tertinggi Saat Diberikan</div>
        <div class="dq-boxed-panel">
          <div id="weekly-evaluation-top10-content"></div>
        </div>
      </div>

      <div class="dq-section">
        <div class="dqs-title">Kalibrasi per Tier Keyakinan</div>
        <div class="text-muted small mb-1">Membuktikan apakah tier yang lebih tinggi benar-benar lebih akurat di dunia nyata.</div>
        <table class="mini-table">
          <thead><tr><th>Tier</th><th>N</th><th>Hit</th><th>Miss</th><th>Hit Rate</th><th>95% CI</th><th>Avg Return</th></tr></thead>
          <tbody>${tierRows || '<tr><td colspan="7" class="text-muted">Belum ada data</td></tr>'}</tbody>
        </table>
      </div>

      <div class="dq-section">
        <div class="dqs-title">Validasi Status Keputusan</div>
        <div class="text-muted small mb-1">Membuktikan apakah gate QUALIFIED/WATCHLIST/NO_TRADE benar-benar memisahkan sinyal baik dan buruk.</div>
        <table class="mini-table">
          <thead><tr><th>Status</th><th>N</th><th>Hit</th><th>Miss</th><th>Hit Rate</th><th>95% CI</th><th>Avg Return</th></tr></thead>
          <tbody>${statusRows || '<tr><td colspan="7" class="text-muted">Belum ada data</td></tr>'}</tbody>
        </table>
      </div>

      ${rollingRows ? `
      <div class="dq-section">
        <div class="dqs-title">Performa Bergulir (Rolling)</div>
        <table class="mini-table">
          <thead><tr><th>Window</th><th>N</th><th>Hit</th><th>Miss</th><th>Hit Rate</th><th>95% CI</th><th>Avg Return</th></tr></thead>
          <tbody>${rollingRows}</tbody>
        </table>
      </div>` : ''}

      ${regimeRows ? `
      <div class="dq-section">
        <div class="dqs-title">Performa per Rezim Pasar</div>
        <table class="mini-table">
          <thead><tr><th>Rezim</th><th>N</th><th>Hit</th><th>Miss</th><th>Hit Rate</th><th>95% CI</th><th>Avg Return</th></tr></thead>
          <tbody>${regimeRows}</tbody>
        </table>
      </div>` : ''}

      <div class="dq-section">
        <div class="dqs-title">Reason Code Paling Sering Muncul</div>
        <div class="text-muted small mb-1">Alasan mana yang secara historis berkorelasi dengan hasil yang benar-benar HIT.</div>
        <table class="mini-table">
          <thead><tr><th>Reason</th><th>N</th><th>Hit Rate</th></tr></thead>
          <tbody>${reasonRows || '<tr><td colspan="3" class="text-muted">Belum cukup data</td></tr>'}</tbody>
        </table>
      </div>

      <div class="dq-section">
        <div class="dqs-title">Risk Flag Paling Sering Muncul</div>
        <table class="mini-table">
          <thead><tr><th>Risk Flag</th><th>N</th><th>Hit Rate</th></tr></thead>
          <tbody>${flagRows || '<tr><td colspan="3" class="text-muted">Belum cukup data</td></tr>'}</tbody>
        </table>
      </div>

      ${timeline.length > 0 ? `
      <div class="dq-section">
        <div class="dqs-title">Linimasa Evaluasi Harian</div>
        <div class="equity-chart-wrap">
          <canvas id="evaluation-timeline-chart" height="200"></canvas>
        </div>
      </div>` : ''}

      <div class="dq-section">
        <div class="dqs-title">📋 Riwayat Sinyal Individual — Sebelum → Sesudah</div>
        <div class="text-muted small mb-1">Sinyal per saham: harga saat sinyal diberikan, harga setelah horizon berakhir, dan persentase perubahannya — bukti mentah di balik angka ringkasan di atas.</div>
        <div style="display:flex; gap:8px; align-items:center; margin-bottom:8px">
          <input type="text" id="eval-signal-ticker-filter" placeholder="Filter kode saham (mis. BBCA)"
            style="background:#0f172a;border:1px solid #1e293b;border-radius:6px;color:var(--text-bright);padding:6px 10px;font-size:13px;width:220px"
            onkeydown="if(event.key==='Enter') searchEvalSignalHistory()">
          <button class="btn-secondary" style="padding:6px 12px;font-size:13px" onclick="searchEvalSignalHistory()">🔍 Cari</button>
          <button class="btn-secondary" style="padding:6px 12px;font-size:13px" onclick="searchEvalSignalHistory(true)">✕ Reset</button>
        </div>
        <div id="weekly-evaluation-signals-content"></div>
      </div>
    `}

    <div class="bt-disclaimer">
      🧠 <strong>Tentang tab ini:</strong> Setiap sinyal yang sudah cukup umur (min. ${dm.min_horizon_days || 5} hari bursa)
      dicek otomatis terhadap harga aktual saat prediksi baru dibuat. Dashboard ini murni melaporkan apa yang benar-benar
      terjadi — tidak ada angka yang dipoles atau dipaksakan. Semakin lama sistem berjalan, semakin banyak dan semakin
      andal pengetahuan yang terkumpul di sini.
    </div>
  `;

  if (timeline.length > 0) {
    setTimeout(() => renderEvaluationTimelineChart(timeline), 100);
  }
}

function renderEvaluationTimelineChart(timeline) {
  const canvas = document.getElementById('evaluation-timeline-chart');
  if (!canvas || typeof Chart === 'undefined') return;

  const labels = timeline.map(t => t.date);
  const hitRates = timeline.map(t => t.evaluated > 0 ? (t.hits / t.evaluated) * 100 : null);
  const counts = timeline.map(t => t.evaluated);

  if (WeeklyState.evaluationChart) WeeklyState.evaluationChart.destroy();

  WeeklyState.evaluationChart = new Chart(canvas, {
    type: 'bar',
    data: {
      labels,
      datasets: [
        {
          type: 'line',
          label: 'Hit Rate (%)',
          data: hitRates,
          borderColor: '#34d399',
          backgroundColor: 'rgba(52,211,153,0.1)',
          borderWidth: 2,
          pointRadius: 3,
          yAxisID: 'y1',
          tension: 0.2,
        },
        {
          type: 'bar',
          label: 'Jumlah Dievaluasi',
          data: counts,
          backgroundColor: 'rgba(96,165,250,0.35)',
          yAxisID: 'y',
        },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { labels: { color: '#94a3b8' } } },
      scales: {
        x: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#94a3b8', maxTicksLimit: 12 } },
        y: { position: 'left', grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#94a3b8', precision: 0 } },
        y1: { position: 'right', min: 0, max: 100, grid: { display: false }, ticks: { color: '#94a3b8', callback: v => v + '%' } },
      },
    },
  });
}

async function triggerEvaluationRun() {
  const btn = document.getElementById('weekly-eval-run-btn');
  const logEl = document.getElementById('weekly-eval-run-log');

  if (btn) btn.disabled = true;
  if (logEl) logEl.innerHTML = '<span style="color:#94a3b8">⏳ Memeriksa sinyal yang sudah matang...</span>';

  try {
    const res = await fetch('/api/weekly/evaluation/run', { method: 'POST' });
    const data = await res.json();

    if (!data.ok) {
      // Blocked because another ML job (training/predict/pipeline) is using
      // the same model_run rows right now — not an error. Wait for it to
      // finish and retry automatically instead of leaving the user stuck.
      if (/proses lain/i.test(data.message || '')) {
        if (WeeklyState.evaluationBlockedPolling) clearInterval(WeeklyState.evaluationBlockedPolling);
        if (logEl) logEl.innerHTML = `<span style="color:#fbbf24">⏳ ${data.message} Akan dicoba otomatis begitu selesai...</span>`;
        WeeklyState.evaluationBlockedPolling = setInterval(async () => {
          try {
            const statusRes = await fetch('/api/weekly/status');
            const s = await statusRes.json();
            if (!s.any_job_running) {
              clearInterval(WeeklyState.evaluationBlockedPolling);
              WeeklyState.evaluationBlockedPolling = null;
              triggerEvaluationRun();
            }
          } catch (e) {
            // ignore, keep polling
          }
        }, 5000);
        return;
      }
      if (logEl) logEl.innerHTML = `<span style="color:#f87171">❌ ${data.message}</span>`;
      if (btn) btn.disabled = false;
      return;
    }

    if (WeeklyState.evaluationPolling) clearInterval(WeeklyState.evaluationPolling);
    WeeklyState.evaluationPolling = setInterval(async () => {
      try {
        const statusRes = await fetch('/api/weekly/evaluation/run/status');
        const s = await statusRes.json();
        const color = s.status === 'failed' ? '#f87171' : s.status === 'success' ? '#34d399' : '#94a3b8';
        if (logEl) logEl.innerHTML = `<span style="color:${color}">${s.message || ''}</span>`;

        if (s.status === 'success' || s.status === 'failed' || s.status === 'idle') {
          clearInterval(WeeklyState.evaluationPolling);
          WeeklyState.evaluationPolling = null;
          if (btn) btn.disabled = false;
          if (s.status === 'success') {
            setTimeout(() => loadWeeklyEvaluation(), 500);
          }
        }
      } catch (e) {
        console.warn('Poll error:', e);
      }
    }, 2000);
  } catch (e) {
    if (logEl) logEl.innerHTML = `<span style="color:#f87171">Error: ${e.message}</span>`;
    if (btn) btn.disabled = false;
  }
}

// ── Prediksi IHSG Tab ──────────────────────────────────────────────────────
const IHSG_REGIME_META = {
  TREN_NAIK: { label: 'Tren Naik', color: '#34d399', icon: '📈' },
  TREN_TURUN: { label: 'Tren Turun', color: '#f87171', icon: '📉' },
  SIDEWAYS: { label: 'Sideways', color: '#fbbf24', icon: '➡️' },
  VOLATILITAS_TINGGI: { label: 'Volatilitas Tinggi', color: '#f97316', icon: '⚡' },
  BELUM_CUKUP_DATA: { label: 'Belum Cukup Data', color: '#94a3b8', icon: '⏳' },
};

const IHSG_HORIZON_META = {
  short: { title: 'Arah Taktis (5 Hari Bursa)', subtitle: '~1 minggu ke depan' },
  medium: { title: 'Arah Tren (60 Hari Bursa)', subtitle: '~3 bulan ke depan' },
};

function ihsgProbColor(p) {
  if (p === null || p === undefined) return '#94a3b8';
  if (p >= 0.6) return '#34d399';
  if (p >= 0.45) return '#fbbf24';
  return '#f87171';
}

async function loadIhsgAnalysis() {
  const el = document.getElementById('weekly-ihsg-content');
  if (!el) return;
  el.innerHTML = '<div class="loading-spinner"><span class="spinner"></span> Memuat analisis IHSG...</div>';

  try {
    const res = await fetch('/api/weekly/ihsg');
    const data = await res.json();
    WeeklyState.ihsg = data;
    renderIhsgAnalysis(el, data);
  } catch (e) {
    el.innerHTML = `<div class="error-msg">Error: ${e.message}</div>`;
  }
}

function renderIhsgAnalysis(el, data) {
  if (!data.data_available) {
    el.innerHTML = `
      <div class="dq-header">
        <div class="dq-title">Prediksi Arah IHSG</div>
      </div>
      <div class="dq-section">
        <div class="text-muted mt-2">${data.note || 'Data IHSG belum tersedia.'}</div>
        <div class="text-muted small mt-2">Baris data saat ini: ${data.rows || 0} / minimal ${data.min_rows_required || '—'} hari bursa.
        Data disinkronkan otomatis setiap hari lewat tombol "Perbarui Data Terkini" atau auto-sync harian.</div>
      </div>
    `;
    return;
  }

  const latest = data.latest || {};
  const regime = IHSG_REGIME_META[data.regime] || { label: data.regime, color: '#94a3b8', icon: '' };
  const changeColor = (latest.change_pct || 0) >= 0 ? '#34d399' : '#f87171';
  const changeSign = (latest.change_pct || 0) >= 0 ? '+' : '';

  const directionCards = Object.entries(data.direction || {}).map(([key, d]) => {
    const meta = IHSG_HORIZON_META[key] || { title: key, subtitle: '' };
    const live = d.live_prediction;
    const liveProb = live ? live.probability_up : null;
    const liveColor = ihsgProbColor(liveProb);
    const liveLabel = liveProb === null ? 'Model belum bisa memberi sinyal (data belum cukup)' :
      (liveProb >= 0.5 ? 'Probabilitas NAIK lebih besar' : 'Probabilitas TURUN lebih besar');
    return `
      <div class="dq-section">
        <div class="dqs-title">${meta.title}</div>
        <div class="text-muted small mb-1">${meta.subtitle}</div>
        <div class="dq-stat-grid">
          <div class="dq-stat-card">
            <div class="dsc-label">Probabilitas Naik (Live)</div>
            <div class="dsc-value" style="color:${liveColor}">${liveProb === null ? '—' : (liveProb * 100).toFixed(1) + '%'}</div>
            <div class="text-muted small">${liveLabel}</div>
          </div>
          <div class="dq-stat-card">
            <div class="dsc-label">Validasi Historis (Walk-Forward)</div>
            <div class="dsc-value" style="color:${evalHitRateColor(d.hit_rate)}">${evalPct(d.hit_rate)}</div>
            <div class="text-muted small">${d.n_validated} sinyal diuji out-of-sample${d.hit_rate !== null ? ` · 95% CI ${evalPct(d.hit_rate_ci_lower)}–${evalPct(d.hit_rate_ci_upper)}` : ''}</div>
          </div>
        </div>
      </div>
    `;
  }).join('');

  const analog = data.bottom_analog || {};
  const analogSection = `
    <div class="dq-section">
      <div class="dqs-title">🔻 Analisis Drawdown & Analog Bottom Historis</div>
      <div class="dq-stat-grid">
        <div class="dq-stat-card">
          <div class="dsc-label">Drawdown dari ATH</div>
          <div class="dsc-value" style="color:#f87171">${analog.current_drawdown_pct !== null && analog.current_drawdown_pct !== undefined ? analog.current_drawdown_pct.toFixed(2) + '%' : '—'}</div>
        </div>
        <div class="dq-stat-card">
          <div class="dsc-label">Persentil Keparahan</div>
          <div class="dsc-value">${analog.drawdown_percentile !== null && analog.drawdown_percentile !== undefined ? analog.drawdown_percentile.toFixed(1) + '%' : '—'}</div>
          <div class="text-muted small">Makin tinggi = makin jarang/dalam dibanding histori</div>
        </div>
        <div class="dq-stat-card">
          <div class="dsc-label">Episode Historis Serupa</div>
          <div class="dsc-value">${analog.similar_episodes || 0}</div>
        </div>
        <div class="dq-stat-card">
          <div class="dsc-label">Median Jarak ke Titik Balik</div>
          <div class="dsc-value">${analog.median_sessions_to_next_bottom !== null && analog.median_sessions_to_next_bottom !== undefined ? analog.median_sessions_to_next_bottom + ' hari' : '—'}</div>
          <div class="text-muted small">${analog.sessions_to_next_bottom_range ? `Rentang ${analog.sessions_to_next_bottom_range[0]}–${analog.sessions_to_next_bottom_range[1]} hari bursa` : ''}</div>
        </div>
      </div>
      <div class="bt-cost-notice" style="margin-top:10px">⚠️ ${analog.note || ''}</div>
    </div>
  `;

  el.innerHTML = `
    <div class="dq-header">
      <div class="dq-title">Prediksi Arah IHSG — per ${data.as_of_date}</div>
      <span class="signal-count-badge" style="background:${regime.color}22; color:${regime.color}; border:1px solid ${regime.color}55; font-weight:600">${regime.icon} ${regime.label}</span>
    </div>

    <div class="dq-stat-grid">
      <div class="dq-stat-card">
        <div class="dsc-label">IHSG Close</div>
        <div class="dsc-value">${latest.close?.toLocaleString('id-ID') ?? '—'}</div>
        <div class="text-muted small" style="color:${changeColor}">${changeSign}${latest.change_pct ?? '—'}%</div>
      </div>
      <div class="dq-stat-card">
        <div class="dsc-label">RSI (14)</div>
        <div class="dsc-value">${latest.rsi14 ?? '—'}</div>
      </div>
      <div class="dq-stat-card">
        <div class="dsc-label">SMA 50 / SMA 200</div>
        <div class="dsc-value small">${latest.sma50?.toLocaleString('id-ID') ?? '—'} / ${latest.sma200?.toLocaleString('id-ID') ?? '—'}</div>
      </div>
      <div class="dq-stat-card">
        <div class="dsc-label">Volatilitas Harian (20d)</div>
        <div class="dsc-value">${latest.volatility_20d_pct ?? '—'}%</div>
      </div>
    </div>

    <div class="dq-section">
      <div class="dqs-title">Grafik IHSG — Close, SMA50, SMA200 & Drawdown dari ATH</div>
      <div class="equity-chart-wrap">
        <canvas id="ihsg-chart" height="280"></canvas>
      </div>
    </div>

    ${directionCards}

    ${analogSection}

    <div class="bt-disclaimer">
      🧠 <strong>Cara membaca dashboard ini:</strong> Probabilitas arah dihasilkan dari model walk-forward
      (dilatih ulang berkala, hanya memakai data yang sudah pasti diketahui sebelum setiap tanggal prediksi —
      tanpa lihat masa depan) sehingga angka "Validasi Historis" di atas adalah performa out-of-sample yang jujur,
      bukan angka yang dipoles. Bagian "Analog Bottom Historis" BUKAN prediksi tanggal pasti kapan IHSG akan
      berbalik arah — tidak ada metode yang bisa menjanjikan itu secara jujur. Angka tersebut murni statistik dasar
      (base rate) dari seberapa jauh drawdown serupa di masa lalu biasanya berlangsung sebelum titik balik lokal.
    </div>
  `;

  setTimeout(() => renderIhsgChart(data.timeseries), 100);
}

function renderIhsgChart(ts) {
  const canvas = document.getElementById('ihsg-chart');
  if (!canvas || typeof Chart === 'undefined' || !ts) return;

  if (WeeklyState.ihsgChart) WeeklyState.ihsgChart.destroy();

  WeeklyState.ihsgChart = new Chart(canvas, {
    type: 'line',
    data: {
      labels: ts.dates,
      datasets: [
        {
          label: 'IHSG Close',
          data: ts.close,
          borderColor: '#60a5fa',
          backgroundColor: 'rgba(96,165,250,0.08)',
          borderWidth: 2.5,
          pointRadius: 0,
          yAxisID: 'y',
          tension: 0.15,
        },
        {
          label: 'SMA 50',
          data: ts.sma50,
          borderColor: '#fbbf24',
          borderWidth: 1.5,
          borderDash: [4, 3],
          pointRadius: 0,
          yAxisID: 'y',
          tension: 0.15,
        },
        {
          label: 'SMA 200',
          data: ts.sma200,
          borderColor: '#a78bfa',
          borderWidth: 1.5,
          borderDash: [2, 2],
          pointRadius: 0,
          yAxisID: 'y',
          tension: 0.15,
        },
        {
          label: 'Drawdown dari ATH (%)',
          data: ts.drawdown_pct,
          borderColor: '#f87171',
          backgroundColor: 'rgba(248,113,113,0.15)',
          borderWidth: 1.5,
          pointRadius: 0,
          fill: true,
          yAxisID: 'y1',
          tension: 0.1,
        },
      ],
    },
    options: {
      responsive: true,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { labels: { color: '#94a3b8', usePointStyle: true, boxHeight: 6 } },
        tooltip: {
          callbacks: {
            label: ctx => {
              if (ctx.dataset.yAxisID === 'y1') return `${ctx.dataset.label}: ${ctx.raw?.toFixed(2)}%`;
              return `${ctx.dataset.label}: ${ctx.raw?.toLocaleString('id-ID')}`;
            },
          },
        },
      },
      scales: {
        x: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#94a3b8', maxTicksLimit: 14 } },
        y: { position: 'left', grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#94a3b8' } },
        y1: {
          position: 'right', max: 2, grid: { display: false },
          ticks: { color: '#94a3b8', callback: v => v + '%' },
        },
      },
    },
  });
}

// ── Settings Tab ───────────────────────────────────────────────────────────
async function loadWeeklySettings() {
  const el = document.getElementById('weekly-settings-content');
  if (!el) return;
  el.innerHTML = '<div class="loading-spinner"><span class="spinner"></span> Memuat pengaturan...</div>';

  try {
    const [statusRes, settingsRes] = await Promise.all([
      fetch('/api/weekly/status'),
      fetch('/api/weekly/settings'),
    ]);
    const status = await statusRes.json();
    const settingsData = await settingsRes.json();
    WeeklyState.status = status;
    WeeklyState.settings = settingsData.settings;
    renderSettings(el, status, settingsData.settings);

    // Another job (started from a different tab, or by auto-retrain) may
    // already be running when this tab loads — without this, its progress
    // banner would render once and then sit frozen until the user manually
    // switches tabs again.
    if (WeeklyState.settingsPolling) clearInterval(WeeklyState.settingsPolling);
    if (status.any_job_running) {
      WeeklyState.settingsPolling = setInterval(async () => {
        if (WeeklyState.tab !== 'settings') {
          clearInterval(WeeklyState.settingsPolling);
          WeeklyState.settingsPolling = null;
          return;
        }
        try {
          const [sRes, cfgRes] = await Promise.all([
            fetch('/api/weekly/status'), fetch('/api/weekly/settings'),
          ]);
          const s = await sRes.json();
          const c = await cfgRes.json();
          WeeklyState.status = s;
          renderSettings(el, s, c.settings);
          if (!s.any_job_running) {
            clearInterval(WeeklyState.settingsPolling);
            WeeklyState.settingsPolling = null;
          }
        } catch (e) {
          console.warn('Settings poll error:', e);
        }
      }, 4000);
    }
  } catch (e) {
    el.innerHTML = `<div class="error-msg">Error: ${e.message}</div>`;
  }
}

const OTHER_JOB_FIELD_MAP = {
  'training': { message: 'train_message', progress: 'train_progress' },
  'prediksi': { message: 'predict_message', progress: 'predict_progress' },
  'evaluasi ensemble': { message: 'ensemble_eval_message', progress: 'ensemble_eval_progress' },
  'pipeline lengkap (latih semua)': { message: 'train_all_message', progress: 'train_all_progress' },
};

function renderOtherJobBanner(status) {
  const jobLabel = status.any_job_running;
  const fields = OTHER_JOB_FIELD_MAP[jobLabel];
  const pct = fields ? status[fields.progress] : null;
  const msg = fields ? status[fields.message] : null;
  const pctText = (pct === null || pct === undefined) ? '' : ` (${pct}%)`;
  return `
    <div class="sog-log" style="color:#fbbf24">⚠ Proses lain sedang berjalan: <strong>${jobLabel}${pctText}</strong> — tombol dinonaktifkan sementara.</div>
    ${pct !== null && pct !== undefined ? `
      <div style="margin-top:6px;background:#0f172a;border-radius:6px;height:10px;overflow:hidden;border:1px solid #1e293b">
        <div style="height:100%;background:linear-gradient(90deg,#f59e0b,#fbbf24);border-radius:6px;transition:width 0.5s;width:${pct}%"></div>
      </div>` : ''}
    ${msg ? `<div class="text-muted small" style="margin-top:4px">${msg}</div>` : ''}
  `;
}

function renderSettings(el, status, cfg) {
  const s = cfg || {};
  const trainState = status.train_status || 'idle';
  const predictState = status.predict_status || 'idle';
  const evalState = status.ensemble_evaluated !== undefined ? (WeeklyState.ensembleEvalLastStatus || 'idle') : 'idle';
  const trainAllState = status.train_all_status || 'idle';
  const anyRunning = !!status.any_job_running;

  el.innerHTML = `
    <div class="settings-header">
      <div class="sh-title">Pengaturan & Operasi ML</div>
    </div>

    <div class="sog-card" style="border:1px solid #3b82f6;background:linear-gradient(180deg, rgba(59,130,246,0.08), transparent)">
      <div class="sog-title">🚀 Perbarui Semua (Satu Tombol)</div>
      <div class="sog-desc">
        Melatih ulang kelima model, membacktest kombinasi ensemble-nya, lalu langsung menghasilkan sinyal terbaru —
        semua berurutan dan otomatis, jadi threshold yang dipakai selalu sudah tervalidasi untuk kombinasi model yang
        aktif saat itu. Ini yang sebaiknya dipakai sehari-hari; tidak perlu memilih model satu-satu.
        Memakai <strong>seluruh riwayat harian sejak 2020</strong> (bukan sampel) untuk setiap model.
        Estimasi <strong>25-45 menit</strong>.
      </div>
      <div class="sog-status ${trainAllState === 'running' ? 'status-running' : trainAllState === 'success' ? 'status-ok' : ''}">
        Status: <strong>${trainAllState}</strong>
      </div>
      <button class="btn-primary sog-btn" id="weekly-train-all-btn"
        onclick="triggerTrainAll()"
        ${anyRunning ? 'disabled' : ''}>
        ${trainAllState === 'running' ? '⏳ Sedang berjalan...' : '▶ Perbarui Semua Sekarang'}
      </button>
      <div style="margin-top:8px;background:#0f172a;border-radius:6px;height:14px;overflow:hidden;border:1px solid #1e293b">
        <div id="weekly-train-all-progress"
          style="height:100%;background:linear-gradient(90deg,#3b82f6,#6366f1);border-radius:6px;transition:width 0.5s;width:${trainAllState === 'running' ? (status.train_all_progress || 0) : (trainAllState === 'success' ? 100 : 0)}%">
        </div>
      </div>
      <div id="weekly-train-all-log" class="sog-log">${status.train_all_message || ''}</div>
      ${anyRunning && trainAllState !== 'running' ? renderOtherJobBanner(status) : ''}
    </div>

    <details class="settings-advanced" ${trainAllState === 'running' ? '' : ''} style="margin-top:16px">
      <summary style="cursor:pointer;color:var(--text-muted,#94a3b8);font-size:13px;padding:8px 0">⚙️ Kontrol manual per langkah (lanjutan)</summary>

      <div class="settings-ops-grid" style="margin-top:12px">
        <div class="sog-card">
          <div class="sog-title">🧠 Latih Satu Model</div>
          <div class="sog-desc">
            Melatih ulang satu algoritma saja (mis. setelah eksperimen). Membangun dataset dari seluruh riwayat
            sejak 2020 lagi (tidak berbagi dengan "Perbarui Semua"), jadi tidak lebih cepat dari itu — estimasi
            15-25 menit. Gunakan "Perbarui Semua" untuk kebutuhan sehari-hari.
          </div>
          <div class="sog-model-select">
            <label>Model:</label>
            <select id="weekly-model-select">
              <option value="hist_gradient_boosting">HistGradientBoosting (Recommended)</option>
              <option value="random_forest">Random Forest</option>
              <option value="logistic">Logistic Regression (Fast)</option>
              <option value="gradient_boosting">Gradient Boosting (Classic — ⚠️ lambat/tidak scalable pada dataset penuh sejak 2020, bisa &gt;1 jam)</option>
              <option value="extra_trees">Extra Trees (Robust)</option>
              <option value="xgboost">XGBoost (Pro)</option>
            </select>
          </div>
          <div class="sog-status ${trainState === 'running' ? 'status-running' : trainState === 'success' ? 'status-ok' : ''}">
            Status: <strong>${trainState}</strong>
          </div>
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <button class="btn-primary sog-btn" id="weekly-train-btn"
              onclick="triggerTraining()"
              ${trainState === 'running' || anyRunning ? 'disabled' : ''}>
              ${trainState === 'running' ? '⏳ Training...' : '▶ Mulai Training'}
            </button>
            <button id="weekly-cancel-btn"
              onclick="cancelTraining()"
              style="display:${trainState === 'running' ? 'inline-block' : 'none'};padding:6px 14px;background:#7f1d1d;border:1px solid #991b1b;color:#fca5a5;border-radius:6px;cursor:pointer;font-size:12px">
              ⛔ Batalkan
            </button>
          </div>
          <div style="margin-top:8px;background:#0f172a;border-radius:6px;height:14px;overflow:hidden;border:1px solid #1e293b">
            <div id="weekly-train-progress"
              style="height:100%;background:linear-gradient(90deg,#3b82f6,#6366f1);border-radius:6px;transition:width 0.5s;width:${trainState === 'running' ? (WeeklyState?.status?.progress || 0) : (trainState === 'success' ? 100 : 0)}%;display:flex;align-items:center;justify-content:center;font-size:9px;color:white;font-weight:bold">
            </div>
          </div>
          <div id="weekly-train-log" class="sog-log"></div>
        </div>

        <div class="sog-card">
          <div class="sog-title">🔮 Generate Prediksi</div>
          <div class="sog-desc">
            Prediksi <strong>selalu otomatis memakai data harga terbaru yang sudah tersinkron</strong>
            (saat ini: <strong>${status.source_max_date || '—'}</strong>) — kolom tanggal di bawah hanya untuk
            keperluan backfill/uji tanggal lampau. Jika Anda memilih tanggal yang datanya belum ada
            (mis. hari ini sebelum sinkronisasi harian selesai), sistem otomatis mundur ke tanggal data
            terakhir yang valid, bukan diam-diam memakai harga lama di bawah label tanggal baru.
            Threshold ensemble juga divalidasi ulang otomatis jika kombinasi model berubah.
          </div>
          <div class="sog-date-select">
            <label>Tanggal Backfill (opsional):</label>
            <input type="date" id="weekly-pred-date"
              value="${status.source_max_date || new Date().toISOString().split('T')[0]}"
              max="${status.source_max_date || ''}">
          </div>
          <div class="sog-status ${predictState === 'running' ? 'status-running' : predictState === 'success' ? 'status-ok' : ''}">
            Status: <strong>${predictState}</strong>
          </div>
          <button class="btn-primary sog-btn" id="weekly-predict-btn"
            onclick="triggerPredict()"
            ${predictState === 'running' || anyRunning || !status.model_active ? 'disabled' : ''}>
            ${predictState === 'running' ? '⏳ Prediksi...' : status.model_active ? '▶ Generate Prediksi (data terbaru)' : '⚠ Perlu Training Dulu'}
          </button>
          <div id="weekly-predict-log" class="sog-log"></div>
        </div>

        <div class="sog-card">
          <div class="sog-title">🧪 Evaluasi Ensemble</div>
          <div class="sog-desc">
            Backtest KOMBINASI seluruh model ensemble (bukan satu model saja) pada data validasi &amp; holdout,
            lalu kunci threshold HIGH_CONFIDENCE berdasarkan hasil nyata kombinasi tersebut. "Generate Prediksi"
            kini menjalankan ini otomatis bila perlu, jadi langkah manual ini hanya untuk melihat hasilnya kapan saja.
          </div>
          <div class="sog-status">
            Status: <strong id="weekly-ensemble-eval-status">${status.ensemble_evaluated ? 'sudah dievaluasi' : (status.ensemble_stale ? 'usang (model berubah)' : 'belum dievaluasi')}</strong>
          </div>
          <button class="btn-primary sog-btn" id="weekly-ensemble-eval-btn"
            onclick="triggerEnsembleEval()"
            ${(status.ensemble_count || 0) < 2 || anyRunning ? 'disabled' : ''}>
            ▶ Evaluasi Ensemble
          </button>
          <div id="weekly-ensemble-eval-log" class="sog-log"></div>
        </div>
      </div>
    </details>

    <div class="settings-params">
      <div class="sp-title">⚙️ Parameter Model</div>
      <div class="sp-desc" style="font-size:12px;color:var(--text-muted,#94a3b8);margin-bottom:10px">
        Parameter ini memengaruhi bagaimana model dilatih dan disaring — <strong>ubah hanya jika Anda paham dampaknya</strong>,
        lalu jalankan "Perbarui Semua" agar model dilatih ulang memakai nilai baru (mengubah angka di sini saja tidak
        otomatis melatih ulang model).
      </div>
      <div class="sp-grid">
        ${renderSettingField('Horizon Prediksi (hari)', 'horizon_days', s.horizon_days, {
          min: '1', max: '20', step: '1',
          help: 'Sinyal dibuat untuk N hari bursa ke depan (5 ≈ 1 minggu). Mengubah ini mengubah definisi target — wajib latih ulang semua model setelahnya.'
        })}
        ${renderSettingField('Target Return (%)', 'primary_return_target_pct', (s.primary_return_target||0)*100, {
          min: '0.5', max: '10', step: '0.1',
          help: 'Kenaikan harga minimum dari harga entry agar suatu hari dilabeli "bullish" saat melatih model. Makin tinggi, makin ketat definisi sinyal yang benar.'
        })}
        ${renderSettingField('Take Profit (%)', 'take_profit_pct', (s.take_profit||0)*100, {
          min: '0.5', max: '20', step: '0.1',
          help: 'Level take-profit yang ditampilkan pada setiap sinyal. Hanya memengaruhi tampilan TP di tabel, tidak memengaruhi cara model dilatih.'
        })}
        ${renderSettingField('Stop Loss (%)', 'stop_loss_pct', Math.abs(s.stop_loss||0)*100, {
          min: '0.5', max: '20', step: '0.1',
          help: 'Level stop-loss (batas potong rugi) yang ditampilkan pada setiap sinyal.'
        })}
        ${renderSettingField('Min Value 20d (Miliar IDR)', 'min_val_b', (s.min_median_value_20d||0)/1e9, {
          min: '0.05', max: '10', step: '0.05',
          help: 'Saham dengan rata-rata nilai transaksi 20 hari di bawah angka ini disingkirkan dari daftar — mencegah sinyal muncul di saham yang susah dieksekusi (tidak likuid).'
        })}
        ${renderSettingField('Min Harga Saham', 'min_price', s.min_price, {
          min: '1', max: '500', step: '1',
          help: 'Saham dengan harga di bawah angka ini disingkirkan — menyaring saham "gocap"/penny stock yang rawan pergerakan tidak wajar.'
        })}
        ${renderSettingField('Threshold HC (%)', 'high_confidence_threshold_pct', (s.high_confidence_threshold||0.85)*100, {
          min: '50', max: '99', step: '0.5',
          help: 'Ambang cadangan sebelum "Evaluasi Ensemble" pernah berhasil jalan. Setelah evaluasi ensemble ada, sinyal live memakai threshold hasil backtest asli, bukan angka ini — jadi nilai ini jarang berpengaruh langsung.'
        })}
        ${renderSettingField('Target Precision (%)', 'precision_target_pct', (s.precision_target||0.9)*100, {
          min: '50', max: '99', step: '0.5',
          help: 'Target presisi yang dikejar saat memilih threshold dari data validasi. Makin tinggi target ini, makin sedikit sinyal yang lolos karena model harus makin yakin.'
        })}
      </div>
      <button class="btn-secondary" onclick="saveWeeklySettings()">💾 Simpan Pengaturan</button>
      <div id="weekly-settings-msg" class="settings-msg"></div>
    </div>
  `;
}

function renderSettingField(label, id, value, opts = {}) {
  const { type = 'number', min = '', max = '', step = '1', help = '' } = opts;
  return `
    <div class="sp-field">
      <label for="wsp-${id}">${label}</label>
      <input type="${type}" id="wsp-${id}" value="${value ?? ''}"
        min="${min}" max="${max}" step="${step}" class="sp-input">
      ${help ? `<div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 4px;">${help}</div>` : ''}
    </div>
  `;
}

async function triggerTraining() {
  const modelName = document.getElementById('weekly-model-select')?.value || 'hist_gradient_boosting';
  const btn = document.getElementById('weekly-train-btn');
  const logEl = document.getElementById('weekly-train-log');

  if (btn) btn.disabled = true;
  if (logEl) logEl.innerHTML = '<span style="color:#94a3b8">⏳ Mengirim perintah training...</span>';

  try {
    const res = await fetch('/api/weekly/train', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model_name: modelName }),
    });
    const data = await res.json();

    if (!data.ok) {
      if (logEl) logEl.innerHTML = `<span style="color:#f87171">❌ ${data.message}</span>`;
      if (btn) btn.disabled = false;
      return;
    }

    if (logEl) logEl.innerHTML = '<span style="color:#34d399">▶ Training dimulai (membangun dataset dari seluruh riwayat sejak 2020, ~15-25 menit)...</span>';

    // Show cancel button
    const cancelBtn = document.getElementById('weekly-cancel-btn');
    if (cancelBtn) cancelBtn.style.display = 'inline-block';

    // Poll for progress
    if (WeeklyState.trainPolling) clearInterval(WeeklyState.trainPolling);
    WeeklyState.trainPolling = setInterval(async () => {
      try {
        const statusRes = await fetch('/api/weekly/train/status');
        const s = await statusRes.json();
        const pct = s.progress || 0;
        const msg = s.message || '';

        // Update progress bar
        const bar = document.getElementById('weekly-train-progress');
        if (bar) {
          bar.style.width = pct + '%';
          bar.textContent = pct + '%';
        }

        if (logEl) {
          const color = s.status === 'failed' ? '#f87171' : s.status === 'success' ? '#34d399' : '#94a3b8';
          logEl.innerHTML = `<span style="color:${color}">${msg}</span>`;
        }

        if (s.status === 'success' || s.status === 'failed' || s.status === 'idle') {
          clearInterval(WeeklyState.trainPolling);
          WeeklyState.trainPolling = null;
          if (btn) { btn.disabled = false; btn.textContent = '▶ Mulai Training'; }
          if (cancelBtn) cancelBtn.style.display = 'none';

          if (s.status === 'success') {
            if (logEl) logEl.innerHTML = `<span style="color:#34d399">✅ ${msg}</span>`;
            setTimeout(() => { loadWeeklyStatus(); loadWeeklySignals(); }, 1000);
          } else if (s.status === 'idle') {
            if (logEl) logEl.innerHTML = `<span style="color:#fbbf24">⚠ ${msg}</span>`;
          } else {
            if (logEl) logEl.innerHTML = `<span style="color:#f87171">❌ ${msg}</span>`;
          }
        }
      } catch (e) {
        console.warn('Poll error:', e);
      }
    }, 2000);

  } catch (e) {
    if (logEl) logEl.innerHTML = `<span style="color:#f87171">Error: ${e.message}</span>`;
    if (btn) btn.disabled = false;
  }
}

async function cancelTraining() {
  try {
    const res = await fetch('/api/weekly/train/cancel', { method: 'POST' });
    const data = await res.json();
    const logEl = document.getElementById('weekly-train-log');
    if (logEl) logEl.innerHTML = `<span style="color:#fbbf24">⚠ ${data.message}</span>`;
  } catch (e) {
    console.warn('Cancel error:', e);
  }
}

async function triggerEnsembleEval() {
  const btn = document.getElementById('weekly-ensemble-eval-btn');
  const logEl = document.getElementById('weekly-ensemble-eval-log');

  if (btn) btn.disabled = true;
  if (logEl) logEl.innerHTML = '<span style="color:#94a3b8">⏳ Mengirim perintah evaluasi...</span>';

  try {
    const res = await fetch('/api/weekly/ensemble/evaluate', { method: 'POST' });
    const data = await res.json();

    if (!data.ok) {
      if (logEl) logEl.innerHTML = `<span style="color:#f87171">❌ ${data.message}</span>`;
      if (btn) btn.disabled = false;
      return;
    }

    if (logEl) logEl.innerHTML = '<span style="color:#34d399">▶ Evaluasi ensemble dimulai (membangun dataset dari seluruh riwayat sejak 2020, ~12-18 menit)...</span>';

    if (WeeklyState.ensembleEvalPolling) clearInterval(WeeklyState.ensembleEvalPolling);
    WeeklyState.ensembleEvalPolling = setInterval(async () => {
      try {
        const statusRes = await fetch('/api/weekly/ensemble/evaluate/status');
        const s = await statusRes.json();
        const msg = s.message || '';
        if (logEl) {
          const color = s.status === 'failed' ? '#f87171' : s.status === 'success' ? '#34d399' : '#94a3b8';
          logEl.innerHTML = `<span style="color:${color}">${msg}</span>`;
        }
        if (s.status === 'success' || s.status === 'failed' || s.status === 'idle') {
          clearInterval(WeeklyState.ensembleEvalPolling);
          WeeklyState.ensembleEvalPolling = null;
          if (btn) btn.disabled = false;
          if (s.status === 'success') {
            setTimeout(() => { loadWeeklyStatus(); loadWeeklySignals(); }, 1000);
          }
        }
      } catch (e) {
        console.warn('Poll error:', e);
      }
    }, 3000);
  } catch (e) {
    if (logEl) logEl.innerHTML = `<span style="color:#f87171">Error: ${e.message}</span>`;
    if (btn) btn.disabled = false;
  }
}



async function triggerTrainAll() {
  const btn = document.getElementById('weekly-train-all-btn');
  const logEl = document.getElementById('weekly-train-all-log');
  const bar = document.getElementById('weekly-train-all-progress');

  if (btn) btn.disabled = true;
  if (logEl) logEl.innerHTML = '<span style="color:#94a3b8">⏳ Mengirim perintah...</span>';

  try {
    const res = await fetch('/api/weekly/train-all', { method: 'POST' });
    const data = await res.json();

    if (!data.ok) {
      if (logEl) logEl.innerHTML = `<span style="color:#f87171">❌ ${data.message}</span>`;
      if (btn) btn.disabled = false;
      return;
    }

    if (logEl) logEl.innerHTML = '<span style="color:#34d399">▶ Pipeline lengkap dimulai (seluruh riwayat sejak 2020, ~25-45 menit)...</span>';

    if (WeeklyState.trainAllPolling) clearInterval(WeeklyState.trainAllPolling);
    WeeklyState.trainAllPolling = setInterval(async () => {
      try {
        const statusRes = await fetch('/api/weekly/train-all/status');
        const s = await statusRes.json();
        const pct = s.progress || 0;
        const msg = s.message || '';

        if (bar) bar.style.width = pct + '%';
        if (logEl) {
          const color = s.status === 'failed' ? '#f87171' : s.status === 'success' ? '#34d399' : '#94a3b8';
          logEl.innerHTML = `<span style="color:${color}">${msg}</span>`;
        }

        if (s.status === 'success' || s.status === 'failed' || s.status === 'idle') {
          clearInterval(WeeklyState.trainAllPolling);
          WeeklyState.trainAllPolling = null;
          if (btn) btn.disabled = false;
          if (s.status === 'success') {
            setTimeout(() => { loadWeeklyStatus(); loadWeeklySettings(); }, 1000);
          }
        }
      } catch (e) {
        console.warn('Poll error:', e);
      }
    }, 3000);
  } catch (e) {
    if (logEl) logEl.innerHTML = `<span style="color:#f87171">Error: ${e.message}</span>`;
    if (btn) btn.disabled = false;
  }
}

async function triggerPredict() {
  const predDate = document.getElementById('weekly-pred-date')?.value || new Date().toISOString().split('T')[0];
  const btn = document.getElementById('weekly-predict-btn');
  const log = document.getElementById('weekly-predict-log');

  if (btn) btn.disabled = true;
  if (log) log.textContent = 'Memulai prediksi...';

  try {
    const res = await fetch('/api/weekly/predict', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ date: predDate }),
    });
    const data = await res.json();
    if (log) log.textContent = data.message || 'Prediksi dimulai';

    // Poll
    if (WeeklyState.predictPolling) clearInterval(WeeklyState.predictPolling);
    WeeklyState.predictPolling = setInterval(async () => {
      try {
        const statusRes = await fetch('/api/weekly/predict/status');
        const s = await statusRes.json();
        if (log) log.textContent = s.message || '';
        if (s.status === 'success' || s.status === 'failed') {
          clearInterval(WeeklyState.predictPolling);
          WeeklyState.predictPolling = null;
          if (btn) { btn.disabled = false; btn.textContent = '▶ Generate Prediksi'; }
          if (s.status === 'success') {
            if (log) log.textContent = '✅ ' + s.message;
            WeeklyState.signalDate = predDate;
          } else {
            if (log) log.textContent = '❌ ' + s.message;
          }
        }
      } catch (e) {}
    }, 2000);

  } catch (e) {
    if (log) log.textContent = 'Error: ' + e.message;
    if (btn) btn.disabled = false;
  }
}

async function saveWeeklySettings() {
  const msgEl = document.getElementById('weekly-settings-msg');
  const getValue = id => {
    const el = document.getElementById(`wsp-${id}`);
    return el ? parseFloat(el.value) : null;
  };

  const settings = {
    horizon_days: getValue('horizon_days'),
    primary_return_target: getValue('primary_return_target_pct') / 100,
    take_profit: getValue('take_profit_pct') / 100,
    stop_loss: -(getValue('stop_loss_pct') / 100),
    min_median_value_20d: getValue('min_val_b') * 1e9,
    min_price: getValue('min_price'),
    high_confidence_threshold: getValue('high_confidence_threshold_pct') / 100,
    precision_target: getValue('precision_target_pct') / 100,
  };

  try {
    const res = await fetch('/api/weekly/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    });
    const data = await res.json();
    if (msgEl) {
      msgEl.className = 'settings-msg success';
      msgEl.textContent = data.ok ? '✅ Pengaturan tersimpan.' : '❌ ' + (data.error || 'Error');
      setTimeout(() => { if (msgEl) msgEl.textContent = ''; }, 3000);
    }
  } catch (e) {
    if (msgEl) { msgEl.className = 'settings-msg error'; msgEl.textContent = 'Error: ' + e.message; }
  }
}

// ── Helpers ────────────────────────────────────────────────────────────────
function fmtPrice(v) {
  if (v === null || v === undefined) return '—';
  if (v >= 1000) return 'Rp ' + Math.round(v).toLocaleString('id-ID');
  return 'Rp ' + v;
}

function fmtPct(v) {
  if (v === null || v === undefined) return '—';
  return (v * 100).toFixed(1) + '%';
}
