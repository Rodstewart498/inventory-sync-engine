"""
eBay Trading API client (XML-based).

Handles ReviseItem, ReviseInventoryStatus, GetItem, and GetOrders
via eBay's legacy Trading API with OAuth 2.0 bearer tokens.
"""

import re
import html
import logging
from typing import Optional, List
from datetime import datetime, timezone, timedelta

import requests

logger = logging.getLogger(__name__)

EBAY_API_URL = "https://api.ebay.com/ws/api.dll"
COMPAT_LEVEL = "967"
DEFAULT_TIMEOUT = 15


class EbayClient:
    """
    Client for eBay's Trading API.

    Uses XML request/response format with OAuth 2.0 bearer tokens.
    Each instance is bound to a single eBay seller account.
    """

    def __init__(self, access_token: str, account_name: str = ""):
        """
        Args:
            access_token: OAuth 2.0 access token for this account.
            account_name: Human-readable account label (for logging).
        """
        self.token = access_token
        self.account_name = account_name

    def _headers(self, call_name: str) -> dict:
        return {
            "X-EBAY-API-SITEID": "0",
            "X-EBAY-API-COMPATIBILITY-LEVEL": COMPAT_LEVEL,
            "X-EBAY-API-CALL-NAME": call_name,
            "X-EBAY-API-IAF-TOKEN": self.token,
            "Content-Type": "text/xml",
        }

    def _post(self, call_name: str, xml_body: str,
              timeout: int = DEFAULT_TIMEOUT) -> requests.Response:
        return requests.post(
            EBAY_API_URL,
            headers=self._headers(call_name),
            data=xml_body,
            timeout=timeout,
        )

    @staticmethod
    def _check_response(resp: requests.Response) -> dict:
        """Parse standard eBay ack/error from XML response."""
        if resp.status_code != 200:
            return {"success": False, "message": f"HTTP {resp.status_code}"}

        text = resp.text
        if "<Ack>Success</Ack>" in text:
            return {"success": True}
        if "<Ack>Warning</Ack>" in text:
            return {"success": True, "warning": True}

        error = re.search(r"<ShortMessage>(.*?)</ShortMessage>", text)
        msg = error.group(1) if error else "Unknown error"
        rate_limited = (
            "usage limit" in msg.lower()
            or "<ErrorCode>518</ErrorCode>" in text
        )
        return {"success": False, "message": msg, "rate_limited": rate_limited}

    # ── ReviseItem ───────────────────────────────────────────────

    def revise_item(self, item_id: str,
                    item_elements_xml: str) -> dict:
        """
        Revise a live eBay listing with arbitrary item elements.

        Args:
            item_id: eBay item ID.
            item_elements_xml: Inner XML for <Item> element
                               (e.g., <Title>...</Title>).

        Returns:
            Standard result dict with 'success' and 'message'.
        """
        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<ReviseItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
    <RequesterCredentials>
        <eBayAuthToken>{self.token}</eBayAuthToken>
    </RequesterCredentials>
    <Item>
        <ItemID>{item_id}</ItemID>
        {item_elements_xml}
    </Item>
