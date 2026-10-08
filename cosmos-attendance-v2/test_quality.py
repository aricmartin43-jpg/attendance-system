"""Broad QA: authorization, hostile inputs, lifecycle and data-integrity boundaries.
All writes use isolated SQLite fixtures with foreign-key checks; no live data.
"""
import io
import re
import pytest
from test_app import module, database, client, post, employee, attendance
from test_operations import foreign_keys, create, patch, fixture_work, stock


def api_routes():
    for rule in module.app.url_map.iter_rules():
        if not rule.rule.startswith('/api/') or rule.rule in ('/api/login','/api/logout','/api/session'):
            continue
        path=re.sub(r'<(?:int:)?[^>]+>', '999999', rule.rule)
        for method in sorted(rule.methods-{'HEAD','OPTIONS'}):
            yield method,path

ROUTES=list(api_routes())
EMPLOYEE_ROUTES={
 ('GET','/api/training'),('POST','/api/training/attempts'),
 ('GET','/api/attendance'),('GET','/api/my-status'),('POST','/api/attendance'),('POST','/api/capture'),
 ('GET','/api/ecosystem/summary'),('GET','/api/work-reports'),('POST','/api/work-reports'),
 ('GET','/api/issues'),('POST','/api/issues'),('GET','/api/meetings'),('PATCH','/api/meeting-actions/999999'),
 ('GET','/api/workflow-options'),('GET','/api/daily-plans'),('PATCH','/api/daily-plans/999999'),
 ('GET','/api/customers'),('GET','/api/customers/999999'),('GET','/api/machine-types'),
 ('GET','/api/machines'),('GET','/api/machines/999999/history'),('GET','/api/work-orders'),

}

@pytest.mark.parametrize('method,path',ROUTES)
def test_anonymous_requests_cannot_access_portal_data(method,path):
    c=module.app.test_client();csrf=c.get('/api/session').json['csrf']
    r=c.open(path,method=method,json={} if method!='GET' else None,headers={'X-CSRF-Token':csrf})
    assert r.status_code==401,(method,path,r.status_code,r.json)

@pytest.mark.parametrize('method,path',[r for r in ROUTES if r not in EMPLOYEE_ROUTES])
def test_employee_cannot_use_administration_endpoints(method,path):
    c,_=employee(client())
    r=c.open(path,method=method,json={} if method!='GET' else None,headers={'X-CSRF-Token':c.csrf})
    assert r.status_code==403,(method,path,r.status_code,r.json)

@pytest.mark.parametrize('method',['POST','PATCH','DELETE'])
@pytest.mark.parametrize('token',[None,'wrong-token'])
def test_csrf_required_for_every_mutation_method(method,token):
    c=client();h={'X-CSRF-Token':token} if token else {}
    r=c.open('/api/customers' if method=='POST' else '/api/customers/999999',method=method,json={},headers=h)
    assert r.status_code==403

@pytest.mark.parametrize('body',[[1],['name'],42,'customer',True,None])
@pytest.mark.parametrize('path',['/api/customers','/api/daily-plans','/api/work-reports'])
def test_nonobject_json_is_rejected_without_server_error(path,body):
    c=client()
    r=c.post(path,data=module.json.dumps(body),content_type='application/json',headers={'X-CSRF-Token':c.csrf})
    assert r.status_code==400,(r.status_code,r.json)

@pytest.mark.parametrize('value',[None,True,12,[],{},'','   ','x'*181])
def test_customer_name_requires_nonempty_text(value):
    r=post(client(),'/api/customers',{'name':value});assert r.status_code==400,(r.status_code,r.json)

@pytest.mark.parametrize('bad',['NaN','Infinity','-Infinity','-1','0','0.0001','100000000000','oops',None,True])
def test_stock_quantity_boundaries_do_not_change_balance(bad):
    a=client();item=stock(a)
    r=post(a,f'/api/stock-items/{item["id"]}/movements',dict(kind='Receive',quantity=bad,reason='Boundary test'))
    assert r.status_code==400,(r.status_code,r.json)
    assert float(a.get('/api/stock-items').json[0]['quantity'])==0

@pytest.mark.parametrize('value',['bad','1.5',1.5,True,-1,0,{},[]])
def test_machine_type_id_rejects_invalid_types(value):
    a=client();c=create(a,'/api/customers',name='QA company')
    r=post(a,'/api/machines',dict(customer_id=c['id'],machine_type_id=value))
    assert r.status_code==400,(r.status_code,r.json)

