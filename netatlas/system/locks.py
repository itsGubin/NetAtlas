"""One lock for writes whose rules span several rows, such as Device trees and the address plan."""

from django.db import connection

# Any fixed number. It identifies this lock among Postgres advisory locks.
_INVENTORY_LOCK = 7_400_801


def lock_inventory() -> None:
    """Hold until the transaction ends, so concurrent writes cannot break a cross-row rule."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_INVENTORY_LOCK])
