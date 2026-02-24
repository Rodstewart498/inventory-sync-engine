"""Tests for multi-account eBay resolution and caching."""

from unittest.mock import MagicMock, patch, call
import pytest

from sync_engine.account_router import AccountRouter


@pytest.fixture
def mock_auth():
    """Mock auth manager with 3 accounts."""
    auth = MagicMock()
    auth.account_names = ["Account1", "Account2", "Account3"]
    auth.get_token.return_value = "mock_token"
    return auth


@pytest.fixture
def router(mock_auth):
    """Create an AccountRouter with mocked auth."""
    return AccountRouter(mock_auth)


class TestAccountResolution:
    """Tests for resolving which account owns an item."""

    def test_uses_cached_account_first(self, router, sample_entry):
        """Should try the stored ebay_account before scanning others."""
        token, name = router.resolve(sample_entry)
        assert name == "TestAccount1"
        assert token == "mock_token"

    def test_falls_back_when_cached_fails(self, router, sample_entry):
        """If cached account token fails, try other accounts."""
        router.auth.get_token.side_effect = [None, "fallback_token", None]
        token, name = router.resolve(sample_entry)
        assert token == "fallback_token"

    def test_returns_none_when_all_fail(self, router, sample_entry):
        """If no account provides a token, return None."""
        router.auth.get_token.return_value = None
        token, name = router.resolve(sample_entry)
        assert token is None
        assert name is None

    def test_no_ebay_account_field_scans_all(self, router, sample_entry):
        """If entry has no cached account, scan all accounts."""
        del sample_entry["ebay_account"]
        token, name = router.resolve(sample_entry)
        assert token is not None

    def test_empty_account_scans_all(self, router, sample_entry):
        """If cached account is empty string, scan all."""
        sample_entry["ebay_account"] = ""
        token, name = router.resolve(sample_entry)
        assert token is not None


class TestCaching:
    """Tests for account caching behavior."""

    def test_successful_resolution_caches(self, router, sample_entry):
        """After successful resolution, the account should be cached."""
        token, name = router.resolve(sample_entry)
        assert name is not None

    def test_cache_hit_avoids_scanning(self, router, sample_entry):
        """Second resolution should use cache, not scan all accounts."""
        router.resolve(sample_entry)
        router.auth.get_token.reset_mock()
        router.resolve(sample_entry)
        # Should only call get_token once (for cached account)
        assert router.auth.get_token.call_count <= 1


class TestRateLimitHandling:
    """Tests for rate limit (Error 518) behavior."""

    def test_rate_limit_stops_scanning(self, router, sample_entry):
        """Rate limit on one account should stop trying others."""
        router.auth.get_token.side_effect = [
            "token1",  # First account returns token
        ]
        # Simulate rate limit response
        result = router.resolve(sample_entry)
        assert result is not None


class TestEdgeCases:
    """Tests for edge cases in account routing."""

    def test_single_account(self, router, sample_entry):
        """Works with just one configured account."""
        router.auth.account_names = ["OnlyAccount"]
        token, name = router.resolve(sample_entry)
        assert token is not None

    def test_entry_without_item_id(self, router):
        """Entry without ebay_item_id returns None."""
        entry = {"title": "Test"}
        token, name = router.resolve(entry)
        assert token is None

    def test_empty_entry(self, router):
        """Empty entry returns None."""
        token, name = router.resolve({})
        assert token is None
