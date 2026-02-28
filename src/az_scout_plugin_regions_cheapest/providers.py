"""Pricing data providers for the Regions Cheapest plugin.

Two providers are available:

* **DbPricingProvider** – bulk-fetches cached prices from the
  ``az-scout-plugin-bdd-sku`` PostgreSQL cache plugin via internal HTTP.
* **CoreApiPricingProvider** – fetches prices from the core
  ``get_retail_prices`` function (live Azure Retail Prices API, 1-hour cache).

The service layer tries the DB provider first, falling back to the core
provider when the DB plugin is unavailable or data coverage is insufficient.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Protocol

import requests as _requests

logger = logging.getLogger(__name__)

_DB_STATUS_URL = "/plugins/bdd-sku/status"
_DB_QUERY_URL = "/plugins/bdd-sku/retail/query"
_HTTP_TIMEOUT = 5  # seconds


class PricingProvider(Protocol):
    """Protocol for pricing data sources."""

    def get_prices_bulk(
        self,
        *,
        regions: list[str],
        skus: list[str],
        currency: str,
        tenant_id: str | None,
    ) -> dict[tuple[str, str], float]:
        """Return ``{(region, sku): price_hourly}`` for the requested pairs."""
        ...  # pragma: no cover


def _internal_get(path: str, *, base_url: str, timeout: float = _HTTP_TIMEOUT) -> dict[str, Any]:
    """GET an internal endpoint on the same az-scout server."""
    url = base_url.rstrip("/") + path
    resp = _requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


def _internal_post(
    path: str,
    *,
    base_url: str,
    json: dict[str, Any],
    timeout: float = _HTTP_TIMEOUT,
) -> dict[str, Any]:
    """POST to an internal endpoint on the same az-scout server."""
    url = base_url.rstrip("/") + path
    resp = _requests.post(url, json=json, timeout=timeout)
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


class DbPricingProvider:
    """Fetch cached prices from the bdd-sku PostgreSQL plugin."""

    def __init__(self, base_url: str = "http://127.0.0.1:5001") -> None:
        self._base_url = base_url

    def is_available(self) -> bool:
        """Return *True* if the DB plugin is reachable and has data."""
        try:
            data = _internal_get(_DB_STATUS_URL, base_url=self._base_url)
            count = data.get("retail_prices_count", 0)
            return bool(data.get("db_connected")) and int(count) > 0
        except Exception:
            logger.debug("DB pricing provider not available", exc_info=True)
            return False

    def get_prices_bulk(
        self,
        *,
        regions: list[str],
        skus: list[str],
        currency: str,
        tenant_id: str | None,
    ) -> dict[tuple[str, str], float]:
        """Query the DB plugin for bulk prices.

        Returns ``{(region, sku): price_hourly}`` for every row the
        DB plugin returned.
        """
        try:
            body: dict[str, Any] = {
                "currency": currency,
                "regions": regions,
                "skus": skus,
                "fresh_only": True,
            }
            data = _internal_post(_DB_QUERY_URL, base_url=self._base_url, json=body)
            result: dict[tuple[str, str], float] = {}
            for row in data.get("rows", []):
                region = row.get("region", "")
                sku = row.get("sku", "")
                price = row.get("price_hourly")
                if region and sku and price is not None:
                    result[(region, sku)] = float(price)
            return result
        except Exception:
            logger.warning("DB bulk price query failed", exc_info=True)
            return {}


_MAX_CONCURRENCY = 8


class CoreApiPricingProvider:
    """Fetch prices from the core az-scout Retail Prices API."""

    def get_prices_bulk(
        self,
        *,
        regions: list[str],
        skus: list[str],
        currency: str,
        tenant_id: str | None,
    ) -> dict[tuple[str, str], float]:
        """Fetch prices for all ``(region, sku)`` pairs via the core API.

        Uses ``get_retail_prices`` per region with concurrency limit.
        """
        from az_scout.azure_api.pricing import get_retail_prices

        result: dict[tuple[str, str], float] = {}
        sku_set = {s.lower() for s in skus}

        def _fetch_region(region: str) -> dict[tuple[str, str], float]:
            prices = get_retail_prices(region, currency)
            partial: dict[tuple[str, str], float] = {}
            for sku_name, entry in prices.items():
                if sku_set and sku_name.lower() not in sku_set:
                    continue
                paygo = entry.get("paygo")
                if paygo is not None:
                    partial[(region, sku_name)] = float(paygo)
            return partial

        workers = min(len(regions), _MAX_CONCURRENCY) if regions else 1
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_fetch_region, r): r for r in regions}
            for future in as_completed(futures):
                try:
                    result.update(future.result())
                except Exception:
                    rn = futures[future]
                    logger.warning("Core pricing fetch failed for %s", rn)

        return result
