"""
Field-level change routing between eBay and Shopify.

Routes individual field changes to the correct API endpoints on each
platform. Different fields have different sync behaviors:
    - Inventory uses dedicated endpoints on both platforms
    - Title changes on eBay trigger a description rebuild cascade
    - Price requires cleaning (strip $, validate numeric)
    - Some fields (like MPN) only sync to one platform
"""

import logging
from typing import Callable

from .shopify_client import ShopifyClient
from .ebay_client import EbayClient
from .account_router import AccountRouter

logger = logging.getLogger(__name__)

# Fields that sync to eBay
EBAY_FIELDS = {"sku", "price", "title", "mpn", "brand", "inventory"}

# Fields that sync to Shopify
SHOPIFY_FIELDS = {"sku", "price", "title", "status", "inventory"}


class FieldSyncEngine:
    """
    Orchestrates field-level syncing across eBay and Shopify.

    For each field change, determines which platforms need updating,
    calls the appropriate API on each, and returns a combined result.
    """

    def __init__(self, ebay_router: AccountRouter,
                 shopify_client: ShopifyClient,
                 description_builder: Callable = None):
        """
        Args:
            ebay_router: AccountRouter for multi-account eBay operations.
            shopify_client: ShopifyClient instance.
            description_builder: Optional callable(title, brand, mpn,
                                 condition, quality_grade, image_urls)
                                 that returns HTML description string.
                                 Used during title rebuilds on eBay.
        """
        self.ebay_router = ebay_router
        self.shopify = shopify_client
        self.description_builder = description_builder

    def sync_field(self, entry: dict, field: str,
                   new_value: str, old_value: str = "",
                   sync_ebay: bool = True,
                   sync_shopify: bool = True) -> dict:
        """
        Sync a field change to eBay and/or Shopify.

        Args:
            entry: Inventory entry with platform IDs
                   (ebay_item_id, product_id, variant_id).
            field: Field name (price, title, sku, inventory, etc.).
            new_value: New field value.
            old_value: Previous value (for logging).
            sync_ebay: Whether to push to eBay.
            sync_shopify: Whether to push to Shopify.

        Returns:
            Dict with 'ebay' and 'shopify' sub-results.
        """
        result = {"ebay": None, "shopify": None}

        # eBay sync
        if sync_ebay and field in EBAY_FIELDS:
            result["ebay"] = self._sync_to_ebay(entry, field, new_value)
        elif sync_ebay:
            result["ebay"] = {"success": True, "message": f"{field} not synced to eBay", "skipped": True}

        # Shopify sync
        if sync_shopify and field in SHOPIFY_FIELDS:
            result["shopify"] = self._sync_to_shopify(entry, field, new_value)
        elif sync_shopify:
            result["shopify"] = {"success": True, "message": f"{field} not synced to Shopify", "skipped": True}

        return result

    def _sync_to_ebay(self, entry: dict, field: str, value: str) -> dict:
        """Route a field change to eBay via the account router."""

        def _ebay_operation(client: EbayClient, entry: dict) -> dict:
            item_id = entry.get("ebay_item_id")
            if not item_id:
                return {"success": False, "message": "No ebay_item_id", "skipped": True}

            if field == "inventory":
                try:
                    qty = int(value)
                except (ValueError, TypeError):
                    qty = 0
                return client.revise_inventory(item_id, qty)

            if field == "title" and self.description_builder:
                return self._title_rebuild(client, entry, value)

            return client.revise_field(item_id, field, value)

        return self.ebay_router.route_operation(entry, _ebay_operation)

    def _title_rebuild(self, client: EbayClient, entry: dict,
                       new_title: str) -> dict:
        """
        Title change cascade: GetItem → rebuild description → ReviseItem.

        When a title changes, the HTML description (which contains the
        title text) also needs to be rebuilt and pushed along with the
        existing photos.
        """
        item_id = entry.get("ebay_item_id")

        # Step 1: Fetch existing images
        get_result = client.get_item(item_id)
        if not get_result.get("success"):
            # Fallback: title-only revise without description rebuild
            logger.warning(f"GetItem failed for {item_id}, falling back to title-only")
            return client.revise_field(item_id, "title", new_title)

        image_urls = get_result.get("picture_urls", [])

        # Step 2: Rebuild description
        import html as html_mod
        description = self.description_builder(
            title=new_title,
            brand=entry.get("brand", "Unbranded"),
            mpn=entry.get("mpn", "Does Not Apply"),
            condition=entry.get("condition", "Used"),
            quality_grade=entry.get("quality_grade", "B"),
            image_urls=image_urls,
        )

        # Step 3: ReviseItem with title + description + photos
        safe_title = html_mod.escape(new_title[:80])
        picture_xml = "\n".join(
            f"        <PictureURL>{html_mod.escape(url)}</PictureURL>"
            for url in image_urls[:24]
        )

        elements = f"""<Title>{safe_title}</Title>
        <Description><![CDATA[{description}]]></Description>
        <PictureDetails>
{picture_xml}
        </PictureDetails>"""

        return client.revise_item(item_id, elements)

    def _sync_to_shopify(self, entry: dict, field: str, value: str) -> dict:
        """Route a field change to Shopify."""
        product_id = entry.get("product_id")
        variant_id = entry.get("variant_id")

        if not product_id:
            return {"success": False, "message": "No product_id", "skipped": True}

        if field == "inventory":
            try:
                qty = int(value)
            except (ValueError, TypeError):
                qty = 0
            return self.shopify.set_inventory_level(variant_id, qty)

        return self.shopify.update_product_field(product_id, variant_id, field, value)
