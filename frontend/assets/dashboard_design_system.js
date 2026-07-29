(() => {
  'use strict';
  if (window.SmartCondoUI) return;

  const safe = value => String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');

  function icon(name, label = '') {
    const accessibleLabel = label
      ? `<span class="sc-visually-hidden">${safe(label)}</span>`
      : '';
    return `<i class="sc-icon" data-lucide="${safe(name)}" aria-hidden="true"></i>${accessibleLabel}`;
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
    return `<article class="sc-metric-card"><div class="sc-metric-card-header">${iconName ? `<span class="sc-metric-icon">${icon(iconName)}</span>` : ''}<span class="sc-metric-label">${safe(label)}</span>${metricStatus(status)}</div><strong class="sc-metric-value">${safe(value)}</strong>${footnote ? `<span class="sc-metric-footnote">${safe(footnote)}</span>` : ''}</article>`;
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
