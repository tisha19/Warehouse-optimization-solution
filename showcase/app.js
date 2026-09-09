const ROUTES = ['/', '/orchestration', '/plan', '/openshell'];
const VIEWS = {'/': 'view-dashboard', '/orchestration': 'view-orchestration', '/plan': 'view-plan', '/openshell': 'view-openshell'};

const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const empty = (message) => `<p class="empty">${esc(message)}</p>`;

let runTimer = null;
let lastRun = null;

async function api(path, options) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({error: `${response.status} ${response.statusText}`}));
  if (!response.ok || body.error) throw new Error(body.error || `${response.status} ${response.statusText}`);
  return body;
}

function showBanner(message) {
  const banner = $('banner');
  banner.textContent = message;
  banner.classList.remove('hidden');
}

function clearBanner() {
  $('banner').classList.add('hidden');
}

function navigate(path, replace = false) {
  if (!ROUTES.includes(path)) path = '/';
  if (replace) history.replaceState({}, '', path);
  else history.pushState({}, '', path);
  render(path);
}

function render(path) {
  Object.values(VIEWS).forEach((id) => $(id).classList.remove('active'));
  $(VIEWS[path]).classList.add('active');
  document.querySelectorAll('.nav-item').forEach((item) => item.classList.toggle('current', item.dataset.route === path));
  clearBanner();
  if (path === '/') loadDashboard();
  if (path === '/orchestration') { loadRun(); startPolling(); }
  if (path === '/plan') loadRun();
  if (path === '/openshell') loadOpenShell();
  if (path !== '/orchestration') stopPolling();
}

/* ------------------------------------------------------------------ dashboard */

const KPI_CARDS = [
  ['daily_travel_km', 'Picker travel', 'km per day', false],
  ['avg_distance_per_pick_m', 'Distance per pick', 'metres round trip', false],
  ['forward_pick_coverage_pct', 'Class A in forward pick', 'share of A demand', true],
  ['daily_picks', 'Picks per day', 'forecast driven', true],
  ['slot_utilisation_pct', 'Slot utilisation', 'of active slots', true],
  ['lines_below_reorder', 'Below reorder point', 'SKUs', false],
];

function deltaMarkup(key, current, baseline, higherIsBetter) {
  if (baseline === undefined || baseline === null || baseline === current) return '';
  const change = current - baseline;
  const good = higherIsBetter ? change > 0 : change < 0;
  const sign = change > 0 ? '+' : '';
  return `<span class="delta ${good ? 'good' : 'bad'}">${sign}${Math.round(change * 10) / 10} vs before</span>`;
}

async function loadDashboard() {
  let data;
  try {
    data = await api('/api/dashboard');
  } catch (error) {
    showBanner(`Dashboard unavailable: ${error.message}`);
    return;
  }
  $('siteLabel').textContent = data.site;
  $('kpiGrid').innerHTML = KPI_CARDS.map(([key, label, unit, higher]) => `
    <div class="kpi">
      <span class="kpi-label">${esc(label)}</span>
      <strong>${esc(data.kpis[key])}</strong>
      <span class="kpi-unit">${esc(unit)}</span>
      ${deltaMarkup(key, data.kpis[key], data.baseline?.[key], higher)}
    </div>`).join('');

  const addressable = data.problems.filter((problem) => problem.addressable);
  const notes = data.problems.filter((problem) => !problem.addressable);
  const problemCard = (problem) => `
    <div class="problem ${esc(problem.severity)}">
      <div class="problem-metric">${esc(problem.metric)}</div>
      <div>
        <strong>${esc(problem.title)}</strong>
        <p>${esc(problem.detail)}</p>
      </div>
    </div>`;

  $('problemCount').textContent = addressable.length ? `${addressable.length} the planner can act on` : 'none outstanding';
  $('problemList').innerHTML = addressable.length
    ? addressable.map(problemCard).join('')
    : `<div class="resolved"><strong>No slotting problems outstanding.</strong>
       <p>Class A demand is served from the forward pick face and the average pick trip is within target.</p></div>`;
  $('noteList').innerHTML = notes.length ? notes.map(problemCard).join('') : empty('Nothing outstanding.');

  const fix = $('fixWithAi');
  fix.disabled = addressable.length === 0;
  fix.textContent = addressable.length ? 'Fix with AI' : 'Nothing to fix';

  $('zoneList').innerHTML = data.zones.map((zone) => `
    <div class="zone">
      <strong>${esc(zone.id)}</strong>
      <span>${esc(zone.label)}</span>
      <div class="bar"><i style="width:${Math.min(100, zone.utilisation)}%"></i></div>
      <span class="muted">${esc(zone.utilisation)}% · ${esc(zone.distance)}m · ${esc(zone.slots)} slots</span>
    </div>`).join('');

  $('countList').innerHTML = Object.entries(data.counts)
    .map(([key, value]) => `<div><strong>${esc(value)}</strong><span>${esc(key.replace(/_/g, ' '))}</span></div>`).join('');

  $('commitList').innerHTML = data.commits.length
    ? data.commits.slice().reverse().map((commit) => `
        <div class="row">
          <span>${esc(commit.approval_id)}</span>
          <span class="muted">${esc(commit.relocated)} relocated · ${esc(commit.rejected)} rejected</span>
          <span class="muted">${esc(new Date(commit.at).toLocaleString())}</span>
        </div>`).join('')
    : empty('Nothing has been written to the WMS yet.');

  $('serviceList').innerHTML = data.services.map((service) => `
    <div class="row">
      <span>${esc(service.name)}</span>
      <span class="muted">${esc(service.detail)}</span>
      <code>${esc(service.endpoint || 'not configured')}</code>
    </div>`).join('');

  updateRunBadge(data.run.status);
}

