import os
import re
from sqlalchemy import select
from werkzeug.security import generate_password_hash
from app import Base, engine, DB, Employee

def initialise():
    Base.metadata.create_all(engine)
    admin_code = os.getenv('ADMIN_USERNAME', 'admin').lower().strip()
    admin_pin = os.getenv('ADMIN_PIN', '').strip()
    fallback = os.getenv('ADMIN_PASSWORD', '').strip()
    if re.fullmatch(r'\d{4}', admin_pin):
        admin_secret = admin_pin
    elif len(fallback) >= 12:
        admin_secret = fallback
    else:
        raise RuntimeError('Set ADMIN_PIN to 4 digits or ADMIN_PASSWORD to at least 12 characters.')
    with DB.begin() as db:
        admin = db.scalar(select(Employee).where(Employee.admin == True))
        if admin:
            admin.code = admin_code
            admin.password = generate_password_hash(admin_secret)
            admin.active = True
            return
        db.add(Employee(code=admin_code, name='Cosmos Admin',
                        department='Administration', admin=True,
                        password=generate_password_hash(admin_secret)))

if __name__ == '__main__':
    initialise()
