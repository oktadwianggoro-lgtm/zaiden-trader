/* =====================================================================
   BROKER.JS — Aktivitas Broker Anggota Bursa IDX
   4 sub-view: Papan Harian · Tren Bulanan · Per Broker · Master
   ===================================================================== */
(() => {
  'use strict';

  /* ── Helpers ──────────────────────────────────────────────────────── */
  const $  = (s, ctx) => (ctx || document).querySelector(s);
  const $$ = (s, ctx) => [...(ctx || document).querySelectorAll(s)];

  const idNum   = new Intl.NumberFormat('id-ID');
  const idCmpct = new Intl.NumberFormat('id-ID', { notation:'compact', maximumFractionDigits:2 });

  const fmt  = v => idNum.format(Number(v || 0));
  const fmtM = v => {
    const n = Number(v || 0);
    if (!n) return '—';
    if (Math.abs(n) >= 1e12) return `Rp ${idCmpct.format(n/1e12)} T`;
    if (Math.abs(n) >= 1e9)  return `Rp ${idCmpct.format(n/1e9)} M`;
    if (Math.abs(n) >= 1e6)  return `Rp ${idCmpct.format(n/1e6)} jt`;
    return `Rp ${fmt(n)}`;
  };
  const fmtPct = (v, d=2) => v == null ? '—' : `${Number(v).toFixed(d).replace('.', ',')}%`;
  const esc    = (v='') => String(v).replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmtDate = v => v ? new Date(`${String(v).slice(0,10)}T00:00:00`)
    .toLocaleDateString('id-ID',{day:'2-digit',month:'short',year:'numeric'}) : '—';
  const fmtMon  = v => v ? new Date(`${String(v).slice(0,10)}T00:00:00`)
    .toLocaleDateString('id-ID',{day:'2-digit',month:'short'}) : '—';
  const addDays = (iso, n) => { const d=new Date(`${iso}T00:00:00`); d.setDate(d.getDate()+n); return d.toISOString().slice(0,10); };
  const todayIso = () => new Date().toISOString().slice(0,10);
  const isoYMD   = (y,m,d) => `${y}-${String(m+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;

  const csvBlob = (hdrs, rows) => {
    const c = v => `"${String(v??'').replace(/"/g,'""')}"`;
    return new Blob(['\ufeff'+[hdrs,...rows].map(r=>r.map(c).join(',')).join('\r\n')],
      {type:'text/csv;charset=utf-8'});
  };
  const dlBlob  = (blob, name) => {
    const url=URL.createObjectURL(blob), a=document.createElement('a');
    a.href=url; a.download=name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  const setTbody = (sel, cols, html) => {
    const el = $(sel);
    if (el) el.innerHTML = html ?? `<tr><td colspan="${cols}" class="loading-cell">—</td></tr>`;
  };
  const loadingRow = (cols, msg) =>
    `<tr><td colspan="${cols}" class="loading-cell"><div class="broker-loading-wrap"><span class="broker-spinner"></span>${esc(msg)}</div></td></tr>`;
  const errorRow = (cols, msg) =>
    `<tr><td colspan="${cols}" class="loading-cell" style="color:#d65463"><b>Gagal memuat:</b> ${esc(msg)}</td></tr>`;

  /* ── HTTP ─────────────────────────────────────────────────────────── */
  async function api(url) {
    const r = await fetch(url, {cache:'no-store'});
    const j = await r.json().catch(()=>({}));
    if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  }

  /* ══════════════════════════════════════════════════════════════════
     DATE RANGE PICKER — uses shared ZaidenDRP factory from drp.js
     ══════════════════════════════════════════════════════════════════ */
  let brokerDrp = null;   // ZaidenDRP instance
  let drpReady  = false;

  function initBrokerDrp(latest, tradingDates) {
    if (drpReady || !window.ZaidenDRP) return;
    drpReady = true;

    brokerDrp = window.ZaidenDRP.create({
      wrapId:        'brokerDrpWrap',
      btnId:         'brokerDrpBtn',
      labelId:       'brokerDrpLabel',
      defaultPreset: 3,   // 28 hari terakhir
    });

    brokerDrp.init(latest, tradingDates, (df, dt) => loadDaily(df, dt));

    // Load with default 28d range now
    const r = brokerDrp.getRange();
    if (r.from && r.to) loadDaily(r.from, r.to);
  }

  /* ══════════════════════════════════════════════════════════════════
     STATE
     ══════════════════════════════════════════════════════════════════ */
  const S = {
    view:'daily', initialized:false,
    // daily
    daily:null, dSort:'nilai', dDir:'desc', dQ:'', dPage:1, dPageSz:20,
    // monthly
    monthly:null, mCode:null, mMetric:'total_nilai',
    // profile
    profile:null, pCode:'XL',
    // master
    master:null, msQ:'', msPage:1, msPageSz:20,
  };

  /* ── Sub-view switching ───────────────────────────────────────────── */
  function switchSub(name) {
    S.view = name;
    $$('#brokerView .broker-tab-btn').forEach(b => b.classList.toggle('active', b.dataset.sub===name));
    $$('#brokerView .broker-sub').forEach(el => {
      el.hidden = el.id !== `broker${name.charAt(0).toUpperCase()+name.slice(1)}`;
    });
  }

  /* ══════════════════════════════════════════════════════════════════
     1. PAPAN HARIAN
     ══════════════════════════════════════════════════════════════════ */

  async function loadDaily(df=null, dt=null) {
    setTbody('#brokerDailyBody', 8, loadingRow(8,'Memuat ranking broker…'));
    try {
      let url = '/api/broker/daily';
      const p = [];
      if (df) p.push(`date_from=${df}`);
      if (dt) p.push(`date_to=${dt}`);
      if (p.length) url += '?'+p.join('&');

      const data = await api(url);
      S.daily = data;

      // Init DRP once after we have trading dates
      if (!drpReady && data.available_dates?.length) {
        initBrokerDrp(data.available_dates[0], data.available_dates);
        return;   // initBrokerDrp will trigger loadDaily again with real range
      }

      // Render info badge
      const rd = $('#brokerDailyRangeInfo');
      const set=(id,v)=>{const el=$(id);if(el)el.textContent=v;};
      if (rd && data.actual_from) {
        rd.innerHTML = `Menampilkan aktivitas dari <b>${data.actual_from}</b> s.d. <b>${data.actual_to}</b> (${data.actual_days} hari bursa).`;
      }
      set('#brokerLatestDateHero', data.available_dates && data.available_dates.length ? data.available_dates[0] : '—');

      renderDailySummary(data.total);
      renderDailyTable();
      renderDailyChart(data.items);
    } catch(e) {
      setTbody('#brokerDailyBody', 8, errorRow(8, e.message));
    }
  }

  function renderDailySummary(t) {
    if (!t) return;
    const set = (id, v) => { const el=$(id); if(el) el.textContent=v; };
    set('#brokerDailyStatNilai',  fmtM(t.nilai));
    set('#brokerDailyStatVolume', fmt(t.volume));
    set('#brokerDailyStatFreq',   fmt(t.frekuensi));
    set('#brokerDailyStatCount',  (t.brokers||0)+' broker');
  }



  function renderDailyTable() {
    const items = S.daily?.items || [];
    const q     = S.dQ.trim().toUpperCase();
    let filtered = q ? items.filter(r => r.code.includes(q) || r.name.toUpperCase().includes(q)) : [...items];

    const col=S.dSort, dir=S.dDir==='asc'?1:-1;
    filtered.sort((a,b) => {
      const va=a[col]??-Infinity, vb=b[col]??-Infinity;
      return typeof va==='string' ? va.localeCompare(vb)*dir : (va-vb)*dir;
    });

    const total=filtered.length, pages=Math.max(1,Math.ceil(total/S.dPageSz));
    S.dPage=Math.min(S.dPage,pages);
    const start=(S.dPage-1)*S.dPageSz;
    const page=filtered.slice(start,start+S.dPageSz);

    // update sort indicators on headers
    $$('#brokerDailyTable th[data-sort]').forEach(th => {
      const raw = th.dataset.label || th.textContent.replace(/[↕▲▼]/g,'').trim();
      th.dataset.label = raw;
      const arrow = th.dataset.sort===col ? (dir>0?' ▲':' ▼') : ' ↕';
      th.textContent = raw+arrow;
    });

    if (!page.length) {
      setTbody('#brokerDailyBody', 8, `<tr><td colspan="8" class="loading-cell">Data tidak ditemukan.</td></tr>`);
    } else {
      $('#brokerDailyBody').innerHTML = page.map(r => `
        <tr class="broker-row" data-code="${esc(r.code)}">
          <td class="td-num"><b>${r.rank}</b></td>
          <td><button class="ticker broker-link" data-broker="${esc(r.code)}">${esc(r.code)}</button></td>
          <td class="td-name"><span title="${esc(r.name)}">${esc(r.name)}</span></td>
          <td class="td-num">${fmtM(r.nilai)}</td>
          <td class="td-num">
            <div class="broker-bar-wrap">
              <div class="broker-bar-fill" style="width:${Math.min(100,r.share_nilai||0).toFixed(2)}%"></div>
              <span>${fmtPct(r.share_nilai)}</span>
            </div>
          </td>
          <td class="td-num">${fmt(r.volume)}</td>
          <td class="td-num">${fmt(r.frekuensi)}</td>
          <td class="td-num">${fmtPct(r.share_frekuensi)}</td>
        </tr>`).join('');
    }

    const end=Math.min(start+S.dPageSz,total);
    const set=(id,v)=>{const el=$(id);if(el)el.textContent=v;};
    set('#brokerDailyPageInfo',`${fmt(start+1)}–${fmt(end)} dari ${fmt(total)}`);
    set('#brokerDailyPageNum',`${S.dPage} / ${pages}`);
    const prev=$('#brokerDailyPrev'), next=$('#brokerDailyNext');
    if(prev) prev.disabled=S.dPage<=1;
    if(next) next.disabled=S.dPage>=pages;
  }

  /* Horizontal bar chart — Top 10 */
  function renderDailyChart(items=[]) {
    const el = $('#brokerDailyChart');
    if (!el) return;
    const top = items.slice(0,10);
    if (!top.length) { el.innerHTML='<div class="broker-chart-empty">Tidak ada data.</div>'; return; }

    const maxV = top[0].nilai || 1;
    const COLORS = ['#2f68e8','#7658d6','#18a06f','#dc9624','#3b91a4','#d65463','#5e7ec8','#a36bc1','#2a9d8f','#e9c46a'];

    el.innerHTML = `<div class="broker-hbar">${
      top.map((r,i) => `
        <div class="broker-hbar-row">
          <span class="broker-hbar-code">${esc(r.code)}</span>
          <div class="broker-hbar-track">
            <div class="broker-hbar-fill" style="width:${(r.nilai/maxV*100).toFixed(2)}%;background:${COLORS[i%COLORS.length]}"></div>
          </div>
          <span class="broker-hbar-val">${fmtM(r.nilai)}</span>
          <span class="broker-hbar-pct">${fmtPct(r.share_nilai)}</span>
        </div>`).join('')
    }</div>`;
  }

  /* ══════════════════════════════════════════════════════════════════
     2. TREN BULANAN
     ══════════════════════════════════════════════════════════════════ */
  async function loadMonthly(code=null) {
    S.mCode = code;
    setTbody('#brokerMonthlyBody', 5, loadingRow(5,'Memuat tren bulanan…'));
    try {
      const url = code
        ? `/api/broker/monthly?code=${encodeURIComponent(code)}`
        : '/api/broker/monthly';
      S.monthly = await api(url);
      renderMonthlyHeader();
      renderMonthlyChart();
      renderMonthlyTable();
    } catch(e) { setTbody('#brokerMonthlyBody', 5, errorRow(5,e.message)); }
  }

  function renderMonthlyHeader() {
    const el = $('#brokerMonthlyTitle');
    if (!el) return;
    const bi = S.monthly?.broker_info;
    el.textContent = bi
      ? `Tren Bulanan — ${bi.kode_broker} · ${bi.nama_broker}`
      : 'Tren Bulanan — Seluruh Pasar';
  }

  function renderMonthlyTable() {
    const items = S.monthly?.items || [];
    if (!items.length) { setTbody('#brokerMonthlyBody',5,`<tr><td colspan="5" class="loading-cell">Tidak ada data.</td></tr>`); return; }
    $('#brokerMonthlyBody').innerHTML = items.map(r=>`<tr>
      <td><b>${esc(r.bulan)}</b></td>
      <td class="td-num">${r.hari_aktif} hari</td>
      <td class="td-num">${fmtM(r.total_nilai)}</td>
      <td class="td-num">${fmt(r.total_volume)}</td>
      <td class="td-num">${fmt(r.total_frekuensi)}</td>
    </tr>`).join('');
  }

  function renderMonthlyChart() {
    const el = $('#brokerMonthlyChart');
    if (!el) return;
    const items = S.monthly?.items || [];
    if (!items.length) { el.innerHTML='<div class="broker-chart-empty">Tidak ada data.</td></tr>'; return; }

    const metric = S.mMetric;
    const data   = [...items].reverse();          // oldest → newest
    const vals   = data.map(r => r[metric]||0);
    const maxV   = Math.max(...vals, 1);

    const W=el.clientWidth||700, H=220;
    const pad={t:16,r:16,b:40,l:68};
    const cW=W-pad.l-pad.r, cH=H-pad.t-pad.b;
    const bW=Math.max(3,Math.min(40, cW/data.length*0.65));
    const getX=i => pad.l + (i+0.5)*(cW/data.length);
    const getY=v => pad.t+cH-(v/maxV)*cH;

    let bars='',xLbls='',yGrid='';
    const step=Math.max(1,Math.ceil(data.length/10));

    data.forEach((r,i)=>{
      const x=getX(i), val=r[metric]||0, y=getY(val), h=pad.t+cH-y;
      bars+=`<rect x="${(x-bW/2).toFixed(1)}" y="${y.toFixed(1)}" width="${bW.toFixed(1)}" height="${Math.max(1,h).toFixed(1)}" fill="#2f68e8" fill-opacity=".78" rx="2"><title>${esc(r.bulan)}: ${metric==='total_nilai'?fmtM(val):fmt(val)}</title></rect>`;
      if (i%step===0||i===data.length-1)
        xLbls+=`<text x="${x.toFixed(1)}" y="${H-10}" text-anchor="middle" class="svg-axis" font-size="8">${esc(r.bulan)}</text>`;
    });
    for(let i=0;i<=4;i++){
      const v=(maxV/4)*i, y=getY(v);
      const lbl=metric==='total_nilai'?fmtM(v).replace('Rp ',''):idCmpct.format(v);
      yGrid+=`<line x1="${pad.l}" y1="${y.toFixed(1)}" x2="${W-pad.r}" y2="${y.toFixed(1)}" stroke="#edf0f4" stroke-width="1"/>`;
      yGrid+=`<text x="${pad.l-6}" y="${(y+3).toFixed(1)}" text-anchor="end" class="svg-axis" font-size="8">${esc(lbl)}</text>`;
    }
    el.innerHTML=`<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block;overflow:visible">${yGrid}${bars}${xLbls}</svg>`;
  }

  /* ══════════════════════════════════════════════════════════════════
     3. PER BROKER (PROFIL)
     ══════════════════════════════════════════════════════════════════ */
  async function loadProfile(code) {
    code = (code||'').trim().toUpperCase();
    if (!code) return;
    S.pCode = code;
    setTbody('#brokerProfileBody', 4, loadingRow(4,`Memuat profil ${code}…`));
    const chartEl = $('#brokerProfileChart');
    if (chartEl) chartEl.innerHTML='<div class="broker-chart-empty">Memuat…</div>';
    try {
      S.profile = await api(`/api/broker/profile?code=${encodeURIComponent(code)}`);
      renderProfileCard();
      renderProfileChart();
      renderProfileTable();
    } catch(e) {
      setTbody('#brokerProfileBody', 4, errorRow(4, e.message));
      if (chartEl) chartEl.innerHTML=`<div class="broker-chart-empty" style="color:#d65463">${esc(e.message)}</div>`;
    }
  }

  function renderProfileCard() {
    const p = S.profile?.profile;
    if (!p) return;
    const set=(id,v)=>{const el=$(id);if(el)el.textContent=v;};
    set('#brokerProfileCode',   p.kode_broker);
    set('#brokerProfileName',   p.nama_broker);
    set('#brokerProfileRank',   p.rank_ytd ? `#${p.rank_ytd} YTD` : '—');
    set('#brokerProfileStatus', p.status   || '—');
    set('#brokerProfileYtdNilai', fmtM(p.ytd_nilai));
    set('#brokerProfileYtdVol',   fmt(p.ytd_volume));
    set('#brokerProfileYtdFreq',  fmt(p.ytd_frekuensi));
    set('#brokerProfileYtdDays',  (p.ytd_days||0)+' hari');
  }

  function renderProfileChart() {
    const el = $('#brokerProfileChart');
    if (!el) return;
    const hist = S.profile?.history || [];
    if (!hist.length) { el.innerHTML='<div class="broker-chart-empty">Riwayat kosong.</div>'; return; }

    const data  = [...hist].reverse();
    const W=el.clientWidth||700, H=220;
    const pad={t:16,r:68,b:28,l:68};
    const cW=W-pad.l-pad.r, cH=H-pad.t-pad.b;
    const nVals=data.map(d=>d.nilai||0), fVals=data.map(d=>d.frekuensi||0);
    const maxN=Math.max(...nVals,1), maxF=Math.max(...fVals,1);
    const bW=Math.max(2,Math.min(14,cW/data.length*0.55));
    const getX=i=>pad.l+(i+.5)*(cW/data.length);
    const getYN=v=>pad.t+cH-(v/maxN)*cH;
    const getYF=v=>pad.t+cH-(v/maxF)*cH;

    let bars='', linePts='', yGrid='', xLbls='';
    const step=Math.max(1,Math.ceil(data.length/10));

    data.forEach((d,i)=>{
      const x=getX(i);
      // bar = frekuensi (right scale, purple)
      const yF=getYF(d.frekuensi||0), hF=pad.t+cH-yF;
      bars+=`<rect x="${(x-bW/2).toFixed(1)}" y="${yF.toFixed(1)}" width="${bW.toFixed(1)}" height="${Math.max(1,hF).toFixed(1)}" fill="#7658d6" fill-opacity=".28" rx="1"><title>${esc(d.tanggal||'')} frek: ${fmt(d.frekuensi)}</title></rect>`;
      // line = nilai (left scale, blue)
      const yN=getYN(d.nilai||0);
      linePts+=`${i===0?'M':'L'}${x.toFixed(1)},${yN.toFixed(1)} `;
      if (i%step===0||i===data.length-1)
        xLbls+=`<text x="${x.toFixed(1)}" y="${H-8}" text-anchor="middle" class="svg-axis" font-size="8">${esc((d.tanggal||'').slice(0,10))}</text>`;
    });

    for(let i=0;i<=4;i++){
      const yPos=pad.t+(cH/4)*(4-i);
      yGrid+=`<line x1="${pad.l}" y1="${yPos.toFixed(1)}" x2="${W-pad.r}" y2="${yPos.toFixed(1)}" stroke="#edf0f4" stroke-width="1"/>`;
      yGrid+=`<text x="${pad.l-6}" y="${(yPos+3).toFixed(1)}" text-anchor="end" class="svg-axis" font-size="8">${esc(fmtM((maxN/4)*i).replace('Rp ',''))}</text>`;
      yGrid+=`<text x="${W-pad.r+6}" y="${(yPos+3).toFixed(1)}" text-anchor="start" class="svg-axis" font-size="8" fill="#7658d6">${esc(idCmpct.format((maxF/4)*i))}</text>`;
    }

    el.innerHTML=`
      <div class="broker-chart-legend">
        <span style="color:#2f68e8">━ Nilai Transaksi</span>
        <span style="color:#7658d6">▮ Frekuensi</span>
      </div>
      <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block;overflow:visible">
        ${yGrid}${bars}
        <path d="${linePts.trim()}" fill="none" stroke="#2f68e8" stroke-width="2" stroke-linejoin="round"/>
        ${xLbls}
      </svg>`;
  }

  function renderProfileTable() {
    const hist = S.profile?.history || [];
    if (!hist.length) { setTbody('#brokerProfileBody',4,`<tr><td colspan="4" class="loading-cell">Riwayat kosong.</td></tr>`); return; }
    $('#brokerProfileBody').innerHTML = hist.map(r=>`<tr>
      <td>${fmtDate(r.tanggal)}</td>
      <td class="td-num">${fmtM(r.nilai)}</td>
      <td class="td-num">${fmt(r.volume)}</td>
      <td class="td-num">${fmt(r.frekuensi)}</td>
    </tr>`).join('');
  }

  /* ══════════════════════════════════════════════════════════════════
     4. MASTER BROKER
     ══════════════════════════════════════════════════════════════════ */
  async function loadMaster() {
    if (S.master) { renderMasterTable(); return; }
    setTbody('#brokerMasterBody', 5, loadingRow(5,'Memuat direktori broker…'));
    try {
      S.master = await api('/api/broker/master');
      fillDatalist(S.master.items);
      renderMasterTable();
    } catch(e) { setTbody('#brokerMasterBody',5,errorRow(5,e.message)); }
  }

  function fillDatalist(items=[]) {
    const dl = $('#brokerCodeList');
    if (!dl) return;
    dl.innerHTML = items.map(r=>
      `<option value="${esc(r.kode_broker)}">${esc(r.kode_broker)} — ${esc(r.nama_broker)}</option>`
    ).join('');
  }

  function renderMasterTable() {
    const items = S.master?.items || [];
    const q     = S.msQ.trim().toUpperCase();
    let filtered = q ? items.filter(r=>r.kode_broker.includes(q)||(r.nama_broker||'').toUpperCase().includes(q)) : [...items];
    const total=filtered.length, pages=Math.max(1,Math.ceil(total/S.msPageSz));
    S.msPage=Math.min(S.msPage,pages);
    const start=(S.msPage-1)*S.msPageSz;
    const page=filtered.slice(start,start+S.msPageSz);

    if (!page.length) {
      setTbody('#brokerMasterBody',5,`<tr><td colspan="5" class="loading-cell">Data tidak ditemukan.</td></tr>`);
    } else {
      $('#brokerMasterBody').innerHTML = page.map((r,i)=>`<tr>
        <td class="td-num">${start+i+1}</td>
        <td><button class="ticker broker-link" data-broker="${esc(r.kode_broker)}">${esc(r.kode_broker)}</button></td>
        <td>${esc(r.nama_broker)}</td>
        <td>${r.status?`<span class="badge-neutral">${esc(r.status)}</span>`:'—'}</td>
        <td><span class="badge-neutral">${esc(r.sumber||'—')}</span></td>
      </tr>`).join('');
    }

    const end=Math.min(start+S.msPageSz,total);
    const set=(id,v)=>{const el=$(id);if(el)el.textContent=v;};
    set('#brokerMasterPageInfo',`${fmt(start+1)}–${fmt(end)} dari ${fmt(total)}`);
    set('#brokerMasterPageNum',`${S.msPage} / ${pages}`);
    const prev=$('#brokerMasterPrev'), next=$('#brokerMasterNext');
    if(prev) prev.disabled=S.msPage<=1;
    if(next) next.disabled=S.msPage>=pages;
  }

  /* ══════════════════════════════════════════════════════════════════
     EVENT BINDING
     ══════════════════════════════════════════════════════════════════ */
  function bindEvents() {
    const view = $('#brokerView');
    if (!view) return;

    /* ─ Tab buttons ─ */
    $$('.broker-tab-btn', view).forEach(btn => {
      btn.addEventListener('click', () => {
        const sub = btn.dataset.sub;
        switchSub(sub);
        if (sub==='daily'   && !S.daily)   loadDaily();
        if (sub==='monthly' && !S.monthly) loadMonthly();
        if (sub==='profile' && !S.profile) loadProfile(S.pCode);
        if (sub==='master'  && !S.master)  loadMaster();
      });
    });

    /* ─ Daily: search ─ */
    let dTimer;
    $('#brokerDailySearch')?.addEventListener('input', e => {
      clearTimeout(dTimer);
      dTimer=setTimeout(()=>{S.dQ=e.target.value;S.dPage=1;renderDailyTable();},200);
    });

    /* ─ Daily: sort headers ─ */
    $$('#brokerDailyTable th[data-sort]').forEach(th => {
      th.addEventListener('click', ()=>{
        const col=th.dataset.sort;
        if(S.dSort===col) S.dDir=S.dDir==='asc'?'desc':'asc';
        else { S.dSort=col; S.dDir='desc'; }
        S.dPage=1; renderDailyTable();
      });
    });

    /* ─ Daily: pagination ─ */
    $('#brokerDailyPrev')?.addEventListener('click', ()=>{
      if(S.dPage>1){S.dPage--;renderDailyTable();}
    });
    $('#brokerDailyNext')?.addEventListener('click', ()=>{
      const pages=Math.ceil((S.daily?.items?.length||0)/S.dPageSz);
      if(S.dPage<pages){S.dPage++;renderDailyTable();}
    });

    /* ─ Daily: export ─ */
    $('#brokerDailyExportBtn')?.addEventListener('click', ()=>{
      if(!S.daily?.items) return;
      const hdrs=['Rank','Kode','Nama Broker','Nilai Transaksi','Share Nilai (%)','Volume','Frekuensi','Share Frekuensi (%)'];
      const rows=S.daily.items.map(r=>[r.rank,r.code,r.name,r.nilai,r.share_nilai,r.volume,r.frekuensi,r.share_frekuensi]);
      const lbl=`${S.daily.date_from||''}--${S.daily.date_to||''}`;
      dlBlob(csvBlob(hdrs,rows),`broker-${lbl}.csv`);
    });

    $('#brokerSyncBtn')?.addEventListener('click', async () => {
      const btn = $('#brokerSyncBtn');
      const stat = $('#brokerSyncStatus');
      if(btn.disabled) return;
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner" style="margin-right:8px; border-top-color:white;"></span> Proses...';
      stat.innerHTML = '';
      stat.className = 'sync-status running';
      try {
        const res = await api('/api/broker/sync');
        stat.innerHTML = '✔ Data broker berhasil diperbarui.';
        stat.className = 'sync-status success';
        setTimeout(() => window.location.reload(), 1500);
      } catch (err) {
        stat.innerHTML = '❌ Gagal: ' + err.message;
        stat.className = 'sync-status failed';
      } finally {
        btn.disabled = false;
        btn.innerHTML = '🔄 Perbarui Data IDX';
      }
    });

    /* ─ Monthly: mode/code/metric ─ */
    $('#brokerMonthlyMode')?.addEventListener('change', e=>{
      const mode=e.target.value;
      const wrap=$('#brokerMonthlyCodeWrap');
      if(wrap) wrap.style.display=mode==='broker'?'flex':'none';
      if(mode==='all') { S.monthly=null; loadMonthly(null); }
    });
    $('#brokerMonthlyLoadBtn')?.addEventListener('click', ()=>{
      const code=($('#brokerMonthlyCode')?.value||'').trim().toUpperCase();
      if(code){ S.monthly=null; loadMonthly(code); }
    });
    $('#brokerMonthlyMetric')?.addEventListener('change', e=>{
      S.mMetric=e.target.value;
      if(S.monthly){ renderMonthlyTable(); renderMonthlyChart(); }
    });
    $('#brokerMonthlyExportBtn')?.addEventListener('click', ()=>{
      if(!S.monthly?.items) return;
      const hdrs=['Bulan','Hari Aktif','Total Nilai','Total Volume','Total Frekuensi'];
      const rows=S.monthly.items.map(r=>[r.bulan,r.hari_aktif,r.total_nilai,r.total_volume,r.total_frekuensi]);
      dlBlob(csvBlob(hdrs,rows),`broker-bulanan-${S.mCode||'all'}.csv`);
    });

    /* ─ Profile: form submit ─ */
    $('#brokerProfileForm')?.addEventListener('submit', e=>{
      e.preventDefault();
      const code=($('#brokerProfileInput')?.value||'').trim().toUpperCase();
      if(code) loadProfile(code);
    });

    /* ─ Profile: export ─ */
    $('#brokerProfileExportBtn')?.addEventListener('click', ()=>{
      if(!S.profile?.history) return;
      const hdrs=['Tanggal','Nilai Transaksi','Volume','Frekuensi'];
      const rows=S.profile.history.map(r=>[r.tanggal,r.nilai,r.volume,r.frekuensi]);
      dlBlob(csvBlob(hdrs,rows),`profil-broker-${S.pCode}.csv`);
    });

    /* ─ Master: search ─ */
    let msTimer;
    $('#brokerMasterSearch')?.addEventListener('input', e=>{
      clearTimeout(msTimer);
      msTimer=setTimeout(()=>{S.msQ=e.target.value;S.msPage=1;renderMasterTable();},200);
    });
    $('#brokerMasterPrev')?.addEventListener('click', ()=>{if(S.msPage>1){S.msPage--;renderMasterTable();}});
    $('#brokerMasterNext')?.addEventListener('click', ()=>{
      const pages=Math.ceil((S.master?.items?.length||0)/S.msPageSz);
      if(S.msPage<pages){S.msPage++;renderMasterTable();}
    });

    /* ─ Delegate: broker code click → profile ─ */
    view.addEventListener('click', e=>{
      const btn=e.target.closest('.broker-link');
      if(!btn) return;
      const code=btn.dataset.broker;
      if(!code) return;
      switchSub('profile');
      const inp=$('#brokerProfileInput');
      if(inp) inp.value=code;
      loadProfile(code);
    });

    /* ─ Resize: re-render charts ─ */
    let rTimer;
    window.addEventListener('resize', ()=>{
      clearTimeout(rTimer);
      rTimer=setTimeout(()=>{
        if(S.view==='daily'   && S.daily)   renderDailyChart(S.daily.items);
        if(S.view==='monthly' && S.monthly) renderMonthlyChart();
        if(S.view==='profile' && S.profile) renderProfileChart();
      },200);
    });
  }

  /* ══════════════════════════════════════════════════════════════════
     INIT
     ══════════════════════════════════════════════════════════════════ */
  function init() {
    bindEvents();

    document.addEventListener('zaiden:viewchange', e=>{
      if(e.detail.view !== 'broker') return;
      if(S.initialized) return;
      S.initialized = true;
      // Pre-load master for datalist (background)
      api('/api/broker/master').then(d=>{ S.master=d; fillDatalist(d.items); }).catch(()=>{});
      // Load daily (will init DRP internally)
      loadDaily();
    });
  }

  init();
})();
