"""One-time registration of the service spares supplied on 30 September 2026.

Keep the original item wording in specification: profile codes and incomplete
dimensions must not be mistaken for manufacturer-confirmed specifications.
"""

import json
import re
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app import AuditLog, StockItem, StockMovement


IMPORT_KEY = 'service_spares_20260930'
RECEIPT = 'CATALOG-20260930'
STARTING_QUANTITY = Decimal('100')

# (original wording, stock group, size / designation, stated material / finish)
CATALOGUE = (
    ('EN2 3 MM Wiper', 'Way wipers', 'EN2 / 3 mm', 'Unspecified'),
    ('EN1 2 MM Wiper', 'Way wipers', 'EN1 / 2 mm', 'Unspecified'),
    ('EN5 5 MM Wiper', 'Way wipers', 'EN5 / 5 mm', 'Unspecified'),
    ('C2 Lip Wiper', 'Way wipers', 'C2 lip', 'Unspecified'),
    ('C2 Casting', 'Guide castings', 'C2', 'Unspecified'),
    ('C5 Casting', 'Guide castings', 'C5', 'Unspecified'),
    ('HMT Wiper', 'Way wipers', 'HMT', 'Unspecified'),
    ('v Wiper', 'Way wipers', 'V profile', 'Unspecified'),
    ('17 MM Guide Wiper', 'Way wipers', '17 mm', 'Unspecified'),
    ('24 MM Guide Wiper', 'Way wipers', '24 mm', 'Unspecified'),
    ('17MM AB-1 SS Casting', 'Guide castings', 'AB-1 / 17 mm', 'Stainless steel (SS)'),
    ('24 MM AB-3 SS Casting', 'Guide castings', 'AB-3 / 24 mm', 'Stainless steel (SS)'),
    ('Steel V Wiper', 'Way wipers', 'V profile', 'Steel'),
    ('Blue Colour Guide Lip', 'Guide lips', 'Size unspecified', 'Blue; material unspecified'),
    ('Blue Alum. Guide Casting', 'Guide castings', 'Size unspecified', 'Blue aluminium'),
    ('Steel Brush', 'Brushes', 'Size unspecified', 'Steel'),
    ('Roller L-Shoe', 'Guide shoes', 'Roller L-shoe', 'Unspecified'),
    ('Red L-Shoe', 'Guide shoes', 'Size unspecified', 'Red; material unspecified'),
    ('D Damper - Small size', 'Dampers', 'D / small', 'Unspecified'),
    ('D Damper - Medium Size', 'Dampers', 'D / medium', 'Unspecified'),
    ('D Damper - Big Size', 'Dampers', 'D / big', 'Unspecified'),
    ('Bumper', 'Dampers', 'Size unspecified', 'Unspecified'),
    ('R-Damper', 'Dampers', 'R profile', 'Unspecified'),
    ('Ball Bearing 6 Pin', 'Bearings and pins', '6 pin (as supplied)', 'Unspecified'),
    ('608 Bearing Pin', 'Bearings and pins', '608 designation', 'Unspecified'),
    ('Stopper Bush', 'Bushes', 'Size unspecified', 'Unspecified'),
    ('Roller 22 MM', 'Rollers', '22 mm (dimension unconfirmed)', 'Unspecified'),
    ('Roller 25 MM', 'Rollers', '25 mm (dimension unconfirmed)', 'Unspecified'),
    ('Roller 28 MM', 'Rollers', '28 mm (dimension unconfirmed)', 'Unspecified'),
    ('Roller 40 MM', 'Rollers', '40 mm (dimension unconfirmed)', 'Unspecified'),
    ('Link Plate - 1.5" Pitch', 'Conveyor plates', '1.5 in pitch', 'Unspecified'),
    ('Side Plate - 1.5" Pitch', 'Conveyor plates', '1.5 in pitch', 'Unspecified'),
    ('Line Plate - 2" Pitch', 'Conveyor plates', '2 in pitch', 'Unspecified'),
    ('Side Plate - 2" Pitch', 'Conveyor plates', '2 in pitch', 'Unspecified'),
    ('Pin Rod - Running Length (TBC)', 'Pins and rods', 'Running length TBC', 'Unspecified'),
)


def _normalize(value):
    return re.sub(r'[^a-z0-9]+', '', value.casefold())


def register(db):
    """Register the batch exactly once, preserving prior stock history.

    A matching existing name is brought to 100 through an adjustment. Ambiguous
    matches and occupied SKUs abort the transaction, so stock is never silently
    assigned to a different product.
    """
    if db.scalar(select(AuditLog.id).where(AuditLog.action == 'catalogue_import',
                                            AuditLog.target == IMPORT_KEY)):
        return {'already_imported': True}

    existing = db.scalars(select(StockItem)).all()
    by_sku = {item.sku: item for item in existing}
    by_label = {}
    for item in existing:
        for label in (item.name, item.specification or ''):
            # Match exact labels, never a substring of a longer specification.
            key = _normalize(label)
            if key:
                by_label.setdefault(key, set()).add(item.id)

    matches = []
    claimed = set()
    for number, (label, group, size, material) in enumerate(CATALOGUE, 1):
        sku = f'CES-SP-{number:03d}'
        label_matches = by_label.get(_normalize(label), set())
        if len(label_matches) > 1:
            raise RuntimeError(f'Ambiguous existing inventory match for {label}')
        match = next((item for item in existing if item.id in label_matches), None)
        occupied = by_sku.get(sku)
        if occupied and occupied is not match and _normalize(occupied.name) != _normalize(label) and _normalize(occupied.specification or '') != _normalize(label):
            raise RuntimeError(f'Import SKU {sku} belongs to a different stock item')
        match = match or occupied
        if match and match.id in claimed:
            raise RuntimeError(f'Existing stock item matches more than one catalogue entry: {label}')
        if match:
            claimed.add(match.id)
        matches.append((sku, label, group, size, material, match))

    created = adjusted = unchanged = 0
    stamp = datetime.now(timezone.utc)
    for sku, label, group, size, material, item in matches:
        if item is None:
            item = StockItem(sku=sku, name=label, category='Spares',
                             sub_category=group, size_dimension=size,
                             material_finish=material,
                             specification=label, unit='pcs', reorder_level=Decimal('0'),
                             quantity=Decimal('0'), active=True)
            db.add(item)
            db.flush()
            created += 1
        delta = STARTING_QUANTITY - item.quantity
        if delta:
            item.quantity = STARTING_QUANTITY
            db.add(StockMovement(item_id=item.id,
                                 kind='Receive' if delta > 0 and item.id not in claimed else 'Adjust',
                                 quantity_change=delta, balance_after=STARTING_QUANTITY,
                                 reference=RECEIPT,
                                 reason='Service spare inventory count supplied by administrator: 100 pcs each.',
                                 actor_id=None, created_at=stamp))
            adjusted += 1
        else:
            unchanged += 1
    db.add(AuditLog(admin_id=None, action='catalogue_import', target=IMPORT_KEY,
                    detail=json.dumps({'created': created, 'balances_set': adjusted,
                                       'already_at_100': unchanged, 'items': len(CATALOGUE)}),
                    created_at=stamp))
    return {'created': created, 'balances_set': adjusted,
            'already_at_100': unchanged, 'items': len(CATALOGUE)}
