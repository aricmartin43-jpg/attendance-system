import base64
import csv
import io
import hashlib
import json
import math
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps
from zoneinfo import ZoneInfo

import cloudinary
import cloudinary.uploader
import numpy as np
from cryptography.fernet import Fernet
from flask import Flask, Response, abort, jsonify, render_template, request, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, ForeignKey, create_engine, select, delete, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from webauthn import (
    base64url_to_bytes, generate_authentication_options, generate_registration_options,
    options_to_json, verify_authentication_response, verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorAttachment, AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
    ResidentKeyRequirement, UserVerificationRequirement,
)

UTC = timezone.utc
LOCAL = ZoneInfo(os.getenv('TZ_NAME', 'Asia/Kolkata'))
PRODUCTION = os.getenv('APP_ENV', 'production') == 'production'
DB_URL = os.getenv('DATABASE_URL', '')
if not DB_URL:
    raise RuntimeError('DATABASE_URL must point to your Neon database.')
if DB_URL.startswith('postgres://'):
    DB_URL = DB_URL.replace('postgres://', 'postgresql://', 1)
if PRODUCTION and not DB_URL.startswith('postgresql://'):
    raise RuntimeError('Production requires PostgreSQL; local storage is disabled.')
if PRODUCTION and ('sslmode=' not in DB_URL or 'sslmode=disable' in DB_URL):
    raise RuntimeError('Use a Neon connection URL with SSL enabled.')
SECRET = os.getenv('SECRET_KEY', '')
if len(SECRET) < 32:
    raise RuntimeError('Set a random SECRET_KEY with at least 32 characters.')
CIPHER = Fernet(os.environ['FACE_ENCRYPTION_KEY'].encode())
THRESHOLD = float(os.getenv('FACE_THRESHOLD', '0.48'))
if not 0.3 <= THRESHOLD <= 0.6:
    raise RuntimeError('FACE_THRESHOLD must be between 0.3 and 0.6.')
engine = create_engine(DB_URL, pool_pre_ping=True)
DB = sessionmaker(engine, expire_on_commit=False)
cloudinary.config(secure=True)


class Base(DeclarativeBase):
    pass


class Employee(Base):
    __tablename__ = 'employees'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    department: Mapped[str] = mapped_column(String(40))
    password: Mapped[str] = mapped_column(Text)
    admin: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    encoding: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    capture_token: Mapped[str | None] = mapped_column(String(100), nullable=True)
    capture_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Attendance(Base):
    __tablename__ = 'attendance'
    __table_args__ = (UniqueConstraint('employee_id', 'work_date', name='one_shift_per_day'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), index=True)
    work_date: Mapped[str] = mapped_column(String(10), index=True)
    in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    in_lat: Mapped[float] = mapped_column(Float)
    in_lng: Mapped[float] = mapped_column(Float)
    in_accuracy: Mapped[float] = mapped_column(Float)
    out_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    out_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    out_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    in_photo: Mapped[str] = mapped_column(Text)
    out_photo: Mapped[str | None] = mapped_column(Text, nullable=True)
    in_distance: Mapped[float] = mapped_column(Float)
    out_distance: Mapped[float | None] = mapped_column(Float, nullable=True)


