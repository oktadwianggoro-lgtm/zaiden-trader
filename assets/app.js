(() => {
  'use strict';

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const numberFormat = new Intl.NumberFormat('id-ID');
  const compactFormat = new Intl.NumberFormat('id-ID', { notation: 'compact', maximumFractionDigits: 2 });
  const state = {
    view: 'dashboard', dashboardMonth: '', ownerPage: 1, ownerSize: 20,
    ownerFilters: { code: '', investor: '', month: '', origin: '' }, stockPage: 1, stockSize: 20,
    stockFilters: { q: '', board: '' },
    topChartPage: 1,
    topChartPages: 1,
    categoryPage: 1,
    categoryPages: 1
  };
  let ownership = [];
  let stocks = [];
  let databaseInfo = {};
  let toastTimer;

  const fmt = (value) => numberFormat.format(Number(value || 0));
  const compact = (value) => compactFormat.format(Number(value || 0));
  const percent = (value, digits = 2) => `${Number(value || 0).toLocaleString('id-ID', { minimumFractionDigits: digits, maximumFractionDigits: digits })}%`;
  const normalized = (value) => String(value || '').trim().toLocaleUpperCase('id-ID');
  const totalShares = (row) => Number(row.scripless || 0) + Number(row.scrip || 0);
  const monthOf = (row) => String(row.record_date || '').slice(0, 7);
  const esc = (value = '') => String(value).replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const dateLabel = (value) => value ? new Date(`${String(value).slice(0, 10)}T00:00:00`).toLocaleDateString('id-ID', { day: '2-digit', month: 'short', year: 'numeric' }) : '—';
  const monthLabel = (value) => value ? new Date(`${value}-01T00:00:00`).toLocaleDateString('id-ID', { month: 'long', year: 'numeric' }) : '—';

  function setBoot(text, progress, count = '') {
    $('#bootText').textContent = text;
    $('#bootBar').style.width = `${Math.max(4, Math.min(100, progress))}%`;
    if (count) $('#bootCount').textContent = count;
  }

  async function apiRequest(path, options = {}) {
    const response = await fetch(path, {
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `Permintaan gagal (${response.status}).`);
    return payload;
  }

  async function putRecord(storeName, record) {
    const collection = storeName === 'ownership' ? ownership : stocks;
    const base = `/api/${storeName}`;
    const saved = await apiRequest(record.id ? `${base}/${record.id}` : base, {
      method: record.id ? 'PUT' : 'POST',
      body: JSON.stringify(record)
    });
    const index = collection.findIndex((item) => item.id === saved.id);
    if (index >= 0) collection[index] = saved;
    else collection.push(saved);
    return saved;
  }

  async function deleteRecord(storeName, id) {
    await apiRequest(`/api/${storeName}/${id}`, { method: 'DELETE' });
    if (storeName === 'ownership') ownership = ownership.filter((row) => row.id !== id);
    else stocks = stocks.filter((row) => row.id !== id);
  }

  async function reloadCaches(loadFromServer = false) {
    if (loadFromServer) {
      const payload = await apiRequest('/api/bootstrap');
      ownership = payload.ownership || [];
      stocks = payload.stocks || [];
    }
    ownership.forEach((row) => { row.share_code = normalized(row.share_code); });
    stocks.forEach((row) => { row.code = normalized(row.code); });
    ownership.sort((a, b) => String(b.record_date).localeCompare(String(a.record_date)) || a.share_code.localeCompare(b.share_code) || String(a.investor_name).localeCompare(String(b.investor_name)));
    stocks.sort((a, b) => a.code.localeCompare(b.code));
  }

  function getMonths() {
    return [...new Set(ownership.map(monthOf).filter(Boolean))].sort().reverse();
  }

  function normalizeClass(value) {
    const raw = normalized(value);
    if (!raw) return 'Tidak diklasifikasikan';
    if (raw === 'ID' || raw.includes('INDIVIDUAL')) return 'Individual';
    if (raw === 'CP' || raw.includes('CORPORATE') || raw.includes('COMPANY')) return 'Corporate';
    if (raw === 'MF' || raw.includes('MUTUAL') || raw.includes('FUND')) return raw.includes('PENSION') ? 'Pension Fund' : 'Fund / Asset Manager';
    if (raw === 'IB' || raw.includes('FINANCIAL') || raw.includes('BANK')) return 'Financial Institution';
    if (raw === 'SC' || raw.includes('SECURIT') || raw.includes('INVESTMENT MANAGER')) return 'Securities / Investment';
    if (raw === 'IS' || raw.includes('INSURANCE')) return 'Insurance';
    if (raw === 'PF' || raw.includes('PENSION')) return 'Pension Fund';
    if (raw === 'FD' || raw.includes('FOUNDATION')) return 'Foundation';
    return String(value).trim();
  }

  function renderMonthOptions() {
    const months = getMonths();
    const dashboard = $('#dashboardMonth');
    const selectedDashboard = months.includes(state.dashboardMonth) ? state.dashboardMonth : (months[0] || '');
    dashboard.innerHTML = months.map((month) => `<option value="${month}">${esc(monthLabel(month))}</option>`).join('');
    dashboard.value = selectedDashboard;
    state.dashboardMonth = dashboard.value;
    const owner = $('#ownerMonth');
    const selectedOwner = state.ownerFilters.month;
    owner.innerHTML = '<option value="">Semua bulan</option>' + months.map((month) => `<option value="${month}">${esc(monthLabel(month))}</option>`).join('');
    owner.value = selectedOwner;
    $('#ownerCodeList').innerHTML = [...new Set(ownership.map((row) => row.share_code).filter(Boolean))].sort().map((code) => `<option value="${esc(code)}"></option>`).join('');
    $('#ownerInvestorList').innerHTML = [...new Set(ownership.map((row) => row.investor_name).filter(Boolean))].sort((a, b) => a.localeCompare(b)).map((name) => `<option value="${esc(name)}"></option>`).join('');
  }

  function dashboardRows() {
    const month = state.dashboardMonth || getMonths()[0] || '';
    return ownership.filter((row) => monthOf(row) === month);
  }

  function renderDashboard() {
    const rows = dashboardRows();
    const month = state.dashboardMonth;
    const marketShares = stocks.reduce((sum, row) => sum + Number(row.shares || 0), 0);
    const monitoredShares = rows.reduce((sum, row) => sum + totalShares(row), 0);
    const nonWarkatShares = rows.filter((row) => row.local_foreign === 'N').reduce((sum, row) => sum + totalShares(row), 0);
    const investors = new Set(rows.map((row) => normalized(row.investor_name)).filter(Boolean)).size;
    $('#metricOutstanding').textContent = compact(marketShares);
    $('#metricOutstanding').title = fmt(marketShares);
    $('#metricOutstandingNote').textContent = `${fmt(stocks.length)} emiten di master IDX`;
    $('#metricInvestors').textContent = fmt(investors);
    $('#metricInvestorsNote').textContent = `${fmt(rows.length)} posisi · ${monthLabel(month)}`;
    $('#metricNonWarkat').textContent = percent(marketShares ? nonWarkatShares / marketShares * 100 : 0);
    $('#metricNonWarkatNote').textContent = `${compact(nonWarkatShares)} lembar`;
    $('#metricRecorded').textContent = percent(marketShares ? monitoredShares / marketShares * 100 : 0);
    $('#metricRecordedNote').textContent = `${compact(monitoredShares)} lembar terpantau`;
    $('#topMonthLabel').textContent = monthLabel(month);
    renderTopChart(rows);
    renderOriginChart(rows);
    renderCategoryChart(rows);
    renderSnapshot(rows);
  }

  function renderTopChart(rows) {
    const pageSize = 10;
    const sorted = [...rows].sort((a, b) => totalShares(b) - totalShares(a));
    const pageCount = Math.max(1, Math.ceil(sorted.length / pageSize));
    state.topChartPages = pageCount;
    const currentPage = Math.max(1, Math.min(state.topChartPage, pageCount));
    state.topChartPage = currentPage;
    const start = (currentPage - 1) * pageSize;
    const top = sorted.slice(start, start + pageSize);
    const maximum = Math.max(1, ...top.map(totalShares));
    const topPager = $('#topPager');
    if (topPager) topPager.hidden = sorted.length <= pageSize;
    const topPageInfo = $('#topPageInfo');
    if (topPageInfo) topPageInfo.textContent = `${currentPage} / ${pageCount}`;
    const topPrev = $('#topPrevBtn');
    if (topPrev) topPrev.disabled = currentPage <= 1;
    const topNext = $('#topNextBtn');
    if (topNext) topNext.disabled = currentPage >= pageCount;
    $('#topChart').innerHTML = top.map((row) => `<div class="bar-row"><div class="bar-label" title="${esc(row.investor_name)}"><b>${esc(row.share_code)}</b>${esc(row.investor_name)}</div><div class="bar-track"><div class="bar-fill" style="width:${(totalShares(row) / maximum * 100).toFixed(2)}%"></div></div><div class="bar-value" title="${fmt(totalShares(row))}">${compact(totalShares(row))}</div></div>`).join('') || '<div class="empty-state"><b>Belum ada data</b></div>';
  }

  function renderOriginChart(rows) {
    const local = rows.filter((row) => row.local_foreign !== 'F').reduce((sum, row) => sum + totalShares(row), 0);
    const foreign = rows.filter((row) => row.local_foreign === 'F').reduce((sum, row) => sum + totalShares(row), 0);
    const total = local + foreign || 1;
    const localPct = local / total * 100;
    const foreignPct = foreign / total * 100;
    $('#originChart').innerHTML = `<div class="donut" style="background:conic-gradient(#2f68e8 0 ${localPct}%,#7b5bd7 ${localPct}% 100%)"><div class="donut-center"><b>${percent(foreignPct, 1)}</b><small>Asing</small></div></div><div class="legend"><div class="legend-row"><span style="--dot:#2f68e8">Lokal + non warkat</span><b>${percent(localPct)}</b><small>${compact(local)} lembar</small></div><div class="legend-row"><span style="--dot:#7b5bd7">Asing</span><b>${percent(foreignPct)}</b><small>${compact(foreign)} lembar</small></div></div>`;
  }

  function renderCategoryChart(rows) {
    const pageSize = 7;
    const groups = new Map();
    rows.forEach((row) => {
      const category = normalizeClass(row.classification);
      groups.set(category, (groups.get(category) || 0) + 1);
    });
    const sorted = [...groups].sort((a, b) => b[1] - a[1]);
    const pageCount = Math.max(1, Math.ceil(sorted.length / pageSize));
    state.categoryPages = pageCount;
    const currentPage = Math.max(1, Math.min(state.categoryPage, pageCount));
    state.categoryPage = currentPage;
    const start = (currentPage - 1) * pageSize;
    const list = sorted.slice(start, start + pageSize);
    const maximum = Math.max(1, ...list.map((item) => item[1]));
    const categoryPager = $('#categoryPager');
    if (categoryPager) categoryPager.hidden = sorted.length <= pageSize;
    const categoryPageInfo = $('#categoryPageInfo');
    if (categoryPageInfo) categoryPageInfo.textContent = `${currentPage} / ${pageCount}`;
    const categoryPrev = $('#categoryPrevBtn');
    if (categoryPrev) categoryPrev.disabled = currentPage <= 1;
    const categoryNext = $('#categoryNextBtn');
    if (categoryNext) categoryNext.disabled = currentPage >= pageCount;
    const colors = ['#2f68e8', '#7857d8', '#16a06e', '#d79523', '#e25265', '#4095a5', '#768398'];
    $('#categoryChart').innerHTML = list.map(([name, count], index) => `<div class="category-row"><span>${esc(name)}</span><b>${fmt(count)}</b><div class="category-track"><div class="category-fill" style="--color:${colors[index]};width:${count / maximum * 100}%"></div></div></div>`).join('');
  }

  function renderSnapshot(rows) {
    const top = [...rows].sort((a, b) => totalShares(b) - totalShares(a)).slice(0, 6);
    $('#snapshotBody').innerHTML = top.map((row) => `<tr><td><span class="ticker">${esc(row.share_code)}</span></td><td><span class="cell-main">${esc(row.investor_name)}</span><span class="cell-sub">${esc(row.issuer_name)}</span></td><td>${esc(normalizeClass(row.classification))}</td><td>${originBadge(row.local_foreign)}</td><td class="number">${fmt(totalShares(row))}</td><td class="number"><b>${percent(row.percentage)}</b></td></tr>`).join('');
  }

  function ownerFilteredRows() {
    const code = normalized(state.ownerFilters.code);
    const investor = normalized(state.ownerFilters.investor);
    return ownership.filter((row) => {
      const matchCode = !code || normalized(`${row.share_code} ${row.issuer_name}`).includes(code);
      const matchInvestor = !investor || normalized(row.investor_name).includes(investor);
      return matchCode && matchInvestor && (!state.ownerFilters.month || monthOf(row) === state.ownerFilters.month) && (!state.ownerFilters.origin || row.local_foreign === state.ownerFilters.origin);
    });
  }

  function originBadge(origin) {
    const labels = { L: 'Lokal', F: 'Asing', N: 'Non warkat' };
    const key = labels[origin] ? origin : 'N';
    return `<span class="origin-badge ${key}">${labels[key]}</span>`;
  }

  function renderOwnership() {
    const filtered = ownerFilteredRows();
    const pageCount = Math.max(1, Math.ceil(filtered.length / state.ownerSize));
    state.ownerPage = Math.min(state.ownerPage, pageCount);
    const start = (state.ownerPage - 1) * state.ownerSize;
    const page = filtered.slice(start, start + state.ownerSize);
    $('#ownerBody').innerHTML = page.map((row) => `<tr><td>${dateLabel(row.record_date)}</td><td><span class="ticker">${esc(row.share_code)}</span></td><td><span class="cell-main">${esc(row.issuer_name)}</span></td><td><span class="cell-main">${esc(row.investor_name)}</span></td><td>${esc(row.classification || '—')}</td><td>${originBadge(row.local_foreign)}</td><td>${esc(row.nationality || '—')}</td><td>${esc(row.domicile || '—')}</td><td class="number">${fmt(row.scripless)}</td><td class="number">${fmt(row.scrip)}</td><td class="number"><b>${fmt(totalShares(row))}</b></td><td class="number"><b>${percent(row.percentage)}</b></td><td><button class="row-menu" data-owner-edit="${row.id}">Ubah</button></td></tr>`).join('');
    $('#ownerEmpty').hidden = page.length > 0;
    const end = Math.min(start + state.ownerSize, filtered.length);
    $('#ownerPageInfo').textContent = `Menampilkan ${filtered.length ? fmt(start + 1) : 0}–${fmt(end)} dari ${fmt(filtered.length)} data`;
    $('#ownerPageNumber').textContent = `${state.ownerPage} / ${pageCount}`;
    $('#ownerPrev').disabled = state.ownerPage === 1;
    $('#ownerNext').disabled = state.ownerPage === pageCount;
    const shares = filtered.reduce((sum, row) => sum + totalShares(row), 0);
    const foreignShares = filtered.filter((row) => row.local_foreign === 'F').reduce((sum, row) => sum + totalShares(row), 0);
    $('#ownerStatInvestors').textContent = fmt(new Set(filtered.map((row) => normalized(row.investor_name)).filter(Boolean)).size);
    $('#ownerStatIssuers').textContent = fmt(new Set(filtered.map((row) => normalized(row.share_code)).filter(Boolean)).size);
    $('#ownerStatShares').textContent = compact(shares);
    $('#ownerStatShares').title = fmt(shares);
    $('#ownerStatForeign').textContent = percent(shares ? foreignShares / shares * 100 : 0);
  }

  function stockFilteredRows() {
    const q = normalized(state.stockFilters.q);
    return stocks.filter((row) => (!q || normalized(`${row.code} ${row.company_name}`).includes(q)) && (!state.stockFilters.board || row.listing_board === state.stockFilters.board));
  }

  function renderStockBoardOptions() {
    const boards = [...new Set(stocks.map((row) => row.listing_board).filter(Boolean))].sort();
    const select = $('#stockBoard');
    select.innerHTML = '<option value="">Semua papan</option>' + boards.map((board) => `<option value="${esc(board)}">${esc(board)}</option>`).join('');
    select.value = state.stockFilters.board;
  }

  function renderStocks() {
    const filtered = stockFilteredRows();
    const pageCount = Math.max(1, Math.ceil(filtered.length / state.stockSize));
    state.stockPage = Math.min(state.stockPage, pageCount);
    const start = (state.stockPage - 1) * state.stockSize;
    const page = filtered.slice(start, start + state.stockSize);
    $('#stockBody').innerHTML = page.map((row, index) => `<tr><td>${fmt(start + index + 1)}</td><td><span class="ticker">${esc(row.code)}</span></td><td><span class="cell-main">${esc(row.company_name)}</span></td><td>${dateLabel(row.listing_date)}</td><td class="number"><b>${fmt(row.shares)}</b></td><td><span class="board-badge ${esc(row.listing_board)}">${esc(row.listing_board || '—')}</span></td><td><button class="row-menu" data-stock-edit="${row.id}">Ubah</button></td></tr>`).join('');
    $('#stockEmpty').hidden = page.length > 0;
    const end = Math.min(start + state.stockSize, filtered.length);
    $('#stockPageInfo').textContent = `Menampilkan ${filtered.length ? fmt(start + 1) : 0}–${fmt(end)} dari ${fmt(filtered.length)} saham`;
    $('#stockPageNumber').textContent = `${state.stockPage} / ${pageCount}`;
    $('#stockPrev').disabled = state.stockPage === 1;
    $('#stockNext').disabled = state.stockPage === pageCount;
    const allShares = stocks.reduce((sum, row) => sum + Number(row.shares || 0), 0);
    $('#stockStatCount').textContent = fmt(stocks.length);
    $('#stockStatShares').textContent = compact(allShares);
    $('#stockStatShares').title = fmt(allShares);
    $('#stockStatMain').textContent = fmt(stocks.filter((row) => row.listing_board === 'Main').length);
    $('#stockStatDevelopment').textContent = fmt(stocks.filter((row) => row.listing_board === 'Development').length);
  }

  function renderSharedStatus() {
    $('#ownershipBadge').textContent = compact(ownership.length);
    $('#stockBadge').textContent = fmt(stocks.length);
    const dailyReady = Number.isFinite(Number(databaseInfo.daily_rows)) && Number(databaseInfo.daily_rows) > 0;
    if ($('#dailyBadge')) $('#dailyBadge').textContent = dailyReady ? compact(databaseInfo.daily_rows) : '—';
    $('#dbStatus').textContent = dailyReady
      ? `${compact(databaseInfo.daily_rows)} baris harian · ${fmt(stocks.length)} saham`
      : `${fmt(ownership.length)} posisi · ${fmt(stocks.length)} saham`;
    $('#dbEngineLabel').textContent = 'Database SQLite aktif';
    $('#backupBtn').textContent = '↓ Backup database';
    $('#lastUpdated').textContent = databaseInfo.daily_to
      ? `SQLite · data s.d. ${dateLabel(databaseInfo.daily_to)}`
      : `SQLite · skema v${databaseInfo.schema_version || 1}`;
    document.documentElement.dataset.dailyFrom = databaseInfo.daily_from || '';
    document.documentElement.dataset.dailyTo = databaseInfo.daily_to || '';
  }

  function renderAll() {
    renderMonthOptions();
    renderStockBoardOptions();
    renderDashboard();
    renderOwnership();
    renderStocks();
    renderSharedStatus();
  }

  function switchView(view) {
    state.view = view;
    const config = {
      dashboard: ['Dashboard Kepemilikan Saham', 'Pantau struktur kepemilikan saham di atas 1% secara menyeluruh.', 'DASHBOARD', '＋ Input data'],
      ownership: ['Database Kepemilikan >1%', 'Input, perbarui, cari, dan analisis posisi investor.', 'KEPEMILIKAN', '＋ Tambah kepemilikan'],
      stocks: ['Master Data Saham IDX', 'Kelola referensi saham tercatat di Bursa Efek Indonesia.', 'MASTER IDX', '＋ Tambah saham'],
      market: ['Market Pulse', 'Baca breadth, partisipasi, aktivitas, dan arus asing seluruh pasar.', 'ANALISIS / MARKET PULSE', null],
      screener: ['Technical Screener', 'Saring saham berdasarkan tren, momentum, likuiditas, aktivitas, dan flow.', 'ANALISIS / SCREENER', null],
      signals: ['Prediksi Sinyal Beli', 'Rekomendasi saham berpotensi naik dengan Target Profit & Stop Loss berbasis indikator teknikal.', 'ANALISIS / PREDIKSI SINYAL', null],
      stocklab: ['Zaiden Stock 360', 'Bedah satu saham dengan indikator teknikal, likuiditas, flow, dan kepemilikan terintegrasi.', 'ANALISIS / STOCK 360', null],
      flow: ['Flow & Likuiditas', 'Pantau akumulasi asing, distribusi, dan aktivitas yang tidak biasa.', 'ANALISIS / FLOW', null],
      daily: ['Data Harian IDX', 'Jelajahi isi tabel ringkasan saham harian langsung dari SQLite.', 'DATABASE / DATA HARIAN', null],
      accumulation: ['Akumulasi Transaksi', 'Analisis akumulasi volume, nilai, frekuensi, dan transaksi asing dalam rentang waktu terstruktur.', 'ANALISIS / AKUMULASI', null],
      broker: ['Aktivitas Broker', 'Pantau dominasi, tren, dan profil lengkap setiap broker anggota bursa IDX.', 'ANALISIS / BROKER', null],
      chat: ['Zaiden AI', 'Tanya apa saja tentang data pasar saham Anda dalam Bahasa Indonesia.', 'AI / ZAIDEN CHAT', null],
      settings: ['Pengaturan Tampilan', 'Sesuaikan tema, warna, tipografi, dan tata letak sesuai selera Anda.', 'PREFERENSI / PENGATURAN', null],
      watchlist: ['Watchlist Saya 📌', 'Pantau saham pilihan Anda secara real-time lengkap dengan sinyal, RSI, dan P/L.', 'ANALISIS / WATCHLIST', null],
      weekly: ['IDX Weekly High-Confidence 🧠', 'Sinyal bullish berbasis ML · Precision target ≥90% · Horizon 5 hari perdagangan.', 'ML / IDX WEEKLY HC', null],
      idx_intelligence: ['Zaiden BDM Intelligence', 'Analisis mendalam dengan puluhan metrik teknikal, likuiditas, dan aliran dana (flow) terintegrasi.', 'ZAIDEN BDM', null],
    }[view];
    if (!config) return;
    $$('.view').forEach((element) => { element.hidden = element.id !== `${view}View`; });
    $$('.nav-item').forEach((element) => element.classList.toggle('active', element.dataset.view === view));
    $('#pageTitle').textContent = config[0];
    $('#pageSubtitle').textContent = config[1];
    $('#breadcrumbCurrent').textContent = config[2];
    $('#primaryAddBtn').hidden = !config[3];
    if (config[3]) $('#primaryAddBtn').textContent = config[3];
    window.scrollTo({ top: 0, behavior: 'smooth' });
    document.dispatchEvent(new CustomEvent('zaiden:viewchange', { detail: { view } }));
  }

  function showToast(message, error = false) {
    $('#toast').className = `toast show${error ? ' error' : ''}`;
    $('#toastIcon').textContent = error ? '!' : '✓';
    $('#toastTitle').textContent = error ? 'Terjadi masalah' : 'Berhasil';
    $('#toastMessage').textContent = message;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { $('#toast').className = 'toast'; }, 2800);
  }

  function openOwnerForm(id = null) {
    $('#ownerForm').reset();
    $('#ownerId').value = '';
    $('#fieldDate').value = ownership.find((row) => monthOf(row) === (state.dashboardMonth || getMonths()[0]))?.record_date || new Date().toISOString().slice(0, 10);
    $('#fieldScrip').value = 0;
    $('#fieldOrigin').value = 'L';
    $('#ownerDeleteBtn').hidden = true;
    $('#ownerFormTitle').textContent = 'Tambah kepemilikan';
    if (id !== null) {
      const row = ownership.find((item) => item.id === Number(id));
      if (!row) return showToast('Data kepemilikan tidak ditemukan.', true);
      $('#ownerId').value = row.id;
      $('#fieldDate').value = row.record_date;
      $('#fieldCode').value = row.share_code;
      $('#fieldIssuer').value = row.issuer_name;
      $('#fieldInvestor').value = row.investor_name;
      $('#fieldClass').value = row.classification || '';
      $('#fieldOrigin').value = row.local_foreign || 'N';
      $('#fieldNationality').value = row.nationality || '';
      $('#fieldDomicile').value = row.domicile || '';
      $('#fieldScripless').value = row.scripless;
      $('#fieldScrip').value = row.scrip;
      $('#fieldPercentage').value = row.percentage;
      $('#ownerDeleteBtn').hidden = false;
      $('#ownerFormTitle').textContent = 'Perbarui kepemilikan';
    }
    $('#ownerDialog').showModal();
  }

  function ownerFormRecord() {
    const id = Number($('#ownerId').value) || undefined;
    const record = {
      record_date: $('#fieldDate').value,
      share_code: normalized($('#fieldCode').value),
      issuer_name: $('#fieldIssuer').value.trim(),
      investor_name: $('#fieldInvestor').value.trim(),
      classification: $('#fieldClass').value.trim(),
      local_foreign: $('#fieldOrigin').value,
      nationality: $('#fieldNationality').value.trim(),
      domicile: $('#fieldDomicile').value.trim(),
      scripless: Number($('#fieldScripless').value),
      scrip: Number($('#fieldScrip').value),
      percentage: Number($('#fieldPercentage').value),
      updated_at: new Date().toISOString()
    };
    if (id) record.id = id;
    if (!record.record_date || !record.share_code || !record.issuer_name || !record.investor_name) throw new Error('Lengkapi semua kolom wajib.');
    if (!['L', 'F', 'N'].includes(record.local_foreign)) throw new Error('Asal investor tidak valid.');
    if (![record.scripless, record.scrip, record.percentage].every(Number.isFinite) || record.scripless < 0 || record.scrip < 0 || record.percentage < 0 || record.percentage > 100) throw new Error('Jumlah saham atau persentase tidak valid.');
    if (![record.scripless, record.scrip].every(Number.isInteger)) throw new Error('Jumlah saham harus berupa bilangan bulat.');
    const duplicate = ownership.some((item) => item.id !== id && item.record_date === record.record_date && normalized(item.share_code) === record.share_code && normalized(item.investor_name) === normalized(record.investor_name));
    if (duplicate) throw new Error('Investor tersebut sudah tercatat untuk saham dan tanggal yang sama.');
    return record;
  }

  async function saveOwner(event) {
    event.preventDefault();
    try {
      const record = ownerFormRecord();
      await putRecord('ownership', record);
      await reloadCaches();
      $('#ownerDialog').close();
      renderAll();
      showToast(record.id ? 'Data kepemilikan diperbarui.' : 'Data kepemilikan ditambahkan.');
    } catch (error) { showToast(error.message, true); }
  }

  async function removeOwner() {
    const id = Number($('#ownerId').value);
    if (!id || !confirm('Hapus posisi kepemilikan ini dari database?')) return;
    try {
      await deleteRecord('ownership', id);
      await reloadCaches();
      $('#ownerDialog').close();
      renderAll();
      showToast('Data kepemilikan dihapus.');
    } catch (error) { showToast(error.message, true); }
  }

  function openStockForm(id = null) {
    $('#stockForm').reset();
    $('#stockId').value = '';
    $('#stockFieldBoard').value = 'Main';
    $('#stockDeleteBtn').hidden = true;
    $('#stockFormTitle').textContent = 'Tambah saham';
    if (id !== null) {
      const row = stocks.find((item) => item.id === Number(id));
      if (!row) return showToast('Master saham tidak ditemukan.', true);
      $('#stockId').value = row.id;
      $('#stockFieldCode').value = row.code;
      $('#stockFieldCompany').value = row.company_name;
      $('#stockFieldDate').value = row.listing_date || '';
      $('#stockFieldShares').value = row.shares;
      const boardSelect = $('#stockFieldBoard');
      if (![...boardSelect.options].some((option) => option.value === row.listing_board)) boardSelect.add(new Option(row.listing_board, row.listing_board));
      boardSelect.value = row.listing_board;
      $('#stockDeleteBtn').hidden = false;
      $('#stockFormTitle').textContent = 'Perbarui master saham';
    }
    $('#stockDialog').showModal();
  }

  function stockFormRecord() {
    const id = Number($('#stockId').value) || undefined;
    const record = {
      code: normalized($('#stockFieldCode').value),
      company_name: $('#stockFieldCompany').value.trim(),
      listing_date: $('#stockFieldDate').value,
      shares: Number($('#stockFieldShares').value),
      listing_board: $('#stockFieldBoard').value
    };
    if (id) record.id = id;
    if (!record.code || !record.company_name || !record.listing_board) throw new Error('Lengkapi semua kolom wajib.');
    if (!Number.isInteger(record.shares) || record.shares < 0) throw new Error('Jumlah saham harus berupa bilangan bulat positif.');
    if (stocks.some((item) => item.id !== id && normalized(item.code) === record.code)) throw new Error(`Kode ${record.code} sudah ada di master saham.`);
    return record;
  }

  async function saveStock(event) {
    event.preventDefault();
    try {
      const record = stockFormRecord();
      await putRecord('stocks', record);
      await reloadCaches();
      $('#stockDialog').close();
      renderAll();
      showToast(record.id ? 'Master saham diperbarui.' : 'Saham baru ditambahkan ke master.');
    } catch (error) { showToast(error.message, true); }
  }

  async function removeStock() {
    const id = Number($('#stockId').value);
    if (!id || !confirm('Hapus saham ini dari master IDX? Data kepemilikan tidak ikut terhapus.')) return;
    try {
      await deleteRecord('stocks', id);
      await reloadCaches();
      $('#stockDialog').close();
      renderAll();
      showToast('Saham dihapus dari master IDX.');
    } catch (error) { showToast(error.message, true); }
  }

  function downloadFile(name, content, type) {
    const blob = new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = name;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function csvCell(value) { return `"${String(value ?? '').replace(/"/g, '""')}"`; }
  function exportOwnership() {
    const rows = ownerFilteredRows();
    const headers = ['Tanggal', 'Kode Saham', 'Emiten', 'Investor', 'Klasifikasi', 'Asal', 'Nasionalitas', 'Domisili', 'Scripless', 'Scrip', 'Total Saham', 'Persentase'];
    const lines = [headers, ...rows.map((row) => [row.record_date, row.share_code, row.issuer_name, row.investor_name, row.classification, row.local_foreign, row.nationality, row.domicile, row.scripless, row.scrip, totalShares(row), row.percentage])];
    downloadFile(`kepemilikan-saham-${new Date().toISOString().slice(0, 10)}.csv`, `\ufeff${lines.map((line) => line.map(csvCell).join(',')).join('\r\n')}`, 'text/csv;charset=utf-8');
    showToast(`${fmt(rows.length)} data kepemilikan diekspor.`);
  }

  function exportStocks() {
    const rows = stockFilteredRows();
    const lines = [['No', 'Kode', 'Nama Perusahaan', 'Tanggal Listing', 'Saham Beredar', 'Papan'], ...rows.map((row, index) => [index + 1, row.code, row.company_name, row.listing_date, row.shares, row.listing_board])];
    downloadFile(`master-saham-idx-${new Date().toISOString().slice(0, 10)}.csv`, `\ufeff${lines.map((line) => line.map(csvCell).join(',')).join('\r\n')}`, 'text/csv;charset=utf-8');
    showToast(`${fmt(rows.length)} master saham diekspor.`);
  }

  function backupDatabase() {
    const anchor = document.createElement('a');
    anchor.href = '/api/database/backup';
    anchor.download = '';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    showToast('Backup SQLite sedang disiapkan.');
  }

  function bindEvents() {
    $$('.nav-item').forEach((button) => button.addEventListener('click', () => switchView(button.dataset.view)));
    $$('[data-go]').forEach((button) => button.addEventListener('click', () => switchView(button.dataset.go)));
    $('#primaryAddBtn').addEventListener('click', () => {
      if (state.view === 'stocks') openStockForm();
      else if (state.view === 'dashboard' || state.view === 'ownership') openOwnerForm();
    });
    $('#ownerAddBtn').addEventListener('click', () => openOwnerForm());
    $('#stockAddBtn').addEventListener('click', () => openStockForm());
    $('#dashboardMonth').addEventListener('change', (event) => { state.dashboardMonth = event.target.value; renderDashboard(); });
    $('#topPrevBtn')?.addEventListener('click', () => {
      if (state.topChartPage > 1) {
        state.topChartPage -= 1;
        renderDashboard();
      }
    });
    $('#topNextBtn')?.addEventListener('click', () => {
      if (state.topChartPage < state.topChartPages) {
        state.topChartPage += 1;
        renderDashboard();
      }
    });
    $('#categoryPrevBtn')?.addEventListener('click', () => {
      if (state.categoryPage > 1) {
        state.categoryPage -= 1;
        renderDashboard();
      }
    });
    $('#categoryNextBtn')?.addEventListener('click', () => {
      if (state.categoryPage < state.categoryPages) {
        state.categoryPage += 1;
        renderDashboard();
      }
    });
    let ownerTimer;
    $('#ownerCodeFilter').addEventListener('input', (event) => { clearTimeout(ownerTimer); ownerTimer = setTimeout(() => { state.ownerFilters.code = event.target.value; state.ownerPage = 1; renderOwnership(); }, 250); });
    $('#ownerInvestorFilter').addEventListener('input', (event) => { clearTimeout(ownerTimer); ownerTimer = setTimeout(() => { state.ownerFilters.investor = event.target.value; state.ownerPage = 1; renderOwnership(); }, 250); });
    $('#ownerMonth').addEventListener('change', (event) => { state.ownerFilters.month = event.target.value; state.ownerPage = 1; renderOwnership(); });
    $('#ownerOrigin').addEventListener('change', (event) => { state.ownerFilters.origin = event.target.value; state.ownerPage = 1; renderOwnership(); });
    $('#ownerPageSize').addEventListener('change', (event) => { state.ownerSize = Number(event.target.value); state.ownerPage = 1; renderOwnership(); });
    $('#ownerPrev').addEventListener('click', () => { if (state.ownerPage > 1) { state.ownerPage -= 1; renderOwnership(); } });
    $('#ownerNext').addEventListener('click', () => { if (state.ownerPage * state.ownerSize < ownerFilteredRows().length) { state.ownerPage += 1; renderOwnership(); } });
    $('#ownerBody').addEventListener('click', (event) => { const button = event.target.closest('[data-owner-edit]'); if (button) openOwnerForm(button.dataset.ownerEdit); });
    let stockTimer;
    $('#stockSearch').addEventListener('input', (event) => { clearTimeout(stockTimer); stockTimer = setTimeout(() => { state.stockFilters.q = event.target.value; state.stockPage = 1; renderStocks(); }, 250); });
    $('#stockBoard').addEventListener('change', (event) => { state.stockFilters.board = event.target.value; state.stockPage = 1; renderStocks(); });
    $('#stockPageSize').addEventListener('change', (event) => { state.stockSize = Number(event.target.value); state.stockPage = 1; renderStocks(); });
    $('#stockPrev').addEventListener('click', () => { if (state.stockPage > 1) { state.stockPage -= 1; renderStocks(); } });
    $('#stockNext').addEventListener('click', () => { if (state.stockPage * state.stockSize < stockFilteredRows().length) { state.stockPage += 1; renderStocks(); } });
    $('#stockBody').addEventListener('click', (event) => { const button = event.target.closest('[data-stock-edit]'); if (button) openStockForm(button.dataset.stockEdit); });
    $('#ownerForm').addEventListener('submit', saveOwner);
    $('#ownerDeleteBtn').addEventListener('click', removeOwner);
    $('#stockForm').addEventListener('submit', saveStock);
    $('#stockDeleteBtn').addEventListener('click', removeStock);
    $$('[data-close]').forEach((button) => button.addEventListener('click', () => $(`#${button.dataset.close}`).close()));
    $('#ownerExportBtn').addEventListener('click', exportOwnership);
    $('#ownerPrintBtn').addEventListener('click', () => window.print());
    $('#stockExportBtn').addEventListener('click', exportStocks);
    $('#backupBtn').addEventListener('click', backupDatabase);
    $('#fieldCode').addEventListener('change', () => {
      const stock = stocks.find((row) => row.code === normalized($('#fieldCode').value));
      if (stock && !$('#fieldIssuer').value.trim()) $('#fieldIssuer').value = stock.company_name;
    });
  }

  async function initialize() {
    bindEvents();
    try {
      setBoot('Menghubungkan SQLite…', 12, 'data/zaiden_trader.db');
      databaseInfo = await apiRequest('/api/health');
      if (Number(databaseInfo.api_version || 0) < 3 || !Object.prototype.hasOwnProperty.call(databaseInfo, 'daily_rows')) {
        throw new Error('Server lama masih aktif. Tutup jendela terminal Zaiden Trader yang lama, lalu jalankan BUKA-APLIKASI.bat kembali.');
      }
      if (!databaseInfo.analytics_ready) throw new Error('Tabel ringkasan_saham_harian belum siap dianalisis.');
      setBoot('Memuat dua tabel database…', 48, `${fmt(databaseInfo.ownership)} posisi · ${fmt(databaseInfo.stocks)} saham`);
      await reloadCaches(true);
    } catch (error) {
      console.error(error);
      setBoot('Aplikasi belum dapat dihubungkan', 100, error.message || 'Jalankan aplikasi melalui BUKA-APLIKASI.bat, bukan membuka index.html langsung.');
      return;
    }
    setBoot('Dashboard siap', 100, `${fmt(ownership.length)} posisi · ${fmt(stocks.length)} saham IDX`);
    renderAll();
    window.zaidenDatabaseInfo = { ...databaseInfo };
    window.zaidenStockMaster = stocks.map((row) => ({ code: row.code, name: row.company_name }));
    document.dispatchEvent(new CustomEvent('zaiden:database-ready', { detail: window.zaidenDatabaseInfo }));
    setTimeout(() => $('#bootScreen').classList.add('done'), 350);
  }

  // ── IDX Weekly view init on first visit ─────────────────────────────────────
  let _weeklyInited = false;
  document.addEventListener('zaiden:viewchange', ({ detail: { view } }) => {
    if (view === 'weekly' && !_weeklyInited) {
      _weeklyInited = true;
      if (typeof initWeekly === 'function') initWeekly();
    } else if (view === 'weekly' && _weeklyInited) {
      if (typeof loadWeeklyStatus === 'function') loadWeeklyStatus();
    } else if (view === 'idx_intelligence') {
      if (typeof window.loadIdxIntelligence === 'function') window.loadIdxIntelligence();
    }
  });

  initialize();
})();


  window.switchIdxIntelligenceTab = function(tabName) {
    const btnFund = document.getElementById('btnTabFundamental');
    const btnHar = document.getElementById('btnTabHarian');
    const btnQuant = document.getElementById('btnTabQuant');
    const btnGrowth = document.getElementById('btnTabGrowth');
    const tabFund = document.getElementById('tabFundamental');
    const tabHar = document.getElementById('tabHarian');
    const tabQuant = document.getElementById('tabQuant');
    const tabGrowth = document.getElementById('tabGrowth');

    if (btnFund) btnFund.classList.remove('active');
    if (btnHar) btnHar.classList.remove('active');
    if (btnQuant) btnQuant.classList.remove('active');
    if (btnGrowth) btnGrowth.classList.remove('active');
    if (tabFund) tabFund.hidden = true;
    if (tabHar) tabHar.hidden = true;
    if (tabQuant) tabQuant.hidden = true;
    if (tabGrowth) tabGrowth.hidden = true;

    if (tabName === 'fundamental') {
      if (btnFund) btnFund.classList.add('active');
      if (tabFund) tabFund.hidden = false;
    } else if (tabName === 'harian') {
      if (btnHar) btnHar.classList.add('active');
      if (tabHar) tabHar.hidden = false;
      if (!harianData.length) window.loadHarian();
    } else if (tabName === 'quant') {
      if (btnQuant) btnQuant.classList.add('active');
      if (tabQuant) tabQuant.hidden = false;
      if (!quantData.length) window.loadQuantAnalysis();
    } else if (tabName === 'growth') {
      if (btnGrowth) btnGrowth.classList.add('active');
      if (tabGrowth) tabGrowth.hidden = false;
      if (!growthData.length) window.loadGrowthScreener();
    }
  };

  let fundamentalData = [];
  let harianData = [];
  let quantData = [];
  let growthData = [];
  
  window.loadIdxIntelligence = async function() {
    const tbody = document.getElementById("fundamentalBody");
    if (tbody) tbody.innerHTML = '<tr><td colspan="8" class="loading-cell">Memuat Zaiden Fundamental Review...</td></tr>';
    
    try {
      // 1. Load Coverage
      const covRes = await fetch("/api/analytics/bdm-coverage");
      const covData = await covRes.json();
      
      const daily = covData.daily || {};
      const fun = covData.fundamental || {};
      const hist = covData.fundamental_history || {};

      if (document.getElementById("covDailyRows")) document.getElementById("covDailyRows").textContent = daily.total_rows ? daily.total_rows.toLocaleString() : "-";
      if (document.getElementById("covDailyDates")) document.getElementById("covDailyDates").textContent = `${(daily.total_local_trading_dates || 0).toLocaleString()} hari bursa · ${(daily.total_stocks || 0).toLocaleString()} saham`;
      if (document.getElementById("covDailyStart")) document.getElementById("covDailyStart").textContent = daily.source_min_available_date || "-";
      if (document.getElementById("covDailyEnd")) document.getElementById("covDailyEnd").textContent = daily.source_max_available_date || "-";

      const fc = fun.field_coverage || {};
      const fcTotal = fun.field_coverage_total || 0;
      const perPct = fcTotal ? Math.round((fc.per || 0) / fcTotal * 100) : 0;
      const pbvPct = fcTotal ? Math.round((fc.pbv || 0) / fcTotal * 100) : 0;
      if (document.getElementById("covFunCoverage")) document.getElementById("covFunCoverage").textContent = `PER ${perPct}% · PBV ${pbvPct}%`;
      if (document.getElementById("covFunReason")) document.getElementById("covFunReason").textContent = fun.per_null_loss_making ? `${fun.per_null_loss_making} saham PER kosong karena rugi (wajar)` : "dari saham dengan data";
      if (document.getElementById("covFunStatus")) document.getElementById("covFunStatus").textContent = fcTotal ? fcTotal.toLocaleString() : "-";

      if (document.getElementById("covHistCoverage")) document.getElementById("covHistCoverage").textContent = hist.total_stocks_covered ? hist.total_stocks_covered.toLocaleString() : "-";
      if (document.getElementById("covHistReason")) document.getElementById("covHistReason").textContent = hist.historical_unavailability_reason || "saham dengan data historis";

      if (document.getElementById("covLastUpdate")) document.getElementById("covLastUpdate").textContent = fun.last_checked_at ? new Date(fun.last_checked_at + 'Z').toLocaleString('id-ID') : "-";

      // 2. Load Fundamental
      const res = await fetch("/api/analytics/bdm-fundamental");
      const data = await res.json();
      fundamentalData = data.items || [];
      if (document.getElementById("idxIntelligenceAsOf")) {
        document.getElementById("idxIntelligenceAsOf").textContent = `Fundamental Snapshot Terakhir: ${fun.last_checked_at || "N/A"}`;
      }
      window.renderFundamental();
      window.renderSectorChart();
    } catch (e) {
      console.error(e);
      if (tbody) tbody.innerHTML = '<tr><td colspan="8" class="error-cell">Gagal memuat data Fundamental.</td></tr>';
    }
  };

  window.renderSectorChart = function() {
    const el = document.getElementById("fundSectorChart");
    if (!el) return;
    const counts = {};
    for (const item of fundamentalData) {
      const s = item.sektor || "Tidak Diketahui";
      counts[s] = (counts[s] || 0) + 1;
    }
    const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 10);
    if (!entries.length) {
      el.innerHTML = '<div class="loading-cell">Belum ada data sektor.</div>';
      return;
    }
    const max = entries[0][1];
    el.innerHTML = entries.map(([sektor, count]) => `
      <div class="fund-sector-row">
        <div class="fund-sector-label">${sektor}</div>
        <div class="fund-sector-track"><div class="fund-sector-fill" style="width:${Math.max(2, count / max * 100)}%"></div></div>
        <div class="fund-sector-value">${count}</div>
      </div>
    `).join("");
  };

  // Generic column sorting shared by the Fundamental / Quant / Growth tables.
  window.SortState = window.SortState || {};
  window.setSort = function(tableKey, col) {
    const cur = window.SortState[tableKey] || {};
    const dir = (cur.col === col && cur.dir === 1) ? -1 : 1;
    window.SortState[tableKey] = { col, dir };
    window.resetPage(tableKey);
    window.updateSortHeaders(tableKey);
    if (tableKey === 'fundamental') window.renderFundamental();
    else if (tableKey === 'quant') window.renderQuantTable();
    else if (tableKey === 'growth') window.renderGrowthTable();
  };
  window.applySortState = function(tableKey, data) {
    const st = window.SortState[tableKey];
    if (!st || !st.col) return data;
    const arr = [...data];
    arr.sort((a, b) => {
      let va = a[st.col], vb = b[st.col];
      const aMissing = va === null || va === undefined;
      const bMissing = vb === null || vb === undefined;
      if (aMissing && bMissing) return 0;
      if (aMissing) return 1;   // missing values always sink to the bottom
      if (bMissing) return -1;
      if (typeof va === 'string') return st.dir * va.localeCompare(vb);
      return st.dir * (va > vb ? 1 : va < vb ? -1 : 0);
    });
    return arr;
  };
  // Generic 30-per-page pagination shared by Fundamental / Quant / Growth.
  window.PageState = window.PageState || {};
  window.PAGE_SIZE = 30;
  window.setPage = function(tableKey, page) {
    const st = window.PageState[tableKey] || { page: 1 };
    st.page = page;
    window.PageState[tableKey] = st;
    if (tableKey === 'fundamental') window.renderFundamental();
    else if (tableKey === 'quant') window.renderQuantTable();
    else if (tableKey === 'growth') window.renderGrowthTable();
  };
  window.resetPage = function(tableKey) {
    window.PageState[tableKey] = { page: 1 };
  };
  window.paginateAndRender = function(tableKey, fullData) {
    const st = window.PageState[tableKey] || { page: 1 };
    const totalPages = Math.max(1, Math.ceil(fullData.length / window.PAGE_SIZE));
    const page = Math.min(Math.max(1, st.page || 1), totalPages);
    st.page = page;
    window.PageState[tableKey] = st;

    const start = (page - 1) * window.PAGE_SIZE;
    const pageData = fullData.slice(start, start + window.PAGE_SIZE);

    const el = document.getElementById(`${tableKey}Pagination`);
    if (el) {
      if (fullData.length <= window.PAGE_SIZE) {
        el.innerHTML = '';
      } else {
        el.innerHTML = `
          <div>Menampilkan ${start + 1}-${Math.min(start + window.PAGE_SIZE, fullData.length)} dari ${fullData.length}</div>
          <div>
            <button ${page <= 1 ? 'disabled' : ''} onclick="window.setPage('${tableKey}', ${page - 1})">‹ Sebelumnya</button>
            <span>${page} / ${totalPages}</span>
            <button ${page >= totalPages ? 'disabled' : ''} onclick="window.setPage('${tableKey}', ${page + 1})">Selanjutnya ›</button>
          </div>`;
      }
    }
    return pageData;
  };

  window.exportTableCSV = function(tableKey) {
    const sources = {
      fundamental: { data: fundamentalData, cols: ['stock_code', 'nama_perusahaan', 'sektor', 'per', 'pbv', 'roe', 'der', 'market_cap'] },
      quant: { data: quantData, cols: ['stock_code', 'nama_perusahaan', 'categories', 'per', 'pbv', 'roe', 'der', 'range_pos_pct'] },
      growth: { data: growthData, cols: ['stock_code', 'nama_perusahaan', 'sektor', 'category', 'net_income_growth_streak_years', 'quarterly_net_income_growth_streak', 'net_income_cagr_pct', 'revenue_cagr_pct'] },
    };
    const src = sources[tableKey];
    if (!src || !src.data.length) return;
    const esc = (v) => {
      if (v === null || v === undefined) return '';
      const s = Array.isArray(v) ? v.join('|') : String(v);
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const lines = [src.cols.join(',')];
    for (const row of src.data) lines.push(src.cols.map(c => esc(row[c])).join(','));
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `zaiden-fundamental-${tableKey}-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  window.updateSortHeaders = function(tableKey) {
    document.querySelectorAll(`th[data-sort-table="${tableKey}"]`).forEach(th => {
      const col = th.getAttribute('data-sort-col');
      const st = window.SortState[tableKey];
      const existing = th.querySelector('.sort-arrow');
      if (existing) existing.remove();
      if (st && st.col === col) {
        const arrow = document.createElement('span');
        arrow.className = 'sort-arrow';
        arrow.style.marginLeft = '3px';
        arrow.textContent = st.dir === 1 ? '▲' : '▼';
        th.appendChild(arrow);
      }
    });
  };

  // PER is legitimately undefined for loss-making companies (no meaningful
  // price/earnings ratio when earnings are negative) — show that as a clear
  // "Rugi" badge instead of a blank dash so it doesn't read as missing data.
  window.fmtPerCell = function(item) {
    if (typeof item.per === 'number') return item.per.toFixed(2);
    if (typeof item.roe === 'number' && item.roe < 0) return '<span class="badge" style="background:#3a1620;color:#f6879a;padding:2px 6px;border-radius:4px;font-size:10px;font-weight:700">Rugi</span>';
    return '<span style="color:var(--text-muted, #8692a4)">N/A</span>';
  };
  window.fmtSignedCell = function(val, decimals=2, suffix='') {
    if (typeof val !== 'number') return '-';
    const cls = val > 0 ? 'positive' : val < 0 ? 'negative' : 'neutral';
    return `<span class="${cls}">${val.toFixed(decimals)}${suffix}</span>`;
  };

  window.renderFundamental = function() {
    const tbody = document.getElementById("fundamentalBody");
    if (!tbody) return;
    const query = (document.getElementById("fundamentalSearch")?.value || "").toLowerCase();

    let filtered = fundamentalData.filter(item => {
      const code = (item.stock_code || "").toLowerCase();
      const name = (item.nama_perusahaan || "").toLowerCase();
      return code.includes(query) || name.includes(query);
    });
    filtered = window.applySortState('fundamental', filtered);
    const paged = window.paginateAndRender('fundamental', filtered);

    if (!paged.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="loading-cell">Tidak ada data ditemukan.</td></tr>';
      return;
    }

    const rows = paged.map(item => {
      const per = window.fmtPerCell(item);
      const pbv = typeof item.pbv === 'number' ? item.pbv.toFixed(2) : '<span style="color:var(--text-muted, #8692a4)">N/A</span>';
      const roe = window.fmtSignedCell(item.roe, 2, '%');
      const der = typeof item.der === 'number' ? item.der.toFixed(2) : '-';
      const mc = item.market_cap ? Math.round(item.market_cap / 1e9).toLocaleString() + ' M' : '-';

      return `
        <tr onclick="window.location.hash = '#stockLabView'; document.getElementById('stockLabCode').value = '${item.stock_code || ''}'; document.getElementById('stockLabForm').dispatchEvent(new Event('submit'))" style="cursor:pointer">
          <td><b>${item.stock_code || '-'}</b></td>
          <td class="truncate" style="max-width:150px" title="${item.nama_perusahaan || ''}">${item.nama_perusahaan || '-'}</td>
          <td>${item.sektor || '-'}</td>
          <td class="number">${per}</td>
          <td class="number">${pbv}</td>
          <td class="number">${roe}</td>
          <td class="number">${der}</td>
          <td class="number">${mc}</td>
        </tr>
      `;
    });

    tbody.innerHTML = rows.join("");
  };

  window.loadHarian = async function() {
    const tbody = document.getElementById("harianBody");
    if (!tbody) return;
    const code = document.getElementById("harianSearch")?.value || "";
    const start = document.getElementById("harianDateStart")?.value || "";
    const end = document.getElementById("harianDateEnd")?.value || "";
    const limit = document.getElementById("harianLimit")?.value || 50;
    
    tbody.innerHTML = '<tr><td colspan="9" class="loading-cell">Mencari data historis...</td></tr>';
    
    try {
      const q = new URLSearchParams({ limit, page: 1 });
      if (code) q.append("code", code);
      if (start) q.append("start", start);
      if (end) q.append("end", end);
      
      const res = await fetch(`/api/analytics/bdm-historical?${q.toString()}`);
      const data = await res.json();
      harianData = data.items || [];
      window.renderHarian();
    } catch (e) {
      console.error(e);
      tbody.innerHTML = '<tr><td colspan="9" class="error-cell">Gagal memuat data Historis.</td></tr>';
    }
  };

  window.renderHarian = function() {
    const tbody = document.getElementById("harianBody");
    if (!tbody) return;
    if (!harianData.length) {
      tbody.innerHTML = '<tr><td colspan="9" class="loading-cell">Tidak ada data ditemukan.</td></tr>';
      return;
    }
    
    const rows = harianData.map(item => {
      const per = item.fun_market_cap && item.kapitalisasi_pasar && item.per ? (item.per * (item.kapitalisasi_pasar / item.fun_market_cap)).toFixed(2) : '-';
      const pbv = item.fun_market_cap && item.kapitalisasi_pasar && item.pbv ? (item.pbv * (item.kapitalisasi_pasar / item.fun_market_cap)).toFixed(2) : '-';
      return `
        <tr>
          <td>${item.tanggal}</td>
          <td><b>${item.kode_saham}</b></td>
          <td class="number">${item.harga_penutupan ? item.harga_penutupan.toLocaleString() : '-'}</td>
          <td class="number">${item.volume ? item.volume.toLocaleString() : '-'}</td>
          <td class="number">${item.nilai_transaksi ? Math.round(item.nilai_transaksi/1e6).toLocaleString() + ' Jt' : '-'}</td>
          <td class="number">${item.frekuensi ? item.frekuensi.toLocaleString() : '-'}</td>
          <td class="number">${item.kapitalisasi_pasar ? Math.round(item.kapitalisasi_pasar / 1e9).toLocaleString() + ' M' : '-'}</td>
          <td class="number">${per}</td>
          <td class="number">${pbv}</td>
        </tr>
      `;
    });
    
    tbody.innerHTML = rows.join("");
  };

  window.loadQuantAnalysis = async function() {
    const tbody = document.getElementById("quantBody");
    if (tbody) tbody.innerHTML = '<tr><td colspan="8" class="loading-cell">Memproses Matriks Quant & Quality Review...</td></tr>';
    
    try {
      const res = await fetch("/api/analytics/bdm-quant");
      const data = await res.json();
      quantData = data.items || [];
      const summary = data.summary || {};
      
      if (document.getElementById("quantSuperValueCount")) document.getElementById("quantSuperValueCount").textContent = summary.super_value || 0;
      if (document.getElementById("quantGrowthCount")) document.getElementById("quantGrowthCount").textContent = summary.quality_growth || 0;
      if (document.getElementById("quantValueTrapCount")) document.getElementById("quantValueTrapCount").textContent = summary.value_trap || 0;
      if (document.getElementById("quantOvervaluedCount")) document.getElementById("quantOvervaluedCount").textContent = summary.overvalued || 0;

      window.renderQuantTable();
    } catch (e) {
      console.error(e);
      if (tbody) tbody.innerHTML = '<tr><td colspan="8" class="error-cell">Gagal memuat Analisis Quant.</td></tr>';
    }
  };

  window.renderQuantTable = function() {
    const tbody = document.getElementById("quantBody");
    if (!tbody) return;
    const query = (document.getElementById("quantSearch")?.value || "").toLowerCase();
    const category = document.getElementById("quantCategoryFilter")?.value || "all";
    
    let filtered = quantData.filter(item => {
      const matchSearch = item.stock_code.toLowerCase().includes(query) || (item.nama_perusahaan && item.nama_perusahaan.toLowerCase().includes(query));
      const matchCat = category === "all" || (item.categories && item.categories.includes(category));
      return matchSearch && matchCat;
    });
    filtered = window.applySortState('quant', filtered);
    const paged = window.paginateAndRender('quant', filtered);

    if (!paged.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="loading-cell">Tidak ada emiten yang sesuai kriteria filter.</td></tr>';
      return;
    }

    const rows = paged.map(item => {
      const badges = (item.categories || []).map(cat => {
        if (cat === 'super_value') return '<span class="badge" style="background:#065f46; color:#34d399; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600;">💎 Super Value</span>';
        if (cat === 'quality_growth') return '<span class="badge" style="background:#1e40af; color:#93c5fd; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600;">🚀 Quality Growth</span>';
        if (cat === 'value_trap') return '<span class="badge" style="background:#92400e; color:#fcd34d; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600;">⚠️ Value Trap</span>';
        if (cat === 'overvalued') return '<span class="badge" style="background:#991b1b; color:#fca5a5; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600;">💸 Overvalued</span>';
        return '<span class="badge" style="background:#374151; color:#9ca3af; padding:2px 8px; border-radius:4px; font-size:11px;">Neutral</span>';
      }).join(' ');

      const rangeDisplay = item.range_pos_pct !== null ? `${item.range_pos_pct}% (Close: ${item.latest_close?.toLocaleString() || '-'})` : '-';

      return `
        <tr onclick="window.location.hash = '#stockLabView'; document.getElementById('stockLabCode').value = '${item.stock_code}'; document.getElementById('stockLabForm').dispatchEvent(new Event('submit'))" style="cursor:pointer">
          <td><b>${item.stock_code}</b></td>
          <td class="truncate" style="max-width:150px" title="${item.nama_perusahaan}">${item.nama_perusahaan || '-'}</td>
          <td>${badges}</td>
          <td class="number">${window.fmtPerCell(item)}</td>
          <td class="number">${item.pbv !== null && item.pbv !== undefined ? item.pbv.toFixed(2) : '<span style="color:var(--text-muted, #8692a4)">N/A</span>'}</td>
          <td class="number">${window.fmtSignedCell(item.roe)}</td>
          <td class="number">${item.der !== null && item.der !== undefined ? item.der.toFixed(2) : '-'}</td>
          <td class="number">${rangeDisplay}</td>
        </tr>
      `;
    });

    tbody.innerHTML = rows.join("");
  };

  window.loadGrowthScreener = async function() {
    const tbody = document.getElementById("growthBody");
    if (tbody) tbody.innerHTML = '<tr><td colspan="8" class="loading-cell">Memuat Growth Screener…</td></tr>';

    try {
      const res = await fetch("/api/analytics/bdm-growth");
      const data = await res.json();
      growthData = data.items || [];
      const summary = data.summary || {};

      if (document.getElementById("growthCompounderCount")) document.getElementById("growthCompounderCount").textContent = summary.consistent_compounder || 0;
      if (document.getElementById("growthGrowingCount")) document.getElementById("growthGrowingCount").textContent = summary.growing || 0;
      if (document.getElementById("growthDecliningCount")) document.getElementById("growthDecliningCount").textContent = summary.declining || 0;
      if (document.getElementById("growthVolatileCount")) document.getElementById("growthVolatileCount").textContent = summary.volatile || 0;
      if (document.getElementById("growthCoverageNote") && data.coverage_note) document.getElementById("growthCoverageNote").textContent = data.coverage_note;

      window.renderGrowthTable();
      window.refreshGrowthSyncStatus();
    } catch (e) {
      console.error(e);
      if (tbody) tbody.innerHTML = '<tr><td colspan="8" class="error-cell">Gagal memuat Growth Screener.</td></tr>';
    }
  };

  window.renderGrowthTable = function() {
    const tbody = document.getElementById("growthBody");
    if (!tbody) return;
    const query = (document.getElementById("growthSearch")?.value || "").toLowerCase();
    const category = document.getElementById("growthCategoryFilter")?.value || "all";

    let filtered = growthData.filter(item => {
      const matchSearch = item.stock_code.toLowerCase().includes(query) || (item.nama_perusahaan && item.nama_perusahaan.toLowerCase().includes(query));
      const matchCat = category === "all" || item.category === category;
      return matchSearch && matchCat;
    });
    filtered = window.applySortState('growth', filtered);
    const paged = window.paginateAndRender('growth', filtered);

    if (!paged.length) {
      tbody.innerHTML = '<tr><td colspan="9" class="loading-cell">Tidak ada emiten yang sesuai kriteria filter. Coba sinkronkan data historis dahulu.</td></tr>';
      return;
    }

    const catBadge = {
      consistent_compounder: '<span class="badge" style="background:#065f46; color:#34d399; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600;">🏆 Compounder</span>',
      growing: '<span class="badge" style="background:#1e40af; color:#93c5fd; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600;">📈 Growing</span>',
      declining: '<span class="badge" style="background:#991b1b; color:#fca5a5; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600;">📉 Declining</span>',
      volatile: '<span class="badge" style="background:#92400e; color:#fcd34d; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600;">🔀 Volatile</span>',
    };

    const rows = paged.map(item => `
      <tr onclick="window.location.hash = '#stockLabView'; document.getElementById('stockLabCode').value = '${item.stock_code}'; document.getElementById('stockLabForm').dispatchEvent(new Event('submit'))" style="cursor:pointer">
        <td><b>${item.stock_code}</b></td>
        <td class="truncate" style="max-width:150px" title="${item.nama_perusahaan || ''}">${item.nama_perusahaan || '-'}</td>
        <td>${item.sektor || '-'}</td>
        <td>${catBadge[item.category] || item.category}</td>
        <td class="number">${item.net_income_growth_streak_years ?? '-'} thn</td>
        <td class="number">${item.quarterly_net_income_growth_streak ?? '-'} kuartal</td>
        <td class="number">${window.fmtSignedCell(item.net_income_cagr_pct, 1, '%')}</td>
        <td class="number">${window.fmtSignedCell(item.revenue_cagr_pct, 1, '%')}</td>
        <td class="number">${item.market_cap ? Math.round(item.market_cap / 1e9).toLocaleString() + ' M' : '-'}</td>
        <td><button class="text-btn" onclick="event.stopPropagation(); window.openValuationHistory('${item.stock_code}')">📊 Lihat Riwayat</button></td>
      </tr>
    `);

    tbody.innerHTML = rows.join("");
  };

  window.openValuationHistory = async function(code) {
    const modal = document.getElementById('valuationHistoryModal');
    const content = document.getElementById('valHistContent');
    const title = document.getElementById('valHistTitle');
    const subtitle = document.getElementById('valHistSubtitle');
    if (!modal || !content) return;
    title.textContent = `Riwayat Valuasi — ${code}`;
    subtitle.textContent = 'Memuat…';
    content.innerHTML = '<div class="loading-cell">Memuat riwayat PER/PBV…</div>';
    modal.showModal();

    try {
      const res = await fetch(`/api/analytics/bdm-valuation-history?code=${encodeURIComponent(code)}`);
      const data = await res.json();
      if (data.error || !data.items || !data.items.length) {
        content.innerHTML = `<div class="loading-cell">${data.error || 'Belum ada data riwayat valuasi untuk saham ini.'}</div>`;
        subtitle.textContent = 'Tidak ada data';
        return;
      }
      title.textContent = `Riwayat Valuasi — ${code} · ${data.nama_perusahaan || ''}`;
      subtitle.textContent = data.projection_note || '';

      const typeLabel = { quarterly: 'Kuartalan', semester: '6 Bulanan', nine_month: '9 Bulanan', annual: 'Tahunan', projection: 'Proyeksi' };
      const rows = data.items.map(it => `
        <tr style="${it.is_projected ? 'opacity:0.75;font-style:italic' : ''}">
          <td>${it.period_label}${it.is_projected ? ' 🔮' : ''}</td>
          <td>${typeLabel[it.period_type] || it.period_type}</td>
          <td>${it.period_end}</td>
          <td class="number">${it.price_close ? it.price_close.toLocaleString() : '-'}</td>
          <td class="number">${it.annualized_eps !== null && it.annualized_eps !== undefined ? Math.round(it.annualized_eps).toLocaleString() : '-'}</td>
          <td class="number">${it.per !== null && it.per !== undefined ? it.per.toFixed(2) : (it.annualized_eps !== null && it.annualized_eps <= 0 ? '<span style="color:#f6879a">Rugi</span>' : '-')}</td>
          <td class="number">${it.pbv !== null && it.pbv !== undefined ? it.pbv.toFixed(2) : '-'}</td>
          <td class="number">${it.market_cap ? Math.round(it.market_cap / 1e9).toLocaleString() + ' M' : '-'}</td>
        </tr>
      `).join('');

      content.innerHTML = `
        <table class="analytics-table">
          <thead><tr>
            <th>Periode</th><th>Tipe</th><th>Akhir Periode</th>
            <th class="number">Harga</th><th class="number">EPS (Disetahunkan)</th>
            <th class="number">PER</th><th class="number">PBV</th><th class="number">Market Cap</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      `;
    } catch (e) {
      content.innerHTML = `<div class="loading-cell">Error: ${e.message}</div>`;
    }
  };

  window.refreshGrowthSyncStatus = async function() {
    const el = document.getElementById("growthSyncStatus");
    if (!el) return;
    try {
      const res = await fetch("/api/analytics/bdm-growth/sync/status");
      const s = await res.json();
      if (s.status && s.status !== 'idle') el.textContent = `Sinkronisasi historis: ${s.message || s.status}`;
    } catch (e) { /* ignore */ }
  };

  document.getElementById("growthSearch")?.addEventListener("input", () => { window.resetPage('growth'); window.renderGrowthTable(); });
  document.getElementById("growthCategoryFilter")?.addEventListener("change", () => { window.resetPage('growth'); window.renderGrowthTable(); });

  document.getElementById("btnSyncGrowth")?.addEventListener("click", async () => {
    const btn = document.getElementById("btnSyncGrowth");
    const statusEl = document.getElementById("growthSyncStatus");
    if (!btn) return;
    btn.disabled = true;
    btn.textContent = "⌛ Menyinkronkan...";

    try {
      const res = await fetch("/api/analytics/bdm-growth/sync", { method: "POST" });
      const data = await res.json();
      if (!data.ok) {
        if (statusEl) statusEl.textContent = data.message || "Gagal memulai sinkronisasi.";
        btn.disabled = false;
        btn.textContent = "🔄 Sinkronkan Data Historis";
        return;
      }
      if (statusEl) statusEl.textContent = "Sinkronisasi berjalan di latar belakang (~5-15 menit)...";

      const poll = setInterval(async () => {
        const s = await (await fetch("/api/analytics/bdm-growth/sync/status")).json();
        if (statusEl) statusEl.textContent = s.message || "";
        if (s.status === 'success' || s.status === 'failed' || s.status === 'idle') {
          clearInterval(poll);
          btn.disabled = false;
          btn.textContent = "🔄 Sinkronkan Data Historis";
          if (s.status === 'success') window.loadGrowthScreener();
        }
      }, 5000);
    } catch (e) {
      if (statusEl) statusEl.textContent = "Error: " + e.message;
      btn.disabled = false;
      btn.textContent = "🔄 Sinkronkan Data Historis";
    }
  });

  // Event Listeners for Filters & Auto Sync
  document.getElementById("fundamentalSearch")?.addEventListener("input", () => { window.resetPage('fundamental'); window.renderFundamental(); });
  document.getElementById("btnHarianLoad")?.addEventListener("click", window.loadHarian);
  document.getElementById("quantSearch")?.addEventListener("input", () => { window.resetPage('quant'); window.renderQuantTable(); });
  document.getElementById("quantCategoryFilter")?.addEventListener("change", () => { window.resetPage('quant'); window.renderQuantTable(); });

  document.getElementById("btnAutoSyncIdx")?.addEventListener("click", async () => {
    const btn = document.getElementById("btnAutoSyncIdx");
    if (!btn) return;
    const origHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span>⏳</span> <span>Memulai sinkronisasi...</span>';

    try {
      const res = await fetch("/api/analytics/sync-idx-now", { method: "POST" });
      if (!res.ok && res.status !== 202 && res.status !== 409) {
        const err = await res.json().catch(()=>({}));
        throw new Error(err.error || "Gagal memulai sinkronisasi");
      }

      // Polling
      const poll = setInterval(async () => {
        try {
          const sRes = await fetch("/api/analytics/sync-idx-now-status");
          const sData = await sRes.json();
          if (sData.status === "running") {
            btn.innerHTML = `<span>⏳</span> <span>${sData.message || 'Menyinkronkan...'}</span>`;
          } else if (sData.status === "success") {
            clearInterval(poll);
            btn.innerHTML = '<span>✔</span> <span>Sinkronisasi Selesai</span>';
            setTimeout(() => {
              btn.innerHTML = origHtml;
              btn.disabled = false;
            }, 3000);
            await window.loadIdxIntelligence();
            if (quantData.length) await window.loadQuantAnalysis();
          } else if (sData.status === "failed") {
            clearInterval(poll);
            alert("Gagal sinkronisasi: " + (sData.message || "Error"));
            btn.innerHTML = origHtml;
            btn.disabled = false;
          }
        } catch (e) {
          // ignore network err during polling
        }
      }, 2000);
    } catch (e) {
      alert("Terjadi kesalahan saat memulai: " + e.message);
      btn.disabled = false;
      btn.innerHTML = origHtml;
    }
  });


