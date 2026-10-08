"""Company operations regression tests with enforced references and isolated data."""
import io
from decimal import Decimal
import pytest
from sqlalchemy import text
from test_app import module, client, post, database
from init_db import initialise


@pytest.fixture(autouse=True)
def foreign_keys(database):
    with module.engine.connect() as connection:
        connection.execute(text('PRAGMA foreign_keys=ON'))
    yield
    with module.engine.connect() as connection:
        assert connection.execute(text('PRAGMA foreign_key_check')).all() == []


def create(c, path, **data):
    r = post(c, path, data)
    assert r.status_code == 201, (path, r.status_code, r.json)
    return r.json


def patch(c, path, **data):
    return c.patch(path, json=data, headers={'X-CSRF-Token': c.csrf})


def delete(c, path, preview=None):
    return c.delete(path, json=preview or {}, headers={'X-CSRF-Token': c.csrf})


def fixture_work():
    a = client()
    e = create(a, '/api/employees', code='ces100', name='Service Engineer', department='Service', pin='1234')
    c = create(a, '/api/customers', name='Precision Engineering', email='projects@example.test', phone='+919876543210', address='Coimbatore')
    m = create(a, '/api/machines', customer_id=c['id'], machine_type='VMC', model='VMC-850', manufacturer='Example OEM', customer_machine_no='VMC-01', specifications='Travel X/Y/Z: recorded from nameplate', next_service_date='2020-01-01')
    j = create(a, '/api/work-orders', customer_id=c['id'], machine_id=m['id'], title='Telescopic cover repair', service_product='X-axis telescopic cover', service_type='Repair', service_activities=['Cover Service', 'Replacement and Fixing work'], target_date='2020-01-02', assigned_employee_ids=[e['id']], current_stage='Welding')
    return a, e, c, m, j


def stock(a):
    return create(a, '/api/stock-items', sku='CR-2', category='Raw material', sub_category='Sheet', size_dimension='2 mm', material_finish='CR', unit='kg', reorder_level=5)


def document(a, kind, id):
    r = a.post(f'/api/documents/{kind}/{id}', data={'file': (io.BytesIO(b'%PDF-1.4 fixture'), 'record.pdf')}, headers={'X-CSRF-Token': a.csrf})
    assert r.status_code == 201
    return r.json['id']


def test_linked_plans_service_reports_and_status_permissions():
    a, e, c, m, j = fixture_work()
    worker = client('ces100', '1234')
    opts = a.get('/api/workflow-options').json
    assert opts['service_types'] == ['Breakdown', 'Preventive', 'Inspection', 'Installation', 'Repair']
    assert len(opts['service_activities']) == 8
    p = create(a, '/api/daily-plans', date='2020-01-01', due_date='2020-01-03', title='Cover installation', work_order_id=j['id'], employee_id=e['id'], estimated_hours=3)
    assert (p['customer_id'], p['machine_id'], p['service_type']) == (c['id'], m['id'], 'Repair')
    assert p['service_activities'] == j['service_activities']
    other = create(a, '/api/customers', name='Other Company')
    assert patch(a, f'/api/daily-plans/{p["id"]}', customer_id=other['id']).status_code == 400
    assert post(a, '/api/work-orders', dict(customer_id=c['id'], title='Bad activity', service_type='Repair', service_activities=['Unlisted work'])).status_code == 400
    assert patch(worker, f'/api/daily-plans/{p["id"]}', status='Closed').status_code == 403
    assert patch(worker, f'/api/daily-plans/{p["id"]}', status='Rework').status_code == 200
    assert patch(a, f'/api/daily-plans/{p["id"]}', title='Refit cover', status='Closed').status_code == 200
    assert a.get(f'/api/employees/{e["id"]}/profile?month=2020-01').json['completed_tasks'] == 1
    assert patch(a, f'/api/work-orders/{j["id"]}', work_type='Repair', status='Closed').status_code == 200
    assert a.get(f'/api/customers/{c["id"]}').json['serviced_machine_count'] == 1
    alerts = a.get('/api/deadline-reminders').json['items']
    assert not any(x['kind']=='Planned task' and x['id']==p['id'] for x in alerts)
    report = create(worker, '/api/work-reports', date='2020-01-01', work_order_id=j['id'], work_details='Wiper replaced and alignment checked', status='Rework')
    assert report['service_type'] == 'Repair'
    assert report['customer'] == c['name']
    assert post(worker, '/api/work-reports', dict(work_order_id='bad', work_details='Invalid link')).status_code == 400
    path = f'/api/daily-plans/{p["id"]}'
    assert worker.get(path+'/delete-preview').status_code == 403
    preview = a.get(path+'/delete-preview').json
    assert delete(a, path).status_code == 400
    assert delete(a, path, preview).status_code == 200
    assert a.get(f'/api/machines/{m["id"]}/history').status_code == 200