class WebAuthnCredential(Base):
    __tablename__ = 'webauthn_credentials'
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), index=True)
    credential_id: Mapped[str] = mapped_column(Text, unique=True)
    public_key: Mapped[str] = mapped_column(Text)
    sign_count: Mapped[int] = mapped_column(Integer, default=0)
    device_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = 'audit_log'
    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(120))
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EmployeeProfile(Base):
    __tablename__ = 'employee_profiles'
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), unique=True, index=True)
    designation: Mapped[str | None] = mapped_column(String(100), nullable=True)
    joining_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    supervisor_id: Mapped[int | None] = mapped_column(ForeignKey('employees.id'), nullable=True)
    skills: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class WorkReport(Base):
    __tablename__ = 'work_reports'
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), index=True)
    work_date: Mapped[str] = mapped_column(String(10), index=True)
    job_no: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    customer: Mapped[str | None] = mapped_column(String(120), nullable=True)
    work_details: Mapped[str] = mapped_column(Text)
    machine: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default='Completed')
    problems: Mapped[str | None] = mapped_column(Text, nullable=True)
    supervisor_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_by: Mapped[int | None] = mapped_column(ForeignKey('employees.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkIssue(Base):
    __tablename__ = 'work_issues'
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), index=True)
    work_date: Mapped[str] = mapped_column(String(10), index=True)
    category: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(160))
    detail: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default='Open')
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey('employees.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Meeting(Base):
    __tablename__ = 'meetings'
    id: Mapped[int] = mapped_column(primary_key=True)
    meeting_date: Mapped[str] = mapped_column(String(10), index=True)
    title: Mapped[str] = mapped_column(String(160))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey('employees.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MeetingAction(Base):
    __tablename__ = 'meeting_actions'
    id: Mapped[int] = mapped_column(primary_key=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey('meetings.id'), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), index=True)
    action: Mapped[str] = mapped_column(Text)
    due_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default='Open')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Customer(Base):
    __tablename__ = 'customers'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str | None] = mapped_column(String(30), unique=True, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(180), index=True)
    gstin: Mapped[str | None] = mapped_column(String(30), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(100), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    email: Mapped[str | None] = mapped_column(String(160), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default='Active')
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CustomerSite(Base):
    __tablename__ = 'customer_sites'
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey('customers.id'), index=True)
    name: Mapped[str] = mapped_column(String(140))
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(String(80), nullable=True)
    state: Mapped[str | None] = mapped_column(String(80), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class CustomerContact(Base):
    __tablename__ = 'customer_contacts'
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey('customers.id'), index=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey('customer_sites.id'), nullable=True)
    name: Mapped[str] = mapped_column(String(120))
    designation: Mapped[str | None] = mapped_column(String(120), nullable=True)
    department: Mapped[str | None] = mapped_column(String(100), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    email: Mapped[str | None] = mapped_column(String(160), nullable=True)
    primary_contact: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class MachineType(Base):
    __tablename__ = 'machine_types'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class CustomerMachine(Base):
    __tablename__ = 'customer_machines'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str | None] = mapped_column(String(30), unique=True, nullable=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey('customers.id'), index=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey('customer_sites.id'), nullable=True)
    machine_type_id: Mapped[int | None] = mapped_column(ForeignKey('machine_types.id'), nullable=True)
    customer_machine_no: Mapped[str | None] = mapped_column(String(80), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    serial_no: Mapped[str | None] = mapped_column(String(120), nullable=True)
    controller: Mapped[str | None] = mapped_column(String(120), nullable=True)
    department: Mapped[str | None] = mapped_column(String(100), nullable=True)
    location: Mapped[str | None] = mapped_column(String(140), nullable=True)
    installation_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default='Active')
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkOrder(Base):
    __tablename__ = 'work_orders'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str | None] = mapped_column(String(40), unique=True, nullable=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey('customers.id'), index=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey('customer_sites.id'), nullable=True)
    machine_id: Mapped[int | None] = mapped_column(ForeignKey('customer_machines.id'), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(180))
    work_type: Mapped[str] = mapped_column(String(60), default='Service')
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_owner_id: Mapped[int | None] = mapped_column(ForeignKey('employees.id'), nullable=True)
    supervisor_id: Mapped[int | None] = mapped_column(ForeignKey('employees.id'), nullable=True)
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey('employees.id'), nullable=True)
    start_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    target_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    completed_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default='Open')
    priority: Mapped[str] = mapped_column(String(20), default='Normal')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkOrderAssignment(Base):
    __tablename__ = 'work_order_assignments'
    __table_args__ = (UniqueConstraint('work_order_id','employee_id','role', name='unique_work_assignment'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey('work_orders.id'), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), index=True)
    role: Mapped[str] = mapped_column(String(50), default='Assigned')
    responsibility: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkReportLink(Base):
    __tablename__ = 'work_report_links'
    __table_args__ = (UniqueConstraint('work_report_id', name='one_link_per_work_report'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    work_report_id: Mapped[int] = mapped_column(ForeignKey('work_reports.id'), index=True)
    work_order_id: Mapped[int | None] = mapped_column(ForeignKey('work_orders.id'), nullable=True, index=True)
    machine_id: Mapped[int | None] = mapped_column(ForeignKey('customer_machines.id'), nullable=True, index=True)


app = Flask(__name__)
app.config.update(SECRET_KEY=SECRET, MAX_CONTENT_LENGTH=3 * 1024 * 1024,
                  SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SECURE=PRODUCTION,
                  SESSION_COOKIE_SAMESITE='Lax', PERMANENT_SESSION_LIFETIME=timedelta(hours=12))
# Render terminates HTTPS at one trusted reverse proxy. Do not expose Gunicorn directly.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
# Deliberately one Gunicorn worker: memory-backed throttles are per process.
limiter = Limiter(get_remote_address, app=app, default_limits=['200 per minute'], storage_uri='memory://')


def now():
    return datetime.now(UTC)


def aware(dt):
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def person(e):
    return dict(id=e.id, code=e.code, name=e.name, department=e.department,
                admin=e.admin, active=e.active, enrolled=bool(e.encoding))


def record(r, e):
    hours = (aware(r.out_at) - aware(r.in_at)).total_seconds() / 3600 if r.out_at else None
    return dict(id=r.id, employee=e.name, code=e.code, department=e.department, date=r.work_date,
                check_in=aware(r.in_at).isoformat(), check_out=aware(r.out_at).isoformat() if r.out_at else None,
                hours=round(hours, 2) if hours is not None else None,
                in_location=dict(lat=r.in_lat, lng=r.in_lng, accuracy=r.in_accuracy),
                out_location=dict(lat=r.out_lat, lng=r.out_lng, accuracy=r.out_accuracy) if r.out_at else None)


def login_required(admin=False):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            with DB() as db:
                e = db.get(Employee, session.get('uid', -1))
                if not e or not e.active:
                    abort(401, 'Please sign in.')
                if session.get('auth') != hashlib.sha256(e.password.encode()).hexdigest():
                    abort(401, 'Your password changed. Please sign in again.')
                if admin and not e.admin:
                    abort(403, 'Administrator access required.')
                request.employee = e
            return fn(*args, **kwargs)
        return wrapped
    return decorate


@app.before_request
def csrf():
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        token = request.headers.get('X-CSRF-Token', '')
        if not token or not secrets.compare_digest(token, session.get('csrf', '')):
            abort(403, 'Session expired. Refresh the page and try again.')


@app.after_request
def headers(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Permissions-Policy'] = 'camera=(self), geolocation=(self), microphone=()'
    response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if PRODUCTION:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    return response


@app.errorhandler(HTTPException)
def http_error(e):
    return jsonify(error=e.description), e.code


@app.errorhandler(Exception)
def unexpected(e):
    app.logger.error('Request failed (%s)', type(e).__name__)
    return jsonify(error='The request could not be saved. Please try again or contact your administrator.'), 503


@app.get('/')
def index():
    return render_template('index.html')


@app.get('/health')
@limiter.exempt
def health():
    with DB() as db:
        db.execute(select(Employee.id).limit(1))
    return {'status': 'ok'}


@app.get('/api/session')
def current_session():
    session.setdefault('csrf', secrets.token_urlsafe(32))
    with DB() as db:
        e = db.get(Employee, session.get('uid', -1))
        valid = e and e.active and session.get('auth') == hashlib.sha256(e.password.encode()).hexdigest()
        return dict(csrf=session['csrf'], user=person(e) if valid else None,
                    timezone=str(LOCAL), today=now().astimezone(LOCAL).date().isoformat())


@app.post('/api/login')
@limiter.limit('5 per minute; 30 per hour')
def login():
    data = request.get_json() or {}
    code = str(data.get('code', '')).strip().lower()
    secret = str(data.get('pin', data.get('password', '')))
    with DB() as db:
        e = db.scalar(select(Employee).where(Employee.code == code))
        if e and not e.admin and not re.fullmatch(r'\d{4}', secret):
            valid = False
        else:
            valid = check_password_hash(e.password if e else DUMMY_PASSWORD, secret)
        if not e or not e.active or not valid:
            abort(401, 'Employee ID or PIN is incorrect.' if not (e and e.admin) else 'Administrator ID or password is incorrect.')
        session.clear()
        session.update(uid=e.id, csrf=secrets.token_urlsafe(32), auth=hashlib.sha256(e.password.encode()).hexdigest())
        session.permanent = True
        return dict(user=person(e), csrf=session['csrf'])


DUMMY_PASSWORD = generate_password_hash(secrets.token_urlsafe(32))


def rp_settings():
    host = request.host.split(':', 1)[0]
    return host, request.host_url.rstrip('/')


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


@app.post('/api/biometric/register/options')
@login_required()
def biometric_register_options():
    rp_id, _ = rp_settings()
    with DB() as db:
        existing = [PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credential_id))
                    for c in db.scalars(select(WebAuthnCredential).where(WebAuthnCredential.employee_id == request.employee.id))]
    options = generate_registration_options(
        rp_id=rp_id, rp_name='Cosmos Engineering Solutions',
        user_id=str(request.employee.id).encode(), user_name=request.employee.code,
        user_display_name=request.employee.name, exclude_credentials=existing,
        authenticator_selection=AuthenticatorSelectionCriteria(
            authenticator_attachment=AuthenticatorAttachment.PLATFORM,
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    session['webauthn_register_challenge'] = b64(options.challenge)
    return app.response_class(options_to_json(options), mimetype='application/json')


@app.post('/api/biometric/register/verify')
@login_required()
def biometric_register_verify():
    challenge = session.pop('webauthn_register_challenge', None)
    if not challenge:
        abort(400, 'Biometric registration expired. Start again.')
    rp_id, origin = rp_settings()
    try:
        result = verify_registration_response(
            credential=request.get_json(), expected_challenge=base64url_to_bytes(challenge),
            expected_origin=origin, expected_rp_id=rp_id, require_user_verification=True,
        )
    except Exception:
        abort(400, 'Biometric registration could not be verified.')
    cid=b64(result.credential_id)
    with DB.begin() as db:
        if db.scalar(select(WebAuthnCredential.id).where(WebAuthnCredential.credential_id==cid)):
            abort(409, 'This biometric credential is already registered.')
        db.add(WebAuthnCredential(employee_id=request.employee.id, credential_id=cid,
                                  public_key=b64(result.credential_public_key), sign_count=result.sign_count,
                                  device_name=str((request.get_json() or {}).get('deviceName',''))[:100] or None,
                                  created_at=now()))
    return {'ok': True}


@app.post('/api/biometric/login/options')
def biometric_login_options():
    data=request.get_json() or {}
    code=str(data.get('code','')).strip().lower()
    with DB() as db:
        e=db.scalar(select(Employee).where(Employee.code==code, Employee.active==True))
        if not e:
            abort(401, 'Employee ID is incorrect.')
        creds=db.scalars(select(WebAuthnCredential).where(WebAuthnCredential.employee_id==e.id)).all()
        if not creds:
            abort(404, 'No biometric login is registered for this employee.')
    rp_id,_=rp_settings()
    options=generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credential_id)) for c in creds],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    session['webauthn_login_challenge']=b64(options.challenge)
    session['webauthn_login_employee']=e.id
    return app.response_class(options_to_json(options), mimetype='application/json')


