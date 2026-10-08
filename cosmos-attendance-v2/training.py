"""Tamil training pilot. Quiz completion is never equipment authorisation."""
import json
from pathlib import Path
from flask import abort, request
from sqlalchemy import ForeignKey, String, Text, DateTime, select
from sqlalchemy.orm import Mapped, mapped_column

LESSON = {
    'id': 'welding-ta-v1', 'version': 1, 'title': 'வெல்டிங் பணியில் பாதுகாப்பு',
    'status': 'Storyboard preview — supervisor content review required',
    'sources': ['https://www.hse.gov.uk/welding/protect-your-workers/avoid-reduce-exposure.htm',
                'https://www.osha.gov/welding-cutting-brazing'],
    'scenes': [
        ['தொடங்கும் முன்', 'பயிற்சி பெற்ற, அனுமதி பெற்ற நபர் மட்டுமே வெல்டிங் செய்ய வேண்டும். வேலைக்கான பாதுகாப்பு நடைமுறையை மேற்பார்வையாளருடன் சரிபார்க்கவும்.'],
        ['பாதுகாப்பு உடை', 'வேலைக்கு ஏற்ற வெல்டிங் முகக்கவசம், பாதுகாப்புக் கண்ணாடி, வெப்பத்தைக் கட்டுப்படுத்தும் கையுறை, பாதுகாப்பு உடை மற்றும் காலணி அணியவும்.'],
        ['புகையைக் கட்டுப்படுத்துங்கள்', 'புகையை உருவாகும் இடத்திலேயே உறிஞ்சும் கருவியைச் சரியாக அமைக்கவும். அறையில் காற்றோட்டம் மட்டும் போதுமானது என்று கருதாதீர்கள். தேவையான சுவாசப் பாதுகாப்பை மேற்பார்வையாளர் தேர்வு செய்ய வேண்டும்.'],
        ['அருகில் இருப்பவர்களையும் பாதுகாக்கவும்', 'வெல்டிங் திரைகளை அமைக்கவும். பாதுகாப்பில்லாமல் வெல்டிங் ஒளியை நேரடியாகப் பார்க்கக் கூடாது.'],
        ['தீ மற்றும் மின்சார அபாயம்', 'எரியக்கூடிய பொருட்களை அகற்றவும். சேதமான கேபிள் அல்லது உபகரணம் இருந்தால் பயன்படுத்தாமல் தெரிவிக்கவும். ஈரமான நிலையில் வேலை செய்யாதீர்கள்.'],
        ['சிறப்புப் பணிகள்', 'மூடிய தொட்டி அல்லது அடைக்கப்பட்ட இடத்தில் வழக்கமான வெல்டிங் செய்யாதீர்கள். தனிப்பட்ட அபாய மதிப்பீடு, அனுமதி மற்றும் மீட்பு ஏற்பாடு தேவை.'],
        ['பணி முடிந்ததும்', 'உபகரணத்தை நடைமுறைப்படி நிறுத்தவும். சூடான பொருட்களை அடையாளப்படுத்தவும். பணிக்கான தீ கண்காணிப்பு நடைமுறையைப் பின்பற்றவும். அபாயம் இருந்தால் பணியை நிறுத்தித் தெரிவிக்கவும்.'],
    ],
}