def test_customer_delete_previews_all_children_and_retains_stock_ledger():
    a, e, c, m, j = fixture_work()
    path = f'/api/customers/{c["id"]}'
    doc_ids = [document(a, 'job', j['id']), document(a, 'customer', c['id'])]
    create(a, path+'/contacts', name='Project Contact')
    create(a, path+'/sites', name='Factory 1')
    create(a, '/api/daily-plans', date='2020-01-01', title='Fit cover', work_order_id=j['id'], employee_id=e['id'], estimated_hours=2)
    create(a, '/api/work-reports', employee_id=e['id'], date='2020-01-01', work_order_id=j['id'], work_details='Service report')
    create(a, '/api/production-steps', work_order_id=j['id'], operation='Welding', sequence=1)
    create(a, f'/api/work-orders/{j["id"]}/drawings', drawing_no='COVER-1', revision='A', file_reference='drawing.pdf')
    create(a, f'/api/work-orders/{j["id"]}/quality', operation='Inspection', inspected_qty=2, accepted_qty=2, rejected_qty=0)
    create(a, f'/api/work-orders/{j["id"]}/dispatch', quantity=1, dispatch_date='2020-01-01')
    create(a, f'/api/work-orders/{j["id"]}/customer-updates', subject='Progress', message='Assembly in progress')
    create(a, '/api/quotations', customer_id=c['id'], title='Replacement cover', amount=100)
    item = stock(a)
    create(a, f'/api/stock-items/{item["id"]}/movements', kind='Receive', quantity=10, reason='Opening stock')
    req = create(a, f'/api/work-orders/{j["id"]}/materials', item_id=item['id'], required_qty=7)
    assert post(a, f'/api/job-materials/{req["id"]}/reserve', {'quantity':7}).status_code == 200
    assert post(a, f'/api/job-materials/{req["id"]}/issue', {'quantity':2}).status_code == 200
    preview = a.get(path+'/delete-preview').json
    assert preview['counts']['Linked work reports'] == 1 and preview['counts']['Customer documents'] == 1
    create(a, path+'/contacts', name='New contact after preview')
    assert delete(a, path, preview).status_code == 409
    preview = a.get(path+'/delete-preview').json
    assert delete(a, path, preview).status_code == 200
    assert a.get(path).status_code == 404
    for id in doc_ids:
        assert a.get(f'/api/documents/download/{id}').status_code == 404
    item_after = a.get('/api/stock-items').json[0]
    assert item_after['quantity'] == item_after['available'] == '8.000'
    with module.DB() as db:
        assert db.get(module.WorkOrder, j['id']) is None
        assert db.get(module.CustomerMachine, m['id']) is None
        assert db.scalar(module.select(module.WorkReport)) is None
        assert db.get(module.Employee, e['id']) is not None
        movements = db.scalars(module.select(module.StockMovement)).all()
        assert len(movements)==2 and all(x.work_order_id is None for x in movements)