@app.post('/api/biometric/login/verify')
def biometric_login_verify():
    challenge=session.pop('webauthn_login_challenge',None)
    employee_id=session.pop('webauthn_login_employee',None)
    if not challenge or not employee_id:
        abort(400, 'Biometric login expired. Start again.')
    data=request.get_json() or {}
    credential_id=str(data.get('id',''))
    with DB.begin() as db:
        e=db.get(Employee, employee_id)
        cred=db.scalar(select(WebAuthnCredential).where(WebAuthnCredential.employee_id==employee_id,
                                                        WebAuthnCredential.credential_id==credential_id))
        if not e or not e.active or not cred:
            abort(401, 'Biometric credential was not recognised.')
        rp_id,origin=rp_settings()
        try:
            result=verify_authentication_response(
                credential=data, expected_challenge=base64url_to_bytes(challenge), expected_rp_id=rp_id,
                expected_origin=origin, credential_public_key=base64url_to_bytes(cred.public_key),
                credential_current_sign_count=cred.sign_count, require_user_verification=True,
            )
        except Exception:
            abort(401, 'Biometric verification failed.')
        cred.sign_count=result.new_sign_count
        auth_hash=hashlib.sha256(e.password.encode()).hexdigest()
    session.clear()
    session.update(uid=e.id, csrf=secrets.token_urlsafe(32), auth=auth_hash)
    session.permanent=True
    return dict(user=person(e), csrf=session['csrf'])


@app.post('/api/logout')
def logout():
    session.clear()
    return {'ok': True}


def clean_text(data, key, limit):
    value = str(data.get(key, '')).strip()
    if not value or len(value) > limit:
        abort(400, f'{key.replace("_", " ").title()} must be between 1 and {limit} characters.')
    return value


@app.get('/api/employees')
@login_required(admin=True)
def employees():
    with DB() as db:
        rows=[]
        for e in db.scalars(select(Employee).where(Employee.admin == False).order_by(Employee.name)):
            item=person(e)
            item['biometric_registered']=bool(db.scalar(select(WebAuthnCredential.id).where(WebAuthnCredential.employee_id==e.id).limit(1)))
            rows.append(item)
        return jsonify(rows)


@app.post('/api/employees')
@login_required(admin=True)
def add_employee():
    data = request.get_json()
    code = clean_text(data, 'code', 40).lower()
    if not re.fullmatch(r'[a-z0-9_-]+', code):
        abort(400, 'Employee ID may contain letters, numbers, hyphens and underscores.')
    pin = clean_text(data, 'pin', 4)
    if not re.fullmatch(r'\d{4}', pin):
        abort(400, 'PIN must be exactly 4 digits.')
    with DB.begin() as db:
        if db.scalar(select(Employee).where(Employee.code == code)):
            abort(409, 'That employee ID already exists.')
        e = Employee(code=code, name=clean_text(data, 'name', 100),
                     department=clean_text(data, 'department', 40), password=generate_password_hash(pin))
        db.add(e)
        db.flush()
        return person(e), 201


@app.patch('/api/employees/<int:employee_id>')
@login_required(admin=True)
def edit_employee(employee_id):
    data = request.get_json() or {}
    with DB.begin() as db:
        e = db.get(Employee, employee_id)
        if not e or e.admin:
            abort(404)
        code = str(data.get('code', e.code)).strip().lower()
        if not re.fullmatch(r'[a-z0-9_-]+', code):
            abort(400, 'Employee ID may contain letters, numbers, hyphens and underscores.')
        clash = db.scalar(select(Employee).where(Employee.code == code, Employee.id != e.id))
        if clash:
            abort(409, 'That employee ID already exists.')
        e.code = code
        e.name = str(data.get('name', e.name)).strip()[:100] or e.name
        e.department = str(data.get('department', e.department)).strip()[:40] or e.department
        db.add(AuditLog(admin_id=request.employee.id, action='edit_employee', target=e.code,
                       detail=json.dumps({'name': e.name, 'department': e.department}), created_at=now()))
        return person(e)


@app.post('/api/employees/<int:employee_id>/reset-pin')
@login_required(admin=True)
def reset_pin(employee_id):
    data = request.get_json() or {}
    pin = str(data.get('pin', ''))
    if not re.fullmatch(r'\d{4}', pin):
        abort(400, 'PIN must be exactly 4 digits.')
    with DB.begin() as db:
        e = db.get(Employee, employee_id)
        if not e or e.admin:
            abort(404)
        e.password = generate_password_hash(pin)
        db.add(AuditLog(admin_id=request.employee.id, action='reset_pin', target=e.code, detail=None, created_at=now()))
    return {'ok': True}


@app.post('/api/employees/<int:employee_id>/reset-face')
@login_required(admin=True)
def reset_face(employee_id):
    old=None
    with DB.begin() as db:
        e=db.get(Employee,employee_id)
        if not e or e.admin:
            abort(404)
        old=e.photo_id
        e.photo_id=None
        e.encoding=None
        e.consent_at=None
        db.add(AuditLog(admin_id=request.employee.id, action='reset_face', target=e.code, detail=None, created_at=now()))
    remove_photo(old)
    return {'ok':True}


@app.post('/api/employees/<int:employee_id>/reset-biometric')
@login_required(admin=True)
def reset_biometric(employee_id):
    with DB.begin() as db:
        e = db.get(Employee, employee_id)
        if not e or e.admin:
            abort(404)
        db.execute(delete(WebAuthnCredential).where(WebAuthnCredential.employee_id == e.id))
        db.add(AuditLog(admin_id=request.employee.id, action='reset_biometric', target=e.code, detail=None, created_at=now()))
    return {'ok': True}


@app.delete('/api/employees/<int:employee_id>')
@login_required(admin=True)
def delete_employee(employee_id):
    photos=[]
    with DB.begin() as db:
        e = db.get(Employee, employee_id)
        if not e or e.admin:
            abort(404)
        has_memory = (
            db.scalar(select(WorkReport.id).where(WorkReport.employee_id == e.id).limit(1))
            or db.scalar(select(WorkIssue.id).where(WorkIssue.employee_id == e.id).limit(1))
            or db.scalar(select(MeetingAction.id).where(MeetingAction.employee_id == e.id).limit(1))
        )
        if has_memory:
            abort(409, 'This employee has company-memory records. Deactivate the account instead of permanently removing it.')
        if db.scalar(select(Attendance.id).where(Attendance.employee_id == e.id, Attendance.out_at == None)):
            abort(409, 'This employee must check out before removal.')
        rows=db.scalars(select(Attendance).where(Attendance.employee_id == e.id)).all()
        for r in rows:
            photos.extend([r.in_photo, r.out_photo])
        photos.append(e.photo_id)
        db.execute(delete(WebAuthnCredential).where(WebAuthnCredential.employee_id == e.id))
        db.execute(delete(Attendance).where(Attendance.employee_id == e.id))
        db.delete(e)
        db.add(AuditLog(admin_id=request.employee.id, action='delete_employee', target=e.code, detail=None, created_at=now()))
    for photo in photos:
        remove_photo(photo)
    return {'ok': True}


@app.post('/api/employees/<int:employee_id>/active')
@login_required(admin=True)
def active_employee(employee_id):
    data = request.get_json()
    if type(data.get('active')) is not bool:
        abort(400, 'Choose an active status.')
    with DB.begin() as db:
        e = db.get(Employee, employee_id)
        if not e or e.admin:
            abort(404)
        if not data['active'] and db.scalar(select(Attendance.id).where(Attendance.employee_id == e.id, Attendance.out_at == None)):
            abort(409, 'This employee must check out before deactivation.')
        e.active = data['active']
        return person(e)


