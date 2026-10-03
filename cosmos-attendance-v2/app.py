import base64
import csv
import io
import hashlib
import json
import math
import os
import re
import secrets
import html
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
from sqlalchemy import Boolean, DateTime, Float, Integer, Numeric, LargeBinary, String, Text, ForeignKey, create_engine, select, delete, UniqueConstraint, func, or_, update
import qrcode
import qrcode.image.svg
from decimal import Decimal, InvalidOperation
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
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
    if PRODUCTION:
        raise RuntimeError('Set a random SECRET_KEY with at least 32 characters.')
    SECRET = secrets.token_urlsafe(48)
_face_key = os.getenv('FACE_ENCRYPTION_KEY', '')
if not _face_key:
    if PRODUCTION:
        raise RuntimeError('Set FACE_ENCRYPTION_KEY.')
    _face_key = Fernet.generate_key().decode()
CIPHER = Fernet(_face_key.encode())
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


class DeadlineReminder(Base):
    __tablename__ = 'deadline_reminders'
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(180))
    due_date: Mapped[str] = mapped_column(String(10), index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EmployeeMemo(Base):
    __tablename__ = 'employee_memos'
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), index=True)
    event_date: Mapped[str] = mapped_column(String(10), index=True)
    rule_id: Mapped[str] = mapped_column(String(60))
    rule_label: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(100))
    half_points: Mapped[int] = mapped_column(Integer)
    details: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(Text)
    employee_response: Mapped[str] = mapped_column(Text)
    review_notes: Mapped[str] = mapped_column(Text)
    recorded_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    voided_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmployeePoint(Base):
    __tablename__ = 'employee_points'
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), index=True)
    event_date: Mapped[str] = mapped_column(String(10), index=True)
    category: Mapped[str] = mapped_column(String(40))
    points: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    recorded_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    voided_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    service_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    service_activities: Mapped[str | None] = mapped_column(Text, nullable=True)


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
    created_by: Mapped[int | None] = mapped_column(ForeignKey('employees.id'), nullable=True)
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


class DailyPlan(Base):
    __tablename__ = 'daily_plans'
    id: Mapped[int] = mapped_column(primary_key=True)
    work_date: Mapped[str] = mapped_column(String(10), index=True)
    title: Mapped[str] = mapped_column(String(180))
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    department: Mapped[str | None] = mapped_column(String(40), nullable=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), index=True)
    work_order_id: Mapped[int | None] = mapped_column(ForeignKey('work_orders.id'), nullable=True)
    estimated_hours: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    priority: Mapped[str] = mapped_column(String(20), default='Normal')
    status: Mapped[str] = mapped_column(String(20), default='Planned')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    service_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    service_activities: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey('customers.id'), nullable=True, index=True)
    machine_id: Mapped[int | None] = mapped_column(ForeignKey('customer_machines.id'), nullable=True)
    due_date: Mapped[str | None] = mapped_column(String(10), nullable=True)


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
    specifications: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_service_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    next_service_date: Mapped[str | None] = mapped_column(String(10), nullable=True)


class WorkOrder(Base):
    __tablename__ = 'work_orders'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str | None] = mapped_column(String(40), unique=True, nullable=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey('customers.id'), index=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey('customer_sites.id'), nullable=True)
    machine_id: Mapped[int | None] = mapped_column(ForeignKey('customer_machines.id'), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(180))
    service_product: Mapped[str | None] = mapped_column(String(180), nullable=True)
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
    service_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    service_activities: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_stage: Mapped[str | None] = mapped_column(String(60), nullable=True)
    customer_remarks: Mapped[str | None] = mapped_column(Text, nullable=True)


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


class StockItem(Base):
    __tablename__ = 'stock_items'
    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(180))
    category: Mapped[str] = mapped_column(String(50), default='Raw material')
    sub_category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    size_dimension: Mapped[str | None] = mapped_column(String(80), nullable=True)
    material_finish: Mapped[str | None] = mapped_column(String(80), nullable=True)
    specification: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit: Mapped[str] = mapped_column(String(20), default='pcs')
    location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reorder_level: Mapped[Decimal] = mapped_column(Numeric(14,3), default=0)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14,3), default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class StockMovement(Base):
    __tablename__ = 'stock_movements'
    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey('stock_items.id'), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    quantity_change: Mapped[Decimal] = mapped_column(Numeric(14,3))
    balance_after: Mapped[Decimal] = mapped_column(Numeric(14,3))
    work_order_id: Mapped[int | None] = mapped_column(ForeignKey('work_orders.id'), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reason: Mapped[str] = mapped_column(String(500))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey('employees.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Supplier(Base):
    __tablename__ = 'suppliers'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(180), unique=True)
    contact: Mapped[str | None] = mapped_column(String(120), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    email: Mapped[str | None] = mapped_column(String(160), nullable=True)
    gstin: Mapped[str | None] = mapped_column(String(30), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class PurchaseOrder(Base):
    __tablename__ = 'purchase_orders'
    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey('suppliers.id'))
    item_id: Mapped[int] = mapped_column(ForeignKey('stock_items.id'))
    ordered_qty: Mapped[Decimal] = mapped_column(Numeric(14,3))
    received_qty: Mapped[Decimal] = mapped_column(Numeric(14,3), default=0)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14,2), default=0)
    status: Mapped[str] = mapped_column(String(20), default='Open')
    expected_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey('employees.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    supplier_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProductionStep(Base):
    __tablename__ = 'production_steps'
    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey('work_orders.id'), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    operation: Mapped[str] = mapped_column(String(80))
    assigned_id: Mapped[int | None] = mapped_column(ForeignKey('employees.id'), nullable=True)
    planned_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    planned_hours: Mapped[Decimal] = mapped_column(Numeric(10,2), default=0)
    actual_hours: Mapped[Decimal] = mapped_column(Numeric(10,2), default=0)
    accepted_qty: Mapped[Decimal] = mapped_column(Numeric(14,3), default=0)
    rejected_qty: Mapped[Decimal] = mapped_column(Numeric(14,3), default=0)
    status: Mapped[str] = mapped_column(String(30), default='Planned')
    delay_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)


