"""Shared test fixtures for inventory-sync-engine tests."""

import pytest
from unittest.mock import MagicMock


@pytest.fixture
def sample_entry():
    """Sample inventory metadata entry for testing."""
    return {
        "internal_id": "INV-00001",
        "ebay_item_id": "123456789012",
        "product_id": "7890123456",
        "variant_id": "45678901234",
        "inventory_item_id": "98765432100",
        "location_id": "11223344556",
        "title": "Replacement Water Pump Assembly 2004-2008",
        "price": "29.95",
        "sku": "BIN-A12",
        "brand": "OEM",
        "mpn": "ABC-12345-00-00",
        "condition": "Used - Good",
        "quality_grade": "B",
        "inventory": "1",
        "ebay_account": "TestAccount1",
    }


@pytest.fixture
def mock_ebay_client():
    """Mock eBay Trading API client."""
    client = MagicMock()
    client.revise_item.return_value = {
        "success": True,
        "message": "Success",
    }
    client.get_item.return_value = {
        "success": True,
        "picture_urls": ["https://i.ebayimg.com/images/g/test/s-l1600.jpg"],
        "title": "Replacement Water Pump Assembly 2004-2008",
    }
    client.get_token.return_value = "mock_token_abc123"
    return client


@pytest.fixture
def mock_shopify_client():
    """Mock Shopify Admin API client."""
    client = MagicMock()
    client.update_variant.return_value = {
        "success": True,
        "message": "Variant updated",
    }
    client.set_inventory_level.return_value = {
        "success": True,
        "message": "Inventory updated",
    }
    client.create_order.return_value = {
        "success": True,
        "order_id": "5001234567890",
        "message": "Order created",
    }
    return client


@pytest.fixture
def sample_ebay_order():
    """Sample eBay order for testing order sync."""
    return {
        "order_id": "12-34567-89012",
        "buyer_user_id": "test_buyer_123",
        "total": "29.95",
        "created_time": "2024-01-15T10:30:00.000Z",
        "shipping_address": {
            "name": "John Doe",
            "street1": "123 Main St",
            "city": "Anytown",
            "state": "CA",
            "postal_code": "90210",
            "country": "US",
        },
        "line_items": [
            {
                "item_id": "123456789012",
                "title": "Replacement Water Pump Assembly",
                "quantity": 1,
                "price": "29.95",
                "sku": "BIN-A12",
            }
        ],
    }


@pytest.fixture
def metadata_lookup():
    """Sample metadata for product matching."""
    return {
        "123456789012": {
            "product_id": "7890123456",
            "variant_id": "45678901234",
            "title": "Replacement Water Pump Assembly",
        },
    }