def face_image(value):
    if not isinstance(value, str) or not value.startswith('data:image/jpeg;base64,'):
        abort(400, 'Take a fresh camera photo.')
    try:
        raw = base64.b64decode(value.split(',', 1)[1], validate=True)
        if len(raw) > 1500000:
            abort(400, 'Photo is too large.')
        img = Image.open(io.BytesIO(raw))
        if img.width * img.height > 2000000:
            abort(400, 'Photo dimensions are too large.')
        img = ImageOps.exif_transpose(img).convert('RGB')
        img.thumbnail((800, 800))
        import face_recognition
        pixels = np.array(img)
        locations = face_recognition.face_locations(pixels, model='hog')
        if len(locations) != 1:
            abort(400, 'Exactly one face must be visible. Face the camera in good light.')
        encoding = face_recognition.face_encodings(pixels, locations)[0]
        output = io.BytesIO()
        img.save(output, format='JPEG', quality=85)
        return encoding, output.getvalue()
    except (ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        abort(400, 'Invalid photo. Please take another photo.')


def upload_photo(raw):
    if not os.getenv('CLOUDINARY_URL'):
        abort(503, 'Photo storage is not configured. Contact your administrator.')
    # Private delivery: never persist or return a public image URL.
    result = cloudinary.uploader.upload(io.BytesIO(raw), resource_type='image', type='authenticated',
                                        folder='cosmos-attendance', public_id=secrets.token_hex(16), overwrite=False)
    return result['public_id']


def remove_photo(public_id):
    if public_id:
        try:
            cloudinary.uploader.destroy(public_id, type='authenticated', invalidate=True)
        except Exception:
            app.logger.warning('Cloudinary cleanup pending for asset %s', public_id)


@app.post('/api/employees/<int:employee_id>/enrol')
@login_required(admin=True)
@limiter.limit('10 per minute')
def enrol(employee_id):
    data = request.get_json()
    if data.get('consent') is not True:
        abort(400, 'Record the employee’s consent before enrolment.')
    with DB() as db:
        e = db.get(Employee, employee_id)
        if not e or e.admin or not e.active:
            abort(404)
    encoding, raw = face_image(data.get('photo'))
    photo = upload_photo(raw)
    old = None
    try:
        with DB.begin() as db:
            e = db.scalar(select(Employee).where(Employee.id == employee_id).with_for_update())
            if not e or not e.active:
                abort(409, 'Employee is no longer active.')
            old = e.photo_id
            e.photo_id = photo
            e.encoding = CIPHER.encrypt(json.dumps(encoding.tolist()).encode()).decode()
            e.consent_at = now()
    except Exception:
        remove_photo(photo)
        raise
    remove_photo(old)
    return {'ok': True}


def gps(data):
    try:
        lat, lng, accuracy = float(data['lat']), float(data['lng']), float(data['accuracy'])
        age = abs(now().timestamp() * 1000 - float(data['timestamp']))
        if not all(math.isfinite(x) for x in (lat, lng, accuracy, age)):
            raise ValueError()
        if not (-90 <= lat <= 90 and -180 <= lng <= 180 and 0 <= accuracy <= 10000 and age <= 120000):
            raise ValueError()
        return lat, lng, accuracy
    except (KeyError, ValueError, TypeError):
        abort(400, 'A fresh GPS location is required. Allow location access and try again.')


@app.post('/api/attendance')
@login_required()
@limiter.limit('6 per minute')
def mark_attendance():
    data = request.get_json()
    action = data.get('action')
    if action not in ('in', 'out'):
        abort(400, 'Choose check-in or check-out.')
    lat, lng, accuracy = gps(data.get('location', {}))
    if request.employee.admin or not request.employee.encoding:
        abort(403, 'Ask your administrator to enrol your face first.')
    # Validate again under the employee row lock before committing attendance.
    if not request.employee.capture_token or data.get('challenge') != request.employee.capture_token or (now() - aware(request.employee.capture_at)).total_seconds() > 120:
        abort(400, 'Camera session expired. Start the camera again.')
    encoding, raw = face_image(data.get('photo'))
    stored = np.array(json.loads(CIPHER.decrypt(request.employee.encoding.encode())))
    distance = float(np.linalg.norm(stored - encoding))
    if not math.isfinite(distance) or distance > THRESHOLD:
        abort(403, 'Face did not match your registered photo. Try better lighting or contact your administrator.')
    photo = None
    try:
        with DB.begin() as db:
            e = db.scalar(select(Employee).where(Employee.id == request.employee.id).with_for_update())
            if not e.active or e.encoding != request.employee.encoding:
                abort(409, 'Your employee profile changed. Sign in again.')
            if not e.capture_token or not secrets.compare_digest(str(data.get('challenge', '')), e.capture_token) or (now() - aware(e.capture_at)).total_seconds() > 120:
                abort(409, 'This camera session expired or was already used. Try again.')
            open_record = db.scalar(select(Attendance).where(Attendance.employee_id == e.id, Attendance.out_at == None))
            stamp = now()
            day = stamp.astimezone(LOCAL).date().isoformat()
            if action == 'in':
                if open_record:
                    abort(409, 'You are already checked in. Check out first.')
                if db.scalar(select(Attendance.id).where(Attendance.employee_id == e.id, Attendance.work_date == day)):
                    abort(409, 'Attendance is complete for today. One shift per day is supported.')
            elif not open_record:
                abort(409, 'You do not have an open check-in.')
            photo = upload_photo(raw)
            e.capture_token, e.capture_at = None, None
            if action == 'in':
                r = Attendance(employee_id=e.id, work_date=day, in_at=stamp, in_lat=lat, in_lng=lng,
                               in_accuracy=accuracy, in_photo=photo, in_distance=distance)
                db.add(r)
            else:
                r = open_record
                r.out_at, r.out_lat, r.out_lng, r.out_accuracy = stamp, lat, lng, accuracy
                r.out_photo, r.out_distance = photo, distance
            db.flush()
            result = record(r, e)
    except Exception:
        remove_photo(photo)
        raise
    return result, 201


@app.post('/api/capture')
@login_required()
def capture_challenge():
    token = secrets.token_urlsafe(32)
    with DB.begin() as db:
        e = db.scalar(select(Employee).where(Employee.id == request.employee.id).with_for_update())
        e.capture_token, e.capture_at = token, now()
    return {'challenge': token}


def filtered_records(db):
    query = select(Attendance, Employee).join(Employee, Employee.id == Attendance.employee_id)
    if not request.employee.admin:
        query = query.where(Employee.id == request.employee.id)
    month = request.args.get('month')
    day = request.args.get('date')
    if month:
        try:
            datetime.strptime(month, '%Y-%m')
            if len(month) != 7:
                raise ValueError()
        except ValueError:
            abort(400, 'Choose a valid month.')
        query = query.where(Attendance.work_date.startswith(month + '-'))
    elif day:
        try:
            datetime.strptime(day, '%Y-%m-%d')
        except ValueError:
            abort(400, 'Choose a valid date.')
        query = query.where(Attendance.work_date == day)
    else:
        query = query.where(Attendance.work_date == now().astimezone(LOCAL).date().isoformat())
    return db.execute(query.order_by(Attendance.in_at.desc())).all()


@app.get('/api/attendance')
@login_required()
def get_attendance():
    with DB() as db:
        return jsonify([record(r, e) for r, e in filtered_records(db)])


@app.get('/api/my-status')
@login_required()
def my_status():
    with DB() as db:
        r = db.scalar(select(Attendance).where(Attendance.employee_id == request.employee.id, Attendance.out_at == None))
        return dict(open_shift=record(r, request.employee) if r else None)


@app.patch('/api/attendance/<int:attendance_id>')
@login_required(admin=True)
def edit_attendance(attendance_id):
    data=request.get_json() or {}
    with DB.begin() as db:
        r=db.get(Attendance, attendance_id)
        if not r:
            abort(404)
        e=db.get(Employee, r.employee_id)
        for key, attr in [('check_in','in_at'),('check_out','out_at')]:
            if key in data:
                value=data.get(key)
                if value in (None,'') and key=='check_out':
                    setattr(r, attr, None)
                else:
                    try:
                        dt=datetime.fromisoformat(str(value))
                        if dt.tzinfo is None:
                            dt=dt.replace(tzinfo=LOCAL)
                        setattr(r, attr, dt.astimezone(UTC))
                    except ValueError:
                        abort(400, f'Invalid {key.replace("_"," ")} time.')
        if r.out_at and aware(r.out_at) < aware(r.in_at):
            abort(400, 'Check-out cannot be before check-in.')
        db.add(AuditLog(admin_id=request.employee.id, action='edit_attendance', target=f'{e.code}:{r.work_date}',
                       detail=json.dumps({'check_in':data.get('check_in'),'check_out':data.get('check_out')}), created_at=now()))
        return record(r,e)


@app.delete('/api/attendance/<int:attendance_id>')
@login_required(admin=True)
def delete_attendance(attendance_id):
    photos=[]
    with DB.begin() as db:
        r=db.get(Attendance, attendance_id)
        if not r:
            abort(404)
        e=db.get(Employee,r.employee_id)
        photos=[r.in_photo,r.out_photo]
        db.delete(r)
        db.add(AuditLog(admin_id=request.employee.id, action='delete_attendance', target=f'{e.code}:{r.work_date}', detail=None, created_at=now()))
    for photo in photos:
        remove_photo(photo)
    return {'ok':True}


@app.get('/api/audit')
@login_required(admin=True)
def audit():
    with DB() as db:
        rows=db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(250)).all()
        return jsonify([dict(id=x.id,action=x.action,target=x.target,detail=x.detail,created_at=aware(x.created_at).isoformat()) for x in rows])