@pytest.mark.parametrize('day',['2026-02-30','2025-02-29','2026-13-01','2026-2-03','2026-02-3','2026-10-01T12:00:00','not a date'])
def test_planning_rejects_invalid_or_noncanonical_dates(day):
    a=client();employee(a)
    r=post(a,'/api/daily-plans',dict(date=day,title='QA task',estimated_hours=1))
    assert r.status_code==400,(r.status_code,r.json)

@pytest.mark.parametrize('hours',[None,True,0,-1,25,'NaN','Infinity','bad'])
def test_planning_duration_boundaries(hours):
    a=client();employee(a)
    r=post(a,'/api/daily-plans',dict(date='2026-10-01',title='QA task',estimated_hours=hours))
    assert r.status_code==400,(r.status_code,r.json)

@pytest.mark.parametrize('service',['Breakdown','Preventive','Inspection','Installation','Repair'])
def test_each_service_type_and_all_activities_roundtrip(service):
    a=client();c=create(a,'/api/customers',name='QA services')
    activities=['Cover Service','Conveyor Service','Alignment / Adjustment','Drawing Work','Repairs','Mechanical Check','Others','Replacement and Fixing work']
    j=create(a,'/api/work-orders',customer_id=c['id'],title='QA service',service_type=service,service_activities=activities)
    assert j['service_type']==service and j['service_activities']==activities

@pytest.mark.parametrize('state',['Open','In Progress','Completed','Closed','Cancelled','Rework'])
def test_project_status_completion_and_reopen_dates(state):
    a,e,c,m,j=fixture_work();path=f'/api/work-orders/{j["id"]}'
    r=patch(a,path,status=state);assert r.status_code==200
    assert bool(r.json['completed_date'])==(state in ('Completed','Closed'))
    r=patch(a,path,status='Open');assert r.json['completed_date'] is None

@pytest.mark.parametrize('activities',['Repairs',[None],['Unknown'],{},[1]])
def test_invalid_service_activities_rollback(activities):
    a=client();c=create(a,'/api/customers',name='QA invalid service')
    r=post(a,'/api/work-orders',dict(customer_id=c['id'],title='Must not save',service_type='Repair',service_activities=activities))
    assert r.status_code==400
    assert a.get('/api/work-orders').json==[]

@pytest.mark.parametrize('filename,content,expected',[
 ('good.pdf',b'%PDF-1.4 fixture',201),('empty.pdf',b'',413),('fake.pdf',b'<script>bad</script>',400),
 ('attack.html',b'<script>bad</script>',400),('drawing.exe',b'MZ',400),
 ('large.pdf',b'%PDF-'+b'0'*2_000_000,413),('limit.pdf',b'%PDF-'+b'0'*1_999_995,201),
 ('..\\..\\drawing.pdf',b'%PDF-1.4 fixture',201),('line\rbreak.pdf',b'%PDF-1.4 fixture',400),
])
def test_document_extension_signature_size_and_safe_download(filename,content,expected):
    a=client();c=create(a,'/api/customers',name='Document QA')
    r=a.post(f'/api/documents/customer/{c["id"]}',data={'file':(io.BytesIO(content),filename)},headers={'X-CSRF-Token':a.csrf})
    assert r.status_code==expected,(r.status_code,r.json)
    if expected==201:
        assert '/' not in r.json['filename'] and '\\' not in r.json['filename']
        d=a.get(f'/api/documents/download/{r.json["id"]}')
        assert d.status_code==200 and d.data==content
        assert d.headers['Content-Disposition'].startswith('attachment;')


def test_plan_assignment_grants_and_revokes_linked_job_visibility():
    a=client();worker,eid=employee(a);other,other_id=employee(a,'ces002')
    c=create(a,'/api/customers',name='Linked planning company')
    m=create(a,'/api/machines',customer_id=c['id'],machine_type='HMC')
    j=create(a,'/api/work-orders',customer_id=c['id'],machine_id=m['id'],title='Planned work')
    assert worker.get('/api/work-orders').json==[]
    p=create(a,'/api/daily-plans',date='2026-10-01',title='Assigned from planner',work_order_id=j['id'],employee_id=eid,estimated_hours=2)
    assert [x['id'] for x in worker.get('/api/work-orders').json]==[j['id']]
    assert worker.get(f'/api/customers/{c["id"]}').status_code==200
    assert worker.get(f'/api/machines/{m["id"]}/history').status_code==200
    assert post(worker,'/api/work-reports',dict(work_order_id=j['id'],work_details='Cover serviced')).status_code==201
    assert other.get('/api/work-orders').json==[]
    assert patch(a,f'/api/daily-plans/{p["id"]}',employee_id=other_id).status_code==200
    assert worker.get('/api/work-orders').json==[]
    assert [x['id'] for x in other.get('/api/work-orders').json]==[j['id']]
    assert patch(a,f'/api/daily-plans/{p["id"]}',status='Cancelled').status_code==200
    assert other.get('/api/work-orders').json==[]


