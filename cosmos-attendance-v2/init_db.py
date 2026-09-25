import os
import re
from sqlalchemy import select
from werkzeug.security import generate_password_hash
from app import Base, engine, DB, Employee

def initialise():
    Base.metadata.create_all(engine)
    admin_code = os.getenv('ADMIN_USERNAME', 'admin').lower().strip()
    admin_pin = os.getenv('ADMIN_PIN', '').strip()
    if not re.fullmatch(r'\d{4}', admin_pin):
        raise RuntimeError('Set ADMIN_PIN to exactly 4 digits.')
    with DB.begin() as db:
        admin = db.scalar(select(Employee).where(Employee.admin == True))
        if admin:
            admin.code = admin_code
            admin.password = generate_password_hash(admin_pin)
            admin.active = True
            return
        db.add(Employee(code=admin_code, name='Cosmos Admin',
                        department='Administration', admin=True,
                        password=generate_password_hash(admin_pin)))

if __name__ == '__main__':
    initialise()
