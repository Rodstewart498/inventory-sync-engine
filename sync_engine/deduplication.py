"""
Order deduplication for cross-platform sync.

Tracks which eBay orders have already been mirrored to Shopify
to prevent duplicate order creation. Uses atomic JSON writes
for crash-safe persistence.

The synced orders file stores a set of eBay order IDs that have
been successfully created in Shopify. Before creating a new
Shopify order, check this set first.
"""

import json
import os
import tempfile
import logging
from typing import Set

logger = logging.getLogger(__name__)


class OrderDeduplicator:
    """
    Tracks synced eBay → Shopify order IDs to prevent duplicates.

    Persists the set of synced order IDs to a JSON file using
    atomic writes. Thread-safe for single-process usage via GIL.
    """

    def __init__(self, filepath: str):
        """
        Args:
            filepath: Path to the JSON file storing synced order IDs.
        """
        self.filepath = filepath
        self._synced_ids: Set[str] = set()
        self._load()

    def _load(self) -> None:
        """Load synced order IDs from disk."""
        if not os.path.exists(self.filepath):
            self._synced_ids = set()
            return

        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self._synced_ids = set(data.get('synced_order_ids', []))
            logger.info(f"Loaded {len(self._synced_ids)} synced order IDs")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load synced orders: {e}")
            self._synced_ids = set()

    def _save(self) -> None:
        """Persist synced order IDs to disk atomically."""
        dir_path = os.path.dirname(self.filepath) or '.'
        os.makedirs(dir_path, exist_ok=True)

        fd, temp_path = tempfile.mkstemp(
            suffix='.tmp', dir=dir_path
        )

        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(
                    {'synced_order_ids': sorted(self._synced_ids)},
                    f, indent=2
                )
                f.flush()
                os.fsync(f.fileno())

            os.replace(temp_path, self.filepath)
        except Exception:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass
            raise

    def is_synced(self, order_id: str) -> bool:
        """
        Check if an eBay order has already been synced to Shopify.

        Args:
            order_id: eBay order ID string.

        Returns:
            True if already synced.
        """
        return str(order_id) in self._synced_ids

    def mark_synced(self, order_id: str) -> None:
        """
        Record that an eBay order has been synced to Shopify.

        Persists immediately to disk.

        Args:
            order_id: eBay order ID string.
        """
        self._synced_ids.add(str(order_id))
        self._save()
        logger.debug(f"Marked order {order_id} as synced")

    def mark_batch_synced(self, order_ids: list) -> None:
        """
        Record multiple orders as synced in a single write.

        More efficient than calling mark_synced() in a loop.

        Args:
            order_ids: List of eBay order ID strings.
        """
        for oid in order_ids:
            self._synced_ids.add(str(oid))
        self._save()
        logger.info(f"Marked {len(order_ids)} orders as synced")

    @property
    def synced_count(self) -> int:
        """Number of orders that have been synced."""
        return len(self._synced_ids)

    def reset(self) -> None:
        """Clear all synced order records. Use with caution."""
        self._synced_ids.clear()
        self._save()
        logger.warning("Reset all synced order IDs")
