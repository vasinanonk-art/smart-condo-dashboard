(() => {
  'use strict';
  if (window.__dashboardSettingsInstalled) return;
  window.__dashboardSettingsInstalled = true;

  const state = {settings:null,maintenance:null,importStatus:null,notifications:[],activeSection:'electricity'};
  const safe = value => window.safeText ? window.safeText(value) : String(value ?? '');
  const number = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;

  function installUi() {
    document.querySelectorAll('.nav,.mobile-nav').forEach(host => {
      if (host.querySelector('[data-nav="settings"]')) return;
      const button = document.createElement('button');
      button.dataset.nav = 'settings';
      button.dataset.short = 'ST';
      button.textContent = 'Settings';
      host.appendChild(button);
    });
    if (!document.querySelector('[data-page="settings"]')) {
      const section = document.createElement('section');
      section.className = 'page';
      section.dataset.page = 'settings';
      section.innerHTML = '<div id="settingsPage" class="settings-page"><div class="card"><div class="empty">Settings are loading.</div></div></div>';
      document.querySelector('.main')?.appendChild(section);
    }
    const statusRow = document.querySelector('.topbar .status-row');
    if (statusRow && !document.getElementById('notificationButton')) {
      const button = document.createElement('button');
      button.id = 'notificationButton';
      button.className = 'btn ghost notification-button';
      button.type = 'button';
      button.innerHTML = 'Notifications <span id="notificationCount" class="notification-count" hidden>0</span>';
      statusRow.insertBefore(button, statusRow.firstChild);
      const panel = document.createElement('div');
      panel.id = 'notificationPanel';
      panel.className = 'notification-panel';
      panel.hidden = true;
      document.body.appendChild(panel);
      button.onclick = () => { panel.hidden = !panel.hidden; if (!panel.hidden) renderNotifications(); };
    }
  }

  async function jsonRequest(url, method, body) {
    const response = await window.fetch(url, {method, headers:{'Content-Type':'application/json'}, body:body === undefined ? undefined : JSON.stringify(body)});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || payload.error || `${method} failed`);
    return payload;
  }

  async function loadAll() {
    const results = await Promise.allSettled([
      window.get('/api/settings'),
      window.get('/api/maintenance/status'),
      window.get('/api/electricity/history/import/status'),
      window.get('/api/notifications')
    ]);
    if (results[0].status === 'fulfilled') state.settings = results[0].value;
    if (results[1].status === 'fulfilled') state.maintenance = results[1].value;
    if (results[2].status === 'fulfilled') state.importStatus = results[2].value;
    if (results[3].status === 'fulfilled') state.notifications = results[3].value.notifications || [];
    updateNotificationCount();
  }

  function updateNotificationCount() {
    const count = document.getElementById('notificationCount');
    if (!count) return;
    const value = state.notifications.length;
    count.textContent = String(value);
    count.hidden = value === 0;
  }

  function renderNotifications() {
    const panel = document.getElementById('notificationPanel');
    if (!panel) return;
    panel.innerHTML = `<div class="notification-head"><strong>Notifications</strong><button class="btn ghost" type="button" data-close-notifications>Close</button></div>${state.notifications.length ? state.notifications.map(item => `<article class="notification-item ${safe(item.severity || 'warning')}"><div><strong>${safe(item.title || 'Dashboard notification')}</strong><p>${safe(item.detail || '')}</p><time>${item.created_ts ? new Date(Number(item.created_ts) * 1000).toLocaleString() : ''}</time></div><button class="btn ghost" type="button" data-dismiss-notification="${safe(item.id)}">Dismiss</button></article>`).join('') : '<div class="notification-empty">No active notifications.</div>'}`;
    panel.querySelector('[data-close-notifications]')?.addEventListener('click', () => { panel.hidden = true; });
    panel.querySelectorAll('[data-dismiss-notification]').forEach(button => button.onclick = async () => {
      await jsonRequest(`/api/notifications/${encodeURIComponent(button.dataset.dismissNotification)}/dismiss`, 'POST', {});
      state.notifications = state.notifications.filter(item => item.id !== button.dataset.dismissNotification);
      updateNotificationCount();
      renderNotifications();
    });
  }

  function tierRow(item = {}, index = 0) {
    const unlimited = item.up_to_kwh === null || item.up_to_kwh === '' || item.up_to_kwh === undefined;
    return `<div class="settings-tier-row" data-tier-row><label>Up to kWh<input type="number" min="0" step="0.001" data-tier-limit value="${unlimited ? '' : safe(item.up_to_kwh)}" placeholder="Unlimited"></label><label>Rate<input type="number" min="0" step="0.0001" data-tier-rate value="${safe(item.rate ?? 0)}"></label><button class="btn ghost" type="button" data-remove-tier="${index}">Remove</button></div>`;
  }

  function renderElectricity(settings) {
    const tariff = settings.tariff || {};
    const tiers = Array.isArray(tariff.tiers) ? tariff.tiers : [];
    return `<form id="electricitySettingsForm" class="settings-form"><div class="settings-grid"><label>Billing cycle day<input name="billing_cycle_day" type="number" min="1" max="31" required value="${safe(settings.billing_cycle_day ?? 2)}"></label><label>Timezone<input name="timezone" type="text" required value="${safe(settings.timezone || 'Asia/Bangkok')}"></label><label>Tariff Name<input name="tariff_name" type="text" value="${safe(tariff.tariff_name || '')}"></label><label>Effective Date<input name="effective_date" type="date" value="${safe(tariff.effective_date || '')}"></label><label>Tariff Source<input name="source" type="text" value="${safe(tariff.source || 'manual')}"></label><label>Version<input name="version" type="text" value="${safe(tariff.version || '')}"></label><label>Ft per kWh<input name="ft_rate" type="number" min="0" step="0.0001" value="${safe(tariff.ft_rate ?? 0)}"></label><label>Service Charge<input name="service_charge" type="number" min="0" step="0.01" value="${safe(tariff.service_charge ?? 0)}"></label><label>VAT %<input name="vat_percent" type="number" min="0" max="100" step="0.01" value="${safe(tariff.vat_percent ?? 7)}"></label><label>Minimum Charge<input name="minimum_charge" type="number" min="0" step="0.01" value="${safe(tariff.minimum_charge ?? 0)}"></label></div><div class="settings-subhead"><div><h3>Progressive tiers</h3><p>Leave the final upper limit blank for the unlimited tier.</p></div><button class="btn ghost" id="addTariffTier" type="button">Add Tier</button></div><div id="tariffTierList" class="settings-tier-list">${tiers.length ? tiers.map(tierRow).join('') : tierRow({up_to_kwh:null,rate:0},0)}</div><div id="settingsMessage" class="settings-message" hidden></div><div class="settings-actions"><button class="btn primary" type="submit">Save Electricity Settings</button></div></form>`;
  }

  function renderDashboard(settings) {
    return `<form id="dashboardSettingsForm" class="settings-form"><div class="settings-grid"><label>Dashboard Timezone<input name="timezone" type="text" required value="${safe(settings.timezone || 'Asia/Bangkok')}"></label></div><div class="settings-actions"><button class="btn primary" type="submit">Save Dashboard Settings</button></div></form>`;
  }

  function formatImport(result) {
    if (!result || result.status === 'not_analyzed') return '<div class="settings-empty">History has not been analyzed yet.</div>';
    return `<div class="maintenance-result-grid"><div><span>Records scanned</span><strong>${safe(result.records_scanned ?? 0)}</strong></div><div><span>Candidate rows</span><strong>${safe(result.candidate_records ?? result.records_would_import ?? 0)}</strong></div><div><span>Duplicates</span><strong>${safe(result.duplicate_records ?? 0)}</strong></div><div><span>Estimated import</span><strong>${safe(result.records_would_import ?? 0)}</strong></div><div><span>Imported</span><strong>${safe(result.records_imported ?? 0)}</strong></div><div><span>Mode</span><strong>${safe(result.mode || (result.dry_run ? 'dry_run' : 'apply'))}</strong></div></div>`;
  }

  function renderMaintenance(settings) {
    const m = state.maintenance || {};
    return `<div class="settings-form"><div class="settings-grid"><label>Daily maintenance hour<input id="maintenanceHour" type="number" min="0" max="23" value="${safe(settings.daily_hour ?? 3)}"></label><label>History retention days<input id="retentionDays" type="number" min="1" max="3650" value="${safe(settings.history_retention_days ?? 400)}"></label><label class="settings-check"><input id="tariffSyncEnabled" type="checkbox" ${settings.tariff_sync_enabled ? 'checked' : ''}> Enable daily tariff check</label><label>Sync interval days<input id="tariffSyncInterval" type="number" min="1" max="365" value="${safe(settings.tariff_sync_interval_days ?? 1)}"></label></div><div class="settings-actions"><button id="saveMaintenance" class="btn primary" type="button">Save Maintenance Settings</button><button id="runMaintenance" class="btn ghost" type="button">Run Maintenance Now</button></div><div class="maintenance-status-grid"><div><span>Last run</span><strong>${safe(m.last_run_ts ? new Date(m.last_run_ts*1000).toLocaleString() : 'Not available')}</strong></div><div><span>Last tariff check</span><strong>${safe(m.last_tariff_check_ts ? new Date(m.last_tariff_check_ts*1000).toLocaleString() : 'Not available')}</strong></div><div><span>Last history prune</span><strong>${safe(m.last_history_prune_ts ? new Date(m.last_history_prune_ts*1000).toLocaleString() : 'Not available')}</strong></div><div><span>History size</span><strong>${safe(m.history_size_bytes ?? 0)} bytes</strong></div><div><span>Coverage</span><strong>${safe(m.billing_coverage_percent ?? 'Not available')}${m.billing_coverage_percent != null ? '%' : ''}</strong></div><div><span>Projection</span><strong>${safe(m.projection_status || 'Not available')}</strong></div></div><section class="maintenance-import"><div class="settings-subhead"><div><h3>Electricity History Import</h3><p>Analyze is dry-run only. Import always requires confirmation and creates a backup.</p></div><div><button id="analyzeHistory" class="btn ghost" type="button">Analyze History</button><button id="importHistory" class="btn primary" type="button">Import History</button></div></div><div id="historyImportResult">${formatImport(state.importStatus)}</div></section><section class="configuration-transfer"><div class="settings-subhead"><div><h3>Configuration Transfer</h3><p>Export or import validated non-secret settings.</p></div></div><div class="settings-actions"><button id="exportSettings" class="btn ghost" type="button">Export settings.json</button><label class="btn ghost settings-file-label">Import settings.json<input id="importSettingsFile" type="file" accept="application/json,.json" hidden></label></div></section></div>`;
  }

  function render() {
    const host = document.getElementById('settingsPage');
    if (!host) return;
    if (!state.settings) { host.innerHTML = '<div class="card"><div class="empty">Settings are not available.</div></div>'; return; }
    const tabs = [['electricity','Electricity'],['dashboard','Dashboard'],['maintenance','Maintenance']];
    const content = state.activeSection === 'electricity' ? renderElectricity(state.settings.electricity || {}) : state.activeSection === 'dashboard' ? renderDashboard(state.settings.dashboard || {}) : renderMaintenance(state.settings.maintenance || {});
    host.innerHTML = `<div class="settings-tabs">${tabs.map(([key,label]) => `<button class="btn ghost ${state.activeSection===key?'active':''}" type="button" data-settings-section="${key}">${label}</button>`).join('')}</div><section class="card settings-card"><div class="card-head"><div><h2>${safe(tabs.find(item=>item[0]===state.activeSection)?.[1] || 'Settings')}</h2><small>Saved to ~/.smart-condo-dashboard/settings.json</small></div></div>${content}</section>`;
    bind();
  }

  function collectTiers() {
    return [...document.querySelectorAll('[data-tier-row]')].map(row => ({
      up_to_kwh: row.querySelector('[data-tier-limit]').value === '' ? null : number(row.querySelector('[data-tier-limit]').value),
      rate: number(row.querySelector('[data-tier-rate]').value)
    }));
  }

  function showMessage(text, error = false) {
    const box = document.getElementById('settingsMessage');
    if (!box) return;
    box.hidden = false;
    box.className = `settings-message ${error ? 'error' : 'success'}`;
    box.textContent = text;
  }

  function bind() {
    document.querySelectorAll('[data-settings-section]').forEach(button => button.onclick = () => { state.activeSection = button.dataset.settingsSection; render(); });
    const addTier = document.getElementById('addTariffTier');
    if (addTier) addTier.onclick = () => { document.getElementById('tariffTierList').insertAdjacentHTML('beforeend', tierRow({up_to_kwh:null,rate:0}, document.querySelectorAll('[data-tier-row]').length)); bindTierRemovers(); };
    bindTierRemovers();

    const electricityForm = document.getElementById('electricitySettingsForm');
    if (electricityForm) electricityForm.onsubmit = async event => {
      event.preventDefault();
      const form = new FormData(electricityForm);
      const payload = {
        billing_cycle_day:number(form.get('billing_cycle_day'),2), timezone:String(form.get('timezone')||'Asia/Bangkok'),
        tariff:{tariff_name:String(form.get('tariff_name')||''),effective_date:String(form.get('effective_date')||''),source:String(form.get('source')||'manual'),version:String(form.get('version')||''),tiers:collectTiers(),ft_rate:number(form.get('ft_rate')),service_charge:number(form.get('service_charge')),vat_percent:number(form.get('vat_percent'),7),minimum_charge:number(form.get('minimum_charge'))}
      };
      try { const result = await jsonRequest('/api/settings/electricity','PUT',payload); state.settings.electricity = result.settings.electricity; showMessage('Electricity settings saved. No restart required.'); }
      catch (error) { showMessage(error.message || 'Save failed.', true); }
    };

    const dashboardForm = document.getElementById('dashboardSettingsForm');
    if (dashboardForm) dashboardForm.onsubmit = async event => {
      event.preventDefault();
      const timezone = new FormData(dashboardForm).get('timezone');
      const payload = {...state.settings, dashboard:{timezone:String(timezone||'Asia/Bangkok')}};
      try { const result = await jsonRequest('/api/settings','PUT',payload); state.settings = result.settings; render(); }
      catch (error) { alert(error.message || 'Save failed.'); }
    };

    document.getElementById('saveMaintenance')?.addEventListener('click', async () => {
      const payload = {...state.settings, maintenance:{daily_hour:number(document.getElementById('maintenanceHour').value,3),history_retention_days:number(document.getElementById('retentionDays').value,400),tariff_sync_enabled:document.getElementById('tariffSyncEnabled').checked,tariff_sync_interval_days:number(document.getElementById('tariffSyncInterval').value,1)}};
      const result = await jsonRequest('/api/settings','PUT',payload); state.settings = result.settings; render();
    });
    document.getElementById('runMaintenance')?.addEventListener('click', async () => { state.maintenance = await jsonRequest('/api/maintenance/run','POST',{}); render(); });
    document.getElementById('analyzeHistory')?.addEventListener('click', async () => { state.importStatus = await jsonRequest('/api/electricity/history/analyze','POST',{}); render(); });
    document.getElementById('importHistory')?.addEventListener('click', async () => { if (!confirm('Import legitimate electricity history now? A backup will be created before apply.')) return; state.importStatus = await jsonRequest('/api/electricity/history/import','POST',{confirm:true}); render(); });
    document.getElementById('exportSettings')?.addEventListener('click', async () => { const response = await window.fetch('/api/settings/export'); const blob = await response.blob(); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = 'settings.json'; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000); });
    document.getElementById('importSettingsFile')?.addEventListener('change', async event => { const file = event.target.files?.[0]; if (!file) return; let payload; try { payload = JSON.parse(await file.text()); } catch (_) { alert('Invalid JSON file.'); return; } if (!confirm('Validate and replace current settings? A backup will be created.')) return; const result = await jsonRequest('/api/settings/import?confirm=true','POST',payload); state.settings = result.settings; render(); });
  }

  function bindTierRemovers() {
    document.querySelectorAll('[data-remove-tier]').forEach(button => button.onclick = () => {
      const rows = document.querySelectorAll('[data-tier-row]');
      if (rows.length <= 1) return;
      button.closest('[data-tier-row]')?.remove();
    });
  }

  installUi();
  const originalRefresh = window.refresh;
  const originalRenderPage = window.renderPage;
  window.refresh = async function refreshWithSettings() { await Promise.allSettled([originalRefresh(), loadAll()]); window.renderPage(window.currentPage()); };
  window.renderPage = function renderPageWithSettings(page = window.currentPage()) { originalRenderPage(page); if (page === 'settings') render(); };
  document.querySelectorAll('[data-nav]').forEach(button => button.onclick = () => window.nav(button.dataset.nav));
  loadAll().then(() => { if (window.currentPage() === 'settings') render(); });
})();
