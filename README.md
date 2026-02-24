# inventory-sync-engine

Bidirectional inventory, pricing, and order synchronization between eBay and Shopify. Manages multi-account eBay operations with intelligent account routing, field-level change propagation, and automated order mirroring.

**Running in production across 3 eBay accounts and 1 Shopify store, managing 11,000+ SKUs.**

---

## 🎯 Problem

Selling the same inventory on multiple platforms means every field change — price, title, quantity, SKU — needs to propagate correctly across all connected stores. Miss an inventory update and you oversell. Miss a price change and your margins erode.

Commercial sync tools exist but lack the flexibility for custom business logic: field-level routing rules, multi-account resolution with caching, and order synchronization with product matching.

## ✅ Solution

A Python sync engine that handles three types of cross-platform operations:

```
┌───────────────────────────────────────────────────────────────┐
│                    Inventory Sync Engine                      │
│                                                               │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │  1. Field Sync — Real-time per-item changes             │ │
│  │     Title, Price, SKU, Inventory, MPN, Brand            │ │
│  │     Routes each field to the correct API endpoint       │ │
│  │     Handles platform-specific constraints               │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                               │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │  2. Account Router — Multi-account eBay resolution      │ │
│  │     Cached account → try first → stop on success        │ │
│  │     Items exist on exactly one account                  │ │
│  │     Automatic caching of successful account             │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                               │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │  3. Order Sync — eBay orders → Shopify                  │ │
│  │     Fetch via GetOrders API → match products            │ │
│  │     Create Shopify orders with address + line items     │ │
│  │     Deduplication via synced order ID tracking          │ │
│  └──────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────┘
```

---

## 🏗 Architecture

### Field Sync Pipeline

```
User edits a field (e.g., price: $29.95 → $34.95)
    │
    ▼
┌─────────────────┐      ┌─────────────────┐
│  eBay Sync       │      │  Shopify Sync    │
│                  │      │                  │
│  ● price →       │      │  ● price →       │
│    ReviseItem    │      │    PUT /variants │
│  ● inventory →   │      │  ● inventory →   │
│    ReviseInv...  │      │    POST /levels  │
│  ● title →       │      │  ● title →       │
│    GetItem +     │      │    PUT /products │
│    rebuild desc  │      │  ● status →      │
│    + ReviseItem  │      │    published     │
│  ● sku, mpn →    │      │    flag          │
│    ReviseItem    │      └─────────────────┘
└─────────────────┘
         │
         ▼
┌─────────────────────────────┐
│  Account Router              │
│                              │
│  1. Check cached account     │
│  2. Try cached first         │
│  3. Fall through to others   │
│  4. Stop on first success    │
│  5. Cache successful account │
│  6. Abort on rate limit      │
└─────────────────────────────┘
```

### Order Sync Pipeline

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│ eBay Account │    │ eBay Account │    │ eBay Account │
│ GetOrders    │    │ GetOrders    │    │ GetOrders    │
└──────┬───────┘    └──────┬───────┘    └──────┬───────┘
       │                   │                   │
       └───────────┬───────┘───────────────────┘
                   │
                   ▼
        ┌────────────────────┐
        │ Deduplication       │
        │ (synced_order_ids)  │
        └─────────┬──────────┘
                  │ new orders only
                  ▼
        ┌────────────────────┐
        │ Product Matching    │
        │ ebay_item_id →     │
        │ shopify_variant_id │
        └─────────┬──────────┘
                  │
                  ▼
        ┌────────────────────┐
        │ Shopify Order       │
        │ POST /orders.json   │
        │ + shipping address  │
        │ + line items        │
        │ + tags & notes      │
        └────────────────────┘
