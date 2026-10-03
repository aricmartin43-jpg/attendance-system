"""PIN login, location validation and team attendance behind one shared network."""
from test_app import module, database, client, post
import pytest


def add_employee(admin, number):
    response=post(admin,'/api/employees',dict(code=f'ces{number:03}',name=f'Team member {number}',department='Production',pin='1234'))
    assert response.status_code==201
    return response.json['id']


def position(**values):
    return dict(lat=11.01,lng=76.96,accuracy=12,timestamp=module.now().timestamp()*1000,**values)


def test_whole_team_can_login_and_mark_attendance_from_one_ip(monkeypatch):
    admin=client()
    for number in range(1,22):add_employee(admin,number)
    module.limiter.reset()
    monkeypatch.setattr(module.limiter,'enabled',True)
    people=[client(f'ces{number:03}','1234') for number in range(1,22)]
    for person in people:
        response=post(person,'/api/attendance',dict(action='in',location=position()))
        assert response.status_code==201,response.json
    # Repeat attempts are still limited for the individual account.
    for _ in range(5):
        assert post(people[0],'/api/attendance',dict(action='in',location=position())).status_code==409
    blocked=post(people[0],'/api/attendance',dict(action='in',location=position()))
    assert blocked.status_code==429 and 'your account' in blocked.json['error']
    assert post(people[1],'/api/attendance',dict(action='out',location=position())).status_code==201


def test_failed_pin_limit_is_per_normalised_account_and_success_does_not_use_it(monkeypatch):
    admin=client();add_employee(admin,1);add_employee(admin,2)
    module.limiter.reset();monkeypatch.setattr(module.limiter,'enabled',True)
    bad=module.app.test_client();bad.csrf=bad.get('/api/session').json['csrf']
    for _ in range(5):
        assert post(bad,'/api/login',dict(code='CES001',pin='0000')).status_code==401
    limited=post(bad,'/api/login',dict(code=' ces001 ',pin='0000'))
    assert limited.status_code==429 and 'employee ID' in limited.json['error']
    good=client('ces002','1234')
    for _ in range(7):
        response=post(good,'/api/login',dict(code='CES002',pin='1234'))
        assert response.status_code==200
        good.csrf=response.json['csrf']
    # Switching IP cannot bypass the per-account failed-PIN protection.
    assert bad.post('/api/login',json=dict(code='ces001',pin='0000'),headers={'X-CSRF-Token':bad.csrf},environ_overrides={'REMOTE_ADDR':'198.51.100.2'}).status_code==429


def test_pin_login_needs_no_location_and_invalid_location_never_records_attendance():
    admin=client();eid=add_employee(admin,1);worker=client('ces001','1234')
    assert worker.get('/api/session').json['user']['id']==eid
    samples=[None,{},'denied',dict(lat=91,lng=76,accuracy=12,timestamp=module.now().timestamp()*1000),dict(lat=11,lng=76,accuracy=10001,timestamp=module.now().timestamp()*1000),dict(lat=11,lng=76,accuracy=12,timestamp=module.now().timestamp()*1000-121000)]
    messages=[]
    for sample in samples:
        response=post(worker,'/api/attendance',dict(action='in',location=sample))
        assert response.status_code==400
        messages.append(response.json['error'])
    assert 'too approximate' in messages[-2]
    assert 'date and time to automatic' in messages[-1]
    assert worker.get('/api/attendance').json==[]
    response=post(worker,'/api/attendance',dict(action='in',location=position()))
    assert response.status_code==201 and response.json['in_location']['accuracy']==12


def test_location_support_is_served_and_permissions_are_kept():
    c=module.app.test_client();page=c.get('/')
    assert page.status_code==200
    assert b'id="attendance-location-status"' in page.data
    assert b'id="test-location"' in page.data
    assert "geolocation=(self)" in page.headers['Permissions-Policy']
    assert page.data.index(b'/static/location.js') < page.data.index(b'/static/app.js')
    assert c.get('/static/location.js').status_code==200