def valid_iso_date(value, field='date'):
    try:
        datetime.strptime(str(value), '%Y-%m-%d')
        return str(value)
    except (ValueError, TypeError):
        abort(400, f'Choose a valid {field}.')


def visible_employee_id(data):
    if request.employee.admin and data.get('employee_id') is not None:
        try:
            employee_id = int(data['employee_id'])
        except (TypeError, ValueError):
            abort(400, 'Choose a valid employee.')
        with DB() as db:
            if not db.get(Employee, employee_id):
                abort(404, 'Employee not found.')
        return employee_id
    return request.employee.id


def work_report_json(r, e):
    return dict(id=r.id, employee_id=e.id, employee=e.name, code=e.code, department=e.department,
                date=r.work_date, job_no=r.job_no, customer=r.customer, work_details=r.work_details,
                machine=r.machine, status=r.status, problems=r.problems, supervisor_note=r.supervisor_note,
                verified_by=r.verified_by, created_at=aware(r.created_at).isoformat())


def issue_json(r, e):
    return dict(id=r.id, employee_id=e.id, employee=e.name, code=e.code, department=e.department,
                date=r.work_date, category=r.category, title=r.title, detail=r.detail, status=r.status,
                resolution=r.resolution, assigned_to=r.assigned_to, created_at=aware(r.created_at).isoformat())


@app.get('/api/ecosystem/summary')
@login_required()
def ecosystem_summary():
    with DB() as db:
        work_q = select(WorkReport)
        issue_q = select(WorkIssue)
        action_q = select(MeetingAction)
        if not request.employee.admin:
            work_q = work_q.where(WorkReport.employee_id == request.employee.id)
            issue_q = issue_q.where(WorkIssue.employee_id == request.employee.id)
            action_q = action_q.where(MeetingAction.employee_id == request.employee.id)
        work = db.scalars(work_q).all()
        issues = db.scalars(issue_q).all()
        actions = db.scalars(action_q).all()
        return dict(
            work_reports=len(work),
            completed_work=sum(1 for x in work if x.status.lower() == 'completed'),
            open_issues=sum(1 for x in issues if x.status.lower() not in ('resolved','closed')),
            open_meeting_actions=sum(1 for x in actions if x.status.lower() not in ('completed','closed')),
        )


@app.get('/api/work-reports')
@login_required()
def list_work_reports():
    with DB() as db:
        q = select(WorkReport, Employee).join(Employee, Employee.id == WorkReport.employee_id)
        if not request.employee.admin:
            q = q.where(WorkReport.employee_id == request.employee.id)
        rows = db.execute(q.order_by(WorkReport.work_date.desc(), WorkReport.id.desc()).limit(250)).all()
        return jsonify([work_report_json(r, e) for r, e in rows])


@app.post('/api/work-reports')
@login_required()
def create_work_report():
    data = request.get_json() or {}
    employee_id = visible_employee_id(data)
    work_date = valid_iso_date(data.get('date') or now().astimezone(LOCAL).date().isoformat(), 'work date')
    details = str(data.get('work_details','')).strip()
    if not details:
        abort(400, 'Work details are required.')
    status = str(data.get('status','Completed')).strip()[:30] or 'Completed'
    with DB.begin() as db:
        r = WorkReport(employee_id=employee_id, work_date=work_date,
                       job_no=str(data.get('job_no','')).strip()[:60] or None,
                       customer=str(data.get('customer','')).strip()[:120] or None,
                       work_details=details[:5000],
                       machine=str(data.get('machine','')).strip()[:120] or None,
                       status=status,
                       problems=str(data.get('problems','')).strip()[:5000] or None,
                       created_at=now())
        db.add(r); db.flush()
        link_order = data.get('work_order_id')
        link_machine = data.get('machine_id')
        work_order_id = int(link_order) if link_order not in (None,'') else None
        machine_id = int(link_machine) if link_machine not in (None,'') else None
        work_order = db.get(WorkOrder, work_order_id) if work_order_id else None
        machine = db.get(CustomerMachine, machine_id) if machine_id else None
        if work_order_id and not work_order:
            abort(404, 'Work order not found.')
        if machine_id and not machine:
            abort(404, 'Machine not found.')
        if not request.employee.admin:
            if work_order_id and not db.scalar(select(WorkOrderAssignment.id).where(
                    WorkOrderAssignment.work_order_id==work_order_id,
                    WorkOrderAssignment.employee_id==employee_id).limit(1)):
                abort(403, 'You can only link work reports to your assigned work orders.')
            if machine_id and not db.scalar(select(WorkOrderAssignment.id).join(
                    WorkOrder, WorkOrder.id==WorkOrderAssignment.work_order_id).where(
                    WorkOrderAssignment.employee_id==employee_id,
                    WorkOrder.machine_id==machine_id).limit(1)):
                abort(403, 'You can only link work reports to machines assigned to your work.')
        if work_order_id and machine_id and work_order.machine_id and work_order.machine_id != machine_id:
            abort(400, 'Selected machine does not match the selected work order.')
        if work_order_id or machine_id:
            db.add(WorkReportLink(work_report_id=r.id, work_order_id=work_order_id, machine_id=machine_id))
        e = db.get(Employee, employee_id)
        db.add(AuditLog(admin_id=request.employee.id if request.employee.admin else None,
                       action='create_work_report', target=f'{e.code}:{r.id}', detail=r.job_no, created_at=now()))
        return work_report_json(r, e), 201


@app.patch('/api/work-reports/<int:report_id>')
@login_required(admin=True)
def update_work_report(report_id):
    data = request.get_json() or {}
    with DB.begin() as db:
        r = db.get(WorkReport, report_id)
        if not r: abort(404)
        if 'status' in data: r.status = str(data['status']).strip()[:30] or r.status
        if 'supervisor_note' in data: r.supervisor_note = str(data['supervisor_note']).strip()[:5000] or None
        if data.get('verify') is True: r.verified_by = request.employee.id
        e = db.get(Employee, r.employee_id)
        db.add(AuditLog(admin_id=request.employee.id, action='review_work_report',
                       target=f'{e.code}:{r.id}', detail=json.dumps({'status':r.status,'verified':bool(r.verified_by)}), created_at=now()))
        return work_report_json(r, e)


@app.get('/api/issues')
@login_required()
def list_issues():
    with DB() as db:
        q = select(WorkIssue, Employee).join(Employee, Employee.id == WorkIssue.employee_id)
        if not request.employee.admin:
            q = q.where(WorkIssue.employee_id == request.employee.id)
        rows = db.execute(q.order_by(WorkIssue.work_date.desc(), WorkIssue.id.desc()).limit(250)).all()
        return jsonify([issue_json(r, e) for r, e in rows])


@app.post('/api/issues')
@login_required()
def create_issue():
    data = request.get_json() or {}
    employee_id = visible_employee_id(data)
    title = str(data.get('title','')).strip()
    detail = str(data.get('detail','')).strip()
    if not title or not detail:
        abort(400, 'Issue title and details are required.')
    category = str(data.get('category','Other')).strip()[:40] or 'Other'
    work_date = valid_iso_date(data.get('date') or now().astimezone(LOCAL).date().isoformat(), 'issue date')
    with DB.begin() as db:
        r = WorkIssue(employee_id=employee_id, work_date=work_date, category=category,
                      title=title[:160], detail=detail[:5000], status='Open', created_at=now())
        db.add(r); db.flush()
        e = db.get(Employee, employee_id)
        db.add(AuditLog(admin_id=request.employee.id if request.employee.admin else None,
                       action='create_work_issue', target=f'{e.code}:{r.id}', detail=category, created_at=now()))
        return issue_json(r, e), 201