# Correct answers remain on the server until an attempt is submitted.
QUESTIONS = [
    ('வெல்டிங் செய்ய யாருக்கு அனுமதி?', ['பயிற்சியும் அனுமதியும் பெற்றவர்', 'எந்த ஊழியரும்', 'பார்த்து கற்றவர் மட்டும்'], 0, 'பயிற்சியும் வேலைக்கான அனுமதியும் அவசியம்.'),
    ('புகையைக் கட்டுப்படுத்த முதலில் எதைப் பயன்படுத்த வேண்டும்?', ['புகையை முகத்திலிருந்து ஊதுதல்', 'உருவாகும் இடத்தில் புகை உறிஞ்சும் கருவி', 'ஜன்னலை மட்டும் திறத்தல்'], 1, 'புகை உருவாகும் இடத்தில் உறிஞ்சப்பட வேண்டும்.'),
    ('வெல்டிங் ஒளியிலிருந்து அருகிலுள்ளவர்களை எது பாதுகாக்கும்?', ['தூரத்தில் நிற்பது மட்டும்', 'சாதாரண கண்ணாடி', 'பொருத்தமான வெல்டிங் திரை'], 2, 'பொருத்தமான வெல்டிங் திரைகளை அமைக்கவும்.'),
    ('சேதமான கேபிளைக் கண்டால்?', ['பயன்படுத்தாமல் மேற்பார்வையாளரிடம் தெரிவிக்கவும்', 'டேப் போட்டுத் தொடர்ந்து செய்யவும்', 'வேகமாக வேலையை முடிக்கவும்'], 0, 'சேதமான உபகரணத்தைப் பயன்படுத்தாதீர்கள்.'),
    ('எரியக்கூடிய பொருட்கள் அருகில் இருந்தால்?', ['அவற்றின் மேல் வெல்டிங் செய்யலாம்', 'பாதுகாப்பாக அகற்றி இடத்தைச் சரிபார்க்கவும்', 'பொருட்களைப் புறக்கணிக்கலாம்'], 1, 'வேலைப் பகுதியில் தீ அபாயத்தை அகற்றவும்.'),
    ('சூடான பொருளை எப்படி கையாள வேண்டும்?', ['வெறும் கையால்', 'நிறத்தை மட்டும் பார்த்து', 'சூடாக இருப்பதை அடையாளப்படுத்தி பொருத்தமான கருவியால்'], 2, 'சூடான பொருளை அடையாளப்படுத்திப் பாதுகாப்பாக கையாளவும்.'),
    ('அடைக்கப்பட்ட இடத்தில் வெல்டிங் செய்ய?', ['தனிப்பட்ட மதிப்பீடு, அனுமதி மற்றும் மீட்பு ஏற்பாடு தேவை', 'கதவு திறந்தால் போதும்', 'ஒருவர் தனியாகச் செய்யலாம்'], 0, 'இது தனிப்பட்ட அனுமதியும் கட்டுப்பாடுகளும் தேவைப்படும் பணி.'),
    ('சுவாசப் பாதுகாப்பை எப்படித் தேர்வு செய்ய வேண்டும்?', ['எந்த துணியும் போதும்', 'அபாய மதிப்பீட்டுக்கு ஏற்ற வகையில் மேற்பார்வையாளர் தேர்வு செய்ய வேண்டும்', 'தேவையில்லை'], 1, 'புகை கட்டுப்பாடுகளுக்குத் துணையாக பொருத்தமான சுவாசப் பாதுகாப்பு தேவைப்படலாம்.'),
    ('பாதுகாப்பில்லாமல் வெல்டிங் ஒளியைப் பார்க்கலாமா?', ['சில வினாடிகள் பார்க்கலாம்', 'கண்களைச் சுருக்கிப் பார்க்கலாம்', 'பார்க்கக் கூடாது'], 2, 'வேலைக்கு ஏற்ற கண் மற்றும் முகப் பாதுகாப்பு அவசியம்.'),
    ('வினாத்தேர்வில் தேர்ச்சி பெற்றதும்?', ['நடைமுறைச் சோதனையும் தனி வேலை அனுமதியும் தேவை', 'எந்த மெஷினையும் இயக்கலாம்', 'பாதுகாப்பு உடை தேவையில்லை'], 0, 'வினாத்தேர்வு மட்டும் இயந்திரம் இயக்கும் அனுமதி அல்ல.'),
]


LESSON.update(role='Welding / fabrication staff', video_url='/static/training-media/welding.mp4',
              poster_url='/static/training-media/welding.jpg', narration_ready=True,
              status='Cartoon awareness video — site supervisor review required')
LESSONS = {LESSON['id']: LESSON}
QUESTION_BANK = {LESSON['id']: QUESTIONS}
for _module in json.loads(Path(__file__).with_name('training_modules.json').read_text()):
    QUESTION_BANK[_module['id']] = _module.pop('questions')
    LESSONS[_module['id']] = _module


