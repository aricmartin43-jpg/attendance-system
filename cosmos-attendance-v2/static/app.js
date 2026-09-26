'use strict';
const $ = id => document.getElementById(id);
let csrf = '', user = null, team = [], customers = [], machines = [], jobs = [], today = '', zone = 'Asia/Kolkata', currentView = '', openShift = null;
let stream = null, cameraMode = null, challenge = '', toastTimer, cameraRun = 0;
const escapeHTML = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function notice(message, error = false) {
  $('notice').textContent = message; $('notice').classList.toggle('error', error); $('notice').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $('notice').hidden = true, error ? 10000 : 5000);
}
async function api(path, method = 'GET', body) {
  const result = await fetch(path, {method, credentials:'same-origin', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}, body:body === undefined ? undefined : JSON.stringify(body)});
  const data = await result.json();
  if (!result.ok) {
    if (result.status === 401 && path !== '/api/login') await boot();
    throw new Error(data.error || 'Unable to complete the request.');
  }
  return data;
}
async function perform(fn) { try { await fn(); } catch (e) { notice(e.message, true); } }
const time = value => value ? new Intl.DateTimeFormat('en-IN', {hour:'2-digit',minute:'2-digit',timeZone:zone}).format(new Date(value)) : '—';
function personCell(name, code) {
  const initials = name.split(/\s+/).map(x => x[0]).slice(0,2).join('');
  return `<div class="person-cell"><span class="avatar">${escapeHTML(initials)}</span><div><strong>${escapeHTML(name)}</strong><small>${escapeHTML(code.toUpperCase())}</small></div></div>`;
}
function locationCell(loc) {
  if (!loc || !Number.isFinite(loc.lat) || !Number.isFinite(loc.lng)) return '—';
  const href = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(loc.lat + ',' + loc.lng)}`;
  return `<a class="location-link" href="${href}" target="_blank" rel="noopener noreferrer">View location ↗</a><small class="muted">Accuracy ±${Math.round(loc.accuracy)} m</small>`;
}
function attendanceTable(target, rows, month = false) {
  if (!rows.length) { $(target).innerHTML = '<div class="empty"><strong>No attendance recorded</strong>Records will appear here after a verified check-in.</div>'; return; }
  const adminActions=user?.admin?'<th>Admin</th>':'';
  $(target).innerHTML = `<div class="table-wrap"><table><thead><tr><th>Employee</th>${month ? '<th>Date</th>' : ''}<th>Check-in</th><th>Check-out</th><th>Hours</th><th>Status</th><th>Check-in location</th><th>Check-out location</th>${adminActions}</tr></thead><tbody>${rows.map(r => `<tr><td>${personCell(r.employee,r.code)}</td>${month ? `<td>${escapeHTML(r.date)}</td>` : ''}<td>${time(r.check_in)}</td><td>${time(r.check_out)}</td><td>${r.hours === null ? '—' : escapeHTML(r.hours.toFixed(2))}</td><td><span class="pill ${r.check_out ? 'neutral' : ''}">${r.check_out ? 'Completed' : 'At work'}</span></td><td>${locationCell(r.in_location)}</td><td>${locationCell(r.out_location)}</td>${user?.admin?`<td><div class="action-buttons"><button class="small-button" data-attedit="${r.id}">Edit</button><button class="small-button danger" data-attdelete="${r.id}">Delete</button></div></td>`:''}</tr>`).join('')}</tbody></table></div>`;
}

async function refreshOverview() {
  const [employees, dayRows, todayRows] = await Promise.all([api('/api/employees'), api('/api/attendance?date='+encodeURIComponent($('day-filter').value)), api('/api/attendance?date='+today)]);
  team = employees;
  const active = team.filter(e => e.active), codes = new Set(todayRows.map(r => r.code));
  $('stat-total').textContent = active.length; $('stat-in').textContent = todayRows.length;
  $('stat-working').textContent = todayRows.filter(r => !r.check_out).length;
  $('stat-away').textContent = active.filter(e => !codes.has(e.code)).length;
  attendanceTable('daily-table', dayRows);
}
async function refreshEmployees() {
  team = await api('/api/employees');
  if (!team.length) { $('employee-table').innerHTML = '<div class="empty"><strong>Build your Cosmos team</strong>Add your first employee and assign a secure 4-digit PIN.</div>'; return; }
  $('employee-table').innerHTML = `<div class="table-wrap"><table><thead><tr><th>Employee</th><th>Department</th><th>Access</th><th>Actions</th></tr></thead><tbody>${team.map(e => `<tr><td>${personCell(e.name,e.code)}</td><td>${escapeHTML(e.department)}</td><td>${e.active ? 'Active' : 'Inactive'}</td><td><div class="action-buttons"><button class="small-button" data-file="${e.id}">File</button><button class="small-button" data-edit="${e.id}">Edit</button><button class="small-button" data-pin="${e.id}">Reset PIN</button><button class="small-button" data-toggle="${e.id}">${e.active ? 'Deactivate' : 'Activate'}</button><button class="small-button danger" data-delete="${e.id}">Remove</button></div></td></tr>`).join('')}</tbody></table></div>`;
}
async function refreshMine() {
  const identity = await api('/api/session');
  if (!identity.user) throw new Error('Please sign in again.');
  user = identity.user; today = identity.today;
  const [rows, status] = await Promise.all([api('/api/attendance?date='+today),api('/api/my-status')]);
  openShift = status.open_shift;
  $('greeting').textContent = 'Hello, ' + user.name.split(' ')[0] + '.';
  const finished = rows.some(r => r.check_out);
  $('employee-status').textContent = openShift ? 'Checked in at ' + time(openShift.check_in) + ' · ' + openShift.date : finished ? 'Your attendance is complete for today.' : 'Ready for a new working day.';
  $('start-attendance').textContent = openShift ? 'Check out with GPS' : 'Check in with GPS';
  $('start-attendance').disabled = !openShift && finished;
  attendanceTable('my-table', rows);
}


async function ensureCustomers(){
  if(!customers.length) customers=await api('/api/customers');
  return customers;
}
function customerOptions(selected=''){
  return customers.map(c=>`<option value="${c.id}" ${String(c.id)===String(selected)?'selected':''}>${escapeHTML(c.name)} · ${escapeHTML(c.code||'')}</option>`).join('');
}
async function refreshCustomers(){
  customers=await api('/api/customers');
  if(!customers.length){$('customer-table').innerHTML='<div class="empty"><strong>No customers yet</strong>Add your first customer to begin the company network.</div>';$('customer-detail').innerHTML='';return;}
  $('customer-table').innerHTML=`<div class="table-wrap"><table><thead><tr><th>Customer</th><th>Industry</th><th>Contacts</th><th>Machines</th><th>Open jobs</th><th>Status</th><th></th></tr></thead><tbody>${customers.map(c=>`<tr><td><strong>${escapeHTML(c.name)}</strong><small class="muted">${escapeHTML(c.code||'')} · GST ${escapeHTML(c.gstin||'—')}</small></td><td>${escapeHTML(c.industry||'—')}</td><td>${c.contact_count}</td><td>${c.machine_count}</td><td>${c.open_jobs}</td><td>${statusPill(c.status)}</td><td><button class="small-button" data-customer-open="${c.id}">Open network</button></td></tr>`).join('')}</tbody></table></div>`;
}
async function showCustomerDetail(id){
  const c=await api('/api/customers/'+id);
  $('customer-detail').innerHTML=`<article class="section-card customer-network"><div class="section-heading"><div><span class="eyebrow accent">${escapeHTML(c.code||'CUSTOMER')}</span><h2>${escapeHTML(c.name)}</h2><p class="muted">${escapeHTML(c.address||'No address recorded')}</p></div><div class="action-buttons">${user.admin?`<button class="secondary" data-edit-customer="${c.id}">Edit customer</button><button class="secondary" data-archive-customer="${c.id}">${c.status==='Archived'?'Restore':'Archive'}</button>`:''}<button class="secondary" data-add-contact="${c.id}">+ Contact</button><button class="secondary" data-add-machine-customer="${c.id}">+ Machine</button><button class="primary" data-add-job-customer="${c.id}">+ Work order</button></div></div>
  <div class="network-columns">
    <section><h3>Important people</h3>${c.contacts.length?c.contacts.map(x=>`<div class="network-item"><strong>${escapeHTML(x.name)}</strong><span>${escapeHTML(x.designation||x.department||'Contact')}</span><small>${escapeHTML(x.phone||'')} ${escapeHTML(x.email||'')}</small>${user.admin?`<button class="small-button" data-edit-contact="${x.id}" data-customer-id="${c.id}">Edit</button>`:''}</div>`).join(''):'<p class="muted">No contacts yet.</p>'}</section>
    <section><h3>Machines</h3>${c.machines.length?c.machines.map(m=>`<button class="network-item network-button" data-machine-open="${m.id}"><strong>${escapeHTML(m.customer_machine_no||m.code)}</strong><span>${escapeHTML(m.machine_type||'Machine')} · ${escapeHTML(m.model||'')}</span><small>${escapeHTML(m.code||'')}</small></button>`).join(''):'<p class="muted">No machines yet.</p>'}</section>
    <section><h3>Recent work orders</h3>${c.jobs.length?c.jobs.slice(0,8).map(j=>`<div class="network-item"><strong>${escapeHTML(j.code)} · ${escapeHTML(j.title)}</strong><span>${escapeHTML(j.machine_no||'General')} · ${escapeHTML(j.status)}</span><small>${j.assignments.map(a=>escapeHTML(a.employee)).join(', ')||'Not assigned'}</small></div>`).join(''):'<p class="muted">No work orders yet.</p>'}</section>
  </div></article>`;
}
async function refreshMachines(){
  machines=await api('/api/machines');
  if(!machines.length){$('machine-table').innerHTML='<div class="empty"><strong>No machines yet</strong>Add customer machines to create lifetime service histories.</div>';$('machine-history').innerHTML='';return;}
  $('machine-table').innerHTML=`<div class="table-wrap"><table><thead><tr><th>Cosmos ID</th><th>Customer</th><th>Customer Machine No.</th><th>Type</th><th>Make / Model</th><th>Status</th><th></th></tr></thead><tbody>${machines.map(m=>`<tr><td><strong>${escapeHTML(m.code)}</strong></td><td>${escapeHTML(m.customer||'—')}</td><td>${escapeHTML(m.customer_machine_no||'—')}</td><td>${escapeHTML(m.machine_type||'—')}</td><td>${escapeHTML([m.manufacturer,m.model].filter(Boolean).join(' / ')||'—')}</td><td>${statusPill(m.status)}</td><td><button class="small-button" data-machine-open="${m.id}">History</button></td></tr>`).join('')}</tbody></table></div>`;
}
async function showMachineHistory(id){
  const h=await api('/api/machines/'+id+'/history'),m=h.machine;
  $('machine-history').innerHTML=`<article class="section-card machine-history-card"><div class="section-heading"><div><span class="eyebrow accent">${escapeHTML(m.code)}</span><h2>${escapeHTML(m.customer_machine_no||m.model||'Machine')}</h2><p class="muted">${escapeHTML(m.customer||'')} · ${escapeHTML(m.machine_type||'')} · ${escapeHTML(m.manufacturer||'')} ${escapeHTML(m.model||'')}</p></div></div>
  <div class="history-timeline">${[...h.work_reports.map(r=>({date:r.date,title:r.work_details,meta:r.employee+' · '+(r.job_no||'Work report'),status:r.status})),...h.jobs.map(j=>({date:j.start_date||'',title:j.title,meta:j.code+' · '+(j.assignments.map(a=>a.employee).join(', ')||'Not assigned'),status:j.status}))].sort((a,b)=>(b.date||'').localeCompare(a.date||'')).map(x=>`<div class="history-entry"><span>${escapeHTML(x.date||'—')}</span><div><strong>${escapeHTML(x.title)}</strong><small>${escapeHTML(x.meta)}</small></div>${statusPill(x.status)}</div>`).join('')||'<div class="empty">No history recorded for this machine yet.</div>'}</div></article>`;
}
async function refreshJobs(){
  jobs=await api('/api/work-orders');
  if(!jobs.length){$('job-table').innerHTML='<div class="empty"><strong>No work orders yet</strong>Create a work order and assign responsibility.</div>';return;}
  $('job-table').innerHTML=`<div class="table-wrap"><table><thead><tr><th>Job</th><th>Customer</th><th>Machine</th><th>Owner</th><th>Assigned</th><th>Target</th><th>Status</th>${user.admin?'<th>Action</th>':''}</tr></thead><tbody>${jobs.map(j=>`<tr><td><strong>${escapeHTML(j.code)}</strong><small class="muted">${escapeHTML(j.title)} · ${escapeHTML(j.work_type)}</small></td><td>${escapeHTML(j.customer||'—')}</td><td>${escapeHTML(j.machine_no||j.machine_code||'General')}</td><td>${escapeHTML(j.job_owner?.name||'—')}</td><td class="wrap-cell">${escapeHTML(j.assignments.map(a=>a.employee+' ('+a.role+')').join(', ')||'—')}</td><td>${escapeHTML(j.target_date||'—')}</td><td>${statusPill(j.status)}</td>${user.admin?`<td><button class="small-button" data-job-status="${j.id}" data-current="${escapeHTML(j.status)}">Update</button></td>`:''}</tr>`).join('')}</tbody></table></div>`;
}
async function openCustomerDialog(){$('customer-form').reset();$('customer-dialog-title').textContent='Add customer';$('customer-dialog').showModal();}
async function openMachineDialog(customerId=''){
  await ensureCustomers();$('machine-form').reset();$('machine-customer').innerHTML=customerOptions(customerId);$('machine-dialog').showModal();
}
async function loadJobMachines(customerId){
  const list=await api('/api/machines?customer_id='+encodeURIComponent(customerId));
  $('job-machine').innerHTML='<option value="">General / no specific machine</option>'+list.map(m=>`<option value="${m.id}">${escapeHTML(m.customer_machine_no||m.code)} · ${escapeHTML(m.machine_type||'Machine')}</option>`).join('');
}
async function openJobDialog(customerId=''){
  await Promise.all([ensureCustomers(),ensureTeam()]);
  $('job-form').reset();$('job-customer').innerHTML=customerOptions(customerId);$('job-owner').innerHTML='<option value="">Not set</option>'+employeeOptions();$('job-supervisor').innerHTML='<option value="">Not set</option>'+employeeOptions();$('job-assigned').innerHTML='<option value="">Not assigned</option>'+employeeOptions();
  if($('job-customer').value)await loadJobMachines($('job-customer').value);
  $('job-dialog').showModal();
}
async function prepareWorkLinks(){
  try{
    jobs=await api('/api/work-orders');machines=await api('/api/machines');
    $('work-order-link').innerHTML='<option value="">Not linked</option>'+jobs.map(j=>`<option value="${j.id}">${escapeHTML(j.code)} · ${escapeHTML(j.title)}</option>`).join('');
    $('work-machine-link').innerHTML='<option value="">Not linked</option>'+machines.map(m=>`<option value="${m.id}">${escapeHTML(m.code)} · ${escapeHTML(m.customer_machine_no||m.model||'Machine')}</option>`).join('');
  }catch(_){}
}
async function runNetworkSearch(){
  const q=$('network-search').value.trim();
  if(q.length<2){$('network-results').innerHTML='';return;}
  const rows=await api('/api/network/search?q='+encodeURIComponent(q));
  $('network-results').innerHTML=rows.length?rows.map(r=>`<button class="search-result" data-search-type="${escapeHTML(r.type)}" data-search-id="${r.id}"><strong>${escapeHTML(r.title)}</strong><span>${escapeHTML(r.type)} · ${escapeHTML(r.code||'')} · ${escapeHTML(r.subtitle||'')}</span></button>`).join(''):'<div class="empty compact-empty">No matches found.</div>';
}

async function ensureTeam(){
  if(user.admin && !team.length) team=await api('/api/employees');
  return team;
}
function employeeOptions(selected=''){
  const rows=user.admin?team:[{id:user.id,name:user.name,code:user.code}];
  return rows.map(e=>`<option value="${e.id}" ${String(e.id)===String(selected)?'selected':''}>${escapeHTML(e.name)} · ${escapeHTML(e.code.toUpperCase())}</option>`).join('');
}
async function refreshEcosystem(){
  const x=await api('/api/ecosystem/summary');
  $('eco-work').textContent=x.work_reports;
  $('eco-completed').textContent=x.completed_work;
  $('eco-issues').textContent=x.open_issues;
  $('eco-actions').textContent=x.open_meeting_actions;
}
function statusPill(status){
  const warning=['Blocked','Pending','Open','In Progress'].includes(status);
  return `<span class="pill ${warning?'warning':'neutral'}">${escapeHTML(status)}</span>`;
}
async function refreshWork(){
  const rows=await api('/api/work-reports');
  if(!rows.length){$('work-table').innerHTML='<div class="empty"><strong>No work reports yet</strong>Add the first daily work report to start building the company memory.</div>';return;}
  $('work-table').innerHTML=`<div class="table-wrap"><table><thead><tr><th>Employee</th><th>Date</th><th>Job / Customer</th><th>Work</th><th>Machine</th><th>Status</th><th>Problems</th>${user.admin?'<th>Review</th>':''}</tr></thead><tbody>${rows.map(r=>`<tr><td>${personCell(r.employee,r.code)}</td><td>${escapeHTML(r.date)}</td><td><strong>${escapeHTML(r.job_no||'—')}</strong><small class="muted">${escapeHTML(r.customer||'')}</small></td><td class="wrap-cell">${escapeHTML(r.work_details)}</td><td>${escapeHTML(r.machine||'—')}</td><td>${statusPill(r.status)}</td><td class="wrap-cell">${escapeHTML(r.problems||'—')}</td>${user.admin?`<td><button class="small-button" data-work-review="${r.id}">${r.verified_by?'Reviewed':'Review'}</button></td>`:''}</tr>`).join('')}</tbody></table></div>`;
}
async function refreshIssues(){
  const rows=await api('/api/issues');
  if(!rows.length){$('issues-table').innerHTML='<div class="empty"><strong>No problems recorded</strong>Reported difficulties and their resolutions will appear here.</div>';return;}
  $('issues-table').innerHTML=`<div class="table-wrap"><table><thead><tr><th>Employee</th><th>Date</th><th>Category</th><th>Problem</th><th>Status</th><th>Resolution</th>${user.admin?'<th>Action</th>':''}</tr></thead><tbody>${rows.map(r=>`<tr><td>${personCell(r.employee,r.code)}</td><td>${escapeHTML(r.date)}</td><td>${escapeHTML(r.category)}</td><td class="wrap-cell"><strong>${escapeHTML(r.title)}</strong><small class="muted">${escapeHTML(r.detail)}</small></td><td>${statusPill(r.status)}</td><td class="wrap-cell">${escapeHTML(r.resolution||'—')}</td>${user.admin?`<td><button class="small-button" data-issue-resolve="${r.id}">${r.status==='Resolved'?'Edit resolution':'Resolve'}</button></td>`:''}</tr>`).join('')}</tbody></table></div>`;
}
async function refreshMeetings(){
  const rows=await api('/api/meetings');
  if(!rows.length){$('meetings-list').innerHTML='<div class="empty"><strong>No meeting actions yet</strong>Meeting decisions and assigned actions will appear here.</div>';return;}
  $('meetings-list').innerHTML=rows.map(m=>`<article class="meeting-card"><div><span class="eyebrow accent">${escapeHTML(m.date)}</span><h3>${escapeHTML(m.title)}</h3><p class="muted">${escapeHTML(m.notes||'No notes recorded.')}</p></div><div class="meeting-actions">${m.actions.length?m.actions.map(a=>`<div class="meeting-action"><div><strong>${escapeHTML(a.action)}</strong><small>${escapeHTML(a.employee)} · Due ${escapeHTML(a.due_date||'not set')}</small></div><div>${statusPill(a.status)} ${(!user.admin&&a.employee_id===user.id)||user.admin?`<button class="small-button" data-action-update="${a.id}" data-current="${escapeHTML(a.status)}">Update</button>`:''}</div></div>`).join(''):'<p class="muted">No actions assigned.</p>'}</div></article>`).join('');
}
async function openWorkDialog(){
  if(user.admin){await ensureTeam();$('work-employee').innerHTML=employeeOptions();}
  $('work-form').reset();$('work-date').value=today;await prepareWorkLinks();$('work-dialog').showModal();
}
async function openIssueDialog(){
  if(user.admin){await ensureTeam();$('issue-employee').innerHTML=employeeOptions();}
  $('issue-form').reset();$('issue-date').value=today;$('issue-dialog').showModal();
}
async function openMeetingDialog(){
  await ensureTeam();$('meeting-form').reset();$('meeting-date').value=today;$('meeting-employee').innerHTML=employeeOptions();$('meeting-dialog').showModal();
}

let stockItems=[],suppliers=[];
async function refreshStock(){
  stockItems=await api('/api/stock-items');
  $('stock-table').innerHTML=stockItems.length?`<div class="table-wrap"><table><thead><tr><th>SKU / item</th><th>Category</th><th>Location</th><th>On hand</th><th>Reorder</th><th>Actions</th></tr></thead><tbody>${stockItems.map(x=>`<tr><td><strong>${escapeHTML(x.sku)}</strong><small>${escapeHTML(x.name)} · ${escapeHTML(x.specification||'')}</small></td><td>${escapeHTML(x.category)}</td><td>${escapeHTML(x.location||'—')}</td><td>${escapeHTML(x.quantity)} ${escapeHTML(x.unit)}</td><td>${Number(x.quantity)<=Number(x.reorder_level)?'<span class="pill">Low stock</span>':escapeHTML(x.reorder_level)}</td><td><button class="small-button" data-stock-history="${x.id}">History</button> <button class="small-button" data-stock-move="${x.id}" ${x.active?'':'disabled'}>Move</button> <button class="small-button" data-stock-edit="${x.id}">Edit</button></td></tr>`).join('')}</tbody></table></div>`:'<div class="empty"><strong>No items yet</strong>Add your first stock item.</div>';
}
async function showStockHistory(id){
  const rows=await api('/api/stock-items/'+id+'/movements');
  $('stock-history').innerHTML=`<div class="section-card"><h3>Movement history · ${escapeHTML(stockItems.find(x=>x.id===Number(id))?.sku||id)}</h3>${rows.length?`<div class="table-wrap"><table><thead><tr><th>When</th><th>Type</th><th>Change</th><th>Balance</th><th>Job</th><th>Reason / reference</th></tr></thead><tbody>${rows.map(m=>`<tr><td>${escapeHTML(new Date(m.at).toLocaleString('en-IN'))}</td><td>${escapeHTML(m.kind)}</td><td>${escapeHTML(m.change)}</td><td>${escapeHTML(m.balance)}</td><td>${escapeHTML(m.work_order_id||'—')}</td><td>${escapeHTML(m.reason)} · ${escapeHTML(m.reference||'')}</td></tr>`).join('')}</tbody></table></div>`:'<p>No movements yet.</p>'}</div>`;
}
async function refreshPurchasing(){
  const [s,orders]=await Promise.all([api('/api/suppliers'),api('/api/purchase-orders')]);suppliers=s;
  $('supplier-table').innerHTML=`<h3>Suppliers</h3>${s.length?`<div class="table-wrap"><table><thead><tr><th>Name</th><th>Contact</th><th>Phone</th><th>GSTIN</th></tr></thead><tbody>${s.map(x=>`<tr><td>${escapeHTML(x.name)}</td><td>${escapeHTML(x.contact||'—')}</td><td>${escapeHTML(x.phone||'—')}</td><td>${escapeHTML(x.gstin||'—')}</td></tr>`).join('')}</tbody></table></div>`:'<p class="muted">No suppliers yet.</p>'}`;
  $('purchase-table').innerHTML=`<h3>Purchase orders</h3>${orders.length?`<div class="table-wrap"><table><thead><tr><th>PO</th><th>Supplier</th><th>Item</th><th>Ordered</th><th>Received</th><th>Status</th><th>Action</th></tr></thead><tbody>${orders.map(x=>`<tr><td>PO-${x.id}</td><td>${escapeHTML(x.supplier)}</td><td>${escapeHTML(x.item)}</td><td>${escapeHTML(x.ordered_qty)}</td><td>${escapeHTML(x.received_qty)}</td><td>${escapeHTML(x.status)}</td><td>${x.status==='Received'? 'Complete':`<button class="small-button" data-po-receive="${x.id}" data-outstanding="${Number(x.ordered_qty)-Number(x.received_qty)}">Receive</button>`}</td></tr>`).join('')}</tbody></table></div>`:'<p class="muted">No purchase orders yet.</p>'}`;
}
async function refreshProduction(){
  const [steps,orders]=await Promise.all([api('/api/production-steps'),api('/api/work-orders')]);
  const byId=new Map(orders.map(j=>[j.id,j]));
  $('production-table').innerHTML=steps.length?`<div class="table-wrap"><table><thead><tr><th>Job</th><th>Sequence / operation</th><th>Plan</th><th>Hours plan / actual</th><th>Accepted / rejected</th><th>Status</th><th>Action</th></tr></thead><tbody>${steps.map(s=>`<tr><td>${escapeHTML(byId.get(s.work_order_id)?.code||s.work_order_id)}</td><td>${s.sequence} · ${escapeHTML(s.operation)}</td><td>${escapeHTML(s.planned_date||'—')}</td><td>${escapeHTML(s.planned_hours)} / ${escapeHTML(s.actual_hours)}</td><td>${escapeHTML(s.accepted_qty)} / ${escapeHTML(s.rejected_qty)}</td><td>${escapeHTML(s.status)}${s.delay_reason?`<small>${escapeHTML(s.delay_reason)}</small>`:''}</td><td><button class="small-button" data-step-update="${s.id}">Update</button></td></tr>`).join('')}</tbody></table></div>`:'<p class="muted">Add operations to a work order to plan production.</p>';
}
async function refreshMaintenance(){
  const [assets,tasks]=await Promise.all([api('/api/company-assets'),api('/api/maintenance-tasks')]);
  $('asset-table').innerHTML=`<h3>Company assets</h3>${assets.length?`<div class="table-wrap"><table><thead><tr><th>Code</th><th>Kind / name</th><th>Registration / serial</th><th>Next service</th></tr></thead><tbody>${assets.map(a=>`<tr><td>${escapeHTML(a.code)}</td><td>${escapeHTML(a.kind)} · ${escapeHTML(a.name)}</td><td>${escapeHTML(a.serial_or_registration||'—')}</td><td>${escapeHTML(a.next_service_date||'—')}</td></tr>`).join('')}</tbody></table></div>`:'<p class="muted">No company assets yet.</p>'}`;
  $('maintenance-table').innerHTML=`<h3>Maintenance tasks</h3>${tasks.length?`<div class="table-wrap"><table><thead><tr><th>Asset</th><th>Type / work</th><th>Due</th><th>Status</th><th>Action</th></tr></thead><tbody>${tasks.map(t=>`<tr><td>${escapeHTML(t.asset)}</td><td>${escapeHTML(t.task_type)} · ${escapeHTML(t.description)}</td><td>${escapeHTML(t.due_date||'—')}</td><td>${escapeHTML(t.status)}</td><td>${t.status==='Completed'?'Done':`<button class="small-button" data-task-complete="${t.id}">Complete</button>`}</td></tr>`).join('')}</tbody></table></div>`:'<p class="muted">No maintenance tasks yet.</p>'}`;
}
async function refreshAnalytics(){
  const data=await api('/api/management-summary');
  const labels={open_jobs:'Open jobs',overdue_jobs:'Overdue jobs',blocked_steps:'Blocked operations',accepted_quantity:'Accepted quantity',rejected_quantity:'Rejected quantity',low_stock:'Low stock items',open_maintenance:'Open maintenance'};
  $('analytics-content').innerHTML=Object.entries(labels).map(([k,label])=>`<article class="stat"><span>${label}</span><strong>${escapeHTML(data[k])}</strong></article>`).join('');
}

const views = {
  overview:['Attendance overview',"A clear view of your team's working day.",'▦'],
  ecosystem:['Company memory','Work, problems and decisions in one connected system.','◈'],
  customers:['Customers','Companies, contacts, machines and complete relationship history.','⌂'],
  machines:['Machines','Customer machines with permanent IDs and lifetime history.','⚙'],
  jobs:['Work orders','Responsibility, assignments and customer work in one place.','▰'],
  inventory:['Inventory','Items, available quantities and stock movements.','▥'],
  purchasing:['Purchasing','Suppliers, orders and goods received.','◫'],
  production:['Production planning','Operations from drawing to dispatch.','▧'],
  maintenance:['Maintenance','Company machines, bikes and service tasks.','⚒'],
  analytics:['Management summary','Live company operating measures.','◉'],
  employees:['Employees','The people behind every working day.','⊞'],
  work:['Daily work','Jobs completed, progress, machines and difficulties.','▣'],
  issues:['Problems & issues','Problems reported, ownership and resolutions.','△'],
  meetings:['Meetings & actions','Decisions, owners, deadlines and follow-up.','☷'],
  reports:['Monthly reports','Attendance records, ready for your monthly review.','▤'],
  checkin:['My attendance','Check in, get to work, and make today count.','◎']
};
async function navigate(view) {
  if (!views[view] || (user.admin ? view === 'checkin' : !['checkin','jobs','work','issues','meetings'].includes(view))) throw new Error('This page is not available.');
  currentView = view;
  document.querySelectorAll('.panel-view').forEach(el => el.hidden = el.id !== view+'-panel');
  document.querySelectorAll('.nav-button').forEach(el => el.classList.toggle('active',el.dataset.view===view));
  $('page-title').textContent=views[view][0]; $('page-subtitle').textContent=views[view][1]; $('breadcrumb').textContent=views[view][0];
  if(view === 'overview') await refreshOverview();
  if(view === 'ecosystem') await refreshEcosystem();
  if(view === 'employees') await refreshEmployees();
  if(view === 'customers') await refreshCustomers();
  if(view === 'machines') await refreshMachines();
  if(view === 'jobs') await refreshJobs();
  if(view === 'inventory') await refreshStock();
  if(view === 'purchasing') await refreshPurchasing();
  if(view === 'production') await refreshProduction();
  if(view === 'maintenance') await refreshMaintenance();
  if(view === 'analytics') await refreshAnalytics();
  if(view === 'work') await refreshWork();
  if(view === 'issues') await refreshIssues();
  if(view === 'meetings') await refreshMeetings();
  if(view === 'reports') attendanceTable('monthly-table',await api('/api/attendance?month='+$('month-filter').value),true);
  if(view === 'checkin') await refreshMine();
}
async function showApp() {
  $('login-view').hidden = true; $('app-view').hidden = false;
  $('account-name').textContent=user.name; $('timezone-label').textContent=zone;
  $('today-label').textContent=new Intl.DateTimeFormat('en-IN',{day:'numeric',month:'short',year:'numeric',timeZone:zone}).format(new Date());
  $('day-filter').value=today; $('month-filter').value=today.slice(0,7);
  const names = user.admin ? ['overview','ecosystem','customers','machines','jobs','production','inventory','purchasing','maintenance','analytics','employees','work','issues','meetings','reports'] : ['checkin','jobs','work','issues','meetings'];
  document.querySelectorAll('.admin-employee-field').forEach(el=>el.hidden=!user.admin);
  $('add-meeting').hidden=!user.admin;
  $('add-job').hidden=!user.admin;
  $('navigation').innerHTML = names.map(view=>`<button class="nav-button" data-view="${view}"><span class="nav-icon" aria-hidden="true">${views[view][2]}</span>${view==='overview'?'Overview':views[view][0]}</button>`).join('');
  await navigate(names[0]);
}
async function boot() {
  const info = await api('/api/session'); csrf=info.csrf; user=info.user; today=info.today; zone=info.timezone;
  if(user) await showApp(); else {$('app-view').hidden=true; $('login-view').hidden=false;}
}
$('login-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{
  const button=e.currentTarget.querySelector('button');button.disabled=true;
  try {const data=await api('/api/login','POST',Object.fromEntries(new FormData($('login-form'))));csrf=data.csrf;user=data.user;$('login-form').reset();await showApp();}finally{button.disabled=false;}
});});
$('logout').addEventListener('click',()=>perform(async()=>{await api('/api/logout','POST',{});stopCamera();await boot();}));
$('navigation').addEventListener('click',e=>{const button=e.target.closest('[data-view]');if(button)perform(()=>navigate(button.dataset.view));});
$('day-filter').addEventListener('change',()=>perform(refreshOverview));
$('month-filter').addEventListener('change',()=>perform(()=>navigate('reports')));
$('export').addEventListener('click',()=>perform(async()=>{
  const month=$('month-filter').value;if(!month)throw new Error('Choose a month first.');
  const response=await fetch('/api/export?month='+encodeURIComponent(month));
  if(!response.ok){const error=await response.json();throw new Error(error.error);}
  const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');link.href=url;link.download='cosmos-attendance-'+month+'.csv';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}));


$('add-customer').addEventListener('click',()=>perform(openCustomerDialog));
$('add-stock-item').addEventListener('click',()=>{$('stock-form').reset();$('stock-form').elements.sku.disabled=false;$('stock-dialog').showModal();});
$('stock-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{const d=Object.fromEntries(new FormData(e.currentTarget)),id=d.item_id;delete d.item_id;await api(id?'/api/stock-items/'+id:'/api/stock-items',id?'PATCH':'POST',d);$('stock-dialog').close();await refreshStock();notice('Stock item saved.');});});
$('stock-table').addEventListener('click',e=>perform(async()=>{const h=e.target.closest('[data-stock-history]'),m=e.target.closest('[data-stock-move]'),ed=e.target.closest('[data-stock-edit]');if(h)return showStockHistory(h.dataset.stockHistory);if(m){$('movement-form').reset();$('movement-form').elements.item_id.value=m.dataset.stockMove;$('movement-dialog').showModal();}if(ed){const x=stockItems.find(i=>i.id===Number(ed.dataset.stockEdit)),f=$('stock-form');f.reset();for(const k of ['sku','name','category','unit','specification','location','reorder_level'])f.elements[k].value=x[k]||'';f.elements.item_id.value=x.id;f.elements.sku.disabled=true;$('stock-dialog').showModal();}}));
$('movement-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{const d=Object.fromEntries(new FormData(e.currentTarget)),id=d.item_id;delete d.item_id;await api('/api/stock-items/'+id+'/movements','POST',d);$('movement-dialog').close();await refreshStock();await showStockHistory(id);notice('Stock movement recorded.');});});
$('add-supplier').addEventListener('click',()=>{$('supplier-form').reset();$('supplier-dialog').showModal();});
$('supplier-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{await api('/api/suppliers','POST',Object.fromEntries(new FormData(e.currentTarget)));$('supplier-dialog').close();await refreshPurchasing();notice('Supplier saved.');});});
$('add-purchase-order').addEventListener('click',()=>perform(async()=>{const [s,items]=await Promise.all([api('/api/suppliers'),api('/api/stock-items')]);if(!s.length||!items.length)throw new Error('Add a supplier and a stock item first.');const f=$('purchase-form');f.reset();f.elements.supplier_id.innerHTML=s.filter(x=>x.active).map(x=>`<option value="${x.id}">${escapeHTML(x.name)}</option>`).join('');f.elements.item_id.innerHTML=items.filter(x=>x.active).map(x=>`<option value="${x.id}">${escapeHTML(x.sku)} · ${escapeHTML(x.name)}</option>`).join('');$('purchase-dialog').showModal();}));
$('purchase-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{await api('/api/purchase-orders','POST',Object.fromEntries(new FormData(e.currentTarget)));$('purchase-dialog').close();await refreshPurchasing();notice('Purchase order created.');});});
$('purchase-table').addEventListener('click',e=>perform(async()=>{const b=e.target.closest('[data-po-receive]');if(!b)return;const value=prompt('Quantity received (maximum '+b.dataset.outstanding+')',b.dataset.outstanding);if(value===null)return;await api('/api/purchase-orders/'+b.dataset.poReceive+'/receive','POST',{quantity:value});await refreshPurchasing();notice('Receipt added to stock.');}));
$('employee-file-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{const d=Object.fromEntries(new FormData(e.currentTarget)),id=d.employee_id;delete d.employee_id;await api('/api/employee-files/'+id,'PATCH',d);$('employee-file-dialog').close();notice('Employee file saved.');});});
$('add-production-step').addEventListener('click',()=>perform(async()=>{const orders=await api('/api/work-orders');if(!orders.length)throw new Error('Create a work order first.');const f=$('production-form');f.reset();f.elements.work_order_id.innerHTML=orders.map(x=>`<option value="${x.id}">${escapeHTML(x.code)} · ${escapeHTML(x.title)}</option>`).join('');$('production-dialog').showModal();}));
$('production-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{await api('/api/production-steps','POST',Object.fromEntries(new FormData(e.currentTarget)));$('production-dialog').close();await refreshProduction();notice('Operation added.');});});
$('production-table').addEventListener('click',e=>perform(async()=>{const b=e.target.closest('[data-step-update]');if(!b)return;const status=prompt('Status: Planned, In Progress, Blocked, Completed');if(status===null)return;if(!['Planned','In Progress','Blocked','Completed'].includes(status))throw new Error('Invalid status.');const actual_hours=prompt('Actual hours (total)','0'),accepted_qty=prompt('Accepted quantity (total)','0'),rejected_qty=prompt('Rejected quantity (total)','0'),delay_reason=status==='Blocked'?prompt('Reason for delay',''):'';if([actual_hours,accepted_qty,rejected_qty,delay_reason].includes(null))return;await api('/api/production-steps/'+b.dataset.stepUpdate,'PATCH',{status,actual_hours,accepted_qty,rejected_qty,delay_reason});await refreshProduction();notice('Production updated.');}));
$('add-asset').addEventListener('click',()=>{$('asset-form').reset();$('asset-dialog').showModal();});
$('asset-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{await api('/api/company-assets','POST',Object.fromEntries(new FormData(e.currentTarget)));$('asset-dialog').close();await refreshMaintenance();notice('Asset saved.');});});
$('add-maintenance-task').addEventListener('click',()=>perform(async()=>{const assets=await api('/api/company-assets');if(!assets.length)throw new Error('Add a company asset first.');const f=$('maintenance-form');f.reset();f.elements.asset_id.innerHTML=assets.map(a=>`<option value="${a.id}">${escapeHTML(a.code)} · ${escapeHTML(a.name)}</option>`).join('');$('maintenance-dialog').showModal();}));
$('maintenance-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{await api('/api/maintenance-tasks','POST',Object.fromEntries(new FormData(e.currentTarget)));$('maintenance-dialog').close();await refreshMaintenance();notice('Maintenance task created.');});});
$('maintenance-table').addEventListener('click',e=>perform(async()=>{const b=e.target.closest('[data-task-complete]');if(!b)return;const cost=prompt('Repair/service cost ₹','0'),downtime_hours=prompt('Downtime hours','0'),notes=prompt('Completion notes','');if([cost,downtime_hours,notes].includes(null))return;await api('/api/maintenance-tasks/'+b.dataset.taskComplete,'PATCH',{status:'Completed',cost,downtime_hours,notes});await refreshMaintenance();notice('Task completed.');}));
$('refresh-analytics').addEventListener('click',()=>perform(refreshAnalytics));
$('add-machine').addEventListener('click',()=>perform(()=>openMachineDialog()));
$('add-job').addEventListener('click',()=>perform(()=>openJobDialog()));
$('customer-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{const d=Object.fromEntries(new FormData(e.currentTarget)),id=d.customer_id;delete d.customer_id;await api(id?'/api/customers/'+id:'/api/customers',id?'PATCH':'POST',d);$('customer-dialog').close();await refreshCustomers();if(id)await showCustomerDetail(id);notice('Customer saved.');});});
$('contact-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{const d=Object.fromEntries(new FormData(e.currentTarget)),id=d.customer_id,contactId=d.contact_id;delete d.customer_id;delete d.contact_id;d.primary_contact=e.currentTarget.elements.primary_contact.checked;await api('/api/customers/'+id+'/contacts'+(contactId?'/'+contactId:''),contactId?'PATCH':'POST',d);$('contact-dialog').close();await showCustomerDetail(id);notice('Customer contact saved.');});});
$('machine-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{await api('/api/machines','POST',Object.fromEntries(new FormData(e.currentTarget)));$('machine-dialog').close();machines=[];await refreshMachines();notice('Machine created.');});});
$('job-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{const d=Object.fromEntries(new FormData(e.currentTarget));d.assigned_employee_ids=d.assigned_employee_id?[Number(d.assigned_employee_id)]:[];delete d.assigned_employee_id;await api('/api/work-orders','POST',d);$('job-dialog').close();jobs=[];await refreshJobs();notice('Work order created.');});});
$('job-customer').addEventListener('change',e=>perform(()=>loadJobMachines(e.target.value)));
$('customer-table').addEventListener('click',e=>{const b=e.target.closest('[data-customer-open]');if(b)perform(()=>showCustomerDetail(b.dataset.customerOpen));});
$('customer-detail').addEventListener('click',e=>perform(async()=>{
  const c=e.target.closest('[data-add-contact]'),m=e.target.closest('[data-add-machine-customer]'),j=e.target.closest('[data-add-job-customer]'),mh=e.target.closest('[data-machine-open]'),ec=e.target.closest('[data-edit-customer]'),ac=e.target.closest('[data-archive-customer]'),ct=e.target.closest('[data-edit-contact]');
  if(ec){const row=await api('/api/customers/'+ec.dataset.editCustomer);const form=$('customer-form');form.reset();for(const key of ['name','gstin','industry','phone','email','address','notes'])form.elements[key].value=row[key]||'';form.elements.customer_id.value=row.id;$('customer-dialog-title').textContent='Edit customer';$('customer-dialog').showModal();return;}
  if(ac){const id=ac.dataset.archiveCustomer,row=await api('/api/customers/'+id),status=row.status==='Archived'?'Active':'Archived';if(status==='Archived'&&!confirm('Archive this customer? Their history will be kept.'))return;await api('/api/customers/'+id,'PATCH',{status});await refreshCustomers();await showCustomerDetail(id);notice('Customer '+status.toLowerCase()+'.');return;}
  if(ct){const id=ct.dataset.customerId,row=await api('/api/customers/'+id),contact=row.contacts.find(x=>x.id===Number(ct.dataset.editContact));if(!contact)throw new Error('Contact not found.');const form=$('contact-form');form.reset();for(const key of ['name','designation','department','phone','email','notes'])form.elements[key].value=contact[key]||'';form.elements.primary_contact.checked=contact.primary_contact;form.elements.customer_id.value=id;form.elements.contact_id.value=contact.id;$('contact-dialog-title').textContent='Edit contact';$('contact-dialog').showModal();return;}
  if(c){$('contact-form').reset();$('contact-customer-id').value=c.dataset.addContact;$('contact-dialog-title').textContent='Add customer contact';$('contact-dialog').showModal();return;}
  if(m){await openMachineDialog(m.dataset.addMachineCustomer);return;}
  if(j){await openJobDialog(j.dataset.addJobCustomer);return;}
  if(mh){await navigate('machines');await showMachineHistory(mh.dataset.machineOpen);}
}));
$('machine-table').addEventListener('click',e=>{const b=e.target.closest('[data-machine-open]');if(b)perform(()=>showMachineHistory(b.dataset.machineOpen));});
$('job-table').addEventListener('click',e=>perform(async()=>{const b=e.target.closest('[data-job-status]');if(!b)return;const next=prompt('Status: Open, In Progress, On Hold, Completed, Closed or Cancelled',b.dataset.current);if(next===null)return;const allowed=['Open','In Progress','On Hold','Completed','Closed','Cancelled'];if(!allowed.includes(next))throw new Error('Use one of: '+allowed.join(', '));await api('/api/work-orders/'+b.dataset.jobStatus,'PATCH',{status:next,completed_date:next==='Completed'?today:null});await refreshJobs();notice('Work order updated.');}));
let networkTimer;
$('network-search').addEventListener('input',()=>{clearTimeout(networkTimer);networkTimer=setTimeout(()=>perform(runNetworkSearch),250);});
$('network-results').addEventListener('click',e=>perform(async()=>{const b=e.target.closest('[data-search-type]');if(!b)return;const type=b.dataset.searchType,id=b.dataset.searchId;if(type==='Customer'){await showCustomerDetail(id);}else if(type==='Machine'){await navigate('machines');await showMachineHistory(id);}else if(type==='Work Order'){await navigate('jobs');}else if(type==='Employee'){await navigate('employees');}}));

$('add-work').addEventListener('click',()=>perform(openWorkDialog));
$('add-issue').addEventListener('click',()=>perform(openIssueDialog));
$('add-meeting').addEventListener('click',()=>perform(openMeetingDialog));
document.querySelectorAll('[data-jump]').forEach(b=>b.addEventListener('click',()=>perform(()=>navigate(b.dataset.jump))));
$('work-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{
  const data=Object.fromEntries(new FormData(e.currentTarget));if(!user.admin)delete data.employee_id;
  await api('/api/work-reports','POST',data);$('work-dialog').close();await refreshWork();notice('Work report saved.');
});});
$('issue-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{
  const data=Object.fromEntries(new FormData(e.currentTarget));if(!user.admin)delete data.employee_id;
  await api('/api/issues','POST',data);$('issue-dialog').close();await refreshIssues();notice('Problem recorded.');
});});
$('meeting-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{
  const fd=new FormData(e.currentTarget), action=$('meeting-action').value.trim();
  const actions=action?[{employee_id:Number($('meeting-employee').value),action,due_date:$('meeting-due').value||null}]:[];
  await api('/api/meetings','POST',{date:fd.get('date'),title:fd.get('title'),notes:fd.get('notes'),actions});
  $('meeting-dialog').close();await refreshMeetings();notice('Meeting saved.');
});});
$('work-table').addEventListener('click',e=>perform(async()=>{
  const b=e.target.closest('[data-work-review]');if(!b)return;
  const note=prompt('Supervisor note (optional):','');if(note===null)return;
  await api('/api/work-reports/'+b.dataset.workReview,'PATCH',{supervisor_note:note,verify:true});
  await refreshWork();notice('Work report reviewed.');
}));
$('issues-table').addEventListener('click',e=>perform(async()=>{
  const b=e.target.closest('[data-issue-resolve]');if(!b)return;
  const resolution=prompt('Resolution / corrective action:','');if(resolution===null||!resolution.trim())return;
  await api('/api/issues/'+b.dataset.issueResolve,'PATCH',{status:'Resolved',resolution});
  await refreshIssues();notice('Problem marked resolved.');
}));
$('meetings-list').addEventListener('click',e=>perform(async()=>{
  const b=e.target.closest('[data-action-update]');if(!b)return;
  const choices=['Open','In Progress','Completed','Closed'];const current=b.dataset.current;
  const next=prompt('Status: Open, In Progress, Completed or Closed',current);if(next===null)return;
  if(!choices.includes(next))throw new Error('Use one of: '+choices.join(', '));
  await api('/api/meeting-actions/'+b.dataset.actionUpdate,'PATCH',{status:next});await refreshMeetings();notice('Meeting action updated.');
}));

$('add-employee').addEventListener('click',()=>{const f=$('employee-form');f.reset();$('employee-id').value='';$('employee-dialog-title').textContent='Add employee';$('employee-dialog-note').textContent='Create an employee with a private 4-digit PIN.';$('pin-label').hidden=false;$('pin-help').hidden=false;$('employee-save').textContent='Create employee';f.elements.pin.required=true;$('employee-dialog').showModal();});
$('employee-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{
  const button=e.currentTarget.querySelector('button[type=submit]');button.disabled=true;
  try{const data=Object.fromEntries(new FormData($('employee-form'))),id=data.employee_id;delete data.employee_id;if(id){delete data.pin;await api('/api/employees/'+id,'PATCH',data);notice('Employee details updated.');}else{await api('/api/employees','POST',data);notice('Employee created.');}$('employee-dialog').close();await refreshEmployees();}finally{button.disabled=false;}
});});
$('employee-table').addEventListener('click',e=>perform(async()=>{
  const button=e.target.closest('button');if(!button)return;
  const id=Number(button.dataset.toggle||button.dataset.edit||button.dataset.pin||button.dataset.delete||button.dataset.file),employee=team.find(p=>p.id===id);if(!employee)return;
  if(button.dataset.file){const data=await api('/api/employee-files/'+id),f=$('employee-file-form');f.reset();f.elements.employee_id.value=id;for(const k of ['designation','joining_date','skills','notes'])f.elements[k].value=data.profile[k]||'';$('employee-file-title').textContent=employee.name+' · '+employee.code;$('employee-recent-work').innerHTML='<h3>Recent work</h3>'+(data.recent_work.map(x=>`<p>${escapeHTML(x.date)} · ${escapeHTML(x.job_no||'General')} · ${escapeHTML(x.status)}<br>${escapeHTML(x.details)}</p>`).join('')||'<p>No work reports yet.</p>');$('employee-file-dialog').showModal();return;}
  if(button.dataset.edit){const f=$('employee-form');f.reset();$('employee-id').value=employee.id;f.elements.name.value=employee.name;f.elements.code.value=employee.code.toUpperCase();f.elements.department.value=employee.department;$('employee-dialog-title').textContent='Edit employee';$('employee-dialog-note').textContent='Change employee identity or department.';$('pin-label').hidden=true;$('pin-help').hidden=true;f.elements.pin.required=false;$('employee-save').textContent='Save changes';$('employee-dialog').showModal();return;}
  if(button.dataset.pin){const pin=prompt(`Enter a new 4-digit PIN for ${employee.name}:`);if(pin===null)return;if(!/^\d{4}$/.test(pin))throw new Error('PIN must be exactly 4 digits.');await api('/api/employees/'+employee.id+'/reset-pin','POST',{pin});notice('Employee PIN reset.');return;}
  if(button.dataset.toggle){if(!confirm(`${employee.active?'Deactivate':'Activate'} ${employee.name}'s account?`))return;await api('/api/employees/'+employee.id+'/active','POST',{active:!employee.active});await refreshEmployees();notice('Employee access updated.');return;}
  if(button.dataset.delete){if(!confirm(`Permanently remove ${employee.name}, including their attendance history?`))return;await api('/api/employees/'+employee.id,'DELETE',{});await refreshEmployees();notice('Employee removed.');}
}));
document.querySelectorAll('.close-dialog').forEach(button=>button.addEventListener('click',()=>button.closest('dialog').close()));
function stopCamera(){cameraRun++;if(stream)stream.getTracks().forEach(track=>track.stop());stream=null;$('video').srcObject=null;}
$('camera-dialog').addEventListener('close',stopCamera);
window.addEventListener('pagehide',stopCamera);
async function startCamera(mode){
  stopCamera();const run=cameraRun;cameraMode=mode;challenge='';$('capture-button').disabled=true;$('consent').checked=false;
  $('consent-label').hidden=!mode.employee;$('camera-title').textContent=mode.employee?'Register '+mode.employee.name:(mode.action==='out'?'Verify check-out':'Verify check-in');
  $('camera-description').textContent=mode.employee?'Administrator: confirm the employee’s identity before enrolment.':'Face the camera. Your location will be captured when you submit.';
  $('camera-status').textContent='Starting camera…';$('camera-dialog').showModal();
  try{
    if(!navigator.mediaDevices?.getUserMedia)throw new Error('Camera access requires HTTPS and a supported browser.');
    const nextStream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'user',width:{ideal:640},height:{ideal:480}},audio:false});
    if(run!==cameraRun){nextStream.getTracks().forEach(t=>t.stop());return;}
    stream=nextStream;$('video').srcObject=stream;await $('video').play();
    if(!mode.employee)challenge=(await api('/api/capture','POST',{})).challenge;
    if(run!==cameraRun)return;
    $('camera-status').textContent='Camera ready. Keep your face inside the guide.';$('capture-button').textContent=mode.employee?'Capture and register':'Capture and verify';$('capture-button').disabled=false;
  }catch(e){$('camera-status').textContent=e.name==='NotAllowedError'?'Camera permission denied. Allow camera access in your browser settings.':e.message;stopCamera();}
}
$('start-attendance').addEventListener('click',()=>perform(async()=>{
  await refreshMine();
  if($('start-attendance').disabled)return;
  const action=openShift?'out':'in', button=$('start-attendance');
  button.disabled=true;
  try{
    const location=await getLocation();
    await api('/api/attendance','POST',{location,action});
    await refreshMine();
    notice(action==='in'?'Checked in successfully.':'Checked out successfully.');
  }finally{button.disabled=false;}
}));
function getLocation(){return new Promise((resolve,reject)=>{
  if(!navigator.geolocation)return reject(new Error('Location is not supported by this browser.'));
  navigator.geolocation.getCurrentPosition(p=>resolve({lat:p.coords.latitude,lng:p.coords.longitude,accuracy:p.coords.accuracy,timestamp:p.timestamp}),()=>reject(new Error('Unable to get location. Allow location access, enable GPS, and try again.')),{enableHighAccuracy:true,timeout:20000,maximumAge:0});
});}
$('capture-button').addEventListener('click',()=>perform(async()=>{
  const mode=cameraMode,run=cameraRun,button=$('capture-button');
  if(mode.employee&&!$('consent').checked)throw new Error('Confirm employee consent before registering their face.');
  if(!$('video').videoWidth)throw new Error('Camera is not ready. Try opening it again.');
  button.disabled=true;
  try{
    let location;
    if(!mode.employee){$('camera-status').textContent='Getting your current location…';location=await getLocation();if(run!==cameraRun)return;challenge=(await api('/api/capture','POST',{})).challenge;}
    if(run!==cameraRun)return;
    const canvas=document.createElement('canvas'),video=$('video'),scale=Math.min(1,800/video.videoWidth,800/video.videoHeight);
    canvas.width=Math.round(video.videoWidth*scale);canvas.height=Math.round(video.videoHeight*scale);canvas.getContext('2d').drawImage(video,0,0,canvas.width,canvas.height);
    const photo=canvas.toDataURL('image/jpeg',.85);$('camera-status').textContent='Verifying and saving…';
    if(mode.employee){await api('/api/employees/'+mode.employee.id+'/enrol','POST',{photo,consent:true});$('camera-dialog').close();await refreshEmployees();notice('Face registered successfully.');}
    else{await api('/api/attendance','POST',{photo,location,challenge,action:mode.action});$('camera-dialog').close();await refreshMine();notice(mode.action==='in'?'Checked in successfully. Have a good day.':'Checked out successfully.');}
  }catch(e){$('camera-status').textContent=e.message;throw e;}finally{button.disabled=false;}
}));
async function attendanceAdminAction(e){
  const edit=e.target.closest('[data-attedit]'),del=e.target.closest('[data-attdelete]');if(!edit&&!del)return;
  const id=Number((edit||del).dataset.attedit||(edit||del).dataset.attdelete);
  if(del){if(!confirm('Delete this attendance record permanently?'))return;await api('/api/attendance/'+id,'DELETE',{});currentView==='reports'?await navigate('reports'):await refreshOverview();notice('Attendance record deleted.');return;}
  const source=await api('/api/attendance?'+(currentView==='reports'?'month='+$('month-filter').value:'date='+$('day-filter').value));const r=source.find(x=>x.id===id);if(!r)throw new Error('Attendance record not found.');
  const cin=prompt('Check-in time (ISO/date-time). Example: 2026-09-25T09:00',r.check_in.slice(0,16));if(cin===null)return;const cout=prompt('Check-out time. Leave blank for open shift.',r.check_out?r.check_out.slice(0,16):'');if(cout===null)return;
  await api('/api/attendance/'+id,'PATCH',{check_in:cin,check_out:cout});currentView==='reports'?await navigate('reports'):await refreshOverview();notice('Attendance corrected.');
}
$('daily-table').addEventListener('click',e=>perform(()=>attendanceAdminAction(e)));
$('monthly-table').addEventListener('click',e=>perform(()=>attendanceAdminAction(e)));
setInterval(()=>{if(user){$('clock').textContent=new Intl.DateTimeFormat('en-IN',{hour:'2-digit',minute:'2-digit',hour12:false,timeZone:zone}).format(new Date());}},1000);
// Read-only tools expose only what the signed-in user can already access.
if(document.modelContext?.registerTool){
  const lifecycle=new AbortController();
  Promise.resolve(document.modelContext.registerTool({name:'read_visible_attendance',title:'Read attendance',description:'Read attendance records for the currently selected date or month. Requires sign-in and preserves the same employee/admin access rules.',inputSchema:{type:'object',properties:{},additionalProperties:false},annotations:{readOnlyHint:true,untrustedContentHint:true},execute:async input=>{
    if(!input||Object.keys(input).length)throw new Error('This tool takes no arguments.');
    if(!user)throw new Error('Sign in first.');
    if(currentView==='employees')throw new Error('Open Overview, My attendance or Monthly reports first.');
    const query=currentView==='reports'?'month='+$('month-filter').value:'date='+(currentView==='overview'?$('day-filter').value:today);
    return {records:await api('/api/attendance?'+query)};
  }},{signal:lifecycle.signal})).catch(()=>{});
  window.addEventListener('pagehide',()=>lifecycle.abort());
}
perform(boot);