```

---

## 📁 Project Structure

```
inventory-sync-engine/
├── sync_engine/
│   ├── __init__.py
│   ├── shopify_client.py     # Shopify Admin API client
│   ├── ebay_client.py        # eBay Trading API client (XML)
│   ├── field_sync.py         # Field-level change routing
│   ├── order_sync.py         # eBay → Shopify order mirroring
│   ├── account_router.py     # Multi-account resolution + caching
│   └── deduplication.py      # Synced order ID tracking
├── tests/
│   ├── test_field_sync.py
│   ├── test_order_sync.py
│   ├── test_account_router.py
│   └── conftest.py
├── docs/
│   └── sync_architecture.md
├── requirements.txt
├── .env.example
├── .gitignore
├── LICENSE
└── README.md
```

---

## ✨ Key Features

### Intelligent Account Routing
When managing multiple eBay seller accounts, each item lives on exactly one. The router uses cached account info to avoid unnecessary API calls — tries the known account first, falls through to others only on failure, and aborts entirely on rate limits (since all accounts share the same API quota).

### Field-Aware Sync Routing
Not all fields sync the same way. The engine knows that:
- **Inventory** uses `ReviseInventoryStatus` (eBay) and `POST /inventory_levels/set.json` (Shopify) — different endpoints from other fields
- **Title changes** trigger a cascade: `GetItem` → fetch existing photos → rebuild HTML description → `ReviseItem` with title + description + photos
- **Price** gets cleaned (strip `$`, validate numeric) before sending
- **Status** maps to Shopify's `published` boolean
- **MPN** syncs to eBay ItemSpecifics but skips Shopify (metafields not implemented)

### Order Deduplication
Tracks synced order IDs to prevent duplicate Shopify orders. Uses atomic JSON writes for crash-safe persistence.

### Product Matching
Maps eBay line items to Shopify variants via `ebay_item_id` → metadata lookup. Unmatched items create custom line items with the eBay title and price, preserving the order even when product mapping is incomplete.

---

## 🛠 Tech Stack

| Component | Technology |
|-----------|-----------|
| eBay Integration | Trading API (XML), OAuth 2.0 |
| Shopify Integration | Admin REST API (JSON), Basic Auth |
| HTTP Client | requests |
| Data Persistence | Atomic JSON writes |
| Concurrency | threading (async Shopify sync) |

---

## 🔧 Setup

```bash
git clone https://github.com/Rodstewart498/inventory-sync-engine.git
cd inventory-sync-engine
pip install -r requirements.txt
cp .env.example .env  # Configure your credentials
```

---

## 📊 Example Usage

### Sync a price change across platforms

```python
from sync_engine.field_sync import FieldSyncEngine

engine = FieldSyncEngine(
    ebay_configs_dir="./config/ebay/",
    shopify_config_path="./config/shopify.json"
)

entry = {
    "ebay_item_id": "123456789012",
    "product_id": "7890123456",
    "variant_id": "45678901234",
    "title": "Replacement Water Pump Assembly",
    "price": "29.95",
}

result = engine.sync_field(
    entry=entry,
    field="price",
    new_value="34.95",
    old_value="29.95"
)

print(result)
# {
#   'ebay': {'success': True, 'message': 'Synced (SellerAccount1)'},
#   'shopify': {'success': True, 'message': 'Synced price to Shopify'}
# }
```

### Sync eBay orders to Shopify

```python
from sync_engine.order_sync import OrderSyncEngine

sync = OrderSyncEngine(
    ebay_configs_dir="./config/ebay/",
    shopify_config_path="./config/shopify.json",
    metadata=inventory_metadata,
    synced_orders_path="./data/synced_orders.json"
)

result = sync.sync_pending_orders(days_back=7)

print(f"Synced {result['synced']} of {result['total']} orders")
print(f"Already synced: {result['already_synced']}")
print(f"Errors: {result['errors']}")
```

---

## ⚡ Performance

| Operation | Time | Notes |
|-----------|------|-------|
| Single field sync (eBay) | ~1-2s | Includes OAuth token check |
| Single field sync (Shopify) | ~0.5-1s | Direct REST call |
| Title rebuild pipeline | ~3-5s | GetItem + rebuild + ReviseItem |
| Order fetch (per account) | ~2-3s | GetOrders with pagination |
| Full order sync (3 accounts) | ~10-15s | Fetch + dedupe + create |
| Account resolution (cached) | ~1s | Skips to known account |
| Account resolution (uncached) | ~3-6s | Tries accounts sequentially |

---

## 📝 License

© 2025 Rod Stewart. All Rights Reserved. This code is provided for portfolio demonstration purposes only. No permission is granted to use, copy, modify, or distribute this software.

---

## 🙋 Author

**Rod Stewart** — [GitHub](https://github.com/Rodstewart498)

Built to keep multi-platform inventory accurate without the cost or limitations of commercial sync tools.
