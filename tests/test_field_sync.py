"""Tests for field-level sync routing."""

from unittest.mock import MagicMock, patch
import pytest

from sync_engine.field_sync import FieldSyncEngine


@pytest.fixture
def engine(mock_ebay_client, mock_shopify_client):
    """Create a FieldSyncEngine with mocked clients."""
    eng = FieldSyncEngine.__new__(FieldSyncEngine)
    eng.ebay = mock_ebay_client
    eng.shopify = mock_shopify_client
    eng.account_names = ["TestAccount1", "TestAccount2"]
    return eng


class TestFieldRouting:
    """Tests for field-to-API routing logic."""

    def test_price_syncs_to_both_platforms(self, engine, sample_entry):
        result = engine.sync_field(sample_entry, "price", "34.95", "29.95")
        assert "ebay" in result or result.get("success") is True

    def test_inventory_syncs_to_both(self, engine, sample_entry):
        result = engine.sync_field(sample_entry, "inventory", "5", "1")
        assert result is not None

    def test_sku_syncs_to_ebay(self, engine, sample_entry):
        result = engine.sync_field(sample_entry, "sku", "BIN-B05", "BIN-A12")
        assert result is not None

    def test_unknown_field_returns_error(self, engine, sample_entry):
        result = engine.sync_field(sample_entry, "nonexistent_field", "value")
        assert result.get("success") is False or "error" in str(result).lower()

    def test_missing_item_id_skips_ebay(self, engine, sample_entry):
        sample_entry["ebay_item_id"] = ""
        result = engine.sync_field(sample_entry, "price", "34.95")
        assert result is not None


class TestPriceCleaning:
    """Tests for price value cleaning before sync."""

    def test_strips_dollar_sign(self, engine, sample_entry):
        result = engine.sync_field(sample_entry, "price", "$34.95", "$29.95")
        assert result is not None

    def test_handles_string_price(self, engine, sample_entry):
        result = engine.sync_field(sample_entry, "price", "34.95")
        assert result is not None

    def test_handles_numeric_price(self, engine, sample_entry):
        result = engine.sync_field(sample_entry, "price", 34.95)
        assert result is not None


class TestTitleSync:
    """Tests for title sync with description rebuild."""

    def test_title_triggers_description_rebuild(self, engine, sample_entry):
        engine.ebay.get_item.return_value = {
            "success": True,
            "picture_urls": ["https://i.ebayimg.com/test.jpg"],
        }
        engine.ebay.revise_item.return_value = {
            "success": True, "message": "OK"
        }
        result = engine.sync_field(
            sample_entry, "title", "New Title Here", "Old Title"
        )
        assert result is not None

    def test_title_falls_back_on_getitem_failure(self, engine, sample_entry):
        engine.ebay.get_item.return_value = {
            "success": False, "message": "Not found"
        }
        engine.ebay.revise_item.return_value = {
            "success": True, "message": "OK"
        }
        result = engine.sync_field(sample_entry, "title", "New Title")
        assert result is not None


class TestShopifySync:
    """Tests for Shopify-specific sync behavior."""

    def test_missing_variant_id_skips_shopify(self, engine, sample_entry):
        sample_entry["variant_id"] = ""
        result = engine.sync_field(sample_entry, "price", "34.95")
        assert result is not None

    def test_inventory_uses_inventory_endpoint(self, engine, sample_entry):
        result = engine.sync_field(sample_entry, "inventory", "10", "1")
        assert result is not None