@app.patch('/api/issues/<int:issue_id>')
@login_required(admin=True)
def update_issue(issue_id):
    data = request.get_json() or {}
    with DB.begin() as db:
        r = db.get(WorkIssue, issue_id)
        if not r: abort(404)
        if 'status' in data: r.status = str(data['status']).strip()[:30] or r.status
        if 'resolution' in data: r.resolution = str(data['resolution']).strip()[:5000] or None
        if 'assigned_to' in data:
            r.assigned_to = int(data['assigned_to']) if data['assigned_to'] not in (None,'') else None
        e = db.get(Employee, r.employee_id)
        db.add(AuditLog(admin_id=request.employee.id, action='update_work_issue',
                       target=f'{e.code}:{r.id}', detail=r.status, created_at=now()))
        return issue_json(r, e)


@app.get('/api/meetings')
@login_required()
def list_meetings():
    with DB() as db:
        if request.employee.admin:
            meetings = db.scalars(select(Meeting).order_by(Meeting.meeting_date.desc(), Meeting.id.desc()).limit(100)).all()
        else:
            meeting_ids = select(MeetingAction.meeting_id).where(MeetingAction.employee_id == request.employee.id)
            meetings = db.scalars(select(Meeting).where(Meeting.id.in_(meeting_ids)).order_by(Meeting.meeting_date.desc()).limit(100)).all()
        out=[]
        for m in meetings:
            actions_q=select(MeetingAction, Employee).join(Employee, Employee.id==MeetingAction.employee_id).where(MeetingAction.meeting_id==m.id)
            if not request.employee.admin:
                actions_q=actions_q.where(MeetingAction.employee_id==request.employee.id)
            actions=db.execute(actions_q.order_by(MeetingAction.id)).all()
            out.append(dict(id=m.id,date=m.meeting_date,title=m.title,notes=m.notes,created_by=m.created_by,
                            actions=[dict(id=a.id,employee_id=e.id,employee=e.name,code=e.code,action=a.action,
                                          due_date=a.due_date,status=a.status) for a,e in actions]))
        return jsonify(out)


@app.post('/api/meetings')
@login_required(admin=True)
def create_meeting():
    data = request.get_json() or {}
    title = str(data.get('title','')).strip()
    if not title: abort(400, 'Meeting title is required.')
    meeting_date = valid_iso_date(data.get('date') or now().astimezone(LOCAL).date().isoformat(), 'meeting date')
    actions = data.get('actions') or []
    with DB.begin() as db:
        m=Meeting(meeting_date=meeting_date,title=title[:160],notes=str(data.get('notes','')).strip()[:8000] or None,
                  created_by=request.employee.id,created_at=now())
        db.add(m); db.flush()
        for item in actions[:50]:
            try: employee_id=int(item.get('employee_id'))
            except (TypeError,ValueError): abort(400,'Each meeting action needs an employee.')
            if not db.get(Employee, employee_id): abort(404,'Employee for meeting action not found.')
            action=str(item.get('action','')).strip()
            if not action: abort(400,'Meeting action cannot be blank.')
            due=item.get('due_date')
            due=valid_iso_date(due,'due date') if due else None
            db.add(MeetingAction(meeting_id=m.id,employee_id=employee_id,action=action[:5000],due_date=due,status='Open',created_at=now()))
        db.add(AuditLog(admin_id=request.employee.id,action='create_meeting',target=f'meeting:{m.id}',detail=m.title,created_at=now()))
        return {'id':m.id,'ok':True},201


@app.patch('/api/meeting-actions/<int:action_id>')
@login_required()
def update_meeting_action(action_id):
    data=request.get_json() or {}
    with DB.begin() as db:
        a=db.get(MeetingAction,action_id)
        if not a: abort(404)
        if not request.employee.admin and a.employee_id!=request.employee.id: abort(403)
        status=str(data.get('status','')).strip()[:30]
        if status not in ('Open','In Progress','Completed','Closed'):
            abort(400,'Choose a valid action status.')
        a.status=status
        db.add(AuditLog(admin_id=request.employee.id if request.employee.admin else None,
                       action='update_meeting_action',target=f'action:{a.id}',detail=status,created_at=now()))
        return {'id':a.id,'status':a.status}


def customer_json(c):
    return dict(id=c.id, code=c.code, name=c.name, gstin=c.gstin, industry=c.industry,
                phone=c.phone, email=c.email, address=c.address, status=c.status, notes=c.notes)


def machine_json(m, customer=None, machine_type=None):
    return dict(id=m.id, code=m.code, customer_id=m.customer_id,
                customer=customer.name if customer else None, site_id=m.site_id,
                machine_type_id=m.machine_type_id, machine_type=machine_type.name if machine_type else None,
                customer_machine_no=m.customer_machine_no, manufacturer=m.manufacturer, model=m.model,
                serial_no=m.serial_no, controller=m.controller, department=m.department, location=m.location,
                installation_date=m.installation_date, status=m.status, notes=m.notes)


def work_order_json(db, w):
    customer=db.get(Customer,w.customer_id)
    machine=db.get(CustomerMachine,w.machine_id) if w.machine_id else None
    assignments=db.execute(select(WorkOrderAssignment,Employee).join(Employee,Employee.id==WorkOrderAssignment.employee_id)
                           .where(WorkOrderAssignment.work_order_id==w.id).order_by(WorkOrderAssignment.id)).all()
    def emp_name(emp_id):
        e=db.get(Employee,emp_id) if emp_id else None
        return dict(id=e.id,name=e.name,code=e.code) if e else None
    return dict(id=w.id, code=w.code, customer_id=w.customer_id, customer=customer.name if customer else None,
                machine_id=w.machine_id, machine_code=machine.code if machine else None,
                machine_no=machine.customer_machine_no if machine else None, title=w.title, work_type=w.work_type,
                description=w.description, job_owner=emp_name(w.job_owner_id), supervisor=emp_name(w.supervisor_id),
                approved_by=emp_name(w.approved_by_id), start_date=w.start_date, target_date=w.target_date,
                completed_date=w.completed_date, status=w.status, priority=w.priority,
                assignments=[dict(id=a.id,employee_id=e.id,employee=e.name,code=e.code,role=a.role,
                                  responsibility=a.responsibility) for a,e in assignments])


@app.get('/api/customers')
@login_required()
def list_customers():
    with DB() as db:
        q=select(Customer).order_by(Customer.name)
        if not request.employee.admin:
            assigned_orders=select(WorkOrderAssignment.work_order_id).where(WorkOrderAssignment.employee_id==request.employee.id)
            customer_ids=select(WorkOrder.customer_id).where(WorkOrder.id.in_(assigned_orders))
            q=q.where(Customer.id.in_(customer_ids))
        rows=db.scalars(q).all()
        out=[]
        for c in rows:
            machines=db.scalars(select(CustomerMachine).where(CustomerMachine.customer_id==c.id)).all()
            contacts=db.scalars(select(CustomerContact).where(CustomerContact.customer_id==c.id)).all()
            open_jobs=db.scalars(select(WorkOrder).where(WorkOrder.customer_id==c.id,
                              WorkOrder.status.notin_(['Completed','Closed','Cancelled']))).all()
            item=customer_json(c)
            item.update(machine_count=len(machines),contact_count=len(contacts),open_jobs=len(open_jobs))
            out.append(item)
        return jsonify(out)


@app.post('/api/customers')
@login_required(admin=True)
def create_customer():
    data=request.get_json() or {}
    name=clean_text(data,'name',180)
    with DB.begin() as db:
        c=Customer(name=name,gstin=str(data.get('gstin','')).strip()[:30] or None,
                   industry=str(data.get('industry','')).strip()[:100] or None,
                   phone=str(data.get('phone','')).strip()[:40] or None,
                   email=str(data.get('email','')).strip()[:160] or None,
                   address=str(data.get('address','')).strip()[:5000] or None,
                   status='Active',notes=str(data.get('notes','')).strip()[:5000] or None,created_at=now())
        db.add(c);db.flush();c.code=f'CUS-{c.id:04d}'
        db.add(AuditLog(admin_id=request.employee.id,action='create_customer',target=c.code,detail=c.name,created_at=now()))
        return customer_json(c),201


