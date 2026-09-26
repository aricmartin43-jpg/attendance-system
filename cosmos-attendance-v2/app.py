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
from sqlalchemy import Boolean, DateTime, Float, Integer, Numeric, LargeBinary, String, Text, ForeignKey, create_engine, select, delete, UniqueConstraint, func
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


class StockItem(Base):
    __tablename__ = 'stock_items'
    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(180))
    category: Mapped[str] = mapped_column(String(50), default='Raw material')
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
    actor_id: Mapped[int] = mapped_column(ForeignKey('employees.id'))
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
    created_by: Mapped[int] = mapped_column(ForeignKey('employees.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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
    uploaded_by: Mapped[int] = mapped_column(ForeignKey('employees.id'))
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
    inspector_id: Mapped[int] = mapped_column(ForeignKey('employees.id'))
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
    created_by: Mapped[int] = mapped_column(ForeignKey('employees.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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
def enrol(employee_id):
    abort(410, 'Face recognition is disabled. Employees use PIN or biometric login with GPS attendance.')


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
                specification=item.specification,unit=item.unit,location=item.location,
                reorder_level=str(item.reorder_level),quantity=str(item.quantity),active=item.active)


def reserved_total(db,item_id):
    return db.scalar(select(func.coalesce(func.sum(MaterialRequirement.reserved_qty),0))
                     .where(MaterialRequirement.item_id==item_id)) or Decimal(0)


@app.get('/api/stock-items')
@login_required(admin=True)
def list_stock_items():
    with DB() as db:
        out=[]
        for x in db.scalars(select(StockItem).order_by(StockItem.name)):
            row=stock_json(x);reserved=reserved_total(db,x.id)
            row.update(reserved=str(reserved),available=str(x.quantity-reserved));out.append(row)
        return jsonify(out)


@app.post('/api/stock-items')
@login_required(admin=True)
def create_stock_item():
    data=request.get_json(silent=True) or {}
    sku=clean_text(data,'sku',60).upper()
    with DB.begin() as db:
        if db.scalar(select(StockItem.id).where(StockItem.sku==sku)): abort(409,'SKU already exists.')
        item=StockItem(sku=sku,name=clean_text(data,'name',180),category=str(data.get('category') or 'Raw material')[:50],
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
        for key,limit in {'name':180,'category':50,'specification':5000,'unit':20,'location':100}.items():
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
        return jsonify([dict(id=p.id,supplier=db.get(Supplier,p.supplier_id).name,item=db.get(StockItem,p.item_id).name,
                             supplier_id=p.supplier_id,item_id=p.item_id,ordered_qty=str(p.ordered_qty),
                             received_qty=str(p.received_qty),unit_price=str(p.unit_price),status=p.status,
                             expected_date=p.expected_date)
                        for p in db.scalars(select(PurchaseOrder).order_by(PurchaseOrder.id.desc()).limit(300))])


@app.post('/api/purchase-orders')
@login_required(admin=True)
def create_purchase_order():
    data=request.get_json(silent=True) or {}
    try: supplier_id=int(data.get('supplier_id'));item_id=int(data.get('item_id'))
    except (TypeError,ValueError): abort(400,'Select a supplier and item.')
    with DB.begin() as db:
        if not db.get(Supplier,supplier_id) or not db.get(StockItem,item_id): abort(404,'Supplier or item not found.')
        p=PurchaseOrder(supplier_id=supplier_id,item_id=item_id,ordered_qty=quantity(data.get('ordered_qty'),True),
                        received_qty=Decimal(0),unit_price=quantity(data.get('unit_price',0)),
                        expected_date=str(data.get('expected_date') or '')[:10] or None,
                        status='Open',created_by=request.employee.id,created_at=now())
        db.add(p);db.flush()
        return dict(id=p.id,status=p.status),201


@app.post('/api/purchase-orders/<int:order_id>/receive')
@login_required(admin=True)
def receive_purchase_order(order_id):
    data=request.get_json(silent=True) or {}
    amount=quantity(data.get('quantity'),True)
    with DB.begin() as db:
        p=db.scalar(select(PurchaseOrder).where(PurchaseOrder.id==order_id).with_for_update())
        if not p: abort(404,'Purchase order not found.')
        if p.status=='Cancelled' or p.received_qty+amount>p.ordered_qty: abort(409,'Receipt exceeds outstanding quantity.')
        item=db.scalar(select(StockItem).where(StockItem.id==p.item_id).with_for_update())
        p.received_qty+=amount
        p.status='Received' if p.received_qty==p.ordered_qty else 'Part received'
        item.quantity+=amount
        db.add(StockMovement(item_id=item.id,kind='Receive',quantity_change=amount,balance_after=item.quantity,
                             reference=f'PO-{p.id}',reason='Purchase order receipt',actor_id=request.employee.id,created_at=now()))
        db.add(AuditLog(admin_id=request.employee.id,action='receive_purchase_order',target=f'PO-{p.id}',
                        detail=f'{amount} {item.unit}',created_at=now()))
        return dict(id=p.id,status=p.status,received_qty=str(p.received_qty),balance=str(item.quantity))


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
                         planned_date=str(data.get('planned_date') or '')[:10] or None,
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
            if data['status'] not in ('Planned','In Progress','Blocked','Completed'): abort(400,'Invalid status.')
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
    if data.get('kind') not in ('Machine','Bike'): abort(400,'Choose Machine or Bike.')
    with DB.begin() as db:
        a=CompanyAsset(code=clean_text(data,'code',60).upper(),kind=data['kind'],name=clean_text(data,'name',160),
                       model=str(data.get('model') or '')[:120] or None,
                       serial_or_registration=str(data.get('serial_or_registration') or '')[:100] or None,
                       location=str(data.get('location') or '')[:100] or None,
                       next_service_date=str(data.get('next_service_date') or '')[:10] or None,active=True)
        db.add(a);db.flush();return asset_json(a),201


@app.get('/api/maintenance-tasks')
@login_required(admin=True)
def list_maintenance_tasks():
    with DB() as db:
        return jsonify([dict(id=t.id,asset_id=t.asset_id,asset=db.get(CompanyAsset,t.asset_id).name,
                             task_type=t.task_type,description=t.description,due_date=t.due_date,
                             completed_date=t.completed_date,status=t.status,downtime_hours=str(t.downtime_hours),
                             cost=str(t.cost),notes=t.notes)
                        for t in db.scalars(select(MaintenanceTask).order_by(MaintenanceTask.id.desc()).limit(300))])


@app.post('/api/maintenance-tasks')
@login_required(admin=True)
def create_maintenance_task():
    data=request.get_json(silent=True) or {}
    try: asset_id=int(data.get('asset_id'))
    except (TypeError,ValueError): abort(400,'Select an asset.')
    with DB.begin() as db:
        if not db.get(CompanyAsset,asset_id): abort(404,'Asset not found.')
        t=MaintenanceTask(asset_id=asset_id,task_type=clean_text(data,'task_type',30),
                          description=clean_text(data,'description',5000),due_date=str(data.get('due_date') or '')[:10] or None,
                          status='Open',downtime_hours=Decimal(0),cost=Decimal(0),created_at=now())
        db.add(t);db.flush();return {'id':t.id,'status':t.status},201


@app.patch('/api/maintenance-tasks/<int:task_id>')
@login_required(admin=True)
def update_maintenance_task(task_id):
    data=request.get_json(silent=True) or {}
    with DB.begin() as db:
        t=db.get(MaintenanceTask,task_id)
        if not t: abort(404,'Task not found.')
        if 'status' in data:
            if data['status'] not in ('Open','In Progress','Completed'): abort(400,'Invalid status.')
            t.status=data['status']
            if t.status=='Completed': t.completed_date=now().astimezone(LOCAL).date().isoformat()
        for key in ('cost','downtime_hours'):
            if key in data: setattr(t,key,quantity(data[key]))
        if 'notes' in data: t.notes=str(data['notes'] or '')[:5000] or None
        db.add(AuditLog(admin_id=request.employee.id,action='update_maintenance_task',target=str(t.id),
                        detail=json.dumps(data)[:1000],created_at=now()))
        return {'id':t.id,'status':t.status}


@app.get('/api/management-summary')
@login_required(admin=True)
def management_summary():
    with DB() as db:
        jobs=db.scalars(select(WorkOrder)).all()
        steps=db.scalars(select(ProductionStep)).all()
        items=db.scalars(select(StockItem).where(StockItem.active==True)).all()
        tasks=db.scalars(select(MaintenanceTask)).all()
        today=now().astimezone(LOCAL).date().isoformat()
        return dict(open_jobs=sum(j.status not in ('Completed','Closed','Cancelled') for j in jobs),
                    overdue_jobs=sum(j.target_date is not None and j.target_date<today and j.status not in ('Completed','Closed','Cancelled') for j in jobs),
                    blocked_steps=sum(s.status=='Blocked' for s in steps),
                    accepted_quantity=str(sum((s.accepted_qty for s in steps),Decimal(0))),
                    rejected_quantity=str(sum((s.rejected_qty for s in steps),Decimal(0))),
                    low_stock=sum(i.quantity<=i.reorder_level for i in items),
                    open_maintenance=sum(t.status!='Completed' for t in tasks))


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
