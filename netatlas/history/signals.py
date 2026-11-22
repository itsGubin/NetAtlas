"""Sent inside the History services' transaction, so a receiver's writes share its fate."""

from django.dispatch import Signal

# Arguments: `actor` (None for NetAtlas itself) and `item`.
item_created = Signal()
# Arguments: `actor` and `items`, every tracked item the delete removes. They still exist.
items_deleting = Signal()