</ReviseItemRequest>"""

        try:
            resp = self._post("ReviseItem", xml)
            result = self._check_response(resp)
            result["account"] = self.account_name
            return result
        except requests.RequestException as e:
            return {"success": False, "message": str(e), "account": self.account_name}

    def revise_field(self, item_id: str, field: str, value: str) -> dict:
        """
        Revise a single field on an eBay listing.

        Supported fields: sku, price, title, mpn, brand.
        Title changes should use revise_title_with_description() instead.

        Args:
            item_id: eBay item ID.
            field: Field name.
            value: New value.

        Returns:
            Standard result dict.
        """
        if field == "sku":
            elements = f"<SKU>{html.escape(value)}</SKU>"
        elif field == "price":
            clean = re.sub(r"[^\d.]", "", str(value)).strip() or "0"
            elements = f"<StartPrice>{clean}</StartPrice>"
        elif field == "title":
            safe = html.escape(value[:80])
            elements = f"<Title>{safe}</Title>"
        elif field == "mpn":
            elements = f"""<ItemSpecifics>
            <NameValueList>
                <Name>MPN</Name>
                <Value>{html.escape(value)}</Value>
            </NameValueList>
        </ItemSpecifics>"""
        elif field == "brand":
            elements = f"""<ItemSpecifics>
            <NameValueList>
                <Name>Brand</Name>
                <Value>{html.escape(value)}</Value>
            </NameValueList>
        </ItemSpecifics>"""
        else:
            return {"success": False, "message": f"Unsupported field: {field}", "skipped": True}

        return self.revise_item(item_id, elements)

    # ── ReviseInventoryStatus ────────────────────────────────────

    def revise_inventory(self, item_id: str, quantity: int) -> dict:
        """
        Update inventory quantity for a live listing.

        Uses ReviseInventoryStatus — faster than ReviseItem for
        quantity-only changes and doesn't count against the same
        rate limits.

        Args:
            item_id: eBay item ID.
            quantity: New available quantity.

        Returns:
            Standard result dict.
        """
        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<ReviseInventoryStatusRequest xmlns="urn:ebay:apis:eBLBaseComponents">
    <RequesterCredentials>
        <eBayAuthToken>{self.token}</eBayAuthToken>
    </RequesterCredentials>
    <InventoryStatus>
        <ItemID>{item_id}</ItemID>
        <Quantity>{quantity}</Quantity>
    </InventoryStatus>
</ReviseInventoryStatusRequest>"""

        try:
            resp = self._post("ReviseInventoryStatus", xml)
            result = self._check_response(resp)
            result["account"] = self.account_name
            return result
        except requests.RequestException as e:
            return {"success": False, "message": str(e), "account": self.account_name}

    # ── GetItem ──────────────────────────────────────────────────

    def get_item(self, item_id: str) -> dict:
        """
        Fetch full item details from eBay.

        Used during title changes to retrieve existing photo URLs
        before rebuilding the description.

        Args:
            item_id: eBay item ID.

        Returns:
            Dict with 'success', 'picture_urls' (list), and raw 'xml'.
        """
        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
    <RequesterCredentials>
        <eBayAuthToken>{self.token}</eBayAuthToken>
    </RequesterCredentials>
    <ItemID>{item_id}</ItemID>
    <DetailLevel>ReturnAll</DetailLevel>
    <IncludeItemSpecifics>false</IncludeItemSpecifics>