/* -------------------------------------------------------------- orchestration */

function updateRunBadge(status) {
  const badge = $('runBadge');
  badge.textContent = status && status !== 'IDLE' ? status : 'No run';
  badge.className = `runbadge ${String(status || '').toLowerCase()}`;
}

function startPolling() {
  stopPolling();
  runTimer = setInterval(loadRun, 1500);
}

function stopPolling() {
  if (runTimer) clearInterval(runTimer);
  runTimer = null;
}

async function loadRun() {
  let run;
  try {
    run = await api('/api/run');
  } catch (error) {
    showBanner(`Run state unavailable: ${error.message}`);
    return;
  }
  lastRun = run;
  updateRunBadge(run.status);
  renderRun(run);
  renderPlan(run);
  if (run.status === 'COMPLETE' || run.status === 'FAILED') stopPolling();
}

function renderRun(run) {
  $('startRun').classList.toggle('hidden', run.status === 'RUNNING' || run.status === 'HALTED');
  $('startRun').textContent = run.status === 'COMPLETE' ? 'Run again' : 'Start run';
  const solvedNothing = run.status === 'COMPLETE' && (run.moves || []).length === 0;
  $('toPlan').classList.toggle('hidden', run.status !== 'COMPLETE' || solvedNothing);

  const optimal = $('optimalNotice');
  optimal.classList.toggle('hidden', !solvedNothing);
  if (solvedNothing) {
    optimal.innerHTML = `<strong>No move would shorten travel.</strong>
      <p>cuOpt solved the model and every relocation it could make costs more than it saves,
      so the layout is already the best available for the current demand and move cap.</p>`;
  }

  const halt = $('haltNotice');
  if (run.status === 'HALTED' && run.halted_on) {
    halt.classList.remove('hidden');
    halt.innerHTML = `<strong>Process halted.</strong> OpenShell has not granted
      <code>${esc(run.halted_on.service)}</code>. ${esc(run.halted_on.reason)}
      <a href="/openshell" data-link>Open the governor</a> to approve; the run resumes on its own.`;
  } else {
    halt.classList.add('hidden');
  }

  const failure = $('runError');
  if (run.status === 'FAILED') {
    failure.classList.remove('hidden');
    failure.innerHTML = `<strong>Run failed.</strong> ${esc(run.error)}<p>No plan is shown because none was produced.</p>`;
  } else {
    failure.classList.add('hidden');
  }

  $('stageRail').innerHTML = run.stages.map((stage, index) => `
    <div class="stage ${esc(stage.status)}">
      <span class="stage-index">${index + 1}</span>
      <div>
        <strong>${esc(stage.label)}</strong>
        <span class="muted">${esc(stage.detail || stage.status)}</span>
      </div>
      <span class="muted">${stage.duration_ms ? `${stage.duration_ms} ms` : ''}</span>
    </div>`).join('');

  $('agentList').innerHTML = run.agents.length
    ? run.agents.map((agent) => `
        <article class="agent ${esc(agent.status)}">
          <header>
            <strong>${esc(agent.name)}</strong>
            <span class="tag">${esc(agent.role)}</span>
            <span class="muted">${agent.telemetry?.duration_ms ? `${agent.telemetry.duration_ms} ms · ${agent.telemetry.completion_tokens} out / ${agent.telemetry.prompt_tokens} in tokens` : agent.status}</span>
          </header>
          ${agent.reasoning?.length ? `<ol class="reasoning">${agent.reasoning.map((step) => `<li>${esc(step)}</li>`).join('')}</ol>` : ''}
          ${agent.findings?.length ? `<div class="chips">${agent.findings.map((item) => `<span>${esc(item)}</span>`).join('')}</div>` : ''}
          ${agent.risks?.length ? `<div class="chips risk">${agent.risks.map((item) => `<span>${esc(item)}</span>`).join('')}</div>` : ''}
          ${agent.telemetry?.model ? `<footer class="muted">${esc(agent.telemetry.model)} @ ${esc(agent.telemetry.endpoint)}</footer>` : ''}
        </article>`).join('')
    : empty('No agent has produced output yet.');

  const cuopt = run.cuopt;
  $('cuoptPanel').innerHTML = cuopt
    ? `<strong>${esc(cuopt.headline)}</strong>
       <p>${esc(cuopt.explanation)}</p>
       <div class="stat-row">
         ${Object.entries(cuopt.metrics || {}).map(([key, value]) => `<div><strong>${esc(value)}</strong><span>${esc(key.replace(/_/g, ' '))}</span></div>`).join('')}
       </div>
       <p class="muted">${esc(cuopt.telemetry?.duration_ms || 0)} ms · ${esc(cuopt.telemetry?.slots || 0)} slots · ${esc(cuopt.telemetry?.candidate_skus || 0)} SKUs · ${esc(cuopt.telemetry?.endpoint || '')}</p>`
    : empty('The solver has not run yet.');

  $('guardrailPanel').innerHTML = run.guardrails.length
    ? run.guardrails.map((event) => `
        <div class="row">
          <span class="pill ${event.allowed ? 'ok' : 'bad'}">${event.allowed ? 'allowed' : 'blocked'}</span>
          <span>${esc(event.stage)} rail</span>
          <span class="muted">${esc(event.policy_id)}</span>
        </div>`).join('')
    : empty('No guardrail check has run yet.');

  $('openshellPanel').innerHTML = run.openshell.length
    ? run.openshell.map((event) => `
        <div class="row">
          <span class="pill ${event.allowed ? 'ok' : 'bad'}">${event.allowed ? 'granted' : 'withheld'}</span>
          <span><code>${esc(event.service)}</code></span>
          <span class="muted">${esc(event.reason)}</span>
        </div>`).join('')
    : empty('No authorisation has been requested yet.');

  $('tokenSummary').textContent = run.tokens?.calls
    ? `${run.tokens.calls} model calls · ${run.tokens.prompt} prompt / ${run.tokens.completion} completion tokens`
    : '';

  $('eventLog').innerHTML = run.events.length
    ? run.events.slice().reverse().map((event) => `
        <div class="log-line ${esc(event.kind)}">
          <time>${esc(new Date(event.at).toLocaleTimeString())}</time>
          <span>${esc(event.message)}</span>
        </div>`).join('')
    : empty('Nothing has happened yet.');
}

