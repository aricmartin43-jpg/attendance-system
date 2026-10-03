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
        additions = {
            'daily_plans': {'customer_id':'INTEGER REFERENCES customers(id)', 'machine_id':'INTEGER REFERENCES customer_machines(id)', 'due_date':'VARCHAR(10)', 'service_type':'VARCHAR(30)', 'service_activities':'TEXT'},
            'work_orders': {'service_type':'VARCHAR(30)', 'service_activities':'TEXT', 'current_stage':'VARCHAR(60)', 'customer_remarks':'TEXT'},
            'work_reports': {'service_type':'VARCHAR(30)', 'service_activities':'TEXT'},
            'customer_machines': {'specifications':'TEXT', 'last_service_date':'VARCHAR(10)', 'next_service_date':'VARCHAR(10)'},
            'purchase_orders': {'supplier_reference':'VARCHAR(100)', 'notes':'TEXT'},
            'maintenance_tasks': {'service_activities':'TEXT', 'checklist':'TEXT', 'parts_used':'TEXT', 'next_service_date':'VARCHAR(10)', 'priority':'VARCHAR(20)'},
        }
        for table, columns in additions.items():
            present = {c['name'] for c in inspect(connection).get_columns(table)}
            for column, sql_type in columns.items():
                if column not in present:
                    connection.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {sql_type}'))
        # Retain company transactions after a personal employee profile is deleted.
        if engine.dialect.name == 'postgresql':
            for table, column in [('meetings','created_by'),('stock_movements','actor_id'),('purchase_orders','created_by'),('private_documents','uploaded_by'),('quality_checks','inspector_id'),('dispatch_records','created_by')]:
                if not next(c for c in inspect(connection).get_columns(table) if c['name']==column)['nullable']:
                    connection.execute(text(f'ALTER TABLE {table} ALTER COLUMN {column} DROP NOT NULL'))
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
    if os.getenv('APP_ENV', 'production') == 'production':
        from inventory_spares_20260930 import register
        with DB.begin() as db:
            result = register(db)
        print(f'Service spare catalogue: {result}', flush=True)

        from customer_import_20261003 import register as register_customers
        with DB.begin() as db:
            result = register_customers(db)
        print(f'Tally customer import: {result}', flush=True)

if __name__ == '__main__':
    initialise()