</GetItemRequest>"""

        try:
            resp = self._post("GetItem", xml, timeout=15)
            if resp.status_code != 200:
                return {"success": False, "message": f"HTTP {resp.status_code}"}

            text = resp.text
            if "<Ack>Failure</Ack>" in text:
                error = re.search(r"<ShortMessage>(.*?)</ShortMessage>", text)
                return {"success": False, "message": error.group(1) if error else "GetItem failed"}

            # Extract and deduplicate picture URLs
            urls = re.findall(r"<PictureURL>([^<]+)</PictureURL>", text)
            seen = set()
            unique = []
            for u in urls:
                if u not in seen:
                    seen.add(u)
                    unique.append(u)

            return {"success": True, "picture_urls": unique, "xml": text}

        except requests.RequestException as e:
            return {"success": False, "message": str(e)}

    # ── GetOrders ────────────────────────────────────────────────

    def get_orders(self, days_back: int = 7) -> List[dict]:
        """
        Fetch completed orders for this account.

        Args:
            days_back: Number of days to look back.

        Returns:
            List of order dicts with ebay_order_id, total, line_items,
            shipping address, buyer info, and created_time.
        """
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days_back)

        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<GetOrdersRequest xmlns="urn:ebay:apis:eBLBaseComponents">
    <RequesterCredentials>
        <eBayAuthToken>{self.token}</eBayAuthToken>
    </RequesterCredentials>
    <CreateTimeFrom>{start.strftime('%Y-%m-%dT%H:%M:%S.000Z')}</CreateTimeFrom>
    <CreateTimeTo>{end.strftime('%Y-%m-%dT%H:%M:%S.000Z')}</CreateTimeTo>
    <OrderRole>Seller</OrderRole>
    <OrderStatus>Completed</OrderStatus>
    <Pagination>
        <EntriesPerPage>100</EntriesPerPage>
        <PageNumber>1</PageNumber>
    </Pagination>
</GetOrdersRequest>"""

        try:
            resp = self._post("GetOrders", xml, timeout=30)
            if resp.status_code != 200:
                logger.error(f"GetOrders failed: HTTP {resp.status_code}")
                return []

            if "<Ack>Failure</Ack>" in resp.text:
                error = re.search(r"<ShortMessage>(.*?)</ShortMessage>", resp.text)
                logger.error(f"GetOrders error: {error.group(1) if error else 'unknown'}")
                return []

            return self._parse_orders(resp.text)

        except requests.RequestException as e:
            logger.error(f"GetOrders connection error: {e}")
            return []

    def _parse_orders(self, xml_text: str) -> List[dict]:
        """Parse GetOrders XML response into a list of order dicts."""
        orders = []
        order_blocks = re.findall(r"<Order>(.*?)</Order>", xml_text, re.DOTALL)

        for block in order_blocks:
            try:
                order = self._parse_single_order(block)
                if order.get("ebay_order_id") and order.get("line_items"):
                    orders.append(order)
            except Exception as e:
                logger.warning(f"Failed to parse order block: {e}")

        logger.info(f"Parsed {len(orders)} orders from {self.account_name}")
        return orders

    @staticmethod
    def _parse_single_order(block: str) -> dict:
        """Extract fields from a single <Order> XML block."""
        def extract(pattern, text, default=""):
            m = re.search(pattern, text)
            return m.group(1) if m else default

        order = {
            "ebay_order_id": extract(r"<OrderID>(.*?)</OrderID>", block),
            "order_status": extract(r"<OrderStatus>(.*?)</OrderStatus>", block),
            "total": extract(r"<Total[^>]*>(.*?)</Total>", block, "0.00"),
            "created_time": extract(r"<CreatedTime>(.*?)</CreatedTime>", block),
            "buyer_user_id": extract(r"<BuyerUserID>(.*?)</BuyerUserID>", block),
        }

        # Shipping address
        shipping_block = re.search(
            r"<ShippingAddress>(.*?)</ShippingAddress>", block, re.DOTALL
        )
        if shipping_block:
            addr = shipping_block.group(1)
            order["shipping"] = {
                "name": extract(r"<Name>(.*?)</Name>", addr),
                "street1": extract(r"<Street1>(.*?)</Street1>", addr),
                "street2": extract(r"<Street2>(.*?)</Street2>", addr),
                "city": extract(r"<CityName>(.*?)</CityName>", addr),
                "state": extract(r"<StateOrProvince>(.*?)</StateOrProvince>", addr),
                "postal_code": extract(r"<PostalCode>(.*?)</PostalCode>", addr),
                "country": extract(r"<Country>(.*?)</Country>", addr, "US"),
                "phone": extract(r"<Phone>(.*?)</Phone>", addr),
            }

        # Line items
        order["line_items"] = []
        for trans in re.findall(r"<Transaction>(.*?)</Transaction>", block, re.DOTALL):
            order["line_items"].append({
                "ebay_item_id": extract(r"<ItemID>(.*?)</ItemID>", trans),
                "title": extract(r"<Title>(.*?)</Title>", trans, "Unknown Item"),
                "sku": extract(r"<SKU>(.*?)</SKU>", trans),
                "quantity": int(extract(r"<QuantityPurchased>(.*?)</QuantityPurchased>", trans, "1")),
                "price": extract(r"<TransactionPrice[^>]*>(.*?)</TransactionPrice>", trans, "0.00"),
            })

        return order