def test_employee_delete_clears_personal_data_and_preserves_company_records():
    a, e, c, m, j = fixture_work()
    worker = client('ces100', '1234')
    personal = document(a, 'employee', e['id'])
    company_doc = document(a, 'job', j['id'])
    create(a, '/api/daily-plans', date='2020-01-01', title='Install', employee_id=e['id'], work_order_id=j['id'], estimated_hours=2)
    create(worker, '/api/work-reports', date='2020-01-01', work_order_id=j['id'], work_details='Installation work')
    assert patch(a, f'/api/employee-files/{e["id"]}', skills='Mechanical service').status_code == 200
    create(a, f'/api/employees/{e["id"]}/points', date='2020-01-01', category='Behaviour', points=-1, reason='Recorded incident')
    create(a, f'/api/employees/{e["id"]}/memos', date='2020-01-01', rule_id='cleanliness', points=0.5, details='Tools left unclean', reviewed=True)
    asset = create(a, '/api/company-assets', code='TOOL-1', kind='Tool', name='Alignment kit')
    task = create(a, '/api/maintenance-tasks', asset_id=asset['id'], task_type='Inspection', description='Inspect tool kit', assigned_id=e['id'])
    item=stock(a)
    vendor=create(a, '/api/suppliers', name='Supplier')
    po=create(a, '/api/purchase-orders', supplier_id=vendor['id'], item_id=item['id'], ordered_qty=5, unit_price=10)
    create(a, f'/api/stock-items/{item["id"]}/movements', kind='Receive', quantity=5, reason='Opening stock')
    with module.DB.begin() as db:
        db.get(module.WorkOrder,j['id']).supervisor_id=e['id']
        db.get(module.PurchaseOrder,po['id']).created_by=e['id']
        db.get(module.PrivateDocument,company_doc).uploaded_by=e['id']
        db.scalar(module.select(module.StockMovement)).actor_id=e['id']
        db.add(module.Attendance(employee_id=e['id'],work_date='2020-01-01',in_at=module.now(),in_lat=0,in_lng=0,in_accuracy=1,in_photo='private-test-photo',in_distance=0))
        meeting=module.Meeting(meeting_date='2020-01-01',title='Service meeting',created_by=e['id'],created_at=module.now())
        db.add(meeting);db.flush()
        db.add(module.MeetingAction(meeting_id=meeting.id,employee_id=e['id'],action='Follow up',status='Open',created_at=module.now()))
    path=f'/api/employees/{e["id"]}'
    assert delete(worker,path).status_code==403
    preview=a.get(path+'/delete-preview').json
    assert preview['counts']['Attendance records']==1 and preview['counts']['Points entries']==1
    assert delete(a,path,preview).status_code==200
    assert worker.get('/api/work-reports').status_code==401
    assert a.get(f'/api/documents/download/{personal}').status_code==404
    assert a.get(f'/api/documents/download/{company_doc}').status_code==200
    with module.DB() as db:
        assert db.get(module.Employee,e['id']) is None
        assert db.get(module.WorkOrder,j['id']).supervisor_id is None
        assert db.get(module.PurchaseOrder,po['id']).created_by is None
        assert db.get(module.MaintenanceTask,task['id']).assigned_id is None
        assert db.scalar(module.select(module.StockMovement)).actor_id is None
        assert db.scalar(module.select(module.Meeting)).created_by is None
        admin_id=db.scalar(module.select(module.Employee.id).where(module.Employee.admin==True))
    assert a.get(f'/api/employees/{admin_id}/delete-preview').status_code==404


def test_purchase_order_edits_partial_receipts_close_cancel_and_print():
    a=client();item=stock(a);v=create(a,'/api/suppliers',name='<script>Supplier</script>')
    po=create(a,'/api/purchase-orders',supplier_id=v['id'],item_id=item['id'],ordered_qty=10,unit_price=80,expected_date='2020-01-01')
    path=f'/api/purchase-orders/{po["id"]}'
    assert patch(a,path,supplier_reference='REF-100',status='Ordered',notes='Approved order').status_code==200
    assert post(a,path+'/receive',{'quantity':4}).status_code==200
    assert patch(a,path,ordered_qty=3).status_code==409
    assert patch(a,path,unit_price=81).status_code==409
    assert patch(a,path,status='Closed').status_code==400
    assert patch(a,path,status='Closed',reason='Balance no longer required').status_code==200
    assert post(a,path+'/receive',{'quantity':1}).status_code==409
    assert not any(x['kind']=='Purchase delivery' for x in a.get('/api/deadline-reminders').json['items'])
    assert patch(a,path,status='Open').json['status']=='Part received'
    assert patch(a,path,status='Cancelled',reason='Order cancelled').status_code==200
    assert a.get('/api/stock-items').json[0]['quantity']=='4.000'
    page=a.get(path+'/print')
    assert page.status_code==200 and b'&lt;script&gt;Supplier&lt;/script&gt;' in page.data
    assert b'<script>Supplier</script>' not in page.data


