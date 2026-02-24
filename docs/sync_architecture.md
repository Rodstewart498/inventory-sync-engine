# Sync Architecture — Technical Detail

## Field Sync: How Different Fields Route

Not every field change is the same API call. The sync engine knows
the platform-specific endpoint for each field type.

### eBay Field Routing

| Field | API Call | Notes |
|-------|----------|-------|
| **price** | ReviseItem → `<StartPrice>` | Strip `$`, validate numeric |
| **sku** | ReviseItem → `<SKU>` | Direct XML element |
| **title** | GetItem + ReviseItem | Cascade: fetch photos → rebuild description → push all three |
| **mpn** | ReviseItem → `<ItemSpecifics>` | NameValueList format |
| **brand** | ReviseItem → `<ItemSpecifics>` | NameValueList format |
| **inventory** | ReviseInventoryStatus | Separate API call — faster, different rate limits |

### Shopify Field Routing

| Field | API Endpoint | Notes |
|-------|-------------|-------|
| **price** | PUT /products/{id}.json → variants | Nested under product.variants |
| **sku** | PUT /products/{id}.json → variants | Same endpoint as price |
| **title** | PUT /products/{id}.json | Top-level product field |
| **status** | PUT /products/{id}.json → published | Boolean: active = true |
| **inventory** | POST /inventory_levels/set.json | Requires variant → inventory_item_id → location_id chain |

### The Title Rebuild Cascade

Title changes on eBay are special because the HTML description contains
the title text. Changing just the title leaves the description stale.

```
1. User changes title
   │
   ▼
2. GetItem(item_id)
   → Extract existing PictureURLs (deduplicated)
   │
   ▼
3. Rebuild HTML description
   → New title + brand + MPN + quality grade + images
   │
   ▼
4. ReviseItem with:
   → <Title> (new)
   → <Description> (rebuilt HTML)
   → <PictureDetails> (existing images re-attached)
```

If GetItem fails (e.g., network timeout), the engine falls back to
a title-only ReviseItem rather than failing the entire operation.

---

## Account Router: Multi-Account Resolution

### The Problem
When you sell on multiple eBay accounts, each item exists on exactly
one of them. But the sync engine doesn't necessarily know which one.

### Resolution Strategy

```
Entry has ebay_account = "SellerAccount2" (cached from last sync)
    │
    ▼
Try SellerAccount2 first
    │
    ├── Success? → Done. Cache confirmed.
    │
    └── Failure?
        │
        ▼
    Try SellerAccount1
        │
        ├── Success? → Done. Update cache to SellerAccount1.
        │
        └── Failure?
            │
            ▼
        Try SellerAccount3
            │
            └── ... and so on
```

### Key Behaviors

**Stop-on-first-success**: Items can only exist on one account.
Once we find it, there's no point trying others.

**Abort-on-rate-limit**: All accounts share the same eBay API
quota. If one account hits the rate limit, the others will too.
Continuing wastes time and quota.

**Cache-on-success**: The successful account name is stored on
the entry dict (`entry['ebay_account']`). Next time, we skip
straight to that account — turning a 3-account sequential search
into a 1-account direct hit.

**Skipped ≠ Failed**: If an account returns "skipped" (e.g., field
not applicable), the router continues to the next account instead
of counting it as a failure.

---

## Order Sync: eBay → Shopify Pipeline

### Pipeline Steps

1. **Fetch**: GetOrders from each eBay account (configurable days back)
2. **Annotate**: Tag each order with source account + sync status
3. **Filter**: Skip orders already in the synced-IDs tracker
4. **Match**: For each line item, look up `ebay_item_id` in metadata
   to find the corresponding Shopify `variant_id`
5. **Create**: POST to Shopify Orders API with matched variants
   (or custom line items for unmatched products)
6. **Track**: Record the eBay order ID to prevent re-sync

### Product Matching

The matcher searches inventory metadata for entries where
`entry['ebay_item_id'] == line_item['ebay_item_id']`.

When a match is found, the Shopify order uses the actual variant ID,
so inventory levels decrement correctly. When no match is found,
a custom line item preserves the order data:

```python
# Matched — uses real Shopify variant
{"variant_id": 45678901234, "quantity": 1}

# Unmatched — preserves eBay data as custom line item
{"title": "Original eBay Title", "price": "49.95", "quantity": 1}
```

### Deduplication

The `SyncedOrderTracker` maintains a JSON file of processed order IDs.
Uses atomic writes (temp file → rename) to prevent corruption if the
process crashes mid-write. The tracker is checked before every sync
attempt and updated immediately after success.
