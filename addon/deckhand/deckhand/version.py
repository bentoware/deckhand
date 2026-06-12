"""Single source of truth for the Deckhand add-on version.

Keep ``manifest.json``'s ``human_version`` and the ``deckhand-server`` crate
version in sync; unit tests enforce both. The companion reports its crate
version from ``/status`` and the add-on treats a mismatch as a stale helper.
"""

ADDON_VERSION = "0.1.11"
