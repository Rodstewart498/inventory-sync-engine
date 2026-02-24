"""
Multi-account eBay routing with cached account resolution.

When managing multiple eBay seller accounts, each item exists on exactly
one account. The router avoids unnecessary API calls by:
    1. Checking for a cached account (from previous successful sync)
    2. Trying the cached account first
    3. Falling through to remaining accounts only on failure
    4. Aborting entirely on rate limits (shared API quota)
    5. Caching the successful account for next time
"""

import os
import json
import logging
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class AccountRouter:
    """
    Routes eBay API operations to the correct seller account.

    Uses entry-level caching: each inventory entry stores the account
    it was last successfully synced to, so subsequent syncs skip
    directly to that account.
    """

    def __init__(self, configs_dir: str,
                 token_provider: Callable[[str], str]):
        """
        Args:
            configs_dir: Directory containing per-account JSON config files.
            token_provider: Callable that takes a config file path and
                            returns a valid OAuth access token.
        """
        self.configs_dir = configs_dir
        self.token_provider = token_provider
        self._accounts: Dict[str, dict] = {}
        self._scan_configs()

    def _scan_configs(self):
        """Discover eBay account configs from the configs directory."""
        self._accounts = {}
        if not os.path.isdir(self.configs_dir):
            logger.warning(f"eBay configs directory not found: {self.configs_dir}")
            return

        for filename in sorted(os.listdir(self.configs_dir)):
            if not filename.endswith(".json"):
                continue

            filepath = os.path.join(self.configs_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    conf = json.load(f)

                name = conf.get("account_name") or os.path.splitext(filename)[0]
                self._accounts[name] = {"filepath": filepath, "config": conf}
                logger.info(f"Discovered eBay account: {name}")

            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Skipping invalid config {filename}: {e}")

    @property
    def account_names(self) -> List[str]:
        return list(self._accounts.keys())

    def get_config_path(self, account_name: str) -> Optional[str]:
        info = self._accounts.get(account_name)
        return info["filepath"] if info else None

    def route_operation(self, entry: dict,
                        operation: Callable,
                        *args, **kwargs) -> dict:
        """
        Execute an operation on the correct eBay account.

        Resolution strategy:
            1. If entry has a cached 'ebay_account', try that first
            2. On failure, try remaining accounts in order
            3. Stop on first success (item can only be on one account)
            4. Abort on rate limit (shared API quota across accounts)
            5. Cache successful account on entry for future calls

        Args:
            entry: Inventory entry dict (must have 'ebay_item_id',
                   may have 'ebay_account' cache).
            operation: Callable that takes (EbayClient, entry, *args, **kwargs)
                       and returns a result dict with 'success'.
            *args, **kwargs: Passed through to the operation.

        Returns:
            Result dict with 'success', 'message', 'account',
            'results' (per-account), and counts.
        """
        if not self._accounts:
            return {"success": False, "message": "No eBay accounts configured"}

        cached_account = entry.get("ebay_account")
        account_order = self._build_account_order(cached_account)

        results = []
        successful_account = None

        for account_name in account_order:
            info = self._accounts[account_name]

            try:
                token = self.token_provider(info["filepath"])
            except Exception as e:
                results.append({
                    "success": False,
                    "message": f"Token error: {e}",
                    "account": account_name,
                })
                continue

            from .ebay_client import EbayClient
            client = EbayClient(token, account_name)

            result = operation(client, entry, *args, **kwargs)
            result["account"] = account_name
            results.append(result)

            # Abort on rate limit — shared quota
            if result.get("rate_limited"):
                logger.warning("Rate limit hit — aborting remaining accounts")
                break

            # Stop on first success
            if result.get("success") and not result.get("skipped"):
                successful_account = account_name
                break

        # Cache successful account
        if successful_account:
            entry["ebay_account"] = successful_account

        success = successful_account is not None
        message = (
            f"Synced ({successful_account})"
            if success
            else f"Failed on all {len(self._accounts)} accounts"
        )

        return {
            "success": success,
            "message": message,
            "account": successful_account,
            "results": results,
            "success_count": 1 if success else 0,
            "fail_count": sum(
                1 for r in results
                if not r.get("success") and not r.get("skipped")
            ),
            "total_accounts": len(self._accounts),
        }

    def route_single(self, account_name: str,
                     operation: Callable,
                     entry: dict,
                     *args, **kwargs) -> dict:
        """
        Execute an operation on a specific named account.

        Args:
            account_name: Target account name.
            operation: Callable that takes (EbayClient, entry, *args, **kwargs).
            entry: Inventory entry dict.

        Returns:
            Result dict from the operation.
        """
        info = self._accounts.get(account_name)
        if not info:
            return {"success": False, "message": f"Unknown account: {account_name}"}

        try:
            token = self.token_provider(info["filepath"])
        except Exception as e:
            return {"success": False, "message": f"Token error: {e}"}

        from .ebay_client import EbayClient
        client = EbayClient(token, account_name)
        return operation(client, entry, *args, **kwargs)

    def _build_account_order(self, cached_account: Optional[str]) -> List[str]:
        """Build account try-order: cached first, then the rest."""
        if cached_account and cached_account in self._accounts:
            rest = [n for n in self._accounts if n != cached_account]
            return [cached_account] + rest
        return list(self._accounts.keys())
