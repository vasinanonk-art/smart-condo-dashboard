(() => {
  'use strict';
  if (window.__dashboardAutomationCoreInstalled) return;
  window.__dashboardAutomationCoreInstalled = true;

  const FIELDS = {
    'electricity.power':'number','electricity.voltage':'number','electricity.current':'number','electricity.health':'string',
    'presence.beer':'string','presence.seem':'string','presence.any_home':'boolean','presence.all_away':'boolean',
    'pm25.living_room':'number','pm25.bedroom':'number','pm25.maximum':'number','temperature.cpu':'number',
    'system.mqtt_connected':'boolean','system.dashboard_health':'string','time.hour':'number','time.minute':'number','time.weekday':'number'
  };
  const OPERATORS = {
    number:[['gt','>'],['gte','≥'],['lt','<'],['lte','≤'],['eq','='],['ne','≠'],['exists','Exists'],['not_exists','Not exists']],
    boolean:[['eq','='],['ne','≠'],['exists','Exists'],['not_exists','Not exists']],
    string:[['eq','='],['ne','≠'],['in','In list'],['not_in','Not in list'],['exists','Exists'],['not_exists','Not exists']]
  };
  const state = {items:[],status:null,editing:null,result:null,csrf:null};
  const safe = value => window.safeText ? window.safeText(value) : String(value ?? '');
  const when = ts => ts ? new Date(Number(ts) * 1000).toLocaleString() : 'Never';

  function installUi() {
    document.querySelectorAll('.nav,.mobile-nav').forEach(host => {
      if (host.querySelector('[data-nav="automation"]')) return;
      const button = document.createElement('button');
      button.dataset.nav = 'automation';
      button.dataset.short = 'AU';
      button.textContent = 'Automation';
      const settings = host.querySelector('[data-nav="settings"]');
      settings ? host.insertBefore(button, settings) : host.appendChild(button);
    });
    if (!document.querySelector('[data-page="automation"]')) {
      const section = document.createElement('section');
      section.className = 'page';
      section.dataset.page = 'automation';
      section.innerHTML = '<div id="automationCorePage" class="automation-core-page"><div class="card"><div class="empty">Automation rules are loading.</div></div></div>';
      document.querySelector('.main')?.appendChild(section);
    }
    document.querySelectorAll('[data-nav="automation"]').forEach(button => button.onclick = () => window.nav('automation'));
  }

  async function csrfToken() {
    if (state.csrf) return state.csrf;
    const response = await fetch('/api/auth/status', {credentials:'same-origin'});
    const payload = await response.json();
    state.csrf = payload.csrf_token || null;
    return state.csrf;
  }

  async function request(url, method = 'GET', body) {
    const headers = {'Content-Type':'application/json'};
    if (!['GET','HEAD'].includes(method)) headers['X-CSRF-Token'] = await csrfToken();
    const response = await fetch(url, {method, credentials:'same-origin', headers, body:body === undefined ? undefined : JSON.stringify(body)});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error((payload.errors || [payload.detail || 'Request failed']).join('\n'));
    return payload;
  }

  async function load() {
    const [list,status] = await Promise.allSettled([request('/api/automations'),request('/api/automations/status')]);
    if (list.status === 'fulfilled') state.items = list.value.automations || [];
    if (status.status === 'fulfilled') state.status = status.value;
    render();
  }

  function triggerSummary(trigger) {
    if (!trigger || !Object.keys(trigger).length) return 'Not configured';
    return safe(trigger.type || 'Declarative trigger');
  }

  function conditionSummary(node) {
    if (!node || typeof node !== 'object') return 'Invalid';
    if (node.and) return `AND · ${node.and.length} conditions`;
    if (node.or) return `OR · ${node.or.length} conditions`;
    if (node.not) return `NOT · ${conditionSummary(node.not)}`;
    return `${node.field || 'field'} ${node.operator || 'operator'} ${node.value ?? ''}`.trim();
  }

  function listView() {
    const cards = state.items.map(item => `<article class="automation-rule-card">
      <div class="automation-rule-head"><div><h3>${safe(item.name)}</h3><p>${safe(item.description || 'No description')}</p></div><span class="automation-enabled ${item.enabled ? 'on':'off'}">${item.enabled ? 'Enabled':'Disabled'}</span></div>
      <div class="automation-rule-meta">
        <div><span>Mode</span><strong>${safe(item.mode)}</strong></div>
        <div><span>Trigger</span><strong>${triggerSummary(item.trigger)}</strong></div>
        <div><span>Condition</span><strong>${safe(conditionSummary(item.condition))}</strong></div>
        <div><span>Last triggered</span><strong>${safe(when(item.last_triggered_ts))}</strong></div>
        <div><span>Last result</span><strong>${safe(item.last_result || 'Not available')}</strong></div>
        <div><span>Cooldown</span><strong>${safe(item.cooldown_sec || 0)} sec</strong></div>
      </div>
      <div class="automation-rule-actions">
        <button class="btn ghost" data-auto-edit="${safe(item.id)}">Edit</button>
        <button class="btn ghost" data-auto-toggle="${safe(item.id)}" data-enabled="${item.enabled}">${item.enabled ? 'Disable':'Enable'}</button>
        <button class="btn ghost" data-auto-simulate="${safe(item.id)}">Simulate</button>
        <button class="btn danger" data-auto-delete="${safe(item.id)}">Delete</button>
      </div>
    </article>`).join('');
    return `<div class="automation-core-banner"><div><strong>Automation execution is not enabled</strong><p>STORY 6.1 validates and simulates rules only. No device command or MQTT publish can run.</p></div><button class="btn primary" id="createAutomation">Create Rule</button></div>
      <section class="card"><div class="card-head"><div><h2>Automation Rules</h2><small>${state.status?.enabled_count ?? 0} enabled · ${state.items.length} total · schema v${state.status?.schema_version ?? 1}</small></div></div>
      ${cards ? `<div class="automation-core-list">${cards}</div>` : '<div class="automation-empty">No automation rules yet.</div>'}</section>
      ${state.result ? `<div class="automation-result">${safe(state.result)}</div>` : ''}`;
  }

  function conditionRows(condition) {
    let group = 'and';
    let rows = [];
    if (condition?.and) { group = 'and'; rows = condition.and; }
    else if (condition?.or) { group = 'or'; rows = condition.or; }
    else if (condition?.not) { group = 'not'; rows = condition.not.and || [condition.not]; }
    else if (condition?.field) rows = [condition];
    if (!rows.length) rows = [{field:'electricity.power',operator:'gt',value:3500}];
    return {group,rows};
  }

  function operatorOptions(type, selected) {
    return OPERATORS[type].map(([value,label]) => `<option value="${value}" ${selected===value?'selected':''}>${label}</option>`).join('');
  }

  function conditionRow(row = {}) {
    const field = row.field && FIELDS[row.field] ? row.field : 'electricity.power';
    const type = FIELDS[field];
    const operator = OPERATORS[type].some(item => item[0] === row.operator) ? row.operator : OPERATORS[type][0][0];
    const value = Array.isArray(row.value) ? row.value.join(', ') : row.value ?? '';
    return `<div class="automation-condition-row" data-condition-row>
      <label>Field<select data-condition-field>${Object.keys(FIELDS).map(name => `<option value="${name}" ${field===name?'selected':''}>${name}</option>`).join('')}</select></label>
      <label>Operator<select data-condition-operator>${operatorOptions(type,operator)}</select></label>
      <label class="automation-condition-value">Value<input data-condition-value value="${safe(value)}" ${['exists','not_exists'].includes(operator)?'disabled':''}></label>
      <button class="btn ghost" type="button" data-remove-condition>Remove</button>
    </div>`;
  }

  function editorView(item) {
    const editing = item || {name:'',description:'',enabled:true,mode:'single',cooldown_sec:0,condition:{and:[{field:'electricity.power',operator:'gt',value:3500}]},trigger:{},actions:[]};
    const parsed = conditionRows(editing.condition);
    return `<section class="card automation-editor"><div class="card-head"><div><h2>${editing.id ? 'Edit Automation':'Create Automation'}</h2><small>Safe declarative conditions only</small></div></div>
      <form id="automationEditorForm">
        <div class="automation-form-grid">
          <label>Name<input name="name" required maxlength="120" value="${safe(editing.name || '')}"></label>
          <label>Mode<select name="mode"><option value="single" ${editing.mode==='single'?'selected':''}>Single</option><option value="restart" ${editing.mode==='restart'?'selected':''}>Restart</option><option value="queued" ${editing.mode==='queued'?'selected':''}>Queued</option></select></label>
          <label>Description<textarea name="description" maxlength="1000">${safe(editing.description || '')}</textarea></label>
          <label>Cooldown (seconds)<input name="cooldown_sec" type="number" min="0" step="1" value="${safe(editing.cooldown_sec || 0)}"></label>
          <label><span>Enabled</span><select name="enabled"><option value="true" ${editing.enabled?'selected':''}>Enabled</option><option value="false" ${!editing.enabled?'selected':''}>Disabled</option></select></label>
        </div>
        <div class="automation-condition-builder">
          <div class="automation-condition-head"><div><h3>Condition Builder</h3><small>Maximum 50 conditions and 8 nesting levels</small></div><div class="automation-condition-controls"><select id="conditionGroup"><option value="and" ${parsed.group==='and'?'selected':''}>AND group</option><option value="or" ${parsed.group==='or'?'selected':''}>OR group</option><option value="not" ${parsed.group==='not'?'selected':''}>NOT group</option></select><button class="btn ghost" id="addCondition" type="button">Add Condition</button></div></div>
          <div id="conditionRows">${parsed.rows.map(conditionRow).join('')}</div>
        </div>
        <div class="automation-actions-placeholder"><strong>Actions</strong><p>Action execution will be added in a later story.</p></div>
        <div class="automation-editor-actions"><button class="btn ghost" type="button" id="cancelAutomation">Cancel</button><button class="btn ghost" type="button" id="simulateDraft">Simulate</button><button class="btn primary" type="submit">Save Rule</button></div>
      </form>
      ${state.result ? `<div class="automation-result">${safe(state.result)}</div>` : ''}</section>`;
  }

  function render() {
    const host = document.getElementById('automationCorePage');
    if (!host) return;
    host.innerHTML = state.editing !== null ? editorView(state.editing) : listView();
    bind();
  }

  function parseValue(raw, type, operator) {
    if (['exists','not_exists'].includes(operator)) return undefined;
    if (operator === 'in' || operator === 'not_in') return raw.split(',').map(item => item.trim()).filter(Boolean);
    if (type === 'number') return Number(raw);
    if (type === 'boolean') return String(raw).toLowerCase() === 'true';
    return raw;
  }

  function collectCondition() {
    const rows = [...document.querySelectorAll('[data-condition-row]')].map(row => {
      const field = row.querySelector('[data-condition-field]').value;
      const operator = row.querySelector('[data-condition-operator]').value;
      const type = FIELDS[field];
      const value = parseValue(row.querySelector('[data-condition-value]').value, type, operator);
      return {field,operator,...(value === undefined ? {} : {value})};
    });
    const group = document.getElementById('conditionGroup').value;
    if (group === 'not') return {not:{and:rows}};
    return {[group]:rows};
  }

  function collectAutomation() {
    const form = new FormData(document.getElementById('automationEditorForm'));
    return {
      ...(state.editing?.id ? {id:state.editing.id,created_ts:state.editing.created_ts,last_triggered_ts:state.editing.last_triggered_ts,last_result:state.editing.last_result}:{}),
      name:String(form.get('name') || ''),description:String(form.get('description') || ''),enabled:form.get('enabled') === 'true',mode:String(form.get('mode') || 'single'),cooldown_sec:Number(form.get('cooldown_sec') || 0),trigger:state.editing?.trigger || {},condition:collectCondition(),actions:[]
    };
  }

  async function simulate(item) {
    const result = await request('/api/automations/simulate','POST',{automation:item,context_override:{}});
    state.result = `Simulation only\nMatched: ${result.matched}\nConditions passed: ${result.conditions_passed}\nActions executed: ${result.actions_executed}\nFields: ${(result.context_fields_used || []).join(', ') || 'None'}`;
    render();
  }

  function bindConditionRows() {
    document.querySelectorAll('[data-condition-field]').forEach(select => select.onchange = () => {
      const row = select.closest('[data-condition-row]');
      const operator = row.querySelector('[data-condition-operator]');
      operator.innerHTML = operatorOptions(FIELDS[select.value], OPERATORS[FIELDS[select.value]][0][0]);
      operator.dispatchEvent(new Event('change'));
    });
    document.querySelectorAll('[data-condition-operator]').forEach(select => select.onchange = () => {
      const input = select.closest('[data-condition-row]').querySelector('[data-condition-value]');
      input.disabled = ['exists','not_exists'].includes(select.value);
      if (input.disabled) input.value = '';
    });
    document.querySelectorAll('[data-remove-condition]').forEach(button => button.onclick = () => {
      const rows = document.querySelectorAll('[data-condition-row]');
      if (rows.length > 1) button.closest('[data-condition-row]').remove();
    });
  }

  function bind() {
    document.getElementById('createAutomation')?.addEventListener('click', () => { state.editing = {}; state.result = null; render(); });
    document.getElementById('cancelAutomation')?.addEventListener('click', () => { state.editing = null; state.result = null; render(); });
    document.getElementById('addCondition')?.addEventListener('click', () => { document.getElementById('conditionRows').insertAdjacentHTML('beforeend', conditionRow()); bindConditionRows(); });
    bindConditionRows();
    document.getElementById('automationEditorForm')?.addEventListener('submit', async event => {
      event.preventDefault();
      try {
        const payload = collectAutomation();
        if (state.editing?.id) await request(`/api/automations/${encodeURIComponent(state.editing.id)}`,'PUT',payload);
        else await request('/api/automations','POST',payload);
        state.editing = null; state.result = 'Automation saved. Execution remains disabled.'; await load();
      } catch (error) { state.result = error.message; render(); }
    });
    document.getElementById('simulateDraft')?.addEventListener('click', async () => { try { await simulate(collectAutomation()); } catch (error) { state.result = error.message; render(); } });
    document.querySelectorAll('[data-auto-edit]').forEach(button => button.onclick = () => { state.editing = state.items.find(item => item.id === button.dataset.autoEdit); state.result = null; render(); });
    document.querySelectorAll('[data-auto-toggle]').forEach(button => button.onclick = async () => { try { await request(`/api/automations/${encodeURIComponent(button.dataset.autoToggle)}/${button.dataset.enabled==='true'?'disable':'enable'}`,'POST',{}); await load(); } catch (error) { state.result=error.message; render(); } });
    document.querySelectorAll('[data-auto-simulate]').forEach(button => button.onclick = async () => { try { await simulate(state.items.find(item => item.id === button.dataset.autoSimulate)); } catch (error) { state.result=error.message; render(); } });
    document.querySelectorAll('[data-auto-delete]').forEach(button => button.onclick = async () => { if (!confirm('Delete this automation rule? A storage backup will be created.')) return; try { await request(`/api/automations/${encodeURIComponent(button.dataset.autoDelete)}`,'DELETE'); await load(); } catch (error) { state.result=error.message; render(); } });
  }

  installUi();
  const originalRender = window.renderPage;
  window.renderPage = function renderPageWithAutomation(page = window.currentPage()) {
    originalRender(page);
    if (page === 'automation') { render(); if (!state.status) load(); }
  };
  document.querySelectorAll('[data-nav]').forEach(button => button.onclick = () => window.nav(button.dataset.nav));
  load();
})();