@app.get('/api/customers/<int:customer_id>')
@login_required()
def customer_detail(customer_id):
    with DB() as db:
        c=db.get(Customer,customer_id)
        if not c: abort(404,'Customer not found.')
        if not request.employee.admin:
            allowed=db.scalar(select(WorkOrderAssignment.id).join(WorkOrder,WorkOrder.id==WorkOrderAssignment.work_order_id)
                              .where(WorkOrderAssignment.employee_id==request.employee.id,WorkOrder.customer_id==customer_id).limit(1))
            if not allowed: abort(403,'This customer is not linked to your assigned work.')
        contacts=db.scalars(select(CustomerContact).where(CustomerContact.customer_id==c.id)
                           .order_by(CustomerContact.primary_contact.desc(),CustomerContact.name)).all()
        sites=db.scalars(select(CustomerSite).where(CustomerSite.customer_id==c.id).order_by(CustomerSite.name)).all()
        machines=[]
        for m in db.scalars(select(CustomerMachine).where(CustomerMachine.customer_id==c.id).order_by(CustomerMachine.id.desc())):
            machines.append(machine_json(m,c,db.get(MachineType,m.machine_type_id) if m.machine_type_id else None))
        jobs=[work_order_json(db,w) for w in db.scalars(select(WorkOrder).where(WorkOrder.customer_id==c.id)
                                                       .order_by(WorkOrder.id.desc()).limit(100))]
        result=customer_json(c)
        result.update(
            contacts=[dict(id=x.id,name=x.name,designation=x.designation,department=x.department,phone=x.phone,
                           email=x.email,primary_contact=x.primary_contact,site_id=x.site_id,notes=x.notes) for x in contacts],
            sites=[dict(id=x.id,name=x.name,address=x.address,city=x.city,state=x.state,notes=x.notes) for x in sites],
            machines=machines,jobs=jobs
        )
        return result


@app.post('/api/customers/<int:customer_id>/contacts')
@login_required(admin=True)
def create_customer_contact(customer_id):
    data=request.get_json() or {}
    with DB.begin() as db:
        if not db.get(Customer,customer_id): abort(404,'Customer not found.')
        contact=CustomerContact(customer_id=customer_id,name=clean_text(data,'name',120),
             designation=str(data.get('designation','')).strip()[:120] or None,
             department=str(data.get('department','')).strip()[:100] or None,
             phone=str(data.get('phone','')).strip()[:40] or None,email=str(data.get('email','')).strip()[:160] or None,
             primary_contact=data.get('primary_contact') is True,notes=str(data.get('notes','')).strip()[:5000] or None)
        db.add(contact);db.flush()
        db.add(AuditLog(admin_id=request.employee.id,action='create_customer_contact',
                        target=f'customer:{customer_id}',detail=contact.name,created_at=now()))
        return {'id':contact.id,'ok':True},201


@app.post('/api/customers/<int:customer_id>/sites')
@login_required(admin=True)
def create_customer_site(customer_id):
    data=request.get_json() or {}
    with DB.begin() as db:
        if not db.get(Customer,customer_id): abort(404,'Customer not found.')
        site=CustomerSite(customer_id=customer_id,name=clean_text(data,'name',140),
                          address=str(data.get('address','')).strip()[:5000] or None,
                          city=str(data.get('city','')).strip()[:80] or None,
                          state=str(data.get('state','')).strip()[:80] or None,
                          notes=str(data.get('notes','')).strip()[:5000] or None)
        db.add(site);db.flush()
        return {'id':site.id,'ok':True},201


@app.get('/api/machine-types')
@login_required()
def list_machine_types():
    with DB() as db:
        return jsonify([dict(id=x.id,name=x.name,description=x.description)
                        for x in db.scalars(select(MachineType).order_by(MachineType.name))])


@app.post('/api/machine-types')
@login_required(admin=True)
def create_machine_type():
    data=request.get_json() or {}
    name=clean_text(data,'name',120)
    with DB.begin() as db:
        existing=db.scalar(select(MachineType).where(MachineType.name==name))
        if existing: return dict(id=existing.id,name=existing.name),200
        row=MachineType(name=name,description=str(data.get('description','')).strip()[:5000] or None)
        db.add(row);db.flush()
        return {'id':row.id,'name':row.name},201


@app.get('/api/machines')
@login_required()
def list_machines():
    customer_id=request.args.get('customer_id')
    with DB() as db:
        q=select(CustomerMachine).order_by(CustomerMachine.id.desc())
        if not request.employee.admin:
            assigned_orders=select(WorkOrderAssignment.work_order_id).where(WorkOrderAssignment.employee_id==request.employee.id)
            machine_ids=select(WorkOrder.machine_id).where(WorkOrder.id.in_(assigned_orders),WorkOrder.machine_id != None)
            q=q.where(CustomerMachine.id.in_(machine_ids))
        if customer_id:
            try:q=q.where(CustomerMachine.customer_id==int(customer_id))
            except ValueError:abort(400,'Choose a valid customer.')
        out=[]
        for m in db.scalars(q.limit(500)):
            out.append(machine_json(m,db.get(Customer,m.customer_id),
                       db.get(MachineType,m.machine_type_id) if m.machine_type_id else None))
        return jsonify(out)


@app.post('/api/machines')
@login_required(admin=True)
def create_machine():
    data=request.get_json() or {}
    try: customer_id=int(data.get('customer_id'))
    except (TypeError,ValueError): abort(400,'Choose a customer.')
    with DB.begin() as db:
        customer=db.get(Customer,customer_id)
        if not customer: abort(404,'Customer not found.')
        type_id=None
        type_name=str(data.get('machine_type','')).strip()
        if data.get('machine_type_id'):
            type_id=int(data['machine_type_id'])
            if not db.get(MachineType,type_id): abort(404,'Machine type not found.')
        elif type_name:
            mt=db.scalar(select(MachineType).where(MachineType.name==type_name))
            if not mt:
                mt=MachineType(name=type_name[:120]);db.add(mt);db.flush()
            type_id=mt.id
        install=str(data.get('installation_date','')).strip()
        if install: install=valid_iso_date(install,'installation date')
        m=CustomerMachine(customer_id=customer_id,machine_type_id=type_id,
             customer_machine_no=str(data.get('customer_machine_no','')).strip()[:80] or None,
             manufacturer=str(data.get('manufacturer','')).strip()[:120] or None,
             model=str(data.get('model','')).strip()[:120] or None,
             serial_no=str(data.get('serial_no','')).strip()[:120] or None,
             controller=str(data.get('controller','')).strip()[:120] or None,
             department=str(data.get('department','')).strip()[:100] or None,
             location=str(data.get('location','')).strip()[:140] or None,
             installation_date=install or None,status='Active',
             notes=str(data.get('notes','')).strip()[:5000] or None,created_at=now())
        db.add(m);db.flush();m.code=f'MCH-{m.id:06d}'
        db.add(AuditLog(admin_id=request.employee.id,action='create_machine',target=m.code,
                        detail=f'{customer.name} / {m.customer_machine_no or ""}',created_at=now()))
        return machine_json(m,customer,db.get(MachineType,type_id) if type_id else None),201


