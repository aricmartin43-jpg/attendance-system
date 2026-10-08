from test_app import module, database, client, post, employee

ANSWERS=[0,1,2,0,1,2,0,1,2,0]

def attempt(c, answers=ANSWERS, **extra):
    return post(c,'/api/training/attempts',dict(lesson_id='welding-ta-v1',reviewed_lesson=True,answers=answers,**extra))

def test_scoring_and_employee_isolation():
    admin=client();worker,eid=employee(admin);other,_=employee(admin,'ces002')
    lesson=worker.get('/api/training').json
    assert 'correct_answer' not in str(lesson['questions'])
    r=attempt(worker, employee_id=other.get('/api/session').json['user']['id'],score=10)
    assert r.status_code==201 and r.json['score']==10
    assert len(worker.get('/api/training').json['attempts'])==1
    assert other.get('/api/training').json['attempts']==[]
    assert len(admin.get('/api/training').json['attempts'])==1
    assert attempt(worker,[0]*10).json['passed'] is False
    assert attempt(worker,[True]*10).status_code==400
    assert attempt(worker,[0]).status_code==400
    assert worker.post('/api/training/attempts',json={}).status_code==403
    assert module.app.test_client().get('/api/training').status_code==401

def test_practical_review_permissions_and_audit():
    admin=client();worker,_=employee(admin)
    r=attempt(worker).json
    path=f'/api/training/attempts/{r["id"]}/review'
    data=dict(result='Passed',notes='Observed PPE, extraction and shutdown.',observed_practical=True)
    assert worker.patch(path,json=data,headers={'X-CSRF-Token':worker.csrf}).status_code==403
    assert admin.patch(path,json=data,headers={'X-CSRF-Token':admin.csrf}).json['equipment_authorised'] is False
    assert admin.patch(path,json=data,headers={'X-CSRF-Token':admin.csrf}).status_code==409
    own=attempt(admin).json
    assert admin.patch(f'/api/training/attempts/{own["id"]}/review',json=data,headers={'X-CSRF-Token':admin.csrf}).status_code==403
    failed=attempt(worker,[0]*10).json
    assert admin.patch(f'/api/training/attempts/{failed["id"]}/review',json=data,headers={'X-CSRF-Token':admin.csrf}).status_code==400


def test_role_modules_score_the_selected_lesson_and_preserve_history():
    import training
    admin=client();worker,_=employee(admin)
    catalogue=worker.get('/api/training').json
    assert len(catalogue['lessons'])==12
    for metadata in catalogue['lessons']:
        lesson_id=metadata['id']
        selected=worker.get('/api/training',query_string={'lesson_id':lesson_id}).json
        assert selected['lesson']['id']==lesson_id
        assert len(selected['questions'])==10
        assert 'questions' not in selected['lesson']
        assert all('correct_answer' not in q and 'answer' not in q for q in selected['questions'])
        answers=[q[2] for q in training.QUESTION_BANK[lesson_id]]
        result=post(worker,'/api/training/attempts',dict(lesson_id=lesson_id,reviewed_lesson=True,answers=answers))
        assert result.status_code==201
        assert result.json['score']==10 and result.json['lesson_id']==lesson_id
        assert result.json['lesson_title']==metadata['title']
    records=worker.get('/api/training').json['attempts']
    assert len(records)==12 and len({r['lesson_id'] for r in records})==12
    assert worker.get('/api/training?lesson_id=unknown').status_code==400
    assert post(worker,'/api/training/attempts',dict(lesson_id='unknown',reviewed_lesson=True,answers=ANSWERS)).status_code==400