def register_training(app, Base, DB, Employee, login_required, now, AuditLog):
    class TrainingAttempt(Base):
        __tablename__ = 'training_attempts'
        id: Mapped[int] = mapped_column(primary_key=True)
        employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), index=True)
        lesson_id: Mapped[str] = mapped_column(String(80))
        score: Mapped[int]
        answers: Mapped[str] = mapped_column(Text)
        created_at: Mapped[object] = mapped_column(DateTime(timezone=True))
        review: Mapped[str] = mapped_column(String(30), default='Pending')
        reviewer_id: Mapped[int | None] = mapped_column(ForeignKey('employees.id'), nullable=True)
        reviewed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
        review_notes: Mapped[str] = mapped_column(Text, default='')

    def serialise(r, db):
        e = db.get(Employee, r.employee_id)
        reviewer = db.get(Employee, r.reviewer_id) if r.reviewer_id else None
        return dict(id=r.id, employee=e.name, code=e.code, lesson_id=r.lesson_id,
                    lesson_title=LESSONS.get(r.lesson_id, {}).get('title', r.lesson_id),
                    score=r.score, total=10, passed=r.score >= 8,
                    date=r.created_at.isoformat(), review=r.review,
                    reviewer=reviewer.name if reviewer else None, notes=r.review_notes,
                    reviewed_at=r.reviewed_at.isoformat() if r.reviewed_at else None,
                    equipment_authorised=False)

    @app.get('/api/training')
    @login_required()
    def training_catalogue():
        lesson_id = request.args.get('lesson_id', LESSON['id'])
        if lesson_id not in LESSONS:
            abort(400, 'Unknown lesson version.')
        lesson = LESSONS[lesson_id]
        questions = QUESTION_BANK[lesson_id]
        with DB() as db:
            query = select(TrainingAttempt).order_by(TrainingAttempt.id.desc()).limit(200)
            if not request.employee.admin:
                query = query.where(TrainingAttempt.employee_id == request.employee.id)
            return dict(lesson=lesson, lessons=[dict(id=l['id'], title=l['title'], role=l['role']) for l in LESSONS.values()],
                        questions=[dict(id=i, text=q[0], options=q[1]) for i,q in enumerate(questions)],
                        attempts=[serialise(r, db) for r in db.scalars(query)], pass_mark=8,
                        record_limit=200, video_ready=True, narration_ready=lesson['narration_ready'])

    @app.post('/api/training/attempts')
    @login_required()
    def submit_training():
        data = request.get_json()
        answers = data.get('answers')
        if data.get('lesson_id') not in LESSONS:
            abort(400, 'Unknown lesson version.')
        if data.get('reviewed_lesson') is not True:
            abort(400, 'Review the lesson before submitting.')
        if not isinstance(answers, list) or len(answers) != 10 or any(type(a) is not int or a not in range(3) for a in answers):
            abort(400, 'Answer all ten questions.')
        lesson_id = data['lesson_id']
        questions = QUESTION_BANK[lesson_id]
        score = sum(a == q[2] for a,q in zip(answers, questions))
        with DB.begin() as db:
            row = TrainingAttempt(employee_id=request.employee.id, lesson_id=lesson_id,
                                  score=score, answers=json.dumps(answers), created_at=now())
            db.add(row); db.flush()
            result = serialise(row, db)
            result['feedback'] = [dict(question=q[0], correct= a==q[2], correct_answer=q[1][q[2]], explanation=q[3]) for a,q in zip(answers,questions)]
            return result, 201

    @app.patch('/api/training/attempts/<int:attempt_id>/review')
    @login_required(admin=True)
    def review_training(attempt_id):
        data = request.get_json()
        notes = data.get('notes')
        if data.get('result') not in ('Passed', 'Needs retraining') or not isinstance(notes, str) or not 10 <= len(notes.strip()) <= 2000:
            abort(400, 'Select a result and record practical observations (10–2000 characters).')
        if data.get('observed_practical') is not True:
            abort(400, 'Confirm that you observed the practical check.')
        with DB.begin() as db:
            row = db.scalar(select(TrainingAttempt).where(TrainingAttempt.id == attempt_id).with_for_update())
            if not row: abort(404, 'Attempt not found.')
            if row.employee_id == request.employee.id: abort(403, 'Another supervisor must review your practical check.')
            if row.score < 8: abort(400, 'Quiz pass required before practical review.')
            if row.review != 'Pending': abort(409, 'Review already recorded; create a new attempt for reassessment.')
            row.review=data['result']; row.reviewer_id=request.employee.id
            row.reviewed_at=now(); row.review_notes=notes.strip()
            db.add(AuditLog(admin_id=request.employee.id, action='training_practical_review', target=str(row.id), detail=row.review+': '+row.review_notes, created_at=now()))
            return serialise(row, db)

    return TrainingAttempt
