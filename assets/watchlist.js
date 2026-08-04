/* ==========================================================================
   ZAIDEN TRADER — Watchlist Saya v2.0
   Multi-Watchlist: buat, kelola, dan pantau beberapa watchlist sekaligus
   ========================================================================== */
(() => {
  'use strict';

  const STORE_KEY  = 'zaiden_watchlists_v3';
  const LEGACY_KEY = 'zaiden_watchlist_v2';

  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  const fmt    = n => n == null ? '—' : Number(n).toLocaleString('id-ID');
  const fmtRp  = n => n == null ? '—' : 'Rp ' + Number(n).toLocaleString('id-ID');
  const fmtPct = (n, dec=2) => n == null ? '—' : (n >= 0 ? '+' : '') + Number(n).toFixed(dec) + '%';
  const fmtVol = n => {
    if (!n) return '—';
    if (n >= 1e9) return (n/1e9).toFixed(1)+' M lembar';
    if (n >= 1e6) return (n/1e6).toFixed(1)+' jt lembar';
    if (n >= 1e3) return (n/1e3).toFixed(0)+' rb lembar';
    return String(n);
  };
  const uid = () => Math.random().toString(36).slice(2, 10);

  /* ── Watchlist icons ─────────────────────────────────────────────── */
  const WL_ICONS = ['📋','⭐','🔥','💎','🏆','📈','💹','🎯','🛡️','🚀','💰','🌟','⚡','🔔','🌙'];

  /* ── Default demo watchlists ─────────────────────────────────────── */
  const DEMO_LISTS = [
    { name: 'Favorit Saya',    icon: '⭐', codes: ['BBCA','TLKM','ASII','BMRI','BBRI'] },
    { name: 'Saham Dividen',   icon: '💰', codes: ['BBCA','BBNI','TLKM','UNVR','HMSP'] },
    { name: 'Saham Teknologi', icon: '🚀', codes: ['TLKM','BUKA','GOTO','EMTK'] },
  ];

  /* ── State ───────────────────────────────────────────────────────── */
  const state = {
    lists:       [],    // [{ id, name, icon, items[], createdAt }]
    activeId:    null,  // active watchlist ID
    live:        {},    // code → live API data (shared across watchlists)
    viewMode:    'grid',
    sortBy:      'signal',
    sortDir:     'desc',
    filterSignal:'all',
    filterTrend: 'all',
    loading:     false,
    lastRefresh: null,
    refreshTimer:null,
  };

  /* ── Active watchlist shortcut ───────────────────────────────────── */
  function activeList() {
    return state.lists.find(l => l.id === state.activeId) || state.lists[0] || null;
  }

  /* ─────────────────────────────────────────────────────────────────
     STORAGE
     ───────────────────────────────────────────────────────────────── */
  function loadStorage() {
    try {
      const raw = localStorage.getItem(STORE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        state.lists    = parsed.lists    || [];
        state.activeId = parsed.activeId || (state.lists[0]?.id ?? null);
        state.viewMode = parsed.viewMode || 'grid';
        state.sortBy   = parsed.sortBy   || 'signal';
        return;
      }
    } catch (_) {}

    // Migrate legacy single-watchlist
    try {
      const legacyRaw = localStorage.getItem(LEGACY_KEY);
      if (legacyRaw) {
        const legacy = JSON.parse(legacyRaw);
        const items = (legacy.items || []).map(normalizeItem);
        const firstId = uid();
        state.lists = [
          { id: firstId, name: 'Watchlist Saya', icon: '⭐', items, createdAt: new Date().toISOString() },
          ...DEMO_LISTS.slice(1).map(d => ({
            id: uid(), name: d.name, icon: d.icon,
            items: d.codes.map(c => newItem(c)),
            createdAt: new Date().toISOString()
          }))
        ];
        state.activeId = firstId;
        saveStorage();
        return;
      }
    } catch (_) {}

    // First use: create demo lists
    state.lists = DEMO_LISTS.map(d => ({
      id: uid(), name: d.name, icon: d.icon,
      items: d.codes.map(c => newItem(c)),
      createdAt: new Date().toISOString()
    }));
    state.activeId = state.lists[0].id;
    saveStorage();
  }

  function saveStorage() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({
        lists:    state.lists,
        activeId: state.activeId,
        viewMode: state.viewMode,
        sortBy:   state.sortBy,
      }));
    } catch (_) {}
  }

  function normalizeItem(raw = {}) {
    return {
      code:     (raw.code || '').toUpperCase().trim(),
      addedAt:  raw.addedAt || new Date().toISOString().slice(0,10),
      tp:       raw.tp || null,
      sl:       raw.sl || null,
      avgBuy:   raw.avgBuy || null,
      qty:      raw.qty || null,
      note:     raw.note || '',
      priority: raw.priority || 'normal',
    };
  }

  function newItem(code, opts = {}) {
    return normalizeItem({ code, ...opts });
  }

  /* ─────────────────────────────────────────────────────────────────
     WATCHLIST CRUD
     ───────────────────────────────────────────────────────────────── */
  function createWatchlist(name, icon = '📋') {
    const wl = { id: uid(), name: name.trim(), icon, items: [], createdAt: new Date().toISOString() };
    state.lists.push(wl);
    state.activeId = wl.id;
    saveStorage();
    return wl;
  }

  function renameWatchlist(id, name, icon) {
    const wl = state.lists.find(l => l.id === id);
    if (!wl) return;
    wl.name = name.trim() || wl.name;
    wl.icon = icon || wl.icon;
    saveStorage();
  }

  function deleteWatchlist(id) {
    const idx = state.lists.findIndex(l => l.id === id);
    if (idx < 0) return;
    state.lists.splice(idx, 1);
    if (state.activeId === id) {
      state.activeId = state.lists[0]?.id ?? null;
    }
    saveStorage();
  }

  function switchList(id) {
    state.activeId = id;
    state.live = {};       // clear live so it re-fetches
    state.filterSignal = 'all';
    state.filterTrend  = 'all';
    saveStorage();
  }

  /* ─────────────────────────────────────────────────────────────────
     ITEM CRUD (operates on active watchlist)
     ───────────────────────────────────────────────────────────────── */
  function getItems() {
    return activeList()?.items || [];
  }

  function addItems(codes, opts = {}) {
    const wl = activeList();
    if (!wl) return 0;
    let added = 0;
    codes.forEach(code => {
      if (!wl.items.find(i => i.code === code)) {
        wl.items.push(newItem(code, opts));
        added++;
      }
    });
    saveStorage();
    return added;
  }

  function updateItem(code, patch) {
    const wl = activeList();
    if (!wl) return;
    const item = wl.items.find(i => i.code === code);
    if (!item) return;
    Object.assign(item, patch);
    saveStorage();
  }

  function removeItem(code) {
    const wl = activeList();
    if (!wl) return;
    wl.items = wl.items.filter(i => i.code !== code);
    delete state.live[code];
    saveStorage();
  }

  /* ─────────────────────────────────────────────────────────────────
     SIGNAL HELPERS
     ───────────────────────────────────────────────────────────────── */
  const SIGNAL_SCORE = { 'strong-buy':6,'buy':5,'accumulate':4,'neutral':3,'caution':2,'sell':1,'strong-sell':0 };

  function signalStyle(cls) {
    const map = {
      'strong-buy':  'background:#dcfce7;color:#166534',
      'buy':         'background:#dbeafe;color:#1e40af',
      'accumulate':  'background:#e0f2fe;color:#0c4a6e',
      'neutral':     'background:#f1f5f9;color:#475569',
      'caution':     'background:#fef9c3;color:#854d0e',
      'sell':        'background:#fee2e2;color:#991b1b',
      'strong-sell': 'background:#fca5a5;color:#7f1d1d',
    };
    return map[cls] || map.neutral;
  }

  function computePL(avgBuy, qty, price) {
    if (!avgBuy || !price) return null;
    const pct = ((price - avgBuy) / avgBuy) * 100;
    const nominal = qty ? (price - avgBuy) * qty : null;
    return { pct: pct.toFixed(2), nominal, color: pct >= 0 ? '#0d7a52' : '#c0283a' };
  }

  function dist(price, target) {
    if (!price || !target) return null;
    return (((target - price) / price) * 100).toFixed(1);
  }

  /* ─────────────────────────────────────────────────────────────────
     SPARKLINE SVG
     ───────────────────────────────────────────────────────────────── */
  function sparklineSVG(prices, w=100, h=36) {
    if (!prices || prices.length < 2) return `<svg width="${w}" height="${h}"></svg>`;
    const valid = prices.filter(p => p > 0);
    if (valid.length < 2) return `<svg width="${w}" height="${h}"></svg>`;
    const min = Math.min(...valid), max = Math.max(...valid);
    const range = max - min || 1, pad = 3;
    const pts = valid.map((p,i) => [pad+(i/(valid.length-1))*(w-2*pad), h-pad-((p-min)/range)*(h-2*pad)]);
    const pathD = pts.map((p,i) => (i===0?'M':'L')+p[0].toFixed(1)+' '+p[1].toFixed(1)).join(' ');
    const areaD = pathD+` L${pts[pts.length-1][0].toFixed(1)},${h} L${pts[0][0].toFixed(1)},${h} Z`;
    const up = valid[valid.length-1] >= valid[0];
    const col = up ? '#22c55e' : '#ef4444';
    const [lx,ly] = pts[pts.length-1];
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
      <path d="${areaD}" fill="${up?'rgba(34,197,94,.12)':'rgba(239,68,68,.12)'}"/>
      <path d="${pathD}" fill="none" stroke="${col}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>
      <circle cx="${lx.toFixed(1)}" cy="${ly.toFixed(1)}" r="2.5" fill="${col}"/>
    </svg>`;
  }

  /* ─────────────────────────────────────────────────────────────────
     SORT & FILTER
     ───────────────────────────────────────────────────────────────── */
  function getSorted() {
    let items = [...getItems()];
    if (state.filterSignal !== 'all')
      items = items.filter(i => (state.live[i.code]?.signal_class||'') === state.filterSignal);
    if (state.filterTrend !== 'all')
      items = items.filter(i => (state.live[i.code]?.trend||'') === state.filterTrend);

    items.sort((a,b) => {
      const la = state.live[a.code] || {}, lb = state.live[b.code] || {};
      let av=0, bv=0;
      switch (state.sortBy) {
        case 'signal':  av=SIGNAL_SCORE[la.signal_class]??3; bv=SIGNAL_SCORE[lb.signal_class]??3; break;
        case 'change':  av=la.change_pct??-999; bv=lb.change_pct??-999; break;
        case 'name':    return (la.name||a.code).localeCompare(lb.name||b.code);
        case 'rsi':     av=la.rsi??50; bv=lb.rsi??50; break;
        case 'tp_dist': {
          const tpa=a.tp||la.tp_sug, tpb=b.tp||lb.tp_sug;
          av=tpa&&la.price?((tpa-la.price)/la.price):-999;
          bv=tpb&&lb.price?((tpb-lb.price)/lb.price):-999; break;
        }
        case 'pl':
          av=a.avgBuy&&la.price?((la.price-a.avgBuy)/a.avgBuy):-999;
          bv=b.avgBuy&&lb.price?((lb.price-b.avgBuy)/b.avgBuy):-999; break;
        case 'added':
          return state.sortDir==='desc'?b.addedAt.localeCompare(a.addedAt):a.addedAt.localeCompare(b.addedAt);
        default:
          av=SIGNAL_SCORE[la.signal_class]??3; bv=SIGNAL_SCORE[lb.signal_class]??3;
      }
      return state.sortDir==='desc'?bv-av:av-bv;
    });
    return items;
  }

  /* ─────────────────────────────────────────────────────────────────
     RENDER — WATCHLIST SIDEBAR
     ───────────────────────────────────────────────────────────────── */
  function renderSidebar() {
    const rows = state.lists.map(wl => {
      const isActive = wl.id === state.activeId;
      const cnt = wl.items.length;
      const buyCnt = wl.items.filter(i => {
        const sc = state.live[i.code]?.signal_class;
        return sc === 'strong-buy' || sc === 'buy';
      }).length;
      return `
        <div class="wl-list-row ${isActive?'active':''}" data-wlid="${wl.id}">
          <span class="wl-list-icon">${esc(wl.icon)}</span>
          <div class="wl-list-info">
            <div class="wl-list-name">${esc(wl.name)}</div>
            <div class="wl-list-meta">${cnt} saham${buyCnt>0?` · <span style="color:#0d7a52;font-weight:700">${buyCnt} beli</span>`:''}
            </div>
          </div>
          <div class="wl-list-actions">
            <button class="wl-tiny-btn" data-wl-action="rename" data-wlid="${wl.id}" title="Rename">✏</button>
            ${state.lists.length>1?`<button class="wl-tiny-btn danger" data-wl-action="delete" data-wlid="${wl.id}" title="Hapus">✕</button>`:''}
          </div>
        </div>`;
    }).join('');

    return `
      <div class="wl-sidebar">
        <div class="wl-sidebar-head">
          <span class="wl-sidebar-title">📋 Watchlist</span>
          <button class="btn primary wl-new-btn" id="wlNewListBtn" title="Buat watchlist baru">＋ Baru</button>
        </div>
        <div class="wl-lists-scroll">
          ${rows}
        </div>
        <div class="wl-sidebar-foot">
          <small>Total ${state.lists.length} watchlist · ${state.lists.reduce((s,l)=>s+l.items.length,0)} saham</small>
        </div>
      </div>`;
  }

  /* ─────────────────────────────────────────────────────────────────
     RENDER — CONTROLS BAR
     ───────────────────────────────────────────────────────────────── */
  function renderControls() {
    const wl = activeList();
    const sortOptions = [
      ['signal','Sinyal'],['change','Perubahan %'],['rsi','RSI'],
      ['name','Nama'],['tp_dist','Jarak TP'],['pl','P/L'],['added','Terbaru']
    ];
    const signalOptions = [
      ['all','Semua Sinyal'],['strong-buy','Strong Buy'],['buy','Buy'],
      ['accumulate','Accumulate'],['neutral','Netral'],['caution','Caution'],
      ['sell','Sell'],['strong-sell','Strong Sell']
    ];
    const trendOptions = [
      ['all','Semua Tren'],['Uptrend','↑ Uptrend'],['Sideways','→ Sideways'],['Downtrend','↓ Downtrend']
    ];

    return `
      <div class="wl-panel-head">
        <div class="wl-panel-title">
          <span style="font-size:1.4rem">${esc(wl?.icon||'📋')}</span>
          <div>
            <h2 class="wl-panel-name">${esc(wl?.name||'Watchlist')}</h2>
            <small style="color:#8995a5">${(wl?.items||[]).length} saham dipantau</small>
          </div>
        </div>
        <div class="wl-controls-right">
          <button class="wl-view-btn ${state.viewMode==='grid'?'active':''}" id="wlViewGrid" title="Grid">▦</button>
          <button class="wl-view-btn ${state.viewMode==='table'?'active':''}" id="wlViewTable" title="Tabel">☰</button>
          <button class="wl-icon-btn" id="wlRefreshBtn" title="Refresh data">⟳</button>
          <button class="wl-icon-btn" id="wlExportBtn" title="Export CSV">↓ CSV</button>
        </div>
      </div>
      <div class="wl-controls">
        <div class="wl-controls-left">
          <button class="btn primary" id="wlAddBtn">＋ Tambah Saham</button>
          <select class="wl-select" id="wlSort">
            ${sortOptions.map(([v,l])=>`<option value="${v}" ${state.sortBy===v?'selected':''}>${l}</option>`).join('')}
          </select>
          <button class="wl-dir-btn" id="wlSortDir">${state.sortDir==='desc'?'↓':'↑'}</button>
          <select class="wl-select" id="wlFilterSignal">
            ${signalOptions.map(([v,l])=>`<option value="${v}" ${state.filterSignal===v?'selected':''}>${l}</option>`).join('')}
          </select>
          <select class="wl-select" id="wlFilterTrend">
            ${trendOptions.map(([v,l])=>`<option value="${v}" ${state.filterTrend===v?'selected':''}>${l}</option>`).join('')}
          </select>
        </div>
      </div>`;
  }

  /* ─────────────────────────────────────────────────────────────────
     RENDER — SUMMARY BAR
     ───────────────────────────────────────────────────────────────── */
  function renderSummaryBar() {
    const items = getItems();
    const total = items.length;
    const liveVals = Object.values(state.live);
    const hasBuy  = liveVals.filter(l => l.signal_class==='strong-buy'||l.signal_class==='buy').length;
    const hasSell = liveVals.filter(l => l.signal_class==='sell'||l.signal_class==='strong-sell').length;
    const up   = liveVals.filter(l => (l.change_pct||0)>0).length;
    const down = liveVals.filter(l => (l.change_pct||0)<0).length;
    const avgChange = liveVals.reduce((a,l)=>a+(l.change_pct||0),0)/(liveVals.length||1);
    return `
      <div class="wl-summary-bar">
        <div class="wl-sum-item"><span class="wl-sum-label">Total dipantau</span><span class="wl-sum-val">${total} saham</span></div>
        <div class="wl-sum-item wl-sum-buy"><span class="wl-sum-label">🟢 Sinyal Beli</span><span class="wl-sum-val">${hasBuy}</span></div>
        <div class="wl-sum-item wl-sum-sell"><span class="wl-sum-label">🔴 Sinyal Jual</span><span class="wl-sum-val">${hasSell}</span></div>
        <div class="wl-sum-item"><span class="wl-sum-label">↑ Naik / ↓ Turun</span>
          <span class="wl-sum-val"><span style="color:#0d7a52">${up}</span> / <span style="color:#c0283a">${down}</span></span></div>
        <div class="wl-sum-item"><span class="wl-sum-label">Rata-rata Perubahan</span>
          <span class="wl-sum-val" style="color:${avgChange>=0?'#0d7a52':'#c0283a'};font-weight:800">${fmtPct(avgChange)}</span></div>
        <span class="wl-sum-refresh">⟳ ${state.lastRefresh||'Belum dimuat'}</span>
      </div>`;
  }

  /* ─────────────────────────────────────────────────────────────────
     RENDER — CARD
     ───────────────────────────────────────────────────────────────── */
  function renderCard(item, live) {
    const code = item.code;
    const err  = live?.error;
    const price = live?.price ?? 0;
    const chgPct = live?.change_pct ?? 0;
    const up = chgPct >= 0;
    const chgColor = up ? '#0d7a52' : '#c0283a';
    const signal = live?.signal || '—';
    const sigCls = live?.signal_class || 'neutral';
    const pl = computePL(item.avgBuy, item.qty, price);
    const tp = item.tp || live?.tp_sug;
    const sl = item.sl || live?.sl_sug;
    const tpDist = dist(price, tp);
    const slDist = dist(price, sl);
    const prioIcon = item.priority==='high'?'🔴':item.priority==='low'?'🟢':'';

    const w52 = (live?.w52_hi&&live?.w52_lo) ? (() => {
      const range = live.w52_hi-live.w52_lo||1;
      const pct = Math.min(100,Math.max(0,((price-live.w52_lo)/range)*100));
      return `<div class="wl-52w-wrap">
        <div class="wl-52w-track"><div class="wl-52w-fill" style="width:${pct.toFixed(1)}%"></div>
        <div class="wl-52w-marker" style="left:${pct.toFixed(1)}%"></div></div>
        <div class="wl-52w-labels">
          <span style="color:#c0283a">${fmt(live.w52_lo)}</span>
          <span style="color:#8a95a5;font-size:9px">52W</span>
          <span style="color:#0d7a52">${fmt(live.w52_hi)}</span>
        </div></div>`;
    })() : '';

    return `
    <div class="wl-card" data-code="${code}" data-animate>
      <div class="wl-card-head">
        <div class="wl-card-identity">
          <div class="wl-card-code">${prioIcon} ${esc(code)}</div>
          <div class="wl-card-name">${esc(live?.name||code)}</div>
          ${live?.board?`<span class="wl-board-badge ${live.board==='Main'?'main':''}">${live.board}</span>`:''}
        </div>
        <div class="wl-card-actions-head">
          <div class="wl-signal-badge" style="${signalStyle(sigCls)}">${esc(signal)}</div>
          <div style="display:flex;gap:4px;margin-top:4px">
            <button class="wl-icon-btn" data-action="edit" data-code="${code}" title="Edit target & catatan">✏</button>
            <button class="wl-icon-btn" data-action="lab"  data-code="${code}" title="Buka di Stock Lab">🔍</button>
            <button class="wl-icon-btn danger" data-action="delete" data-code="${code}" title="Hapus dari watchlist">✕</button>
          </div>
        </div>
      </div>
      ${err?`<div class="wl-error-badge">⚠ ${esc(err)}</div>`:''}
      <div class="wl-price-row">
        <div>
          <div class="wl-price-label">Harga Terakhir</div>
          <div class="wl-price-val">${price?fmtRp(price):'—'}</div>
        </div>
        <div class="wl-change-block" style="color:${chgColor}">
          <div class="wl-change-pct">${up?'▲':'▼'} ${fmtPct(Math.abs(chgPct))}</div>
          <div class="wl-change-abs">${up?'+':''}${fmt(live?.change)}</div>
        </div>
      </div>
      <div class="wl-spark">${sparklineSVG(live?.sparkline, 280, 48)}</div>
      <div class="wl-indicators">
        <div class="wl-ind-item"><span class="wl-ind-label">RSI 14</span>
          <span class="wl-ind-val">${live?.rsi!=null?`<span style="color:${live.rsi<=30?'#0d7a52':live.rsi>=70?'#c0283a':'#3b82f6'};font-weight:800">${live.rsi}${live.rsi<=30?' (Oversold)':live.rsi>=70?' (Overbought)':''}</span>`:'<span class="wl-dim">—</span>'}</span></div>
        <div class="wl-ind-item"><span class="wl-ind-label">Tren</span>
          <span class="wl-ind-val" style="font-weight:700;color:${live?.trend==='Uptrend'?'#0d7a52':live?.trend==='Downtrend'?'#c0283a':'#6b7280'}">
            ${live?.trend==='Uptrend'?'↑ ':live?.trend==='Downtrend'?'↓ ':'→ '}${esc(live?.trend||'—')}</span></div>
        <div class="wl-ind-item"><span class="wl-ind-label">Vol / Avg20</span>
          <span class="wl-ind-val">${live?.vol_ratio!=null?`<span style="color:${live.vol_ratio>2?'#0d7a52':live.vol_ratio>1.5?'#2563eb':live.vol_ratio<0.5?'#c0283a':'#6b7280'};font-weight:700">${live.vol_ratio.toFixed(1)}×</span>`:'<span class="wl-dim">—</span>'}</span></div>
        <div class="wl-ind-item"><span class="wl-ind-label">Volume</span>
          <span class="wl-ind-val">${fmtVol(live?.volume)}</span></div>
      </div>
      ${w52}
      ${(tp||sl)?`<div class="wl-tp-sl">
        ${tp?`<div class="wl-tp-row"><span class="wl-tp-lbl">🎯 Target${item.tp?'':' (saran)'}:</span>
          <span class="wl-tp-price tp">${fmtRp(tp)}</span>
          <span class="wl-tp-pct tp">${tpDist!=null?(tpDist>=0?'+':'')+tpDist+'%':''}</span></div>`:''}
        ${sl?`<div class="wl-tp-row"><span class="wl-tp-lbl">🛑 Stop Loss${item.sl?'':' (saran)'}:</span>
          <span class="wl-tp-price sl">${fmtRp(sl)}</span>
          <span class="wl-tp-pct sl">${slDist!=null?slDist+'%':''}</span></div>`:''}
      </div>`:''}
      ${item.avgBuy?`<div class="wl-pl-row">
        <span class="wl-pl-label">Rata-rata Beli: <b>${fmtRp(item.avgBuy)}</b>${item.qty?` × ${fmt(item.qty)} lot`:''}</span>
        ${pl?`<span class="wl-pl-badge" style="color:${pl.color};background:${pl.color+'18'}">
          P/L ${pl.pct>=0?'+':''}${pl.pct}%${pl.nominal!=null?' / '+(pl.nominal>=0?'+':'')+'Rp '+Math.abs(pl.nominal).toLocaleString('id-ID'):''}</span>`:''}
      </div>`:''}
      ${item.note?`<div class="wl-note">📝 ${esc(item.note)}</div>`:''}
      <div class="wl-card-foot">
        <span class="wl-dim">Ditambah: ${item.addedAt}</span>
        <span class="wl-dim">Data: ${live?.date||'—'}</span>
      </div>
    </div>`;
  }

  /* ─────────────────────────────────────────────────────────────────
     RENDER — TABLE VIEW
     ───────────────────────────────────────────────────────────────── */
  function renderTable(sorted) {
    const rows = sorted.map(item => {
      const live = state.live[item.code] || {};
      const price = live.price??0, chgPct = live.change_pct??0;
      const up = chgPct>=0, chgColor = up?'#0d7a52':'#c0283a';
      const pl = computePL(item.avgBuy, item.qty, price);
      const tp = item.tp||live.tp_sug, sl = item.sl||live.sl_sug;
      return `<tr data-code="${item.code}">
        <td><div class="wl-tbl-code">${item.priority==='high'?'🔴 ':item.priority==='low'?'🟢 ':''}${esc(item.code)}</div>
          <div class="wl-tbl-name">${esc(live.name||item.code)}</div></td>
        <td class="number">${sparklineSVG(live.sparkline,80,28)}</td>
        <td class="number"><b>${price?fmtRp(price):'—'}</b></td>
        <td class="number" style="color:${chgColor};font-weight:700">${up?'▲':'▼'} ${fmtPct(Math.abs(chgPct))}</td>
        <td><span class="wl-signal-badge" style="${signalStyle(live.signal_class||'neutral')};font-size:10px;padding:3px 8px">${esc(live.signal||'—')}</span></td>
        <td class="number">${live.rsi!=null?`<span style="font-weight:700;color:${live.rsi<=30?'#0d7a52':live.rsi>=70?'#c0283a':'#3b82f6'}">${live.rsi}</span>`:'—'}</td>
        <td style="font-weight:700;color:${live.trend==='Uptrend'?'#0d7a52':live.trend==='Downtrend'?'#c0283a':'#6b7280'}">${live.trend==='Uptrend'?'↑ ':live.trend==='Downtrend'?'↓ ':'→ '}${esc(live.trend||'—')}</td>
        <td class="number">${tp?fmtRp(tp):'—'}</td>
        <td class="number">${sl?fmtRp(sl):'—'}</td>
        <td class="number" style="color:${pl?.color||'inherit'};font-weight:700">${pl?(pl.pct>=0?'+':'')+pl.pct+'%':'—'}</td>
        <td style="max-width:160px;color:#6b7280;font-size:11px">${item.note?esc(item.note.slice(0,50)):''}</td>
        <td><div style="display:flex;gap:3px">
          <button class="wl-icon-btn" data-action="edit" data-code="${item.code}" title="Edit">✏</button>
          <button class="wl-icon-btn" data-action="lab"  data-code="${item.code}" title="Stock Lab">🔍</button>
          <button class="wl-icon-btn danger" data-action="delete" data-code="${item.code}" title="Hapus">✕</button>
        </div></td>
      </tr>`;
    }).join('');

    return `<div class="wl-table-wrap">
      <table class="wl-table wide-table"><thead><tr>
        <th>Saham</th><th>Grafik</th><th class="number">Harga</th><th class="number">Perubahan</th>
        <th>Sinyal</th><th class="number">RSI</th><th>Tren</th>
        <th class="number">TP</th><th class="number">SL</th><th class="number">P/L</th>
        <th>Catatan</th><th></th>
      </tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  /* ─────────────────────────────────────────────────────────────────
     MAIN RENDER
     ───────────────────────────────────────────────────────────────── */
  function render() {
    const container = $('watchlistContent');
    if (!container) return;

    const wl    = activeList();
    const items = getItems();
    const sidebar = renderSidebar();

    let mainContent;
    if (!wl) {
      mainContent = `<div class="wl-empty">
        <div style="font-size:4rem;margin-bottom:16px">📋</div>
        <h3>Belum ada watchlist</h3>
        <p>Klik <b>＋ Baru</b> untuk membuat watchlist pertama Anda.</p>
      </div>`;
    } else if (items.length === 0) {
      mainContent = renderControls() + `<div class="wl-empty">
        <div style="font-size:4rem;margin-bottom:16px">📋</div>
        <h3>${esc(wl.icon)} ${esc(wl.name)} masih kosong</h3>
        <p>Tambahkan saham yang ingin Anda pantau dengan klik tombol <b>＋ Tambah Saham</b> di atas.</p>
        <button class="btn primary" id="wlAddBtnEmpty" style="margin-top:16px">＋ Tambah Saham Pertama</button>
      </div>`;
    } else {
      const sorted = getSorted();
      let body;
      if (state.loading && Object.keys(state.live).length === 0) {
        body = `<div class="wl-loading"><div class="wl-spinner"></div>
          <div><strong>Memuat data live…</strong>
          <p style="opacity:.6;font-size:13px;margin:4px 0 0">Mengambil harga & indikator untuk ${items.length} saham.</p></div></div>`;
      } else if (state.viewMode === 'grid') {
        body = `<div class="wl-grid">${sorted.map(item=>renderCard(item,state.live[item.code])).join('')}</div>`;
      } else {
        body = renderTable(sorted);
      }
      mainContent = renderControls() + renderSummaryBar() + body;
    }

    container.innerHTML = `<div class="wl-layout">${sidebar}<div class="wl-main">${mainContent}</div></div>`;
    bindEvents(container);

    container.querySelectorAll('[data-animate]').forEach((el,i) => {
      el.style.animation = `wlCardIn .3s ease ${i*0.04}s both`;
    });
  }

  /* ─────────────────────────────────────────────────────────────────
     BIND ALL EVENTS
     ───────────────────────────────────────────────────────────────── */
  function bindEvents(container) {
    // Sidebar: switch list
    container.querySelectorAll('.wl-list-row[data-wlid]').forEach(row => {
      row.addEventListener('click', e => {
        if (e.target.closest('[data-wl-action]')) return;
        const id = row.dataset.wlid;
        if (id !== state.activeId) {
          switchList(id);
          render();
          fetchLiveData();
        }
      });
    });

    // Sidebar: rename / delete watchlist
    container.querySelectorAll('[data-wl-action]').forEach(btn => {
      btn.addEventListener('click', e => {
        e.stopPropagation();
        const action = btn.dataset.wlAction;
        const id = btn.dataset.wlid;
        if (action === 'rename') showRenameModal(id);
        if (action === 'delete') confirmDeleteList(id);
      });
    });

    // New list button
    container.querySelector('#wlNewListBtn')?.addEventListener('click', showNewListModal);

    // Controls
    container.querySelector('#wlAddBtn')?.addEventListener('click', showAddModal);
    container.querySelector('#wlAddBtnEmpty')?.addEventListener('click', showAddModal);
    container.querySelector('#wlSort')?.addEventListener('change', e => { state.sortBy=e.target.value; saveStorage(); render(); });
    container.querySelector('#wlSortDir')?.addEventListener('click', () => { state.sortDir=state.sortDir==='desc'?'asc':'desc'; render(); });
    container.querySelector('#wlFilterSignal')?.addEventListener('change', e => { state.filterSignal=e.target.value; render(); });
    container.querySelector('#wlFilterTrend')?.addEventListener('change', e => { state.filterTrend=e.target.value; render(); });
    container.querySelector('#wlViewGrid')?.addEventListener('click', () => { state.viewMode='grid'; saveStorage(); render(); });
    container.querySelector('#wlViewTable')?.addEventListener('click', () => { state.viewMode='table'; saveStorage(); render(); });
    container.querySelector('#wlRefreshBtn')?.addEventListener('click', () => { state.live={}; fetchLiveData(); });
    container.querySelector('#wlExportBtn')?.addEventListener('click', exportCSV);

    // Card/table action buttons
    container.querySelectorAll('[data-action]').forEach(btn => {
      btn.addEventListener('click', e => {
        e.stopPropagation();
        const code = btn.dataset.code, action = btn.dataset.action;
        if (action === 'delete') { if (confirm(`Hapus ${code} dari watchlist?`)) { removeItem(code); render(); } }
        else if (action === 'edit') showEditModal(code);
        else if (action === 'lab')  openInStockLab(code);
      });
    });
  }

  /* ─────────────────────────────────────────────────────────────────
     MODAL: NEW WATCHLIST
     ───────────────────────────────────────────────────────────────── */
  function showNewListModal() {
    removeModal();
    const modal = document.createElement('div');
    modal.className = 'wl-modal-overlay';
    modal.id = 'wlModal';
    const iconBtns = WL_ICONS.map((ic,i) =>
      `<button class="wl-icon-pick ${i===0?'selected':''}" data-icon="${ic}">${ic}</button>`
    ).join('');

    modal.innerHTML = `
      <div class="wl-modal">
        <div class="wl-modal-head">
          <div><span class="section-kicker">WATCHLIST BARU</span>
            <h3>Buat Watchlist Baru</h3>
            <p>Atur nama dan ikon untuk watchlist Anda.</p></div>
          <button class="wl-modal-close" id="wlModalClose">✕</button>
        </div>
        <div class="wl-modal-body">
          <div class="wl-form-group">
            <label>Nama Watchlist *</label>
            <input type="text" id="wlNewName" class="wl-input" placeholder="Contoh: Saham Dividen, Growth Picks…" maxlength="40">
          </div>
          <div class="wl-form-group">
            <label>Pilih Ikon</label>
            <div class="wl-icon-grid" id="wlIconGrid">${iconBtns}</div>
          </div>
          <div id="wlAddError" class="wl-form-error" hidden></div>
        </div>
        <div class="wl-modal-foot">
          <button class="btn secondary" id="wlModalCancel">Batal</button>
          <button class="btn primary" id="wlModalSave">＋ Buat Watchlist</button>
        </div>
      </div>`;

    document.body.appendChild(modal);
    modal.querySelector('#wlModalClose').addEventListener('click', removeModal);
    modal.querySelector('#wlModalCancel').addEventListener('click', removeModal);
    modal.addEventListener('click', e => { if (e.target === modal) removeModal(); });
    modal.querySelector('#wlNewName').focus();

    // Icon picker
    let selectedIcon = WL_ICONS[0];
    modal.querySelector('#wlIconGrid').addEventListener('click', e => {
      const btn = e.target.closest('[data-icon]');
      if (!btn) return;
      modal.querySelectorAll('.wl-icon-pick').forEach(b => b.classList.remove('selected'));
      btn.classList.add('selected');
      selectedIcon = btn.dataset.icon;
    });

    modal.querySelector('#wlModalSave').addEventListener('click', () => {
      const name = modal.querySelector('#wlNewName').value.trim();
      if (!name) { showFormError(modal, 'Nama watchlist tidak boleh kosong.'); return; }
      createWatchlist(name, selectedIcon);
      removeModal();
      render();
      // No stocks yet, so no fetchLiveData needed
    });
  }

  /* ─────────────────────────────────────────────────────────────────
     MODAL: RENAME WATCHLIST
     ───────────────────────────────────────────────────────────────── */
  function showRenameModal(id) {
    removeModal();
    const wl = state.lists.find(l => l.id === id);
    if (!wl) return;
    const modal = document.createElement('div');
    modal.className = 'wl-modal-overlay';
    modal.id = 'wlModal';
    const iconBtns = WL_ICONS.map(ic =>
      `<button class="wl-icon-pick ${ic===wl.icon?'selected':''}" data-icon="${ic}">${ic}</button>`
    ).join('');

    modal.innerHTML = `
      <div class="wl-modal" style="max-width:460px">
        <div class="wl-modal-head">
          <div><span class="section-kicker">EDIT WATCHLIST</span>
            <h3>Ubah Nama & Ikon</h3></div>
          <button class="wl-modal-close" id="wlModalClose">✕</button>
        </div>
        <div class="wl-modal-body">
          <div class="wl-form-group">
            <label>Nama Watchlist</label>
            <input type="text" id="wlRenameName" class="wl-input" value="${esc(wl.name)}" maxlength="40">
          </div>
          <div class="wl-form-group">
            <label>Ikon</label>
            <div class="wl-icon-grid" id="wlIconGrid">${iconBtns}</div>
          </div>
        </div>
        <div class="wl-modal-foot">
          <button class="btn secondary" id="wlModalCancel">Batal</button>
          <button class="btn primary" id="wlModalSave">💾 Simpan</button>
        </div>
      </div>`;

    document.body.appendChild(modal);
    modal.querySelector('#wlModalClose').addEventListener('click', removeModal);
    modal.querySelector('#wlModalCancel').addEventListener('click', removeModal);
    modal.addEventListener('click', e => { if (e.target === modal) removeModal(); });
    modal.querySelector('#wlRenameName').focus();

    let selectedIcon = wl.icon;
    modal.querySelector('#wlIconGrid').addEventListener('click', e => {
      const btn = e.target.closest('[data-icon]');
      if (!btn) return;
      modal.querySelectorAll('.wl-icon-pick').forEach(b => b.classList.remove('selected'));
      btn.classList.add('selected');
      selectedIcon = btn.dataset.icon;
    });

    modal.querySelector('#wlModalSave').addEventListener('click', () => {
      const name = modal.querySelector('#wlRenameName').value.trim();
      renameWatchlist(id, name || wl.name, selectedIcon);
      removeModal();
      render();
    });
  }

  /* ─────────────────────────────────────────────────────────────────
     CONFIRM: DELETE WATCHLIST
     ───────────────────────────────────────────────────────────────── */
  function confirmDeleteList(id) {
    const wl = state.lists.find(l => l.id === id);
    if (!wl) return;
    if (!confirm(`Hapus watchlist "${wl.name}" beserta ${wl.items.length} saham di dalamnya?\n\nTindakan ini tidak dapat dibatalkan.`)) return;
    deleteWatchlist(id);
    render();
    fetchLiveData();
  }

  /* ─────────────────────────────────────────────────────────────────
     MODAL: ADD SAHAM
     ───────────────────────────────────────────────────────────────── */
  function showAddModal() {
    removeModal();
    const wl = activeList();
    const modal = document.createElement('div');
    modal.className = 'wl-modal-overlay';
    modal.id = 'wlModal';
    modal.innerHTML = `
      <div class="wl-modal">
        <div class="wl-modal-head">
          <div><span class="section-kicker">${esc(wl?.icon||'📋')} ${esc(wl?.name||'WATCHLIST')}</span>
            <h3>Tambah Saham ke Watchlist</h3>
            <p>Pisahkan dengan koma untuk menambah banyak sekaligus.</p></div>
          <button class="wl-modal-close" id="wlModalClose">✕</button>
        </div>
        <div class="wl-modal-body">
          <div class="wl-form-group">
            <label>Kode Saham * <small>(pisah koma: BBCA, TLKM, ASII)</small></label>
            <input type="text" id="wlAddCodes" class="wl-input" placeholder="Contoh: BBCA, TLKM, BMRI"
                   list="analyticsTickerList" autocomplete="off" style="text-transform:uppercase">
          </div>
          <div class="wl-form-row">
            <div class="wl-form-group">
              <label>Target Harga / TP <small>(opsional)</small></label>
              <input type="number" id="wlAddTp" class="wl-input" placeholder="Contoh: 10000" min="0">
            </div>
            <div class="wl-form-group">
              <label>Stop Loss / SL <small>(opsional)</small></label>
              <input type="number" id="wlAddSl" class="wl-input" placeholder="Contoh: 8500" min="0">
            </div>
          </div>
          <div class="wl-form-row">
            <div class="wl-form-group">
              <label>Harga Rata-rata Beli <small>(opsional)</small></label>
              <input type="number" id="wlAddAvg" class="wl-input" placeholder="Contoh: 9000" min="0">
            </div>
            <div class="wl-form-group">
              <label>Jumlah Lot <small>(opsional)</small></label>
              <input type="number" id="wlAddQty" class="wl-input" placeholder="Contoh: 10" min="0">
            </div>
          </div>
          <div class="wl-form-group">
            <label>Catatan Pribadi <small>(opsional)</small></label>
            <textarea id="wlAddNote" class="wl-input wl-textarea" placeholder="Alasan beli, strategi, atau catatan lain…" rows="2"></textarea>
          </div>
          <div class="wl-form-group">
            <label>Prioritas</label>
            <div class="wl-radio-group">
              <label><input type="radio" name="wlPrio" value="high"> 🔴 Tinggi</label>
              <label><input type="radio" name="wlPrio" value="normal" checked> ⚪ Normal</label>
              <label><input type="radio" name="wlPrio" value="low"> 🟢 Rendah</label>
            </div>
          </div>
          <div id="wlAddError" class="wl-form-error" hidden></div>
        </div>
        <div class="wl-modal-foot">
          <button class="btn secondary" id="wlModalCancel">Batal</button>
          <button class="btn primary" id="wlModalSave">＋ Tambahkan ke Watchlist</button>
        </div>
      </div>`;

    document.body.appendChild(modal);
    modal.querySelector('#wlModalClose').addEventListener('click', removeModal);
    modal.querySelector('#wlModalCancel').addEventListener('click', removeModal);
    modal.addEventListener('click', e => { if (e.target === modal) removeModal(); });
    modal.querySelector('#wlAddCodes').focus();

    modal.querySelector('#wlModalSave').addEventListener('click', () => {
      const raw = modal.querySelector('#wlAddCodes').value;
      const codes = raw.toUpperCase().split(/[\s,;]+/).map(c=>c.trim()).filter(Boolean);
      if (!codes.length) { showFormError(modal,'Masukkan minimal 1 kode saham.'); return; }
      const opts = {
        tp:       parseFloat(modal.querySelector('#wlAddTp').value)||null,
        sl:       parseFloat(modal.querySelector('#wlAddSl').value)||null,
        avgBuy:   parseFloat(modal.querySelector('#wlAddAvg').value)||null,
        qty:      parseFloat(modal.querySelector('#wlAddQty').value)||null,
        note:     modal.querySelector('#wlAddNote').value.trim(),
        priority: modal.querySelector('[name="wlPrio"]:checked').value,
      };
      const added = addItems(codes, opts);
      if (added === 0) { showFormError(modal,'Semua saham sudah ada di watchlist ini.'); return; }
      removeModal();
      fetchLiveData();
    });
  }

  /* ─────────────────────────────────────────────────────────────────
     MODAL: EDIT SAHAM
     ───────────────────────────────────────────────────────────────── */
  function showEditModal(code) {
    removeModal();
    const item = getItems().find(i => i.code === code);
    if (!item) return;
    const live = state.live[code] || {};
    const modal = document.createElement('div');
    modal.className = 'wl-modal-overlay';
    modal.id = 'wlModal';
    modal.innerHTML = `
      <div class="wl-modal">
        <div class="wl-modal-head">
          <div><span class="section-kicker">EDIT SAHAM</span>
            <h3>${esc(code)} — ${esc(live.name||code)}</h3>
            <p>Harga saat ini: <b>${fmtRp(live.price)}</b> ${live.change_pct!=null?`<span style="color:${live.change_pct>=0?'#0d7a52':'#c0283a'}">${fmtPct(live.change_pct)}</span>`:''}</p></div>
          <button class="wl-modal-close" id="wlModalClose">✕</button>
        </div>
        <div class="wl-modal-body">
          <div class="wl-form-row">
            <div class="wl-form-group">
              <label>Target Harga / TP</label>
              <input type="number" id="wlEditTp" class="wl-input" value="${item.tp||''}" placeholder="Harga target jual" min="0">
              ${live.tp_sug?`<small class="wl-hint">Saran engine: ${fmtRp(live.tp_sug)}</small>`:''}
            </div>
            <div class="wl-form-group">
              <label>Stop Loss / SL</label>
              <input type="number" id="wlEditSl" class="wl-input" value="${item.sl||''}" placeholder="Harga cut loss" min="0">
              ${live.sl_sug?`<small class="wl-hint">Saran engine: ${fmtRp(live.sl_sug)}</small>`:''}
            </div>
          </div>
          <div class="wl-form-row">
            <div class="wl-form-group">
              <label>Harga Rata-rata Beli</label>
              <input type="number" id="wlEditAvg" class="wl-input" value="${item.avgBuy||''}" placeholder="Rata-rata beli" min="0">
            </div>
            <div class="wl-form-group">
              <label>Jumlah Lot</label>
              <input type="number" id="wlEditQty" class="wl-input" value="${item.qty||''}" placeholder="Lot dimiliki" min="0">
            </div>
          </div>
          <div class="wl-form-group">
            <label>Catatan Pribadi</label>
            <textarea id="wlEditNote" class="wl-input wl-textarea" rows="3" placeholder="Strategi, alasan, target waktu…">${esc(item.note)}</textarea>
          </div>
          <div class="wl-form-group">
            <label>Prioritas</label>
            <div class="wl-radio-group">
              <label><input type="radio" name="wlEditPrio" value="high" ${item.priority==='high'?'checked':''}> 🔴 Tinggi</label>
              <label><input type="radio" name="wlEditPrio" value="normal" ${item.priority==='normal'?'checked':''}> ⚪ Normal</label>
              <label><input type="radio" name="wlEditPrio" value="low" ${item.priority==='low'?'checked':''}> 🟢 Rendah</label>
            </div>
          </div>
        </div>
        <div class="wl-modal-foot">
          <button class="btn danger" id="wlEditDelete" style="margin-right:auto">🗑 Hapus dari Watchlist</button>
          <button class="btn secondary" id="wlModalCancel">Batal</button>
          <button class="btn primary" id="wlModalSave">💾 Simpan Perubahan</button>
        </div>
      </div>`;

    document.body.appendChild(modal);
    modal.querySelector('#wlModalClose').addEventListener('click', removeModal);
    modal.querySelector('#wlModalCancel').addEventListener('click', removeModal);
    modal.addEventListener('click', e => { if (e.target === modal) removeModal(); });
    modal.querySelector('#wlEditDelete').addEventListener('click', () => {
      if (confirm(`Hapus ${code} dari watchlist?`)) { removeItem(code); removeModal(); render(); }
    });
    modal.querySelector('#wlModalSave').addEventListener('click', () => {
      updateItem(code, {
        tp:       parseFloat(modal.querySelector('#wlEditTp').value)||null,
        sl:       parseFloat(modal.querySelector('#wlEditSl').value)||null,
        avgBuy:   parseFloat(modal.querySelector('#wlEditAvg').value)||null,
        qty:      parseFloat(modal.querySelector('#wlEditQty').value)||null,
        note:     modal.querySelector('#wlEditNote').value.trim(),
        priority: modal.querySelector('[name="wlEditPrio"]:checked').value,
      });
      removeModal();
      render();
    });
  }

  /* ─────────────────────────────────────────────────────────────────
     HELPERS
     ───────────────────────────────────────────────────────────────── */
  function removeModal() { document.getElementById('wlModal')?.remove(); }

  function showFormError(modal, msg) {
    const el = modal.querySelector('#wlAddError');
    if (el) { el.textContent = msg; el.hidden = false; }
  }

  function openInStockLab(code) {
    const codeInput = document.getElementById('stockLabCode');
    if (codeInput) codeInput.value = code;
    const navBtn = document.querySelector('.nav-item[data-view="stocklab"]');
    if (navBtn) navBtn.click();
    setTimeout(() => {
      const form = document.getElementById('stockLabForm');
      if (form) form.dispatchEvent(new Event('submit', { bubbles: true }));
    }, 300);
  }

  /* ─────────────────────────────────────────────────────────────────
     FETCH LIVE DATA
     ───────────────────────────────────────────────────────────────── */
  async function fetchLiveData() {
    const items = getItems();
    if (!items.length) { render(); return; }
    state.loading = true;
    render();

    const codes = items.map(i => i.code).join(',');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      const res = await fetch(`/api/watchlist/data?codes=${encodeURIComponent(codes)}`, { signal: controller.signal });
      clearTimeout(timer);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      state.live = {};
      (data.items||[]).forEach(item => { state.live[item.code] = item; });
      state.lastRefresh = new Date().toLocaleTimeString('id-ID',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
    } catch (err) {
      clearTimeout(timer);
      if (err.name !== 'AbortError') console.error('Watchlist fetch error:', err);
    }
    state.loading = false;
    render();
  }

  /* ─────────────────────────────────────────────────────────────────
     EXPORT CSV
     ───────────────────────────────────────────────────────────────── */
  function exportCSV() {
    const wl = activeList();
    const headers = ['Watchlist','Kode','Nama','Harga','Perubahan%','RSI','Tren','Sinyal','TP User','SL User','TP Saran','SL Saran','Avg Beli','P/L%','Lot','Catatan','Prioritas','Ditambah'];
    const rows = getItems().map(item => {
      const l = state.live[item.code]||{};
      const pl = computePL(item.avgBuy, item.qty, l.price);
      return [wl?.name||'',item.code,l.name||item.code,l.price||'',l.change_pct||'',
        l.rsi||'',l.trend||'',l.signal||'',item.tp||'',item.sl||'',l.tp_sug||'',l.sl_sug||'',
        item.avgBuy||'',pl?.pct||'',item.qty||'',item.note||'',item.priority,item.addedAt]
        .map(v=>`"${String(v).replace(/"/g,'""')}"`).join(',');
    });
    const csv = [headers.join(','),...rows].join('\n');
    const blob = new Blob(['\ufeff'+csv],{type:'text/csv;charset=utf-8'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href=url; a.download=`watchlist_${wl?.name||'export'}_${new Date().toISOString().slice(0,10)}.csv`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  }

  /* ─────────────────────────────────────────────────────────────────
     INIT
     ───────────────────────────────────────────────────────────────── */
  let _activated = false;

  function onActivate() {
    if (_activated) { render(); return; }
    _activated = true;
    loadStorage();
    render();
    fetchLiveData();

    state.refreshTimer = setInterval(() => {
      const view = document.getElementById('watchlistView');
      if (view && !view.hidden && getItems().length) fetchLiveData();
    }, 60 * 1000);
  }

  document.addEventListener('zaiden:viewchange', e => {
    if (e.detail?.view === 'watchlist') onActivate();
  });

})();
