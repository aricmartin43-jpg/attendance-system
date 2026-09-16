"""
database.py
Persistence layer using SQLAlchemy so the same code works against:
 - local SQLite (default, for development)
 - a cloud Postgres database (set DATABASE_URL env var, e.g. from Render/Supabase/Neon)

Tables:
 - employees: id, name, employee_code, face_encoding (pickled numpy array), created_at
 - attendance: id, employee_id, date, check_in_time, check_out_time, check_in_lat/lng, check_out_lat/lng
"""

import os
import pickle
from datetime import datetime, date

import numpy as np
from sqlalchemy import (
    create_engine, Column, Integer, String, LargeBinary, Date, Time,
    Float, ForeignKey, UniqueConstraint, DateTime
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///instance/attendance.db")

# Render/Heroku-style Postgres URLs sometimes start with postgres:// — SQLAlchemy needs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class Employee(Base):
    __tablename__ = "employees"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    employee_code = Column(String, unique=True, nullable=False)
    face_encoding = Column(LargeBinary, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    attendance_records = relationship("Attendance", back_populates="employee")


class Attendance(Base):
    __tablename__ = "attendance"
    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    date = Column(Date, nullable=False)
    check_in_time = Column(Time, nullable=True)
    check_out_time = Column(Time, nullable=True)
    check_in_lat = Column(Float, nullable=True)
    check_in_lng = Column(Float, nullable=True)
    check_out_lat = Column(Float, nullable=True)
    check_out_lng = Column(Float, nullable=True)

    employee = relationship("Employee", back_populates="attendance_records")

    __table_args__ = (UniqueConstraint("employee_id", "date", name="uq_employee_date"),)


def init_db():
    os.makedirs("instance", exist_ok=True)
    Base.metadata.create_all(engine)


# ---------- Employees ----------

def add_employee(name: str, employee_code: str, encoding: np.ndarray):
    blob = pickle.dumps(encoding)
    with SessionLocal() as session:
        session.add(Employee(name=name, employee_code=employee_code, face_encoding=blob))
        session.commit()


def get_all_employees():
    with SessionLocal() as session:
        rows = session.query(Employee).order_by(Employee.name).all()
        return [
            {"id": e.id, "name": e.name, "employee_code": e.employee_code,
             "created_at": e.created_at.isoformat() if e.created_at else None}
            for e in rows
        ]


def get_all_encodings():
    """Returns list of (employee_id, name, employee_code, encoding) for face matching."""
    with SessionLocal() as session:
        rows = session.query(Employee).all()
        return [(e.id, e.name, e.employee_code, pickle.loads(e.face_encoding)) for e in rows]


def employee_code_exists(employee_code: str) -> bool:
    with SessionLocal() as session:
        return session.query(Employee).filter_by(employee_code=employee_code).first() is not None


def delete_employee(employee_id: int):
    with SessionLocal() as session:
        session.query(Attendance).filter_by(employee_id=employee_id).delete()
        session.query(Employee).filter_by(id=employee_id).delete()
        session.commit()


# ---------- Attendance ----------

def mark_check_in(employee_id: int, lat: float = None, lng: float = None):
    today = date.today()
    now = datetime.now().time()
    with SessionLocal() as session:
        existing = session.query(Attendance).filter_by(employee_id=employee_id, date=today).first()
        if existing:
            return "already_checked_in"
        session.add(Attendance(
            employee_id=employee_id, date=today, check_in_time=now,
            check_in_lat=lat, check_in_lng=lng,
        ))
        session.commit()
        return "checked_in"


def mark_check_out(employee_id: int, lat: float = None, lng: float = None):
    today = date.today()
    now = datetime.now().time()
    with SessionLocal() as session:
        existing = session.query(Attendance).filter_by(employee_id=employee_id, date=today).first()
        if not existing:
            return "not_checked_in"
        if existing.check_out_time:
            return "already_checked_out"
        existing.check_out_time = now
        existing.check_out_lat = lat
        existing.check_out_lng = lng
        session.commit()
        return "checked_out"


def get_attendance(date_filter: str = None):
    with SessionLocal() as session:
        query = session.query(Attendance).join(Employee)
        if date_filter:
            query = query.filter(Attendance.date == date_filter)
        rows = query.order_by(Attendance.date.desc(), Attendance.check_in_time.desc()).all()
        return [
            {
                "id": a.id, "name": a.employee.name, "employee_code": a.employee.employee_code,
                "date": a.date.isoformat(),
                "check_in_time": a.check_in_time.strftime("%H:%M:%S") if a.check_in_time else None,
                "check_out_time": a.check_out_time.strftime("%H:%M:%S") if a.check_out_time else None,
            }
            for a in rows
        ]
