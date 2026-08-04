(() => {
  'use strict';

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const idNumber = new Intl.NumberFormat('id-ID');
  const idCompact = new Intl.NumberFormat('id-ID', { notation: 'compact', maximumFractionDigits: 2 });
  const CACHE_TTL = 5 * 60 * 1000;
  const STOCK_REQUEST_TIMEOUT_MS = 25000;
  const SCREENER_PRESETS = {
    all: { description: 'Seluruh saham pada snapshot terbaru.', test: () => true },
    momentum: { description: 'Close > SMA20 > SMA50, RSI 50–70, return 20D positif, dan avg nilai ≥ Rp10 miliar.', test: (row) => row.sma20 !== null && row.sma50 !== null && row.close > row.sma20 && row.sma20 > row.sma50 && row.rsi14 >= 50 && row.rsi14 <= 70 && (row.return_20d || 0) > 0 && (row.avg_value_20 || 0) >= 10e9 },
    breakout: { description: 'Close melampaui high 20 sesi sebelumnya, activity ratio ≥1,5×, dan avg nilai ≥ Rp10 miliar.', test: (row) => row.breakout_20 && (row.activity_confirmation_ratio || 0) >= 1.5 && (row.avg_value_20 || 0) >= 10e9 },
    volume: { description: 'Volume, nilai, dan frekuensi masing-masing ≥1,5× rata-rata 20 sesi sebelumnya.', test: (row) => (row.volume_ratio || 0) >= 1.5 && (row.value_ratio || 0) >= 1.5 && (row.frequency_ratio || 0) >= 1.5 },
    foreign: { description: 'Net foreign/volume positif, konsistensi ≥60%, dan avg nilai ≥ Rp10 miliar.', test: (row) => (row.foreign_net_volume_pct_20 || 0) > 0 && (row.foreign_consistency_20 || 0) >= 60 && (row.avg_value_20 || 0) >= 10e9 },
    oversold: { description: 'RSI14 ≤30 dengan avg nilai ≥ Rp1 miliar; oversold bukan jaminan rebound.', test: (row) => row.rsi14 !== null && row.rsi14 <= 30 && (row.avg_value_20 || 0) >= 1e9 },
    liquid: { description: 'Avg nilai ≥ Rp50 miliar dan aktif setidaknya 90% dari 20 sesi.', test: (row) => (row.avg_value_20 || 0) >= 50e9 && (row.active_days_20 || 0) >= 90 }
  };
  const state = {
    overview: null,
    breadth: new Map(),
    screener: null,
    screenerPage: 1,
    screenerSize: 50,
    screenerPreset: 'all',
    screenerSearch: '',
    screenerTrend: '',
    screenerLiquidity: '',
    screenerSort: 'setup_score',
    stock: null,
    stockCache: new Map(),
    flow: new Map(),
    flowWindow: 20,
    flowHistory: new Map(),
    flowHistoryDays: 252,
    flowMode: 'accumulation',
    daily: null,
    dailyPage: 1,
    methodology: null,
    health: null,
    validatedAt: 0,
    syncTimer: null,
    lastHandledSync: null,
    pending: new Map(),
    stockRequestId: 0,
    stockRequestController: null
  };

  const esc = (value = '') => String(value).replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]));
  const num = (value) => value === null || value === undefined || Number.isNaN(Number(value)) ? '—' : idNumber.format(Number(value));
  const compact = (value) => value === null || value === undefined || Number.isNaN(Number(value)) ? '—' : idCompact.format(Number(value));
  const price = (value) => value === null || value === undefined ? '—' : Number(value).toLocaleString('id-ID', { maximumFractionDigits: 2 });
  const ratio = (value) => value === null || value === undefined ? '—' : `${Number(value).toLocaleString('id-ID', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}×`;
  const pct = (value, digits = 2, signed = false) => {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
    const number = Number(value);
    return `${signed && number > 0 ? '+' : ''}${number.toLocaleString('id-ID', { minimumFractionDigits: digits, maximumFractionDigits: digits })}%`;
  };
  const money = (value) => {
    if (value === null || value === undefined) return '—';
    const absolute = Math.abs(Number(value));
    const sign = Number(value) < 0 ? '-' : '';
    if (absolute >= 1e12) return `${sign}Rp${(absolute / 1e12).toLocaleString('id-ID', { maximumFractionDigits: 2 })} T`;
    if (absolute >= 1e9) return `${sign}Rp${(absolute / 1e9).toLocaleString('id-ID', { maximumFractionDigits: 2 })} M`;
    if (absolute >= 1e6) return `${sign}Rp${(absolute / 1e6).toLocaleString('id-ID', { maximumFractionDigits: 2 })} jt`;
    return `${sign}Rp${idNumber.format(absolute)}`;
  };
  const unit = (value) => {
    if (value === null || value === undefined) return '—';
    const number = Number(value);
    return `${number > 0 ? '+' : ''}${compact(number)}`;
  };
  const shortDate = (value) => value ? new Date(`${value}T00:00:00`).toLocaleDateString('id-ID', { day: '2-digit', month: 'short', year: '2-digit' }) : '—';
  const longDate = (value) => value ? new Date(`${value}T00:00:00`).toLocaleDateString('id-ID', { day: '2-digit', month: 'long', year: 'numeric' }) : '—';
  const tone = (value) => Number(value) > 0 ? 'positive' : Number(value) < 0 ? 'negative' : 'neutral';
  const tickerCell = (row) => `<button class="ticker ticker-link" data-analyze="${esc(row.code)}">${esc(row.code)}</button><span class="cell-sub" title="${esc(row.name)}">${esc(row.name)}</span>`;

  async function api(path, options = {}) {
    const response = await fetch(path, { cache: 'no-store', headers: { Accept: 'application/json', ...(options.headers || {}) }, ...options });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `Permintaan gagal (${response.status}).`);
    return payload;
  }

  async function once(key, loader) {
    if (state.pending.has(key)) return state.pending.get(key);
    const promise = loader().finally(() => state.pending.delete(key));
    state.pending.set(key, promise);
    return promise;
  }

  function tickerMatchRank(code, name, query) {
    if (!query) return 0;
    const normalizedCode = String(code || '').toUpperCase();
    const normalizedName = String(name || '').toUpperCase();
    if (normalizedCode === query) return 0;
    if (normalizedCode.startsWith(query)) return 1;
    if (normalizedCode.includes(query)) return 2;
    if (normalizedName === query) return 3;
    if (normalizedName.startsWith(query)) return 4;
    if (normalizedName.split(/[^A-Z0-9]+/).some((word) => word.startsWith(query))) return 5;
    if (normalizedName.includes(query)) return 6;
    return Number.POSITIVE_INFINITY;
  }

  function populateTickerList(rows = window.zaidenStockMaster || [], query = '') {
    const unique = new Map(rows.map((row) => [String(row.code || '').toUpperCase(), row.name || row.company_name || '']));
    if (state.screener) state.screener.items.forEach((row) => unique.set(row.code, row.name));
    const normalizedQuery = String(query || '').trim().toUpperCase();
    const ranked = [...unique]
      .filter(([code]) => code)
      .map(([code, name]) => ({ code, name, rank: tickerMatchRank(code, name, normalizedQuery) }))
      .filter((row) => Number.isFinite(row.rank))
      .sort((a, b) => a.rank - b.rank || a.code.localeCompare(b.code, 'id-ID'));
    const visible = normalizedQuery ? ranked.slice(0, 40) : ranked;
    $('#analyticsTickerList').innerHTML = visible
      .map(({ code, name }) => `<option value="${esc(code)}" label="${esc(name)}"></option>`).join('');
  }

  function clearDataCaches() {
    state.overview = null;
    state.breadth.clear();
    state.screener = null;
    state.stock = null;
    state.stockCache.clear();
    state.flow.clear();
    state.flowHistory.clear();
    state.daily = null;
  }

  function renderMethodology() {
    if (!state.methodology) return;
    const method = state.methodology;
    const profile = method.data_profile;
    const sync = profile.sync_summary || {};
    const freshnessText = `${profile.freshness} · ${profile.calendar_age_days} hari kalender`;
    $('#marketMethodProfile').textContent = `${profile.source} · ${num(profile.total_rows)} baris · ${num(profile.trading_days)} hari bursa · sync ${num(sync.success)} sukses / ${num(sync.failed)} gagal`;
    $('#marketMethodFreshness').textContent = freshnessText;
    $('#marketMethodFreshness').className = `freshness-badge ${profile.freshness === 'Mutakhir' ? 'fresh' : 'warning'}`;
    $('#marketScoreEquation').textContent = method.breadth_score.formula;
    $('#analyticsVersion').textContent = `Analytics v${method.analytics_version}`;
    const colors = ['#2f68e8', '#7658d6', '#18a06f', '#dc9624', '#3b91a4', '#d65463', '#68768d'];
    $('#scoreWeightBar').innerHTML = method.setup_score.components.map((component, index) => `<span style="width:${component.weight}%;--weight-color:${colors[index]}" title="${esc(component.label)}: ${component.weight} poin"></span>`).join('');
    $('#scoreWeightLegend').innerHTML = method.setup_score.components.map((component, index) => `<span style="--weight-color:${colors[index]}"><b>${component.weight}</b>${esc(component.label)}</span>`).join('');
    $('#screenerMethodNotes').textContent = method.setup_score.formula + '. Condition Score v1 belum backtested dan bukan prediksi.';
    $('#databaseProvenance').textContent = `${profile.source} → data/zaiden_trader.db → ${profile.database_table} · ${num(profile.total_rows)} baris · ${shortDate(profile.date_from)}–${shortDate(profile.date_to)} · checksum ${pct(method.quality.successful_dates_hashed_pct, 0)}`;
    $('#dailyMethodRows').textContent = `${num(profile.total_rows)} baris, ${num(profile.trading_days)} hari bursa, ${num(profile.historical_tickers)} kode historis.`;
    $('#dailyQualityBadge').textContent = freshnessText;
    $('#dailyQualityBadge').className = `freshness-badge ${profile.freshness === 'Mutakhir' ? 'fresh' : 'warning'}`;
    $('#dailyRange').textContent = `${shortDate(profile.date_from)} — ${shortDate(profile.date_to)}`;
  }

  async function ensureContext(force = false) {
    const expired = Date.now() - state.validatedAt > CACHE_TTL;
    if (force || expired || !state.health) {
      const previousDate = state.health?.daily_to;
      const health = await api('/api/health');
      if (Number(health.api_version || 0) < 3 || !health.analytics_ready) throw new Error('Server analitik belum siap. Tutup server lama lalu jalankan BUKA-APLIKASI.bat kembali.');
      state.health = health;
      state.validatedAt = Date.now();
      if (previousDate && previousDate !== health.daily_to) clearDataCaches();
      document.documentElement.dataset.dailyFrom = health.daily_from || '';
      document.documentElement.dataset.dailyTo = health.daily_to || '';
      if ($('#dailyBadge')) $('#dailyBadge').textContent = compact(health.daily_rows);
    }
    if (force || !state.methodology || state.methodology.data_profile.date_to !== state.health.daily_to) {
      state.methodology = await once('methodology', () => api('/api/analytics/methodology'));
    }
    populateTickerList();
    renderMethodology();
  }

  function showTableError(body, columns, error) {
    body.innerHTML = `<tr><td colspan="${columns}"><div class="inline-error"><b>Data belum dapat dimuat</b><span>${esc(error.message)}</span><button type="button" data-retry-view="${esc(document.querySelector('.nav-item.active')?.dataset.view || '')}">Coba lagi</button></div></td></tr>`;
  }

  function downsample(items, maximum = 420) {
    if (items.length <= maximum) return items;
    const result = [];
    const step = (items.length - 1) / (maximum - 1);
    for (let index = 0; index < maximum; index += 1) result.push(items[Math.round(index * step)]);
    return result;
  }

  function storeStockCache(key, data) {
    state.stockCache.delete(key);
    state.stockCache.set(key, data);
    while (state.stockCache.size > 12) state.stockCache.delete(state.stockCache.keys().next().value);
  }

  function svgLineChart(items, series, options = {}) {
    if (!items.length) return '<div class="chart-empty">Belum ada data untuk digambar.</div>';
    const width = 1000;
    const height = options.height || 270;
    const padding = { left: 58, right: 22, top: 18, bottom: 34 };
    const plotWidth = width - padding.left - padding.right;
    const plotHeight = height - padding.top - padding.bottom;
    const values = series.flatMap((entry) => items.map((item) => Number(item[entry.key])).filter(Number.isFinite));
    let minimum = options.minimum !== undefined ? options.minimum : Math.min(...values);
    let maximum = options.maximum !== undefined ? options.maximum : Math.max(...values);
    if (minimum === maximum) { minimum -= 1; maximum += 1; }
    const margin = options.fixedRange ? 0 : (maximum - minimum) * 0.08;
    minimum -= margin;
    maximum += margin;
    const x = (index) => padding.left + (items.length === 1 ? plotWidth / 2 : index / (items.length - 1) * plotWidth);
    const y = (value) => padding.top + (maximum - value) / (maximum - minimum) * plotHeight;
    const pathFor = (key) => {
      let path = '';
      let drawing = false;
      items.forEach((item, index) => {
        const value = Number(item[key]);
        if (!Number.isFinite(value)) { drawing = false; return; }
        path += `${drawing ? 'L' : 'M'}${x(index).toFixed(2)},${y(value).toFixed(2)} `;
        drawing = true;
      });
      return path.trim();
    };
    const grid = Array.from({ length: 5 }, (_, index) => {
      const gridY = padding.top + index / 4 * plotHeight;
      const value = maximum - index / 4 * (maximum - minimum);
      return `<line x1="${padding.left}" y1="${gridY}" x2="${width - padding.right}" y2="${gridY}" class="svg-grid"/><text x="${padding.left - 10}" y="${gridY + 4}" text-anchor="end" class="svg-axis">${esc(options.axisFormat ? options.axisFormat(value) : compact(value))}</text>`;
    }).join('');
    const labelIndexes = [...new Set([0, Math.floor((items.length - 1) * .25), Math.floor((items.length - 1) * .5), Math.floor((items.length - 1) * .75), items.length - 1])];
    const xLabels = labelIndexes.map((index) => `<text x="${x(index)}" y="${height - 8}" text-anchor="middle" class="svg-axis">${esc(shortDate(items[index].date))}</text>`).join('');
    const lines = series.map((entry) => `<path d="${pathFor(entry.key)}" fill="none" stroke="${entry.color}" stroke-width="${entry.width || 2.4}" stroke-linecap="round" stroke-linejoin="round" class="svg-series"/>`).join('');
    const zero = minimum < 0 && maximum > 0 ? `<line x1="${padding.left}" y1="${y(0)}" x2="${width - padding.right}" y2="${y(0)}" class="svg-zero"/>` : '';
    const references = (options.referenceLines || []).map((reference) => `<line x1="${padding.left}" y1="${y(reference.value)}" x2="${width - padding.right}" y2="${y(reference.value)}" class="svg-reference"/><text x="${width - padding.right - 2}" y="${y(reference.value) - 4}" text-anchor="end" class="svg-reference-label">${esc(reference.label || reference.value)}</text>`).join('');
    return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(options.label || 'Grafik analitik')}">${grid}${zero}${references}${lines}${xLabels}</svg>`;
  }

  function svgBarChart(items, key, options = {}) {
    if (!items.length) return '<div class="chart-empty">Belum ada data untuk digambar.</div>';
    const width = options.scrollable ? Math.max(1000, items.length * (options.pointWidth || 11) + 78) : 1000;
    const height = options.height || 245;
    const padding = { left: 58, right: 20, top: 15, bottom: 34 };
    const plotWidth = width - padding.left - padding.right;
    const plotHeight = height - padding.top - padding.bottom;
    const values = items.map((item) => Number(item[key]) || 0);
    const min = Math.min(0, ...values);
    const max = Math.max(0, ...values);
    const range = max - min || 1;
    const y = (value) => padding.top + (max - value) / range * plotHeight;
    const zeroY = y(0);
    const barWidth = Math.max(.8, plotWidth / items.length * .72);
    const bars = items.map((item, index) => {
      const value = Number(item[key]) || 0;
      const barX = padding.left + index / items.length * plotWidth + (plotWidth / items.length - barWidth) / 2;
      const barY = value >= 0 ? y(value) : zeroY;
      const barHeight = Math.max(1, Math.abs(y(value) - zeroY));
      const interactive = options.interactive ? ` class="chart-bar-hit" tabindex="${options.scrollable ? '-1' : '0'}" role="button" data-flow-chart-point="${esc(item.date)}" aria-label="${esc(shortDate(item.date))}, net foreign ${esc(unit(value))}"` : '';
      return `<rect x="${barX.toFixed(2)}" y="${barY.toFixed(2)}" width="${barWidth.toFixed(2)}" height="${barHeight.toFixed(2)}" rx="1.5" fill="${value >= 0 ? (options.positive || '#20a572') : (options.negative || '#e05262')}"${interactive}><title>${shortDate(item.date)}: ${unit(value)}</title></rect>`;
    }).join('');
    const pixelsPerItem = plotWidth / items.length;
    const labelStep = options.scrollable ? Math.max(1, Math.ceil(115 / pixelsPerItem)) : items.length;
    const labelIndexes = options.scrollable
      ? [...new Set(items.map((_, index) => index).filter((index) => index % labelStep === 0).concat(items.length - 1))]
      : [...new Set([0, Math.floor((items.length - 1) / 2), items.length - 1])];
    const labels = labelIndexes.map((index) => `<text x="${padding.left + (index + .5) / items.length * plotWidth}" y="${height - 8}" text-anchor="middle" class="svg-axis">${esc(shortDate(items[index].date))}</text>`).join('');
    const style = options.scrollable ? ` style="width:${width}px;max-width:none"` : '';
    return `<svg viewBox="0 0 ${width} ${height}"${style} role="img" aria-label="${esc(options.label || 'Grafik batang')}"><line x1="${padding.left}" y1="${zeroY}" x2="${width - padding.right}" y2="${zeroY}" class="svg-zero"/>${bars}${labels}</svg>`;
  }

  async function loadMarket(force = false) {
    if (force) { state.overview = null; state.breadth.clear(); }
    $('#marketView').setAttribute('aria-busy', 'true');
    try {
      const days = Number($('#breadthDays').value);
      const [overview, breadth] = await Promise.all([
        state.overview || once('overview', () => api('/api/analytics/overview')),
        state.breadth.get(days) || once(`breadth-${days}`, () => api(`/api/analytics/breadth?days=${days}`))
      ]);
      state.overview = overview;
      state.breadth.set(days, breadth);
      renderMarket(overview, breadth);
    } catch (error) {
      $('#breadthChart').innerHTML = `<div class="inline-error"><b>Market Pulse gagal dimuat</b><span>${esc(error.message)}</span><button data-retry-view="market">Coba lagi</button></div>`;
    } finally {
      $('#marketView').setAttribute('aria-busy', 'false');
    }
  }

  function renderMarket(data, breadthData) {
    const market = data.market;
    $('#marketAsOf').textContent = `Data per ${longDate(data.as_of)}`;
    $('#marketActiveBadge').textContent = `${num(market.active)} saham aktif`;
    $('#marketInactiveBadge').textContent = `${num(market.inactive)} tidak aktif`;
    $('#breadthScore').textContent = Math.round(market.breadth_score);
    $('#breadthLabel').textContent = market.breadth_label;
    $('#breadthScoreRing').style.setProperty('--score', `${Math.max(0, Math.min(100, market.breadth_score)) * 3.6}deg`);
    $('#marketAdvancers').textContent = num(market.advancers);
    $('#marketAdvancersNote').textContent = `${pct(market.advancers / Math.max(market.active, 1) * 100, 1)} dari saham aktif`;
    $('#marketDecliners').textContent = num(market.decliners);
    $('#marketDeclinersNote').textContent = `${num(market.unchanged)} tetap · median ${pct(market.median_return, 2, true)}`;
    $('#marketValue').textContent = money(market.value);
    $('#marketValueNote').textContent = `${compact(market.volume)} saham · ${compact(market.frequency)} transaksi`;
    $('#marketForeign').textContent = unit(market.foreign_net);
    $('#marketForeign').className = tone(market.foreign_net);
    $('#marketAbove20').textContent = pct(market.above_sma20_pct, 1);
    $('#marketHighLow').textContent = `${num(market.new_high_20)} / ${num(market.new_low_20)}`;
    $('#breadthChart').classList.remove('chart-loading');
    $('#breadthChart').innerHTML = svgLineChart(breadthData.items, [{ key: 'ad_line', color: '#2f68e8', width: 3 }], { label: 'Garis advance decline', axisFormat: compact });
    $('#breadthLegend').innerHTML = `<span style="--legend:#2f68e8">A/D line</span><small>Kumulatif naik dikurangi turun selama ${breadthData.days} sesi</small>`;
    const activeFlat = market.unchanged_active ?? Math.max(0, market.active - market.advancers - market.decliners);
    const total = market.advancers + market.decliners + activeFlat || 1;
    const advance = market.advancers / total * 100;
    const decline = market.decliners / total * 100;
    $('#participationDonut').innerHTML = `<div class="donut market-donut" style="background:conic-gradient(#1fa16f 0 ${advance}%,#e05262 ${advance}% ${advance + decline}%,#a6afbd ${advance + decline}% 100%)"><div class="donut-center"><b>${pct(advance, 1)}</b><small>dari saham aktif</small></div></div><div class="participation-legend"><span class="up">Naik <b>${num(market.advancers)}</b></span><span class="down">Turun <b>${num(market.decliners)}</b></span><span class="flat">Tetap aktif <b>${num(activeFlat)}</b></span></div>`;
    $('#marketFacts').innerHTML = `<div><span>Tidak bertransaksi</span><b>${num(market.inactive)}</b></div><div><span>Di atas SMA50</span><b>${pct(market.above_sma50_pct, 1)}</b></div><div><span>New high / low 20D</span><b>${num(market.new_high_20)} / ${num(market.new_low_20)}</b></div>`;
    const scoreParts = market.breadth_score_components;
    if (scoreParts) $('#marketScoreWeights').innerHTML = `<div><span>Naik / aktif <b>${pct(scoreParts.advance_ratio, 1)}</b></span><i style="width:${Math.min(scoreParts.advance_ratio, 100)}%"></i><small>Bobot 40%</small></div><div><span>Di atas SMA20 <b>${pct(scoreParts.above_sma20, 1)}</b></span><i style="width:${Math.min(scoreParts.above_sma20, 100)}%"></i><small>Bobot 35%</small></div><div><span>Di atas SMA50 <b>${pct(scoreParts.above_sma50, 1)}</b></span><i style="width:${Math.min(scoreParts.above_sma50, 100)}%"></i><small>Bobot 25%</small></div>`;
    renderMovers('gainers');
    $('#marketActiveBody').innerHTML = data.most_active.map((row) => `<tr><td>${tickerCell(row)}</td><td class="number"><b>${money(row.value)}</b></td><td class="number ${tone(row.return_1d)}">${pct(row.return_1d, 2, true)}</td><td class="number">${ratio(row.volume_ratio)}</td></tr>`).join('') || '<tr><td colspan="4"><div class="chart-empty">Belum ada saham aktif.</div></td></tr>';
  }

  function renderMovers(mode) {
    if (!state.overview) return;
    $$('#marketView [data-mover]').forEach((button) => button.classList.toggle('active', button.dataset.mover === mode));
    const gainers = mode === 'gainers';
    $('#moversTitle').textContent = gainers ? 'Top gainers likuid' : 'Top losers likuid';
    const rows = gainers ? state.overview.top_gainers : state.overview.top_losers;
    $('#marketMoversBody').innerHTML = rows.map((row) => `<tr><td>${tickerCell(row)}</td><td class="number"><b>${price(row.close)}</b></td><td class="number ${tone(row.return_1d)}">${pct(row.return_1d, 2, true)}</td><td class="number ${tone(row.return_20d)}">${pct(row.return_20d, 2, true)}</td></tr>`).join('') || '<tr><td colspan="4"><div class="chart-empty">Belum ada mover yang memenuhi likuiditas minimum.</div></td></tr>';
  }

  async function loadScreener(force = false) {
    if (force) state.screener = null;
    if (!state.screener) {
      $('#screenerView').setAttribute('aria-busy', 'true');
      $('#screenerBody').innerHTML = '<tr><td colspan="12" class="loading-cell">Menghitung indikator adjusted untuk seluruh saham…</td></tr>';
      try {
        state.screener = await once('screener', () => api('/api/analytics/screener'));
        populateTickerList();
      } catch (error) {
        showTableError($('#screenerBody'), 12, error);
        return;
      } finally {
        $('#screenerView').setAttribute('aria-busy', 'false');
      }
    }
    $('#screenerAsOf').textContent = `Data per ${longDate(state.screener.as_of)}`;
    $('#screenerMethodProfile').textContent = `${num(state.screener.count)} saham · maksimal 261 sesi per saham · indikator memakai reference-adjusted price IDX.`;
    renderScreener();
  }

  function filteredScreenerRows() {
    if (!state.screener) return [];
    const search = state.screenerSearch.trim().toLocaleUpperCase('id-ID');
    let rows = state.screener.items.filter((row) => {
      if (search && !`${row.code} ${row.name}`.toLocaleUpperCase('id-ID').includes(search)) return false;
      if (state.screenerTrend && row.trend !== state.screenerTrend) return false;
      if (state.screenerLiquidity && row.liquidity !== state.screenerLiquidity) return false;
      return (SCREENER_PRESETS[state.screenerPreset] || SCREENER_PRESETS.all).test(row);
    });
    const key = state.screenerSort;
    rows = [...rows].sort((a, b) => (Number(b[key]) || -Infinity) - (Number(a[key]) || -Infinity) || a.code.localeCompare(b.code));
    return rows;
  }

  function conditionBadges(row) {
    const tags = [row.trend];
    if (row.breakout_20) tags.push('Breakout');
    if ((row.activity_confirmation_ratio || 0) >= 1.5) tags.push('Activity spike');
    if ((row.foreign_net_volume_pct_20 || 0) > 0 && (row.foreign_consistency_20 || 0) >= 60) tags.push('Foreign +');
    return tags.slice(0, 3).map((tag) => `<span class="condition-tag ${esc(tag.toLowerCase().replace(/[^a-z]+/g, '-'))}">${esc(tag)}</span>`).join('');
  }

  function renderScreener() {
    const rows = filteredScreenerRows();
    const pages = Math.max(1, Math.ceil(rows.length / state.screenerSize));
    state.screenerPage = Math.min(state.screenerPage, pages);
    const start = (state.screenerPage - 1) * state.screenerSize;
    const pageRows = rows.slice(start, start + state.screenerSize);
    $('#screenerBody').innerHTML = pageRows.map((row) => `<tr><td>${tickerCell(row)}${row.quality_flags?.length ? `<span class="quality-dot" title="${esc(row.quality_flags.join(' · '))}">!</span>` : ''}</td><td><div class="condition-tags">${conditionBadges(row)}</div></td><td class="number"><b>${price(row.close)}</b></td><td class="number ${tone(row.return_1d)}">${pct(row.return_1d, 2, true)}</td><td class="number ${tone(row.return_20d)}">${pct(row.return_20d, 2, true)}</td><td class="number">${row.rsi14 === null ? '—' : price(row.rsi14)}</td><td class="number ${tone(row.distance_sma20)}">${pct(row.distance_sma20, 2, true)}</td><td class="number">${ratio(row.activity_confirmation_ratio)}</td><td class="number">${pct(row.atr14_pct, 2)}</td><td class="number ${tone(row.foreign_net_20d)}">${unit(row.foreign_net_20d)}</td><td class="number">${money(row.avg_value_20)}</td><td class="number"><span class="score-chip ${Number(row.setup_score) >= 65 ? 'high' : Number(row.setup_score) < 35 ? 'low' : ''}" title="Cakupan skor ${pct(row.score_coverage_pct, 0)}">${row.setup_score === null ? '—' : Math.round(row.setup_score)}</span></td></tr>`).join('') || '<tr><td colspan="12"><div class="chart-empty">Tidak ada saham yang cocok dengan kombinasi filter ini.</div></td></tr>';
    $('#screenerResultCount').textContent = `${num(rows.length)} dari ${num(state.screener.count)} saham cocok`;
    $('#screenerPageInfo').textContent = `Menampilkan ${rows.length ? num(start + 1) : 0}–${num(Math.min(start + state.screenerSize, rows.length))} dari ${num(rows.length)}`;
    $('#screenerPageNumber').textContent = `${state.screenerPage} / ${pages}`;
    $('#screenerPrev').disabled = state.screenerPage <= 1;
    $('#screenerNext').disabled = state.screenerPage >= pages;
    $('#screenerMethodNotes').textContent = (SCREENER_PRESETS[state.screenerPreset] || SCREENER_PRESETS.all).description + ' Condition Score v1 belum backtested dan bukan prediksi.';
  }

  function exportScreener() {
    const rows = filteredScreenerRows();
    const headers = ['Kode', 'Nama', 'Tanggal', 'Harga Adjusted', 'Tren', 'Return 1D %', 'Return 5D %', 'Return 20D %', 'Return 60D %', 'RSI14 Cutler', 'SMA20', 'SMA50', 'Activity Ratio', 'ATR14 %', 'Volatilitas 20D %', 'Net Asing 20D', 'Net Asing/Volume %', 'Avg Nilai 20D', 'Condition Score', 'Score Coverage %', 'Quality Flags'];
    const data = rows.map((row) => [row.code, row.name, row.date, row.close, row.trend, row.return_1d, row.return_5d, row.return_20d, row.return_60d, row.rsi14, row.sma20, row.sma50, row.activity_confirmation_ratio, row.atr14_pct, row.volatility20, row.foreign_net_20d, row.foreign_net_volume_pct_20, row.avg_value_20, row.setup_score, row.score_coverage_pct, (row.quality_flags || []).join('; ')]);
    downloadCsv(`screener-idx-${new Date().toISOString().slice(0, 10)}.csv`, [headers, ...data]);
  }

  async function loadStock(force = false) {
    const code = ($('#stockLabCode').value || 'BBCA').trim().toUpperCase();
    const days = Number($('#stockLabDays').value || 260);
    const cacheKey = `${code}|${days}`;
    const requestId = ++state.stockRequestId;
    state.stockRequestController?.abort();
    if (!force && state.stockCache.has(cacheKey)) {
      state.stock = state.stockCache.get(cacheKey);
      state.stockCache.delete(cacheKey);
      state.stockCache.set(cacheKey, state.stock);
      state.stock.clientLoadMs = 0;
      state.stockRequestController = null;
      $('#stocklabView').setAttribute('aria-busy', 'false');
      renderStock(state.stock);
      return;
    }
    $('#stocklabView').setAttribute('aria-busy', 'true');
    $('#stockLabLoading').hidden = false;
    $('#stockLabContent').hidden = true;
    $('#stockLabLoading').innerHTML = '<span></span><b>Menyiapkan Stock Lab…</b><small>Menghitung indikator dan menyatukan data kepemilikan.</small>';
    const started = performance.now();
    const controller = new AbortController();
    let timedOut = false;
    const timeoutId = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, STOCK_REQUEST_TIMEOUT_MS);
    state.stockRequestController = controller;
    try {
      const data = await api(`/api/analytics/stock?code=${encodeURIComponent(code)}&days=${days}`, { signal: controller.signal });
      if (requestId !== state.stockRequestId) return;
      state.stock = data;
      state.stock.requestedDays = days;
      state.stock.clientLoadMs = Math.round(performance.now() - started);
      storeStockCache(cacheKey, state.stock);
      renderStock(state.stock);
    } catch (error) {
      if (requestId !== state.stockRequestId || (error.name === 'AbortError' && !timedOut)) return;
      $('#stockLabContent').hidden = true;
      $('#stockLabLoading').hidden = false;
      const message = timedOut
        ? 'Analisis melewati 25 detik. Server lokal mungkin sedang sibuk; coba lagi atau buka ulang aplikasi.'
        : error.message;
      $('#stockLabLoading').innerHTML = `<div class="inline-error"><b>${esc(code)} belum dapat dianalisis</b><span>${esc(message)}</span><button data-retry-view="stocklab">Coba lagi</button></div>`;
    } finally {
      clearTimeout(timeoutId);
      if (requestId === state.stockRequestId) {
        $('#stocklabView').setAttribute('aria-busy', 'false');
        state.stockRequestController = null;
      }
    }
  }

  function renderPriceChart(history) {
    if (!history.length) return '<div class="chart-empty">Riwayat harga kosong.</div>';
    const plotHistory = downsample(history, 420);
    const width = 1000, height = 330;
    const pad = { left: 62, right: 24, top: 18, bottom: 36 };
    const plotW = width - pad.left - pad.right, plotH = height - pad.top - pad.bottom;
    const lows = plotHistory.map((row) => Number(row.low)).filter(Number.isFinite);
    const highs = plotHistory.map((row) => Number(row.high)).filter(Number.isFinite);
    let min = Math.min(...lows), max = Math.max(...highs);
    const margin = (max - min || 1) * .05; min -= margin; max += margin;
    const x = (index) => pad.left + index / Math.max(plotHistory.length - 1, 1) * plotW;
    const y = (value) => pad.top + (max - value) / (max - min) * plotH;
    const path = (key) => {
      let result = '', live = false;
      plotHistory.forEach((row, index) => {
        const value = Number(row[key]);
        if (!Number.isFinite(value)) { live = false; return; }
        result += `${live ? 'L' : 'M'}${x(index).toFixed(2)},${y(value).toFixed(2)} `; live = true;
      });
      return result.trim();
    };
    const grid = Array.from({ length: 5 }, (_, index) => {
      const gy = pad.top + index / 4 * plotH;
      const value = max - index / 4 * (max - min);
      return `<line x1="${pad.left}" y1="${gy}" x2="${width - pad.right}" y2="${gy}" class="svg-grid"/><text x="${pad.left - 10}" y="${gy + 4}" text-anchor="end" class="svg-axis">${price(value)}</text>`;
    }).join('');
    const step = Math.max(1, Math.ceil(plotHistory.length / 130));
    const ranges = plotHistory.map((row, index) => index % step ? '' : `<line x1="${x(index)}" y1="${y(row.high)}" x2="${x(index)}" y2="${y(row.low)}" stroke="#ced7e5" stroke-width="1"/>`).join('');
    const adjustments = plotHistory.map((row, index) => row.reference_adjusted ? `<line x1="${x(index)}" y1="${pad.top}" x2="${x(index)}" y2="${pad.top + plotH}" class="svg-adjustment"><title>Reference price disesuaikan ${shortDate(row.date)}</title></line>` : '').join('');
    const labels = [0, .25, .5, .75, 1].map((part) => Math.round((plotHistory.length - 1) * part)).map((index) => `<text x="${x(index)}" y="${height - 8}" text-anchor="middle" class="svg-axis">${shortDate(plotHistory[index].date)}</text>`).join('');
    const hits = plotHistory.map((row, index) => `<circle cx="${x(index)}" cy="${y(row.close)}" r="8" class="chart-point-hit" tabindex="0" role="button" data-stock-point="${esc(row.date)}" aria-label="${esc(shortDate(row.date))}, close ${esc(price(row.close))}"><title>${shortDate(row.date)} · close ${price(row.close)}</title></circle>`).join('');
    const closeArea = `${path('close')} L${x(plotHistory.length - 1)},${pad.top + plotH} L${x(0)},${pad.top + plotH} Z`;
    return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Grafik harga adjusted, klik titik untuk detail"><defs><linearGradient id="priceArea" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2f68e8" stop-opacity=".18"/><stop offset="1" stop-color="#2f68e8" stop-opacity="0"/></linearGradient></defs>${grid}${ranges}${adjustments}<path d="${closeArea}" fill="url(#priceArea)"/><path d="${path('close')}" fill="none" stroke="#2f68e8" stroke-width="2.8"/><path d="${path('sma20')}" fill="none" stroke="#dd9a26" stroke-width="2"/><path d="${path('sma50')}" fill="none" stroke="#7958d7" stroke-width="2"/>${hits}${labels}</svg>`;
  }

  function renderVolumeChart(history) {
    const plotHistory = downsample(history, 420);
    const maximum = Math.max(1, ...plotHistory.map((row) => Number(row.volume) || 0));
    return `<div class="volume-bars">${plotHistory.map((row) => `<i style="height:${Math.max(1, Number(row.volume || 0) / maximum * 100)}%;--bar:${Number(row.foreign_net) >= 0 ? '#73a8ef' : '#d9a3aa'}" title="${shortDate(row.date)} · ${compact(row.volume)} saham"></i>`).join('')}</div><div class="volume-caption"><span>Volume harian</span><small>Warna biru: net foreign ≥ 0 · merah: net foreign &lt; 0</small></div>`;
  }

  function renderStock(data) {
    const { profile, metrics, history, levels, ownership } = data;
    $('#stockLabCode').value = profile.code;
    $('#labTicker').textContent = profile.code;
    $('#labBoard').textContent = profile.listing_board || '—';
    $('#labCompany').textContent = profile.company_name || metrics.name;
    const speedNote = data.clientLoadMs === 0 ? 'cache lokal' : `${num(data.clientLoadMs || data.generated_ms || 0)} ms`;
    $('#labProfileNote').textContent = `Data per ${longDate(data.as_of)} · ${num(history.length)} sesi · adjusted ke close terbaru · ${speedNote} · listing ${profile.listing_date ? longDate(profile.listing_date) : 'belum tersedia'}`;
    $('#labClose').textContent = price(metrics.close);
    $('#labReturn1').textContent = `${pct(metrics.return_1d, 2, true)} hari ini`;
    $('#labReturn1').className = tone(metrics.return_1d);
    $('#labReturn20').textContent = pct(metrics.return_20d, 2, true);
    $('#labReturn20').className = tone(metrics.return_20d);
    $('#labReturn60').textContent = `${pct(metrics.return_60d, 2, true)} dalam 60 sesi`;
    $('#labTrend').textContent = metrics.trend;
    $('#labTrendNote').textContent = `Harga ${pct(metrics.distance_sma20, 2, true)} dari SMA20`;
    $('#labRsi').textContent = price(metrics.rsi14);
    $('#labMomentum').textContent = metrics.momentum;
    $('#labAtr').textContent = pct(metrics.atr14_pct, 2);
    $('#labVolatility').textContent = `Volatilitas 20D ${pct(metrics.volatility20, 1)}`;
    $('#labVolumeRatio').textContent = ratio(metrics.activity_confirmation_ratio);
    $('#labLiquidity').textContent = `${metrics.turnover_activity_ratio !== null ? 'Turnover ratio' : 'Volume ratio'} · ${metrics.liquidity} · avg ${money(metrics.avg_value_20)}`;
    $('#labForeign').textContent = unit(metrics.foreign_net_20d);
    $('#labForeign').className = tone(metrics.foreign_net_20d);
    $('#labForeignConsistency').textContent = `${pct(metrics.foreign_consistency_20, 0)} sesi positif`;
    $('#stockPriceChart').innerHTML = renderPriceChart(history);
    $('#stockVolumeChart').innerHTML = renderVolumeChart(history);
    $('#stockRsiChart').innerHTML = svgLineChart(downsample(history.filter((row) => row.rsi14 !== null), 420), [{ key: 'rsi14', color: '#7958d7', width: 2.8 }], { height: 235, minimum: 0, maximum: 100, fixedRange: true, axisFormat: (value) => Math.round(value), label: 'RSI 14', referenceLines: [{ value: 30, label: 'Oversold 30' }, { value: 50, label: '50' }, { value: 70, label: 'Overbought 70' }] });
    $('#stockTags').innerHTML = (metrics.tags.length ? metrics.tags : ['Tidak ada kondisi ekstrem']).map((tag) => `<span>${esc(tag)}</span>`).join('');
    $('#stockLevels').innerHTML = `<div><span>Support 20D</span><b>${price(levels.support_20)}</b></div><div><span>Resistance 20D</span><b>${price(levels.resistance_20)}</b></div><div><span>Support 55D</span><b>${price(levels.support_55)}</b></div><div><span>Resistance 55D</span><b>${price(levels.resistance_55)}</b></div><div><span>Max drawdown 60D</span><b class="${tone(metrics.max_drawdown60)}">${pct(metrics.max_drawdown60, 2)}</b></div>`;
    $('#stockOrderBook').innerHTML = `<div><span>Best bid</span><b>${price(metrics.bid)}</b></div><div><span>Best offer</span><b>${price(metrics.offer)}</b></div><div><span>Spread</span><b>${pct(metrics.spread_pct, 3)}</b></div><div><span>Bid depth</span><b>${compact(metrics.bid_volume)}</b></div><div><span>Offer depth</span><b>${compact(metrics.offer_volume)}</b></div><div><span>Depth imbalance</span><b class="${tone(metrics.depth_imbalance)}">${pct(metrics.depth_imbalance, 1, true)}</b></div>`;
    $('#labScore').textContent = metrics.setup_score === null ? '—' : Math.round(metrics.setup_score);
    $('#labScore').title = `Cakupan faktor ${pct(metrics.score_coverage_pct, 0)}`;
    $('#stockFactorCheck').innerHTML = (metrics.score_components || []).map((component) => `<div class="${!component.available ? 'unavailable' : component.passed ? 'passed' : 'missed'}"><i>${!component.available ? '?' : component.passed ? '✓' : '–'}</i><span>${esc(component.label)} <small>${component.points}/${component.weight} poin</small></span></div>`).join('');
    $('#stockCoverageBadge').textContent = `${pct(metrics.indicator_coverage, 0)} indikator tersedia`;
    $('#stockCoverageBadge').className = `freshness-badge ${metrics.indicator_coverage >= 80 ? 'fresh' : 'warning'}`;
    $('#stockDataQuality').textContent = `${num(metrics.history_sessions)} sesi · cakupan indikator ${pct(metrics.indicator_coverage, 0)} · ${metrics.latest_active ? 'aktif pada sesi terbaru' : 'tidak bertransaksi pada sesi terbaru'}`;
    const events = data.price_basis?.adjustment_events || [];
    $('#stockAdjustmentEvents').innerHTML = events.length ? `<div class="adjustment-list">${events.map((event) => `<span><b>${shortDate(event.date)}</b> reference gap ${pct(event.reference_gap_pct, 2, true)}</span>`).join('')}</div>` : '<p>Tidak terdeteksi reference gap ≥5% pada rentang ini.</p>';
    const methodNotes = [...(data.notes || []), ...(metrics.quality_flags || [])];
    $('#stockMethodNotes').innerHTML = methodNotes.map((note) => `<p>• ${esc(note)}</p>`).join('');
    renderStockPoint(history[history.length - 1]);
    renderOwnershipLab(ownership);
    $('#stockLabLoading').hidden = true;
    $('#stockLabContent').hidden = false;
  }

  function renderStockPoint(row) {
    if (!row) return;
    $('#stockChartInspector').innerHTML = `<div><b>${shortDate(row.date)}</b><span>Adjusted close <strong>${price(row.close)}</strong> · raw close ${price(row.raw_close)}</span></div><div><span>High / low <strong>${price(row.high)} / ${price(row.low)}</strong></span><span>Return resmi <strong class="${tone(row.official_return)}">${pct(row.official_return, 2, true)}</strong></span><span>Volume <strong>${compact(row.volume)}</strong></span><span>Net foreign <strong class="${tone(row.foreign_net)}">${unit(row.foreign_net)}</strong></span></div>${row.reference_adjusted ? '<small>Reference price disesuaikan pada sesi ini; grafik mempertahankan kontinuitas corporate action.</small>' : ''}`;
  }

  function renderOwnershipLab(ownership) {
    if (!ownership.available) {
      $('#stockOwnershipSummary').textContent = 'Belum ada posisi kepemilikan untuk saham ini di tabel ownership_positions.';
      $('#stockOwnershipContent').innerHTML = '<div class="chart-empty">Data harga tersedia, tetapi snapshot pemegang saham &gt;1% belum ditemukan.</div>';
      return;
    }
    $('#stockOwnershipSummary').textContent = `${num(ownership.position_count)} posisi pada ${longDate(ownership.current_date)} · konsentrasi tercatat ${pct(ownership.concentration_pct, 2)} · porsi asing ${pct(ownership.foreign_pct, 2)}`;
    const changes = new Map((ownership.changes || []).map((row) => [String(row.investor_name).toUpperCase(), row]));
    const exits = (ownership.changes || []).filter((row) => row.status === 'Keluar');
    $('#stockOwnershipContent').innerHTML = `<div class="table-wrap"><table class="analytics-table"><thead><tr><th>Investor</th><th>Klasifikasi</th><th>Asal</th><th class="number">Jumlah saham</th><th class="number">Porsi</th><th class="number">Perubahan saham</th></tr></thead><tbody>${ownership.positions.map((row) => { const change = changes.get(String(row.investor_name).toUpperCase()); return `<tr><td><b>${esc(row.investor_name)}</b></td><td>${esc(row.classification || '—')}</td><td>${row.local_foreign === 'F' ? 'Asing' : row.local_foreign === 'L' ? 'Lokal' : 'Non warkat'}</td><td class="number">${num(row.shares)}</td><td class="number"><b>${pct(row.percentage, 4)}</b></td><td class="number ${tone(change?.share_change)}">${change ? unit(change.share_change) : '—'}</td></tr>`; }).join('')}</tbody></table></div>${exits.length ? `<div class="ownership-exits"><b>Keluar dari snapshot:</b> ${exits.map((row) => `${esc(row.investor_name)} (${unit(row.share_change)})`).join(' · ')}</div>` : ''}${ownership.previous_date ? `<small class="panel-footnote">Perubahan dibanding snapshot ${longDate(ownership.previous_date)}; tanggal ownership dapat berbeda dari tanggal pasar.</small>` : ''}`;
  }

  async function loadFlow(force = false) {
    if (force) {
      state.flow.delete(state.flowWindow);
      state.flowHistory.delete(state.flowHistoryDays);
    }
    let data = state.flow.get(state.flowWindow);
    let history = state.flowHistory.get(state.flowHistoryDays);
    const scrollToLatest = !history;
    if (!data || !history) {
      $('#flowView').setAttribute('aria-busy', 'true');
      if (!data) $('#flowBody').innerHTML = '<tr><td colspan="13" class="loading-cell">Menghitung flow terukur…</td></tr>';
      if (!history) $('#marketFlowChart').innerHTML = '<div class="chart-loading">Memuat riwayat flow pasar…</div>';
      try {
        [data, history] = await Promise.all([
          data || once(`flow-${state.flowWindow}`, () => api(`/api/analytics/flow-liquidity?window=${state.flowWindow}&limit=50&history_days=60`)),
          history || once(`flow-history-${state.flowHistoryDays}`, () => api(`/api/analytics/breadth?days=${state.flowHistoryDays}`))
        ]);
        state.flow.set(state.flowWindow, data);
        state.flowHistory.set(state.flowHistoryDays, history);
      } catch (error) {
        if (!data) showTableError($('#flowBody'), 13, error);
        $('#marketFlowChart').innerHTML = `<div class="inline-error"><b>Grafik flow gagal dimuat</b><span>${esc(error.message)}</span></div>`;
        return;
      } finally {
        $('#flowView').setAttribute('aria-busy', 'false');
      }
    }
    const marketFlow = history.items || [];
    $('#flowAsOf').textContent = `Data per ${longDate(history.as_of || data.as_of)}`;
    $('#flowNetHeader').textContent = `Net asing ${state.flowWindow}D`;
    $('#flowMethodWindow').textContent = `Window aktif ${state.flowWindow} sesi · ranking akumulasi/distribusi memakai net foreign sebagai % volume dengan minimum avg nilai Rp1 miliar.`;
    $('#flowHistoryTitle').textContent = `Net foreign seluruh pasar, ${num(history.days)} sesi`;
    $('#flowHistoryRange').textContent = marketFlow.length
      ? `${longDate(marketFlow[0].date)} — ${longDate(marketFlow[marketFlow.length - 1].date)} · geser ke kiri untuk data lama`
      : 'Belum ada riwayat flow.';
    $('#marketFlowChart').innerHTML = svgBarChart(marketFlow, 'foreign_net', { label: 'Net foreign seluruh pasar', interactive: true, scrollable: true, pointWidth: 11 });
    renderFlowTable(data);
    renderFlowChartPoint(marketFlow[marketFlow.length - 1]);
    if (scrollToLatest) requestAnimationFrame(() => {
      const scroller = $('#marketFlowScroller');
      scroller.scrollLeft = scroller.scrollWidth;
    });
  }

  function renderFlowTable(data) {
    const rows = data[state.flowMode] || [];
    $('#flowBody').innerHTML = rows.map((row) => `<tr class="flow-click-row" tabindex="0" role="button" data-flow-code="${esc(row.code)}" aria-label="Buka detail ${esc(row.code)}"><td>${tickerCell(row)}</td><td><span class="condition-tag ${state.flowMode === 'unusual_activity' ? 'vol-spike' : state.flowMode === 'most_liquid' ? 'liquid' : Number(row.foreign_net) >= 0 ? 'foreign-' : 'distribution'}" title="${esc(row.flow_context || '')}">${state.flowMode === 'unusual_activity' ? `Aktivitas ${ratio(row.activity_score)}` : state.flowMode === 'most_liquid' ? 'Likuid' : esc(row.flow_context || (Number(row.foreign_net) >= 0 ? 'Akumulasi' : 'Distribusi'))}</span></td><td class="number"><b>${price(row.close)}</b></td><td class="number ${tone(row.period_return)}">${pct(row.period_return, 2, true)}</td><td class="number ${tone(row.foreign_net)}"><b>${unit(row.foreign_net)}</b></td><td class="number ${tone(row.foreign_net_volume_pct)}">${pct(row.foreign_net_volume_pct, 3, true)}</td><td class="number">${pct(row.foreign_consistency, 0)}</td><td class="number">${ratio(row.volume_ratio)}</td><td class="number">${ratio(row.value_ratio)}</td><td class="number">${ratio(row.frequency_ratio)}</td><td class="number">${money(row.avg_value_20)}</td><td class="number">${row.price_impact_20 === null ? '—' : Number(row.price_impact_20).toLocaleString('id-ID', { maximumFractionDigits: 5 })}</td><td class="number"><span class="score-chip">${row.setup_score === null ? '—' : Math.round(row.setup_score)}</span></td></tr>`).join('') || '<tr><td colspan="13"><div class="chart-empty">Belum ada saham yang memenuhi kriteria pada window ini.</div></td></tr>';
    renderFlowDetail(rows[0]);
  }

  function renderFlowDetail(row) {
    if (!row) {
      $('#flowDetailPanel').textContent = 'Belum ada saham yang memenuhi kriteria pada window ini.';
      return;
    }
    const windowLabel = `${state.flowWindow} sesi`;
    $('#flowDetailPanel').innerHTML = `<div class="flow-detail-head"><span class="ticker">${esc(row.code)}</span><div><b>${esc(row.name)}</b><small>${esc(row.flow_context || 'Konteks flow belum tersedia')}</small></div></div><div class="flow-detail-metrics"><span><small>Net foreign</small><b class="${tone(row.foreign_net)}">${unit(row.foreign_net)}</b></span><span><small>Net / volume</small><b class="${tone(row.foreign_net_volume_pct)}">${pct(row.foreign_net_volume_pct, 3, true)}</b></span><span><small>Konsistensi</small><b>${pct(row.foreign_consistency, 0)}</b></span><span><small>Estimasi nilai EOD</small><b>${money(row.foreign_value_estimate)}</b></span></div><p><b>Cara membaca:</b> net foreign adalah beli dikurangi jual dalam ${windowLabel}; Net/volume menormalkan ukuran emiten. Konsistensi menghitung proporsi hari aktif asing yang net positif. Price impact ${row.price_impact_20 === null ? 'belum cukup data' : `${Number(row.price_impact_20).toLocaleString('id-ID', { maximumFractionDigits: 5 })}% per Rp1 miliar`}; lebih rendah biasanya lebih mudah dieksekusi. Ini konteks statistik, bukan sinyal transaksi tunggal.</p>`;
  }

  function renderFlowChartPoint(row) {
    if (!row) return;
    $('#flowChartInspector').innerHTML = `<div><b>${shortDate(row.date)}</b><span>Net foreign pasar <strong class="${tone(row.foreign_net)}">${unit(row.foreign_net)}</strong></span></div><div><span>Naik / turun <strong>${num(row.advancers)} / ${num(row.decliners)}</strong></span><span>Nilai transaksi <strong>${money(row.value)}</strong></span><span>Breadth <strong>${pct(row.breadth_pct, 1)}</strong></span></div><small>Nilai net foreign pada grafik pasar memakai lembar saham seluruh pasar; gunakan Net/volume pada tabel untuk membandingkan emiten.</small>`;
  }

  async function loadDaily(force = false) {
    if (!force && state.daily) { renderDaily(state.daily); return; }
    $('#dailyView').setAttribute('aria-busy', 'true');
    $('#dailyBody').innerHTML = '<tr><td colspan="16" class="loading-cell">Mengambil potongan data dari SQLite…</td></tr>';
    const parameters = new URLSearchParams({
      code: $('#dailyCode').value.trim().toUpperCase(),
      from: $('#dailyFrom').value,
      to: $('#dailyTo').value,
      page: String(state.dailyPage),
      page_size: $('#dailyPageSize').value
    });
    try {
      state.daily = await api(`/api/analytics/daily?${parameters}`);
      renderDaily(state.daily);
    } catch (error) {
      showTableError($('#dailyBody'), 16, error);
    } finally {
      $('#dailyView').setAttribute('aria-busy', 'false');
    }
  }

  function renderDaily(data) {
    $('#dailyBody').innerHTML = data.items.map((row) => `<tr><td>${shortDate(row.tanggal)}</td><td><button class="ticker ticker-link" data-analyze="${esc(row.kode_saham)}">${esc(row.kode_saham)}</button></td><td><span class="cell-main">${esc(row.nama_perusahaan || '—')}</span></td><td class="number">${price(row.sebelumnya)}</td><td class="number">${price(row.harga_tertinggi)}</td><td class="number">${price(row.harga_terendah)}</td><td class="number"><b>${price(row.harga_penutupan)}</b></td><td class="number ${tone(row.perubahan)}">${Number(row.perubahan) > 0 ? '+' : ''}${price(row.perubahan)}</td><td class="number">${num(row.volume)}</td><td class="number">${money(row.nilai_transaksi)}</td><td class="number">${num(row.frekuensi)}</td><td class="number">${num(row.beli_asing)}</td><td class="number">${num(row.jual_asing)}</td><td class="number ${tone(row.net_asing)}">${unit(row.net_asing)}</td><td class="number">${price(row.penawaran_beli)}</td><td class="number">${price(row.penawaran_jual)}</td></tr>`).join('') || '<tr><td colspan="16"><div class="chart-empty">Tidak ada data pada filter ini.</div></td></tr>';
    $('#dailyTotal').textContent = num(data.total);
    const filters = [data.filters.code, data.filters.date_from && `dari ${shortDate(data.filters.date_from)}`, data.filters.date_to && `s.d. ${shortDate(data.filters.date_to)}`].filter(Boolean);
    $('#dailyFilterLabel').textContent = filters.join(' · ') || 'Semua data';
    const start = (data.page - 1) * data.page_size;
    $('#dailyPageInfo').textContent = `Menampilkan ${data.total ? num(start + 1) : 0}–${num(Math.min(start + data.page_size, data.total))} dari ${num(data.total)} baris`;
    $('#dailyPageNumber').textContent = `${data.page} / ${data.pages}`;
    $('#dailyPrev').disabled = data.page <= 1;
    $('#dailyNext').disabled = data.page >= data.pages;
    const from = document.documentElement.dataset.dailyFrom;
    const to = document.documentElement.dataset.dailyTo;
    if (from || to) $('#dailyRange').textContent = `${shortDate(from)} — ${shortDate(to)}`;
  }

  function renderSyncStatus(payload) {
    const running = payload.status === 'running';
    $('#syncDataBtn').disabled = running;
    $('#syncDataBtn').textContent = running ? '↻ Memeriksa IDX…' : '↻ Perbarui dari IDX';
    const finalLine = String(payload.message || '').trim().split(/\r?\n/).filter(Boolean).pop() || 'Siap memeriksa data terbaru.';
    $('#syncStatus').textContent = finalLine;
    $('#syncStatus').className = `sync-status ${payload.status || 'idle'}`;
  }

  async function pollSyncStatus() {
    clearTimeout(state.syncTimer);
    try {
      const payload = await api('/api/analytics/sync-status');
      renderSyncStatus(payload);
      if (payload.status === 'running') {
        state.syncTimer = setTimeout(pollSyncStatus, 1800);
      } else if (payload.status === 'success' && payload.finished_at !== state.lastHandledSync) {
        state.lastHandledSync = payload.finished_at;
        clearDataCaches();
        state.methodology = null;
        state.validatedAt = 0;
        await ensureContext(true);
        await loadDaily(true);
      }
    } catch (error) {
      $('#syncStatus').textContent = error.message;
      $('#syncStatus').className = 'sync-status failed';
      $('#syncDataBtn').disabled = false;
    }
  }

  async function startDataSync() {
    $('#syncDataBtn').disabled = true;
    $('#syncStatus').textContent = 'Memulai pemeriksaan sumber resmi IDX…';
    try {
      const payload = await api('/api/analytics/sync', { method: 'POST' });
      renderSyncStatus(payload);
      pollSyncStatus();
    } catch (error) {
      $('#syncStatus').textContent = error.message;
      $('#syncStatus').className = 'sync-status failed';
      $('#syncDataBtn').disabled = false;
    }
  }

  function renderBrokerSyncStatus(payload) {
    const running = payload.status === 'running';
    const btn = $('#syncBrokerBtn');
    if (btn) {
      btn.disabled = running;
      btn.textContent = running ? '↻ Mengunduh Broker…' : '↻ Perbarui Broker dari IDX';
    }
    const statusEl = $('#brokerSyncStatus');
    if (statusEl) {
      const finalLine = String(payload.message || '').trim().split(/\r?\n/).filter(Boolean).pop() || '';
      statusEl.textContent = finalLine;
      statusEl.className = `sync-status ${payload.status || 'idle'}`;
    }
  }

  async function pollBrokerSyncStatus() {
    try {
      const payload = await api('/api/broker/sync-status');
      renderBrokerSyncStatus(payload);
      if (payload.status === 'running') {
        setTimeout(pollBrokerSyncStatus, 2500);
      } else if (payload.status === 'success') {
        const statusEl = $('#brokerSyncStatus');
        if (statusEl) statusEl.textContent = '✓ Data broker berhasil diperbarui.';
      }
    } catch (error) {
      const statusEl = $('#brokerSyncStatus');
      if (statusEl) { statusEl.textContent = error.message; statusEl.className = 'sync-status failed'; }
    }
  }

  async function startBrokerSync() {
    const btn = $('#syncBrokerBtn');
    if (btn) btn.disabled = true;
    const statusEl = $('#brokerSyncStatus');
    if (statusEl) statusEl.textContent = 'Memulai unduhan data broker IDX…';
    try {
      const payload = await api('/api/broker/sync', { method: 'POST' });
      renderBrokerSyncStatus(payload);
      pollBrokerSyncStatus();
    } catch (error) {
      if (statusEl) { statusEl.textContent = error.message; statusEl.className = 'sync-status failed'; }
      if (btn) btn.disabled = false;
    }
  }

  function csvCell(value) { return `"${String(value ?? '').replace(/"/g, '""')}"`; }
  function downloadCsv(filename, rows) {
    const content = `\ufeff${rows.map((row) => row.map(csvCell).join(',')).join('\r\n')}`;
    const url = URL.createObjectURL(new Blob([content], { type: 'text/csv;charset=utf-8' }));
    const anchor = document.createElement('a');
    anchor.href = url; anchor.download = filename; document.body.appendChild(anchor); anchor.click(); anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function exportDaily() {
    if (!state.daily) return;
    const headers = ['Tanggal', 'Kode', 'Nama', 'Sebelumnya', 'High', 'Low', 'Close', 'Perubahan', 'Volume', 'Nilai Transaksi', 'Frekuensi', 'Beli Asing', 'Jual Asing', 'Net Asing', 'Bid', 'Offer'];
    const rows = state.daily.items.map((row) => [row.tanggal, row.kode_saham, row.nama_perusahaan, row.sebelumnya, row.harga_tertinggi, row.harga_terendah, row.harga_penutupan, row.perubahan, row.volume, row.nilai_transaksi, row.frekuensi, row.beli_asing, row.jual_asing, row.net_asing, row.penawaran_beli, row.penawaran_jual]);
    downloadCsv(`ringkasan-saham-harian-halaman-${state.daily.page}.csv`, [headers, ...rows]);
  }

  async function activate(view, force = false) {
    if (!['market', 'screener', 'stocklab', 'flow', 'daily'].includes(view)) return;
    try {
      await ensureContext(force);
      if (view === 'market') await loadMarket(force);
      if (view === 'screener') await loadScreener(force);
      if (view === 'stocklab') await loadStock(force);
      if (view === 'flow') await loadFlow(force);
      if (view === 'daily') { await loadDaily(force); await pollSyncStatus(); }
    } catch (error) {
      const target = view === 'market' ? $('#breadthChart') : view === 'screener' ? $('#screenerBody') : view === 'stocklab' ? $('#stockLabLoading') : view === 'flow' ? $('#flowBody') : $('#dailyBody');
      if (target?.tagName === 'TBODY') showTableError(target, view === 'flow' ? 13 : view === 'screener' ? 12 : 16, error);
      else if (target) target.innerHTML = `<div class="inline-error"><b>Analitik belum dapat dimuat</b><span>${esc(error.message)}</span><button data-retry-view="${esc(view)}">Coba lagi</button></div>`;
    }
  }

  function openStockLab(code) {
    $('#stockLabCode').value = code;
    const navigation = document.querySelector('[data-view="stocklab"]');
    if (navigation?.classList.contains('active')) loadStock(false);
    else navigation?.click();
  }

  function bindEvents() {
    document.addEventListener('zaiden:viewchange', (event) => activate(event.detail.view));
    document.addEventListener('zaiden:database-ready', () => populateTickerList());
    document.addEventListener('click', (event) => {
      const analyze = event.target.closest('[data-analyze]');
      if (analyze) { openStockLab(analyze.dataset.analyze); return; }
      const stockPoint = event.target.closest('[data-stock-point]');
      if (stockPoint) { renderStockPoint(state.stock?.history?.find((row) => row.date === stockPoint.dataset.stockPoint)); return; }
      const flowPoint = event.target.closest('[data-flow-chart-point]');
      if (flowPoint) { renderFlowChartPoint(state.flowHistory.get(state.flowHistoryDays)?.items?.find((row) => row.date === flowPoint.dataset.flowChartPoint)); return; }
      const flowRow = event.target.closest('[data-flow-code]');
      if (flowRow) { renderFlowDetail((state.flow.get(state.flowWindow)?.[state.flowMode] || []).find((row) => row.code === flowRow.dataset.flowCode)); return; }
      const retry = event.target.closest('[data-retry-view]');
      if (retry) activate(retry.dataset.retryView, true);
    });
    document.addEventListener('keydown', (event) => {
      if (!['Enter', ' '].includes(event.key)) return;
      const target = event.target.closest('[data-stock-point],[data-flow-chart-point],[data-flow-code]');
      if (!target) return;
      event.preventDefault();
      target.click();
    });
    $('#breadthDays').addEventListener('change', () => loadMarket());
    $$('#marketView [data-mover]').forEach((button) => button.addEventListener('click', () => renderMovers(button.dataset.mover)));
    $('#screenerPresets').addEventListener('click', (event) => {
      const button = event.target.closest('[data-preset]'); if (!button) return;
      state.screenerPreset = button.dataset.preset; state.screenerPage = 1;
      if (state.screenerPreset === 'liquid') state.screenerSort = 'avg_value_20';
      else if (state.screenerPreset === 'foreign') state.screenerSort = 'foreign_net_volume_pct_20';
      else if (state.screenerPreset === 'volume') state.screenerSort = 'activity_confirmation_ratio';
      $('#screenerSort').value = state.screenerSort;
      $$('#screenerPresets [data-preset]').forEach((item) => item.classList.toggle('active', item === button)); renderScreener();
    });
    let searchTimer;
    $('#screenerSearch').addEventListener('input', (event) => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.screenerSearch = event.target.value; state.screenerPage = 1; renderScreener(); }, 180); });
    $('#screenerTrend').addEventListener('change', (event) => { state.screenerTrend = event.target.value; state.screenerPage = 1; renderScreener(); });
    $('#screenerLiquidity').addEventListener('change', (event) => { state.screenerLiquidity = event.target.value; state.screenerPage = 1; renderScreener(); });
    $('#screenerSort').addEventListener('change', (event) => { state.screenerSort = event.target.value; state.screenerPage = 1; renderScreener(); });
    $('#screenerPageSize').addEventListener('change', (event) => { state.screenerSize = Number(event.target.value); state.screenerPage = 1; renderScreener(); });
    $('#screenerPrev').addEventListener('click', () => { if (state.screenerPage > 1) { state.screenerPage -= 1; renderScreener(); } });
    $('#screenerNext').addEventListener('click', () => { const pages = Math.ceil(filteredScreenerRows().length / state.screenerSize); if (state.screenerPage < pages) { state.screenerPage += 1; renderScreener(); } });
    $('#screenerExportBtn').addEventListener('click', exportScreener);
    const refreshTickerSuggestions = (event) => populateTickerList(undefined, event.target.value);
    $('#stockLabCode').addEventListener('input', refreshTickerSuggestions);
    $('#stockLabCode').addEventListener('focus', refreshTickerSuggestions);
    $('#dailyCode').addEventListener('input', refreshTickerSuggestions);
    $('#dailyCode').addEventListener('focus', refreshTickerSuggestions);
    $('#accumulationTicker').addEventListener('input', refreshTickerSuggestions);
    $('#accumulationTicker').addEventListener('focus', refreshTickerSuggestions);
    $('#stockLabForm').addEventListener('submit', (event) => { event.preventDefault(); loadStock(false); });
    $('#stockLabDays').addEventListener('change', () => loadStock(false));
    $('#stockLabRefresh').addEventListener('click', () => loadStock(true));
    $('#flowWindow').addEventListener('click', (event) => {
      const button = event.target.closest('[data-window]'); if (!button) return;
      state.flowWindow = Number(button.dataset.window); $$('#flowWindow button').forEach((item) => item.classList.toggle('active', item === button));
      $('#flowBody').innerHTML = '<tr><td colspan="13" class="loading-cell">Mengganti window analisis…</td></tr>'; loadFlow();
    });
    $('#flowHistoryDays').addEventListener('change', (event) => {
      state.flowHistoryDays = Number(event.target.value);
      $('#marketFlowChart').innerHTML = '<div class="chart-loading">Memuat rentang riwayat…</div>';
      loadFlow();
    });
    $('#flowScrollOlder').addEventListener('click', () => {
      const scroller = $('#marketFlowScroller');
      scroller.scrollBy({ left: -Math.max(420, scroller.clientWidth * .8), behavior: 'smooth' });
    });
    $('#flowScrollLatest').addEventListener('click', () => {
      const scroller = $('#marketFlowScroller');
      scroller.scrollTo({ left: scroller.scrollWidth, behavior: 'smooth' });
    });
    $('#flowTabs').addEventListener('click', (event) => {
      const button = event.target.closest('[data-flow]'); if (!button) return;
      state.flowMode = button.dataset.flow; $$('#flowTabs button').forEach((item) => item.classList.toggle('active', item === button));
      const data = state.flow.get(state.flowWindow); if (data) renderFlowTable(data);
    });
    $('#dailyFilterForm').addEventListener('submit', (event) => { event.preventDefault(); state.dailyPage = 1; state.daily = null; loadDaily(true); });
    $('#dailyClearBtn').addEventListener('click', () => { $('#dailyFilterForm').reset(); $('#dailyPageSize').value = '50'; state.dailyPage = 1; state.daily = null; loadDaily(true); });
    $('#dailyPrev').addEventListener('click', () => { if (state.daily?.page > 1) { state.dailyPage = state.daily.page - 1; state.daily = null; loadDaily(true); } });
    $('#dailyNext').addEventListener('click', () => { if (state.daily?.page < state.daily?.pages) { state.dailyPage = state.daily.page + 1; state.daily = null; loadDaily(true); } });
    $('#dailyExportBtn').addEventListener('click', exportDaily);
    $('#syncDataBtn').addEventListener('click', startDataSync);
    $('#syncBrokerBtn')?.addEventListener('click', startBrokerSync);
  }


  bindEvents();
  populateTickerList();
})();
