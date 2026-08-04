/**
 * drp.js — Shared Date Range Picker (DRP) Factory
 * Reusable across: Akumulasi, Broker Harian, dan semua filter tanggal.
 *
 * Usage:
 *   const picker = ZaidenDRP.create({
 *     wrapId:    'myDrpWrap',    // id container div
 *     btnId:     'myDrpBtn',     // id trigger button
 *     labelId:   'myDrpLabel',   // id span label text
 *     latest:    '2026-07-11',   // string YYYY-MM-DD
 *     trading:   ['2026-07-11',…], // array trading dates
 *     onApply:   (dateFrom, dateTo) => { ... },
 *     defaultPreset: 3,          // index in PRESETS (default: 28d = 3)
 *   });
 *
 *   picker.getRange()  → { from, to }
 *   picker.setLatest(date) → update latest & trading
 */

window.ZaidenDRP = (() => {
  'use strict';

  /* ── Shared helpers ───────────────────────────────────────────────── */
  const esc = (v = '') => String(v).replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  const fmtDate = v => v ? new Date(`${String(v).slice(0,10)}T00:00:00`)
    .toLocaleDateString('id-ID',{day:'2-digit',month:'short',year:'numeric'}) : '—';
  const fmtMon  = v => v ? new Date(`${String(v).slice(0,10)}T00:00:00`)
    .toLocaleDateString('id-ID',{day:'2-digit',month:'short'}) : '—';

  const addDays = (iso, n) => {
    const d = new Date(`${iso}T00:00:00`); d.setDate(d.getDate() + n);
    return d.toISOString().slice(0,10);
  };
  const todayIso = () => new Date().toISOString().slice(0,10);
  const isoYMD   = (y,m,d) => `${y}-${String(m+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;

  const MONTHS_ID  = ['Januari','Februari','Maret','April','Mei','Juni',
                      'Juli','Agustus','September','Oktober','November','Desember'];
  const DAYS_SHORT = ['Min','Sen','Sel','Rab','Kam','Jum','Sab'];

  const PRESETS = [
    { label:'Hari ini',          fn: t => [t, t] },
    { label:'Kemarin',           fn: t => [addDays(t,-1), addDays(t,-1)] },
    { label:'7 hari terakhir',   fn: t => [addDays(t,-6), t] },
    { label:'28 hari terakhir',  fn: t => [addDays(t,-27), t] },
    { label:'30 hari terakhir',  fn: t => [addDays(t,-29), t] },
    { label:'Bulan ini',         fn: t => {
        const d=new Date(`${t}T00:00:00`);
        return [`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-01`, t];
      }
    },
    { label:'Bulan lalu',        fn: t => {
        const d=new Date(`${t}T00:00:00`); d.setDate(1); d.setMonth(d.getMonth()-1);
        const y=d.getFullYear(), m=d.getMonth(), last=new Date(y,m+1,0);
        return [isoYMD(y,m,1), isoYMD(last.getFullYear(),last.getMonth(),last.getDate())];
      }
    },
    { label:'90 hari terakhir',  fn: t => [addDays(t,-89), t] },
    { label:'6 bulan terakhir',  fn: t => {
        const d=new Date(`${t}T00:00:00`); d.setMonth(d.getMonth()-6);
        return [d.toISOString().slice(0,10), t];
      }
    },
    { label:'Tahun ini (YTD)',   fn: t => [`${t.slice(0,4)}-01-01`, t] },
    { label:'Semua data',        fn: (t, latest) => ['2020-01-02', latest || t] },
    { label:'Kustom…',           fn: null },
  ];

  /* ── Counter for unique IDs (support multiple instances) ────────── */
  let _counter = 0;

  /* ── Factory ──────────────────────────────────────────────────────── */
  function create(opts = {}) {
    const id  = ++_counter;
    const uid = s => `${s}_${id}`;   // unique element IDs per instance

    /* state */
    const s = {
      latest:  opts.latest  || todayIso(),
      trading: new Set(opts.trading || []),
      start:   null, end: null,
      tmp0: null, tmp1: null,
      hover: null, step: 0,
      vy: new Date().getFullYear(),
      vm: new Date().getMonth(),
      preset: null,
      open: false,
      onApply: opts.onApply || (() => {}),
    };

    /* ── Internal helpers ─────────────────────────────────────────── */
    const $ = sel => document.querySelector(sel);
    const getBtn   = () => $(`#${opts.btnId}`);
    const getLabel = () => $(`#${opts.labelId}`);
    const getPanel = () => $(`#${uid('drpPanel')}`);
    const getWrap  = () => $(`#${opts.wrapId}`);

    /* ── Label ──────────────────────────────────────────────────────── */
    function updateLabel() {
      const el = getLabel(); if (!el) return;
      if (!s.start) { el.textContent = 'Pilih rentang'; return; }
      const pre   = (s.preset && s.preset !== 'Kustom…') ? s.preset + ' · ' : '';
      const range = s.start === s.end
        ? fmtDate(s.start)
        : `${fmtMon(s.start)} – ${fmtMon(s.end)} ${s.end.slice(0,4)}`;
      el.textContent = pre + range;
    }

    /* ── Calendar render ────────────────────────────────────────────── */
    function renderCal() {
      const grid  = $(`#${uid('calGrid')}`);
      const title = $(`#${uid('calTitle')}`);
      if (!grid) return;

      const m1y=s.vy, m1m=s.vm;
      const m2d=new Date(m1y,m1m+1,1), m2y=m2d.getFullYear(), m2m=m2d.getMonth();
      if (title) title.textContent = `${MONTHS_ID[m1m]} ${m1y}  ·  ${MONTHS_ID[m2m]} ${m2y}`;
      grid.innerHTML = monthHTML(m1y,m1m) + monthHTML(m2y,m2m);

      grid.querySelectorAll('.dc:not(.dc-disabled)').forEach(cell => {
        const iso = cell.dataset.d;
        if (!iso) return;
        cell.addEventListener('mouseenter', () => {
          if (s.step===1) { s.hover=iso; renderCal(); }
        });
        cell.addEventListener('mouseleave', () => {
          if (s.step===1) { s.hover=null; renderCal(); }
        });
        cell.addEventListener('click', () => {
          if (s.step===0) {
            s.tmp0=iso; s.tmp1=null; s.step=1; s.preset='Kustom…';
            refreshPresets(); syncInputs(); renderCal();
          } else {
            let [a,b]=[s.tmp0,iso]; if(b<a)[a,b]=[b,a];
            s.tmp0=a; s.tmp1=b; s.step=0; s.hover=null;
            syncInputs(); renderCal();
          }
        });
      });
      refreshPresets();
    }

    function monthHTML(y, m) {
      const first = new Date(y,m,1).getDay();
      const dim   = new Date(y,m+1,0).getDate();
      const today = todayIso();
      const selS  = s.tmp0 || s.start;
      const selE  = s.step===1 ? (s.hover||null) : (s.tmp1||s.end);
      const rMin  = selS&&selE ? (selS<selE?selS:selE) : null;
      const rMax  = selS&&selE ? (selS<selE?selE:selS) : null;

      let html = `<div class="drp-month"><div class="drp-month-head">${MONTHS_ID[m]} ${y}</div><div class="drp-month-grid">`;
      DAYS_SHORT.forEach(d => { html+=`<div class="drp-dh">${d}</div>`; });
      for(let i=0;i<first;i++) html+='<div class="dc dc-empty"></div>';
      for(let d=1;d<=dim;d++){
        const iso=isoYMD(y,m,d);
        const isFut=iso>s.latest, isT=s.trading.has(iso), isToday=iso===today;
        const isS=iso===selS, isE=iso===selE, inRng=rMin&&rMax&&iso>rMin&&iso<rMax;
        const cls=['dc',
          isFut?'dc-disabled':'', isT?'dc-trading':'', isToday?'dc-today':'',
          isS?'dc-sel-start':'', isE?'dc-sel-end':'', inRng?'dc-in-range':'',
          (isS&&isE)?'dc-single':'',
        ].filter(Boolean).join(' ');
        html+=`<div class="${cls}" data-d="${iso}">${d}</div>`;
      }
      html+='</div></div>';
      return html;
    }

    /* ── Sync text inputs ───────────────────────────────────────────── */
    function syncInputs() {
      const inS=$(`#${uid('inputStart')}`), inE=$(`#${uid('inputEnd')}`);
      if(inS){ inS.value=s.tmp0||s.start||''; inS.classList.toggle('drp-active',s.step===0); }
      if(inE){ inE.value=(s.step===0?s.tmp1||s.end:s.tmp1)||''; inE.classList.toggle('drp-active',s.step===1); }
      const lS=$(`#${uid('labelStart')}`), lE=$(`#${uid('labelEnd')}`);
      if(lS) lS.classList.toggle('drp-label-active', s.step===0);
      if(lE) lE.classList.toggle('drp-label-active', s.step===1);
    }

    /* ── Preset highlight ───────────────────────────────────────────── */
    function refreshPresets() {
      getPanel()?.querySelectorAll('.drp-preset-btn').forEach(b =>
        b.classList.toggle('active', b.textContent.trim() === s.preset));
    }

    /* ── Build panel DOM ────────────────────────────────────────────── */
    function build() {
      const wrap = getWrap(); if (!wrap || $(`#${uid('drpPanel')}`)) return;
      const panel = document.createElement('div');
      panel.id        = uid('drpPanel');
      panel.className = 'drp-panel';
      panel.hidden    = true;

      panel.innerHTML = `
        <div class="drp-side">
          <div class="drp-presets-title">Rentang Waktu</div>
          <div class="drp-presets" id="${uid('presets')}">
            ${PRESETS.map((p,i)=>`<button class="drp-preset-btn" data-idx="${i}">${esc(p.label)}</button>`).join('')}
          </div>
        </div>
        <div class="drp-main">
          <div class="drp-date-inputs">
            <div class="drp-date-group">
              <label class="drp-date-label" id="${uid('labelStart')}">Mulai dari</label>
              <input id="${uid('inputStart')}" class="drp-date-input" type="text" placeholder="YYYY-MM-DD" maxlength="10" autocomplete="off">
            </div>
            <div class="drp-date-arrow">→</div>
            <div class="drp-date-group">
              <label class="drp-date-label" id="${uid('labelEnd')}">Sampai</label>
              <input id="${uid('inputEnd')}" class="drp-date-input" type="text" placeholder="YYYY-MM-DD" maxlength="10" autocomplete="off">
            </div>
          </div>
          <div class="drp-cal-header">
            <button class="drp-nav-btn" id="${uid('navPrev')}" title="Bulan sebelumnya">‹</button>
            <span class="drp-cal-title" id="${uid('calTitle')}"></span>
            <button class="drp-nav-btn" id="${uid('navNext')}" title="Bulan berikutnya">›</button>
          </div>
          <div class="drp-cal-grid" id="${uid('calGrid')}"></div>
          <div class="drp-actions">
            <button class="btn secondary" id="${uid('btnCancel')}">Batal</button>
            <button class="btn primary"   id="${uid('btnApply')}">Terapkan</button>
          </div>
        </div>`;

      wrap.appendChild(panel);

      /* preset clicks */
      panel.querySelectorAll('.drp-preset-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          const p = PRESETS[+btn.dataset.idx];
          s.preset = p.label;
          if (p.fn) {
            const [a,b] = p.fn(s.latest, s.latest);
            s.tmp0=a; s.tmp1=b; s.step=0;
            const d=new Date(`${b}T00:00:00`); s.vm=d.getMonth(); s.vy=d.getFullYear();
          } else {
            s.tmp0=s.start; s.tmp1=s.end; s.step=0;
          }
          refreshPresets(); syncInputs(); renderCal();
        });
      });

      $(`#${uid('navPrev')}`).addEventListener('click', () => {
        const d=new Date(s.vy,s.vm-1,1); s.vy=d.getFullYear(); s.vm=d.getMonth(); renderCal();
      });
      $(`#${uid('navNext')}`).addEventListener('click', () => {
        const d=new Date(s.vy,s.vm+1,1); s.vy=d.getFullYear(); s.vm=d.getMonth(); renderCal();
      });

      $(`#${uid('inputStart')}`).addEventListener('change', e => {
        if (/^\d{4}-\d{2}-\d{2}$/.test(e.target.value)) { s.tmp0=e.target.value; s.step=1; renderCal(); }
      });
      $(`#${uid('inputEnd')}`).addEventListener('change', e => {
        if (/^\d{4}-\d{2}-\d{2}$/.test(e.target.value)) { s.tmp1=e.target.value; s.step=0; renderCal(); }
      });

      $(`#${uid('btnApply')}`).addEventListener('click', () => {
        const a=s.tmp0||s.start, b=s.tmp1||s.end;
        if (!a||!b) return;
        s.start=a<=b?a:b; s.end=a<=b?b:a;
        s.tmp0=s.tmp1=null; s.step=0;
        updateLabel(); close();
        s.onApply(s.start, s.end);
      });

      $(`#${uid('btnCancel')}`).addEventListener('click', () => {
        s.tmp0=s.tmp1=null; s.step=0; close();
      });

      renderCal(); refreshPresets(); syncInputs();
    }

    /* ── Open / close ───────────────────────────────────────────────── */
    function open() {
      s.open=true; s.tmp0=s.start; s.tmp1=s.end; s.step=0;
      syncInputs(); refreshPresets(); renderCal();
      const p=getPanel(); if(p) p.hidden=false;
      getBtn()?.classList.add('active');
    }
    function close() {
      s.open=false;
      const p=getPanel(); if(p) p.hidden=true;
      getBtn()?.classList.remove('active');
    }

    /* ── Trigger + outside-click ────────────────────────────────────── */
    function bindTrigger() {
      const btn=getBtn(); if(!btn) return;
      btn.addEventListener('click', e => { e.stopPropagation(); s.open ? close() : open(); });
      document.addEventListener('click', e => {
        if (s.open && !getWrap()?.contains(e.target)) close();
      });
      document.addEventListener('keydown', e => { if(e.key==='Escape'&&s.open) close(); });
    }

    /* ── Apply default preset ───────────────────────────────────────── */
    function applyDefault(presetIdx) {
      const p = PRESETS[presetIdx ?? 3];
      s.preset = p.label;
      if (p.fn) {
        const [a,b] = p.fn(s.latest, s.latest);
        s.start=a; s.end=b;
        const d=new Date(`${b}T00:00:00`); s.vm=d.getMonth(); s.vy=d.getFullYear();
      }
      updateLabel();
    }

    /* ── Public init ────────────────────────────────────────────────── */
    function init(latest, trading, onApply) {
      if (latest) s.latest = latest;
      if (trading) s.trading = new Set(trading);
      if (onApply) s.onApply = onApply;
      applyDefault(opts.defaultPreset ?? 3);
      build();
      bindTrigger();
    }

    /* ── Public API ─────────────────────────────────────────────────── */
    return {
      init,
      getRange: () => ({ from: s.start, to: s.end }),
      setLatest: (latest, trading) => {
        s.latest = latest;
        if (trading) s.trading = new Set(trading);
        renderCal();
      },
      updateLabel,
    };
  }

  return { create, PRESETS };
})();