/* ------------------------------------------------------------------- plan */

function renderPlan(run) {
  const moves = run.moves || [];
  const decisions = run.decisions || {};
  const metrics = run.cuopt?.metrics || {};

  $('compare').innerHTML = moves.length
    ? `<div class="compare-card">
         <span class="kpi-label">Current layout</span>
         <strong>${esc(run.stages.length ? 'as measured on the dashboard' : '')}</strong>
         <p class="muted">Every pick walks the distance its slot sits from the pick face today.</p>
       </div>
       <div class="compare-card highlight">
         <span class="kpi-label">After this plan</span>
         <strong>${esc(metrics.travel_reduction_pct ?? 0)}% less picker travel</strong>
         <p class="muted">${esc(moves.length)} relocations · ${esc(metrics.plan_value ?? 0)} metre-picks removed per day · ${esc(metrics.constraint_violations ?? 0)} constraint violations</p>
       </div>`
    : empty('No plan to compare. Run the orchestration first.');

  const counts = Object.values(decisions).reduce((acc, value) => ({...acc, [value]: (acc[value] || 0) + 1}), {});
  $('decisionSummary').textContent = moves.length
    ? `${counts.approved || 0} approved · ${counts.rejected || 0} rejected · ${counts.pending || 0} pending`
    : '';

  $('moveBody').innerHTML = moves.length
    ? moves.map((move) => {
        const decision = decisions[move.id] || 'pending';
        return `<tr class="${esc(decision)}">
          <td class="rank">${String(move.priority).padStart(2, '0')}</td>
          <td><strong>${esc(move.sku)}</strong><span class="muted">${esc(move.code)}${move.abc_class ? ` · class ${esc(move.abc_class)}` : ''}</span></td>
          <td><code>${esc(move.from_slot)}</code> → <code>${esc(move.to_slot)}</code><span class="muted">day ${esc(move.day + 1)} · ${esc(move.window)}</span></td>
          <td class="reason">${esc(move.reason)}</td>
          <td>${esc(move.benefit_hours_per_day)} hr/day</td>
          <td>${esc(move.labor_minutes)} min</td>
          <td class="decide">
            <button class="chip ${decision === 'approved' ? 'on' : ''}" data-decide="${esc(move.id)}" data-value="approved">Approve</button>
            <button class="chip ${decision === 'rejected' ? 'off' : ''}" data-decide="${esc(move.id)}" data-value="rejected">Reject</button>
          </td>
        </tr>`;
      }).join('')
    : `<tr><td colspan="7">${esc('cuOpt returned no move that shortens travel. Run the orchestration from the dashboard when the warehouse changes.')}</td></tr>`;
}