@app.get('/api/machines/<int:machine_id>/history')
@login_required()
def machine_history(machine_id):
    with DB() as db:
        m=db.get(CustomerMachine,machine_id)
        if not m: abort(404,'Machine not found.')
        if not request.employee.admin:
            allowed=db.scalar(select(WorkOrderAssignment.id).join(WorkOrder,WorkOrder.id==WorkOrderAssignment.work_order_id)
                              .where(WorkOrderAssignment.employee_id==request.employee.id,WorkOrder.machine_id==machine_id).limit(1))
            if not allowed: abort(403,'This machine is not linked to your assigned work.')
        customer=db.get(Customer,m.customer_id)
        jobs=db.scalars(select(WorkOrder).where(WorkOrder.machine_id==m.id).order_by(WorkOrder.id.desc())).all()
        links=db.scalars(select(WorkReportLink).where(WorkReportLink.machine_id==m.id).order_by(WorkReportLink.id.desc())).all()
        reports=[]
        for link in links:
            r=db.get(WorkReport,link.work_report_id)
            if not r: continue
            e=db.get(Employee,r.employee_id)
            reports.append(work_report_json(r,e))
        return dict(machine=machine_json(m,customer,db.get(MachineType,m.machine_type_id) if m.machine_type_id else None),
                    jobs=[work_order_json(db,w) for w in jobs],work_reports=reports)


@app.get('/api/work-orders')
@login_required()
def list_work_orders():
    with DB() as db:
        q=select(WorkOrder).order_by(WorkOrder.id.desc())
        if not request.employee.admin:
            ids=select(WorkOrderAssignment.work_order_id).where(WorkOrderAssignment.employee_id==request.employee.id)
            q=q.where(WorkOrder.id.in_(ids))
        return jsonify([work_order_json(db,w) for w in db.scalars(q.limit(300))])


@app.post('/api/work-orders')
@login_required(admin=True)
def create_work_order():
    data=request.get_json() or {}
    try: customer_id=int(data.get('customer_id'))
    except (TypeError,ValueError): abort(400,'Choose a customer.')
    title=clean_text(data,'title',180)
    with DB.begin() as db:
        if not db.get(Customer,customer_id): abort(404,'Customer not found.')
        machine_id=int(data['machine_id']) if data.get('machine_id') else None
        if machine_id:
            machine=db.get(CustomerMachine,machine_id)
            if not machine or machine.customer_id!=customer_id: abort(400,'Machine does not belong to this customer.')
        def employee_id(key):
            if not data.get(key): return None
            try:value=int(data[key])
            except (TypeError,ValueError):abort(400,f'Choose a valid {key.replace("_"," ")}.')
            if not db.get(Employee,value):abort(404,'Employee not found.')
            return value
        start=valid_iso_date(data['start_date'],'start date') if data.get('start_date') else None
        target=valid_iso_date(data['target_date'],'target date') if data.get('target_date') else None
        w=WorkOrder(customer_id=customer_id,machine_id=machine_id,title=title,
                    work_type=str(data.get('work_type','Service')).strip()[:60] or 'Service',
                    description=str(data.get('description','')).strip()[:8000] or None,
                    job_owner_id=employee_id('job_owner_id'),supervisor_id=employee_id('supervisor_id'),
                    approved_by_id=employee_id('approved_by_id'),start_date=start,target_date=target,
                    status=str(data.get('status','Open')).strip()[:30] or 'Open',
                    priority=str(data.get('priority','Normal')).strip()[:20] or 'Normal',created_at=now())
        db.add(w);db.flush();w.code=f'JO-{now().astimezone(LOCAL).year}-{w.id:04d}'
        assigned=data.get('assigned_employee_ids') or []
        for employee in assigned[:30]:
            try:eid=int(employee)
            except (TypeError,ValueError):continue
            if db.get(Employee,eid):
                db.add(WorkOrderAssignment(work_order_id=w.id,employee_id=eid,role='Assigned',
                                           responsibility=None,assigned_at=now()))
        db.add(AuditLog(admin_id=request.employee.id,action='create_work_order',target=w.code,detail=w.title,created_at=now()))
        return work_order_json(db,w),201


@app.post('/api/work-orders/<int:work_order_id>/assignments')
@login_required(admin=True)
def assign_work_order(work_order_id):
    data=request.get_json() or {}
    try:employee_id=int(data.get('employee_id'))
    except (TypeError,ValueError):abort(400,'Choose an employee.')
    with DB.begin() as db:
        if not db.get(WorkOrder,work_order_id):abort(404,'Work order not found.')
        if not db.get(Employee,employee_id):abort(404,'Employee not found.')
        role=str(data.get('role','Assigned')).strip()[:50] or 'Assigned'
        existing=db.scalar(select(WorkOrderAssignment).where(WorkOrderAssignment.work_order_id==work_order_id,
                           WorkOrderAssignment.employee_id==employee_id,WorkOrderAssignment.role==role))
        if existing: return {'id':existing.id,'ok':True}
        a=WorkOrderAssignment(work_order_id=work_order_id,employee_id=employee_id,role=role,
                              responsibility=str(data.get('responsibility','')).strip()[:5000] or None,assigned_at=now())
        db.add(a);db.flush()
        return {'id':a.id,'ok':True},201


@app.patch('/api/work-orders/<int:work_order_id>')
@login_required(admin=True)
def update_work_order(work_order_id):
    data=request.get_json() or {}
    with DB.begin() as db:
        w=db.get(WorkOrder,work_order_id)
        if not w:abort(404,'Work order not found.')
        if 'status' in data:w.status=str(data['status']).strip()[:30] or w.status
        if 'completed_date' in data:
            w.completed_date=valid_iso_date(data['completed_date'],'completed date') if data['completed_date'] else None
        db.add(AuditLog(admin_id=request.employee.id,action='update_work_order',target=w.code or str(w.id),
                        detail=w.status,created_at=now()))
        return work_order_json(db,w)


@app.get('/api/network/search')
@login_required(admin=True)
def network_search():
    q=str(request.args.get('q','')).strip()
    if len(q)<2:return jsonify([])
    term=f'%{q}%'
    with DB() as db:
        results=[]
        for c in db.scalars(select(Customer).where(Customer.name.ilike(term)).limit(10)):
            results.append(dict(type='Customer',id=c.id,code=c.code,title=c.name,subtitle=c.industry or c.address))
        for m in db.scalars(select(CustomerMachine).where(
                CustomerMachine.code.ilike(term) | CustomerMachine.customer_machine_no.ilike(term) |
                CustomerMachine.model.ilike(term) | CustomerMachine.serial_no.ilike(term)).limit(10)):
            c=db.get(Customer,m.customer_id)
            results.append(dict(type='Machine',id=m.id,code=m.code,title=m.customer_machine_no or m.model or m.code,
                                subtitle=c.name if c else None))
        for w in db.scalars(select(WorkOrder).where(WorkOrder.code.ilike(term) | WorkOrder.title.ilike(term)).limit(10)):
            c=db.get(Customer,w.customer_id)
            results.append(dict(type='Work Order',id=w.id,code=w.code,title=w.title,subtitle=c.name if c else None))
        for e in db.scalars(select(Employee).where(Employee.admin==False,Employee.name.ilike(term)).limit(10)):
            results.append(dict(type='Employee',id=e.id,code=e.code,title=e.name,subtitle=e.department))
        return jsonify(results[:30])


@app.get('/api/export')
@login_required(admin=True)
def export():
    if not request.args.get('month'):
        abort(400, 'Select a month to export.')
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['Employee ID', 'Name', 'Department', 'Work date', f'Check-in ({LOCAL})', f'Check-out ({LOCAL})',
                     'Hours', 'In latitude', 'In longitude', 'In GPS accuracy (m)', 'Out latitude', 'Out longitude', 'Out GPS accuracy (m)'])
    def safe(value):
        value = str(value) if value is not None else ''
        return "'" + value if value.startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else value
    with DB() as db:
        for r, e in filtered_records(db):
            writer.writerow([safe(e.code), safe(e.name), safe(e.department), r.work_date,
                             aware(r.in_at).astimezone(LOCAL).strftime('%Y-%m-%d %H:%M:%S'),
                             aware(r.out_at).astimezone(LOCAL).strftime('%Y-%m-%d %H:%M:%S') if r.out_at else '',
                             record(r, e)['hours'], r.in_lat, r.in_lng, r.in_accuracy, r.out_lat, r.out_lng, r.out_accuracy])
    return Response('\ufeff' + out.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename=cosmos-attendance-{request.args["month"]}.csv'})