class CompanyAsset(Base):
    __tablename__ = 'company_assets'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(60), unique=True)
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(160))
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    serial_or_registration: Mapped[str | None] = mapped_column(String(100), nullable=True)
    location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    next_service_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class MaintenanceTask(Base):
    __tablename__ = 'maintenance_tasks'
    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey('company_assets.id'), index=True)
    task_type: Mapped[str] = mapped_column(String(30))
    description: Mapped[str] = mapped_column(Text)
    due_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    completed_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='Open')
    downtime_hours: Mapped[Decimal] = mapped_column(Numeric(10,2), default=0)
    cost: Mapped[Decimal] = mapped_column(Numeric(14,2), default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_id: Mapped[int | None] = mapped_column(ForeignKey('employees.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    service_activities: Mapped[str | None] = mapped_column(Text, nullable=True)
    checklist: Mapped[str | None] = mapped_column(Text, nullable=True)
    parts_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_service_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    priority: Mapped[str | None] = mapped_column(String(20), nullable=True)


class Quotation(Base):
    __tablename__ = 'quotations'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey('customers.id'), index=True)
    title: Mapped[str] = mapped_column(String(180))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    amount: Mapped[Decimal] = mapped_column(Numeric(14,2), default=0)
    status: Mapped[str] = mapped_column(String(30), default='Draft')
    follow_up_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    promised_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    customer_po: Mapped[str | None] = mapped_column(String(100), nullable=True)
    work_order_id: Mapped[int | None] = mapped_column(ForeignKey('work_orders.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MaterialRequirement(Base):
    __tablename__ = 'material_requirements'
    __table_args__ = (UniqueConstraint('work_order_id','item_id',name='one_item_per_job'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey('work_orders.id'), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey('stock_items.id'), index=True)
    required_qty: Mapped[Decimal] = mapped_column(Numeric(14,3))
    reserved_qty: Mapped[Decimal] = mapped_column(Numeric(14,3), default=0)
    issued_qty: Mapped[Decimal] = mapped_column(Numeric(14,3), default=0)


class DrawingRevision(Base):
    __tablename__ = 'drawing_revisions'
    __table_args__ = (UniqueConstraint('work_order_id','drawing_no','revision',name='unique_job_drawing_rev'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey('work_orders.id'), index=True)
    drawing_no: Mapped[str] = mapped_column(String(100))
    revision: Mapped[str] = mapped_column(String(30))
    file_reference: Mapped[str] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey('employees.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PrivateDocument(Base):
    __tablename__ = 'private_documents'
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_type: Mapped[str] = mapped_column(String(20))
    owner_id: Mapped[int] = mapped_column(Integer, index=True)
    filename: Mapped[str] = mapped_column(String(180))
    mime: Mapped[str] = mapped_column(String(100))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey('employees.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class QualityCheck(Base):
    __tablename__ = 'quality_checks'
    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey('work_orders.id'), index=True)
    operation: Mapped[str] = mapped_column(String(80))
    inspected_qty: Mapped[Decimal] = mapped_column(Numeric(14,3))
    accepted_qty: Mapped[Decimal] = mapped_column(Numeric(14,3))
    rejected_qty: Mapped[Decimal] = mapped_column(Numeric(14,3))
    result: Mapped[str] = mapped_column(String(20))
    defect: Mapped[str | None] = mapped_column(Text, nullable=True)
    inspector_id: Mapped[int | None] = mapped_column(ForeignKey('employees.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DispatchRecord(Base):
    __tablename__ = 'dispatch_records'
    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey('work_orders.id'), index=True)
    dispatch_date: Mapped[str] = mapped_column(String(10))
    quantity: Mapped[Decimal] = mapped_column(Numeric(14,3))
    transporter: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tracking_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    delivery_note: Mapped[str | None] = mapped_column(String(120), nullable=True)
    proof_reference: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey('employees.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CustomerUpdate(Base):
    __tablename__ = 'customer_updates'
    id: Mapped[int] = mapped_column(primary_key=True)
    work_order_id: Mapped[int] = mapped_column(ForeignKey('work_orders.id'), index=True)
    message: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(String(250))
    recipient_email: Mapped[str | None] = mapped_column(String(160), nullable=True)
    recipient_phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default='Draft')
    channel: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey('employees.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
                admin=e.admin, active=e.active, enrolled=True, face_enabled=False)


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
        if request.is_json:
            data = request.get_json()
            if not isinstance(data, dict):
                abort(400, 'Send the form fields as a JSON object.')
            # Record references are positive integer IDs, never booleans or decimals.
            for key, value in data.items():
                if key in ('employee_id','customer_id','machine_id','machine_type_id','site_id',
                           'work_order_id','item_id','supplier_id','asset_id','assigned_id',
                           'supervisor_id','job_owner_id','approved_by_id'):
                    optional_id(value, key.replace('_', ' '))
            if 'assigned_employee_ids' in data:
                values = data['assigned_employee_ids']
                if not isinstance(values, list) or len(values) > 30:
                    abort(400, 'Choose up to 30 employees.')
                for value in values:
                    if optional_id(value, 'employee') is None:
                        abort(400, 'Choose a valid employee.')


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


def login_attempt_key():
    data=request.get_json(silent=True)
    code=str(data.get('code','') if isinstance(data,dict) else '').strip().lower()
    return 'login-account:'+hashlib.sha256(code.encode()).hexdigest()


def attendance_attempt_key():
    # Signed sessions separate employees who share the factory's public IP address.
    return 'attendance-account:'+str(session['uid']) if session.get('uid') else 'attendance-ip:'+get_remote_address()


@app.post('/api/login')
@limiter.limit('5 per minute; 30 per hour', key_func=login_attempt_key,
               deduct_when=lambda response: response.status_code==401,
               error_message='Too many incorrect PIN attempts for this employee ID. Wait a minute before retrying. If it continues, ask your administrator for help.')
@limiter.limit('60 per minute; 300 per hour', key_func=get_remote_address,
               deduct_when=lambda response: response.status_code==401,
               error_message='Too many incorrect sign-in attempts from this network. Please wait before retrying.')
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



@app.post('/api/logout')
def logout():
    session.clear()
    return {'ok': True}


def clean_text(data, key, limit):
    value = data.get(key, '')
    if not isinstance(value, str):
        abort(400, f'{key.replace("_", " ").title()} must be text.')
    value = value.strip()
    if not value or len(value) > limit:
        abort(400, f'{key.replace("_", " ").title()} must be between 1 and {limit} characters.')
    return value


@app.get('/api/employees')
@login_required(admin=True)
def employees():
    with DB() as db:
        return jsonify([person(e) for e in db.scalars(
            select(Employee).where(Employee.admin == False).order_by(Employee.name)
        )])


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


@app.delete('/api/employees/<int:employee_id>')
@login_required(admin=True)
def delete_employee(employee_id):
    return permanently_delete_employee(employee_id)


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
def enrol(employee_id):
    abort(410, 'Face recognition is disabled. Employees use PIN or biometric login with GPS attendance.')


def gps(data):
    try:
        if not isinstance(data,dict):raise ValueError()
        lat, lng, accuracy = float(data['lat']), float(data['lng']), float(data['accuracy'])
        age = abs(now().timestamp() * 1000 - float(data['timestamp']))
        if not all(math.isfinite(x) for x in (lat, lng, accuracy, age)):
            raise ValueError()
        if not (-90 <= lat <= 90 and -180 <= lng <= 180 and 0 <= accuracy):
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        abort(400, 'A valid location was not received. Allow location access on your phone and browser, then try attendance again.')
    if age>120000:
        abort(400, 'The location reading is out of date. Set your phone date and time to automatic, then get your location again.')
    if accuracy>10000:
        abort(400, 'Your location is too approximate. Enable precise location and try again near a window or outdoors.')
    return lat, lng, accuracy


@app.post('/api/attendance')
@login_required()
@limiter.limit('6 per minute', key_func=attendance_attempt_key,
               error_message='Too many attendance attempts for your account. Wait a minute, then refresh your attendance before retrying.')
def mark_attendance():
    data = request.get_json() or {}
    action = data.get('action')
    if action not in ('in', 'out'):
        abort(400, 'Choose check-in or check-out.')
    if request.employee.admin:
        abort(403, 'Administrator accounts do not mark employee attendance.')
    lat, lng, accuracy = gps(data.get('location', {}))
    with DB.begin() as db:
        e = db.scalar(select(Employee).where(Employee.id == request.employee.id).with_for_update())
        if not e or not e.active:
            abort(401, 'Please sign in again.')
        open_record = db.scalar(select(Attendance).where(Attendance.employee_id == e.id, Attendance.out_at == None))
        stamp = now()
        day = stamp.astimezone(LOCAL).date().isoformat()
        if action == 'in':
            if open_record:
                abort(409, 'You are already checked in. Check out first.')
            if db.scalar(select(Attendance.id).where(Attendance.employee_id == e.id, Attendance.work_date == day)):
                abort(409, 'Attendance is complete for today. One shift per day is supported.')
            r = Attendance(employee_id=e.id, work_date=day, in_at=stamp, in_lat=lat, in_lng=lng,
                           in_accuracy=accuracy, in_photo='', in_distance=0.0)
            db.add(r)
        else:
            if not open_record:
                abort(409, 'You do not have an open check-in.')
            r = open_record
            r.out_at, r.out_lat, r.out_lng, r.out_accuracy = stamp, lat, lng, accuracy
            r.out_photo, r.out_distance = '', 0.0
        db.flush()
        result = record(r, e)
        db.add(AuditLog(admin_id=None, action='attendance_'+action, target=e.code,
                        detail=json.dumps({'date':day,'gps_accuracy':accuracy}), created_at=now()))
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
        r=db.get(Attendance,attendance_id)
        if not r:
            abort(404)
        e=db.scalar(select(Employee).where(Employee.id==r.employee_id).with_for_update())
        r=db.scalar(select(Attendance).where(Attendance.id==attendance_id).with_for_update().execution_options(populate_existing=True))
        if not r:abort(404)
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
        with db.no_autoflush:
            work_date=aware(r.in_at).astimezone(LOCAL).date().isoformat()
            if db.scalar(select(Attendance.id).where(Attendance.employee_id==r.employee_id,
                    Attendance.id!=r.id, Attendance.work_date==work_date)):
                abort(409, 'This employee already has attendance for that date.')
            if r.out_at is None and db.scalar(select(Attendance.id).where(
                    Attendance.employee_id==r.employee_id, Attendance.id!=r.id, Attendance.out_at==None)):
                abort(409, 'This employee already has another open shift.')
        r.work_date=work_date
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
        if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
            raise ValueError()
        datetime.strptime(value, '%Y-%m-%d')
        return value
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
                verified_by=r.verified_by,service_type=r.service_type,service_activities=json_list(r.service_activities), created_at=aware(r.created_at).isoformat())


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
    if status not in (*PLAN_STATUSES,'Pending'):abort(400,'Choose a listed work status.')
    with DB.begin() as db:
        r = WorkReport(employee_id=employee_id, work_date=work_date,
                       job_no=str(data.get('job_no','')).strip()[:60] or None,
                       customer=str(data.get('customer','')).strip()[:120] or None,
                       work_details=details[:5000],
                       machine=str(data.get('machine','')).strip()[:120] or None,
                       status=status,**service_fields(data),
                       problems=str(data.get('problems','')).strip()[:5000] or None,
                       created_at=now())
        db.add(r); db.flush()
        link_order = data.get('work_order_id')
        link_machine = data.get('machine_id')
        work_order_id = optional_id(link_order, 'work order')
        machine_id = optional_id(link_machine, 'machine')
        work_order = db.get(WorkOrder, work_order_id) if work_order_id else None
        machine = db.get(CustomerMachine, machine_id) if machine_id else None
        if work_order_id and not work_order:
            abort(404, 'Work order not found.')
        if machine_id and not machine:
            abort(404, 'Machine not found.')
        if not request.employee.admin:
            if work_order_id and not db.scalar(select(WorkOrder.id).where(WorkOrder.id==work_order_id,
                    WorkOrder.id.in_(assigned_order_ids(employee_id))).limit(1)):
                abort(403, 'You can only link work reports to your assigned work orders.')
            if machine_id and not db.scalar(select(WorkOrder.id).where(WorkOrder.id.in_(assigned_order_ids(employee_id)),
                    WorkOrder.machine_id==machine_id).limit(1)):
                abort(403, 'You can only link work reports to machines assigned to your work.')
        if work_order_id and machine_id and (machine.customer_id!=work_order.customer_id or (work_order.machine_id and work_order.machine_id!=machine_id)):
            abort(400, 'Selected machine does not match the selected work order.')
        if work_order:
            customer=db.get(Customer,work_order.customer_id)
            r.customer=customer.name;r.job_no=work_order.code
            if not r.service_type:
                r.service_type=work_order.service_type;r.service_activities=work_order.service_activities
            if not machine_id and work_order.machine_id:
                machine_id=work_order.machine_id;machine=db.get(CustomerMachine,machine_id)
        if machine:r.machine=machine.customer_machine_no or machine.model or machine.code
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
        if 'status' in data:
            if data['status'] not in (*PLAN_STATUSES,'Pending'):abort(400,'Choose a listed work status.')
            r.status=data['status']
        if any(k in data for k in ('service_type','service_activities')):
            for key,value in service_fields(data,r).items():setattr(r,key,value)
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


SERVICE_TYPES = ('Breakdown','Preventive','Inspection','Installation','Repair')
SERVICE_ACTIVITIES = ('Cover Service','Conveyor Service','Alignment / Adjustment','Drawing Work','Repairs','Mechanical Check','Others','Replacement and Fixing work')
WORK_STATUSES = ('Open','In Progress','On Hold','Completed','Closed','Cancelled','Rework')
PLAN_STATUSES = ('Open','Planned','In Progress','Blocked','On Hold','Completed','Closed','Cancelled','Rework')
TERMINAL_STATUSES = ('Completed','Closed','Cancelled')
PROJECT_STAGES = ('Planning','Design','Drawing Work','Material preparation','Laser cutting','Bending','Welding','Assembly','Powder coating','Inspection','Service','Ready for dispatch','Dispatch','Delivered','Rework')


def json_list(value):
    try: result=json.loads(value or '[]')
    except (ValueError,TypeError): return []
    return result if isinstance(result,list) else []


def service_fields(data, existing=None):
    raw=data.get('service_type', getattr(existing,'service_type',None))
    service_type=str(raw or '').strip() or None
    if service_type=='Preventive service': service_type='Preventive'
    if service_type and service_type not in SERVICE_TYPES: abort(400,'Choose a listed service type.')
    activities=data.get('service_activities',json_list(getattr(existing,'service_activities',None)))
    if not isinstance(activities,list) or len(activities)>len(SERVICE_ACTIVITIES) or any(not isinstance(a,str) or a not in SERVICE_ACTIVITIES for a in activities):
        abort(400,'Choose activities from the service list.')
    if activities and not service_type: abort(400,'Choose a service type for these activities.')
    return dict(service_type=service_type,service_activities=json.dumps(list(dict.fromkeys(activities))))


def optional_id(value,label):
    if value in (None,''):return None
    if type(value) is not int and not (isinstance(value,str) and re.fullmatch(r'[0-9]+',value)):
        abort(400,'Choose a valid '+label+'.')
    result=int(value)
    if result<=0 or result>2147483647:abort(400,'Choose a valid '+label+'.')
    return result


def assigned_order_ids(employee_id):
    """Explicit assignments and non-cancelled plans both authorize linked work.

    Deriving plan access avoids stale permissions after reassignment or deletion.
    """
    return select(WorkOrderAssignment.work_order_id).where(
        WorkOrderAssignment.employee_id==employee_id).union(
        select(DailyPlan.work_order_id).where(DailyPlan.employee_id==employee_id,
            DailyPlan.work_order_id!=None,DailyPlan.status!='Cancelled'))


def linked_work_context(db,data):
    order_id=optional_id(data.get('work_order_id'),'work order')
    customer_id=optional_id(data.get('customer_id'),'customer')
    machine_id=optional_id(data.get('machine_id'),'machine')
    order=db.get(WorkOrder,order_id) if order_id else None
    if order_id and not order:abort(404,'Work order not found.')
    if order:
        if customer_id and customer_id!=order.customer_id:abort(400,'Work order does not belong to the selected company.')
        customer_id=order.customer_id
        if machine_id and order.machine_id and machine_id!=order.machine_id:abort(400,'Machine does not match this work order.')
        machine_id=machine_id or order.machine_id
    machine=db.get(CustomerMachine,machine_id) if machine_id else None
    if machine_id and not machine:abort(404,'Machine not found.')
    if machine:
        if customer_id and machine.customer_id!=customer_id:abort(400,'Machine does not belong to the selected company.')
        customer_id=machine.customer_id
    if customer_id and not db.get(Customer,customer_id):abort(404,'Customer not found.')
    return customer_id,machine_id,order


@app.get('/api/workflow-options')
@login_required()
def workflow_options():
    return dict(service_types=SERVICE_TYPES,service_activities=SERVICE_ACTIVITIES,work_statuses=WORK_STATUSES,
                plan_statuses=PLAN_STATUSES,project_stages=PROJECT_STAGES,
                machine_types=['VMC','HMC','CNC Turning','VTL','Grinding','Other'])


def customer_json(c):
    return dict(id=c.id, code=c.code, name=c.name, gstin=c.gstin, industry=c.industry,
                phone=c.phone, email=c.email, address=c.address, status=c.status, notes=c.notes)


def machine_json(m, customer=None, machine_type=None):
    return dict(id=m.id, code=m.code, customer_id=m.customer_id,
                customer=customer.name if customer else None, site_id=m.site_id,
                machine_type_id=m.machine_type_id, machine_type=machine_type.name if machine_type else None,
                customer_machine_no=m.customer_machine_no, manufacturer=m.manufacturer, model=m.model,
                serial_no=m.serial_no, controller=m.controller, department=m.department, location=m.location,
                installation_date=m.installation_date, status=m.status, notes=m.notes, specifications=m.specifications,
                last_service_date=m.last_service_date,next_service_date=m.next_service_date)


def work_order_context(db, orders):
    context={'customers':{},'machines':{},'employees':{},'assignments':{}}
    if not orders:return context
    context['customers']={c.id:c for c in db.scalars(select(Customer).where(Customer.id.in_({w.customer_id for w in orders})))}
    machine_ids={w.machine_id for w in orders if w.machine_id}
    if machine_ids:context['machines']={m.id:m for m in db.scalars(select(CustomerMachine).where(CustomerMachine.id.in_(machine_ids)))}
    employee_ids={eid for w in orders for eid in (w.job_owner_id,w.supervisor_id,w.approved_by_id) if eid}
    if employee_ids:context['employees']={e.id:e for e in db.scalars(select(Employee).where(Employee.id.in_(employee_ids)))}
    for assignment,employee in db.execute(select(WorkOrderAssignment,Employee).join(Employee,Employee.id==WorkOrderAssignment.employee_id)
            .where(WorkOrderAssignment.work_order_id.in_([w.id for w in orders])).order_by(WorkOrderAssignment.id)):
        context['assignments'].setdefault(assignment.work_order_id,[]).append((assignment,employee))
    return context


def work_order_json(db, w, context=None):
    customer=context['customers'].get(w.customer_id) if context is not None else db.get(Customer,w.customer_id)
    machine=context['machines'].get(w.machine_id) if context is not None else db.get(CustomerMachine,w.machine_id) if w.machine_id else None
    assignments=context['assignments'].get(w.id,[]) if context is not None else db.execute(select(WorkOrderAssignment,Employee).join(Employee,Employee.id==WorkOrderAssignment.employee_id)
                           .where(WorkOrderAssignment.work_order_id==w.id).order_by(WorkOrderAssignment.id)).all()
    def emp_name(emp_id):
        e=context['employees'].get(emp_id) if context is not None else db.get(Employee,emp_id) if emp_id else None
        return dict(id=e.id,name=e.name,code=e.code) if e else None
    return dict(id=w.id, code=w.code, customer_id=w.customer_id, customer=customer.name if customer else None,
                machine_id=w.machine_id, machine_code=machine.code if machine else None,
                machine_no=machine.customer_machine_no if machine else None, title=w.title,
                service_product=w.service_product, work_type=w.work_type,service_type=w.service_type,
                service_activities=json_list(w.service_activities),current_stage=w.current_stage,customer_remarks=w.customer_remarks,
                machine_model=machine.model if machine else None,machine_manufacturer=machine.manufacturer if machine else None,
                description=w.description, job_owner=emp_name(w.job_owner_id), supervisor=emp_name(w.supervisor_id),
                approved_by=emp_name(w.approved_by_id), start_date=w.start_date, target_date=w.target_date,
                completed_date=w.completed_date, status=w.status, priority=w.priority,
                assignments=[dict(id=a.id,employee_id=e.id,employee=e.name,code=e.code,role=a.role,
                                  responsibility=a.responsibility) for a,e in assignments])


def daily_plan_json(db, row):
    employee=db.get(Employee,row.employee_id)
    order=db.get(WorkOrder,row.work_order_id) if row.work_order_id else None
    customer_id=row.customer_id or (order.customer_id if order else None)
    machine_id=row.machine_id or (order.machine_id if order else None)
    customer=db.get(Customer,customer_id) if customer_id else None
    machine=db.get(CustomerMachine,machine_id) if machine_id else None
    return dict(id=row.id,date=row.work_date,due_date=row.due_date or row.work_date,title=row.title,details=row.details,
                department=row.department,employee_id=row.employee_id,
                employee=employee.name if employee else 'Former employee',
                customer_id=customer_id,customer=customer.name if customer else None,
                machine_id=machine_id,machine=machine.customer_machine_no or machine.model or machine.code if machine else None,
                work_order_id=row.work_order_id,work_order=order.code if order else None,
                service_type=row.service_type,service_activities=json_list(row.service_activities),
                estimated_hours=float(row.estimated_hours),priority=row.priority,status=row.status)


@app.get('/api/daily-plans')
@login_required()
def list_daily_plans():
    date=valid_iso_date(request.args.get('date'), 'planning date')
    with DB() as db:
        q=select(DailyPlan).where(DailyPlan.work_date==date).order_by(DailyPlan.id)
        if not request.employee.admin:
            q=q.where(DailyPlan.employee_id==request.employee.id)
        return jsonify([daily_plan_json(db,row) for row in db.scalars(q)])


def planning_limit(value, label='daily planning limit'):
    try: result=Decimal(str(value))
    except (InvalidOperation,TypeError): abort(400,f'Enter a valid {label}.')
    if not result.is_finite() or result<=0 or result>24: abort(400,f'The {label} must be greater than zero and no more than 24 hours.')
    return result


def planning_workload(db,date,department=None,lock=False):
    query=select(Employee).where(Employee.active==True,Employee.admin==False).order_by(Employee.name,Employee.id)
    if lock: query=query.with_for_update()
    candidates=db.scalars(query).all()
    if department: candidates=[e for e in candidates if e.department.casefold()==department.casefold()]
    loads={e.id:Decimal('0') for e in candidates}
    for eid,hours in db.execute(select(DailyPlan.employee_id,func.sum(DailyPlan.estimated_hours)).where(
            DailyPlan.work_date==date,DailyPlan.status!='Cancelled').group_by(DailyPlan.employee_id)):
        if eid in loads: loads[eid]=hours
    return candidates,loads


@app.get('/api/planning/workload')
@login_required(admin=True)
def get_planning_workload():
    date=valid_iso_date(request.args.get('date'),'planning date')
    capacity=planning_limit(request.args.get('daily_capacity',8))
    hours=planning_limit(request.args.get('estimated_hours',1),'task duration')
    department=str(request.args.get('department') or '').strip()[:40]
    with DB() as db:
        candidates,loads=planning_workload(db,date,department)
        available=[e for e in candidates if loads[e.id]+hours<=capacity]
        recommended=min(available,key=lambda e:(loads[e.id],e.name,e.id)) if available else None
        return dict(date=date,daily_capacity=float(capacity),estimated_hours=float(hours),
                    recommended_employee_id=recommended.id if recommended else None,
                    employees=[dict(id=e.id,name=e.name,department=e.department,planned_hours=float(loads[e.id]),
                                    remaining_hours=float(max(Decimal('0'),capacity-loads[e.id])),
                                    fits=loads[e.id]+hours<=capacity) for e in candidates])


@app.post('/api/daily-plans')
@login_required(admin=True)
def create_daily_plan():
    data=request.get_json(silent=True) or {}
    date=valid_iso_date(data.get('date'), 'planning date')
    title=clean_text(data,'title',180)
    try: hours=Decimal(str(data.get('estimated_hours')))
    except (InvalidOperation,TypeError): abort(400,'Enter estimated hours.')
    if not hours.is_finite() or hours<=0 or hours>24: abort(400,'Estimated hours must be between 0 and 24.')
    priority=data.get('priority','Normal')
    if priority not in ('Normal','High','Urgent'): abort(400,'Invalid priority.')
    department=str(data.get('department') or '').strip()[:40] or None
    with DB.begin() as db:
        customer_id,machine_id,order=linked_work_context(db,data)
        order_id=order.id if order else None
        employee_id=data.get('employee_id') or None
        if employee_id:
            try: employee_id=int(employee_id)
            except (ValueError,TypeError): abort(400,'Choose a valid employee.')
            employee=db.get(Employee,employee_id)
            if not employee or not employee.active or employee.admin: abort(400,'Choose an active employee.')
        else:
            candidates,load=planning_workload(db,date,department,lock=True)
            if not candidates: abort(400,'No active employee is available in this department.')
            if data.get('daily_capacity') is not None:
                capacity=planning_limit(data['daily_capacity'])
                candidates=[e for e in candidates if load[e.id]+hours<=capacity]
                if not candidates: abort(409,'This task exceeds the remaining daily capacity. Choose another date, adjust the limit, or assign an employee manually after review.')
            employee_id=min(candidates,key=lambda e:(load[e.id],e.name,e.id)).id
        row=DailyPlan(work_date=date,title=title,customer_id=customer_id,machine_id=machine_id,
                      due_date=valid_iso_date(data['due_date'],'deadline') if data.get('due_date') else date,
                      **service_fields(data,order),details=str(data.get('details') or '').strip()[:5000] or None,
                      department=department,employee_id=employee_id,work_order_id=order_id,
                      estimated_hours=hours,priority=priority,status='Planned',created_at=now())
        db.add(row);db.flush()
        db.add(AuditLog(admin_id=request.employee.id,action='create_daily_plan',target=str(row.id),
                        detail=f'{date} / {title} / employee:{employee_id}',created_at=now()))
        return daily_plan_json(db,row),201


@app.patch('/api/daily-plans/<int:plan_id>')
@login_required()
def update_daily_plan(plan_id):
    data=request.get_json(silent=True) or {}
    with DB.begin() as db:
        row=db.scalar(select(DailyPlan).where(DailyPlan.id==plan_id).with_for_update())
        if not row: abort(404,'Planned task not found.')
        if not request.employee.admin and row.employee_id!=request.employee.id:abort(403,'This task is not assigned to you.')
        if not request.employee.admin and (set(data)-{'status'} or data.get('status') not in ('In Progress','Completed','Blocked','Rework')):
            abort(403,'Only an administrator can change assignments, close or cancel a plan.')
        if 'status' in data:
            if data['status'] not in PLAN_STATUSES:abort(400,'Choose a listed task status.')
            row.status=data['status']
        if request.employee.admin:
            if 'title' in data:row.title=clean_text(data,'title',180)
            if 'details' in data:row.details=str(data['details'] or '').strip()[:5000] or None
            if 'department' in data:row.department=str(data['department'] or '').strip()[:40] or None
            if 'date' in data:row.work_date=valid_iso_date(data['date'],'work date')
            if 'due_date' in data:row.due_date=valid_iso_date(data['due_date'],'deadline') if data['due_date'] else row.work_date
            if 'estimated_hours' in data:row.estimated_hours=planning_limit(data['estimated_hours'],'task duration')
            if 'priority' in data:
                if data['priority'] not in ('Normal','High','Urgent'):abort(400,'Choose a valid priority.')
                row.priority=data['priority']
            if any(k in data for k in ('customer_id','machine_id','work_order_id')):
                context={k:data.get(k,getattr(row,k)) for k in ('customer_id','machine_id','work_order_id')}
                customer_id,machine_id,order=linked_work_context(db,context)
                row.customer_id=customer_id;row.machine_id=machine_id;row.work_order_id=order.id if order else None
            if 'employee_id' in data:
                eid=optional_id(data['employee_id'],'employee')
                employee=db.get(Employee,eid) if eid else None
                if not employee or not employee.active or employee.admin:abort(400,'Choose an active employee when editing a plan.')
                row.employee_id=eid
            if any(k in data for k in ('service_type','service_activities')):
                for key,value in service_fields(data,row).items():setattr(row,key,value)
        db.add(AuditLog(admin_id=request.employee.id if request.employee.admin else None,action='update_daily_plan',target=str(row.id),detail=row.status,created_at=now()))
        return daily_plan_json(db,row)


@app.get('/api/customers')
@login_required()
def list_customers():
    with DB() as db:
        q=select(Customer).order_by(Customer.name)
        if not request.employee.admin:
            assigned_orders=assigned_order_ids(request.employee.id)
            customer_ids=select(WorkOrder.customer_id).where(WorkOrder.id.in_(assigned_orders))
            q=q.where(Customer.id.in_(customer_ids))
        rows=db.scalars(q).all()
        ids=[c.id for c in rows]
        machine_counts=dict(db.execute(select(CustomerMachine.customer_id,func.count()).where(CustomerMachine.customer_id.in_(ids)).group_by(CustomerMachine.customer_id)).all())
        contact_counts=dict(db.execute(select(CustomerContact.customer_id,func.count()).where(CustomerContact.customer_id.in_(ids)).group_by(CustomerContact.customer_id)).all())
        job_counts=dict(db.execute(select(WorkOrder.customer_id,func.count()).where(WorkOrder.customer_id.in_(ids),WorkOrder.status.notin_(TERMINAL_STATUSES)).group_by(WorkOrder.customer_id)).all())
        out=[]
        for c in rows:
            item=customer_json(c)
            item.update(machine_count=machine_counts.get(c.id,0),contact_count=contact_counts.get(c.id,0),open_jobs=job_counts.get(c.id,0))
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


@app.patch('/api/customers/<int:customer_id>')
@login_required(admin=True)
def update_customer(customer_id):
    data=request.get_json(silent=True) or {}
    allowed={'name':180,'gstin':30,'industry':100,'phone':40,'email':160,'address':5000,'notes':5000}
    if not any(k in data for k in (*allowed,'status')): abort(400,'No changes supplied.')
    with DB.begin() as db:
        c=db.get(Customer,customer_id)
        if not c: abort(404,'Customer not found.')
        changes={}
        for key,limit in allowed.items():
            if key in data:
                value=str(data[key] or '').strip()[:limit] or None
                if key=='name' and not value: abort(400,'Company name is required.')
                if getattr(c,key)!=value:
                    changes[key]={'from':getattr(c,key),'to':value}
                    setattr(c,key,value)
        if 'status' in data:
            if data['status'] not in ('Active','Archived'): abort(400,'Invalid customer status.')
            if c.status!=data['status']:
                changes['status']={'from':c.status,'to':data['status']}
                c.status=data['status']
        if changes:
            db.add(AuditLog(admin_id=request.employee.id,action='update_customer',target=c.code or str(c.id),
                            detail=json.dumps(changes),created_at=now()))
        return customer_json(c)


@app.patch('/api/customers/<int:customer_id>/contacts/<int:contact_id>')
@login_required(admin=True)
def update_customer_contact(customer_id,contact_id):
    data=request.get_json(silent=True) or {}
    allowed={'name':120,'designation':120,'department':100,'phone':40,'email':160,'notes':5000}
    with DB.begin() as db:
        c=db.get(CustomerContact,contact_id)
        if not c or c.customer_id!=customer_id: abort(404,'Contact not found.')
        changes={}
        for key,limit in allowed.items():
            if key in data:
                value=str(data[key] or '').strip()[:limit] or None
                if key=='name' and not value: abort(400,'Contact name is required.')
                if getattr(c,key)!=value:
                    changes[key]={'from':getattr(c,key),'to':value}
                    setattr(c,key,value)
        if 'primary_contact' in data:
            value=data['primary_contact'] is True
            if c.primary_contact!=value:
                changes['primary_contact']={'from':c.primary_contact,'to':value}
                c.primary_contact=value
        if changes:
            db.add(AuditLog(admin_id=request.employee.id,action='update_customer_contact',
                            target=f'contact:{c.id}',detail=json.dumps(changes),created_at=now()))
        return {'id':c.id,'ok':True}


@app.get('/api/customers/<int:customer_id>')
@login_required()
def customer_detail(customer_id):
    with DB() as db:
        c=db.get(Customer,customer_id)
        if not c: abort(404,'Customer not found.')
        if not request.employee.admin:
            allowed=db.scalar(select(WorkOrder.id).where(WorkOrder.id.in_(assigned_order_ids(request.employee.id)),
                              WorkOrder.customer_id==customer_id).limit(1))
            if not allowed: abort(403,'This customer is not linked to your assigned work.')
        contacts=db.scalars(select(CustomerContact).where(CustomerContact.customer_id==c.id)
                           .order_by(CustomerContact.primary_contact.desc(),CustomerContact.name)).all()
        sites=db.scalars(select(CustomerSite).where(CustomerSite.customer_id==c.id).order_by(CustomerSite.name)).all()
        machines=[]
        for m in db.scalars(select(CustomerMachine).where(CustomerMachine.customer_id==c.id).order_by(CustomerMachine.id.desc())):
            machines.append(machine_json(m,c,db.get(MachineType,m.machine_type_id) if m.machine_type_id else None))
        jobs=[work_order_json(db,w) for w in db.scalars(select(WorkOrder).where(WorkOrder.customer_id==c.id)
                                                       .order_by(WorkOrder.id.desc()).limit(100))]
        # Reports are associated through a work order or a registered customer machine.
        # Never match free-text customer names, which can collide across companies.
        machine_ids=[m['id'] for m in machines]
        order_ids=[j['id'] for j in jobs]
        report_links=db.scalars(select(WorkReportLink).where(
            (WorkReportLink.machine_id.in_(machine_ids)) |
            (WorkReportLink.work_order_id.in_(order_ids)))).all() if machine_ids or order_ids else []
        reports=[]
        for link in report_links:
            report=db.get(WorkReport,link.work_report_id)
            employee=db.get(Employee,report.employee_id) if report else None
            if report and employee:
                item=work_report_json(report,employee)
                item.update(machine_id=link.machine_id,work_order_id=link.work_order_id)
                reports.append(item)
        reports.sort(key=lambda r:(r['date'],r['id']),reverse=True)
        serviced_ids={j['machine_id'] for j in jobs if j['machine_id'] and (j['service_type'] or j['work_type'].lower() in ('service','repair','installation','inspection','preventive maintenance')) and j['status'] in ('Completed','Closed')}
        serviced_ids.update(r['machine_id'] for r in reports if r['machine_id'] and r['status'] in ('Completed','Closed'))
        result=customer_json(c)
        result.update(
            contacts=[dict(id=x.id,name=x.name,designation=x.designation,department=x.department,phone=x.phone,
                           email=x.email,primary_contact=x.primary_contact,site_id=x.site_id,notes=x.notes) for x in contacts],
            sites=[dict(id=x.id,name=x.name,address=x.address,city=x.city,state=x.state,notes=x.notes) for x in sites],
            machines=machines,jobs=jobs,work_reports=reports,
            serviced_machine_count=len(serviced_ids)
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
        q=select(CustomerMachine,Customer,MachineType).join(Customer,Customer.id==CustomerMachine.customer_id).outerjoin(MachineType,MachineType.id==CustomerMachine.machine_type_id).order_by(CustomerMachine.id.desc())
        if not request.employee.admin:
            assigned_orders=assigned_order_ids(request.employee.id)
            machine_ids=select(WorkOrder.machine_id).where(WorkOrder.id.in_(assigned_orders),WorkOrder.machine_id != None)
            q=q.where(CustomerMachine.id.in_(machine_ids))
        if customer_id:
            try:q=q.where(CustomerMachine.customer_id==int(customer_id))
            except ValueError:abort(400,'Choose a valid customer.')
        out=[]
        for m,customer,machine_type in db.execute(q.limit(500)):
            out.append(machine_json(m,customer,machine_type))
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
             specifications=str(data.get('specifications') or '').strip()[:5000] or None,
             last_service_date=valid_iso_date(data['last_service_date'],'last service date') if data.get('last_service_date') else None,
             next_service_date=valid_iso_date(data['next_service_date'],'next service date') if data.get('next_service_date') else None,
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
            allowed=db.scalar(select(WorkOrder.id).where(WorkOrder.id.in_(assigned_order_ids(request.employee.id)),
                              WorkOrder.machine_id==machine_id).limit(1))
            if not allowed: abort(403,'This machine is not linked to your assigned work.')
        customer=db.get(Customer,m.customer_id)
        jobs=db.scalars(select(WorkOrder).where(WorkOrder.machine_id==m.id).order_by(WorkOrder.id.desc())).all()
        job_ids=[w.id for w in jobs]
        links=db.scalars(select(WorkReportLink).where(
            (WorkReportLink.machine_id==m.id) | (WorkReportLink.work_order_id.in_(job_ids)))
            .order_by(WorkReportLink.id.desc())).all()
        reports=[]
        for link in links:
            r=db.get(WorkReport,link.work_report_id)
            if not r: continue
            e=db.get(Employee,r.employee_id)
            item=work_report_json(r,e)
            item['work_order_id']=link.work_order_id
            reports.append(item)
        return dict(machine=machine_json(m,customer,db.get(MachineType,m.machine_type_id) if m.machine_type_id else None),
                    jobs=[work_order_json(db,w) for w in jobs],work_reports=reports)


@app.get('/api/work-orders')
@login_required()
def list_work_orders():
    with DB() as db:
        q=select(WorkOrder).order_by(WorkOrder.id.desc())
        if not request.employee.admin:
            ids=assigned_order_ids(request.employee.id)
            q=q.where(WorkOrder.id.in_(ids))
        orders=db.scalars(q.limit(300)).all()
        context=work_order_context(db,orders)
        return jsonify([work_order_json(db,w,context) for w in orders])


@app.post('/api/work-orders')
@login_required(admin=True)
def create_work_order():
    data=request.get_json() or {}
    try: customer_id=int(data.get('customer_id'))
    except (TypeError,ValueError): abort(400,'Choose a customer.')
    title=clean_text(data,'title',180)
    with DB.begin() as db:
        if not db.get(Customer,customer_id): abort(404,'Customer not found.')
        machine_id=optional_id(data.get('machine_id'),'machine')
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
        if data.get('status','Open') not in WORK_STATUSES:abort(400,'Choose a listed work order status.')
        stage=str(data.get('current_stage') or '').strip() or None
        if stage and stage not in PROJECT_STAGES:abort(400,'Choose a listed project stage.')
        w=WorkOrder(customer_id=customer_id,machine_id=machine_id,title=title,**service_fields(data),
                    current_stage=stage,customer_remarks=str(data.get('customer_remarks') or '').strip()[:3000] or None,
                    service_product=str(data.get('service_product') or '').strip()[:180] or None,
                    work_type=str(data.get('work_type','Service')).strip()[:60] or 'Service',
                    description=str(data.get('description','')).strip()[:8000] or None,
                    job_owner_id=employee_id('job_owner_id'),supervisor_id=employee_id('supervisor_id'),
                    approved_by_id=employee_id('approved_by_id'),start_date=start,target_date=target,
                    status=str(data.get('status','Open')).strip()[:30] or 'Open',
                    priority=str(data.get('priority','Normal')).strip()[:20] or 'Normal',created_at=now())
        db.add(w);db.flush();w.code=f'JO-{now().astimezone(LOCAL).year}-{w.id:04d}'
        assigned=list(dict.fromkeys(optional_id(value,'employee') for value in (data.get('assigned_employee_ids') or [])))
        for eid in assigned:
            employee=db.get(Employee,eid)
            if not employee or not employee.active or employee.admin:
                abort(400,'Choose active employees for this work order.')
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
        w=db.scalar(select(WorkOrder).where(WorkOrder.id==work_order_id).with_for_update())
        if not w:abort(404,'Work order not found.')
        if 'status' in data:
            if data['status'] not in WORK_STATUSES:abort(400,'Choose a listed work order status.')
            w.status=data['status']
            if w.status in ('Completed','Closed'):w.completed_date=w.completed_date or now().astimezone(LOCAL).date().isoformat()
            else:w.completed_date=None
        for key in ('start_date','target_date'):
            if key in data:setattr(w,key,valid_iso_date(data[key],key.replace('_',' ')) if data[key] else None)
        if 'completed_date' in data and w.status in ('Completed','Closed'):
            w.completed_date=valid_iso_date(data['completed_date'],'completed date') if data['completed_date'] else w.completed_date
        if 'current_stage' in data:
            if data['current_stage'] and data['current_stage'] not in PROJECT_STAGES:abort(400,'Choose a listed project stage.')
            w.current_stage=data['current_stage'] or None
        for key,limit in [('title',180),('description',8000),('service_product',180),('customer_remarks',3000)]:
            if key in data:setattr(w,key,clean_text(data,key,limit) if key=='title' else str(data[key] or '').strip()[:limit] or None)
        if 'work_type' in data:
            if data['work_type'] not in ('Service','Manufacturing','Repair','Installation','Inspection','Preventive Maintenance'):abort(400,'Choose a listed work type.')
            w.work_type=data['work_type']
        if 'machine_id' in data:
            mid=optional_id(data['machine_id'],'machine');m=db.get(CustomerMachine,mid) if mid else None
            if mid and (not m or m.customer_id!=w.customer_id):abort(400,'Machine does not belong to this customer.')
            w.machine_id=mid
        if any(k in data for k in ('service_type','service_activities')):
            for key,value in service_fields(data,w).items():setattr(w,key,value)
        db.add(AuditLog(admin_id=request.employee.id,action='update_work_order',target=w.code or str(w.id),detail=w.status,created_at=now()))
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


def quantity(value, positive=False):
    try:
        n=Decimal(str(value))
        if not n.is_finite() or n.as_tuple().exponent < -3 or abs(n)>Decimal('99999999999.999') or (positive and n<=0) or (not positive and n<0):
            raise ValueError()
        return n
    except (InvalidOperation, ValueError, TypeError):
        abort(400,'Enter a valid quantity (up to three decimal places).')


def stock_json(item):
    return dict(id=item.id,sku=item.sku,name=item.name,category=item.category,
                sub_category=item.sub_category,size_dimension=item.size_dimension,
                material_finish=item.material_finish,
                specification=item.specification,unit=item.unit,location=item.location,
                reorder_level=str(item.reorder_level),quantity=str(item.quantity),active=item.active)


def reserved_total(db,item_id):
    return db.scalar(select(func.coalesce(func.sum(MaterialRequirement.reserved_qty),0))
                     .where(MaterialRequirement.item_id==item_id)) or Decimal(0)


def reserved_totals(db):
    return dict(db.execute(select(MaterialRequirement.item_id,func.sum(MaterialRequirement.reserved_qty))
                           .group_by(MaterialRequirement.item_id)).all())


@app.get('/api/stock-items')
@login_required(admin=True)
def list_stock_items():
    with DB() as db:
        out=[]
        reservations=reserved_totals(db)
        for x in db.scalars(select(StockItem).order_by(StockItem.name)):
            row=stock_json(x);reserved=reservations.get(x.id,Decimal(0))
            row.update(reserved=str(reserved),available=str(x.quantity-reserved));out.append(row)
        return jsonify(out)


@app.post('/api/stock-items')
@login_required(admin=True)
def create_stock_item():
    data=request.get_json(silent=True) or {}
    sku=clean_text(data,'sku',60).upper()
    with DB.begin() as db:
        if db.scalar(select(StockItem.id).where(StockItem.sku==sku)): abort(409,'SKU already exists.')
        category=clean_text(data,'category',50)
        sub_category=clean_text(data,'sub_category',80)
        size_dimension=clean_text(data,'size_dimension',80)
        material_finish=clean_text(data,'material_finish',80)
        name=' - '.join((category,sub_category,size_dimension,material_finish))
        if len(name)>180: abort(400,'Combined item name must be 180 characters or fewer.')
        item=StockItem(sku=sku,name=name,category=category,sub_category=sub_category,
                       size_dimension=size_dimension,material_finish=material_finish,
                       specification=str(data.get('specification') or '')[:5000] or None,
                       unit=clean_text(data,'unit',20),location=str(data.get('location') or '')[:100] or None,
                       reorder_level=quantity(data.get('reorder_level',0)),quantity=Decimal(0),active=True)
        db.add(item);db.flush()
        db.add(AuditLog(admin_id=request.employee.id,action='create_stock_item',target=sku,created_at=now()))
        return stock_json(item),201


@app.patch('/api/stock-items/<int:item_id>')
@login_required(admin=True)
def update_stock_item(item_id):
    data=request.get_json(silent=True) or {}
    with DB.begin() as db:
        item=db.get(StockItem,item_id)
        if not item: abort(404,'Item not found.')
        structured=('category','sub_category','size_dimension','material_finish')
        if any(key in data for key in structured):
            merged={key:data.get(key,getattr(item,key)) for key in structured}
            parts=[clean_text(merged,key,limit) for key,limit in zip(structured,(50,80,80,80))]
            name=' - '.join(parts)
            if len(name)>180: abort(400,'Combined item name must be 180 characters or fewer.')
            item.category,item.sub_category,item.size_dimension,item.material_finish=parts
            item.name=name
        for key,limit in {'specification':5000,'unit':20,'location':100}.items():
            if key in data:
                value=str(data[key] or '').strip()[:limit]
                if key in ('name','unit') and not value: abort(400,key+' is required.')
                setattr(item,key,value or None)
        if 'reorder_level' in data: item.reorder_level=quantity(data['reorder_level'])
        if 'active' in data: item.active=data['active'] is True
        db.add(AuditLog(admin_id=request.employee.id,action='update_stock_item',target=item.sku,
                        detail=json.dumps(data)[:5000],created_at=now()))
        return stock_json(item)


@app.get('/api/stock-items/<int:item_id>/movements')
@login_required(admin=True)
def stock_history(item_id):
    with DB() as db:
        if not db.get(StockItem,item_id): abort(404,'Item not found.')
        return jsonify([dict(id=m.id,kind=m.kind,change=str(m.quantity_change),balance=str(m.balance_after),
                             work_order_id=m.work_order_id,reference=m.reference,reason=m.reason,
                             actor_id=m.actor_id,at=aware(m.created_at).isoformat())
                        for m in db.scalars(select(StockMovement).where(StockMovement.item_id==item_id)
                                            .order_by(StockMovement.id.desc()).limit(200))])


@app.post('/api/stock-items/<int:item_id>/movements')
@login_required(admin=True)
def move_stock(item_id):
    data=request.get_json(silent=True) or {}
    kind=data.get('kind')
    if kind not in ('Receive','Issue','Return','Adjust'): abort(400,'Choose a stock movement type.')
    amount=quantity(data.get('quantity'),positive=True) if kind!='Adjust' else None
    if kind=='Adjust':
        try: amount=Decimal(str(data.get('quantity')))
        except (InvalidOperation,TypeError): abort(400,'Invalid adjustment.')
        if not amount.is_finite() or not amount or amount.as_tuple().exponent < -3 or abs(amount)>Decimal('99999999999.999'): abort(400,'Invalid adjustment.')
    elif kind=='Issue': amount=-amount
    reason=clean_text(data,'reason',500)
    work_order_id=data.get('work_order_id') or None
    with DB.begin() as db:
        item=db.scalar(select(StockItem).where(StockItem.id==item_id).with_for_update())
        if not item or not item.active: abort(404,'Active item not found.')
        if work_order_id:
            try: work_order_id=int(work_order_id)
            except (ValueError,TypeError): abort(400,'Invalid work order.')
            if not db.get(WorkOrder,work_order_id): abort(404,'Work order not found.')
        new_balance=item.quantity+amount
        if new_balance<0: abort(409,'Insufficient stock.')
        reserved=reserved_total(db,item.id)
        if new_balance<reserved: abort(409,'Quantity is reserved for jobs. Issue through the job material list or release its reservation first.')
        item.quantity=new_balance
        m=StockMovement(item_id=item.id,kind=kind,quantity_change=amount,balance_after=new_balance,
                        work_order_id=work_order_id,reference=str(data.get('reference') or '')[:100] or None,
                        reason=reason,actor_id=request.employee.id,created_at=now())
        db.add(m);db.flush()
        db.add(AuditLog(admin_id=request.employee.id,action='stock_movement',target=item.sku,
                        detail=f'{kind} {amount} {item.unit}: {reason}',created_at=now()))
        return dict(id=m.id,balance=str(new_balance)),201


@app.get('/api/suppliers')
@login_required(admin=True)
def list_suppliers():
    with DB() as db:
        return jsonify([dict(id=s.id,name=s.name,contact=s.contact,phone=s.phone,email=s.email,gstin=s.gstin,active=s.active)
                        for s in db.scalars(select(Supplier).order_by(Supplier.name))])


@app.post('/api/suppliers')
@login_required(admin=True)
def create_supplier():
    data=request.get_json(silent=True) or {}
    with DB.begin() as db:
        s=Supplier(name=clean_text(data,'name',180),contact=str(data.get('contact') or '')[:120] or None,
                   phone=str(data.get('phone') or '')[:40] or None,email=str(data.get('email') or '')[:160] or None,
                   gstin=str(data.get('gstin') or '')[:30] or None,active=True)
        db.add(s);db.flush()
        return dict(id=s.id,name=s.name),201


@app.get('/api/purchase-orders')
@login_required(admin=True)
def list_purchase_orders():
    with DB() as db:
        return jsonify([purchase_json(db,p) for p in db.scalars(select(PurchaseOrder).order_by(PurchaseOrder.id.desc()).limit(300))])


@app.post('/api/purchase-orders')
@login_required(admin=True)
def create_purchase_order():
    data=request.get_json(silent=True) or {}
    supplier_id=optional_id(data.get('supplier_id'),'supplier');item_id=optional_id(data.get('item_id'),'stock item')
    price=quantity(data.get('unit_price',0))
    if price<0:abort(400,'Unit price cannot be negative.')
    with DB.begin() as db:
        if not supplier_id or not item_id or not db.get(Supplier,supplier_id) or not db.get(StockItem,item_id):abort(404,'Supplier or item not found.')
        p=PurchaseOrder(supplier_id=supplier_id,item_id=item_id,ordered_qty=quantity(data.get('ordered_qty'),True),
                        received_qty=Decimal(0),unit_price=price,
                        expected_date=valid_iso_date(data['expected_date'],'expected delivery') if data.get('expected_date') else None,
                        supplier_reference=str(data.get('supplier_reference') or '').strip()[:100] or None,
                        notes=str(data.get('notes') or '').strip()[:3000] or None,
                        status='Open',created_by=request.employee.id,created_at=now())
        db.add(p);db.flush();return purchase_json(db,p),201


@app.post('/api/purchase-orders/<int:order_id>/receive')
@login_required(admin=True)
def receive_purchase_order(order_id):
    data=request.get_json(silent=True) or {}
    amount=quantity(data.get('quantity'),True)
    with DB.begin() as db:
        p=db.scalar(select(PurchaseOrder).where(PurchaseOrder.id==order_id).with_for_update())
        if not p: abort(404,'Purchase order not found.')
        if p.status in ('Cancelled','Closed','Received'):abort(409,'Reopen this purchase order before recording another receipt.')
        if p.received_qty+amount>p.ordered_qty:abort(409,'Receipt exceeds outstanding quantity.')
        item=db.scalar(select(StockItem).where(StockItem.id==p.item_id).with_for_update())
        p.received_qty+=amount
        p.status='Received' if p.received_qty==p.ordered_qty else 'Part received'
        item.quantity+=amount
        db.add(StockMovement(item_id=item.id,kind='Receive',quantity_change=amount,balance_after=item.quantity,
                             reference=f'PO-{p.id}',reason='Purchase order receipt',actor_id=request.employee.id,created_at=now()))
        db.add(AuditLog(admin_id=request.employee.id,action='receive_purchase_order',target=f'PO-{p.id}',
                        detail=f'{amount} {item.unit}',created_at=now()))
        return dict(id=p.id,status=p.status,received_qty=str(p.received_qty),balance=str(item.quantity))


# Half-point units avoid floating-point rounding in the disciplinary ledger.
MEMO_RULES = [
    dict(id='emergency_early',category='Attendance',label='Leaving early due to an unapproved emergency',minimum=0.5,maximum=0.5,source='Company policy section 3'),
    dict(id='late_30',category='Attendance',label='Late by 30 minutes or less without a valid reason',minimum=1,maximum=1,source='Company policy section 3'),
    dict(id='early_2h',category='Attendance',label='Unapproved departure up to 2 hours early',minimum=1,maximum=1,source='Company policy section 3'),
    dict(id='late_or_early_major',category='Attendance',label='More than 30 minutes late or more than 2 hours early, unapproved',minimum=2,maximum=2,source='Company policy section 3'),
    dict(id='absence_no_form',category='Attendance',label='Full-day absence without a formal leave form',minimum=3,maximum=3,source='Company policy section 3 and leave form'),
    dict(id='absence_no_notice',category='Attendance',label='Absence without prior notice',minimum=3,maximum=3,source='Company policy section 3 and leave form'),
    dict(id='behaviour',category='Behaviour',label='Documented disrespectful or disruptive workplace behaviour',minimum=0.5,maximum=3,source='Suggested portal range; not specified in policy'),
    dict(id='cleanliness',category='Cleanliness',label='Assigned work area or tools left unclean after instruction',minimum=0.5,maximum=2,source='Suggested portal range; not specified in policy'),
    dict(id='performance',category='Work performance',label='Verified avoidable quality failure or missed agreed task',minimum=0.5,maximum=3,source='Suggested portal range; not specified in policy'),
    dict(id='discipline',category='Discipline',label='Documented failure to follow a communicated work instruction',minimum=0.5,maximum=3,source='Suggested portal range; not specified in policy'),
    dict(id='safety',category='Safety',label='Verified failure to follow a communicated safety instruction',minimum=1,maximum=3,source='Suggested portal range; not specified in policy'),
]


@app.route('/api/employees/<int:employee_id>/memos', methods=['GET', 'POST'])
@login_required(admin=True)
def employee_memos(employee_id):
    with DB.begin() as db:
        employee = db.get(Employee, employee_id)
        if not employee or employee.admin: abort(404, 'Employee not found.')
        if request.method == 'POST':
            data=request.get_json() or {}
            rule=next((r for r in MEMO_RULES if r['id']==data.get('rule_id')),None)
            if not rule: abort(400, 'Choose a memo reason.')
            value=data.get('points')
            if type(value) not in (int,float) or not math.isfinite(value) or not rule['minimum']<=value<=rule['maximum'] or value*2!=int(value*2):
                abort(400, 'Points must match the selected range in steps of 0.5.')
            if data.get('reviewed') is not True: abort(400, 'Review the evidence, permission and leave exceptions before recording a memo.')
            fields={key:str(data.get(key) or '').strip() for key in ['details','evidence','employee_response','review_notes']}
            if not 3<=len(fields['details'])<=2000: abort(400, 'Describe the incident in 3 to 2000 characters.')
            if any(len(v)>2000 for v in fields.values()): abort(400, 'Each note must be 2000 characters or fewer.')
            event_date=str(data.get('date') or '')
            try:
                parsed=datetime.strptime(event_date,'%Y-%m-%d').date()
                if parsed.isoformat()!=event_date or parsed>now().astimezone(LOCAL).date(): raise ValueError()
            except ValueError: abort(400, 'Choose today or an earlier valid date.')
            if db.scalar(select(EmployeeMemo.id).where(EmployeeMemo.employee_id==employee_id,EmployeeMemo.event_date==event_date,
                         EmployeeMemo.rule_id==rule['id'],EmployeeMemo.details==fields['details'],EmployeeMemo.voided_at==None)):
                abort(409, 'This memo is already recorded. Review the existing entry.')
            # One full-day absence must not receive both absence penalties.
            if rule['id'].startswith('absence_') and db.scalar(select(EmployeeMemo.id).where(EmployeeMemo.employee_id==employee_id,
                    EmployeeMemo.event_date==event_date,EmployeeMemo.rule_id.in_(['absence_no_form','absence_no_notice']),EmployeeMemo.voided_at==None)):
                abort(409, 'An absence memo already exists for this employee and date. Do not count the same absence twice.')
            entry=EmployeeMemo(employee_id=employee_id,event_date=event_date,rule_id=rule['id'],rule_label=rule['label'],source=rule['source'],
                               half_points=int(value*2),**fields,recorded_by=request.employee.name,created_at=now())
            db.add(entry);db.flush()
            db.add(AuditLog(admin_id=request.employee.id,action='employee_memo_added',target=str(employee_id),detail=json.dumps(dict(memo_id=entry.id,points=value,rule_id=rule['id'])),created_at=now()))
            return dict(id=entry.id),201
        month=request.args.get('month') or now().astimezone(LOCAL).strftime('%Y-%m')
        try:
            if datetime.strptime(month,'%Y-%m').strftime('%Y-%m')!=month: raise ValueError()
        except ValueError: abort(400,'Choose a valid month.')
        rows=db.scalars(select(EmployeeMemo).where(EmployeeMemo.employee_id==employee_id,EmployeeMemo.event_date.startswith(month+'-')).order_by(EmployeeMemo.event_date.desc(),EmployeeMemo.id.desc())).all()
        total=db.scalar(select(func.coalesce(func.sum(EmployeeMemo.half_points),0)).where(EmployeeMemo.employee_id==employee_id,EmployeeMemo.voided_at==None))/2
        threshold=20 if total>=20 else 16 if total>=16 else 12 if total>=12 else 0
        return dict(rules=MEMO_RULES,total=total,monthly=sum(r.half_points for r in rows if not r.voided_at)/2,threshold=threshold,
                    entries=[dict(id=r.id,date=r.event_date,rule=r.rule_label,source=r.source,points=r.half_points/2,details=r.details,evidence=r.evidence,
                                  employee_response=r.employee_response,review_notes=r.review_notes,recorded_by=r.recorded_by,created_at=aware(r.created_at).isoformat(),
                                  voided_at=aware(r.voided_at).isoformat() if r.voided_at else None,void_reason=r.void_reason,voided_by=r.voided_by) for r in rows])


@app.post('/api/employees/<int:employee_id>/memos/<int:memo_id>/void')
@login_required(admin=True)
def void_employee_memo(employee_id,memo_id):
    reason=str((request.get_json() or {}).get('reason') or '').strip()
    if not 3<=len(reason)<=1000: abort(400,'Enter a correction reason between 3 and 1000 characters.')
    with DB.begin() as db:
        entry=db.scalar(select(EmployeeMemo).where(EmployeeMemo.id==memo_id,EmployeeMemo.employee_id==employee_id).with_for_update())
        if not entry: abort(404,'Memo not found.')
        if entry.voided_at: abort(409,'This memo is already voided.')
        entry.voided_at=now();entry.void_reason=reason;entry.voided_by=request.employee.name
        db.add(AuditLog(admin_id=request.employee.id,action='employee_memo_voided',target=str(employee_id),detail=json.dumps(dict(memo_id=memo_id,reason=reason)),created_at=now()))
    return dict(ok=True)


POINT_CATEGORIES = ['Work performance', 'Attendance', 'Behaviour', 'Cleanliness', 'Discipline', 'Safety', 'Teamwork', 'Initiative', 'Other']


@app.route('/api/employees/<int:employee_id>/points', methods=['GET', 'POST'])
@login_required(admin=True)
def employee_points(employee_id):
    with DB.begin() as db:
        employee = db.get(Employee, employee_id)
        if not employee or employee.admin: abort(404, 'Employee not found.')
        if request.method == 'POST':
            data = request.get_json() or {}
            value = data.get('points')
            if type(value) is not int or value == 0 or abs(value) > 100:
                abort(400, 'Enter whole points from 1 to 100 to award or deduct.')
            category = data.get('category')
            if category not in POINT_CATEGORIES: abort(400, 'Choose a points category.')
            reason = str(data.get('reason') or '').strip()
            if not 3 <= len(reason) <= 1000: abort(400, 'Enter a reason between 3 and 1000 characters.')
            event_date = str(data.get('date') or '')
            try:
                parsed = datetime.strptime(event_date, '%Y-%m-%d').date()
                if parsed.isoformat() != event_date or parsed > now().astimezone(LOCAL).date(): raise ValueError()
            except ValueError: abort(400, 'Choose today or an earlier valid date.')
            entry = EmployeePoint(employee_id=employee_id,event_date=event_date,category=category,
                                  points=value,reason=reason,recorded_by=request.employee.name,created_at=now())
            db.add(entry)
            db.flush()
            db.add(AuditLog(admin_id=request.employee.id,action='employee_points_added',target=str(employee_id),
                            detail=json.dumps(dict(entry_id=entry.id,points=value,category=category,reason=reason)),created_at=now()))
            return dict(id=entry.id), 201
        month = request.args.get('month') or now().astimezone(LOCAL).strftime('%Y-%m')
        try:
            if datetime.strptime(month, '%Y-%m').strftime('%Y-%m') != month: raise ValueError()
        except ValueError: abort(400, 'Choose a valid month.')
        rows = db.scalars(select(EmployeePoint).where(EmployeePoint.employee_id==employee_id,
                          EmployeePoint.event_date.startswith(month+'-')).order_by(EmployeePoint.event_date.desc(),EmployeePoint.id.desc())).all()
        valid = [r for r in rows if r.voided_at is None]
        awarded = sum(r.points for r in valid if r.points>0)
        deducted = -sum(r.points for r in valid if r.points<0)
        all_time = db.scalar(select(func.coalesce(func.sum(EmployeePoint.points),0)).where(
            EmployeePoint.employee_id==employee_id,EmployeePoint.voided_at==None))
        return dict(month=month,categories=POINT_CATEGORIES,awarded=awarded,deducted=deducted,net=awarded-deducted,
                    all_time=all_time,by_category=[dict(category=c,points=sum(r.points for r in valid if r.category==c)) for c in POINT_CATEGORIES],
                    entries=[dict(id=r.id,date=r.event_date,category=r.category,points=r.points,reason=r.reason,
                                  recorded_by=r.recorded_by,created_at=aware(r.created_at).isoformat(),void_reason=r.void_reason,
                                  voided_by=r.voided_by,voided_at=aware(r.voided_at).isoformat() if r.voided_at else None) for r in rows])


@app.post('/api/employees/<int:employee_id>/points/<int:entry_id>/void')
@login_required(admin=True)
def void_employee_points(employee_id, entry_id):
    reason = str((request.get_json() or {}).get('reason') or '').strip()
    if not 3 <= len(reason) <= 1000: abort(400, 'Enter a correction reason between 3 and 1000 characters.')
    with DB.begin() as db:
        entry = db.scalar(select(EmployeePoint).where(EmployeePoint.id==entry_id,EmployeePoint.employee_id==employee_id).with_for_update())
        if not entry: abort(404, 'Points entry not found.')
        if entry.voided_at: abort(409, 'This entry has already been voided.')
        entry.voided_at=now()
        entry.voided_by=request.employee.name
        entry.void_reason=reason
        db.add(AuditLog(admin_id=request.employee.id,action='employee_points_voided',target=str(employee_id),
                       detail=json.dumps(dict(entry_id=entry_id,reason=reason)),created_at=now()))
    return dict(ok=True)


@app.get('/api/employees/<int:employee_id>/profile')
@login_required(admin=True)
def employee_monthly_profile(employee_id):
    month=str(request.args.get('month') or now().astimezone(LOCAL).strftime('%Y-%m'))
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',month): abort(400,'Choose a valid month.')
    start=month+'-01'
    end=(datetime.strptime(start,'%Y-%m-%d').replace(day=28)+timedelta(days=4)).replace(day=1).date().isoformat()
    today=now().astimezone(LOCAL).date().isoformat()
    with DB() as db:
        employee=db.get(Employee,employee_id)
        if not employee or employee.admin: abort(404,'Employee not found.')
        profile=db.scalar(select(EmployeeProfile).where(EmployeeProfile.employee_id==employee_id))
        company_days=set(db.scalars(select(Attendance.work_date).where(
            Attendance.work_date>=start,Attendance.work_date<end,Attendance.work_date<=today)).all())
        present=set(db.scalars(select(Attendance.work_date).where(
            Attendance.employee_id==employee_id,Attendance.work_date>=start,
            Attendance.work_date<end,Attendance.work_date<=today)).all())
        plans=db.scalars(select(DailyPlan).where(DailyPlan.employee_id==employee_id,
                          DailyPlan.work_date>=start,DailyPlan.work_date<end).order_by(DailyPlan.work_date.desc())).all()
        due_plans=[p for p in plans if p.work_date<=today and p.status!='Cancelled']
        done_plans=[p for p in due_plans if p.status in ('Completed','Closed')]
        reports=db.scalars(select(WorkReport).where(WorkReport.employee_id==employee_id,
                           WorkReport.work_date>=start,WorkReport.work_date<end)
                           .order_by(WorkReport.work_date.desc(),WorkReport.id.desc())).all()
        assigned_ids=assigned_order_ids(employee_id)
        completed_jobs=db.scalars(select(WorkOrder).where(WorkOrder.id.in_(assigned_ids),
                                 WorkOrder.status.in_(['Completed','Closed']),
                                 WorkOrder.completed_date>=start,WorkOrder.completed_date<end)).all()
        attendance_percent=round(100*len(present&company_days)/len(company_days),1) if company_days else None
        completion_percent=round(100*len(done_plans)/len(due_plans),1) if due_plans else None
        # Activity points describe recorded actions; they are not an employee rating.
        points=len(present&company_days)+2*len(done_plans)+3*len(completed_jobs)+len(reports)
        return dict(employee=person(employee),month=month,
                    designation=profile.designation if profile else None,
                    joining_date=profile.joining_date if profile else None,
                    skills=profile.skills if profile else None,
                    attendance_days=len(present&company_days),company_recorded_days=len(company_days),
                    attendance_percent=attendance_percent,
                    planned_tasks=len(plans),due_tasks=len(due_plans),completed_tasks=len(done_plans),
                    blocked_tasks=sum(p.status=='Blocked' for p in due_plans),
                    completion_percent=completion_percent,
                    completed_work_orders=len(completed_jobs),work_reports=len(reports),
                    completed_work_reports=sum(r.status.lower()=='completed' for r in reports),
                    activity_points=points,
                    recent_tasks=[daily_plan_json(db,p) for p in plans[:20]],
                    recent_reports=[work_report_json(r,employee) for r in reports[:20]],
                    completed_jobs=[work_order_json(db,w) for w in completed_jobs[:20]])


@app.get('/api/employee-files/<int:employee_id>')
@login_required(admin=True)
def employee_file(employee_id):
    with DB() as db:
        e=db.get(Employee,employee_id)
        if not e: abort(404,'Employee not found.')
        p=db.scalar(select(EmployeeProfile).where(EmployeeProfile.employee_id==employee_id))
        reports=db.scalars(select(WorkReport).where(WorkReport.employee_id==employee_id).order_by(WorkReport.id.desc()).limit(30)).all()
        return dict(employee=person(e),profile=dict(designation=p.designation,joining_date=p.joining_date,
                    supervisor_id=p.supervisor_id,skills=p.skills,notes=p.notes) if p else {},
                    recent_work=[dict(date=r.work_date,job_no=r.job_no,status=r.status,details=r.work_details) for r in reports])


@app.patch('/api/employee-files/<int:employee_id>')
@login_required(admin=True)
def update_employee_file(employee_id):
    data=request.get_json(silent=True) or {}
    with DB.begin() as db:
        if not db.get(Employee,employee_id): abort(404,'Employee not found.')
        p=db.scalar(select(EmployeeProfile).where(EmployeeProfile.employee_id==employee_id))
        if not p: p=EmployeeProfile(employee_id=employee_id);db.add(p)
        for key,limit in {'designation':100,'joining_date':10,'skills':5000,'notes':5000}.items():
            if key in data: setattr(p,key,str(data[key] or '').strip()[:limit] or None)
        if 'joining_date' in data:
            p.joining_date=valid_iso_date(data['joining_date'],'joining date') if data['joining_date'] else None
        if 'supervisor_id' in data:
            try: sid=int(data['supervisor_id']) if data['supervisor_id'] else None
            except (TypeError,ValueError): abort(400,'Invalid supervisor.')
            if sid and not db.get(Employee,sid): abort(404,'Supervisor not found.')
            p.supervisor_id=sid
        db.add(AuditLog(admin_id=request.employee.id,action='update_employee_file',target=str(employee_id),
                        detail=','.join(data.keys())[:500],created_at=now()))
        return {'ok':True}


def step_json(s):
    return dict(id=s.id,work_order_id=s.work_order_id,sequence=s.sequence,operation=s.operation,
                assigned_id=s.assigned_id,planned_date=s.planned_date,planned_hours=str(s.planned_hours),
                actual_hours=str(s.actual_hours),accepted_qty=str(s.accepted_qty),rejected_qty=str(s.rejected_qty),
                status=s.status,delay_reason=s.delay_reason)


@app.get('/api/production-steps')
@login_required(admin=True)
def list_production_steps():
    with DB() as db:
        return jsonify([step_json(s) for s in db.scalars(select(ProductionStep).order_by(ProductionStep.work_order_id,ProductionStep.sequence).limit(500))])


@app.post('/api/production-steps')
@login_required(admin=True)
def create_production_step():
    data=request.get_json(silent=True) or {}
    try: job_id=int(data.get('work_order_id'));seq=int(data.get('sequence',1))
    except (TypeError,ValueError): abort(400,'Select a work order and sequence.')
    with DB.begin() as db:
        if not db.get(WorkOrder,job_id): abort(404,'Work order not found.')
        s=ProductionStep(work_order_id=job_id,sequence=seq,operation=clean_text(data,'operation',80),
                         planned_date=valid_iso_date(data['planned_date'],'planned date') if data.get('planned_date') else None,
                         planned_hours=quantity(data.get('planned_hours',0)),actual_hours=Decimal(0),
                         accepted_qty=Decimal(0),rejected_qty=Decimal(0),status='Planned')
        db.add(s);db.flush();return step_json(s),201


@app.patch('/api/production-steps/<int:step_id>')
@login_required(admin=True)
def update_production_step(step_id):
    data=request.get_json(silent=True) or {}
    with DB.begin() as db:
        s=db.get(ProductionStep,step_id)
        if not s: abort(404,'Production step not found.')
        if 'status' in data:
            if data['status'] not in PLAN_STATUSES: abort(400,'Invalid status.')
            s.status=data['status']
        for key in ('actual_hours','accepted_qty','rejected_qty'):
            if key in data: setattr(s,key,quantity(data[key]))
        if 'delay_reason' in data: s.delay_reason=str(data['delay_reason'] or '')[:500] or None
        db.add(AuditLog(admin_id=request.employee.id,action='update_production_step',target=str(s.id),
                        detail=json.dumps(data)[:1000],created_at=now()))
        return step_json(s)


def asset_json(a):
    return dict(id=a.id,code=a.code,kind=a.kind,name=a.name,model=a.model,
                serial_or_registration=a.serial_or_registration,location=a.location,
                next_service_date=a.next_service_date,active=a.active)


@app.get('/api/company-assets')
@login_required(admin=True)
def list_company_assets():
    with DB() as db: return jsonify([asset_json(a) for a in db.scalars(select(CompanyAsset).order_by(CompanyAsset.code))])


@app.post('/api/company-assets')
@login_required(admin=True)
def create_company_asset():
    data=request.get_json(silent=True) or {}
    if data.get('kind') not in ('Machine','Bike','Vehicle','Tool','Equipment'): abort(400,'Choose Machine, Bike, Vehicle, Tool or Equipment.')
    with DB.begin() as db:
        a=CompanyAsset(code=clean_text(data,'code',60).upper(),kind=data['kind'],name=clean_text(data,'name',160),
                       model=str(data.get('model') or '')[:120] or None,
                       serial_or_registration=str(data.get('serial_or_registration') or '')[:100] or None,
                       location=str(data.get('location') or '')[:100] or None,
                       next_service_date=valid_iso_date(data['next_service_date'],'next service date') if data.get('next_service_date') else None,active=True)
        db.add(a);db.flush();return asset_json(a),201


@app.patch('/api/company-assets/<int:asset_id>')
@login_required(admin=True)
def update_company_asset(asset_id):
    data=request.get_json(silent=True) or {}
    with DB.begin() as db:
        a=db.get(CompanyAsset,asset_id)
        if not a: abort(404,'Asset not found.')
        if 'kind' in data:
            if data['kind'] not in ('Machine','Bike','Vehicle','Tool','Equipment'): abort(400,'Choose Machine, Bike, Vehicle, Tool or Equipment.')
            a.kind=data['kind']
        for key,limit in {'code':60,'name':160,'model':120,'serial_or_registration':100,'location':100}.items():
            if key in data:
                value=str(data[key] or '').strip()
                if len(value)>limit or (key in ('code','name') and not value): abort(400,'Invalid '+key+'.')
                if key=='code':
                    value=value.upper()
                    if db.scalar(select(CompanyAsset.id).where(CompanyAsset.code==value,CompanyAsset.id!=asset_id)):
                        abort(409,'Asset code already exists.')
                setattr(a,key,value or None)
        if 'next_service_date' in data:
            a.next_service_date=valid_iso_date(data['next_service_date'],'next service date') if data['next_service_date'] else None
        if 'active' in data: a.active=data['active'] is True
        db.add(AuditLog(admin_id=request.employee.id,action='update_company_asset',target=str(a.id),detail=json.dumps(data)[:1000],created_at=now()))
        return asset_json(a)


@app.get('/api/maintenance-tasks')
@login_required(admin=True)
def list_maintenance_tasks():
    with DB() as db:
        return jsonify([maintenance_json(db,t) for t in db.scalars(select(MaintenanceTask).order_by(MaintenanceTask.id.desc()).limit(300))])


@app.post('/api/maintenance-tasks')
@login_required(admin=True)
def create_maintenance_task():
    data=request.get_json(silent=True) or {}
    with DB.begin() as db:
        if not data.get('asset_id') or not data.get('task_type') or not data.get('description'):abort(400,'Select an asset, service type and description.')
        t=MaintenanceTask(task_type='Inspection',description='',status='Open',downtime_hours=Decimal(0),cost=Decimal(0),created_at=now())
        apply_maintenance_fields(db,t,data)
        db.add(t);db.flush()
        db.add(AuditLog(admin_id=request.employee.id,action='create_maintenance_task',target=str(t.id),detail=t.task_type,created_at=now()))
        return maintenance_json(db,t),201


@app.patch('/api/maintenance-tasks/<int:task_id>')
@login_required(admin=True)
def update_maintenance_task(task_id):
    data=request.get_json(silent=True) or {}
    with DB.begin() as db:
        t=db.scalar(select(MaintenanceTask).where(MaintenanceTask.id==task_id).with_for_update())
        if not t:abort(404,'Task not found.')
        apply_maintenance_fields(db,t,data)
        db.add(AuditLog(admin_id=request.employee.id,action='update_maintenance_task',target=str(t.id),detail=json.dumps(data)[:1000],created_at=now()))
        return maintenance_json(db,t)


@app.get('/api/storage-summary')
@login_required(admin=True)
def storage_summary():
    with DB() as db:
        size=db.scalar(select(func.pg_database_size(func.current_database()))) if engine.dialect.name=='postgresql' else None
        documents=db.scalar(select(func.count(PrivateDocument.id))) or 0
        document_bytes=db.scalar(select(func.coalesce(func.sum(func.length(PrivateDocument.content)),0))) or 0
        provider='Neon PostgreSQL' if '.neon.tech' in (engine.url.host or '') else engine.dialect.name
        return dict(provider=provider,database_bytes=size,document_count=documents,
                    document_bytes=document_bytes,upload_limit_bytes=2000000)


@app.route('/api/deadline-reminders',methods=['GET','POST'])
@login_required(admin=True)
def deadline_reminders():
    with DB.begin() as db:
        if request.method=='POST':
            data=request.get_json() or {}
            title=str(data.get('title') or '').strip()
            notes=str(data.get('notes') or '').strip()
            if not 1<=len(title)<=180 or len(notes)>2000: abort(400,'Enter a title up to 180 characters and notes up to 2000 characters.')
            due=valid_iso_date(data.get('due_date'),'deadline')
            r=DeadlineReminder(title=title,due_date=due,notes=notes,created_by=request.employee.id,created_at=now())
            db.add(r);db.flush()
            db.add(AuditLog(admin_id=request.employee.id,action='reminder_created',target=str(r.id),detail=title,created_at=now()))
            return dict(id=r.id),201
        today=now().astimezone(LOCAL).date()
        rows=[]
        def add(kind,r,date,title,view):
            if not date:return
            try: remaining=(datetime.strptime(date,'%Y-%m-%d').date()-today).days
            except (ValueError,TypeError):return
            rows.append(dict(key=f'{kind}:{r.id}',id=r.id,kind=kind,title=title,due_date=date,days=remaining,view=view,
                             urgency='Overdue' if remaining<0 else 'Due today' if remaining==0 else 'Due tomorrow' if remaining==1 else 'Next 7 days' if remaining<=7 else 'Upcoming',
                             notes=r.notes if kind=='Custom' else None))
        for r in db.scalars(select(WorkOrder).where(WorkOrder.status.notin_(['Completed','Closed','Cancelled']))):add('Work order',r,r.target_date,r.code+' · '+r.title,'jobs')
        for r in db.scalars(select(DailyPlan).where(DailyPlan.status.notin_(TERMINAL_STATUSES))):add('Planned task',r,r.due_date or r.work_date,r.title,'planning')
        for r in db.scalars(select(ProductionStep).where(ProductionStep.status.notin_(TERMINAL_STATUSES))):add('Production',r,r.planned_date,f'Order #{r.work_order_id} · '+r.operation,'production')
        for r in db.scalars(select(MaintenanceTask).where(MaintenanceTask.status.notin_(TERMINAL_STATUSES))):add('Maintenance',r,r.due_date,r.description,'maintenance')
        for r in db.scalars(select(CompanyAsset).where(CompanyAsset.active==True)):add('Asset service',r,r.next_service_date,r.code+' · '+r.name,'maintenance')
        for r in db.scalars(select(PurchaseOrder).where(PurchaseOrder.status.notin_(['Received','Closed','Cancelled']))):add('Purchase delivery',r,r.expected_date,f'Purchase order #{r.id}','purchasing')
        for r in db.scalars(select(Quotation).where(Quotation.status.notin_(['Accepted','Lost','Cancelled']))):
            add('Quotation follow-up',r,r.follow_up_date,r.code+' · '+r.title,'quotations')
            add('Quotation promised date',r,r.promised_date,r.code+' · '+r.title,'quotations')
        for r in db.scalars(select(MeetingAction).where(MeetingAction.status.notin_(['Completed','Closed','Cancelled','Done']))):add('Meeting action',r,r.due_date,r.action,'meetings')
        for r in db.scalars(select(DeadlineReminder).where(DeadlineReminder.completed==False)):add('Custom',r,r.due_date,r.title,'reminders')
        for r,company in db.execute(select(CustomerMachine,Customer).join(Customer,Customer.id==CustomerMachine.customer_id).where(CustomerMachine.status!='Out of service',CustomerMachine.next_service_date!=None)):
            add('Machine service',r,r.next_service_date,f'{company.name} · {r.customer_machine_no or r.model or r.code}','machines')
        reservations=reserved_totals(db)
        for item in db.scalars(select(StockItem).where(StockItem.active==True)):
            available=item.quantity-reservations.get(item.id,Decimal(0))
            if available<=item.reorder_level:
                rows.append(dict(key=f'Low stock:{item.id}',id=item.id,kind='Low stock',title=item.sku+' · '+item.name,
                                 due_date=None,days=None,view='inventory',urgency='Low stock',
                                 notes=f'{available} {item.unit} available after reservations; reorder threshold {item.reorder_level} {item.unit}.'))
        rows.sort(key=lambda r:(r['due_date'] or today.isoformat(),r['kind'],r['id']))
        return dict(today=today.isoformat(),timezone=str(LOCAL),items=rows,overdue=sum(r['days'] is not None and r['days']<0 for r in rows),due_today=sum(r['days']==0 for r in rows),next_week=sum(r['days'] is not None and 0<r['days']<=7 for r in rows),low_stock=sum(r['kind']=='Low stock' for r in rows))


@app.patch('/api/deadline-reminders/<int:reminder_id>')
@login_required(admin=True)
def update_deadline_reminder(reminder_id):
    data=request.get_json() or {}
    with DB.begin() as db:
        r=db.get(DeadlineReminder,reminder_id)
        if not r:abort(404,'Reminder not found.')
        if 'completed' in data:
            if type(data['completed']) is not bool:abort(400,'Choose a valid completion status.')
            r.completed=data['completed']
        if 'due_date' in data:r.due_date=valid_iso_date(data['due_date'],'deadline')
        db.add(AuditLog(admin_id=request.employee.id,action='reminder_updated',target=str(r.id),detail=json.dumps(data),created_at=now()))
    return dict(ok=True)


@app.get('/api/management-summary')
@login_required(admin=True)
def management_summary():
    with DB() as db:
        jobs=db.scalars(select(WorkOrder)).all()
        steps=db.scalars(select(ProductionStep)).all()
        items=db.scalars(select(StockItem).where(StockItem.active==True)).all()
        tasks=db.scalars(select(MaintenanceTask)).all()
        today=now().astimezone(LOCAL).date().isoformat()
        reservations=reserved_totals(db)
        return dict(open_jobs=sum(j.status not in ('Completed','Closed','Cancelled') for j in jobs),
                    overdue_jobs=sum(j.target_date is not None and j.target_date<today and j.status not in ('Completed','Closed','Cancelled') for j in jobs),
                    blocked_steps=sum(s.status=='Blocked' for s in steps),
                    accepted_quantity=str(sum((s.accepted_qty for s in steps),Decimal(0))),
                    rejected_quantity=str(sum((s.rejected_qty for s in steps),Decimal(0))),
                    low_stock=sum(i.quantity-reservations.get(i.id,Decimal(0))<=i.reorder_level for i in items),
                    open_maintenance=sum(t.status not in TERMINAL_STATUSES for t in tasks))


def quote_json(db,q):
    c=db.get(Customer,q.customer_id)
    return dict(id=q.id,code=q.code,customer_id=q.customer_id,customer=c.name if c else None,
                title=q.title,description=q.description,revision=q.revision,amount=str(q.amount),
                status=q.status,follow_up_date=q.follow_up_date,promised_date=q.promised_date,
                customer_po=q.customer_po,work_order_id=q.work_order_id)


@app.get('/api/quotations')
@login_required(admin=True)
def list_quotations():
    with DB() as db:
        return jsonify([quote_json(db,q) for q in db.scalars(select(Quotation).order_by(Quotation.id.desc()).limit(300))])


@app.post('/api/quotations')
@login_required(admin=True)
def create_quotation():
    data=request.get_json(silent=True) or {}
    try: customer_id=int(data.get('customer_id'))
    except (TypeError,ValueError): abort(400,'Select a customer.')
    with DB.begin() as db:
        if not db.get(Customer,customer_id): abort(404,'Customer not found.')
        q=Quotation(customer_id=customer_id,title=clean_text(data,'title',180),
                    description=str(data.get('description') or '')[:5000] or None,
                    amount=quantity(data.get('amount',0)),status='Draft',
                    follow_up_date=str(data.get('follow_up_date') or '')[:10] or None,
                    promised_date=str(data.get('promised_date') or '')[:10] or None,created_at=now())
        q.code='TMP-'+secrets.token_hex(8);db.add(q);db.flush();q.code=f'QT-{q.id:05d}'
        return quote_json(db,q),201


@app.patch('/api/quotations/<int:quote_id>')
@login_required(admin=True)
def update_quotation(quote_id):
    data=request.get_json(silent=True) or {}
    with DB.begin() as db:
        q=db.get(Quotation,quote_id)
        if not q: abort(404,'Quotation not found.')
        if q.work_order_id: abort(409,'Accepted quotation is locked. Record changes against the work order.')
        for key,limit in {'title':180,'description':5000,'follow_up_date':10,'promised_date':10}.items():
            if key in data:
                value=str(data[key] or '').strip()[:limit] or None
                if key=='title' and not value: abort(400,'Title is required.')
                setattr(q,key,value)
        if 'amount' in data: q.amount=quantity(data['amount'])
        if 'status' in data:
            if data['status'] not in ('Draft','Sent','Negotiation','Lost'): abort(400,'Invalid quotation status.')
            q.status=data['status']
        q.revision+=1
        db.add(AuditLog(admin_id=request.employee.id,action='update_quotation',target=q.code,
                        detail=json.dumps(data)[:1000],created_at=now()))
        return quote_json(db,q)


@app.post('/api/quotations/<int:quote_id>/accept')
@login_required(admin=True)
def accept_quotation(quote_id):
    data=request.get_json(silent=True) or {}
    po=clean_text(data,'customer_po',100)
    with DB.begin() as db:
        q=db.scalar(select(Quotation).where(Quotation.id==quote_id).with_for_update())
        if not q: abort(404,'Quotation not found.')
        if q.status=='Lost' or q.work_order_id: abort(409,'Quotation cannot be accepted.')
        w=WorkOrder(customer_id=q.customer_id,title=q.title,work_type='Manufacturing',
                    description=q.description,target_date=q.promised_date,status='Open',priority='Normal',created_at=now())
        db.add(w);db.flush();w.code=f'WO-{w.id:05d}'
        q.customer_po=po;q.status='Accepted';q.work_order_id=w.id
        db.add(AuditLog(admin_id=request.employee.id,action='accept_quotation',target=q.code,
                        detail=f'{po} -> {w.code}',created_at=now()))
        return dict(quotation=quote_json(db,q),work_order=work_order_json(db,w)),201


@app.get('/api/work-orders/<int:job_id>/materials')
@login_required(admin=True)
def list_job_materials(job_id):
    with DB() as db:
        if not db.get(WorkOrder,job_id): abort(404,'Work order not found.')
        return jsonify([dict(id=r.id,item_id=r.item_id,sku=db.get(StockItem,r.item_id).sku,
                             item=db.get(StockItem,r.item_id).name,required=str(r.required_qty),
                             reserved=str(r.reserved_qty),issued=str(r.issued_qty))
                        for r in db.scalars(select(MaterialRequirement).where(MaterialRequirement.work_order_id==job_id))])


@app.post('/api/work-orders/<int:job_id>/materials')
@login_required(admin=True)
def add_job_material(job_id):
    data=request.get_json(silent=True) or {}
    try: item_id=int(data.get('item_id'))
    except (TypeError,ValueError): abort(400,'Select an item.')
    amount=quantity(data.get('required_qty'),True)
    with DB.begin() as db:
        if not db.get(WorkOrder,job_id) or not db.get(StockItem,item_id): abort(404,'Job or item not found.')
        if db.scalar(select(MaterialRequirement.id).where(MaterialRequirement.work_order_id==job_id,MaterialRequirement.item_id==item_id)):
            abort(409,'Item already exists on this job.')
        r=MaterialRequirement(work_order_id=job_id,item_id=item_id,required_qty=amount,
                              reserved_qty=Decimal(0),issued_qty=Decimal(0))
        db.add(r);db.flush();return {'id':r.id,'required':str(r.required_qty)},201


@app.post('/api/job-materials/<int:requirement_id>/<action>')
@login_required(admin=True)
def change_job_material(requirement_id,action):
    if action not in ('reserve','release','issue'): abort(404)
    data=request.get_json(silent=True) or {}
    amount=quantity(data.get('quantity'),True)
    with DB.begin() as db:
        # Lock the item first for consistent availability across concurrent jobs.
        item_id=db.scalar(select(MaterialRequirement.item_id).where(MaterialRequirement.id==requirement_id))
        if not item_id: abort(404,'Material requirement not found.')
        item=db.scalar(select(StockItem).where(StockItem.id==item_id).with_for_update())
        r=db.scalar(select(MaterialRequirement).where(MaterialRequirement.id==requirement_id).with_for_update())
        if action=='reserve':
            if amount>r.required_qty-r.issued_qty-r.reserved_qty: abort(409,'Reservation exceeds remaining requirement.')
            if amount>item.quantity-reserved_total(db,item.id): abort(409,'Insufficient available stock.')
            r.reserved_qty+=amount
        elif action=='release':
            if amount>r.reserved_qty: abort(409,'Cannot release more than reserved.')
            r.reserved_qty-=amount
        else:
            if amount>r.reserved_qty: abort(409,'Reserve this material before issuing it.')
            if amount>item.quantity: abort(409,'Insufficient stock.')
            r.reserved_qty-=amount;r.issued_qty+=amount;item.quantity-=amount
            db.add(StockMovement(item_id=item.id,kind='Issue',quantity_change=-amount,balance_after=item.quantity,
                                 work_order_id=r.work_order_id,reference=f'JOB-{r.work_order_id}',
                                 reason='Issued against reserved job material',actor_id=request.employee.id,created_at=now()))
        db.add(AuditLog(admin_id=request.employee.id,action=f'material_{action}',target=f'job:{r.work_order_id}',
                        detail=f'{item.sku} {amount}',created_at=now()))
        return dict(id=r.id,reserved=str(r.reserved_qty),issued=str(r.issued_qty),available=str(item.quantity-reserved_total(db,item.id)))


@app.get('/api/work-orders/<int:job_id>/drawings')
@login_required(admin=True)
def list_drawings(job_id):
    with DB() as db:
        if not db.get(WorkOrder,job_id): abort(404,'Work order not found.')
        return jsonify([dict(id=d.id,drawing_no=d.drawing_no,revision=d.revision,
                             file_reference=d.file_reference,notes=d.notes,approved=d.approved,
                             approved_by=d.approved_by)
                        for d in db.scalars(select(DrawingRevision).where(DrawingRevision.work_order_id==job_id)
                                            .order_by(DrawingRevision.id.desc()))])


@app.post('/api/work-orders/<int:job_id>/drawings')
@login_required(admin=True)
def add_drawing(job_id):
    data=request.get_json(silent=True) or {}
    with DB.begin() as db:
        if not db.get(WorkOrder,job_id): abort(404,'Work order not found.')
        d=DrawingRevision(work_order_id=job_id,drawing_no=clean_text(data,'drawing_no',100),
                          revision=clean_text(data,'revision',30),file_reference=clean_text(data,'file_reference',500),
                          notes=str(data.get('notes') or '')[:5000] or None,approved=False,created_at=now())
        db.add(d);db.flush();return {'id':d.id,'approved':False},201


@app.post('/api/drawings/<int:drawing_id>/approve')
@login_required(admin=True)
def approve_drawing(drawing_id):
    with DB.begin() as db:
        d=db.get(DrawingRevision,drawing_id)
        if not d: abort(404,'Drawing not found.')
        for old in db.scalars(select(DrawingRevision).where(DrawingRevision.work_order_id==d.work_order_id,
                                                            DrawingRevision.drawing_no==d.drawing_no).with_for_update()):
            old.approved=old.id==d.id
            old.approved_by=request.employee.id if old.id==d.id else None
        db.add(AuditLog(admin_id=request.employee.id,action='approve_drawing',target=str(d.id),
                        detail=f'{d.drawing_no} revision {d.revision}',created_at=now()))
        return {'id':d.id,'approved':True}


def verify_document_owner(db,owner_type,owner_id):
    models={'employee':Employee,'job':WorkOrder,'customer':Customer}
    if owner_type not in models or not db.get(models[owner_type],owner_id): abort(404,'Record not found.')


@app.get('/api/documents/<owner_type>/<int:owner_id>')
@login_required(admin=True)
def list_documents(owner_type,owner_id):
    with DB() as db:
        verify_document_owner(db,owner_type,owner_id)
        return jsonify([dict(id=d.id,filename=d.filename,mime=d.mime,uploaded_at=aware(d.created_at).isoformat())
                        for d in db.scalars(select(PrivateDocument).where(PrivateDocument.owner_type==owner_type,
                            PrivateDocument.owner_id==owner_id).order_by(PrivateDocument.id.desc()))])


@app.post('/api/documents/<owner_type>/<int:owner_id>')
@login_required(admin=True)
def upload_document(owner_type,owner_id):
    uploaded=request.files.get('file')
    if not uploaded or not uploaded.filename: abort(400,'Choose a document.')
    name=os.path.basename(uploaded.filename.replace('\\','/'))[:180]
    ext=name.rsplit('.',1)[-1].lower() if '.' in name else ''
    types={'pdf':'application/pdf','png':'image/png','jpg':'image/jpeg','jpeg':'image/jpeg',
           'dxf':'application/octet-stream','dwg':'application/octet-stream','step':'application/octet-stream',
           'stp':'application/octet-stream'}
    if ext not in types: abort(400,'Supported files: PDF, PNG, JPG, DXF, DWG and STEP.')
    content=uploaded.stream.read(2_000_001)
    if not content or len(content)>2_000_000: abort(413,'File must be 2 MB or smaller.')
    if (ext=='pdf' and not content.startswith(b'%PDF-')) or (ext=='png' and not content.startswith(b'\x89PNG')) or (ext in ('jpg','jpeg') and not content.startswith(b'\xff\xd8')):
        abort(400,'File content does not match its extension.')
    with DB.begin() as db:
        verify_document_owner(db,owner_type,owner_id)
        d=PrivateDocument(owner_type=owner_type,owner_id=owner_id,filename=name,mime=types[ext],
                          content=content,uploaded_by=request.employee.id,created_at=now())
        db.add(d);db.flush()
        db.add(AuditLog(admin_id=request.employee.id,action='upload_document',target=f'{owner_type}:{owner_id}',
                        detail=f'{d.id} {name}',created_at=now()))
        return {'id':d.id,'filename':name},201


@app.get('/api/documents/download/<int:document_id>')
@login_required(admin=True)
def download_document(document_id):
    with DB() as db:
        d=db.get(PrivateDocument,document_id)
        if not d: abort(404,'Document not found.')
        safe_name=d.filename.replace('"','').replace('\n','')
        return Response(d.content,mimetype=d.mime,headers={'Content-Disposition':f'attachment; filename="{safe_name}"'})


@app.get('/api/work-orders/<int:job_id>/job-card')
@login_required(admin=True)
def printable_job_card(job_id):
    with DB() as db:
        w=db.get(WorkOrder,job_id)
        if not w: abort(404,'Work order not found.')
        customer=db.get(Customer,w.customer_id)
        steps=db.scalars(select(ProductionStep).where(ProductionStep.work_order_id==job_id).order_by(ProductionStep.sequence)).all()
        drawings=db.scalars(select(DrawingRevision).where(DrawingRevision.work_order_id==job_id,
                                                          DrawingRevision.approved==True)).all()
        url=request.url_root.rstrip('/')+'/?job='+str(job_id)
        qr=qrcode.make(url,image_factory=qrcode.image.svg.SvgPathImage)
        buffer=io.BytesIO();qr.save(buffer)
        qr_uri='data:image/svg+xml;base64,'+base64.b64encode(buffer.getvalue()).decode()
        esc=html.escape
        operations=''.join(f'<tr><td>{s.sequence}</td><td>{esc(s.operation)}</td><td>{esc(s.planned_date or "")}</td><td>{esc(s.status)}</td></tr>' for s in steps)
        approved=', '.join(esc(d.drawing_no+' Rev '+d.revision) for d in drawings) or 'No approved drawing'
        page=f'''<!doctype html><html><head><meta charset="utf-8"><title>{esc(w.code or str(w.id))} job card</title>
        <style>body{{font:16px Arial;max-width:900px;margin:32px auto;color:#172231}}header{{display:flex;justify-content:space-between}}
        img{{width:130px}}table{{border-collapse:collapse;width:100%;margin-top:25px}}td,th{{border:1px solid #aaa;padding:10px;text-align:left}}
        @media print{{body{{margin:10mm}}}}</style></head><body><header><div><h1>COSMOS · JOB CARD</h1><h2>{esc(w.code or str(w.id))}</h2></div><img src="{qr_uri}" alt="QR link to job"></header>
        <p><b>Customer:</b> {esc(customer.name if customer else '')}<br><b>Work:</b> {esc(w.title)}<br><b>Target:</b> {esc(w.target_date or 'Not set')}<br><b>Approved drawing:</b> {approved}</p>
        <table><thead><tr><th>#</th><th>Operation</th><th>Planned date</th><th>Status</th></tr></thead><tbody>{operations}</tbody></table><p>Scan QR and sign in to view the job record.</p></body></html>'''
        response=Response(page,mimetype='text/html')
        response.headers['Content-Security-Policy']="default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"
        return response


@app.get('/api/work-orders/<int:job_id>/quality')
@login_required(admin=True)
def list_quality(job_id):
    with DB() as db:
        if not db.get(WorkOrder,job_id): abort(404,'Job not found.')
        return jsonify([dict(id=x.id,operation=x.operation,inspected=str(x.inspected_qty),accepted=str(x.accepted_qty),
                             rejected=str(x.rejected_qty),result=x.result,defect=x.defect,
                             inspector_id=x.inspector_id,at=aware(x.created_at).isoformat())
                        for x in db.scalars(select(QualityCheck).where(QualityCheck.work_order_id==job_id).order_by(QualityCheck.id.desc()))])


@app.post('/api/work-orders/<int:job_id>/quality')
@login_required(admin=True)
def create_quality(job_id):
    data=request.get_json(silent=True) or {}
    inspected=quantity(data.get('inspected_qty'),True)
    accepted=quantity(data.get('accepted_qty',0))
    rejected=quantity(data.get('rejected_qty',0))
    if accepted+rejected!=inspected: abort(400,'Accepted plus rejected must equal inspected.')
    result='Pass' if rejected==0 else 'Rework'
    with DB.begin() as db:
        if not db.get(WorkOrder,job_id): abort(404,'Job not found.')
        x=QualityCheck(work_order_id=job_id,operation=clean_text(data,'operation',80),
                       inspected_qty=inspected,accepted_qty=accepted,rejected_qty=rejected,result=result,
                       defect=str(data.get('defect') or '')[:5000] or None,
                       inspector_id=request.employee.id,created_at=now())
        db.add(x);db.flush()
        return {'id':x.id,'result':result},201


@app.get('/api/work-orders/<int:job_id>/dispatch')
@login_required(admin=True)
def list_dispatch(job_id):
    with DB() as db:
        if not db.get(WorkOrder,job_id): abort(404,'Job not found.')
        return jsonify([dict(id=x.id,date=x.dispatch_date,quantity=str(x.quantity),transporter=x.transporter,
                             tracking_reference=x.tracking_reference,delivery_note=x.delivery_note,proof_reference=x.proof_reference)
                        for x in db.scalars(select(DispatchRecord).where(DispatchRecord.work_order_id==job_id).order_by(DispatchRecord.id.desc()))])


@app.post('/api/work-orders/<int:job_id>/dispatch')
@login_required(admin=True)
def create_dispatch(job_id):
    data=request.get_json(silent=True) or {}
    amount=quantity(data.get('quantity'),True)
    with DB.begin() as db:
        w=db.scalar(select(WorkOrder).where(WorkOrder.id==job_id).with_for_update())
        if not w: abort(404,'Job not found.')
        accepted=db.scalar(select(func.coalesce(func.sum(QualityCheck.accepted_qty),0)).where(QualityCheck.work_order_id==job_id)) or Decimal(0)
        sent=db.scalar(select(func.coalesce(func.sum(DispatchRecord.quantity),0)).where(DispatchRecord.work_order_id==job_id)) or Decimal(0)
        if amount>accepted-sent: abort(409,'Dispatch exceeds inspected and accepted quantity.')
        date=clean_text(data,'dispatch_date',10)
        try: datetime.strptime(date,'%Y-%m-%d')
        except ValueError: abort(400,'Use a valid dispatch date.')
        x=DispatchRecord(work_order_id=job_id,dispatch_date=date,quantity=amount,
                         transporter=str(data.get('transporter') or '')[:120] or None,
                         tracking_reference=str(data.get('tracking_reference') or '')[:120] or None,
                         delivery_note=str(data.get('delivery_note') or '')[:120] or None,
                         proof_reference=str(data.get('proof_reference') or '')[:500] or None,
                         created_by=request.employee.id,created_at=now())
        db.add(x);db.flush()
        db.add(AuditLog(admin_id=request.employee.id,action='dispatch_job',target=w.code or str(job_id),
                        detail=f'{amount} on {date}',created_at=now()))
        return {'id':x.id,'quantity':str(amount)},201



def deletion_summary(kind, record, groups, notes):
    label=getattr(record,'code',None) or f'PLAN-{record.id}'
    payload=dict(kind=kind,id=record.id,label=label,groups=groups)
    token=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    return dict(name=getattr(record,'name',None) or getattr(record,'title',label),
                confirmation='DELETE '+label,preview_token=token,
                counts={name:len(ids) for name,ids in groups.items()},notes=notes)


def require_deletion_confirmation(preview):
    data=request.get_json(silent=True) or {}
    if data.get('confirmation')!=preview['confirmation']:abort(400,'Type the exact deletion phrase shown in the preview.')
    if data.get('preview_token')!=preview['preview_token']:abort(409,'Linked records changed. Open the deletion preview again.')


def customer_deletion_scope(db,c):
    ids=lambda model,condition:list(db.scalars(select(model.id).where(condition).order_by(model.id)))
    machines=ids(CustomerMachine,CustomerMachine.customer_id==c.id)
    jobs=ids(WorkOrder,or_(WorkOrder.customer_id==c.id,WorkOrder.machine_id.in_(machines)))
    reports=list(db.scalars(select(WorkReportLink.work_report_id).where(or_(WorkReportLink.work_order_id.in_(jobs),WorkReportLink.machine_id.in_(machines))).order_by(WorkReportLink.work_report_id)))
    groups={'Customer profiles':[c.id],'Contacts':ids(CustomerContact,CustomerContact.customer_id==c.id),
            'Sites':ids(CustomerSite,CustomerSite.customer_id==c.id),'Machines':machines,'Work orders':jobs,
            'Plans':ids(DailyPlan,or_(DailyPlan.customer_id==c.id,DailyPlan.work_order_id.in_(jobs),DailyPlan.machine_id.in_(machines))),
            'Linked work reports':reports,'Quotations':ids(Quotation,Quotation.customer_id==c.id),
            'Project documents':ids(PrivateDocument,(PrivateDocument.owner_type=='job') & PrivateDocument.owner_id.in_(jobs)),
            'Customer documents':ids(PrivateDocument,(PrivateDocument.owner_type=='customer') & (PrivateDocument.owner_id==c.id))}
    for label,model in [('Assignments',WorkOrderAssignment),('Production operations',ProductionStep),('Material requirements',MaterialRequirement),('Drawing revisions',DrawingRevision),('Quality inspections',QualityCheck),('Dispatch records',DispatchRecord),('Customer updates',CustomerUpdate)]:
        groups[label]=ids(model,model.work_order_id.in_(jobs))
    return groups


@app.get('/api/customers/<int:customer_id>/delete-preview')
@login_required(admin=True)
def preview_customer_deletion(customer_id):
    with DB() as db:
        c=db.get(Customer,customer_id)
        if not c:abort(404,'Customer not found.')
        return deletion_summary('customer',c,customer_deletion_scope(db,c),
            'Permanently deletes this company and the linked records listed below, including linked service reports. Stock movement history and stock balances are retained; material reservations are released. This cannot be undone in the portal.')


@app.delete('/api/customers/<int:customer_id>')
@login_required(admin=True)
def delete_customer(customer_id):
    with DB.begin() as db:
        c=db.scalar(select(Customer).where(Customer.id==customer_id).with_for_update())
        if not c:abort(404,'Customer not found.')
        groups=customer_deletion_scope(db,c)
        require_deletion_confirmation(deletion_summary('customer',c,groups,''))
        jobs=groups['Work orders']; reports=groups['Linked work reports']
        # Issued stock remains issued; only the removed work-order reference is cleared.
        db.execute(update(StockMovement).where(StockMovement.work_order_id.in_(jobs)).values(work_order_id=None))
        db.execute(update(Quotation).where(Quotation.work_order_id.in_(jobs)).values(work_order_id=None))
        db.execute(delete(WorkReportLink).where(WorkReportLink.work_report_id.in_(reports)))
        for label,model in [('Project documents',PrivateDocument),('Customer documents',PrivateDocument),('Customer updates',CustomerUpdate),('Assignments',WorkOrderAssignment),('Production operations',ProductionStep),('Material requirements',MaterialRequirement),('Drawing revisions',DrawingRevision),('Quality inspections',QualityCheck),('Dispatch records',DispatchRecord),('Plans',DailyPlan),('Linked work reports',WorkReport),('Quotations',Quotation),('Work orders',WorkOrder),('Machines',CustomerMachine),('Contacts',CustomerContact),('Sites',CustomerSite)]:
            db.execute(delete(model).where(model.id.in_(groups[label])))
        db.add(AuditLog(admin_id=request.employee.id,action='delete_customer',target=str(c.id),detail=json.dumps({k:len(v) for k,v in groups.items()}),created_at=now()))
        db.delete(c)
    return dict(ok=True)


def employee_deletion_scope(db,e):
    ids=lambda model,condition:list(db.scalars(select(model.id).where(condition).order_by(model.id)))
    groups={'Employee profiles':[e.id],'Attendance records':ids(Attendance,Attendance.employee_id==e.id),
            'Personal documents':ids(PrivateDocument,(PrivateDocument.owner_type=='employee') & (PrivateDocument.owner_id==e.id))}
    for label,model in [('Employee files',EmployeeProfile),('Points entries',EmployeePoint),('Policy memos',EmployeeMemo),('Plans',DailyPlan),('Work reports',WorkReport),('Reported issues',WorkIssue),('Meeting actions',MeetingAction),('Work assignments',WorkOrderAssignment),('Biometric credentials',WebAuthnCredential)]:
        groups[label]=ids(model,model.employee_id==e.id)
    return groups


@app.get('/api/employees/<int:employee_id>/delete-preview')
@login_required(admin=True)
def preview_employee_deletion(employee_id):
    with DB() as db:
        e=db.get(Employee,employee_id)
        if not e or e.admin:abort(404,'Employee not found or administrator account protected.')
        return deletion_summary('employee',e,employee_deletion_scope(db,e),
            'Permanently deletes this employee, their login, attendance, personal documents, points, memos and records listed below. Company work orders, purchases, maintenance and stock history remain; employee references are cleared. This cannot be undone in the portal.')


def permanently_delete_employee(employee_id):
    photos=[]
    with DB.begin() as db:
        e=db.scalar(select(Employee).where(Employee.id==employee_id).with_for_update())
        if not e or e.admin:abort(404,'Employee not found or administrator account protected.')
        groups=employee_deletion_scope(db,e)
        require_deletion_confirmation(deletion_summary('employee',e,groups,''))
        photos=[p for r in db.scalars(select(Attendance).where(Attendance.employee_id==e.id)) for p in (r.in_photo,r.out_photo) if p]
        if e.photo_id:photos.append(e.photo_id)
        db.execute(delete(WorkReportLink).where(WorkReportLink.work_report_id.in_(groups['Work reports'])))
        for model,field in [(WorkOrder,'job_owner_id'),(WorkOrder,'supervisor_id'),(WorkOrder,'approved_by_id'),(ProductionStep,'assigned_id'),(MaintenanceTask,'assigned_id'),(WorkReport,'verified_by'),(EmployeeProfile,'supervisor_id'),(WorkIssue,'assigned_to'),(DrawingRevision,'approved_by'),(Meeting,'created_by'),(StockMovement,'actor_id'),(PurchaseOrder,'created_by'),(PrivateDocument,'uploaded_by'),(QualityCheck,'inspector_id'),(DispatchRecord,'created_by'),(CustomerUpdate,'created_by'),(AuditLog,'admin_id')]:
            db.execute(update(model).where(getattr(model,field)==e.id).values({field:None}))
        for label,model in [('Personal documents',PrivateDocument),('Employee files',EmployeeProfile),('Points entries',EmployeePoint),('Policy memos',EmployeeMemo),('Plans',DailyPlan),('Work reports',WorkReport),('Reported issues',WorkIssue),('Meeting actions',MeetingAction),('Work assignments',WorkOrderAssignment),('Biometric credentials',WebAuthnCredential),('Attendance records',Attendance)]:
            db.execute(delete(model).where(model.id.in_(groups[label])))
        db.add(AuditLog(admin_id=request.employee.id,action='delete_employee',target=str(e.id),detail=json.dumps({k:len(v) for k,v in groups.items()}),created_at=now()))
        db.delete(e)
    for photo in set(photos):remove_photo(photo)
    return dict(ok=True)


@app.get('/api/daily-plans/<int:plan_id>/delete-preview')
@login_required(admin=True)
def preview_plan_deletion(plan_id):
    with DB() as db:
        row=db.get(DailyPlan,plan_id)
        if not row:abort(404,'Plan not found.')
        return deletion_summary('plan',row,{'Plans':[row.id]},'Permanently removes this planning record and frees its booked hours. The linked customer, machine and work order remain.')


@app.delete('/api/daily-plans/<int:plan_id>')
@login_required(admin=True)
def delete_daily_plan(plan_id):
    with DB.begin() as db:
        row=db.scalar(select(DailyPlan).where(DailyPlan.id==plan_id).with_for_update())
        if not row:abort(404,'Plan not found.')
        require_deletion_confirmation(deletion_summary('plan',row,{'Plans':[row.id]},''))
        db.add(AuditLog(admin_id=request.employee.id,action='delete_daily_plan',target=str(row.id),detail=None,created_at=now()))
        db.delete(row)
    return dict(ok=True)



@app.patch('/api/machines/<int:machine_id>')
@login_required(admin=True)
def edit_customer_machine(machine_id):
    data=request.get_json(silent=True) or {}
    with DB.begin() as db:
        m=db.get(CustomerMachine,machine_id)
        if not m:abort(404,'Machine not found.')
        if 'customer_id' in data and optional_id(data['customer_id'],'customer')!=m.customer_id:abort(400,'A machine with service history must stay with its company.')
        for key,limit in [('customer_machine_no',80),('manufacturer',120),('model',120),('serial_no',120),('controller',120),('department',100),('location',140),('notes',5000),('specifications',5000)]:
            if key in data:setattr(m,key,str(data[key] or '').strip()[:limit] or None)
        for key in ('installation_date','last_service_date','next_service_date'):
            if key in data:setattr(m,key,valid_iso_date(data[key],key.replace('_',' ')) if data[key] else None)
        if 'machine_type' in data:
            name=str(data['machine_type'] or '').strip()[:120]
            mt=db.scalar(select(MachineType).where(MachineType.name==name)) if name else None
            if name and not mt:mt=MachineType(name=name);db.add(mt);db.flush()
            m.machine_type_id=mt.id if mt else None
        if 'status' in data:
            if data['status'] not in ('Active','Under maintenance','Out of service'):abort(400,'Choose a listed machine status.')
            m.status=data['status']
        db.add(AuditLog(admin_id=request.employee.id,action='update_customer_machine',target=m.code,detail=None,created_at=now()))
        return machine_json(m,db.get(Customer,m.customer_id),db.get(MachineType,m.machine_type_id) if m.machine_type_id else None)


def purchase_json(db,p):
    supplier=db.get(Supplier,p.supplier_id);item=db.get(StockItem,p.item_id)
    return dict(id=p.id,supplier=supplier.name,item=item.name,unit=item.unit,supplier_id=p.supplier_id,item_id=p.item_id,
                ordered_qty=str(p.ordered_qty),received_qty=str(p.received_qty),unit_price=str(p.unit_price),
                outstanding_qty=str(max(Decimal(0),p.ordered_qty-p.received_qty)),status=p.status,
                expected_date=p.expected_date,supplier_reference=p.supplier_reference,notes=p.notes,
                order_value=str(p.ordered_qty*p.unit_price))


@app.patch('/api/purchase-orders/<int:order_id>')
@login_required(admin=True)
def edit_purchase_order(order_id):
    data=request.get_json(silent=True) or {}
    with DB.begin() as db:
        p=db.scalar(select(PurchaseOrder).where(PurchaseOrder.id==order_id).with_for_update())
        if not p:abort(404,'Purchase order not found.')
        for key,model in [('supplier_id',Supplier),('item_id',StockItem)]:
            if key in data:
                value=optional_id(data[key],key.replace('_',' '))
                if not value or not db.get(model,value):abort(404,'Supplier or item not found.')
                if p.received_qty>0 and value!=getattr(p,key):abort(409,'Supplier and item are locked after the first receipt.')
                setattr(p,key,value)
        if 'ordered_qty' in data:
            value=quantity(data['ordered_qty'],True)
            if value<p.received_qty:abort(409,'Ordered quantity cannot be below the quantity already received.')
            p.ordered_qty=value
        if 'unit_price' in data:
            value=quantity(data['unit_price'])
            if value<0:abort(400,'Unit price cannot be negative.')
            if p.received_qty>0 and value!=p.unit_price:abort(409,'Unit price is locked after the first receipt.')
            p.unit_price=value
        if 'expected_date' in data:p.expected_date=valid_iso_date(data['expected_date'],'expected delivery') if data['expected_date'] else None
        for key,limit in [('supplier_reference',100),('notes',3000)]:
            if key in data:setattr(p,key,str(data[key] or '').strip()[:limit] or None)
        if 'status' in data:
            if data['status'] not in ('Open','Ordered','Closed','Cancelled'):abort(400,'Use Receive to record delivered quantities.')
            if data['status'] in ('Closed','Cancelled') and not str(data.get('reason') or '').strip():abort(400,'Enter a reason for closing or cancelling the remaining order.')
            p.status=data['status']
        if p.status not in ('Closed','Cancelled'):
            if p.received_qty==p.ordered_qty:p.status='Received'
            elif p.received_qty>0:p.status='Part received'
            elif p.status=='Received':p.status='Open'
        db.add(AuditLog(admin_id=request.employee.id,action='update_purchase_order',target=f'PO-{p.id}',detail=json.dumps(data)[:3000],created_at=now()))
        return purchase_json(db,p)


@app.get('/api/purchase-orders/<int:order_id>/print')
@login_required(admin=True)
def print_purchase_order(order_id):
    with DB() as db:
        p=db.get(PurchaseOrder,order_id)
        if not p:abort(404,'Purchase order not found.')
        row=purchase_json(db,p);supplier=db.get(Supplier,p.supplier_id)
        esc=lambda v:html.escape(str(v or '—'))
        content=f'''<!doctype html><html><head><meta charset="utf-8"><title>PO-{p.id} · Cosmos</title><link rel="stylesheet" href="/static/workspace.css"></head><body class="print-record"><h1>Cosmos Engineering Solutions</h1><h2>Purchase order · PO-{p.id}</h2><p>Supplier: {esc(supplier.name)}<br>GSTIN: {esc(supplier.gstin)}<br>Contact: {esc(supplier.contact)} · {esc(supplier.phone)}</p><p>Supplier reference: {esc(p.supplier_reference)}<br>Expected delivery: {esc(p.expected_date)}<br>Status: {esc(p.status)}</p><table><thead><tr><th>Item</th><th>Quantity</th><th>Unit price (₹)</th><th>Value (₹)</th></tr></thead><tbody><tr><td>{esc(row['item'])}</td><td>{esc(p.ordered_qty)} {esc(row['unit'])}</td><td>{esc(p.unit_price)}</td><td>{esc(row['order_value'])}</td></tr></tbody></table><p>Item value before tax and freight. Confirm the applicable charges with the supplier.</p><p>Notes: {esc(p.notes)}</p><p>Use your browser’s Print option to print or save as PDF.</p></body></html>'''
        return Response(content,mimetype='text/html')


def maintenance_json(db,t):
    asset=db.get(CompanyAsset,t.asset_id);employee=db.get(Employee,t.assigned_id) if t.assigned_id else None
    return dict(id=t.id,asset_id=t.asset_id,asset=asset.name,task_type=t.task_type,description=t.description,
                due_date=t.due_date,completed_date=t.completed_date,status=t.status,downtime_hours=str(t.downtime_hours),
                cost=str(t.cost),notes=t.notes,assigned_id=t.assigned_id,assigned=employee.name if employee else None,
                service_activities=json_list(t.service_activities),checklist=json_list(t.checklist),parts_used=t.parts_used,
                next_service_date=t.next_service_date,priority=t.priority or 'Normal')


def apply_maintenance_fields(db,t,data):
    if 'asset_id' in data:
        aid=optional_id(data['asset_id'],'asset')
        if not aid or not db.get(CompanyAsset,aid):abort(404,'Asset not found.')
        t.asset_id=aid
    if 'description' in data:t.description=clean_text(data,'description',5000)
    if 'task_type' in data or 'service_activities' in data:
        requested_type=data.get('task_type',t.task_type)
        legacy_type=bool(t.id and requested_type==t.task_type and t.task_type not in (*SERVICE_TYPES,'Preventive service'))
        # Existing historical classifications remain editable; new tasks use the shared list.
        fields=service_fields(dict(service_type='Inspection' if legacy_type else requested_type,service_activities=data.get('service_activities',json_list(t.service_activities))))
        if not fields['service_type']:abort(400,'Choose a service type.')
        if not legacy_type:t.task_type=fields['service_type']
        t.service_activities=fields['service_activities']
    for key in ('due_date','next_service_date'):
        if key in data:setattr(t,key,valid_iso_date(data[key],key.replace('_',' ')) if data[key] else None)
    if 'assigned_id' in data:
        eid=optional_id(data['assigned_id'],'employee');e=db.get(Employee,eid) if eid else None
        if eid and (not e or not e.active):abort(400,'Choose an active employee.')
        t.assigned_id=eid
    if 'priority' in data:
        if data['priority'] not in ('Normal','High','Urgent'):abort(400,'Choose a valid priority.')
        t.priority=data['priority']
    if 'checklist' in data:
        rows=data['checklist']
        if not isinstance(rows,list) or len(rows)>30:abort(400,'Use no more than 30 checklist items.')
        for item in rows:
            if not isinstance(item,dict) or not isinstance(item.get('label'),str) or not 1<=len(item['label'].strip())<=160 or type(item.get('done')) is not bool:
                abort(400,'Enter valid checklist labels and completion states.')
        t.checklist=json.dumps([dict(label=r['label'].strip(),done=r['done']) for r in rows])
    if 'status' in data:
        if data['status'] not in WORK_STATUSES:abort(400,'Choose a listed maintenance status.')
        t.status=data['status']
    if t.status in ('Completed','Closed'):
        if any(not r['done'] for r in json_list(t.checklist)):abort(409,'Complete every checklist item before completing or closing this task.')
        t.completed_date=valid_iso_date(data['completed_date'],'completion date') if data.get('completed_date') else t.completed_date or now().astimezone(LOCAL).date().isoformat()
        if t.next_service_date:db.get(CompanyAsset,t.asset_id).next_service_date=t.next_service_date
    else:t.completed_date=None
    for key in ('cost','downtime_hours'):
        if key in data:
            value=quantity(data[key])
            if value<0:abort(400,'Cost and downtime cannot be negative.')
            setattr(t,key,value)
    for key in ('notes','parts_used'):
        if key in data:setattr(t,key,str(data[key] or '').strip()[:5000] or None)


@app.get('/api/company-assets/<int:asset_id>/history')
@login_required(admin=True)
def asset_service_history(asset_id):
    with DB() as db:
        asset=db.get(CompanyAsset,asset_id)
        if not asset:abort(404,'Asset not found.')
        tasks=db.scalars(select(MaintenanceTask).where(MaintenanceTask.asset_id==asset_id).order_by(MaintenanceTask.id.desc())).all()
        return dict(asset=asset_json(asset),tasks=[maintenance_json(db,t) for t in tasks],
                    total_cost=str(sum((t.cost for t in tasks),Decimal(0))),total_downtime=str(sum((t.downtime_hours for t in tasks),Decimal(0))))


def customer_update_json(row):
    return dict(id=row.id,message=row.message,subject=row.subject,recipient_email=row.recipient_email,recipient_phone=row.recipient_phone,
                status=row.status,channel=row.channel,created_at=aware(row.created_at).isoformat(),
                sent_at=aware(row.sent_at).isoformat() if row.sent_at else None)


def customer_update_context(db,w):
    c=db.get(Customer,w.customer_id);m=db.get(CustomerMachine,w.machine_id) if w.machine_id else None
    steps=db.scalars(select(ProductionStep).where(ProductionStep.work_order_id==w.id).order_by(ProductionStep.sequence,ProductionStep.id)).all()
    contacts=db.scalars(select(CustomerContact).where(CustomerContact.customer_id==c.id).order_by(CustomerContact.primary_contact.desc(),CustomerContact.id)).all()
    return dict(job=work_order_json(db,w),customer=dict(name=c.name,email=c.email,phone=c.phone),
                machine=machine_json(m,c,db.get(MachineType,m.machine_type_id) if m.machine_type_id else None) if m else None,
                steps=[step_json(s) for s in steps],contacts=[dict(name=x.name,email=x.email,phone=x.phone) for x in contacts])


@app.route('/api/work-orders/<int:job_id>/customer-updates',methods=['GET','POST'])
@login_required(admin=True)
def project_customer_updates(job_id):
    with DB.begin() as db:
        w=db.get(WorkOrder,job_id)
        if not w:abort(404,'Work order not found.')
        context=customer_update_context(db,w)
        if request.method=='GET':
            context['updates']=[customer_update_json(r) for r in db.scalars(select(CustomerUpdate).where(CustomerUpdate.work_order_id==job_id).order_by(CustomerUpdate.id.desc()).limit(30))]
            return context
        data=request.get_json(silent=True) or {}
        # The reviewed message is stored as a draft. Delivery happens in the user's email or WhatsApp application.
        message=clean_text(data,'message',10000);subject=clean_text(data,'subject',250)
        email=str(data.get('recipient_email') or '').strip();phone=str(data.get('recipient_phone') or '').strip()
        if email and (len(email)>160 or not re.fullmatch(r'[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+',email)):abort(400,'Enter one valid customer email address.')
        if phone and not re.fullmatch(r'\+[1-9]\d{6,14}',phone):abort(400,'Use an international phone number, for example +919876543210.')
        row=CustomerUpdate(work_order_id=job_id,message=message,subject=subject,recipient_email=email or None,recipient_phone=phone or None,
                           status='Draft',created_by=request.employee.id,created_at=now())
        db.add(row);db.flush()
        db.add(AuditLog(admin_id=request.employee.id,action='customer_update_drafted',target=str(row.id),detail=w.code,created_at=now()))
        return customer_update_json(row),201


@app.patch('/api/customer-updates/<int:update_id>')
@login_required(admin=True)
def confirm_customer_update_sent(update_id):
    data=request.get_json(silent=True) or {}
    if data.get('sent_by_user') is not True or data.get('channel') not in ('Email','WhatsApp','Other'):abort(400,'Confirm the channel you used to send this update.')
    with DB.begin() as db:
        row=db.get(CustomerUpdate,update_id)
        if not row:abort(404,'Update not found.')
        if row.status!='Sent (manual)':
            row.status='Sent (manual)';row.channel=data['channel'];row.sent_at=now()
            db.add(AuditLog(admin_id=request.employee.id,action='customer_update_marked_sent',target=str(row.id),detail=row.channel,created_at=now()))
        return customer_update_json(row)
