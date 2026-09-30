from decimal import Decimal

from sqlalchemy import select

from test_app import database, module  # isolated SQLite fixture
from inventory_spares_20260930 import CATALOGUE, IMPORT_KEY, RECEIPT, register


def test_service_spares_register_once_with_auditable_balances():
    with module.DB.begin() as db:
        result = register(db)
    assert result == {'created': 35, 'balances_set': 35, 'already_at_100': 0, 'items': 35}
    with module.DB() as db:
        items = db.scalars(select(module.StockItem).order_by(module.StockItem.sku)).all()
        movements = db.scalars(select(module.StockMovement)).all()
        assert [item.name for item in items] == [entry[0] for entry in CATALOGUE]
        assert all(item.quantity == Decimal('100') and item.unit == 'pcs' for item in items)
        assert all(item.category == 'Spares' and item.sub_category and item.size_dimension and item.material_finish for item in items)
        assert len(movements) == 35
        assert all(m.kind == 'Receive' and m.quantity_change == Decimal('100') and m.reference == RECEIPT for m in movements)
        assert db.scalar(select(module.AuditLog.id).where(module.AuditLog.target == IMPORT_KEY))
    with module.DB.begin() as db:
        assert register(db) == {'already_imported': True}
    with module.DB() as db:
        assert len(db.scalars(select(module.StockMovement)).all()) == 35


def test_existing_item_is_adjusted_without_duplicate_and_deleted_item_stays_deleted():
    with module.DB.begin() as db:
        db.add(module.StockItem(sku='OLD-WIPER', name='EN2 3 MM Wiper', category='Spares',
                                quantity=Decimal('125'), unit='pcs', active=True))
    with module.DB.begin() as db:
        result = register(db)
    assert result == {'created': 34, 'balances_set': 35, 'already_at_100': 0, 'items': 35}
    with module.DB.begin() as db:
        old = db.scalar(select(module.StockItem).where(module.StockItem.sku == 'OLD-WIPER'))
        assert old.quantity == Decimal('100')
        adjustment = db.scalar(select(module.StockMovement).where(module.StockMovement.item_id == old.id))
        assert adjustment.kind == 'Adjust' and adjustment.quantity_change == Decimal('-25')
        imported = db.scalar(select(module.StockItem).where(module.StockItem.sku == 'CES-SP-035'))
        movement = db.scalar(select(module.StockMovement).where(module.StockMovement.item_id == imported.id))
        db.delete(movement)
        db.flush()
        db.delete(imported)
    with module.DB.begin() as db:
        assert register(db) == {'already_imported': True}
    with module.DB() as db:
        assert db.scalar(select(module.StockItem).where(module.StockItem.sku == 'CES-SP-035')) is None
