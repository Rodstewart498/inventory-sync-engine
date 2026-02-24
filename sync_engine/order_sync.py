"""
eBay → Shopify order synchronization.

Fetches completed orders from all eBay accounts, deduplicates against
previously synced orders, matches line items to Shopify products via
metadata lookup, and creates corresponding Shopify orders.
"""

import logging
from typing import List, Dict, Callable, Optional

from .ebay_client import EbayClient
from .shopify_client import ShopifyClient
from .account_router import AccountRouter
from .deduplication import SyncedOrderTracker

logger = logging.getLogger(__name__)


class OrderSyncEngine:
    """
    Sync eBay orders to Shopify.

    Pipeline:
        1. Fetch orders from all eBay accounts via GetOrders
        2. Filter out already-synced orders
        3. Match eBay line items to Shopify variants via metadata
        4. Create Shopify orders with address and line items
        5. Track synced order IDs for deduplication
    """

    def __init__(self, ebay_router: AccountRouter,
                 shopify_client: ShopifyClient,
                 tracker: SyncedOrderTracker,
                 product_matcher: Callable[[str], Optional[dict]]):
        """
        Args:
            ebay_router: AccountRouter for multi-account eBay access.
            shopify_client: ShopifyClient instance.
            tracker: SyncedOrderTracker for deduplication.
            product_matcher: Callable that takes an ebay_item_id string
                             and returns a dict with 'product_id',
                             'variant_id', 'title', etc., or None.
        """
        self.ebay_router = ebay_router
        self.shopify = shopify_client
        self.tracker = tracker
        self.product_matcher = product_matcher

    def fetch_all_orders(self, days_back: int = 7) -> List[dict]:
        """
        Fetch orders from all configured eBay accounts.

        Returns:
            List of order dicts, each annotated with 'ebay_account'
            and 'already_synced' flags. Sorted newest first.
        """
        all_orders = []

        for account_name in self.ebay_router.account_names:
            config_path = self.ebay_router.get_config_path(account_name)
            if not config_path:
                continue

            try:
                token = self.ebay_router.token_provider(config_path)
                client = EbayClient(token, account_name)
                orders = client.get_orders(days_back)

                for order in orders:
                    order["ebay_account"] = account_name
                    order["already_synced"] = self.tracker.is_synced(
                        order.get("ebay_order_id", "")
                    )
                    all_orders.append(order)

            except Exception as e:
                logger.error(f"Failed to fetch orders from {account_name}: {e}")

        all_orders.sort(key=lambda x: x.get("created_time", ""), reverse=True)
        return all_orders

    def sync_pending_orders(self, days_back: int = 7) -> dict:
        """
        Fetch and sync all pending eBay orders to Shopify.

        Returns:
            Summary dict with 'total', 'synced', 'already_synced',
            'errors', and 'details' list.
        """
        orders = self.fetch_all_orders(days_back)

        total = len(orders)
        already_synced = sum(1 for o in orders if o.get("already_synced"))
        pending = [o for o in orders if not o.get("already_synced")]

        synced = 0
        errors = 0
        details = []

        for order in pending:
            order_id = order.get("ebay_order_id", "")
            account = order.get("ebay_account", "")

            result = self._sync_single_order(order, account)
            details.append({
                "ebay_order_id": order_id,
                "account": account,
                **result,
            })

            if result.get("success"):
                self.tracker.mark_synced(order_id)
                synced += 1
            else:
                errors += 1

        return {
            "total": total,
            "already_synced": already_synced,
            "synced": synced,
            "errors": errors,
            "details": details,
        }

    def _sync_single_order(self, order: dict, account_name: str) -> dict:
        """
        Create a Shopify order from a single eBay order.

        Matches line items to Shopify variants where possible.
        Unmatched items create custom line items preserving
        the eBay title and price.
        """
        line_items = []
        unmatched = []

        for item in order.get("line_items", []):
            ebay_item_id = item.get("ebay_item_id")
            shopify_product = self.product_matcher(ebay_item_id) if ebay_item_id else None

            properties = []
            if ebay_item_id:
                properties.append({"name": "ebay_item_id", "value": str(ebay_item_id)})

            if shopify_product and shopify_product.get("variant_id"):
                li = {
                    "variant_id": int(shopify_product["variant_id"]),
                    "quantity": item.get("quantity", 1),
                }
                if properties:
                    li["properties"] = properties
                line_items.append(li)
            else:
                unmatched.append(item.get("title", "Unknown"))
                li = {
                    "title": item.get("title", "eBay Item"),
                    "quantity": item.get("quantity", 1),
                    "price": item.get("price", "0.00"),
                    "requires_shipping": True,
                    "sku": item.get("sku", ""),
                }
                if properties:
                    li["properties"] = properties
                line_items.append(li)

        if not line_items:
            return {"success": False, "error": "No line items"}

        # Build shipping address
        shipping = order.get("shipping", {})
        full_name = shipping.get("name", "Buyer")
        parts = full_name.split(" ", 1)
        first = parts[0] if parts else "Buyer"
        last = parts[1] if len(parts) > 1 else ""

        address = {
            "first_name": first,
            "last_name": last,
            "address1": shipping.get("street1", ""),
            "address2": shipping.get("street2", ""),
            "city": shipping.get("city", ""),
            "province": shipping.get("state", ""),
            "zip": shipping.get("postal_code", ""),
            "country": shipping.get("country", "US"),
            "phone": shipping.get("phone", ""),
        }

        order_id = order.get("ebay_order_id", "")
        buyer = order.get("buyer_user_id", "N/A")
        note = f"eBay Order: {order_id} | Account: {account_name} | Buyer: {buyer}"
        tags = f"ebay, {account_name}, {order_id}"

        result = self.shopify.create_order(
            line_items=line_items,
            shipping_address=address,
            note=note,
            tags=tags,
            source_name="ebay",
        )

        if unmatched:
            result["unmatched_items"] = unmatched

        return result
