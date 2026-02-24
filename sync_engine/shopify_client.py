"""
Shopify Admin REST API client.

Handles product updates, inventory level management, and order creation
via Shopify's Admin API with Basic Auth credentials.
"""

import json
import re
import logging
from typing import Optional

import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15


class ShopifyClient:
    """
    Client for Shopify Admin REST API.

    Supports product field updates, inventory level management,
    and order creation. Handles rate limiting (HTTP 429) and
    returns structured result dicts for every operation.
    """

    def __init__(self, config_path: str):
        """
        Load Shopify credentials from a JSON config file.

        Expected config format:
            {
                "store_name": "mystore",
                "api_key": "...",
                "password": "...",
                "api_version": "2024-01"
            }

        Args:
            config_path: Path to shopify config JSON.

        Raises:
            RuntimeError: If config is missing or invalid.
        """
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                conf = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            raise RuntimeError(f"Invalid Shopify config: {e}")

        self.shop = conf.get("store_name", "").strip()
        self.api_key = conf.get("api_key", "").strip()
        self.password = conf.get("password", "").strip()
        self.api_version = conf.get("api_version", "2024-01").strip()

        if not all([self.shop, self.api_key, self.password]):
            raise RuntimeError("Config must include store_name, api_key, and password")

        self._auth = HTTPBasicAuth(self.api_key, self.password)

    @property
    def base_url(self) -> str:
        return f"https://{self.shop}.myshopify.com/admin/api/{self.api_version}"

    # ── Product Updates ──────────────────────────────────────────

    def update_product_field(self, product_id: str, variant_id: str,
                             field: str, value: str) -> dict:
        """
        Update a single field on a Shopify product.

        Supported fields:
            title    → product.title
            price    → variant.price
            sku      → variant.sku
            status   → product.published (active = True)

        Args:
            product_id: Shopify product ID.
            variant_id: Shopify variant ID (required for price/sku).
            field: Field name to update.
            value: New field value.

        Returns:
            Dict with 'success', 'message', and optional 'rate_limited'.
        """
        if not product_id:
            return {"success": False, "message": "No product_id", "skipped": True}

        payload = {"product": {"id": int(product_id)}}

        if field == "title":
            payload["product"]["title"] = value
        elif field == "status":
            payload["product"]["published"] = value.lower() == "active"
        elif field in ("sku", "price") and variant_id:
            variant_update = {"id": int(variant_id)}
            if field == "sku":
                variant_update["sku"] = value
            elif field == "price":
                clean = re.sub(r"[^\d.]", "", str(value)).strip() or "0"
                variant_update["price"] = clean
            payload["product"]["variants"] = [variant_update]
        else:
            return {"success": False, "message": f"Unsupported field: {field}", "skipped": True}

        url = f"{self.base_url}/products/{product_id}.json"
        return self._put(url, payload, field)

    # ── Inventory ────────────────────────────────────────────────

    def set_inventory_level(self, variant_id: str, quantity: int,
                            location_name: Optional[str] = None) -> dict:
        """
        Set inventory quantity for a variant at a specific location.

        Pipeline:
            1. GET variant → extract inventory_item_id
            2. GET locations → find target (or first available)
            3. POST inventory_levels/set → update quantity

        Args:
            variant_id: Shopify variant ID.
            quantity: New inventory quantity.
            location_name: Target location name (optional, falls back to first).

        Returns:
            Dict with 'success' and 'message'.
        """
        if not variant_id:
            return {"success": False, "message": "No variant_id", "skipped": True}

        # Step 1: Get inventory_item_id from variant
        variant_url = f"{self.base_url}/variants/{variant_id}.json"
        try:
            resp = requests.get(variant_url, auth=self._auth, timeout=DEFAULT_TIMEOUT)
            if resp.status_code != 200:
                return {"success": False, "message": f"Variant lookup failed: HTTP {resp.status_code}"}

            inv_item_id = resp.json().get("variant", {}).get("inventory_item_id")
            if not inv_item_id:
                return {"success": False, "message": "No inventory_item_id found"}
        except requests.RequestException as e:
            return {"success": False, "message": f"Variant lookup error: {e}"}

        # Step 2: Resolve location
        location_id = self._resolve_location(location_name)
        if not location_id:
            return {"success": False, "message": "No Shopify locations found"}

        # Step 3: Set inventory level
        url = f"{self.base_url}/inventory_levels/set.json"
        payload = {
            "location_id": location_id,
            "inventory_item_id": inv_item_id,
            "available": quantity,
        }

        try:
            resp = requests.post(url, auth=self._auth, json=payload, timeout=DEFAULT_TIMEOUT)
            if resp.status_code == 200:
                return {"success": True, "message": f"Inventory set to {quantity}"}
            elif resp.status_code == 429:
                return {"success": False, "message": "Rate limited", "rate_limited": True}
            return {"success": False, "message": f"HTTP {resp.status_code}"}
        except requests.RequestException as e:
            return {"success": False, "message": f"Connection error: {e}"}

    # ── Order Creation ───────────────────────────────────────────

    def create_order(self, line_items: list, shipping_address: dict,
                     note: str = "", tags: str = "",
                     source_name: str = "external") -> dict:
        """
        Create a new order on Shopify.

        Args:
            line_items: List of line item dicts (variant_id + quantity,
                        or title + price for unmatched items).
            shipping_address: Dict with first_name, last_name, address1, etc.
            note: Order note (e.g., source order ID).
            tags: Comma-separated order tags.
            source_name: Order source identifier.

        Returns:
            Dict with 'success', 'shopify_order_id', 'shopify_order_name'.
        """
        payload = {
            "order": {
                "line_items": line_items,
                "shipping_address": shipping_address,
                "billing_address": shipping_address,
                "financial_status": "paid",
                "fulfillment_status": None,
                "note": note,
                "tags": tags,
                "source_name": source_name,
            }
        }

        url = f"{self.base_url}/orders.json"
        try:
            resp = requests.post(url, auth=self._auth, json=payload, timeout=30)
            if resp.status_code in (200, 201):
                order = resp.json().get("order", {})
                return {
                    "success": True,
                    "shopify_order_id": order.get("id"),
                    "shopify_order_name": order.get("name"),
                }
            return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        except requests.RequestException as e:
            return {"success": False, "error": str(e)}

    # ── Product Creation ─────────────────────────────────────────

    def create_product(self, title: str, body_html: str, price: str,
                       sku: str = "", mpn: str = "", brand: str = "",
                       tags: str = "", image_urls: list = None) -> dict:
        """
        Create a new product on Shopify.

        Args:
            title: Product title.
            body_html: HTML description.
            price: Product price.
            sku: SKU / custom label.
            mpn: MPN (stored as barcode).
            brand: Brand (stored as vendor).
            tags: Comma-separated tags.
            image_urls: List of image URLs for Shopify to download.

        Returns:
            Dict with 'success', 'product_id', 'variant_id', 'handle'.
        """
        payload = {
            "product": {
                "title": title,
                "body_html": body_html or f"<p>{title}</p>",
                "vendor": brand or "Unbranded",
                "tags": tags,
                "status": "active",
                "variants": [{
                    "option1": "Default Title",
                    "price": re.sub(r"[^\d.]", "", str(price)).strip() or "0",
                    "sku": sku,
                    "barcode": mpn,
                    "inventory_management": "shopify",
                    "inventory_policy": "deny",
                    "fulfillment_service": "manual",
                    "requires_shipping": True,
                    "taxable": True,
                }],
                "images": [{"src": url} for url in (image_urls or [])],
            }
        }

        url = f"{self.base_url}/products.json"
        try:
            resp = requests.post(url, auth=self._auth, json=payload, timeout=30)
            if resp.status_code in (200, 201):
                product = resp.json().get("product", {})
                variants = product.get("variants", [])
                return {
                    "success": True,
                    "product_id": product.get("id"),
                    "variant_id": variants[0].get("id") if variants else None,
                    "handle": product.get("handle"),
                }
            return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        except requests.RequestException as e:
            return {"success": False, "error": str(e)}

    # ── Internal Helpers ─────────────────────────────────────────

    def _resolve_location(self, name: Optional[str] = None) -> Optional[int]:
        """Find a Shopify location by name, or fall back to the first one."""
        url = f"{self.base_url}/locations.json"
        try:
            resp = requests.get(url, auth=self._auth, timeout=DEFAULT_TIMEOUT)
            if resp.status_code != 200:
                return None

            locations = resp.json().get("locations", [])
            if not locations:
                return None

            if name:
                for loc in locations:
                    if loc.get("name", "").lower() == name.lower():
                        return loc.get("id")

            return locations[0].get("id")
        except requests.RequestException:
            return None

    def _put(self, url: str, payload: dict, field: str) -> dict:
        """Execute a PUT request with standard error handling."""
        try:
            resp = requests.put(url, auth=self._auth, json=payload,
                                timeout=DEFAULT_TIMEOUT)
            if resp.status_code == 200:
                return {"success": True, "message": f"Synced {field} to Shopify"}
            elif resp.status_code == 429:
                return {"success": False, "message": "Rate limited", "rate_limited": True}
            return {"success": False, "message": f"HTTP {resp.status_code}"}
        except requests.Timeout:
            return {"success": False, "message": "Timeout"}
        except requests.RequestException as e:
            return {"success": False, "message": f"Connection error: {str(e)[:100]}"}
