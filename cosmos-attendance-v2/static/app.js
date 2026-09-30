'use strict';
const $ = id => document.getElementById(id);
let csrf = '', user = null, team = [], customers = [], machines = [], jobs = [], today = '', zone = 'Asia/Kolkata', currentView = '', openShift = null;
let stream = null, cameraMode = null, challenge = '', toastTimer, cameraRun = 0;
let attendanceBusy = false, attendanceComplete = false;
const escapeHTML = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function donutChart(parts,centreLabel){
  const values=parts.map(p=>({...p,value:Math.max(0,Number(p.value)||0)}));
  const total=values.reduce((sum,p)=>sum+p.value,0);
  let offset=0;
  const segments=values.filter(p=>p.value>0).map(p=>{
    const percent=p.value/total*100,start=offset;offset+=percent;
    return `<circle cx="50" cy="50" r="42" pathLength="100" fill="none" stroke="${escapeHTML(p.color)}" stroke-width="16" stroke-dasharray="${percent} ${100-percent}" stroke-dashoffset="${-start}" transform="rotate(-90 50 50)"><title>${escapeHTML(p.label)}: ${p.value} (${Math.round(percent)}%)</title></circle>`;
  }).join('');
  const label=total?values.map(p=>`${p.label}: ${p.value}`).join(', '):'No records yet';
  return `<div class="donut-layout"><div class="donut-chart svg-donut" role="img" aria-label="${escapeHTML(centreLabel+': '+label)}"><svg viewBox="0 0 100 100" aria-hidden="true"><circle cx="50" cy="50" r="42" fill="none" stroke="#e2eaf3" stroke-width="16"/>${segments}</svg><div class="donut-hole"><strong>${total}</strong><span>${escapeHTML(centreLabel)}</span></div></div><ul class="donut-legend">${values.map(p=>`<li><svg class="donut-swatch" viewBox="0 0 10 10" aria-hidden="true"><circle cx="5" cy="5" r="5" fill="${escapeHTML(p.color)}"/></svg><span>${escapeHTML(p.label)}</span><strong>${p.value}</strong></li>`).join('')}${total?'':'<li class="donut-no-data">No records yet</li>'}</ul></div>`;
}
function progressRing(percent,total,color,label){
  const value=Math.max(0,Math.min(100,percent));
  return `<div class="progress-ring svg-donut" role="img" aria-label="${escapeHTML(label)}"><svg viewBox="0 0 100 100" aria-hidden="true"><circle cx="50" cy="50" r="43" fill="none" stroke="#e2eaf3" stroke-width="13"/>${total&&value>0?`<circle cx="50" cy="50" r="43" pathLength="100" fill="none" stroke="${color}" stroke-width="13" stroke-linecap="${value===100?'butt':'round'}" stroke-dasharray="${value} ${100-value}" transform="rotate(-90 50 50)"/>`:''}</svg><span>${total?value+'%':'—'}</span></div>`;
}
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
  if (!team.length) { $('employee-table').innerHTML = '<div class="empty"><strong>Your team starts here</strong>Add your first employee and assign a secure 4-digit PIN.</div>'; return; }
  $('employee-table').innerHTML = `<div class="table-wrap"><table><thead><tr><th>Employee</th><th>Department</th><th>Access</th><th>Actions</th></tr></thead><tbody>${team.map(e => `<tr><td>${personCell(e.name,e.code)}</td><td>${escapeHTML(e.department)}</td><td>${e.active ? 'Active' : 'Inactive'}</td><td><div class="action-buttons"><button class="small-button" data-profile="${e.id}">Profile & points</button><button class="small-button" data-file="${e.id}">File</button><button class="small-button" data-edit="${e.id}">Edit</button><button class="small-button" data-pin="${e.id}">Reset PIN</button><button class="small-button" data-toggle="${e.id}">${e.active ? 'Deactivate' : 'Activate'}</button><button class="small-button danger" data-delete="${e.id}">Delete</button></div></td></tr>`).join('')}</tbody></table></div>`;
}
let selectedEmployeeProfileId=null;
async function showEmployeeProfile(id,month=today.slice(0,7)){
  selectedEmployeeProfileId=Number(id);
  const [p,points,memos]=await Promise.all([api('/api/employees/'+id+'/profile?month='+encodeURIComponent(month)),api('/api/employees/'+id+'/points?month='+encodeURIComponent(month)),api('/api/employees/'+id+'/memos?month='+encodeURIComponent(month))]);
  $('employee-profile').innerHTML=`<article class="section-card employee-profile-card"><div class="section-heading"><div><span class="eyebrow accent">EMPLOYEE PROFILE · ${escapeHTML(p.employee.code.toUpperCase())}</span><h2>${escapeHTML(p.employee.name)}</h2><p class="muted">${escapeHTML(p.designation||p.employee.department)} · ${escapeHTML(p.employee.department)}${p.joining_date?' · Joined '+escapeHTML(p.joining_date):''}</p></div><label class="compact-label">Month<input id="employee-profile-month" type="month" value="${escapeHTML(p.month)}"></label></div>
    <div class="stats"><article class="stat"><span>Attendance</span><strong>${p.attendance_percent===null?'—':p.attendance_percent+'%'}</strong><small>${p.attendance_days} of ${p.company_recorded_days} recorded company days</small></article><article class="stat"><span>Planned work completed</span><strong>${p.completed_tasks}</strong><small>${p.due_tasks} due tasks · ${p.completion_percent===null?'No rate yet':p.completion_percent+'% completion'}</small></article><article class="stat"><span>Completed work orders</span><strong>${p.completed_work_orders}</strong><small>Assigned jobs completed this month</small></article><article class="stat"><span>Work reports</span><strong>${p.work_reports}</strong><small>${p.completed_work_reports} marked completed</small></article><article class="stat"><span>Activity points</span><strong>${p.activity_points}</strong><small>Recorded activity, not a rating</small></article></div>
    <div class="employee-donut-grid"><section><h3>Monthly attendance</h3>${donutChart([{label:'Present',value:p.attendance_days,color:'#4aa889'},{label:'No check-in',value:Math.max(0,p.company_recorded_days-p.attendance_days),color:'#e7a05b'}],'recorded days')}</section><section><h3>Planned work progress</h3>${donutChart([{label:'Completed',value:p.completed_tasks,color:'#4aa889'},{label:'Blocked',value:p.blocked_tasks,color:'#d77b6c'},{label:'Other due tasks',value:Math.max(0,p.due_tasks-p.completed_tasks-p.blocked_tasks),color:'#658fc1'}],'due tasks')}</section></div>
    <p class="help">Attendance uses dates when at least one employee checked in, through today. No check-in may include approved leave or holidays because those are not recorded in this rate. Completion rate uses planned tasks due through today, excluding cancelled tasks. Points = 1 per attended day + 2 per completed plan + 3 per completed assigned work order + 1 per submitted work report. Reports and tasks can describe the same job.</p>
    ${employeeMemoPanel(memos,p.employee,p.month)}
    ${employeePointsPanel(points)}
    <div class="network-columns"><section><h3>Planned tasks</h3>${p.recent_tasks.length?p.recent_tasks.map(t=>`<div class="network-item"><strong>${escapeHTML(t.date)} · ${escapeHTML(t.title)}</strong><span>${escapeHTML(t.status)} · ${escapeHTML(t.estimated_hours)} h</span><small>${escapeHTML(t.work_order||'No work order')}</small></div>`).join(''):'<p class="muted">No tasks planned this month.</p>'}</section>
    <section><h3>Completed work orders</h3>${p.completed_jobs.length?p.completed_jobs.map(j=>`<div class="network-item"><strong>${escapeHTML(j.code)} · ${escapeHTML(j.title)}</strong><span>${escapeHTML(j.completed_date||'—')} · ${escapeHTML(j.customer||'')}</span></div>`).join(''):'<p class="muted">No assigned work orders recorded as completed this month.</p>'}</section>
    <section><h3>Work reports</h3>${p.recent_reports.length?p.recent_reports.map(r=>`<div class="network-item"><strong>${escapeHTML(r.date)} · ${escapeHTML(r.job_no||'General work')}</strong><span>${escapeHTML(r.work_details)}</span><small>${escapeHTML(r.status)}</small></div>`).join(''):'<p class="muted">No work reports this month.</p>'}</section></div></article>`;
  $('employee-profile').scrollIntoView({behavior:'smooth',block:'start'});
}
let employeeMemoRules=[];
function employeeMemoPanel(memos,employee,month){
  employeeMemoRules=memos.rules;
  const stage=memos.threshold===20?'20-point threshold: urgent management review':memos.threshold===16?'16-point threshold: final warning review':memos.threshold===12?'12-point threshold: written warning review':'Below the 12-point review threshold';
  return `<section class="employee-points-panel"><div class="section-heading"><div><h2>Disciplinary points and memo form</h2><p class="muted">${escapeHTML(employee.name)} · ${escapeHTML(employee.code)} · ${escapeHTML(employee.department)}</p></div></div>
  <div class="stats"><article class="stat"><span>Cumulative disciplinary points</span><strong class="points-negative">${memos.total}</strong><small>All recorded dates; voided memos excluded</small></article><article class="stat"><span>Selected month</span><strong>${memos.monthly}</strong><small>${escapeHTML(month)}</small></article><article class="stat"><span>Management review</span><strong>${memos.threshold||'—'}</strong><small>${stage}</small></article></div>
  <p class="help">Disciplinary points accumulate separately from rewards. The documents do not define an expiry or reset period; the cumulative total includes all dates. A threshold flags review and does not change salary, bonus, benefits or employment status.</p>
  <details class="memo-guide"><summary>Policy point ranges, exceptions and review thresholds</summary><div class="table-wrap"><table><thead><tr><th>Reason</th><th>Points</th><th>Source</th></tr></thead><tbody>${memos.rules.map(r=>`<tr><td>${escapeHTML(r.label)}</td><td>${r.minimum===r.maximum?r.minimum:r.minimum+'–'+r.maximum}</td><td>${escapeHTML(r.source)}</td></tr>`).join('')}</tbody></table></div><p>Policy thresholds: 12 = written warning and annual bonus restriction; 16 = final warning and increment/promotion restriction; 20 = termination and benefits clause. These are the supplied policy's provisions, recorded here for management review, not automatic actions.</p><p>No disciplinary points for appropriately documented work injury, qualifying medical/family illness, disaster/transport disruption, travel accident, approved advance casual leave or eligible maternity leave. Check approved monthly permission before recording lateness or early departure. Do not count the same incident under multiple rules.</p><p>Suggested additions: use the lower end for a minor first incident, the middle for a documented repeat after guidance, and the upper end for a verified serious or repeated incident. Record the impact, previous guidance and employee explanation. These suggested ranges are not specified in the uploaded policy.</p></details>
  <form id="employee-memo-form" class="points-entry-form"><label>Employee<input value="${escapeHTML(employee.name+' · '+employee.code)}" readonly></label><label>Incident date<input name="date" type="date" value="${today}" max="${today}" required></label><label class="points-reason">Reason / policy rule<select name="rule_id" id="memo-rule">${memos.rules.map(r=>`<option value="${r.id}">${escapeHTML(r.label)} (${r.minimum===r.maximum?r.minimum:r.minimum+'–'+r.maximum} points)</option>`).join('')}</select></label><label>Disciplinary points<input id="memo-points" name="points" type="number" min="0.5" max="0.5" step="0.5" value="0.5" readonly required></label><p id="memo-rule-source" class="help">${escapeHTML(memos.rules[0].source)} · Fixed policy points</p><label class="points-reason">Incident details and reason for selected points<textarea name="details" minlength="3" maxlength="2000" rows="3" required placeholder="Record the date/time, what happened, impact and any previous guidance."></textarea></label><label class="points-reason">Evidence / leave or permission reference<textarea name="evidence" maxlength="2000" rows="2" placeholder="Attendance record, approved leave reference, job number or witness details. Text reference only."></textarea></label><label class="points-reason">Employee explanation<textarea name="employee_response" maxlength="2000" rows="2" placeholder="Record the employee's explanation or state that it has not yet been received."></textarea></label><label class="points-reason">Supervisor / Production Manager / Manager / MD review notes<textarea name="review_notes" maxlength="2000" rows="2" placeholder="Names, roles, review dates and comments. These notes are not electronic signatures."></textarea></label><label class="points-reason memo-check"><input name="reviewed" type="checkbox" required><span>I reviewed the incident, supporting information, approved permission and leave exceptions. This memo is for the employee concerned and does not duplicate another penalty for the same incident.</span></label><button class="primary" type="submit">Record disciplinary memo</button></form>
  <h3 class="points-history-title">Memo history · ${escapeHTML(month)}</h3>${memos.entries.length?memos.entries.map(r=>`<article class="memo-record"><div><strong>Memo #${r.id} · ${escapeHTML(r.date)} · ${r.points} points ${r.voided_at?'· VOIDED':''}</strong>${r.voided_at?'':`<button class="small-button" data-void-memo="${r.id}">Void / correct</button>`}</div><h4>${escapeHTML(r.rule)}</h4><p>${escapeHTML(r.details)}</p><small>${escapeHTML(r.source)}</small><details><summary>Evidence, explanation and review record</summary><p><strong>Evidence:</strong> ${escapeHTML(r.evidence||'Not provided')}</p><p><strong>Employee explanation:</strong> ${escapeHTML(r.employee_response||'Not recorded')}</p><p><strong>Review notes:</strong> ${escapeHTML(r.review_notes||'Not recorded')}</p><p><strong>Recorded by:</strong> ${escapeHTML(r.recorded_by)} · ${escapeHTML(new Date(r.created_at).toLocaleString('en-IN',{timeZone:zone}))}</p>${r.voided_at?`<p><strong>Voided by:</strong> ${escapeHTML(r.voided_by)} · ${escapeHTML(new Date(r.voided_at).toLocaleString('en-IN',{timeZone:zone}))}<br>${escapeHTML(r.void_reason)}</p>`:''}</details></article>`).join(''):'<p class="help">No disciplinary memos recorded for this month.</p>'}</section>`;
}
$('employee-profile').addEventListener('change',e=>{
  if(e.target.id!=='memo-rule')return;
  const r=employeeMemoRules.find(r=>r.id===e.target.value),input=$('memo-points');
  input.min=r.minimum;input.max=r.maximum;input.value=r.minimum;input.readOnly=r.minimum===r.maximum;
  $('memo-rule-source').textContent=r.source+(input.readOnly?' · Fixed policy points':' · Select '+r.minimum+'–'+r.maximum+' points in steps of 0.5');
});
$('employee-profile').addEventListener('submit',e=>{
  if(e.target.id!=='employee-memo-form')return;e.preventDefault();
  const form=e.target,button=form.querySelector('button[type="submit"]');if(button.disabled)return;
  const data=Object.fromEntries(new FormData(form)),id=selectedEmployeeProfileId;button.disabled=true;
  perform(async()=>{try{await api('/api/employees/'+id+'/memos','POST',{...data,points:Number(data.points),reviewed:data.reviewed==='on'});await showEmployeeProfile(id,data.date.slice(0,7));notice('Disciplinary memo recorded.');}finally{button.disabled=false;}});
});
$('employee-profile').addEventListener('click',e=>{
  const button=e.target.closest('[data-void-memo]');if(!button||button.disabled)return;
  const reason=prompt('Reason for voiding this memo? The original history will be retained.');if(reason===null)return;
  const id=selectedEmployeeProfileId,month=$('employee-profile-month').value;button.disabled=true;
  perform(async()=>{try{await api('/api/employees/'+id+'/memos/'+button.dataset.voidMemo+'/void','POST',{reason});await showEmployeeProfile(id,month);notice('Memo voided; disciplinary total updated.');}finally{button.disabled=false;}});
});
function employeePointsPanel(points){
  const signed=n=>n>0?'+'+n:String(n);
  return `<section class="employee-points-panel"><div class="section-heading"><div><h2>Reward points and adjustments</h2><p class="muted">${escapeHTML(points.month)} · Administrator-recorded awards and deductions</p></div></div>
    <div class="stats"><article class="stat"><span>Awarded this month</span><strong class="points-positive">+${points.awarded}</strong></article><article class="stat"><span>Deducted this month</span><strong class="points-negative">−${points.deducted}</strong></article><article class="stat"><span>Monthly net</span><strong>${signed(points.net)}</strong></article><article class="stat"><span>All-time balance</span><strong>${signed(points.all_time)}</strong></article></div>
    <p class="help">Starts at zero. These points are separate from automatic activity points above. Awards add points; deductions subtract points. Voided entries do not affect totals. No automatic attendance deductions.</p>
    <div class="points-categories">${points.by_category.map(c=>`<span>${escapeHTML(c.category)} <strong>${signed(c.points)}</strong></span>`).join('')}</div>
    <form id="employee-points-form" class="points-entry-form"><label>Action<select name="action"><option value="award">Award points</option><option value="deduct">Deduct points</option></select></label><label>Category<select name="category">${points.categories.map(c=>`<option>${escapeHTML(c)}</option>`).join('')}</select></label><label>Points<input name="points" type="number" min="1" max="100" step="1" value="5" required></label><label>Event date<input name="date" type="date" value="${today}" max="${today}" required></label><label class="points-reason">Reason / observed work or behaviour<textarea name="reason" minlength="3" maxlength="1000" rows="2" placeholder="Describe what happened and why points are being awarded or deducted." required></textarea></label><button class="primary" type="submit">Save points entry</button></form>
    <h3 class="points-history-title">Points history · ${escapeHTML(points.month)}</h3>
    ${points.entries.length?`<div class="table-wrap"><table><thead><tr><th>Date</th><th>Category</th><th>Points</th><th>Reason</th><th>Recorded by</th><th>Status / correction</th></tr></thead><tbody>${points.entries.map(r=>`<tr><td>${escapeHTML(r.date)}</td><td>${escapeHTML(r.category)}</td><td class="${r.points>0?'points-positive':'points-negative'}"><strong>${signed(r.points)}</strong></td><td class="points-reason-cell">${escapeHTML(r.reason)}</td><td>${escapeHTML(r.recorded_by)}<small>${escapeHTML(new Date(r.created_at).toLocaleString('en-IN',{timeZone:zone}))}</small></td><td>${r.voided_at?`<strong>Voided</strong><small>${escapeHTML(r.void_reason)}</small><small>By ${escapeHTML(r.voided_by)} · ${escapeHTML(new Date(r.voided_at).toLocaleString('en-IN',{timeZone:zone}))}</small>`:`<button class="small-button" data-void-points="${r.id}">Void / correct</button>`}</td></tr>`).join('')}</tbody></table></div>`:'<p class="help">No points entries this month. Add the first award or deduction above.</p>'}</section>`;
}
$('employee-profile').addEventListener('submit',e=>{
  if(e.target.id!=='employee-points-form')return;
  e.preventDefault();const form=e.target,button=form.querySelector('button[type="submit"]');if(button.disabled)return;
  const data=Object.fromEntries(new FormData(form)),employeeId=selectedEmployeeProfileId;
  button.disabled=true;
  perform(async()=>{try{
    await api('/api/employees/'+employeeId+'/points','POST',{category:data.category,points:Number(data.points)*(data.action==='deduct'?-1:1),date:data.date,reason:data.reason});
    await showEmployeeProfile(employeeId,data.date.slice(0,7));notice('Points entry saved.');
  }finally{button.disabled=false;}});
});
$('employee-profile').addEventListener('click',e=>{
  const button=e.target.closest('[data-void-points]');if(!button||button.disabled)return;
  const reason=prompt('Why should this entry be voided? The original entry will remain in the history.');if(reason===null)return;
  const employeeId=selectedEmployeeProfileId,month=$('employee-profile-month').value;button.disabled=true;
  perform(async()=>{try{await api('/api/employees/'+employeeId+'/points/'+button.dataset.voidPoints+'/void','POST',{reason});await showEmployeeProfile(employeeId,month);notice('Entry voided and points recalculated. Add a new entry if a replacement is needed.');}finally{button.disabled=false;}});
});
async function refreshMine() {
  const identity = await api('/api/session');
  if (!identity.user) throw new Error('Please sign in again.');
  user = identity.user; today = identity.today;
  const [rows, status] = await Promise.all([api('/api/attendance?date='+today),api('/api/my-status')]);
  openShift = status.open_shift;
  $('greeting').textContent = 'Hello, ' + user.name.split(' ')[0] + '.';
  const finished = rows.some(r => r.check_out);
  $('employee-status').textContent = openShift ? 'Checked in at ' + time(openShift.check_in) + ' · ' + openShift.date : finished ? 'Your attendance is complete for today.' : 'Ready for a new working day.';
  attendanceComplete = !openShift && finished;
  setAttendanceBusy(attendanceBusy);
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
  $('customer-table').innerHTML=`<div class="table-wrap"><table><thead><tr><th>Customer</th><th>Industry</th><th>Contacts</th><th>Machines</th><th>Open jobs</th><th>Status</th><th></th></tr></thead><tbody>${customers.map(c=>`<tr><td><strong>${escapeHTML(c.name)}</strong><small class="muted">${escapeHTML(c.code||'')} · GST ${escapeHTML(c.gstin||'—')}</small></td><td>${escapeHTML(c.industry||'—')}</td><td>${c.contact_count}</td><td>${c.machine_count}</td><td>${c.open_jobs}</td><td>${statusPill(c.status)}</td><td><button class="small-button" data-customer-open="${c.id}">View company profile</button></td></tr>`).join('')}</tbody></table></div>`;
}
async function showCustomerDetail(id){
  const c=await api('/api/customers/'+id);
  $('customer-detail').innerHTML=`<article class="section-card customer-network"><div class="section-heading"><div><span class="eyebrow accent">${escapeHTML(c.code||'CUSTOMER')}</span><h2>${escapeHTML(c.name)}</h2><p class="muted">${escapeHTML(c.address||'No address recorded')}</p></div><div class="action-buttons">${user.admin?`<button class="secondary" data-edit-customer="${c.id}">Edit customer</button><button class="small-button danger" data-delete-customer="${c.id}">Delete customer</button>`:''}<button class="secondary" data-add-contact="${c.id}">+ Contact</button><button class="secondary" data-add-machine-customer="${c.id}">+ Machine</button><button class="primary" data-add-job-customer="${c.id}">+ Work order</button></div></div>
  <div class="stats company-profile-stats"><article class="stat"><span>Registered machines</span><strong>${c.machines.length}</strong><small>Machines recorded for this company</small></article><article class="stat"><span>Machines serviced</span><strong>${c.serviced_machine_count}</strong><small>With linked service work or reports</small></article><article class="stat"><span>Service work orders</span><strong>${c.jobs.filter(j=>Boolean(j.service_type)||['service','repair','installation','inspection','preventive maintenance'].includes(j.work_type.toLowerCase())).length}</strong><small>All recorded statuses</small></article><article class="stat"><span>Linked work reports</span><strong>${c.work_reports.length}</strong><small>Recorded work details</small></article></div>
  <div class="network-columns">
    <section><h3>Important people</h3>${c.contacts.length?c.contacts.map(x=>`<div class="network-item"><strong>${escapeHTML(x.name)}</strong><span>${escapeHTML(x.designation||x.department||'Contact')}</span><small>${escapeHTML(x.phone||'')} ${escapeHTML(x.email||'')}</small>${user.admin?`<button class="small-button" data-edit-contact="${x.id}" data-customer-id="${c.id}">Edit</button>`:''}</div>`).join(''):'<p class="muted">No contacts yet.</p>'}</section>
    <section><h3>Machines at this company</h3>${c.machines.length?c.machines.map(m=>`<button class="network-item network-button" data-machine-open="${m.id}"><strong>${escapeHTML(m.customer_machine_no||m.code)}</strong><span>${escapeHTML(m.machine_type||'Machine')} · ${escapeHTML(m.model||'')}</span><small>${escapeHTML(m.code||'')}</small></button>`).join(''):'<p class="muted">No machines yet.</p>'}</section>
    <section><h3>Recent work orders</h3>${c.jobs.length?c.jobs.slice(0,8).map(j=>`<div class="network-item"><strong>${escapeHTML(j.code)} · ${escapeHTML(j.title)}</strong><span>${escapeHTML(j.machine_no||'General')} · ${escapeHTML(j.status)}</span><small>${j.assignments.map(a=>escapeHTML(a.employee)).join(', ')||'Not assigned'}</small></div>`).join(''):'<p class="muted">No work orders yet.</p>'}</section>
  </div>
  <section class="company-service-section"><h3>Service history</h3>${c.jobs.filter(j=>Boolean(j.service_type)||['service','repair','installation','inspection','preventive maintenance'].includes(j.work_type.toLowerCase())).length?`<div class="table-wrap"><table><thead><tr><th>Date</th><th>Service job</th><th>Machine</th><th>Details</th><th>Status</th></tr></thead><tbody>${c.jobs.filter(j=>Boolean(j.service_type)||['service','repair','installation','inspection','preventive maintenance'].includes(j.work_type.toLowerCase())).map(j=>`<tr><td>${escapeHTML(j.completed_date||j.start_date||'—')}</td><td><strong>${escapeHTML(j.code)}</strong><small class="muted">${escapeHTML(j.title)}</small></td><td>${escapeHTML(j.machine_no||j.machine_code||'Not linked')}</td><td class="wrap-cell">${escapeHTML(j.description||'—')}</td><td>${statusPill(j.status)}</td></tr>`).join('')}</tbody></table></div>`:'<p class="muted">No service work orders linked to this company yet.</p>'}</section>
  <section class="company-service-section"><h3>Service and work reports</h3>${c.work_reports.length?`<div class="table-wrap"><table><thead><tr><th>Date</th><th>Employee</th><th>Job / Machine</th><th>Work performed</th><th>Problems / supervisor note</th><th>Status</th></tr></thead><tbody>${c.work_reports.map(r=>`<tr><td>${escapeHTML(r.date)}</td><td>${escapeHTML(r.employee)}</td><td>${escapeHTML(r.job_no||'—')}<small class="muted">${escapeHTML(r.machine||'—')}</small></td><td class="wrap-cell">${escapeHTML(r.work_details)}</td><td class="wrap-cell">${escapeHTML([r.problems,r.supervisor_note].filter(Boolean).join(' · ')||'—')}</td><td>${statusPill(r.status)}</td></tr>`).join('')}</tbody></table></div>`:'<p class="muted">No linked work reports yet. Link a work report to this customer’s work order or machine to show it here.</p>'}</section>
  </article>`;
  $('customer-detail').scrollIntoView({behavior:'smooth',block:'start'});
}
let selectedMachineCompanyId=null;
async function refreshMachines(){
  selectedMachineCompanyId=null;
  const companies=await api('/api/customers');
  $('machine-history').innerHTML='';
  $('machine-table').innerHTML=companies.length?`<div class="machine-folder-grid">${companies.map(c=>`<button class="section-card machine-folder" data-machine-company="${c.id}"><span class="eyebrow accent">COMPANY FOLDER</span><strong>${escapeHTML(c.name)}</strong><small>${escapeHTML(c.code||'')} · ${c.machine_count} machine${c.machine_count===1?'':'s'}</small></button>`).join('')}</div>`:'<div class="empty"><strong>No companies yet</strong>Add a customer to create its machine folder.</div>';
}
async function openMachineCompany(id){
  selectedMachineCompanyId=Number(id);
  const [company,rows]=await Promise.all([api('/api/customers/'+id),api('/api/machines?customer_id='+encodeURIComponent(id))]);
  machines=rows;
  $('machine-history').innerHTML='';
  $('machine-table').innerHTML=`<div class="section-card"><button class="text-button" data-machine-back="1">← All companies</button><div class="section-heading"><div><span class="eyebrow accent">COMPANY MACHINES</span><h2>${escapeHTML(company.name)}</h2><p class="muted">${rows.length} registered machine${rows.length===1?'':'s'} · Open a machine to see who serviced it and which product was serviced.</p></div></div>${rows.length?`<div class="table-wrap"><table><thead><tr><th>Cosmos ID</th><th>Customer machine no.</th><th>Type</th><th>Make / model</th><th>Status</th><th></th></tr></thead><tbody>${rows.map(m=>`<tr><td><strong>${escapeHTML(m.code)}</strong></td><td>${escapeHTML(m.customer_machine_no||'—')}</td><td>${escapeHTML(m.machine_type||'—')}</td><td>${escapeHTML([m.manufacturer,m.model].filter(Boolean).join(' / ')||'—')}</td><td>${statusPill(m.status)}</td><td><button class="small-button" data-machine-open="${m.id}">Service history</button><button class="small-button" data-machine-edit="${m.id}">Edit machine</button></td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">No machines registered for this company yet.</div>'}</div>`;
}
async function showMachineHistory(id){
  const h=await api('/api/machines/'+id+'/history'),m=h.machine;
  if(selectedMachineCompanyId!==m.customer_id)await openMachineCompany(m.customer_id);
  const serviceJobs=h.jobs.filter(j=>['service','repair','inspection','preventive maintenance','installation'].includes(j.work_type.toLowerCase()));
  const jobById=new Map(h.jobs.map(j=>[j.id,j]));
  const events=[
    ...serviceJobs.map(j=>({date:j.completed_date||j.start_date||'',product:j.service_product||j.title,
      people:j.assignments.map(a=>a.employee).join(', ')||j.job_owner?.name||'Not recorded',
      details:j.description||j.title,reference:j.code,status:j.status,type:'Service work order (assigned staff)'})),
    ...h.work_reports.map(r=>({date:r.date,product:jobById.get(r.work_order_id)?.service_product||jobById.get(r.work_order_id)?.title||r.machine||'Not specified',
      people:r.employee,details:r.work_details,reference:r.job_no||'Work report',status:r.status,type:'Work report (reported by)'}))
  ].sort((a,b)=>(b.date||'').localeCompare(a.date||''));
  $('machine-history').innerHTML=`<article class="section-card machine-history-card"><div class="section-heading"><div><span class="eyebrow accent">${escapeHTML(m.code)} · SERVICE FILE</span><h2>${escapeHTML(m.customer_machine_no||m.model||'Machine')}</h2><p class="muted">${escapeHTML(m.customer||'')} · ${escapeHTML(m.machine_type||'')} · ${escapeHTML([m.manufacturer,m.model].filter(Boolean).join(' / ')||'—')}</p></div>${user.admin?`<button class="secondary" data-machine-edit="${m.id}">Edit machine</button>`:''}</div><div class="machine-specs"><p><strong>Controller:</strong> ${escapeHTML(m.controller||'—')} · <strong>Serial number:</strong> ${escapeHTML(m.serial_no||'—')}</p><p>${escapeHTML(m.specifications||'Machine specifications have not been recorded.')}</p><p>Last service: ${escapeHTML(m.last_service_date||'—')} · Next service: ${deadlineLabel(m.next_service_date,m.status)}</p></div><h3>Service history</h3>${events.length?`<div class="table-wrap"><table><thead><tr><th>Date</th><th>Product serviced</th><th>Employee / assignment</th><th>Work done</th><th>Record</th><th>Status</th></tr></thead><tbody>${events.map(x=>`<tr><td>${escapeHTML(x.date||'—')}</td><td><strong>${escapeHTML(x.product)}</strong></td><td>${escapeHTML(x.people)}</td><td class="wrap-cell">${escapeHTML(x.details)}</td><td>${escapeHTML(x.type)}<small class="muted">${escapeHTML(x.reference)}</small></td><td>${statusPill(x.status)}</td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">No service work or linked reports recorded for this machine yet.</div>'}</article>`;
  $('machine-history').scrollIntoView({behavior:'smooth',block:'start'});
}
async function refreshJobs(){
  jobs=await api('/api/work-orders');
  $('job-table').innerHTML=jobs.length?`<div class="table-wrap"><table><thead><tr><th>Project / work order</th><th>Company / machine</th><th>Assigned team</th><th>Delivery</th><th>Status / stage</th>${user.admin?'<th>Actions</th>':''}</tr></thead><tbody>${jobs.map(j=>`<tr><td><strong>${escapeHTML(j.code)}</strong><small>${escapeHTML(j.title)} · ${escapeHTML(j.service_product||j.work_type)}</small><small>${serviceSummary(j)}</small></td><td>${escapeHTML(j.customer||'—')}<small>${escapeHTML([j.machine_no,j.machine_model].filter(Boolean).join(' · ')||'General work')}</small></td><td class="wrap-cell">${escapeHTML(j.assignments.map(a=>a.employee).join(', ')||'Not assigned')}</td><td>${deadlineLabel(j.target_date,j.status)}</td><td>${statusPill(j.status)}<small>${escapeHTML(j.current_stage||'Stage not set')}</small></td>${user.admin?`<td><div class="action-buttons"><button class="small-button" data-job-status="${j.id}">Edit progress</button><button class="small-button" data-customer-update="${j.id}">Update customer</button></div></td>`:''}</tr>`).join('')}</tbody></table></div>`:'<div class="empty"><strong>No projects yet</strong>Create a work order to start tracking customer work.</div>';
}
async function refreshPlanning(){
  const date=$('plan-date').value||today;
  const [rows,workload]=await Promise.all([api('/api/daily-plans?date='+encodeURIComponent(date)),user.admin?api('/api/planning/workload?date='+encodeURIComponent(date)):Promise.resolve(null)]);
  planRows=rows;
  $('plan-workload').hidden=!user.admin;
  if(workload)$('plan-workload').innerHTML=`<div class="workload-heading"><h3>Team workload</h3><span class="subtle-tag">8-hour planning reference</span></div><div class="workload-grid">${workload.employees.map(e=>`<article class="workload-card ${e.planned_hours>8?'over-capacity':''}"><div><strong>${escapeHTML(e.name)}</strong><span>${e.planned_hours.toFixed(1)} h booked</span></div><div class="workload-track" role="img" aria-label="${escapeHTML(e.name)}: ${e.planned_hours} of 8 reference hours"><svg viewBox="0 0 100 6" preserveAspectRatio="none" aria-hidden="true"><rect width="${Math.min(100,e.planned_hours/8*100)}" height="6" rx="3" class="workload-fill"/></svg></div><small>${escapeHTML(e.department)} · ${e.planned_hours>8?(e.planned_hours-8).toFixed(1)+' h above reference':e.remaining_hours.toFixed(1)+' h remaining'}</small></article>`).join('')}</div><p class="help">Based on non-cancelled plans for this date. Leave, skills, shifts and machine availability are not included.</p>`;
  const hours=rows.filter(r=>r.status!=='Cancelled').reduce((sum,r)=>sum+r.estimated_hours,0);
  $('plan-summary').innerHTML=`<article class="stat"><span>Planned tasks</span><strong>${rows.length}</strong></article><article class="stat"><span>Estimated hours</span><strong>${hours.toFixed(1)}</strong></article><article class="stat"><span>Team members assigned</span><strong>${new Set(rows.filter(r=>r.status!=='Cancelled').map(r=>r.employee_id)).size}</strong></article>`;
  $('plan-table').innerHTML=rows.length?`<div class="table-wrap"><table><thead><tr><th>Priority / task</th><th>Company / project / machine</th><th>Employee</th><th>Hours / deadline</th><th>Status</th><th>Actions</th></tr></thead><tbody>${rows.map(r=>`<tr><td><strong>${escapeHTML(r.title)}</strong><small>${escapeHTML(r.priority)} · ${escapeHTML(r.department||'Any department')}</small><small>${serviceSummary(r)}</small><small>${escapeHTML(r.details||'')}</small></td><td>${escapeHTML(r.customer||'Internal work')}<small>${escapeHTML(r.work_order||'No work order')} · ${escapeHTML(r.machine||'No specific machine')}</small></td><td>${escapeHTML(r.employee)}</td><td>${r.estimated_hours} h<small>${deadlineLabel(r.due_date,r.status)}</small></td><td>${statusPill(r.status)}</td><td><select aria-label="Update ${escapeHTML(r.title)}" data-plan-status="${r.id}"><option value="">Change status</option>${optionsHTML(user.admin?workflowOptions.plan_statuses:['In Progress','Completed','Blocked','Rework'])}</select>${user.admin?`<div class="action-buttons"><button class="small-button" data-plan-edit="${r.id}">Edit</button><button class="small-button danger" data-plan-delete="${r.id}">Delete</button></div>`:''}</td></tr>`).join('')}</tbody></table></div>`:'<div class="empty"><strong>No tasks for this date</strong>Select another date or plan upcoming work.</div>';
}
async function openPlanDialog(){
  await Promise.all([ensureTeam(),ensureCustomers()]);jobs=await api('/api/work-orders');
  const form=$('plan-form');form.reset();$('plan-dialog-title').textContent='Create a work plan';$('plan-form-date').value=$('plan-date').value||today;form.elements.due_date.value=$('plan-form-date').value;
  $('plan-customer').innerHTML='<option value="">Internal work</option>'+customerOptions();$('plan-customer').value='';
  const departments=[...new Set(team.filter(e=>e.active).map(e=>e.department).filter(Boolean))].sort();
  $('plan-department').innerHTML='<option value="">Any department</option>'+optionsHTML(departments);fillPlanEmployees();await syncPlanCompany();
  $('plan-capacity').closest('label').hidden=false;$('preview-assignment').hidden=false;$('plan-assignment-preview').className='assignment-preview';$('plan-assignment-preview').textContent='Preview who has capacity for this task before saving.';
  $('plan-dialog').showModal();
}
async function openCustomerDialog(){$('customer-form').reset();$('customer-dialog-title').textContent='Add customer';$('customer-dialog').showModal();}
async function openMachineDialog(customerId=''){
  await ensureCustomers();$('machine-form').reset();$('machine-customer').disabled=false;$('machine-dialog-title').textContent='Add machine';$('machine-customer').innerHTML=customerOptions(customerId);$('machine-dialog').showModal();
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
  const warning=['Blocked','Pending','Open','In Progress','Rework','On Hold','Overdue','Low stock'].includes(status);
  return `<span class="pill ${warning?'warning':'neutral'}">${escapeHTML(status)}</span>`;
}
async function refreshWork(){
  const rows=await api('/api/work-reports');
  if(!rows.length){$('work-table').innerHTML='<div class="empty"><strong>No work reports yet</strong>Add the first daily work report to start building the company memory.</div>';return;}
  $('work-table').innerHTML=`<div class="table-wrap"><table><thead><tr><th>Employee</th><th>Date</th><th>Job / Customer</th><th>Work</th><th>Machine</th><th>Status</th><th>Problems</th>${user.admin?'<th>Review</th>':''}</tr></thead><tbody>${rows.map(r=>`<tr><td>${personCell(r.employee,r.code)}</td><td>${escapeHTML(r.date)}</td><td><strong>${escapeHTML(r.job_no||'—')}</strong><small class="muted">${escapeHTML(r.customer||'')}</small></td><td class="wrap-cell">${escapeHTML(r.work_details)}<small>${serviceSummary(r)}</small></td><td>${escapeHTML(r.machine||'—')}</td><td>${statusPill(r.status)}</td><td class="wrap-cell">${escapeHTML(r.problems||'—')}</td>${user.admin?`<td><button class="small-button" data-work-review="${r.id}">${r.verified_by?'Reviewed':'Review'}</button></td>`:''}</tr>`).join('')}</tbody></table></div>`;
}
async function refreshIssues(){
  const rows=await api('/api/issues');
  if(!rows.length){$('issues-table').innerHTML='<div class="empty"><strong>No problems recorded</strong>Reported difficulties and their resolutions will appear here.</div>';return;}
  $('issues-table').innerHTML=`<div class="table-wrap"><table><thead><tr><th>Employee</th><th>Date</th><th>Category</th><th>Problem</th><th>Status</th><th>Resolution</th>${user.admin?'<th>Actions</th>':''}</tr></thead><tbody>${rows.map(r=>`<tr><td>${personCell(r.employee,r.code)}</td><td>${escapeHTML(r.date)}</td><td>${escapeHTML(r.category)}</td><td class="wrap-cell"><strong>${escapeHTML(r.title)}</strong><small class="muted">${escapeHTML(r.detail)}</small></td><td>${statusPill(r.status)}</td><td class="wrap-cell">${escapeHTML(r.resolution||'—')}</td>${user.admin?`<td><button class="small-button" data-issue-resolve="${r.id}">${r.status==='Resolved'?'Edit resolution':'Resolve'}</button></td>`:''}</tr>`).join('')}</tbody></table></div>`;
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
  $('stock-table').innerHTML=stockItems.length?`<div class="table-wrap"><table><thead><tr><th>SKU / item</th><th>Category</th><th>Location</th><th>On hand / available</th><th>Reorder</th><th>Actions</th></tr></thead><tbody>${stockItems.map(x=>`<tr><td><strong>${escapeHTML(x.sku)}</strong><small>${escapeHTML(x.name)} · ${escapeHTML(x.specification||'')}</small></td><td>${escapeHTML(x.category)}</td><td>${escapeHTML(x.location||'—')}</td><td>${escapeHTML(x.quantity)} ${escapeHTML(x.unit)}<small>${escapeHTML(x.available)} available · ${escapeHTML(x.reserved)} reserved</small></td><td>${Number(x.available)<=Number(x.reorder_level)?'<span class="pill">Low stock</span>':escapeHTML(x.reorder_level)}</td><td><button class="small-button" data-stock-history="${x.id}">History</button> <button class="small-button" data-stock-move="${x.id}" ${x.active?'':'disabled'}>Move</button> <button class="small-button" data-stock-edit="${x.id}">Edit</button></td></tr>`).join('')}</tbody></table></div>`:'<div class="empty"><strong>No items yet</strong>Add your first stock item.</div>';
}
async function showStockHistory(id){
  const rows=await api('/api/stock-items/'+id+'/movements');
  $('stock-history').innerHTML=`<div class="section-card"><h3>Movement history · ${escapeHTML(stockItems.find(x=>x.id===Number(id))?.sku||id)}</h3>${rows.length?`<div class="table-wrap"><table><thead><tr><th>When</th><th>Type</th><th>Change</th><th>Balance</th><th>Work order</th><th>Reason / reference</th></tr></thead><tbody>${rows.map(m=>`<tr><td>${escapeHTML(new Date(m.at).toLocaleString('en-IN'))}</td><td>${escapeHTML(m.kind)}</td><td>${escapeHTML(m.change)}</td><td>${escapeHTML(m.balance)}</td><td>${escapeHTML(m.work_order_id||'—')}</td><td>${escapeHTML(m.reason)} · ${escapeHTML(m.reference||'')}</td></tr>`).join('')}</tbody></table></div>`:'<p>No movements yet.</p>'}</div>`;
}
async function refreshPurchasing(){
  const [s,orders]=await Promise.all([api('/api/suppliers'),api('/api/purchase-orders')]);suppliers=s;purchaseOrders=orders;
  $('supplier-table').innerHTML=`<h3>Supplier directory</h3>${s.length?`<div class="table-wrap"><table><thead><tr><th>Name</th><th>Contact</th><th>Phone</th><th>GSTIN</th></tr></thead><tbody>${s.map(x=>`<tr><td>${escapeHTML(x.name)}</td><td>${escapeHTML(x.contact||'—')}</td><td>${escapeHTML(x.phone||'—')}</td><td>${escapeHTML(x.gstin||'—')}</td></tr>`).join('')}</tbody></table></div>`:'<p class="muted">No suppliers yet.</p>'}`;
  $('purchase-table').innerHTML=`<h3>Purchase orders</h3>${orders.length?`<div class="table-wrap"><table><thead><tr><th>PO / supplier</th><th>Item</th><th>Ordered / received</th><th>Expected delivery</th><th>Status</th><th>Actions</th></tr></thead><tbody>${orders.map(x=>`<tr><td><strong>PO-${x.id}</strong><small>${escapeHTML(x.supplier)} · ${escapeHTML(x.supplier_reference||'')}</small></td><td>${escapeHTML(x.item)}<small>₹${escapeHTML(x.order_value)} before tax / freight</small></td><td>${escapeHTML(x.ordered_qty)} / ${escapeHTML(x.received_qty)} ${escapeHTML(x.unit)}</td><td>${deadlineLabel(x.expected_date,['Received','Closed','Cancelled'].includes(x.status)?'Completed':x.status)}</td><td>${statusPill(x.status)}</td><td><div class="action-buttons"><button class="small-button" data-po-edit="${x.id}">Edit</button><a class="small-button" href="/api/purchase-orders/${x.id}/print" target="_blank" rel="noopener">Print</a>${!['Received','Closed','Cancelled'].includes(x.status)?`<button class="small-button" data-po-receive="${x.id}" data-outstanding="${escapeHTML(x.outstanding_qty)}">Receive</button>`:''}</div><select aria-label="Purchase order actions" data-po-status="${x.id}"><option value="">More actions</option><option value="Ordered">Mark ordered</option><option value="Closed">Close remaining order</option><option value="Cancelled">Cancel remaining order</option><option value="Open">Reopen</option></select></td></tr>`).join('')}</tbody></table></div>`:'<p class="muted">No purchase orders yet.</p>'}`;
}
async function refreshProduction(){
  const [steps,orders]=await Promise.all([api('/api/production-steps'),api('/api/work-orders')]);
  productionRows=steps;const byId=new Map(orders.map(j=>[j.id,j]));
  $('production-table').innerHTML=steps.length?`<div class="table-wrap"><table><thead><tr><th>Work order</th><th>Sequence / operation</th><th>Plan</th><th>Hours plan / actual</th><th>Accepted / rejected</th><th>Status</th><th>Actions</th></tr></thead><tbody>${steps.map(s=>`<tr><td>${escapeHTML(byId.get(s.work_order_id)?.code||s.work_order_id)}</td><td>${s.sequence} · ${escapeHTML(s.operation)}</td><td>${escapeHTML(s.planned_date||'—')}</td><td>${escapeHTML(s.planned_hours)} / ${escapeHTML(s.actual_hours)}</td><td>${escapeHTML(s.accepted_qty)} / ${escapeHTML(s.rejected_qty)}</td><td>${escapeHTML(s.status)}${s.delay_reason?`<small>${escapeHTML(s.delay_reason)}</small>`:''}</td><td><button class="small-button" data-step-update="${s.id}">Update</button></td></tr>`).join('')}</tbody></table></div>`:'<p class="muted">Add operations to a work order to plan production.</p>';
}
let maintenanceAssets=[],maintenanceTasks=[];
async function refreshMaintenance(){
  const [assets,tasks]=await Promise.all([api('/api/company-assets'),api('/api/maintenance-tasks')]);
  maintenanceAssets=assets;maintenanceTasks=tasks;
  $('maintenance-summary').innerHTML=[['Open tasks',tasks.filter(t=>!terminalStates.includes(t.status)).length],['Overdue',tasks.filter(t=>!terminalStates.includes(t.status)&&t.due_date&&t.due_date<today).length],['Company assets',assets.filter(a=>a.active).length],['Tools',assets.filter(a=>a.active&&a.kind==='Tool').length]].map(([label,n])=>`<article class="stat"><span>${label}</span><strong>${n}</strong></article>`).join('');
  $('asset-table').innerHTML=`<h3>Company assets</h3>${assets.length?`<div class="table-wrap"><table><thead><tr><th>Code</th><th>Kind / name</th><th>Registration / serial</th><th>Next service</th><th>Actions</th></tr></thead><tbody>${assets.map(a=>`<tr><td>${escapeHTML(a.code)}</td><td>${escapeHTML(a.kind)} · ${escapeHTML(a.name)}</td><td>${escapeHTML(a.serial_or_registration||'—')}</td><td>${escapeHTML(a.next_service_date||'—')}</td><td><button class="small-button" data-asset-edit="${a.id}">Edit</button><button class="small-button" data-asset-history="${a.id}">Service history</button></td></tr>`).join('')}</tbody></table></div>`:'<p class="muted">No company assets yet.</p>'}`;
  $('maintenance-table').innerHTML=`<h3>Maintenance tasks</h3>${tasks.length?`<div class="table-wrap"><table><thead><tr><th>Asset / service</th><th>Work / activities</th><th>Assigned to</th><th>Due</th><th>Status / checks</th><th>Actions</th></tr></thead><tbody>${tasks.map(t=>`<tr><td><strong>${escapeHTML(t.asset)}</strong><small>${escapeHTML(t.task_type)} · ${escapeHTML(t.priority)}</small></td><td class="wrap-cell">${escapeHTML(t.description)}<small>${escapeHTML(t.service_activities.join(' · '))}</small></td><td>${escapeHTML(t.assigned||'Not assigned')}</td><td>${deadlineLabel(t.due_date,t.status)}</td><td>${statusPill(t.status)}<small>${t.checklist.filter(c=>c.done).length} / ${t.checklist.length} checks complete</small></td><td><button class="small-button" data-task-edit="${t.id}">Edit / complete</button></td></tr>`).join('')}</tbody></table></div>`:'<p class="muted">No maintenance tasks yet.</p>'}`;
}
async function refreshAnalytics(){
  const [data,storage]=await Promise.all([api('/api/management-summary'),api('/api/storage-summary')]);
  const labels={open_jobs:'Open jobs',overdue_jobs:'Overdue jobs',blocked_steps:'Blocked operations',accepted_quantity:'Accepted quantity',rejected_quantity:'Rejected quantity',low_stock:'Low stock items',open_maintenance:'Open maintenance'};
  $('analytics-content').innerHTML=Object.entries(labels).map(([k,label])=>`<article class="stat"><span>${label}</span><strong>${escapeHTML(data[k])}</strong></article>`).join('');
  $('analytics-content').insertAdjacentHTML('beforeend',`<article class="stat"><span>Database storage</span><strong>${storage.database_bytes===null?'—':(storage.database_bytes/1000000).toFixed(1)+' MB'}</strong><small>${escapeHTML(storage.provider)} · Plan quota: check provider dashboard</small></article><article class="stat"><span>Stored documents</span><strong>${storage.document_count}</strong><small>${(storage.document_bytes/1000000).toFixed(1)} MB · 2 MB per upload</small></article>`);
}
async function refreshQuotations(){
  const rows=await api('/api/quotations');
  $('quotation-table').innerHTML=rows.length?`<div class="table-wrap"><table><thead><tr><th>Quote</th><th>Customer / scope</th><th>Amount ₹</th><th>Follow up</th><th>Status</th><th>Work order</th><th>Actions</th></tr></thead><tbody>${rows.map(q=>`<tr><td>${escapeHTML(q.code)} <small>Rev ${q.revision}</small></td><td>${escapeHTML(q.customer)}<small>${escapeHTML(q.title)}</small></td><td>${escapeHTML(q.amount)}</td><td>${escapeHTML(q.follow_up_date||'—')}</td><td>${escapeHTML(q.status)}</td><td>${escapeHTML(q.work_order_id||'—')}</td><td>${q.work_order_id?'Converted':`<button class="small-button" data-quote-status="${q.id}">Status</button> <button class="small-button" data-quote-accept="${q.id}">Accept PO</button>`}</td></tr>`).join('')}</tbody></table></div>`:'<p class="muted">No quotations yet.</p>';
}
async function refreshJobFiles(){
  const jobs=await api('/api/work-orders'),select=$('job-file-select'),current=select.value;
  select.innerHTML=jobs.map(j=>`<option value="${j.id}">${escapeHTML(j.code)} · ${escapeHTML(j.title)}</option>`).join('');
  if(jobs.some(j=>String(j.id)===current))select.value=current;
  if(!select.value){$('job-material-table').innerHTML='<p>Create a work order first.</p>';$('job-drawing-table').innerHTML='';return;}
  const id=select.value,[materials,drawings,items,documents,quality,dispatch]=await Promise.all([api('/api/work-orders/'+id+'/materials'),api('/api/work-orders/'+id+'/drawings'),api('/api/stock-items'),api('/api/documents/job/'+id),api('/api/work-orders/'+id+'/quality'),api('/api/work-orders/'+id+'/dispatch')]);
  const itemMap=new Map(items.map(x=>[x.id,x]));
  $('job-material-table').innerHTML=`<h3>Material requirements</h3>${materials.length?`<div class="table-wrap"><table><thead><tr><th>Material</th><th>Required</th><th>Reserved</th><th>Issued</th><th>Available</th><th>Actions</th></tr></thead><tbody>${materials.map(m=>`<tr><td>${escapeHTML(m.sku)} · ${escapeHTML(m.item)}</td><td>${escapeHTML(m.required)}</td><td>${escapeHTML(m.reserved)}</td><td>${escapeHTML(m.issued)}</td><td>${escapeHTML(itemMap.get(m.item_id)?.available||'—')}</td><td><button class="small-button" data-material-action="reserve" data-requirement="${m.id}">Reserve</button> <button class="small-button" data-material-action="issue" data-requirement="${m.id}">Issue</button> <button class="small-button" data-material-action="release" data-requirement="${m.id}">Release</button></td></tr>`).join('')}</tbody></table></div>`:'<p class="muted">No material requirements yet.</p>'}`;
  $('job-drawing-table').innerHTML=`<h3>Drawing revisions</h3>${drawings.length?`<div class="table-wrap"><table><thead><tr><th>Drawing</th><th>Revision</th><th>File reference</th><th>Status</th><th>Actions</th></tr></thead><tbody>${drawings.map(d=>`<tr><td>${escapeHTML(d.drawing_no)}</td><td>${escapeHTML(d.revision)}</td><td>${escapeHTML(d.file_reference)}</td><td>${d.approved?'Approved':'Pending'}</td><td>${d.approved?'Current':`<button class="small-button" data-drawing-approve="${d.id}">Approve</button>`}</td></tr>`).join('')}</tbody></table></div>`:'<p class="muted">No drawing revisions yet.</p>'}`;
  $('job-documents').innerHTML='<h3>Private documents</h3>'+documentLinks(documents);
  $('job-quality').innerHTML='<h3>Quality checks</h3>'+(quality.map(q=>`<p>${escapeHTML(q.operation)} · ${escapeHTML(q.result)} · accepted ${escapeHTML(q.accepted)}, rejected ${escapeHTML(q.rejected)} ${escapeHTML(q.defect||'')}</p>`).join('')||'<p class="muted">No inspections yet.</p>');
  $('job-dispatch').innerHTML='<h3>Dispatch</h3>'+(dispatch.map(d=>`<p>${escapeHTML(d.date)} · ${escapeHTML(d.quantity)} · ${escapeHTML(d.transporter||'')} · ${escapeHTML(d.tracking_reference||'')}</p>`).join('')||'<p class="muted">No dispatch recorded.</p>');
}
function documentLinks(rows){return rows.map(d=>`<p><a href="/api/documents/download/${d.id}">${escapeHTML(d.filename)}</a> · ${escapeHTML(new Date(d.uploaded_at).toLocaleDateString('en-IN'))}</p>`).join('')||'<p class="muted">No documents uploaded.</p>';}
async function loadEmployeeDocuments(id){$('employee-documents').innerHTML='<h3>Private documents</h3>'+documentLinks(await api('/api/documents/employee/'+id));}

function moduleIcon(view){
  const paths={home:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',customers:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M9 3a4 4 0 1 0 0 8a4 4 0 0 0 0-8 M17 4a4 4 0 0 1 0 8 M22 21v-2a4 4 0 0 0-3-3.9',quotations:'M6 3h12v18l-3-2-3 2-3-2-3 2z M9 7h6 M9 11h6 M9 15h3',jobs:'M3 7h18v14H3z M8 7V3h8v4 M3 12h18 M10 12v3h4v-3',inventory:'M3 7l9-4 9 4v10l-9 4-9-4z M3 7l9 4 9-4 M12 11v10 M8 5l9 4',production:'M3 21V9l6 4V7l6 5V3h5v18z M7 17h1 M12 17h1 M17 17h1',maintenance:'M14 6a5 5 0 0 0-6 6L3 17a3 3 0 0 0 4 4l5-5a5 5 0 0 0 6-6l-3 3-4-4z',analytics:'M4 3v18h17 M8 16v-4 M13 16V8 M18 16V5',overview:'M4 5h16v16H4z M8 3v4 M16 3v4 M4 11h16 M8 15h3',issues:'M12 3L2 21h20z M12 9v5 M12 17v1',meetings:'M3 4h18v13H8l-5 4z M7 8h10 M7 12h6'};
  const path=paths[view]||paths[{employees:'customers',purchasing:'inventory',machines:'maintenance',checkin:'overview'}[view]]||'M5 3h10l4 4v14H5z M14 3v5h5 M8 12h8 M8 16h6';
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="${path}"/></svg>`;
}
function closeNavigation(){document.body.classList.remove('navigation-open');$('navigation-backdrop').hidden=true;$('open-navigation').setAttribute('aria-expanded','false');}
let deadlineItems=[];
function paintAlertBadge(data){
  $('home-reminder-alert').textContent=`Alerts: ${data.overdue} overdue · ${data.due_today} due today · ${data.low_stock} low-stock items →`;
  $('alerts-count').textContent=data.overdue+data.due_today+data.low_stock;$('open-alerts').classList.toggle('has-alerts',data.overdue+data.due_today+data.low_stock>0);
}
async function refreshReminderBadge(){if(user?.admin)paintAlertBadge(await api('/api/deadline-reminders'));}
async function refreshReminders(){
  const data=await api('/api/deadline-reminders');deadlineItems=data.items;paintAlertBadge(data);
  $('reminder-summary').innerHTML=[['Overdue',data.overdue],['Due today',data.due_today],['Next 7 days',data.next_week],['Low stock',data.low_stock]].map(([label,n])=>`<article class="stat"><span>${label}</span><strong>${n}</strong></article>`).join('');renderReminders();
}
function renderReminders(){
  const filter=$('reminder-filter').value,rows=deadlineItems.filter(r=>filter==='all'||(filter==='stock'?r.kind==='Low stock':filter==='overdue'?r.days!==null&&r.days<0:filter==='today'?r.days===0:r.kind==='Low stock'||r.days!==null&&r.days<=7));
  $('reminder-list').innerHTML=rows.length?`<div class="table-wrap"><table><thead><tr><th>Deadline / condition</th><th>Priority</th><th>Type</th><th>Work / reminder</th><th>Actions</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${escapeHTML(r.due_date||'Replenishment needed')}</td><td>${statusPill(r.urgency)}<small>${r.days===null?'Available stock at or below threshold':r.days<0?Math.abs(r.days)+' days overdue':r.days===0?'Today':'In '+r.days+' days'}</small></td><td>${escapeHTML(r.kind)}</td><td><strong>${escapeHTML(r.title)}</strong><small>${escapeHTML(r.notes||'')}</small></td><td>${r.kind==='Custom'?`<button class="small-button" data-reminder-complete="${r.id}">Complete</button><button class="small-button" data-reminder-date="${r.id}">Change date</button>`:`<button class="small-button" data-reminder-view="${r.view}">Open module</button>`}</td></tr>`).join('')}</tbody></table></div>`:'<p class="help">No open alerts match this filter.</p>';
}
$('refresh-reminders').addEventListener('click',()=>perform(refreshReminders));
$('reminder-filter').addEventListener('change',renderReminders);
$('reminder-form').addEventListener('submit',e=>{e.preventDefault();const form=e.target,button=form.querySelector('button');if(button.disabled)return;button.disabled=true;perform(async()=>{try{await api('/api/deadline-reminders','POST',Object.fromEntries(new FormData(form)));form.reset();$('reminder-filter').value='all';await refreshReminders();notice('Deadline reminder added.');}finally{button.disabled=false;}});});
$('reminder-list').addEventListener('click',e=>perform(async()=>{const view=e.target.closest('[data-reminder-view]'),done=e.target.closest('[data-reminder-complete]'),date=e.target.closest('[data-reminder-date]');if(view)return navigate(view.dataset.reminderView);if(done){await api('/api/deadline-reminders/'+done.dataset.reminderComplete,'PATCH',{completed:true});await refreshReminders();}if(date){const r=deadlineItems.find(r=>r.kind==='Custom'&&r.id===Number(date.dataset.reminderDate));const value=prompt('New deadline (YYYY-MM-DD)',r.due_date);if(value===null)return;await api('/api/deadline-reminders/'+r.id,'PATCH',{due_date:value});await refreshReminders();}}));
async function refreshHome(){
  $('home-metrics').innerHTML='<p class="muted">Loading your workspace…</p>';
  const [summary,orders,quotes,stock,purchases]=await Promise.all([api('/api/management-summary'),api('/api/work-orders'),api('/api/quotations'),api('/api/stock-items'),api('/api/purchase-orders')]);
  const [people,attendance,plans,steps,maintenance]=await Promise.all([api('/api/employees'),api('/api/attendance?date='+today),api('/api/daily-plans?date='+today),api('/api/production-steps'),api('/api/maintenance-tasks')]);
  const activePeople=people.filter(e=>e.active),presentIds=new Set(attendance.map(a=>a.code));
  const present=activePeople.filter(e=>presentIds.has(e.code)).length;
  const duePlans=plans.filter(p=>p.status!=='Cancelled');
  const rings=[['Attendance today',present,activePeople.length,'#087bff','checked in'],['Tasks today',duePlans.filter(p=>['Completed','Closed'].includes(p.status)).length,duePlans.length,'#16b977','completed'],['Production operations',steps.filter(s=>['Completed','Closed'].includes(s.status)).length,steps.length,'#8d35eb','completed'],['Maintenance tasks',maintenance.filter(t=>['Completed','Closed'].includes(t.status)).length,maintenance.length,'#ff9418','completed']];
  $('home-progress-rings').innerHTML=rings.map(([title,done,total,color,word])=>{const percent=total?Math.round(done/total*100):0;return `<article class="section-card progress-ring-card"><h2>${title}</h2><div class="progress-ring-content">${progressRing(percent,total,color,title+': '+done+' of '+total+' '+word)}<div><small>${word}</small><strong>${done} / ${total}</strong></div></div></article>`;}).join('');
  const departments=new Map();activePeople.forEach(e=>departments.set(e.department||'Other',(departments.get(e.department||'Other')||0)+1));
  const palette=['#087bff','#16b977','#ff9418','#8d35eb','#f6c635','#9aaabd'];
  $('home-department-chart').innerHTML=donutChart([...departments].map(([label,value],i)=>({label,value,color:palette[i%palette.length]})),'employees');
  const activeOrders=orders.filter(j=>!['Completed','Closed','Cancelled'].includes(j.status));
  const followUps=quotes.filter(q=>!['Accepted','Lost'].includes(q.status)&&q.follow_up_date&&q.follow_up_date<=today);
  const shortages=stock.filter(s=>s.active&&Number(s.available??s.quantity)<=Number(s.reorder_level));
  const pendingQuotes=quotes.filter(q=>!['Accepted','Lost'].includes(q.status));
  const pendingPurchases=purchases.filter(p=>!['Received','Cancelled'].includes(p.status));
  const atWork=activePeople.filter(e=>attendance.some(a=>a.code===e.code&&!a.check_out)).length;
  const metrics=[['Total employees',activePeople.length,'Active team members','employees'],['On duty',atWork,'Open shifts today','overview'],['Checked in',present,'Attendance recorded today','overview'],['Not checked in',activePeople.length-present,'No check-in today','overview']];
  $('home-metrics').innerHTML=metrics.map(([label,value,note,view],i)=>`<button class="stat metric-card" data-home-view="${view}"><span class="metric-top">${label}<span class="metric-icon">${moduleIcon(view)}</span></span><strong>${escapeHTML(value)}</strong><small>${note} <span aria-hidden="true">↗</span></small></button>`).join('');
  const count=statuses=>orders.filter(j=>statuses.includes(j.status)).length;
  $('home-status-chart').innerHTML=donutChart([
    {label:'Open',value:count(['Open']),color:'#4388d1'},
    {label:'In progress',value:count(['In Progress']),color:'#e19638'},
    {label:'On hold',value:count(['On Hold']),color:'#9b77ba'},
    {label:'Completed / closed',value:count(['Completed','Closed']),color:'#4aa889'},
    {label:'Cancelled',value:count(['Cancelled']),color:'#a3afba'}
  ],'work orders');
  $('home-progress-rings').insertAdjacentHTML('beforeend',`<article class="section-card progress-ring-card order-ring-card"><h2>Order status</h2>${donutChart([{label:'Completed',value:count(['Completed','Closed']),color:'#16b977'},{label:'In progress',value:count(['In Progress']),color:'#087bff'},{label:'Other',value:orders.length-count(['Completed','Closed','In Progress']),color:'#ff9418'}],'orders')}</article>`);
  const statusChart=(items,label)=>donutChart([...new Set(items.map(i=>i.status))].map((status,i)=>({label:status,value:items.filter(x=>x.status===status).length,color:({Completed:'#16b977',Closed:'#16b977',Rework:'#e56b43','In Progress':'#087bff',Cancelled:'#9aaabd',Open:'#ff9418',Pending:'#ff9418'})[status]||palette[i%palette.length]})),label);
  $('home-task-chart').innerHTML=statusChart(plans,'tasks');
  $('home-maintenance-chart').innerHTML=statusChart(maintenance,'tasks');
  $('home-attendance-chart').innerHTML=donutChart([{label:'Checked in',value:present,color:'#16b977'},{label:'Not checked in',value:activePeople.length-present,color:'#ff9418'}],'employees');
  const days=Array.from({length:7},(_,i)=>{const d=new Date(today+'T12:00:00Z');d.setUTCDate(d.getUTCDate()-6+i);return d.toISOString().slice(0,10);});
  const monthRecords=(await Promise.all([...new Set(days.map(d=>d.slice(0,7)))].map(m=>api('/api/attendance?month='+m)))).flat();
  const activeCodes=new Set(activePeople.map(e=>e.code));
  $('home-week-chart').innerHTML='<div class="week-bars">'+days.map(day=>{const n=new Set(monthRecords.filter(r=>r.date===day&&activeCodes.has(r.code)).map(r=>r.code)).size;const pct=activePeople.length?Math.round(n/activePeople.length*100):0;return `<div class="week-column" aria-label="${day}: ${n} checked in, ${pct}%"><strong>${activePeople.length?pct+'%':'—'}</strong><div class="week-track"><svg viewBox="0 0 36 100" preserveAspectRatio="none" aria-hidden="true"><rect x="0" y="${100-pct}" width="36" height="${pct}" rx="2" fill="#258bfa"/></svg></div><small>${new Date(day+'T12:00:00Z').toLocaleDateString('en',{weekday:'short',timeZone:'UTC'})}</small></div>`;}).join('')+'</div>';
  $('home-team-table').innerHTML=activePeople.length?`<div class="table-wrap"><table><thead><tr><th>Employee</th><th>Department</th><th>Last 7 days</th><th>Today</th></tr></thead><tbody>${activePeople.slice(0,5).map(e=>{const n=new Set(monthRecords.filter(r=>r.code===e.code&&days.includes(r.date)).map(r=>r.date)).size;return `<tr><td><strong>${escapeHTML(e.name)}</strong></td><td>${escapeHTML(e.department||'—')}</td><td>${n} check-in days</td><td><span class="attendance-chip ${presentIds.has(e.code)?'present':''}">${presentIds.has(e.code)?'Checked in':'No check-in'}</span></td></tr>`;}).join('')}</tbody></table></div>`:'<p class="dashboard-empty">No active employees yet.</p>';
  $('home-recent').innerHTML=attendance.length?[...attendance].sort((a,b)=>b.check_in.localeCompare(a.check_in)).slice(0,4).map(r=>`<div class="recent-checkin"><span class="recent-icon">${moduleIcon('overview')}</span><div><strong>${escapeHTML(r.employee)}</strong><small>${escapeHTML(r.department||'Team member')} · Checked in</small></div><time>${new Date(r.check_in).toLocaleTimeString('en',{hour:'2-digit',minute:'2-digit',timeZone:zone})}</time></div>`).join(''):'<p class="dashboard-empty">No check-ins recorded today.</p>';
  $('home-shortcuts').innerHTML=[['employees','Employees'],['overview','Attendance'],['planning','Plan tasks'],['jobs','Work orders'],['customers','Customers'],['inventory','Inventory'],['analytics','Reports'],['maintenance','Maintenance']].map(([view,label])=>`<button data-home-view="${view}">${moduleIcon(view)}<span>${label}</span></button>`).join('');
  const attention=[['Overdue work orders',summary.overdue_jobs,'Review delivery dates and blockers','jobs'],['Quotation follow-ups',followUps.length,'Enquiries with a follow-up due today or earlier','quotations'],['Material shortages',shortages.length,'Review reservations and purchasing','inventory'],['Blocked operations',summary.blocked_steps,'Resolve delays on the production floor','production']];
  $('home-attention').innerHTML=attention.map(([label,count,note,view])=>`<button class="attention-row" data-home-view="${view}"><span class="attention-number ${count?'needs-action':''}">${escapeHTML(count)}</span><span><strong>${label}</strong><small>${note}</small></span><span class="row-arrow">↗</span></button>`).join('');
  const stages=[['01','Quotations',pendingQuotes.length,'quotations'],['02','Open work orders',summary.open_jobs,'jobs'],['03','Awaiting materials',pendingPurchases.length,'purchasing']];
  $('home-flow').innerHTML=stages.map(([n,label,count,view])=>`<button class="flow-row" data-home-view="${view}"><span class="flow-index">${n}</span><span>${label}${view==='purchasing'?'<small>Outstanding purchase orders</small>':''}</span><strong>${escapeHTML(count)}</strong></button>`).join('');
  const upcoming=activeOrders.sort((a,b)=>(a.target_date||'9999').localeCompare(b.target_date||'9999')).slice(0,5);
  $('home-jobs').innerHTML=upcoming.length?`<div class="table-wrap"><table><thead><tr><th>Work order</th><th>Customer</th><th>Target date</th><th>Status</th><th></th></tr></thead><tbody>${upcoming.map(j=>`<tr><td><strong>${escapeHTML(j.code)}</strong><small>${escapeHTML(j.title)}</small></td><td>${escapeHTML(j.customer||'—')}</td><td>${escapeHTML(j.target_date||'Not scheduled')}</td><td>${statusPill(j.status)}</td><td><button class="small-button" data-home-job="${j.id}">Open job →</button></td></tr>`).join('')}</tbody></table></div>`:'<div class="empty"><strong>Your next job starts here</strong>Create a work order or accept a quotation to begin.</div>';
}

const views = {
  reminders:['Deadline reminders','Track overdue work, upcoming dates and custom reminders.','◷'],
  home:['Employee Portal','People · Productivity · Progress','▦'],
  overview:['Attendance overview',"A clear view of your team's working day.",'▦'],
  ecosystem:['Operations library','Work records, resolved issues and team decisions.','◈'],
  customers:['Customer relationships','Company profiles, key contacts and service history.','⌂'],
  machines:['Customer equipment','Installed machines, service records and the people who maintain them.','⚙'],
  jobs:['Work orders','Responsibility, assignments and customer work in one place.','▰'],
  planning:['Work planner','A clear view of priorities, team workloads and upcoming assignments.','▦'],
  quotations:['Quotations','Enquiries, follow-ups and customer purchase orders.','▣'],
  'job-files':['Work order files','Drawings, materials, quality records and delivery documents.','▤'],
  inventory:['Materials & inventory','Stock availability, reservations and replenishment.','▥'],
  purchasing:['Procurement','Suppliers, purchase commitments and incoming materials.','◫'],
  production:['Production workflow','Plan each operation and follow its progress to dispatch.','▧'],
  maintenance:['Asset care','Equipment, vehicles and service schedules.','⚒'],
  analytics:['Business overview','Operational measures to support your daily decisions.','◉'],
  employees:['People & performance','Employee profiles, attendance and recorded contributions.','⊞'],
  work:['Work journal','Capture progress, completed work and support needed.','▣'],
  issues:['Issues & resolutions','Give each issue an owner and a clear path to resolution.','△'],
  meetings:['Meetings & actions','Decisions, owners, deadlines and follow-up.','☷'],
  reports:['Attendance reports','Monthly records for review and export.','▤'],
  checkin:['My attendance','Check in, get to work, and make today count.','◎']
};
async function navigate(view) {
  if (!views[view] || (user.admin ? view === 'checkin' : !['checkin','jobs','planning','work','issues','meetings'].includes(view))) throw new Error('This page is not available.');
  currentView = view;
  document.body.classList.toggle("dashboard-view",view==="home");
  closeNavigation();
  document.querySelectorAll('.panel-view').forEach(el => el.hidden = el.id !== view+'-panel');
  document.querySelectorAll('.nav-button').forEach(el => {el.classList.toggle('active',el.dataset.view===view);if(el.dataset.view===view)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current');});
  $('page-title').textContent=views[view][0]; $('page-subtitle').textContent=views[view][1]; $('breadcrumb').textContent=views[view][0];
  if(view === 'home') {await refreshHome();await refreshReminderBadge();}
  if(view === 'reminders') await refreshReminders();
  if(view === 'overview') await refreshOverview();
  if(view === 'ecosystem') await refreshEcosystem();
  if(view === 'employees') await refreshEmployees();
  if(view === 'customers') await refreshCustomers();
  if(view === 'machines') await refreshMachines();
  if(view === 'jobs') await refreshJobs();
  if(view === 'planning') await refreshPlanning();
  if(view === 'quotations') await refreshQuotations();
  if(view === 'job-files') await refreshJobFiles();
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
  await initialiseWorkflowUI();
  $('login-view').hidden = true; $('app-view').hidden = false;
  $('account-name').textContent=user.name; $('timezone-label').textContent=zone;
  $('account-avatar').textContent=user.name.split(/\s+/).map(x=>x[0]).slice(0,2).join('');
  $('today-label').textContent=new Intl.DateTimeFormat('en-IN',{day:'numeric',month:'short',year:'numeric',timeZone:zone}).format(new Date());
  $('day-filter').value=today; $('month-filter').value=today.slice(0,7); $('plan-date').value=today;
  const groups=user.admin?[
    ['Workspace',['home','reminders']],['Customer operations',['customers','quotations','machines']],
    ['Operations',['planning','jobs','job-files','production','inventory','purchasing','maintenance']],
    ['People & collaboration',['overview','employees','work','issues','meetings']],
    ['Insights',['analytics','reports','ecosystem']]
  ]:[['My workspace',['checkin','planning','jobs','work','issues','meetings']]];
  const names=groups.flatMap(g=>g[1]);
  document.querySelectorAll('.admin-employee-field').forEach(el=>el.hidden=!user.admin);
  $('add-meeting').hidden=!user.admin;
  $('add-plan').hidden=!user.admin;
  $('add-job').hidden=!user.admin;
  const shortNames={home:'Dashboard',overview:'Attendance',ecosystem:'Operations library',analytics:'Business overview',employees:'Employees',customers:'Customers',machines:'Customer equipment',inventory:'Inventory',maintenance:'Maintenance',planning:'Work planner'};
  $('navigation').innerHTML=groups.map(([title,list])=>`<section class="nav-group"><h2>${title}</h2>${list.map(view=>`<button class="nav-button" data-view="${view}"><span class="nav-icon" aria-hidden="true">${moduleIcon(view)}</span><span>${shortNames[view]||views[view][0]}</span></button>`).join('')}</section>`).join('');
  $('module-search').value='';$('nav-no-results').hidden=true;
  await navigate(names[0]);
  const linkedJob=new URLSearchParams(location.search).get('job');
  if(user.admin && /^\d+$/.test(linkedJob||'')){
    await navigate('job-files');$('job-file-select').value=linkedJob;await refreshJobFiles();
  }
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
function renderCommandResults(){
  const term=$('command-query').value.trim().toLowerCase();
  const permitted=[...document.querySelectorAll('#navigation [data-view]')].map(b=>b.dataset.view);
  const matches=permitted.filter(view=>(views[view][0]+' '+views[view][1]+' '+view).toLowerCase().includes(term));
  $('command-results').innerHTML=matches.length?matches.map(view=>`<button class="command-result" data-command-view="${view}"><span class="nav-icon" aria-hidden="true">${moduleIcon(view)}</span><span><strong>${escapeHTML(views[view][0])}</strong><small>${escapeHTML(views[view][1])}</small></span><span aria-hidden="true">↗</span></button>`).join(''):'<p class="help">No matching workspace. Try “planning”, “customer” or “inventory”.</p>';
}
function openCommand(){if(!user)return;$('command-query').value='';renderCommandResults();$('command-dialog').showModal();$('command-query').focus();}
$('open-command').addEventListener('click',openCommand);
$('command-query').addEventListener('input',renderCommandResults);
$('command-query').addEventListener('keydown',e=>{if(e.key==='Enter'){const first=$('command-results').querySelector('button');if(first){e.preventDefault();first.click();}}});
$('command-results').addEventListener('click',e=>{const button=e.target.closest('[data-command-view]');if(button){$('command-dialog').close();perform(()=>navigate(button.dataset.commandView));}});
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'&&user){e.preventDefault();if(!$('command-dialog').open&&!document.querySelector('dialog[open]'))openCommand();}});
$('navigation').addEventListener('click',e=>{const button=e.target.closest('[data-view]');if(button)perform(()=>navigate(button.dataset.view));});
$('open-navigation').addEventListener('click',()=>{document.body.classList.add('navigation-open');$('navigation-backdrop').hidden=false;$('open-navigation').setAttribute('aria-expanded','true');$('close-navigation').focus();});
$('close-navigation').addEventListener('click',()=>{closeNavigation();$('open-navigation').focus();});
$('navigation-backdrop').addEventListener('click',closeNavigation);
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeNavigation();});
$('module-search').addEventListener('input',e=>{const term=e.target.value.trim().toLowerCase();let matches=0;document.querySelectorAll('.nav-group').forEach(group=>{let found=0;group.querySelectorAll('.nav-button').forEach(button=>{button.hidden=!button.textContent.toLowerCase().includes(term);if(!button.hidden){found++;matches++;}});group.hidden=!found;});$('nav-no-results').hidden=matches>0;});
$('home-panel').addEventListener('click',e=>perform(async()=>{const view=e.target.closest('[data-home-view]'),action=e.target.closest('[data-home-action]'),job=e.target.closest('[data-home-job]');if(view)return navigate(view.dataset.homeView);if(action){if(action.dataset.homeAction==='quote'){await navigate('quotations');$('add-quotation').click();}else {await navigate('jobs');await openJobDialog();}}if(job){await navigate('job-files');$('job-file-select').value=job.dataset.homeJob;await refreshJobFiles();}}));
$('day-filter').addEventListener('change',()=>perform(refreshOverview));
$('month-filter').addEventListener('change',()=>perform(()=>navigate('reports')));
$('export').addEventListener('click',()=>perform(async()=>{
  const month=$('month-filter').value;if(!month)throw new Error('Choose a month first.');
  const response=await fetch('/api/export?month='+encodeURIComponent(month));
  if(!response.ok){const error=await response.json();throw new Error(error.error);}
  const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');link.href=url;link.download='cosmos-attendance-'+month+'.csv';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}));


$('add-customer').addEventListener('click',()=>perform(openCustomerDialog));
function updateStockNamePreview(){
  const form=$('stock-form');
  $('stock-name-preview').textContent=['category','sub_category','size_dimension','material_finish'].map(k=>form.elements[k].value.trim()||'…').join(' - ');
}
$('stock-form').addEventListener('input',updateStockNamePreview);
$('stock-form').addEventListener('change',updateStockNamePreview);
$('add-stock-item').addEventListener('click',()=>{$('stock-form').reset();$('stock-form').elements.sku.disabled=false;for(const k of ['sub_category','size_dimension','material_finish'])$('stock-form').elements[k].required=true;updateStockNamePreview();$('stock-dialog').showModal();});
$('stock-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{const d=Object.fromEntries(new FormData(e.currentTarget)),id=d.item_id;delete d.item_id;await api(id?'/api/stock-items/'+id:'/api/stock-items',id?'PATCH':'POST',d);$('stock-dialog').close();await refreshStock();notice('Stock item saved.');});});
$('stock-table').addEventListener('click',e=>perform(async()=>{const h=e.target.closest('[data-stock-history]'),m=e.target.closest('[data-stock-move]'),ed=e.target.closest('[data-stock-edit]');if(h)return showStockHistory(h.dataset.stockHistory);if(m){$('movement-form').reset();$('movement-form').elements.item_id.value=m.dataset.stockMove;$('movement-dialog').showModal();}if(ed){const x=stockItems.find(i=>i.id===Number(ed.dataset.stockEdit)),f=$('stock-form');f.reset();for(const k of ['sku','category','sub_category','size_dimension','material_finish','unit','specification','location','reorder_level'])f.elements[k].value=x[k]||'';for(const k of ['sub_category','size_dimension','material_finish'])f.elements[k].required=Boolean(x.sub_category);f.elements.item_id.value=x.id;f.elements.sku.disabled=true;updateStockNamePreview();$('stock-dialog').showModal();}}));
$('movement-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{const d=Object.fromEntries(new FormData(e.currentTarget)),id=d.item_id;delete d.item_id;await api('/api/stock-items/'+id+'/movements','POST',d);$('movement-dialog').close();await refreshStock();await showStockHistory(id);notice('Stock movement recorded.');});});
$('add-supplier').addEventListener('click',()=>{$('supplier-form').reset();$('supplier-dialog').showModal();});
$('supplier-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{await api('/api/suppliers','POST',Object.fromEntries(new FormData(e.currentTarget)));$('supplier-dialog').close();await refreshPurchasing();notice('Supplier saved.');});});
$('add-purchase-order').addEventListener('click',()=>perform(()=>openPurchaseOrder()));
$('purchase-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{const data=Object.fromEntries(new FormData(e.target)),id=data.record_id;delete data.record_id;await api(id?'/api/purchase-orders/'+id:'/api/purchase-orders',id?'PATCH':'POST',data);$('purchase-dialog').close();await refreshPurchasing();await refreshReminderBadge();notice('Purchase order saved.');});});
$('purchase-table').addEventListener('click',e=>perform(async()=>{const edit=e.target.closest('[data-po-edit]');if(edit)return openPurchaseOrder(edit.dataset.poEdit);const b=e.target.closest('[data-po-receive]');if(!b)return;const value=prompt('Quantity received (maximum '+b.dataset.outstanding+')',b.dataset.outstanding);if(value===null)return;await api('/api/purchase-orders/'+b.dataset.poReceive+'/receive','POST',{quantity:value});await refreshPurchasing();await refreshReminderBadge();notice('Receipt added to stock.');}));
$('purchase-table').addEventListener('change',e=>{const el=e.target.closest('[data-po-status]');if(!el||!el.value)return;perform(async()=>{const status=el.value;el.value='';let reason='';if(['Closed','Cancelled'].includes(status)){reason=prompt('Reason for '+status.toLowerCase()+' order (received stock is retained)');if(reason===null)return;}await api('/api/purchase-orders/'+el.dataset.poStatus,'PATCH',{status,reason});await refreshPurchasing();await refreshReminderBadge();notice('Purchase order status updated.');});});
$('employee-file-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{const d=Object.fromEntries(new FormData(e.currentTarget)),id=d.employee_id;delete d.employee_id;await api('/api/employee-files/'+id,'PATCH',d);$('employee-file-dialog').close();notice('Employee file saved.');});});
$('add-production-step').addEventListener('click',()=>perform(async()=>{const orders=await api('/api/work-orders');if(!orders.length)throw new Error('Create a work order first.');const f=$('production-form');f.reset();f.elements.work_order_id.innerHTML=orders.map(x=>`<option value="${x.id}">${escapeHTML(x.code)} · ${escapeHTML(x.title)}</option>`).join('');$('production-dialog').showModal();}));
$('production-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{await api('/api/production-steps','POST',Object.fromEntries(new FormData(e.currentTarget)));$('production-dialog').close();await refreshProduction();notice('Operation added.');});});
$('production-table').addEventListener('click',e=>{const b=e.target.closest('[data-step-update]');if(!b)return;const row=productionRows.find(x=>x.id===Number(b.dataset.stepUpdate)),f=$('production-progress-form');f.reset();f.elements.step_id.value=row.id;for(const k of ['status','actual_hours','accepted_qty','rejected_qty','delay_reason'])f.elements[k].value=row[k]??'';$('production-progress-dialog').showModal();});
$('production-progress-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{const data=Object.fromEntries(new FormData(e.target)),id=data.step_id;delete data.step_id;await api('/api/production-steps/'+id,'PATCH',data);$('production-progress-dialog').close();await refreshProduction();await refreshReminderBadge();notice('Production operation updated.');});});
$('add-asset').addEventListener('click',()=>{$('asset-form').reset();$('asset-dialog').showModal();});
$('asset-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{const d=Object.fromEntries(new FormData(e.currentTarget)),id=d.record_id;delete d.record_id;await api(id?'/api/company-assets/'+id:'/api/company-assets',id?'PATCH':'POST',d);$('asset-dialog').close();await refreshMaintenance();notice('Asset saved.');});});
$('add-maintenance-task').addEventListener('click',()=>perform(()=>openMaintenanceTask()));
$('maintenance-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{renderMaintenanceChecklist();const d=operationForm(e.target),id=d.record_id;delete d.record_id;d.checklist=maintenanceChecklist;await api(id?'/api/maintenance-tasks/'+id:'/api/maintenance-tasks',id?'PATCH':'POST',d);$('maintenance-dialog').close();await refreshMaintenance();await refreshReminderBadge();notice('Maintenance task saved.');});});
$('maintenance-table').addEventListener('click',e=>{const b=e.target.closest('[data-task-edit]');if(b)perform(()=>openMaintenanceTask(b.dataset.taskEdit));});
$('refresh-analytics').addEventListener('click',()=>perform(refreshAnalytics));
$('add-quotation').addEventListener('click',()=>perform(async()=>{await ensureCustomers();if(!customers.length)throw new Error('Add a customer first.');const f=$('quotation-form');f.reset();f.elements.customer_id.innerHTML=customerOptions();$('quotation-dialog').showModal();}));
$('quotation-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{await api('/api/quotations','POST',Object.fromEntries(new FormData(e.currentTarget)));$('quotation-dialog').close();await refreshQuotations();notice('Quotation created.');});});
$('quotation-table').addEventListener('click',e=>perform(async()=>{const accept=e.target.closest('[data-quote-accept]'),status=e.target.closest('[data-quote-status]');if(status){const next=prompt('Status: Draft, Sent, Negotiation, Lost','Sent');if(next===null)return;if(!['Draft','Sent','Negotiation','Lost'].includes(next))throw new Error('Invalid quote status.');await api('/api/quotations/'+status.dataset.quoteStatus,'PATCH',{status:next});await refreshQuotations();return;}if(accept){const customer_po=prompt('Enter customer purchase order number');if(customer_po===null)return;if(!customer_po.trim())throw new Error('Customer PO is required.');await api('/api/quotations/'+accept.dataset.quoteAccept+'/accept','POST',{customer_po});await refreshQuotations();notice('Quotation accepted; work order created.');}}));
$('job-file-select').addEventListener('change',()=>perform(refreshJobFiles));
$('add-job-material').addEventListener('click',()=>perform(async()=>{if(!$('job-file-select').value)throw new Error('Create a work order first.');const items=await api('/api/stock-items');const f=$('job-material-form');f.reset();f.elements.item_id.innerHTML=items.filter(x=>x.active).map(x=>`<option value="${x.id}">${escapeHTML(x.sku)} · ${escapeHTML(x.name)}</option>`).join('');if(!f.elements.item_id.value)throw new Error('Add a stock item first.');$('job-material-dialog').showModal();}));
$('job-material-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{await api('/api/work-orders/'+$('job-file-select').value+'/materials','POST',Object.fromEntries(new FormData(e.currentTarget)));$('job-material-dialog').close();await refreshJobFiles();notice('Material requirement saved.');});});
$('job-material-table').addEventListener('click',e=>perform(async()=>{const b=e.target.closest('[data-material-action]');if(!b)return;const quantity=prompt('Quantity to '+b.dataset.materialAction);if(quantity===null)return;await api('/api/job-materials/'+b.dataset.requirement+'/'+b.dataset.materialAction,'POST',{quantity});await refreshJobFiles();notice('Material '+b.dataset.materialAction+' recorded.');}));
$('add-job-drawing').addEventListener('click',()=>{if(!$('job-file-select').value)return notice('Create a work order first.',true);$('drawing-form').reset();$('drawing-dialog').showModal();});
$('drawing-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{await api('/api/work-orders/'+$('job-file-select').value+'/drawings','POST',Object.fromEntries(new FormData(e.currentTarget)));$('drawing-dialog').close();await refreshJobFiles();notice('Drawing revision added.');});});
$('job-drawing-table').addEventListener('click',e=>perform(async()=>{const b=e.target.closest('[data-drawing-approve]');if(!b)return;if(!confirm('Approve this revision for production?'))return;await api('/api/drawings/'+b.dataset.drawingApprove+'/approve','POST',{});await refreshJobFiles();notice('Drawing revision approved.');}));
$('print-job-card').addEventListener('click',()=>{const id=$('job-file-select').value;if(!id)return notice('Select a work order.',true);window.open('/api/work-orders/'+id+'/job-card','_blank','noopener');});
$('upload-job-document').addEventListener('click',()=>{const id=$('job-file-select').value;if(!id)return notice('Select a work order.',true);openDocumentDialog('job',id);});
$('upload-employee-document').addEventListener('click',()=>openDocumentDialog('employee',$('employee-file-form').elements.employee_id.value));
function openDocumentDialog(type,id){const f=$('document-form');f.reset();f.elements.owner_type.value=type;f.elements.owner_id.value=id;$('document-dialog').showModal();}
$('document-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{const f=e.currentTarget,type=f.elements.owner_type.value,id=f.elements.owner_id.value,body=new FormData(f);body.delete('owner_type');body.delete('owner_id');const response=await fetch('/api/documents/'+type+'/'+id,{method:'POST',credentials:'same-origin',headers:{'X-CSRF-Token':csrf},body});const result=await response.json();if(!response.ok)throw new Error(result.error||'Upload failed.');$('document-dialog').close();if(type==='employee')await loadEmployeeDocuments(id);else await refreshJobFiles();notice('Document uploaded.');});});
$('add-quality').addEventListener('click',()=>{if(!$('job-file-select').value)return notice('Select a work order.',true);$('quality-form').reset();$('quality-dialog').showModal();});
$('quality-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{await api('/api/work-orders/'+$('job-file-select').value+'/quality','POST',Object.fromEntries(new FormData(e.currentTarget)));$('quality-dialog').close();await refreshJobFiles();notice('Inspection recorded.');});});
$('add-dispatch').addEventListener('click',()=>{if(!$('job-file-select').value)return notice('Select a work order.',true);$('dispatch-form').reset();$('dispatch-form').elements.dispatch_date.value=today;$('dispatch-dialog').showModal();});
$('dispatch-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{await api('/api/work-orders/'+$('job-file-select').value+'/dispatch','POST',Object.fromEntries(new FormData(e.currentTarget)));$('dispatch-dialog').close();await refreshJobFiles();notice('Dispatch recorded.');});});
$('add-machine').addEventListener('click',()=>perform(()=>openMachineDialog()));
$('add-job').addEventListener('click',()=>perform(()=>openJobDialog()));
$('customer-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{const d=Object.fromEntries(new FormData(e.currentTarget)),id=d.customer_id;delete d.customer_id;await api(id?'/api/customers/'+id:'/api/customers',id?'PATCH':'POST',d);$('customer-dialog').close();await refreshCustomers();if(id)await showCustomerDetail(id);notice('Customer saved.');});});
$('contact-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{const d=Object.fromEntries(new FormData(e.currentTarget)),id=d.customer_id,contactId=d.contact_id;delete d.customer_id;delete d.contact_id;d.primary_contact=e.currentTarget.elements.primary_contact.checked;await api('/api/customers/'+id+'/contacts'+(contactId?'/'+contactId:''),contactId?'PATCH':'POST',d);$('contact-dialog').close();await showCustomerDetail(id);notice('Contact details saved.');});});
$('machine-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{const data=Object.fromEntries(new FormData(e.target)),id=data.machine_id;delete data.machine_id;await api(id?'/api/machines/'+id:'/api/machines',id?'PATCH':'POST',data);$('machine-dialog').close();machines=[];await refreshMachines();if(id)await showMachineHistory(id);await refreshReminderBadge();notice('Machine saved.');});});
$('job-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{const d=operationForm(e.target);d.assigned_employee_ids=d.assigned_employee_id?[Number(d.assigned_employee_id)]:[];delete d.assigned_employee_id;await api('/api/work-orders','POST',d);$('job-dialog').close();jobs=[];await refreshJobs();await refreshReminderBadge();notice('Work order created.');});});
$('plan-today').addEventListener('click',()=>{$('plan-date').value=today;perform(refreshPlanning);});
$('plan-tomorrow').addEventListener('click',()=>{const d=new Date(today+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+1);$('plan-date').value=d.toISOString().slice(0,10);perform(refreshPlanning);});
$('plan-form').addEventListener('input',()=>{$('plan-assignment-preview').textContent='Plan details changed. Preview again to check the latest workload.';$('plan-assignment-preview').className='assignment-preview';});
$('preview-assignment').addEventListener('click',()=>perform(async()=>{
  const form=$('plan-form'),button=$('preview-assignment'),data=Object.fromEntries(new FormData(form));
  for(const name of ['date','estimated_hours','daily_capacity'])if(!form.elements[name].reportValidity())return;
  button.disabled=true;
  try{
    const query=new URLSearchParams({date:data.date,department:data.department,estimated_hours:data.estimated_hours,daily_capacity:data.daily_capacity});
    const result=await api('/api/planning/workload?'+query.toString());
    const employee=result.employees.find(e=>e.id===Number(data.employee_id||result.recommended_employee_id));
    const target=$('plan-assignment-preview');target.className='assignment-preview'+(!employee||!employee.fits?' warning':'');
    target.innerHTML=employee?`<strong>${data.employee_id?'Selected employee':'Suggested assignment'} · ${escapeHTML(employee.name)}</strong>${employee.planned_hours.toFixed(1)} h already planned + ${result.estimated_hours.toFixed(1)} h for this task = ${(employee.planned_hours+result.estimated_hours).toFixed(1)} h.<small>${employee.fits?'Within the '+result.daily_capacity+'-hour planning limit.':'Above the planning limit. A manual assignment will override it.'}</small>`:'<strong>No employee fits this plan yet</strong>Try another date, reduce the task duration, or review the daily planning limit.';
  }finally{button.disabled=false;}
}));
$('plan-date').addEventListener('change',()=>perform(refreshPlanning));
$('add-plan').addEventListener('click',()=>perform(openPlanDialog));
$('plan-department').addEventListener('change',()=>fillPlanEmployees());
$('plan-form').addEventListener('submit',e=>{e.preventDefault();const form=e.target,button=form.querySelector('button[type="submit"]');if(button.disabled)return;const data=operationForm(form),id=data.plan_id;delete data.plan_id;button.disabled=true;perform(async()=>{try{
  if(id&&!data.employee_id)throw new Error('Choose an employee when editing this plan.');
  const row=await api(id?'/api/daily-plans/'+id:'/api/daily-plans',id?'PATCH':'POST',data);
  $('plan-dialog').close();$('plan-date').value=row.date;await refreshPlanning();await refreshReminderBadge();notice('Work plan saved for '+row.employee+'.');
}finally{button.disabled=false;}});});
$('plan-table').addEventListener('change',e=>{const input=e.target.closest('[data-plan-status]');if(!input||!input.value)return;perform(async()=>{await api('/api/daily-plans/'+input.dataset.planStatus,'PATCH',{status:input.value});await refreshPlanning();notice('Task progress updated.');});});
$('job-customer').addEventListener('change',e=>perform(()=>loadJobMachines(e.target.value)));
$('customer-table').addEventListener('click',e=>{const b=e.target.closest('[data-customer-open]');if(b)perform(()=>showCustomerDetail(b.dataset.customerOpen));});
$('customer-detail').addEventListener('click',e=>perform(async()=>{
  const del=e.target.closest('[data-delete-customer]');if(del)return openDeleteReview('customers',del.dataset.deleteCustomer);
  const c=e.target.closest('[data-add-contact]'),m=e.target.closest('[data-add-machine-customer]'),j=e.target.closest('[data-add-job-customer]'),mh=e.target.closest('[data-machine-open]'),ec=e.target.closest('[data-edit-customer]'),ac=e.target.closest('[data-archive-customer]'),ct=e.target.closest('[data-edit-contact]');
  if(ec){const row=await api('/api/customers/'+ec.dataset.editCustomer);const form=$('customer-form');form.reset();for(const key of ['name','gstin','industry','phone','email','address','notes'])form.elements[key].value=row[key]||'';form.elements.customer_id.value=row.id;$('customer-dialog-title').textContent='Edit customer';$('customer-dialog').showModal();return;}
  if(ac){const id=ac.dataset.archiveCustomer,row=await api('/api/customers/'+id),status=row.status==='Archived'?'Active':'Archived';if(status==='Archived'&&!confirm('Archive this customer? Their history will be kept.'))return;await api('/api/customers/'+id,'PATCH',{status});await refreshCustomers();await showCustomerDetail(id);notice('Customer '+status.toLowerCase()+'.');return;}
  if(ct){const id=ct.dataset.customerId,row=await api('/api/customers/'+id),contact=row.contacts.find(x=>x.id===Number(ct.dataset.editContact));if(!contact)throw new Error('Contact not found.');const form=$('contact-form');form.reset();for(const key of ['name','designation','department','phone','email','notes'])form.elements[key].value=contact[key]||'';form.elements.primary_contact.checked=contact.primary_contact;form.elements.customer_id.value=id;form.elements.contact_id.value=contact.id;$('contact-dialog-title').textContent='Edit contact';$('contact-dialog').showModal();return;}
  if(c){$('contact-form').reset();$('contact-customer-id').value=c.dataset.addContact;$('contact-dialog-title').textContent='Add customer contact';$('contact-dialog').showModal();return;}
  if(m){await openMachineDialog(m.dataset.addMachineCustomer);return;}
  if(j){await openJobDialog(j.dataset.addJobCustomer);return;}
  if(mh){await navigate('machines');await showMachineHistory(mh.dataset.machineOpen);}
}));
$('machine-table').addEventListener('click',e=>{const company=e.target.closest('[data-machine-company]'),machine=e.target.closest('[data-machine-open]'),back=e.target.closest('[data-machine-back]');if(company)perform(()=>openMachineCompany(company.dataset.machineCompany));else if(machine)perform(()=>showMachineHistory(machine.dataset.machineOpen));else if(back)perform(refreshMachines);});
$('job-table').addEventListener('click',e=>{const b=e.target.closest('[data-job-status]');if(b)perform(()=>openProjectProgress(b.dataset.jobStatus));});
let networkTimer;
$('network-search').addEventListener('input',()=>{clearTimeout(networkTimer);networkTimer=setTimeout(()=>perform(runNetworkSearch),250);});
$('network-results').addEventListener('click',e=>perform(async()=>{const b=e.target.closest('[data-search-type]');if(!b)return;const type=b.dataset.searchType,id=b.dataset.searchId;if(type==='Customer'){await showCustomerDetail(id);}else if(type==='Machine'){await navigate('machines');await showMachineHistory(id);}else if(type==='Work Order'){await navigate('jobs');}else if(type==='Employee'){await navigate('employees');}}));

$('add-work').addEventListener('click',()=>perform(openWorkDialog));
$('add-issue').addEventListener('click',()=>perform(openIssueDialog));
$('add-meeting').addEventListener('click',()=>perform(openMeetingDialog));
document.querySelectorAll('[data-jump]').forEach(b=>b.addEventListener('click',()=>perform(()=>navigate(b.dataset.jump))));
$('work-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{
  const data=operationForm(e.currentTarget);if(!user.admin)delete data.employee_id;
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
  const id=Number(button.dataset.toggle||button.dataset.edit||button.dataset.pin||button.dataset.delete||button.dataset.file||button.dataset.profile),employee=team.find(p=>p.id===id);if(!employee)return;
  if(button.dataset.profile){await showEmployeeProfile(id);return;}
  if(button.dataset.file){const data=await api('/api/employee-files/'+id),f=$('employee-file-form');f.reset();f.elements.employee_id.value=id;for(const k of ['designation','joining_date','skills','notes'])f.elements[k].value=data.profile[k]||'';$('employee-file-title').textContent=employee.name+' · '+employee.code;$('employee-recent-work').innerHTML='<h3>Recent work</h3>'+(data.recent_work.map(x=>`<p>${escapeHTML(x.date)} · ${escapeHTML(x.job_no||'General')} · ${escapeHTML(x.status)}<br>${escapeHTML(x.details)}</p>`).join('')||'<p>No work reports yet.</p>');await loadEmployeeDocuments(id);$('employee-file-dialog').showModal();return;}
  if(button.dataset.edit){const f=$('employee-form');f.reset();$('employee-id').value=employee.id;f.elements.name.value=employee.name;f.elements.code.value=employee.code.toUpperCase();f.elements.department.value=employee.department;$('employee-dialog-title').textContent='Edit employee';$('employee-dialog-note').textContent='Change employee identity or department.';$('pin-label').hidden=true;$('pin-help').hidden=true;f.elements.pin.required=false;$('employee-save').textContent='Save changes';$('employee-dialog').showModal();return;}
  if(button.dataset.pin){const pin=prompt(`Enter a new 4-digit PIN for ${employee.name}:`);if(pin===null)return;if(!/^\d{4}$/.test(pin))throw new Error('PIN must be exactly 4 digits.');await api('/api/employees/'+employee.id+'/reset-pin','POST',{pin});notice('Employee PIN reset.');return;}
  if(button.dataset.toggle){if(!confirm(`${employee.active?'Deactivate':'Activate'} ${employee.name}'s account?`))return;await api('/api/employees/'+employee.id+'/active','POST',{active:!employee.active});await refreshEmployees();notice('Employee access updated.');return;}
  if(button.dataset.delete)return openDeleteReview('employees',employee.id);
}));
$('employee-profile').addEventListener('change',e=>{if(e.target.id==='employee-profile-month'&&selectedEmployeeProfileId)perform(()=>showEmployeeProfile(selectedEmployeeProfileId,e.target.value));});
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
function setAttendanceBusy(busy, label) {
  attendanceBusy=busy;
  $('start-attendance').disabled=busy||attendanceComplete;
  $('test-location').disabled=busy;
  $('start-attendance').textContent=label||(busy?'Please wait…':attendanceComplete?'Attendance complete':openShift?'Check out':'Check in');
}
function locationStatus(message, error=false) {
  $('attendance-location-status').textContent=message;
  $('attendance-location-status').classList.toggle('location-error',error);
  if(error)$('attendance-location-help').open=true;
}
$('start-attendance').addEventListener('click',()=>{
  if(attendanceBusy||$('start-attendance').disabled)return;
  const employeeId=user?.id;
  setAttendanceBusy(true,'Checking attendance…');
  perform(async()=>{
    let submitted=false,saved=false;
    try {
      await refreshMine();
      if(attendanceComplete)return;
      const action=openShift?'out':'in';
      setAttendanceBusy(true,'Getting location…');
      const location=await getLocation(message=>locationStatus(message));
      if(user?.id!==employeeId)throw new Error('The signed-in account changed. Sign in with your employee ID and try again.');
      setAttendanceBusy(true,'Saving attendance…');
      locationStatus('Location received. Saving your '+(action==='in'?'check-in':'check-out')+'…');
      submitted=true;
      await api('/api/attendance','POST',{location,action});
      saved=true;
      await refreshMine();
      const message=(action==='in'?'Check-in':'Check-out')+' saved. Location accuracy: ±'+Math.round(location.accuracy)+' m.';
      locationStatus(message);notice(message);
    } catch(error) {
      const prefix=saved?'Attendance was saved. Reload to update this page. ':submitted?'Could not confirm attendance. Refresh your attendance to check before retrying. ':'Attendance was not submitted. ';
      locationStatus(prefix+error.message,true);notice(error.message,true);
    } finally {setAttendanceBusy(false);}
  });
});
$('test-location').addEventListener('click',()=>{
  if(attendanceBusy)return;
  setAttendanceBusy(true);
  perform(async()=>{
    try {
      const location=await getLocation(message=>locationStatus(message));
      locationStatus('Location is available (±'+Math.round(location.accuracy)+' m). No attendance was recorded.');
    } catch(error) {locationStatus(error.message,true);notice(error.message,true);}
    finally {setAttendanceBusy(false);}
  });
});
function getLocation(onProgress){return CosmosLocation.getLocation(onProgress);}
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
let workflowOptions=null,planRows=[],purchaseOrders=[],productionRows=[],deleteReview=null,customerUpdateContext=null,savedCustomerDraft=null,alertRefreshTimer=null,maintenanceChecklist=[];
const terminalStates=['Completed','Closed','Cancelled'];
const optionsHTML=(values,selected='')=>values.map(v=>`<option value="${escapeHTML(v)}" ${v===selected?'selected':''}>${escapeHTML(v)}</option>`).join('');
function operationForm(form){const fd=new FormData(form),data=Object.fromEntries(fd);if(form.querySelector('[data-service-activities]'))data.service_activities=fd.getAll('service_activities');return data;}
function setServiceForm(form,data={}){
  const select=form.querySelector('[data-service-type]'),value=data.service_type||data.task_type||'';
  if(select){if(value&&![...select.options].some(o=>o.value===value))select.add(new Option(value,value));select.value=value;}
  form.querySelectorAll('[name="service_activities"]').forEach(el=>el.checked=(data.service_activities||[]).includes(el.value));
}
function serviceSummary(record){return [record.service_type,...(record.service_activities||[])].filter(Boolean).map(escapeHTML).join(' · ');}
function deadlineLabel(date,status){return date&&!terminalStates.includes(status)&&date<today?`<span class="pill warning">Overdue · ${escapeHTML(date)}</span>`:escapeHTML(date||'—');}
async function initialiseWorkflowUI(){
  workflowOptions=workflowOptions||await api('/api/workflow-options');
  document.querySelectorAll('[data-service-type]').forEach(el=>el.innerHTML=(el.hasAttribute('data-required-service')?'':'<option value="">Not a service task / not specified</option>')+optionsHTML(workflowOptions.service_types));
  document.querySelectorAll('[data-service-activities]').forEach(el=>el.innerHTML='<legend>Service activity / work carried out</legend>'+workflowOptions.service_activities.map(v=>`<label class="checkbox-line"><input type="checkbox" name="service_activities" value="${escapeHTML(v)}">${escapeHTML(v)}</label>`).join(''));
  document.querySelectorAll('[data-work-status]').forEach(el=>el.innerHTML=optionsHTML(workflowOptions.work_statuses));
  document.querySelectorAll('[data-plan-status]').forEach(el=>el.innerHTML=optionsHTML(workflowOptions.plan_statuses,'Planned'));
  document.querySelectorAll('[data-report-status]').forEach(el=>el.innerHTML=optionsHTML([...workflowOptions.plan_statuses,'Pending'],'Completed'));
  document.querySelectorAll('[data-project-stage]').forEach(el=>el.innerHTML='<option value="">Not specified</option>'+optionsHTML(workflowOptions.project_stages));
  $('open-alerts').hidden=!user.admin;
  clearInterval(alertRefreshTimer);
  if(user.admin)alertRefreshTimer=setInterval(()=>{if(user?.admin&&!document.hidden)perform(currentView==='reminders'?refreshReminders:refreshReminderBadge);},60000);
}
async function openDeleteReview(type,id){
  const path='/api/'+type+'/'+id,preview=await api(path+'/delete-preview');deleteReview={path,type,preview};
  $('delete-record-form').reset();$('delete-record-name').textContent=preview.name;$('delete-record-notes').textContent=preview.notes;
  $('delete-record-phrase').textContent=preview.confirmation;
  $('delete-record-counts').innerHTML=Object.entries(preview.counts).filter(([,count])=>count).map(([label,count])=>`<div><span>${escapeHTML(label)}</span><strong>${count}</strong></div>`).join('');
  $('delete-record-dialog').showModal();
}
$('delete-record-form').addEventListener('submit',e=>{e.preventDefault();const button=e.target.querySelector('button[type="submit"]');if(button.disabled)return;perform(async()=>{
  if($('delete-record-confirm').value!==deleteReview.preview.confirmation)throw new Error('Type the exact deletion phrase to confirm.');button.disabled=true;
  try{await api(deleteReview.path,'DELETE',{confirmation:$('delete-record-confirm').value,preview_token:deleteReview.preview.preview_token});$('delete-record-dialog').close();
    if(deleteReview.type==='customers'){customers=[];machines=[];jobs=[];$('customer-detail').innerHTML='';$('machine-history').innerHTML='';await refreshCustomers();}
    if(deleteReview.type==='employees'){team=[];selectedEmployeeProfileId=null;$('employee-profile').innerHTML='';await refreshEmployees();}
    if(deleteReview.type==='daily-plans')await refreshPlanning();await refreshReminderBadge();notice('Record permanently deleted.');
  }finally{button.disabled=false;}
});});
async function syncPlanCompany(selectedMachine='',selectedOrder=''){
  const cid=$('plan-customer').value,company=customers.find(c=>String(c.id)===cid);
  const matching=jobs.filter(j=>!cid||String(j.customer_id)===cid);
  $('plan-work-order').innerHTML='<option value="">No work order</option>'+matching.map(j=>`<option value="${j.id}">${escapeHTML(j.code)} · ${escapeHTML(j.title)}</option>`).join('');
  $('plan-work-order').value=String(selectedOrder||'');
  const rows=cid?await api('/api/machines?customer_id='+encodeURIComponent(cid)):[];
  $('plan-machine').innerHTML='<option value="">No specific machine</option>'+rows.map(m=>`<option value="${m.id}">${escapeHTML(m.customer_machine_no||m.model||m.code)} · ${escapeHTML(m.machine_type||'Machine')}</option>`).join('');
  $('plan-machine').value=String(selectedMachine||'');
  $('plan-company-details').hidden=!company;
  $('plan-company-details').innerHTML=company?`<strong>${escapeHTML(company.name)}</strong><span>${escapeHTML(company.address||'Address not recorded')}</span><span>${escapeHTML([company.phone,company.email].filter(Boolean).join(' · '))}</span>`:'';
}
$('plan-customer').addEventListener('change',()=>perform(()=>syncPlanCompany()));
$('plan-work-order').addEventListener('change',()=>perform(async()=>{const job=jobs.find(j=>String(j.id)===$('plan-work-order').value);if(!job)return;$('plan-customer').value=job.customer_id;await syncPlanCompany(job.machine_id,job.id);setServiceForm($('plan-form'),job);if(job.target_date)$('plan-form').elements.due_date.value=job.target_date;}));
async function editPlan(id){
  const row=planRows.find(r=>r.id===Number(id));if(!row)return;await openPlanDialog();
  const form=$('plan-form');$('plan-dialog-title').textContent='Edit work plan';
  for(const key of ['title','details','date','due_date','estimated_hours','priority'])form.elements[key].value=row[key]??'';
  form.elements.plan_id.value=row.id;$('plan-customer').value=String(row.customer_id||'');await syncPlanCompany(row.machine_id,row.work_order_id);
  $('plan-department').value=row.department||'';fillPlanEmployees(row.employee_id);setServiceForm(form,row);
  $('plan-capacity').closest('label').hidden=true;$('preview-assignment').hidden=true;
  $('plan-assignment-preview').textContent='This is an existing assignment. Review the workload for its work date before saving.';
}
function fillPlanEmployees(selected=''){
  const dept=$('plan-department').value;
  $('plan-employee').innerHTML='<option value="">Automatic · balance planned hours</option>'+team.filter(e=>e.active&&(!dept||e.department===dept)).map(e=>`<option value="${e.id}">${escapeHTML(e.name)} · ${escapeHTML(e.department)}</option>`).join('');
  $('plan-employee').value=String(selected||'');
  $('plan-employee').options[0].disabled=Boolean($('plan-form').elements.plan_id.value);
}
$('plan-table').addEventListener('click',e=>perform(async()=>{const edit=e.target.closest('[data-plan-edit]'),del=e.target.closest('[data-plan-delete]');if(edit)return editPlan(edit.dataset.planEdit);if(del)return openDeleteReview('daily-plans',del.dataset.planDelete);}));
async function editMachine(id){
  const record=await api('/api/machines/'+id+'/history'),m=record.machine;await openMachineDialog(m.customer_id);const f=$('machine-form');
  $('machine-dialog-title').textContent='Edit machine';f.elements.machine_id.value=m.id;$('machine-customer').disabled=true;
  for(const k of ['machine_type','customer_machine_no','manufacturer','model','serial_no','controller','department','location','installation_date','notes','specifications','last_service_date','next_service_date','status'])f.elements[k].value=m[k]??'';
}
document.addEventListener('click',e=>{const edit=e.target.closest('[data-machine-edit]'),update=e.target.closest('[data-customer-update]');if(edit)perform(()=>editMachine(edit.dataset.machineEdit));if(update)perform(()=>openCustomerUpdate(update.dataset.customerUpdate));});
$('work-order-link').addEventListener('change',()=>{const job=jobs.find(j=>String(j.id)===$('work-order-link').value);if(!job)return;setServiceForm($('work-form'),job);$('work-form').elements.job_no.value=job.code;$('work-form').elements.customer.value=job.customer||'';$('work-machine-link').value=String(job.machine_id||'');});
async function openProjectProgress(id){
  jobs=await api('/api/work-orders');const j=jobs.find(x=>x.id===Number(id));if(!j)throw new Error('Work order not found.');const f=$('job-progress-form');f.reset();f.elements.job_id.value=j.id;
  for(const k of ['title','description','service_product','status','current_stage','target_date','customer_remarks'])f.elements[k].value=j[k]??'';
  setServiceForm(f,j);$('job-progress-context').textContent=j.code+' · '+j.customer+' · '+(j.machine_no||j.machine_model||'No specific machine');$('job-progress-dialog').showModal();
}
$('job-progress-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{const data=operationForm(e.target),id=data.job_id;delete data.job_id;await api('/api/work-orders/'+id,'PATCH',data);$('job-progress-dialog').close();await refreshJobs();await refreshReminderBadge();notice('Project progress saved.');});});
function regenerateCustomerMessage(){
  const c=customerUpdateContext,j=c.job,m=c.machine,stage=$('customer-update-stage').value||'Not specified',date=$('customer-update-date').value,remarks=$('customer-update-remarks').value.trim();
  const sections=['Dear '+c.customer.name+' team,','Here is the latest update from Cosmos Engineering Solutions.','Project: '+j.code+' — '+j.title,'Product / assembly: '+(j.service_product||'Not specified'),'Machine: '+(m?[m.customer_machine_no,m.machine_type,m.manufacturer,m.model].filter(Boolean).join(' · '):'Not linked to a specific machine'),'Project details: '+(j.description||'Not specified'),'Status: '+j.status,'Current stage: '+stage];
  if(j.service_type)sections.push('Service type: '+j.service_type);
  if(j.service_activities?.length)sections.push('Service activities: '+j.service_activities.join(', '));
  if(c.steps.length)sections.push('Production progress:\n'+c.steps.map(s=>'• '+s.operation+': '+s.status).join('\n'));
  sections.push('Estimated delivery: '+(date||'To be confirmed'),'Remarks: '+(remarks||'No additional remarks.'),'We will keep you informed of any changes.','Regards,\nCosmos Engineering Solutions\nInnovate. Engineer. Excel.');
  $('customer-update-form').elements.subject.value='Project update · '+j.code+' · '+j.title;
  $('customer-update-form').elements.message.value=sections.join('\n\n');invalidateCustomerDraft();
}
function invalidateCustomerDraft(){savedCustomerDraft=null;$('customer-update-send').hidden=true;}
async function openCustomerUpdate(id){
  customerUpdateContext=await api('/api/work-orders/'+id+'/customer-updates');const c=customerUpdateContext,f=$('customer-update-form');f.reset();invalidateCustomerDraft();
  $('customer-update-context').textContent=c.job.code+' · '+c.customer.name;
  $('customer-update-contact').innerHTML='<option value="company">Company contact</option>'+c.contacts.map((x,i)=>`<option value="${i}">${escapeHTML(x.name)}</option>`).join('');
  f.elements.recipient_email.value=c.customer.email||'';f.elements.recipient_phone.value=c.customer.phone||'';
  $('customer-update-date').value=c.job.target_date||'';$('customer-update-stage').value=c.job.current_stage||'';$('customer-update-remarks').value=c.job.customer_remarks||'';
  regenerateCustomerMessage();renderCustomerUpdateHistory(c.updates);$('customer-update-dialog').showModal();
}
function renderCustomerUpdateHistory(rows){$('customer-update-history').innerHTML='<h3>Update history</h3>'+(rows.length?rows.map(r=>`<details class="update-history-row"><summary>${escapeHTML(new Date(r.created_at).toLocaleString('en-IN',{timeZone:zone}))} · ${escapeHTML(r.status)} ${escapeHTML(r.channel||'')}</summary><p>${escapeHTML([r.recipient_email,r.recipient_phone].filter(Boolean).join(' · '))}</p><pre>${escapeHTML(r.message)}</pre></details>`).join(''):'<p class="help">No customer updates recorded yet.</p>');}
$('customer-update-contact').addEventListener('change',()=>{const c=customerUpdateContext,contact=$('customer-update-contact').value==='company'?c.customer:c.contacts[Number($('customer-update-contact').value)],f=$('customer-update-form');f.elements.recipient_email.value=contact.email||'';f.elements.recipient_phone.value=contact.phone||'';invalidateCustomerDraft();});
$('customer-update-form').addEventListener('input',e=>{if(e.target.id!=='customer-update-channel')invalidateCustomerDraft();});
for(const id of ['customer-update-date','customer-update-stage','customer-update-remarks'])$(id).addEventListener('change',regenerateCustomerMessage);
$('regenerate-customer-update').addEventListener('click',regenerateCustomerMessage);
$('customer-update-form').addEventListener('submit',e=>{e.preventDefault();const button=e.target.querySelector('button[type="submit"]');if(button.disabled)return;button.disabled=true;perform(async()=>{try{
  savedCustomerDraft=await api('/api/work-orders/'+customerUpdateContext.job.id+'/customer-updates','POST',Object.fromEntries(new FormData(e.target)));
  $('customer-update-email').hidden=!savedCustomerDraft.recipient_email;$('customer-update-whatsapp').hidden=!savedCustomerDraft.recipient_phone;
  $('customer-update-email').href=savedCustomerDraft.recipient_email?'mailto:'+encodeURIComponent(savedCustomerDraft.recipient_email)+'?subject='+encodeURIComponent(savedCustomerDraft.subject)+'&body='+encodeURIComponent(savedCustomerDraft.message):'#';
  $('customer-update-whatsapp').href=savedCustomerDraft.recipient_phone?'https://wa.me/'+savedCustomerDraft.recipient_phone.slice(1)+'?text='+encodeURIComponent(savedCustomerDraft.message):'#';
  $('customer-update-send').hidden=false;const context=await api('/api/work-orders/'+customerUpdateContext.job.id+'/customer-updates');renderCustomerUpdateHistory(context.updates);notice('Draft saved. Open your email or WhatsApp app to send it.');
}finally{button.disabled=false;}});});
$('copy-customer-update').addEventListener('click',()=>perform(async()=>{await navigator.clipboard.writeText($('customer-update-form').elements.message.value);notice('Customer update copied.');}));
$('mark-customer-update-sent').addEventListener('click',()=>perform(async()=>{if(!savedCustomerDraft)throw new Error('Save the reviewed draft first.');if(!confirm('Confirm that you have sent this exact update yourself. This records your confirmation; it does not send a message.'))return;await api('/api/customer-updates/'+savedCustomerDraft.id,'PATCH',{sent_by_user:true,channel:$('customer-update-channel').value});const c=await api('/api/work-orders/'+customerUpdateContext.job.id+'/customer-updates');renderCustomerUpdateHistory(c.updates);notice('Your sending confirmation is recorded.');}));
$('job-file-customer-update').addEventListener('click',()=>perform(async()=>{const id=$('job-file-select').value;if(!id)throw new Error('Select a work order first.');await openCustomerUpdate(id);}));
async function openPurchaseOrder(id=''){
  const [vendors,items]=await Promise.all([api('/api/suppliers'),api('/api/stock-items')]);if(!vendors.length||!items.length)throw new Error('Add a supplier and stock item first.');
  const row=id?purchaseOrders.find(p=>p.id===Number(id)):null,f=$('purchase-form');f.reset();f.elements.record_id.value=id;$('purchase-dialog-title').textContent=id?'Edit purchase order · PO-'+id:'Create purchase order';
  f.elements.supplier_id.innerHTML=vendors.filter(x=>x.active||x.id===row?.supplier_id).map(x=>`<option value="${x.id}">${escapeHTML(x.name)}</option>`).join('');
  f.elements.item_id.innerHTML=items.filter(x=>x.active||x.id===row?.item_id).map(x=>`<option value="${x.id}">${escapeHTML(x.sku)} · ${escapeHTML(x.name)}</option>`).join('');
  for(const k of ['supplier_id','item_id','unit_price'])f.elements[k].disabled=Boolean(row&&Number(row.received_qty)>0);
  if(row)for(const k of ['supplier_id','item_id','ordered_qty','unit_price','expected_date','supplier_reference','notes'])f.elements[k].value=row[k]??'';
  f.querySelector('button.primary').textContent=id?'Save purchase order':'Create purchase order';$('purchase-dialog').showModal();
}
function renderMaintenanceChecklist(){
  const doneByLabel=new Map(maintenanceChecklist.map(x=>[x.label,x.done]));
  const labels=$('maintenance-checklist-input').value.split('\n').map(x=>x.trim()).filter(Boolean);
  if(labels.length>30||labels.some(x=>x.length>160))throw new Error('Use up to 30 checklist items, with at most 160 characters each.');
  if(new Set(labels).size!==labels.length)throw new Error('Give each checklist item a different label.');
  maintenanceChecklist=labels.map(label=>({label,done:doneByLabel.get(label)||false}));
  $('maintenance-checklist').innerHTML=maintenanceChecklist.map((x,i)=>`<label class="checkbox-line"><input type="checkbox" data-maintenance-check="${i}" ${x.done?'checked':''}>${escapeHTML(x.label)}</label>`).join('');
}
$('maintenance-checklist-input').addEventListener('change',()=>perform(renderMaintenanceChecklist));
$('maintenance-checklist').addEventListener('change',e=>{if(e.target.matches('[data-maintenance-check]'))maintenanceChecklist[Number(e.target.dataset.maintenanceCheck)].done=e.target.checked;});
async function openMaintenanceTask(id=''){
  await ensureTeam();const assets=await api('/api/company-assets');if(!assets.length)throw new Error('Add a company asset first.');const row=id?maintenanceTasks.find(t=>t.id===Number(id)):null,f=$('maintenance-form');f.reset();f.elements.record_id.value=id;
  f.elements.asset_id.innerHTML=assets.filter(a=>a.active||a.id===row?.asset_id).map(a=>`<option value="${a.id}">${escapeHTML(a.code)} · ${escapeHTML(a.name)}</option>`).join('');
  $('maintenance-assignee').innerHTML='<option value="">Not assigned</option>'+team.filter(x=>x.active||x.id===row?.assigned_id).map(x=>`<option value="${x.id}">${escapeHTML(x.name)}</option>`).join('');
  $('maintenance-edit-fields').hidden=!row;maintenanceChecklist=row?.checklist||[];$('maintenance-checklist-input').value=maintenanceChecklist.map(x=>x.label).join('\n');renderMaintenanceChecklist();
  if(row){for(const k of ['asset_id','description','due_date','status','completed_date','cost','downtime_hours','notes','assigned_id','priority','parts_used','next_service_date'])f.elements[k].value=row[k]??'';setServiceForm(f,{...row,service_type:row.task_type==='Preventive service'?'Preventive':row.task_type});}
  $('maintenance-dialog').showModal();
}
$('asset-table').addEventListener('click',e=>{const b=e.target.closest('[data-asset-history]');if(b)perform(async()=>{const result=await api('/api/company-assets/'+b.dataset.assetHistory+'/history');$('asset-history').innerHTML=`<article class="asset-history-card"><h3>${escapeHTML(result.asset.name)} · Service history</h3><p>Total recorded cost: ₹${escapeHTML(result.total_cost)} · Downtime: ${escapeHTML(result.total_downtime)} hours</p>${result.tasks.map(t=>`<div class="history-entry"><strong>${escapeHTML(t.task_type)} · ${escapeHTML(t.status)}</strong><p>${escapeHTML(t.description)}</p><small>${escapeHTML(t.completed_date||t.due_date||'No date')} · ${escapeHTML(t.assigned||'Unassigned')} · ${escapeHTML((t.service_activities||[]).join(', '))}</small><p>${escapeHTML(t.parts_used||'')}</p></div>`).join('')||'<p>No service tasks yet.</p>'}</article>`;$('asset-history').scrollIntoView({block:'start',behavior:'smooth'});});});
$('open-alerts').addEventListener('click',()=>perform(()=>navigate('reminders')));

perform(boot);

$('asset-table').addEventListener('click',e=>{const b=e.target.closest('[data-asset-edit]');if(!b)return;const a=maintenanceAssets.find(x=>x.id===Number(b.dataset.assetEdit)),f=$('asset-form');f.reset();for(const k of ['code','kind','name','model','serial_or_registration','location','next_service_date'])f.elements[k].value=a[k]||'';f.elements.record_id.value=a.id;$('asset-dialog').showModal();});