def test_maintenance_checklists_machine_dates_and_available_stock_alerts():
    a,e,c,m,j=fixture_work()
    asset=create(a,'/api/company-assets',code='TOOL-1',kind='Tool',name='Measuring tool kit')
    task=create(a,'/api/maintenance-tasks',asset_id=asset['id'],task_type='Preventive',service_activities=['Mechanical Check','Alignment / Adjustment'],description='Check alignment kit',due_date='2020-01-01',assigned_id=e['id'],checklist=[{'label':'Inspect guides','done':False}],next_service_date='2030-01-01')
    path=f'/api/maintenance-tasks/{task["id"]}'
    assert patch(a,path,status='Completed').status_code==409
    done=patch(a,path,status='Completed',checklist=[{'label':'Inspect guides','done':True}],cost=120,downtime_hours=2,parts_used='Guide pad — 1')
    assert done.status_code==200 and done.json['completed_date']
    history=a.get(f'/api/company-assets/{asset["id"]}/history').json
    assert history['asset']['next_service_date']=='2030-01-01' and Decimal(history['total_cost'])==120
    # Existing classifications remain editable after migration.
    with module.DB.begin() as db:db.get(module.MaintenanceTask,task['id']).task_type='Service'
    assert patch(a,path,task_type='Service',notes='Historical classification retained').status_code==200
    assert post(a,'/api/maintenance-tasks',dict(asset_id=asset['id'],task_type='Unlisted',description='Bad')).status_code==400
    assert patch(a,f'/api/machines/{m["id"]}',machine_type='HMC',specifications='Pallet dimensions recorded',status='Active').json['machine_type']=='HMC'
    item=stock(a)
    create(a,f'/api/stock-items/{item["id"]}/movements',kind='Receive',quantity=10,reason='Opening stock')
    req=create(a,f'/api/work-orders/{j["id"]}/materials',item_id=item['id'],required_qty=6)
    assert post(a,f'/api/job-materials/{req["id"]}/reserve',{'quantity':6}).status_code==200
    alerts=a.get('/api/deadline-reminders').json
    low=next(x for x in alerts['items'] if x['kind']=='Low stock')
    assert low['days'] is None and alerts['low_stock']==1 and '4.000' in low['notes']
    assert any(x['kind']=='Machine service' and x['id']==m['id'] for x in alerts['items'])
    assert not any(x['kind']=='Maintenance' and x['id']==task['id'] for x in alerts['items'])


def test_customer_update_drafts_and_manual_send_confirmation():
    a,e,c,m,j=fixture_work();worker=client('ces100','1234');path=f'/api/work-orders/{j["id"]}/customer-updates'
    assert worker.get(path).status_code==403
    assert patch(a,f'/api/work-orders/{j["id"]}',status='Rework',current_stage='Powder coating',customer_remarks='Final inspection scheduled').status_code==200
    context=a.get(path).json
    assert context['job']['current_stage']=='Powder coating' and context['machine']['model']=='VMC-850'
    assert post(a,path,dict(subject='Progress',message='Assembly',recipient_phone='9876543210')).status_code==400
    assert post(a,path,dict(subject='Progress',message='Assembly',recipient_email='a@x.test,b@x.test')).status_code==400
    draft=create(a,path,subject='Cover project update',message='Powder coating complete. Dispatch planned.',recipient_email='projects@example.test',recipient_phone='+919876543210')
    assert draft['status']=='Draft' and draft['sent_at'] is None
    assert patch(a,f'/api/customer-updates/{draft["id"]}',channel='Email').status_code==400
    sent=patch(a,f'/api/customer-updates/{draft["id"]}',sent_by_user=True,channel='WhatsApp')
    assert sent.json['status']=='Sent (manual)' and sent.json['sent_at']
    assert len(a.get(path).json['updates'])==1


def test_initialisation_is_idempotent_with_operations_data():
    a,e,c,m,j=fixture_work()
    initialise();initialise()
    # Startup refreshes the administrator PIN hash and invalidates existing sessions.
    a=client()
    assert a.get(f'/api/machines/{m["id"]}/history').status_code==200
    with module.DB() as db:
        assert db.get(module.WorkOrder,j['id']).service_type=='Repair'


def test_existing_database_receives_missing_operations_columns():
    a,e,c,m,j=fixture_work()
    # Simulate a pre-upgrade installation while retaining company and machine rows.
    with module.engine.begin() as connection:
        for table,column in [('daily_plans','due_date'),('work_orders','service_type'),('work_orders','service_activities'),('customer_machines','specifications'),('customer_machines','next_service_date'),('purchase_orders','supplier_reference'),('maintenance_tasks','checklist'),('work_reports','service_type')]:
            connection.execute(text(f'ALTER TABLE {table} DROP COLUMN {column}'))
        connection.execute(text('DROP TABLE customer_updates'))
    initialise()
    a=client()
    assert patch(a,f'/api/machines/{m["id"]}',specifications='Migrated machine details',next_service_date='2030-02-01').status_code==200
    assert patch(a,f'/api/work-orders/{j["id"]}',service_type='Repair',service_activities=['Cover Service']).status_code==200
    assert create(a,f'/api/work-orders/{j["id"]}/customer-updates',subject='Project update',message='Migration retained company data')['status']=='Draft'
    with module.DB() as db:
        assert db.get(module.Customer,c['id']).name==c['name']
        assert db.get(module.WorkOrder,j['id']).title==j['title']