async function decide(moveId, value) {
  try {
    lastRun = await api('/api/plan/decide', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({move_id: moveId, decision: value})});
    renderPlan(lastRun);
  } catch (error) {
    showBanner(error.message);
  }
}

async function commit() {
  const notice = $('commitNotice');
  try {
    let result = await api('/api/plan/commit', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
    while (result.status === 'AWAITING_APPROVAL') {
      notice.classList.remove('hidden');
      notice.innerHTML = `<strong>Write held by OpenShell.</strong> <code>write_wms</code> needs approval for this specific call.
        <a href="/openshell" data-link>Approve it in the governor</a>; the write completes on its own.`;
      await new Promise((resolve) => setTimeout(resolve, 2000));
      result = await api('/api/plan/commit');
    }
    notice.classList.add('hidden');
    if (result.status === 'COMMITTED') {
      navigate('/');
      return;
    }
    showBanner(`Write not completed: ${result.error || result.status}`);
  } catch (error) {
    notice.classList.add('hidden');
    showBanner(error.message);
  }
}

/* --------------------------------------------------------------- openshell */

async function loadOpenShell() {
  let data;
  try {
    data = await api('/api/openshell');
  } catch (error) {
    showBanner(`OpenShell unavailable: ${error.message}`);
    ['governorTelemetry', 'pendingList', 'grantList', 'governedList', 'auditLog'].forEach((id) => { $(id).innerHTML = ''; });
    return;
  }
  $('governorEndpoint').textContent = data.endpoint;
  const totals = data.telemetry.totals;
  $('governorTelemetry').innerHTML = `
    <div class="kpi"><span class="kpi-label">Model calls</span><strong>${esc(totals.calls)}</strong><span class="kpi-unit">this process</span></div>
    <div class="kpi"><span class="kpi-label">Prompt tokens</span><strong>${esc(totals.prompt_tokens)}</strong><span class="kpi-unit">sent to NIM</span></div>
    <div class="kpi"><span class="kpi-label">Completion tokens</span><strong>${esc(totals.completion_tokens)}</strong><span class="kpi-unit">returned</span></div>
    <div class="kpi"><span class="kpi-label">WMS commits</span><strong>${esc(data.telemetry.commits)}</strong><span class="kpi-unit">approved writes</span></div>`
    + data.telemetry.models.map((model) => `
      <div class="kpi wide"><span class="kpi-label">${esc(model.role)}</span><strong>${esc(model.model)}</strong>
      <span class="kpi-unit">${esc(model.endpoint)} · ${esc(model.calls)} calls</span></div>`).join('');

  const pending = Array.isArray(data.pending) ? data.pending : [];
  $('pendingList').innerHTML = pending.length
    ? pending.map((request) => `
        <div class="row">
          <span><code>${esc(request.service)}</code></span>
          <span class="muted">${esc(request.user)} · ${esc(request.operation || 'call')}</span>
          <span class="actions">
            <button class="chip" data-resolve="${esc(request.id || request.request_id)}" data-approve="true">Approve</button>
            <button class="chip" data-resolve="${esc(request.id || request.request_id)}" data-approve="false">Deny</button>
          </span>
        </div>`).join('')
    : empty('No requests are waiting.');

  const grants = Array.isArray(data.grants) ? data.grants : [];
  $('grantList').innerHTML = grants.length
    ? grants.map((grant) => `
        <div class="row">
          <span><code>${esc(grant.service)}</code></span>
          <span class="muted">${esc(grant.user)} · ${esc(grant.status || 'approved')}${grant.calls ? ` · ${esc(grant.calls)} calls` : ''}</span>
          <span class="actions"><button class="chip off" data-revoke="${esc(grant.user)}|${esc(grant.service)}">Revoke</button></span>
        </div>`).join('')
    : empty('No grants have been issued.');

  const services = Array.isArray(data.services) ? data.services : [];
  $('governedList').innerHTML = services.length
    ? services.map((service) => `
        <div class="row">
          <span><code>${esc(service.name || service.service)}</code></span>
          <span class="pill ${String(service.approval || '').includes('call') ? 'bad' : 'ok'}">${esc(service.approval || 'auto')}</span>
          <span class="muted">${esc(service.description || '')}</span>
        </div>`).join('')
    : empty('The governor returned no policy.');

  const audit = Array.isArray(data.audit) ? data.audit : [];
  $('auditLog').innerHTML = audit.length
    ? audit.slice().reverse().map((entry) => `
        <div class="log-line ${entry.decision === 'allowed' ? 'ok' : 'openshell'}">
          <time>${esc((entry.at || entry.timestamp || '').slice(11, 19))}</time>
          <span>${esc(entry.user)} · <code>${esc(entry.service)}</code> · ${esc(entry.decision)}${entry.source ? ` (${esc(entry.source)})` : ''}</span>
        </div>`).join('')
    : empty('The audit log is empty.');
}

/* ------------------------------------------------------------------ wiring */

document.addEventListener('click', async (event) => {
  const link = event.target.closest('a[data-route], a[data-link]');
  if (link) {
    event.preventDefault();
    navigate(new URL(link.href).pathname);
    return;
  }
  const decideButton = event.target.closest('[data-decide]');
  if (decideButton) {
    await decide(decideButton.dataset.decide, decideButton.dataset.value);
    return;
  }
  const resolveButton = event.target.closest('[data-resolve]');
  if (resolveButton) {
    try {
      await api('/api/openshell/resolve', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({request_id: resolveButton.dataset.resolve, approve: resolveButton.dataset.approve === 'true'})});
      loadOpenShell();
    } catch (error) { showBanner(error.message); }
    return;
  }
  const revokeButton = event.target.closest('[data-revoke]');
  if (revokeButton) {
    const [user, service] = revokeButton.dataset.revoke.split('|');
    try {
      await api('/api/openshell/revoke', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({user, service})});
      loadOpenShell();
    } catch (error) { showBanner(error.message); }
  }
});

$('fixWithAi').addEventListener('click', async () => {
  navigate('/orchestration');
  try {
    await api('/api/run/start', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
    startPolling();
  } catch (error) { showBanner(error.message); }
});

$('resetDemo').addEventListener('click', async () => {
  try {
    await api('/api/reset', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
    navigate('/');
  } catch (error) { showBanner(error.message); }
});

$('startRun').addEventListener('click', async () => {
  try {
    await api('/api/run/start', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
    startPolling();
  } catch (error) { showBanner(error.message); }
});

$('toPlan').addEventListener('click', () => navigate('/plan'));
$('approveAll').addEventListener('click', () => decide('*', 'approved'));
$('rejectAll').addEventListener('click', () => decide('*', 'rejected'));
$('sendToWms').addEventListener('click', commit);
window.addEventListener('popstate', () => render(location.pathname));

navigate(location.pathname, true);
