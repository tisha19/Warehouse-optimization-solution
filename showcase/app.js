const days = ['Today · Fri 5', 'Sat 6', 'Sun 7', 'Mon 8', 'Tue 9', 'Wed 10', 'Thu 11'];
let state;

const $ = (id) => document.getElementById(id);
const icon = (name, size = 14) => `<i data-lucide="${name}" style="width:${size}px"></i>`;

async function request(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) throw new Error(`Request failed: ${response.status}`);
  return response.json();
}

function render(data) {
  state = data;
  $('modeLabel').textContent = data.mode;
  $('planVersion').textContent = data.version;
  $('headline').textContent = data.headline;
  $('travelMetric').textContent = `${data.metrics.travel_reduction}%`;
  $('replenishmentMetric').textContent = `-${data.metrics.replenishment_reduction}%`;
  $('laborMetric').textContent = `${data.metrics.labor_minutes} min`;
  $('violationMetric').textContent = data.metrics.violations;
  $('moveCount').textContent = data.metrics.move_count;
  $('tableLabor').textContent = data.metrics.labor_minutes;
  $('explanation').textContent = data.explanation;
  $('moveCap').value = data.constraints.max_moves;
  $('moveCapValue').textContent = data.constraints.max_moves;

  $('calendar').innerHTML = days.map((day, index) => {
    const moves = data.moves.filter((move) => move.day === index);
    return `<div class="day ${index === 0 ? 'today' : ''}"><div class="day-head"><strong>${day}</strong><span>${moves.length} moves</span></div><div class="day-body">${moves.length ? moves.map((move) => `<div class="move-chip ${move.type}" data-move="${move.id}"><time>${move.window}</time><strong>${move.code}</strong><small>${move.from} → ${move.to}</small></div>`).join('') : '<div class="empty-day">No moves planned</div>'}</div></div>`;
  }).join('');

  $('manifest').innerHTML = data.moves.map((move) => `<tr><td class="rank">${String(move.priority).padStart(2, '0')}</td><td><strong>Day ${move.day + 1}</strong><br><span class="muted">${move.window}</span></td><td class="sku"><strong>${move.sku}</strong><span>${move.code} · ${move.confidence}% confidence</span></td><td class="route"><strong>${move.from} → ${move.to}</strong><br><span>${move.type}</span></td><td>${move.reason}</td><td class="benefit">${move.benefit} hr/day</td><td>${move.labor} min</td><td><button class="status-btn ${move.status === 'Approved' ? 'approved' : ''}" data-approve="${move.id}">${move.status === 'Approved' ? `${icon('check', 11)} Approved` : 'Review'}</button></td></tr>`).join('');

  $('warehouseMap').innerHTML = data.zones.map((zone) => `<div class="zone ${zone.tone}"><strong>${zone.id}</strong><span>${zone.label}</span><small>${zone.utilization}% · ${zone.distance}m</small></div>`).join('');
  $('agentList').innerHTML = data.agents.map((agent, index) => `<div class="agent-row"><span class="agent-index">${index + 1}</span><div><strong>${agent.name}</strong><small>${agent.detail}</small></div>${icon('circle-check', 13)}</div>`).join('');
  $('serviceList').innerHTML = data.services.map((service) => `<div class="service-row"><strong>${service.name}</strong><span class="service-state ${service.status}">${service.status}</span></div>`).join('');
  document.querySelectorAll('[data-approve]').forEach((button) => button.addEventListener('click', () => approveMove(button.dataset.approve)));
  lucide.createIcons();
}

async function replan(extra = {}) {
  $('runPlan').classList.add('working');
  const data = await request('/api/replan', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({max_moves: Number($('moveCap').value), ...extra})});
  render(data);
  showToast(extra.lock_sku ? `${extra.lock_sku} locked · plan re-optimized` : 'Plan re-optimized');
  $('runPlan').classList.remove('working');
}

async function approveMove(moveId) {
  const data = await request('/api/approve', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({move_id: moveId})});
  render(data);
  showToast(`${moveId} approved for WMS handoff`);
}

function showToast(message) {
  $('toast').querySelector('span').textContent = message;
  $('toast').classList.add('show');
  setTimeout(() => $('toast').classList.remove('show'), 2600);
}

$('moveCap').addEventListener('input', (event) => $('moveCapValue').textContent = event.target.value);
$('moveCap').addEventListener('change', () => replan());
$('runPlan').addEventListener('click', () => {
  const prompt = $('plannerPrompt').value.toLowerCase();
  replan(prompt.includes('lock sparkling') || prompt.includes('do not move sparkling') ? {lock_sku: 'SKU-100'} : {});
});
$('plannerPrompt').addEventListener('keydown', (event) => { if (event.key === 'Enter') $('runPlan').click(); });
$('approvePlan').addEventListener('click', () => showToast('Plan opened for supervisor review'));
$('coldChain').addEventListener('click', (event) => event.currentTarget.classList.toggle('active'));

request('/api/state').then(render).catch(() => showToast('Unable to load scenario'));
lucide.createIcons();
