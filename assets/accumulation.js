(() => {
  'use strict';

  // Helper elements selector
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];

  const numberFormat = new Intl.NumberFormat('id-ID');
  const compactFormat = new Intl.NumberFormat('id-ID', { notation: 'compact', maximumFractionDigits: 2 });

  const fmt = (value) => numberFormat.format(Number(value || 0));
  const compact = (value) => compactFormat.format(Number(value || 0));
  const percent = (value, digits = 2) => `${Number(value || 0).toLocaleString('id-ID', { minimumFractionDigits: digits, maximumFractionDigits: digits })}%`;
  const esc = (value = '') => String(value).replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const shortDate = (value) => value ? new Date(`${value.slice(0, 10)}T00:00:00`).toLocaleDateString('id-ID', { day: '2-digit', month: 'short', year: 'numeric' }) : '—';

  // Local state
  const state = {
    viewMode: 'all', // 'all' or 'stock'
    dateFrom: null,   // set by DRP
    dateTo:   null,   // set by DRP
    stockCode: 'BBCA',
    searchQuery: '',
    sortColumn: 'value',
    sortDirection: 'desc',
    currentPage: 1,
    pageSize: 20,
    allData: [],
    stockData: null,
    loading: false
  };

  // DRP instance (shared)
  let accDrp = null;

  // API Client helper
  async function apiRequest(path) {
    const response = await fetch(path, { cache: 'no-store' });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `Permintaan gagal (${response.status}).`);
    return payload;
  }

  // Load All Stocks Accumulation
  async function loadAllAccumulation(df, dt) {
    if (df) state.dateFrom = df;
    if (dt) state.dateTo   = dt;
    state.loading = true;
    renderLoading('#accumulationAllBody', 13);
    try {
      const params = new URLSearchParams();
      if (state.dateFrom) params.set('date_from', state.dateFrom);
      if (state.dateTo)   params.set('date_to',   state.dateTo);
      const payload = await apiRequest(`/api/analytics/accumulation?${params}`);
      state.allData = payload.items || [];
      state.currentPage = 1;
      applyFilterAndSort();
    } catch (error) {
      console.error(error);
      showError('#accumulationAllBody', 13, error.message);
    } finally {
      state.loading = false;
    }
  }

  // Load Single Stock Accumulation
  async function loadStockAccumulation(df, dt) {
    if (df) state.dateFrom = df;
    if (dt) state.dateTo   = dt;
    state.loading = true;
    renderLoading('#accumulationStockBody', 14);
    try {
      const code = state.stockCode.trim().toUpperCase();
      if (!code) throw new Error('Silakan masukkan kode saham.');
      const params = new URLSearchParams({ code });
      if (state.dateFrom) params.set('date_from', state.dateFrom);
      if (state.dateTo)   params.set('date_to',   state.dateTo);
      const payload = await apiRequest(`/api/analytics/accumulation?${params}`);
      state.stockData = payload;
      renderStockDetail();
    } catch (error) {
      console.error(error);
      showError('#accumulationStockBody', 14, error.message);
      // Reset profile labels
      $('#accumulationStockTicker').textContent = '—';
      $('#accumulationStockBoard').textContent = '—';
      $('#accumulationStockCompany').textContent = '—';
      $('#accumulationStockNote').textContent = '—';
      $('#accumulationStockTotalNet').textContent = '—';
      $('#accumulationStockAdvice').textContent = '—';
      $('#accumulationStockChart').innerHTML = `<div class="chart-empty">${esc(error.message)}</div>`;
    } finally {
      state.loading = false;
    }
  }

  function renderLoading(selector, colspan) {
    $(selector).innerHTML = `<tr><td colspan="${colspan}" class="loading-cell">
      <div class="analysis-loading"><span></span><b>Memuat data akumulasi…</b></div>
    </td></tr>`;
  }

  function showError(selector, colspan, message) {
    $(selector).innerHTML = `<tr><td colspan="${colspan}" class="loading-cell negative">
      <b>Gagal memuat data</b><br><small>${esc(message)}</small>
    </td></tr>`;
  }

  // Apply search filtering and column sorting
  function applyFilterAndSort() {
    const q = state.searchQuery.trim().toUpperCase();
    let filtered = [...state.allData];
    if (q) {
      filtered = filtered.filter(item => 
        (item.code || '').toUpperCase().includes(q) || 
        (item.name || '').toUpperCase().includes(q)
      );
    }

    // Sort
    const col = state.sortColumn;
    const dir = state.sortDirection === 'asc' ? 1 : -1;
    filtered.sort((a, b) => {
      let valA = a[col];
      let valB = b[col];

      // Handle string comparison for code/name
      if (typeof valA === 'string') {
        return valA.localeCompare(valB) * dir;
      }

      valA = valA === null || valA === undefined ? -Infinity : valA;
      valB = valB === null || valB === undefined ? -Infinity : valB;
      return (valA - valB) * dir;
    });

    renderAllTable(filtered);
  }

  // Render All Stocks Table
  function renderAllTable(items) {
    const totalItems = items.length;
    const pageCount = Math.max(1, Math.ceil(totalItems / state.pageSize));
    state.currentPage = Math.min(state.currentPage, pageCount);
    const start = (state.currentPage - 1) * state.pageSize;
    const pageData = items.slice(start, start + state.pageSize);

    // Update Sorting Headers visually
    $$('#accumulationView th[data-sort]').forEach(th => {
      const col = th.dataset.sort;
      let text = th.textContent.replace(/[↕▲▼]/g, '').trim();
      if (col === state.sortColumn) {
        text += state.sortDirection === 'asc' ? ' ▲' : ' ▼';
      } else {
        text += ' ↕';
      }
      th.textContent = text;
    });

    const tbody = $('#accumulationAllBody');
    if (!pageData.length) {
      tbody.innerHTML = `<tr><td colspan="13" class="loading-cell">Data tidak ditemukan.</td></tr>`;
      $('#accumulationAllPageInfo').textContent = `Menampilkan 0–0 dari 0 data`;
      $('#accumulationAllPrev').disabled = true;
      $('#accumulationAllNext').disabled = true;
      return;
    }

    tbody.innerHTML = pageData.map(row => {
      const retClass = row.change_pct > 0 ? 'positive' : row.change_pct < 0 ? 'negative' : 'neutral';
      const foreignClass = row.net_foreign > 0 ? 'positive' : row.net_foreign < 0 ? 'negative' : 'neutral';
      return `
        <tr>
          <td><button class="ticker ticker-link" data-view-ticker="${esc(row.code)}">${esc(row.code)}</button></td>
          <td><span class="cell-main" title="${esc(row.name)}">${esc(row.name)}</span></td>
          <td class="number">${fmt(row.active_days)}</td>
          <td class="number">${fmt(row.start_price)}</td>
          <td class="number">${fmt(row.end_price)}</td>
          <td class="number"><b class="${retClass}">${percent(row.change_pct)}</b></td>
          <td class="number">${compact(row.volume)}</td>
          <td class="number">${compact(row.value)}</td>
          <td class="number"><b class="${foreignClass}">${compact(row.net_foreign)}</b></td>
          <td class="number"><b class="${foreignClass}">${compact(row.net_foreign_value_est)}</b></td>
          <td class="number">${fmt(row.vwap)}</td>
          <td class="number">${fmt(row.low)}</td>
          <td class="number">${fmt(row.high)}</td>
        </tr>
      `;
    }).join('');

    const end = Math.min(start + state.pageSize, totalItems);
    $('#accumulationAllPageInfo').textContent = `Menampilkan ${fmt(start + 1)}–${fmt(end)} dari ${fmt(totalItems)} data`;
    $('#accumulationAllPageNumber').textContent = `${state.currentPage} / ${pageCount}`;
    $('#accumulationAllPrev').disabled = state.currentPage === 1;
    $('#accumulationAllNext').disabled = state.currentPage === pageCount;
  }

  // Render Single Stock Details and Breakdown Table
  function renderStockDetail() {
    if (!state.stockData) return;
    const { profile, items } = state.stockData;

    // Header Profile
    $('#accumulationStockTicker').textContent = esc(profile.code);
    $('#accumulationStockBoard').textContent = esc(profile.listing_board || '—');
    $('#accumulationStockBoard').className = `board-badge ${esc(profile.listing_board)}`;
    $('#accumulationStockCompany').textContent = esc(profile.company_name);
    
    const listingStr = profile.listing_date ? `Listing: ${shortDate(profile.listing_date)} · ` : '';
    const sharesStr = profile.shares ? `Beredar: ${compact(profile.shares)} lembar` : '';
    $('#accumulationStockNote').textContent = `${listingStr}${sharesStr}`;

    // Total Net Foreign Accumulation over all breakdown periods combined
    const totalNetForeign = items.reduce((sum, item) => sum + (item.net_foreign || 0), 0);
    const totalNetValEst = items.reduce((sum, item) => sum + (item.net_foreign_value_est || 0), 0);
    
    const netClass = totalNetForeign > 0 ? 'positive' : totalNetForeign < 0 ? 'negative' : 'neutral';
    $('#accumulationStockTotalNet').textContent = compact(totalNetForeign);
    $('#accumulationStockTotalNet').className = netClass;
    $('#accumulationStockTotalNet').title = `Est. Nilai: Rp ${fmt(totalNetValEst)}`;

    // Textual advice based on net foreign flow
    let advice = 'Netral';
    if (totalNetForeign > 0) {
      advice = 'Terjadi Akumulasi Asing';
    } else if (totalNetForeign < 0) {
      advice = 'Terjadi Distribusi Asing';
    }
    $('#accumulationStockAdvice').textContent = advice;
    $('#accumulationStockAdvice').className = netClass;

    // Table Title
    const periodLabel = (state.dateFrom && state.dateTo) ? `${state.dateFrom} sd ${state.dateTo}` : 'Semua Waktu';
    $('#accumulationStockTableTitle').textContent = `Breakdown Transaksi ${esc(profile.code)} (${esc(periodLabel)})`;

    // Breakdown Table Body
    const tbody = $('#accumulationStockBody');
    if (!items.length) {
      tbody.innerHTML = `<tr><td colspan="14" class="loading-cell">Riwayat transaksi kosong.</td></tr>`;
      $('#accumulationStockChart').innerHTML = `<div class="chart-empty">Riwayat kosong.</div>`;
      return;
    }

    tbody.innerHTML = items.map(row => {
      const retClass = row.change_pct > 0 ? 'positive' : row.change_pct < 0 ? 'negative' : 'neutral';
      const foreignClass = row.net_foreign > 0 ? 'positive' : row.net_foreign < 0 ? 'negative' : 'neutral';
      return `
        <tr>
          <td><b>${esc(row.period_key)}</b></td>
          <td>${shortDate(row.start_date)}</td>
          <td>${shortDate(row.end_date)}</td>
          <td class="number">${fmt(row.active_days)}</td>
          <td class="number">${fmt(row.start_price)}</td>
          <td class="number">${fmt(row.end_price)}</td>
          <td class="number"><b class="${retClass}">${percent(row.change_pct)}</b></td>
          <td class="number">${compact(row.volume)}</td>
          <td class="number">${compact(row.value)}</td>
          <td class="number"><b class="${foreignClass}">${compact(row.net_foreign)}</b></td>
          <td class="number"><b class="${foreignClass}">${compact(row.net_foreign_value_est)}</b></td>
          <td class="number">${fmt(row.vwap)}</td>
          <td class="number">${fmt(row.low)}</td>
          <td class="number">${fmt(row.high)}</td>
        </tr>
      `;
    }).join('');

    // Draw SVG Chart
    drawStockChart(items);
  }

  // Draw Dual-Axis SVG Chart (Price Line + Net Foreign Bars)
  function drawStockChart(items) {
    const container = $('#accumulationStockChart');
    if (!items || !items.length) {
      container.innerHTML = `<div class="chart-empty">Data tidak mencukupi untuk membuat grafik.</div>`;
      return;
    }

    // Clone and reverse items to display chronologically (past to present, left to right)
    const sortedItems = [...items].reverse();

    const width = container.clientWidth || 800;
    const height = 220;
    const pad = { top: 20, right: 60, bottom: 30, left: 60 };

    const chartW = width - pad.left - pad.right;
    const chartH = height - pad.top - pad.bottom;

    // Price scaling variables
    const prices = sortedItems.flatMap(d => [d.end_price, d.vwap]).filter(v => Number.isFinite(v));
    const minPrice = Math.min(...prices) * 0.98;
    const maxPrice = Math.max(...prices) * 1.02;
    const priceRange = maxPrice - minPrice || 1;

    // Net Foreign scaling variables
    const netForeigns = sortedItems.map(d => d.net_foreign || 0);
    const maxAbsForeign = Math.max(...netForeigns.map(Math.abs)) || 1;

    // Helper functions for coordinates
    const getX = (idx) => pad.left + (idx / Math.max(1, sortedItems.length - 1)) * chartW;
    const getYPrice = (val) => pad.top + chartH - ((val - minPrice) / priceRange) * chartH;
    const getYForeign = (val) => pad.top + (chartH / 2) - (val / maxAbsForeign) * (chartH / 2);

    // Build SVG Grid lines
    let gridLines = '';
    const yGridSteps = 4;
    for (let i = 0; i <= yGridSteps; i++) {
      const yVal = minPrice + (i / yGridSteps) * priceRange;
      const yPos = getYPrice(yVal);
      gridLines += `<line x1="${pad.left}" y1="${yPos}" x2="${width - pad.right}" y2="${yPos}" stroke="#edf0f4" stroke-width="1"/>`;
      // Left Y-axis label (Price)
      gridLines += `<text x="${pad.left - 8}" y="${yPos + 3}" text-anchor="end" class="svg-axis" style="font-size: 8px;">${fmt(Math.round(yVal))}</text>`;
    }

    // Right Y-axis label (Foreign Flow)
    const yForeignSteps = [-maxAbsForeign, 0, maxAbsForeign];
    yForeignSteps.forEach(fVal => {
      const yPos = getYForeign(fVal);
      gridLines += `<line x1="${pad.left}" y1="${yPos}" x2="${width - pad.right}" y2="${yPos}" stroke="#cbd5e1" stroke-width="0.5" stroke-dasharray="2 2"/>`;
      gridLines += `<text x="${width - pad.right + 8}" y="${yPos + 3}" text-anchor="start" class="svg-axis" style="font-size: 8px; fill: ${fVal > 0 ? '#169768' : fVal < 0 ? '#d94355' : '#64748b'};">${compact(fVal)}</text>`;
    });

    // Draw Bars for Net Foreign
    let barsHtml = '';
    const barWidth = Math.max(2, Math.min(25, (chartW / sortedItems.length) * 0.4));
    sortedItems.forEach((d, index) => {
      const val = d.net_foreign || 0;
      const xPos = getX(index) - barWidth / 2;
      const yZero = getYForeign(0);
      const yVal = getYForeign(val);

      const color = val > 0 ? '#169768' : val < 0 ? '#d94355' : '#94a3b8';
      const opacity = 0.65;
      const y = Math.min(yZero, yVal);
      const h = Math.abs(yZero - yVal) || 1;

      barsHtml += `<rect x="${xPos}" y="${y}" width="${barWidth}" height="${h}" fill="${color}" fill-opacity="${opacity}" rx="1"/>`;
    });

    // Draw lines for Close Price and VWAP
    let closePath = '';
    let vwapPath = '';
    sortedItems.forEach((d, index) => {
      const xPos = getX(index);
      const yClose = getYPrice(d.end_price);
      const yVwap = getYPrice(d.vwap);

      closePath += `${index === 0 ? 'M' : 'L'}${xPos.toFixed(1)},${yClose.toFixed(1)} `;
      vwapPath += `${index === 0 ? 'M' : 'L'}${xPos.toFixed(1)},${yVwap.toFixed(1)} `;
    });

    const closeLine = `<path d="${closePath}" fill="none" stroke="#2f68e8" stroke-width="2" class="svg-series"/>`;
    const vwapLine = `<path d="${vwapPath}" fill="none" stroke="#d89422" stroke-dasharray="3 3" stroke-width="1.8"/>`;

    // Draw X-axis labels
    let xLabelsHtml = '';
    const labelInterval = Math.max(1, Math.ceil(sortedItems.length / 8));
    sortedItems.forEach((d, index) => {
      if (index % labelInterval === 0 || index === sortedItems.length - 1) {
        const xPos = getX(index);
        xLabelsHtml += `
          <line x1="${xPos}" y1="${pad.top + chartH}" x2="${xPos}" y2="${pad.top + chartH + 4}" stroke="#cbd5e1" stroke-width="1"/>
          <text x="${xPos}" y="${pad.top + chartH + 16}" text-anchor="middle" class="svg-axis" style="font-size: 8px;">${esc(d.period_key)}</text>
        `;
      }
    });

    container.innerHTML = `
      <svg viewBox="0 0 ${width} ${height}" style="width: 100%; height: auto; overflow: visible;">
        ${gridLines}
        ${barsHtml}
        ${closeLine}
        ${vwapLine}
        ${xLabelsHtml}
      </svg>
    `;
  }

  // Exports
  function exportCSV(title, headers, lines) {
    const csvContent = lines.map(line => line.map(cell => `"${String(cell ?? '').replace(/"/g, '""')}"`).join(',')).join('\r\n');
    const blob = new Blob([`\ufeff${csvContent}`], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${title}-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function handleAllExport() {
    const headers = ['Kode', 'Nama Emiten', 'Hari Aktif', 'Harga Awal', 'Harga Akhir', 'Return (%)', 'Volume Traded', 'Nilai Transaksi', 'Net Asing (S)', 'Est Net Value (Rp)', 'VWAP', 'Low Price', 'High Price'];
    const lines = [headers, ...state.allData.map(r => [
      r.code, r.name, r.active_days, r.start_price, r.end_price, r.change_pct, r.volume, r.value, r.net_foreign, r.net_foreign_value_est, r.vwap, r.low, r.high
    ])];
    const periodLabel = (state.dateFrom && state.dateTo) ? `${state.dateFrom}_${state.dateTo}` : 'semua_waktu';
    exportCSV(`akumulasi-seluruh-saham-${periodLabel}`, headers, lines);
  }

  function handleStockExport() {
    if (!state.stockData) return;
    const { profile, items } = state.stockData;
    const headers = ['Periode', 'Tanggal Mulai', 'Tanggal Selesai', 'Hari Aktif', 'Harga Awal', 'Harga Akhir', 'Return (%)', 'Volume Traded', 'Nilai Transaksi', 'Net Asing (S)', 'Est Net Value (Rp)', 'VWAP', 'Low Price', 'High Price'];
    const lines = [headers, ...items.map(r => [
      r.period_key, r.start_date, r.end_date, r.active_days, r.start_price, r.end_price, r.change_pct, r.volume, r.value, r.net_foreign, r.net_foreign_value_est, r.vwap, r.low, r.high
    ])];
    const periodLabel = (state.dateFrom && state.dateTo) ? `${state.dateFrom}_${state.dateTo}` : 'semua_waktu';
    exportCSV(`breakdown-akumulasi-${profile.code}-${periodLabel}`, headers, lines);
  }

  // Bind all interactive events
  function bindLocalEvents() {
    // Switch View mode tabs
    $('#accumulationTab').addEventListener('click', (event) => {
      const button = event.target.closest('button');
      if (!button) return;
      
      $$('#accumulationTab button').forEach(btn => btn.classList.remove('active'));
      button.classList.add('active');

      const tab = button.dataset.tab;
      state.viewMode = tab;

      if (tab === 'all') {
        $('#accumulationAllPanel').style.display = 'block';
        $('#accumulationStockPanel').style.display = 'none';
        $('#accumulationStockForm').style.display = 'none';
      } else {
        $('#accumulationAllPanel').style.display = 'none';
        $('#accumulationStockPanel').style.display = 'block';
        $('#accumulationStockForm').style.display = 'flex';
        // Load default stock if empty
        if (!state.stockData) {
          loadStockAccumulation();
        }
      }
    });


    // Period filter now handled by DRP (accDrp) — see initDrp()

    // Search input (Debounced)
    let searchTimer;
    $('#accumulationAllSearch').addEventListener('input', (event) => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        state.searchQuery = event.target.value;
        state.currentPage = 1;
        applyFilterAndSort();
      }, 250);
    });

    // Autocomplete select for Ticker
    $('#accumulationStockForm').addEventListener('submit', (event) => {
      event.preventDefault();
      const val = $('#accumulationTicker').value.trim().toUpperCase();
      if (!val) return;
      state.stockCode = val;
      $('#accumulationTicker').value = val;
      loadStockAccumulation();
    });

    // Auto-load saat user memilih dari dropdown datalist (tanpa perlu tekan Enter)
    $('#accumulationTicker').addEventListener('change', (event) => {
      const val = event.target.value.trim().toUpperCase();
      if (!val) return;
      // Hanya auto-load jika nilai berubah dari yang sedang tampil
      if (val !== state.stockCode) {
        state.stockCode = val;
        event.target.value = val;
        loadStockAccumulation();
      }
    });

    // Sorting columns click
    $$('#accumulationView th[data-sort]').forEach(th => {
      th.addEventListener('click', () => {
        const col = th.dataset.sort;
        if (state.sortColumn === col) {
          state.sortDirection = state.sortDirection === 'asc' ? 'desc' : 'asc';
        } else {
          state.sortColumn = col;
          state.sortDirection = 'desc'; // Default to desc
        }
        state.currentPage = 1;
        applyFilterAndSort();
      });
    });

    // Pagination All Stocks
    $('#accumulationAllPrev').addEventListener('click', () => {
      if (state.currentPage > 1) {
        state.currentPage--;
        applyFilterAndSort();
      }
    });

    $('#accumulationAllNext').addEventListener('click', () => {
      const pageCount = Math.ceil(state.allData.length / state.pageSize);
      if (state.currentPage < pageCount) {
        state.currentPage++;
        applyFilterAndSort();
      }
    });

    // Ticker link clicks inside table (redirects to stock breakdown)
    $('#accumulationAllBody').addEventListener('click', (event) => {
      const button = event.target.closest('[data-view-ticker]');
      if (!button) return;

      const code = button.dataset.viewTicker;
      state.stockCode = code;
      $('#accumulationTicker').value = code;

      // Switch to Stock tab
      const stockTabButton = $$('#accumulationTab button').find(btn => btn.dataset.tab === 'stock');
      if (stockTabButton) {
        stockTabButton.click();
      }
      loadStockAccumulation();
    });

    // CSV Exports
    $('#accumulationAllExportBtn').addEventListener('click', handleAllExport);
    $('#accumulationStockExportBtn').addEventListener('click', handleStockExport);
  }

  // Handle global events dispatched by app.js router
  function initRouterListener() {
    document.addEventListener('zaiden:viewchange', (event) => {
      const { view } = event.detail;
      if (view === 'accumulation') {
        if (!state.allData.length && !state.loading) {
          // DRP will trigger initial load via onApply
          // But if DRP already initialized (re-visit), reload
          if (state.dateFrom) loadAllAccumulation();
        }
      }
    });

    // Listen to window resize to redraw SVG chart responsively
    window.addEventListener('resize', () => {
      if (state.viewMode === 'stock' && state.stockData && state.stockData.items) {
        drawStockChart(state.stockData.items);
      }
    });
  }

  // Initialize DRP for accumulation
  function initDrp() {
    if (!window.ZaidenDRP) return;
    accDrp = window.ZaidenDRP.create({
      wrapId:  'accDrpWrap',
      btnId:   'accDrpBtn',
      labelId: 'accDrpLabel',
      defaultPreset: 3,  // 28 hari terakhir
    });

    // We'll init after we have trading dates from first API call
    // For now use today as latest and empty trading set
    const todayStr = new Date().toISOString().slice(0,10);

    accDrp.init(todayStr, [], (df, dt) => {
      state.dateFrom = df;
      state.dateTo   = dt;
      if (state.viewMode === 'all') {
        loadAllAccumulation(df, dt);
      } else {
        loadStockAccumulation(df, dt);
      }
    });

    // Trigger initial load with default range
    const r = accDrp.getRange();
    state.dateFrom = r.from;
    state.dateTo   = r.to;
    // Immediately load on first render
    if (state.dateFrom) loadAllAccumulation(state.dateFrom, state.dateTo);
  }

  // Entry point
  function main() {
    initDrp();
    bindLocalEvents();
    initRouterListener();
  }

  main();
})();
