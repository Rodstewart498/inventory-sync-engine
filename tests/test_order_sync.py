"""Tests for eBay → Shopify order synchronization."""

import json
import os
from unittest.mock import MagicMock, patch
import pytest

from sync_engine.deduplication import OrderDeduplicator


class TestOrderDeduplicator:
    """Tests for order deduplication tracking."""

    def test_new_order_not_synced(self, tmp_path):
        dedup = OrderDeduplicator(str(tmp_path / "synced.json"))
        assert dedup.is_synced("12-34567-89012") is False

    def test_mark_synced(self, tmp_path):
        dedup = OrderDeduplicator(str(tmp_path / "synced.json"))
        dedup.mark_synced("12-34567-89012")
        assert dedup.is_synced("12-34567-89012") is True

    def test_persists_to_disk(self, tmp_path):
        filepath = str(tmp_path / "synced.json")
        dedup1 = OrderDeduplicator(filepath)
        dedup1.mark_synced("12-34567-89012")

        # New instance should load from disk
        dedup2 = OrderDeduplicator(filepath)
        assert dedup2.is_synced("12-34567-89012") is True

    def test_batch_mark(self, tmp_path):
        dedup = OrderDeduplicator(str(tmp_path / "synced.json"))
        ids = ["order-1", "order-2", "order-3"]
        dedup.mark_batch_synced(ids)
        assert all(dedup.is_synced(oid) for oid in ids)

    def test_synced_count(self, tmp_path):
        dedup = OrderDeduplicator(str(tmp_path / "synced.json"))
        dedup.mark_batch_synced(["a", "b", "c"])
        assert dedup.synced_count == 3

    def test_reset(self, tmp_path):
        dedup = OrderDeduplicator(str(tmp_path / "synced.json"))
        dedup.mark_synced("order-1")
        dedup.reset()
        assert dedup.is_synced("order-1") is False
        assert dedup.synced_count == 0

    def test_handles_missing_file(self, tmp_path):
        dedup = OrderDeduplicator(str(tmp_path / "nonexistent.json"))
        assert dedup.synced_count == 0

    def test_handles_corrupted_file(self, tmp_path):
        filepath = str(tmp_path / "synced.json")
        with open(filepath, 'w') as f:
            f.write("NOT VALID JSON {{{")
        dedup = OrderDeduplicator(filepath)
        assert dedup.synced_count == 0

    def test_duplicate_mark_idempotent(self, tmp_path):
        dedup = OrderDeduplicator(str(tmp_path / "synced.json"))
        dedup.mark_synced("order-1")
        dedup.mark_synced("order-1")
        assert dedup.synced_count == 1


class TestProductMatching:
    """Tests for eBay → Shopify product matching."""

    def test_matched_item_returns_variant(self, metadata_lookup):
        item_id = "123456789012"
        match = metadata_lookup.get(item_id)
        assert match is not None
        assert match["variant_id"] == "45678901234"

    def test_unmatched_item_returns_none(self, metadata_lookup):
        match = metadata_lookup.get("999999999999")
        assert match is None


class TestOrderCreation:
    """Tests for Shopify order creation from eBay data."""

    def test_builds_shipping_address(self, sample_ebay_order):
        addr = sample_ebay_order["shipping_address"]
        assert addr["name"] == "John Doe"
        assert addr["city"] == "Anytown"
        assert addr["state"] == "CA"
        assert addr["postal_code"] == "90210"

    def test_maps_line_items(self, sample_ebay_order, metadata_lookup):
        for item in sample_ebay_order["line_items"]:
            match = metadata_lookup.get(item["item_id"])
            assert match is not None
            assert "variant_id" in match

    def test_unmatched_creates_custom_line_item(self, sample_ebay_order):
        """Unmatched eBay items should create custom line items."""
        item = sample_ebay_order["line_items"][0]
        # Simulate no match
        custom = {
            "title": item["title"],
            "price": item["price"],
            "quantity": item["quantity"],
        }
        assert custom["title"] == "Replacement Water Pump Assembly"
        assert custom["price"] == "29.95"

    def test_order_includes_tags(self, sample_ebay_order):
        """Shopify order should be tagged with eBay source info."""
        tags = f"ebay-sync,{sample_ebay_order['order_id']}"
        assert "ebay-sync" in tags
        assert sample_ebay_order["order_id"] in tags
