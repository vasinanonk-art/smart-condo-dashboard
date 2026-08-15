(() => {
  'use strict';
  if (window.SmartCondoUI) return;

  const safe = value => String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');

  const ICON_PATHS = Object.freeze({
    'circle-check':'<circle cx="12" cy="12" r="9"/><path d="m8 12 2.5 2.5L16 9"/>',
    camera:'<path d="M4 7h3l1.5-2h7L17 7h3a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2z"/><circle cx="12" cy="13" r="4"/>',
    lightbulb:'<path d="M9 18h6M10 22h4M8.5 14.5A6 6 0 1 1 15.5 14.5C14.5 15.3 14 16 14 18h-4c0-2-.5-2.7-1.5-3.5z"/>',
    network:'<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    power:'<path d="M12 2v10M6.3 5.7a8 8 0 1 0 11.4 0"/>',
    'power-off':'<path d="M12 2v6M6.3 5.7a8 8 0 1 0 11.4 0"/><path d="m3 3 18 18"/>',
    'shield-check':'<path d="M12 3 4 6v5c0 5 3.4 8.4 8 10 4.6-1.6 8-5 8-10V6z"/><path d="m8.5 12 2.2 2.2 4.8-5"/>',
    thermometer:'<path d="M10 14.8V5a2 2 0 1 1 4 0v9.8a4 4 0 1 1-4 0z"/><path d="M12 9v7"/>',
    'thermometer-sun':'<path d="M8 14.8V6a2 2 0 1 1 4 0v8.8a4 4 0 1 1-4 0z"/><path d="M18 4v2M18 12v2M14 9h-2M22 9h-2M20.8 6.2l-1.4 1.4"/>',
    'toggle-right':'<rect x="3" y="7" width="18" height="10" rx="5"/><circle cx="16" cy="12" r="3"/>',
    'triangle-alert':'<path d="M10.3 3.7 2.6 17a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 3.7a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
    wifi:'<path d="M3 8.5a14 14 0 0 1 18 0M6.5 12a9 9 0 0 1 11 0M10 15.5a4 4 0 0 1 4 0"/><circle cx="12" cy="19" r="1"/>',
    wind:'<path d="M3 8h11a2 2 0 1 0-2-2M3 12h16a2 2 0 1 1-2 2M3 16h8"/>',
    zap:'<path d="m13 2-8 12h7l-1 8 8-12h-7z"/>',
  });

  function icon(name, label = '') {
    const accessibleLabel = label
      ? `<span class="sc-visually-hidden">${safe(label)}</span>`
      : '';
    const paths = ICON_PATHS[name];
    return `${paths
      ? `<svg class="sc-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${paths}</svg>`
      : `<i class="sc-icon" data-lucide="${safe(name)}" aria-hidden="true"></i>`}${accessibleLabel}`;
  }

  function statusChip({label, status = 'neutral', iconName = ''}) {
    return `<span class="sc-status-chip" data-status="${safe(status)}">${iconName ? icon(iconName) : ''}${safe(label)}</span>`;
  }

  function metricStatus(status) {
    const presentation = {
      success: {label:'Normal', status:'success'},
      warning: {label:'Attention', status:'warning'},
      critical: {label:'Offline', status:'critical'},
      info: {label:'Information', status:'info'},
    }[status];
    return presentation ? statusChip(presentation) : '';
  }

  function widgetHeader({title, subtitle = '', trailing = ''}) {
    return `<header class="sc-widget-header"><div><h2 class="sc-widget-title">${safe(title)}</h2>${subtitle ? `<p class="sc-widget-subtitle">${safe(subtitle)}</p>` : ''}</div>${trailing}</header>`;
  }

  function sectionTitle({title, subtitle = ''}) {
    return `<header><h1 class="sc-section-title">${safe(title)}</h1>${subtitle ? `<p class="sc-widget-subtitle">${safe(subtitle)}</p>` : ''}</header>`;
  }

  function glassCard({content = '', className = '', attributes = ''}) {
    return `<article class="sc-glass-card${className ? ` ${safe(className)}` : ''}"${attributes ? ` ${attributes}` : ''}>${content}</article>`;
  }

  function pageContainer({content = '', className = ''}) {
    return `<main class="sc-page-container${className ? ` ${safe(className)}` : ''}">${content}</main>`;
  }

  function responsiveGrid({content = '', className = ''}) {
    return `<div class="sc-responsive-grid${className ? ` ${safe(className)}` : ''}">${content}</div>`;
  }

  function dashboardShell({topBar = '', content = '', bottomNavigation = ''}) {
    return `<div class="sc-dashboard-shell">${topBar}<div class="sc-content-area">${content}</div>${bottomNavigation}</div>`;
  }

  function topBar({leading = '', title = '', trailing = ''}) {
    return `<header class="sc-top-bar">${leading}<div>${title ? `<span class="sc-widget-title">${safe(title)}</span>` : ''}</div>${trailing}</header>`;
  }

  function widgetGrid({content = '', className = ''}) {
    return `<div class="sc-widget-grid${className ? ` ${safe(className)}` : ''}">${content}</div>`;
  }

  function widgetColumn({content = '', className = ''}) {
    return `<div class="sc-widget-column${className ? ` ${safe(className)}` : ''}">${content}</div>`;
  }

  function metricCard({label, value, footnote = '', status = '', iconName = ''}) {
    return `<article class="sc-metric-card" data-status="${safe(status || 'neutral')}"><div class="sc-metric-card-header">${iconName ? `<span class="sc-metric-icon">${icon(iconName)}</span>` : ''}<span class="sc-metric-label">${safe(label)}</span>${metricStatus(status)}</div><strong class="sc-metric-value">${safe(value)}</strong>${footnote ? `<span class="sc-metric-footnote">${safe(footnote)}</span>` : ''}</article>`;
  }

  function button({
    label,
    variant = 'secondary',
    iconName = '',
    disabled = false,
    attributes = '',
  }) {
    return `<button type="button" class="sc-button sc-button-${safe(variant)}"${disabled ? ' disabled' : ''}${attributes ? ` ${attributes}` : ''}>${iconName ? icon(iconName) : ''}<span>${safe(label)}</span></button>`;
  }

  function deviceCard({
    title,
    subtitle = '',
    status = '',
    content = '',
    actions = '',
    attributes = '',
  }) {
    return `<article class="sc-device-card"${attributes ? ` ${attributes}` : ''}><header class="sc-device-header"><div><h3 class="sc-device-title">${safe(title)}</h3>${subtitle ? `<p class="sc-device-subtitle">${safe(subtitle)}</p>` : ''}</div>${status}</header>${content}${actions ? `<div class="sc-device-card-actions">${actions}</div>` : ''}</article>`;
  }

  function heroBanner({label = '', value = '', content = '', trailing = ''}) {
    return `<section class="sc-hero-banner"><div>${label ? `<span class="sc-metric-label">${safe(label)}</span>` : ''}${value ? `<div class="sc-hero-value">${safe(value)}</div>` : ''}${content}</div>${trailing}</section>`;
  }

  function widgetContainer({title, subtitle = '', content = '', trailing = ''}) {
    return `<section class="sc-widget-container">${widgetHeader({title, subtitle, trailing})}<div class="sc-widget-body">${content}</div></section>`;
  }

  function gaugeCard({title, value = '', content = ''}) {
    return `<section class="sc-gauge-card">${widgetHeader({title})}<div class="sc-gauge-slot">${content || `<span class="sc-metric-value">${safe(value)}</span>`}</div></section>`;
  }

  function lineChartCard({title, subtitle = '', content = ''}) {
    return `<section class="sc-line-chart-card">${widgetHeader({title, subtitle})}<div class="sc-chart-slot">${content}</div></section>`;
  }

  function quickAction({label, iconName = '', disabled = false, attributes = ''}) {
    return `<button type="button" class="sc-quick-action"${disabled ? ' disabled' : ''}${attributes ? ` ${attributes}` : ''}>${iconName ? icon(iconName) : ''}<span>${safe(label)}</span></button>`;
  }

  function infoTile({label, value, content = ''}) {
    return `<article class="sc-info-tile"><span class="sc-metric-label">${safe(label)}</span><strong class="sc-widget-title">${safe(value)}</strong>${content}</article>`;
  }

  function bottomNavigation({items = [], label = 'Dashboard navigation'}) {
    const buttons = items.map(item => (
      `<button type="button"${item.current ? ' aria-current="page"' : ''}${item.attributes ? ` ${item.attributes}` : ''}>${item.iconName ? icon(item.iconName) : ''}<span>${safe(item.label)}</span></button>`
    )).join('');
    return `<nav class="sc-bottom-navigation" aria-label="${safe(label)}">${buttons}</nav>`;
  }

  function bottomNavigationContainer({content = ''}) {
    return `<div class="sc-bottom-navigation-container">${content}</div>`;
  }

  function refreshIcons(root = document) {
    if (window.lucide?.createIcons) {
      window.lucide.createIcons({
        root,
        attrs: {
          'aria-hidden': 'true',
          class: 'sc-icon',
        },
      });
      return true;
    }
    return false;
  }

  window.SmartCondoUI = Object.freeze({
    safe,
    icon,
    statusChip,
    widgetHeader,
    sectionTitle,
    glassCard,
    pageContainer,
    responsiveGrid,
    dashboardShell,
    topBar,
    widgetGrid,
    widgetColumn,
    metricCard,
    primaryButton: options => button({...options, variant:'primary'}),
    secondaryButton: options => button({...options, variant:'secondary'}),
    button,
    deviceCard,
    heroBanner,
    widgetContainer,
    gaugeCard,
    lineChartCard,
    quickAction,
    infoTile,
    bottomNavigation,
    bottomNavigationContainer,
    refreshIcons,
  });
})();
