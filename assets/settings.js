/* =====================================================================
   ZAIDEN TRADER — Settings & Theme Engine v1.0
   Kelola tampilan: tema, aksen, ukuran font, kerapatan, animasi, sidebar
   ===================================================================== */
(() => {
  'use strict';

  /* ── Storage key ──────────────────────────────────────────────────── */
  const STORE_KEY = 'zaiden_settings_v1';

  /* ── Defaults ─────────────────────────────────────────────────────── */
  const DEFAULTS = {
    theme:     'classic',
    accent:    null,          // null = use theme default
    fontSize:  'md',          // sm | md | lg | xl
    density:   'normal',      // compact | normal | spacious
    animation: true,
    sidebar:   'full',        // full | compact
  };

  /* ── Theme catalogue ─────────────────────────────────────────────── */
  const THEMES = [
    {
      id: 'classic',
      name: 'Zaiden Classic',
      desc: 'Navy sidebar elegan. Default Zaiden Trader.',
      icon: '🎯',
      badge: 'Default',
      preview: { sidebar: '#0c1424', main: '#f4f6fa', card: '#ffffff', accent: '#2f68e8' },
      accentLocked: false,
    },
    {
      id: 'dark',
      name: 'Dark Mode',
      desc: 'Mode gelap penuh. Nyaman untuk mata.',
      icon: '🌑',
      badge: null,
      preview: { sidebar: '#090d17', main: '#0f1623', card: '#161e2e', accent: '#4d85f5' },
      accentLocked: false,
    },
    {
      id: 'google',
      name: 'Google Material',
      desc: 'Material Design. Bersih & familiar.',
      icon: '🎨',
      badge: 'Popular',
      preview: { sidebar: '#1a73e8', main: '#f8f9fa', card: '#ffffff', accent: '#1a73e8' },
      accentLocked: true,
      defaultAccent: '#1a73e8',
    },
    {
      id: 'apple',
      name: 'Apple Design',
      desc: 'macOS style. Minimalis & presisi.',
      icon: '🍎',
      badge: null,
      preview: { sidebar: '#1c1c1e', main: '#f5f5f7', card: '#ffffff', accent: '#007aff' },
      accentLocked: true,
      defaultAccent: '#007aff',
    },
    {
      id: 'microsoft',
      name: 'Microsoft Fluent',
      desc: 'Fluent Design System. Profesional.',
      icon: '🪟',
      badge: null,
      preview: { sidebar: '#201f1e', main: '#f3f2f1', card: '#ffffff', accent: '#0078d4' },
      accentLocked: true,
      defaultAccent: '#0078d4',
    },
    {
      id: 'ocean',
      name: 'Ocean Deep',
      desc: 'Biru teal dalam laut. Tenang & fokus.',
      icon: '🌊',
      badge: null,
      preview: { sidebar: '#023e58', main: '#e8f4f8', card: '#ffffff', accent: '#00b4d8' },
      accentLocked: true,
      defaultAccent: '#00b4d8',
    },
    {
      id: 'sunset',
      name: 'Sunset Premium',
      desc: 'Ungu dalam, aksen oranye. Artistik.',
      icon: '🌅',
      badge: null,
      preview: { sidebar: '#1a0a2e', main: '#fafaf9', card: '#ffffff', accent: '#f97316' },
      accentLocked: true,
      defaultAccent: '#f97316',
    },
    {
      id: 'worldclass',
      name: 'World Class ★',
      desc: 'Tampilan premium terbaik dunia. Gold & Glassmorphism.',
      icon: '🏆',
      badge: '★ Terbaik',
      preview: { sidebar: '#0a0612', main: '#080712', card: '#14112a', accent: '#c9a227' },
      accentLocked: true,
      defaultAccent: '#c9a227',
    },
  ];

  /* ── Accent presets ──────────────────────────────────────────────── */
  const ACCENT_PRESETS = [
    { color: '#2f68e8', label: 'Biru Zaiden' },
    { color: '#7857d8', label: 'Violet' },
    { color: '#0078d4', label: 'Biru Microsoft' },
    { color: '#007aff', label: 'Biru Apple' },
    { color: '#1a73e8', label: 'Biru Google' },
    { color: '#00b4d8', label: 'Teal' },
    { color: '#06d6a0', label: 'Emerald' },
    { color: '#f97316', label: 'Oranye' },
    { color: '#ef4444', label: 'Merah' },
    { color: '#ec4899', label: 'Pink' },
    { color: '#c9a227', label: 'Emas' },
    { color: '#64748b', label: 'Slate' },
  ];

  /* ── Load / Save settings ────────────────────────────────────────── */
  function loadSettings() {
    try {
      const raw = localStorage.getItem(STORE_KEY);
      if (raw) return { ...DEFAULTS, ...JSON.parse(raw) };
    } catch (_) {}
    return { ...DEFAULTS };
  }

  function saveSettings(s) {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(s)); } catch (_) {}
  }

  /* ── Apply theme ─────────────────────────────────────────────────── */
  function applyTheme(themeId) {
    const html = document.documentElement;
    if (themeId === 'classic') {
      html.removeAttribute('data-theme');
    } else {
      html.setAttribute('data-theme', themeId);
    }
  }

  /* ── Apply accent color ──────────────────────────────────────────── */
  function applyAccent(color, theme) {
    const t = THEMES.find(t => t.id === theme);
    const effectiveColor = (t?.accentLocked ? (t.defaultAccent ?? null) : color) ?? color;
    if (!effectiveColor) {
      document.documentElement.style.removeProperty('--blue');
      document.documentElement.style.removeProperty('--blue-soft');
      return;
    }
    document.documentElement.style.setProperty('--blue', effectiveColor);
    document.documentElement.style.setProperty('--blue-soft', hexToAlpha(effectiveColor, 0.12));
  }

  function hexToAlpha(hex, alpha) {
    const r = parseInt(hex.slice(1,3), 16);
    const g = parseInt(hex.slice(3,5), 16);
    const b = parseInt(hex.slice(5,7), 16);
    return `rgba(${r},${g},${b},${alpha})`;
  }

  /* ── Apply font size ─────────────────────────────────────────────── */
  const FONT_SIZES = { sm: '13px', md: '15px', lg: '17px', xl: '19px' };
  function applyFontSize(size) {
    document.documentElement.style.setProperty('--fs-base', FONT_SIZES[size] || '15px');
    document.body.style.fontSize = FONT_SIZES[size] || '';
    // Update font-size utility class on html
    ['font-sm','font-md','font-lg','font-xl'].forEach(c => document.documentElement.classList.remove(c));
    document.documentElement.classList.add(`font-${size}`);
  }

  /* ── Apply density ───────────────────────────────────────────────── */
  function applyDensity(d) {
    ['density-compact','density-normal','density-spacious'].forEach(c => document.body.classList.remove(c));
    document.body.classList.add(`density-${d}`);
  }

  /* ── Apply animation ─────────────────────────────────────────────── */
  function applyAnimation(on) {
    document.body.classList.toggle('no-animation', !on);
  }

  /* ── Apply sidebar ───────────────────────────────────────────────── */
  function applySidebar(mode) {
    document.body.classList.toggle('sidebar-compact', mode === 'compact');
  }

  /* ── Apply all settings ──────────────────────────────────────────── */
  function applyAll(s) {
    applyTheme(s.theme);
    applyAccent(s.accent, s.theme);
    applyFontSize(s.fontSize);
    applyDensity(s.density);
    applyAnimation(s.animation);
    applySidebar(s.sidebar);
  }

  /* ════════════════════════════════════════════════════════════════════
     SETTINGS PAGE UI
     ════════════════════════════════════════════════════════════════════ */

  /* ── Render theme preview card ───────────────────────────────────── */
  function renderThemeCard(t, isActive) {
    const p = t.preview;
    const sbItems = [
      `<div class="preview-sb-item active" style="background:${p.accent}88"></div>`,
      `<div class="preview-sb-item" style="background:rgba(255,255,255,.15)"></div>`,
      `<div class="preview-sb-item" style="background:rgba(255,255,255,.15)"></div>`,
      `<div class="preview-sb-item" style="background:rgba(255,255,255,.1)"></div>`,
    ].join('');

    return `
    <div class="theme-card${isActive ? ' active' : ''}" data-theme-id="${t.id}" title="${t.name}">
      ${t.badge ? `<div class="theme-card-badge">${t.badge}</div>` : ''}
      <div class="theme-preview-wrap">
        <div class="theme-preview-inner">
          <div class="preview-sb" style="background:${p.sidebar};width:28%;padding:8px 5px;gap:5px;display:flex;flex-direction:column">
            <div style="height:8px;border-radius:4px;background:${p.accent};width:60%;margin-bottom:3px"></div>
            ${sbItems}
          </div>
          <div class="preview-main-area" style="background:${p.main}">
            <div class="preview-metric-row">
              <div class="preview-metric" style="background:${p.card};border:1px solid ${p.card === '#ffffff' ? '#e0e8f0' : 'rgba(255,255,255,.08)'}"></div>
              <div class="preview-metric" style="background:${p.card};border:1px solid ${p.card === '#ffffff' ? '#e0e8f0' : 'rgba(255,255,255,.08)'}"></div>
            </div>
            <div class="preview-table" style="background:${p.card};border:1px solid ${p.card === '#ffffff' ? '#e0e8f0' : 'rgba(255,255,255,.08)'}">
              <div style="height:6px;background:${p.accent}28;width:100%;border-radius:2px 2px 0 0"></div>
            </div>
          </div>
        </div>
      </div>
      <div class="theme-card-info">
        <span class="theme-card-name">${t.icon} ${t.name}</span>
        <span class="theme-card-desc">${t.desc}</span>
      </div>
      <div class="theme-active-tick">✓</div>
    </div>`;
  }

  /* ── Render settings page ────────────────────────────────────────── */
  function renderSettings(container, s) {
    const currentTheme = THEMES.find(t => t.id === s.theme) || THEMES[0];
    const accentLocked = currentTheme.accentLocked;
    const effectiveAccent = accentLocked ? (currentTheme.defaultAccent ?? s.accent) : (s.accent ?? '#2f68e8');

    container.innerHTML = `
    <div class="settings-page">

      <!-- Tema Visual -->
      <div class="settings-section">
        <div class="settings-section-title">
          <span>🎨</span> TEMA VISUAL
        </div>
        <div class="theme-grid" id="settingsThemeGrid">
          ${THEMES.map(t => renderThemeCard(t, t.id === s.theme)).join('')}
        </div>
      </div>

      <!-- Warna Aksen -->
      <div class="settings-section" id="settingsAccentSection">
        <div class="settings-section-title">
          <span>🎯</span> WARNA AKSEN
          ${accentLocked ? `<span style="font-size:11px;font-weight:600;letter-spacing:0;text-transform:none;padding:3px 10px;background:var(--blue-soft);color:var(--blue);border-radius:20px;">Terkunci oleh tema</span>` : ''}
        </div>
        <div class="accent-swatches" id="settingsAccentSwatches">
          ${ACCENT_PRESETS.map(a => `
            <div class="accent-swatch ${!accentLocked && a.color === effectiveAccent ? 'active' : ''}"
                 data-accent="${a.color}"
                 title="${a.label}"
                 style="background:${a.color};${accentLocked ? 'opacity:.35;pointer-events:none' : ''}">
            </div>`).join('')}
        </div>
        <div class="accent-custom-wrap" ${accentLocked ? 'style="opacity:.35;pointer-events:none"' : ''}>
          <span class="accent-custom-label">Warna kustom:</span>
          <input type="color" class="accent-picker" id="settingsAccentPicker"
                 value="${effectiveAccent}"
                 title="Pilih warna aksen kustom">
          <span style="font-size:12px;color:var(--muted)">Klik lingkaran untuk memilih warna bebas</span>
        </div>
      </div>

      <!-- Ukuran Font -->
      <div class="settings-section">
        <div class="settings-section-title"><span>📝</span> UKURAN TEKS</div>
        <div class="font-size-options">
          <button class="option-btn ${s.fontSize==='sm'?'active':''}" data-font="sm">
            <span class="option-icon">A</span><div><div>Kecil</div><small style="font-size:10px;font-weight:500">13px</small></div>
          </button>
          <button class="option-btn ${s.fontSize==='md'?'active':''}" data-font="md">
            <span class="option-icon" style="font-size:18px">A</span><div><div>Normal</div><small style="font-size:10px;font-weight:500">15px</small></div>
          </button>
          <button class="option-btn ${s.fontSize==='lg'?'active':''}" data-font="lg">
            <span class="option-icon" style="font-size:22px">A</span><div><div>Besar</div><small style="font-size:10px;font-weight:500">17px</small></div>
          </button>
          <button class="option-btn ${s.fontSize==='xl'?'active':''}" data-font="xl">
            <span class="option-icon" style="font-size:26px">A</span><div><div>Ekstra Besar</div><small style="font-size:10px;font-weight:500">19px</small></div>
          </button>
        </div>
      </div>

      <!-- Kerapatan -->
      <div class="settings-section">
        <div class="settings-section-title"><span>📊</span> KERAPATAN DATA</div>
        <div class="density-options">
          <button class="option-btn ${s.density==='compact'?'active':''}" data-density="compact">
            <span class="option-icon">⊟</span><div><div>Kompak</div><small style="font-size:10px;font-weight:500">Lebih banyak data</small></div>
          </button>
          <button class="option-btn ${s.density==='normal'?'active':''}" data-density="normal">
            <span class="option-icon">☰</span><div><div>Normal</div><small style="font-size:10px;font-weight:500">Default</small></div>
          </button>
          <button class="option-btn ${s.density==='spacious'?'active':''}" data-density="spacious">
            <span class="option-icon">▤</span><div><div>Longgar</div><small style="font-size:10px;font-weight:500">Lebih nyaman</small></div>
          </button>
        </div>
      </div>

      <!-- Sidebar mode -->
      <div class="settings-section">
        <div class="settings-section-title"><span>◧</span> MODE SIDEBAR</div>
        <div class="sidebar-options">
          <button class="option-btn ${s.sidebar==='full'?'active':''}" data-sidebar="full">
            <span class="option-icon">◧</span><div><div>Penuh</div><small style="font-size:10px;font-weight:500">Teks & ikon</small></div>
          </button>
          <button class="option-btn ${s.sidebar==='compact'?'active':''}" data-sidebar="compact">
            <span class="option-icon">◫</span><div><div>Kompak</div><small style="font-size:10px;font-weight:500">Ikon saja</small></div>
          </button>
        </div>
      </div>

      <!-- Animasi & Efek -->
      <div class="settings-section">
        <div class="settings-section-title"><span>✨</span> ANIMASI &amp; EFEK</div>
        <div class="toggle-row">
          <div class="toggle-info">
            <strong>Animasi halus</strong>
            <small>Transisi & micro-animation pada seluruh elemen UI</small>
          </div>
          <div class="toggle-switch ${s.animation?'on':''}" data-toggle="animation" title="Toggle animasi"></div>
        </div>
      </div>

      <!-- Reset -->
      <div class="settings-section">
        <div class="settings-section-title"><span>🔄</span> RESET</div>
        <div class="settings-reset-bar">
          <div class="settings-reset-info">
            Mengembalikan seluruh pengaturan tampilan ke kondisi awal (Zaiden Classic, font normal, animasi aktif).
          </div>
          <button class="btn-reset" id="settingsResetBtn">⟳ Reset ke Default</button>
        </div>
      </div>

    </div>`;

    bindSettingsEvents(container, s);
  }

  /* ── Bind events ─────────────────────────────────────────────────── */
  function bindSettingsEvents(container, s) {
    // Theme cards
    container.querySelectorAll('.theme-card').forEach(card => {
      // Preview on hover
      card.addEventListener('mouseenter', () => {
        applyTheme(card.dataset.themeId);
      });
      card.addEventListener('mouseleave', () => {
        applyTheme(s.theme);
      });
      // Select on click
      card.addEventListener('click', () => {
        s.theme = card.dataset.themeId;
        saveSettings(s);
        applyAll(s);
        renderSettings(container, s); // re-render to update active state + lock accent
      });
    });

    // Accent swatches
    container.querySelectorAll('.accent-swatch').forEach(sw => {
      sw.addEventListener('click', () => {
        if (sw.style.pointerEvents === 'none') return;
        s.accent = sw.dataset.accent;
        saveSettings(s);
        applyAccent(s.accent, s.theme);
        // Update active state
        container.querySelectorAll('.accent-swatch').forEach(a => a.classList.remove('active'));
        sw.classList.add('active');
        // Sync picker
        const picker = container.querySelector('#settingsAccentPicker');
        if (picker) picker.value = sw.dataset.accent;
      });
    });

    // Custom color picker
    const picker = container.querySelector('#settingsAccentPicker');
    if (picker) {
      picker.addEventListener('input', () => {
        s.accent = picker.value;
        applyAccent(s.accent, s.theme);
        container.querySelectorAll('.accent-swatch').forEach(a => a.classList.remove('active'));
      });
      picker.addEventListener('change', () => {
        s.accent = picker.value;
        saveSettings(s);
      });
    }

    // Font size
    container.querySelectorAll('[data-font]').forEach(btn => {
      btn.addEventListener('click', () => {
        s.fontSize = btn.dataset.font;
        saveSettings(s);
        applyFontSize(s.fontSize);
        container.querySelectorAll('[data-font]').forEach(b => b.classList.toggle('active', b.dataset.font === s.fontSize));
      });
    });

    // Density
    container.querySelectorAll('[data-density]').forEach(btn => {
      btn.addEventListener('click', () => {
        s.density = btn.dataset.density;
        saveSettings(s);
        applyDensity(s.density);
        container.querySelectorAll('[data-density]').forEach(b => b.classList.toggle('active', b.dataset.density === s.density));
      });
    });

    // Sidebar
    container.querySelectorAll('[data-sidebar]').forEach(btn => {
      btn.addEventListener('click', () => {
        s.sidebar = btn.dataset.sidebar;
        saveSettings(s);
        applySidebar(s.sidebar);
        container.querySelectorAll('[data-sidebar]').forEach(b => b.classList.toggle('active', b.dataset.sidebar === s.sidebar));
      });
    });

    // Toggle animation
    const animToggle = container.querySelector('[data-toggle="animation"]');
    if (animToggle) {
      animToggle.addEventListener('click', () => {
        s.animation = !s.animation;
        saveSettings(s);
        applyAnimation(s.animation);
        animToggle.classList.toggle('on', s.animation);
      });
    }

    // Reset
    const resetBtn = container.querySelector('#settingsResetBtn');
    if (resetBtn) {
      resetBtn.addEventListener('click', () => {
        const fresh = { ...DEFAULTS };
        Object.assign(s, fresh);
        saveSettings(s);
        applyAll(s);
        renderSettings(container, s);
        // Toast feedback
        const toast = document.getElementById('toastTitle');
        const toastMsg = document.getElementById('toastMessage');
        const toastEl = document.getElementById('toast');
        if (toast && toastEl) {
          toastEl.className = 'toast show';
          document.getElementById('toastIcon').textContent = '✓';
          toast.textContent = 'Berhasil';
          toastMsg.textContent = 'Tampilan dikembalikan ke pengaturan default.';
          setTimeout(() => { toastEl.classList.remove('show'); }, 3000);
        }
      });
    }
  }

  /* ════════════════════════════════════════════════════════════════════
     INIT — Apply saved settings immediately on page load
     ════════════════════════════════════════════════════════════════════ */
  let _state = loadSettings();
  let _activated = false;

  // Apply immediately on script load (before DOM ready — for FOUC prevention)
  applyAll(_state);

  function onActivate() {
    if (_activated) return;
    _activated = true;

    const container = document.getElementById('settingsContent');
    if (!container) return;

    renderSettings(container, _state);
  }

  // Listen for view change
  document.addEventListener('zaiden:viewchange', e => {
    if (e.detail?.view === 'settings') {
      // Re-render to reflect any state changes
      _activated = false;
      onActivate();
    }
  });

  // Expose apply function for early theme loading
  window._zaidenSettings = { applyAll, loadSettings };
})();
