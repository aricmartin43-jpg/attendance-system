import os
import re
from sqlalchemy import select, inspect, text
from werkzeug.security import generate_password_hash
from app import Base, engine, DB, Employee

def initialise():
    Base.metadata.create_all(engine)
    # Existing installations already have stock_items; create_all does not add columns.
    existing = {column['name'] for column in inspect(engine).get_columns('stock_items')}
    with engine.begin() as connection:
        for column in ('sub_category', 'size_dimension', 'material_finish'):
            if column not in existing:
                connection.execute(text(f'ALTER TABLE stock_items ADD COLUMN {column} VARCHAR(80)'))
        if 'service_product' not in {column['name'] for column in inspect(engine).get_columns('work_orders')}:
            connection.execute(text('ALTER TABLE work_orders ADD COLUMN service_product VARCHAR(180)'))
    admin_code = os.getenv('ADMIN_USERNAME', 'admin').lower().strip()
    admin_pin = os.getenv('ADMIN_PIN', '').strip()
    if not re.fullmatch(r'\d{4}', admin_pin):
        raise RuntimeError('ADMIN_PIN must be set to exactly 4 digits.')
    with DB.begin() as db:
        admin = db.scalar(select(Employee).where(Employee.admin == True))
        if admin:
            admin.code = admin_code
            admin.password = generate_password_hash(admin_pin)
            admin.active = True
        else:
            db.add(Employee(code=admin_code, name='Cosmos Admin',
                            department='Administration', admin=True, active=True,
                            password=generate_password_hash(admin_pin)))

if __name__ == '__main__':
    initialise()
