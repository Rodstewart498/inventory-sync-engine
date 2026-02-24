"""
sync_engine — Bidirectional eBay ↔ Shopify synchronization.

Cross-platform inventory, pricing, and order sync with
multi-account routing and field-level change propagation.

Modules:
    field_sync        Field-level change routing (price, title, SKU, etc.)
    order_sync        eBay → Shopify order mirroring
    account_router    Multi-account eBay resolution with caching
    ebay_client       eBay Trading API client (XML/SOAP)
    shopify_client    Shopify Admin REST API client
    deduplication     Synced order ID tracking
"""

__version__ = "1.0.0"
