(() => {
  'use strict';
  if (window.__dashboardNasBackupInstalled) return;
  window.__dashboardNasBackupInstalled = true;

  const state = {loading:true, data:null, error:null};
  const LABELS = {
    healthy:'Healthy', warning:'Needs attention', failed:'Failed', running:'Running', unknown:'Unknown',
  };
  const REASONS = {
    latest_backup_verified:'Latest backup verified', latest_backup_successful:'Latest backup completed',
    timer_disabled:'Scheduled backup is disabled', checksum_failed:'Checksum verification failed',
    running_status_stale:'Backup progress stopped updating', last_success_stale:'Latest backup is overdue',
    storage_low:'NAS storage is running low', no_success_record:'No successful backup recorded',
    status_missing:'Backup status is not connected yet', status_invalid:'Backup status data is invalid',
    readiness:'Checking NAS availability', snapshot:'Creating a consistent snapshot', checksum:'Calculating checksums',
    transfer:'Copying files to NAS', verify:'Verifying the NAS copy',
  };
  const PHASES = {
    idle:'Idle', readiness:'Readiness check', snapshot:'Snapshot', checksum:'Checksum',
    transfer:'Transfer', verify:'Verification', complete:'Complete', failed:'Failed',
  };

  const safe = value => window.safeText ? window.safeText(value) : String(value ?? '').replace(/[&<>"']/g, '');
  const dateTime = value => {
    if (!value) return 'Not available';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? 'Not available' : date.toLocaleString([], {dateStyle:'medium', timeStyle:'short'});
  };
  const relative = value => {
    if (!value) return 'No successful backup recorded';
    const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
    if (seconds < 3600) return `${Math.max(1, Math.floor(seconds / 60))} min ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} hr ago`;
    return `${Math.floor(seconds / 86400)} day${seconds < 172800 ? '' : 's'} ago`;
  };
  const bytes = value => {
    if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) return 'Not available';
    const units = ['B','KB','MB','GB','TB'];
    let amount = Number(value), unit = 0;
    while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1; }
    return `${amount >= 10 || unit === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`;
  };
  const duration = row => {
    if (!row?.started_at || !row?.finished_at) return 'Not available';
    const seconds = Math.max(0, Math.round((new Date(row.finished_at) - new Date(row.started_at)) / 1000));
    return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  };
  const tone = value => ['healthy','warning','failed','running'].includes(value) ? value : 'unknown';

  async function load() {
    state.loading = true;
    state.error = null;
    try {
      state.data = await window.get('/api/nas-backup/status');
    } catch (error) {
      if (window.NAS_BACKUP_PREVIEW_DATA) state.data = structuredClone(window.NAS_BACKUP_PREVIEW_DATA);
      else { state.data = null; state.error = 'Backup status could not be loaded.'; }
    } finally {
      state.loading = false;
    }
  }

  function statusSummary(data) {
    const overall = tone(data?.overall);
    const reason = REASONS[data?.reason] || 'Backup status needs review';
    return `<section class="nas-backup-hero ${overall}"><div><span class="nas-backup-eyebrow">BEER-NAS</span><h2>${safe(LABELS[overall])}</h2><p>${safe(reason)}</p></div><div class="nas-backup-hero-meta"><span>Last successful backup</span><strong>${safe(relative(data?.last_success_at))}</strong><small>${safe(dateTime(data?.last_success_at))}</small></div><span class="nas-backup-readonly">Read-only</span></section>`;
  }

  function metrics(data) {
    const latest = data?.latest_backup || {};
    const storage = data?.storage || {};
    const reachable = data?.nas_reachable;
    return `<section class="nas-backup-metrics" aria-label="Backup summary">
      <article><span>Next run</span><strong>${safe(dateTime(data?.next_run_at))}</strong><small>${data?.timer_enabled === false ? 'Timer disabled' : 'Scheduled automatically'}</small></article>
      <article><span>Latest backup</span><strong>${safe(latest.id || 'Not available')}</strong><small>${latest.file_count == null ? 'File count unavailable' : `${safe(latest.file_count)} files`}</small></article>
      <article><span>Verification</span><strong class="${latest.checksum_verified === true ? 'ok' : latest.checksum_verified === false ? 'bad' : 'muted'}">${latest.checksum_verified === true ? 'Passed' : latest.checksum_verified === false ? 'Failed' : 'Unknown'}</strong><small>SHA-256 and database checks</small></article>
      <article><span>NAS now</span><strong class="${reachable === true ? 'ok' : reachable === false ? 'muted' : 'muted'}">${reachable === true ? 'Reachable' : reachable === false ? 'Offline' : 'Unknown'}</strong><small>Offline outside the backup window is allowed</small></article>
      <article><span>Backup size</span><strong>${safe(bytes(latest.size_bytes))}</strong><small>Latest verified copy</small></article>
      <article><span>Free space</span><strong>${safe(bytes(storage.free_bytes))}</strong><small>${storage.total_bytes == null ? 'Capacity unavailable' : `${safe(bytes(storage.total_bytes))} total`}</small></article>
    </section>`;
  }

  function progress(data) {
    const progress = data?.progress || {};
    const percent = Number.isFinite(progress.percent) ? Math.max(0, Math.min(100, progress.percent)) : null;
    const active = data?.overall === 'running';
    return `<section class="nas-backup-card"><header><div><span class="nas-backup-eyebrow">Current state</span><h3>${safe(PHASES[data?.phase] || 'Unknown')}</h3></div><strong class="nas-backup-phase ${tone(data?.overall)}">${safe(LABELS[tone(data?.overall)])}</strong></header>${percent == null ? `<div class="nas-backup-phase-track ${active ? 'is-running' : ''}" aria-label="Exact progress is unavailable"><i></i></div><p>${active ? 'Phase is updating. A percentage appears only when reliable totals exist.' : 'No backup is currently running.'}</p>` : `<div class="nas-backup-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}"><i style="width:${percent}%"></i></div><p>${safe(percent.toFixed(1))}% complete</p>`}<dl><div><dt>Last attempt</dt><dd>${safe(dateTime(data?.last_attempt_at))}</dd></div><div><dt>Status updated</dt><dd>${safe(dateTime(data?.updated_at))}</dd></div></dl></section>`;
  }

  function history(data) {
    const rows = Array.isArray(data?.history) ? data.history.slice().reverse().slice(0, 7) : [];
    if (!rows.length) return `<section class="nas-backup-card"><header><div><span class="nas-backup-eyebrow">Recent runs</span><h3>No history available</h3></div></header><p>Run history will appear after the local status writer is connected.</p></section>`;
    return `<section class="nas-backup-card"><header><div><span class="nas-backup-eyebrow">Recent runs</span><h3>Last ${rows.length} backup${rows.length === 1 ? '' : 's'}</h3></div></header><div class="nas-backup-history">${rows.map(row => `<article><span class="nas-backup-result ${row.result === 'success' ? 'healthy' : 'failed'}">${row.result === 'success' ? 'Passed' : 'Failed'}</span><div><strong>${safe(dateTime(row.finished_at || row.started_at))}</strong><small>${safe(row.id || 'Backup run')} · ${safe(duration(row))}</small></div><div><strong>${safe(bytes(row.size_bytes))}</strong><small>${row.file_count == null ? 'Files unavailable' : `${safe(row.file_count)} files`}</small></div><div><strong>${row.checksum_verified === true ? 'Verified' : row.checksum_verified === false ? 'Check failed' : 'Not verified'}</strong><small>${safe(row.error_code || 'No error')}</small></div></article>`).join('')}</div></section>`;
  }

  function render() {
    const host = document.getElementById('nasBackupDashboard');
    if (!host) return;
    host.setAttribute('aria-busy', state.loading ? 'true' : 'false');
    if (state.loading && !state.data) { host.innerHTML = '<div class="nas-backup-loading">Loading backup status…</div>'; return; }
    if (!state.data) { host.innerHTML = `<section class="nas-backup-error"><h2>Status unavailable</h2><p>${safe(state.error)}</p></section>`; return; }
    const error = state.data.last_error;
    host.innerHTML = `${statusSummary(state.data)}${error ? `<section class="nas-backup-alert"><strong>${safe(error.message)}</strong><span>${safe(dateTime(error.at))}</span></section>` : ''}${metrics(state.data)}<div class="nas-backup-detail-grid">${progress(state.data)}${history(state.data)}</div><p class="nas-backup-footnote">Opening this page reads a sanitized local status record. It does not connect to, mount, wake, restore, or modify the NAS.</p>`;
  }

  function updateSecondarySurfaces() {
    const data = state.data;
    const route = document.querySelector('[data-nas-backup-route-status]');
    if (route && data) route.textContent = `${LABELS[tone(data.overall)]} · ${relative(data.last_success_at)}`;
    const backup = document.querySelector('[data-page="system"] .system-storage-summary .system-summary-grid > div:nth-child(2) strong');
    if (backup && data) {
      backup.textContent = LABELS[tone(data.overall)];
      backup.className = data.overall === 'healthy' ? 'ok' : data.overall === 'failed' ? 'bad' : 'warn';
    }
  }

  window.DashboardDataLifecycle?.register('nas-backup-status', ['backup','system','more'], async () => {
    await load();
    render();
    updateSecondarySurfaces();
  });
  const originalRenderPage = window.renderPage;
  window.renderPage = function renderPageWithNasBackup(page = window.currentPage()) {
    originalRenderPage(page);
    if (page === 'backup') render();
    if (page === 'system' || page === 'more') updateSecondarySurfaces();
  };
  window.DashboardNasBackup = Object.freeze({load, render, state});
})();