def test_attendance_date_edit_keeps_daily_identity_and_prevents_collisions(monkeypatch):
    a=client();w,eid=employee(a)
    day=module.datetime(2026,9,15,4,tzinfo=module.UTC);monkeypatch.setattr(module,'now',lambda:day)
    first=attendance(w).json
    r=patch(a,f'/api/attendance/{first["id"]}',check_in='2026-09-16T09:30:00+05:30')
    assert r.status_code==200 and r.json['date']=='2026-09-16'
    patch(a,f'/api/attendance/{first["id"]}',check_out='2026-09-16T17:30:00+05:30')
    monkeypatch.setattr(module,'now',lambda:day+module.timedelta(days=2))
    second=attendance(w).json
    r=patch(a,f'/api/attendance/{second["id"]}',check_in='2026-09-16T10:30:00+05:30')
    assert r.status_code==409
    assert next(x for x in w.get('/api/attendance').json if x['id']==second['id'])['date']=='2026-09-17'
    assert patch(a,f'/api/attendance/{first["id"]}',check_out=None).status_code==409


def test_repeated_assignee_does_not_fail_or_duplicate_assignment():
    a=client();w,eid=employee(a);c=create(a,'/api/customers',name='Assignment QA')
    j=create(a,'/api/work-orders',customer_id=c['id'],title='Repeated assignee',assigned_employee_ids=[eid,eid])
    assert len(j['assignments'])==1


def test_partial_inventory_taxonomy_edit_updates_display_name():
    a=client();item=stock(a)
    r=patch(a,f'/api/stock-items/{item["id"]}',category='Consumables')
    assert r.status_code==200 and r.json['name']=='Consumables - Sheet - 2 mm - CR'


def test_employee_joining_date_is_validated():
    a=client();w,eid=employee(a)
    assert patch(a,f'/api/employee-files/{eid}',joining_date='2026-02-30').status_code==400
    assert patch(a,f'/api/employee-files/{eid}',joining_date='2024-02-29').status_code==200


def test_pin_reset_invalidates_existing_session():
    a=client();w,eid=employee(a)
    assert post(a,f'/api/employees/{eid}/reset-pin',{'pin':'5432'}).status_code==200
    assert w.get('/api/attendance').status_code==401
    assert client('ces001','5432').get('/api/attendance').status_code==200


def test_cross_company_machine_link_is_rejected_transactionally():
    a,e,c,m,j=fixture_work();other=create(a,'/api/customers',name='Other QA company')
    before=len(a.get('/api/work-orders').json)
    assert post(a,'/api/work-orders',dict(customer_id=other['id'],machine_id=m['id'],title='Wrong link')).status_code==400
    assert len(a.get('/api/work-orders').json)==before


def test_cancelled_purchase_and_rejected_quality_cannot_release_stock():
    a,e,c,m,j=fixture_work();item=stock(a);supplier=create(a,'/api/suppliers',name='QA vendor')
    po=create(a,'/api/purchase-orders',supplier_id=supplier['id'],item_id=item['id'],ordered_qty=10,unit_price=50)
    assert patch(a,f'/api/purchase-orders/{po["id"]}',status='Cancelled',reason='Supplier cannot deliver').status_code==200
    assert post(a,f'/api/purchase-orders/{po["id"]}/receive',{'quantity':1}).status_code==409
    assert float(a.get('/api/stock-items').json[0]['quantity'])==0
    assert post(a,f'/api/work-orders/{j["id"]}/quality',dict(operation='Final',inspected_qty=5,accepted_qty=4,rejected_qty=2)).status_code==400
    create(a,f'/api/work-orders/{j["id"]}/quality',operation='Final',inspected_qty=5,accepted_qty=0,rejected_qty=5)
    assert post(a,f'/api/work-orders/{j["id"]}/dispatch',dict(quantity=1,dispatch_date='2026-09-30')).status_code==409
