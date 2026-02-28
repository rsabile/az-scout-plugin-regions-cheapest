"""Core business logic for the Regions Cheapest plugin.

Computes per-region average VM pricing using **all** VM SKUs available
in each region via the core az-scout pricing API (which already caches
results for 1 hour).  Plugin-level results are cached with a configurable
TTL to avoid redundant computation.

**Key definitions:**

* *Average price per VM per hour* — mean hourly Linux retail (pay-as-you-go)
  price across all VM SKUs returned by the Retail Prices API for a region.
* *Availability %* — ``(priced_count / sku_count) * 100`` where
  ``priced_count`` is the number of SKUs that have a valid paygo price.
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from az_scout_plugin_regions_cheapest.models import (
    RegionPriceRow,
    RegionPriceSummaryResult,
)

logger = logging.getLogger(__name__)

_CACHE_TTL = 600  # 10 minutes
_MAX_CONCURRENCY = 8

# Module-level cache: key -> (timestamp, result)
_result_cache: dict[str, tuple[float, RegionPriceSummaryResult]] = {}

_DATA_DIR = Path(__file__).parent / "static" / "data"

# Lazy-loaded geography and location lookups
_geography_map: dict[str, str] | None = None
_location_map: dict[str, dict[str, Any]] | None = None


def _load_geography_map() -> dict[str, str]:
    """Load regionId → geography label mapping."""
    global _geography_map  # noqa: PLW0603
    if _geography_map is None:
        path = _DATA_DIR / "region_geography.json"
        _geography_map = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return _geography_map


def _load_location_map() -> dict[str, dict[str, Any]]:
    """Load regionId → {lat, lon, countryCode} mapping."""
    global _location_map  # noqa: PLW0603
    if _location_map is None:
        path = _DATA_DIR / "region_locations.json"
        _location_map = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return _location_map


def _fetch_region_prices(
    region_name: str,
    currency_code: str,
) -> dict[str, float | None]:
    """Fetch pay-as-you-go prices for all VM SKUs in one region.

    Calls the core ``get_retail_prices`` function which fetches all VM
    prices for a region in a single paginated request and caches them
    (1-hour TTL).  Returns ``{sku_name: paygo_price | None}``.
    """
    from az_scout.azure_api.pricing import get_retail_prices

    all_prices = get_retail_prices(region_name, currency_code)
    return {
        sku: entry["paygo"] if entry.get("paygo") is not None else None
        for sku, entry in all_prices.items()
    }


def compute_region_stats(
    tenant_id: str | None = None,
    currency: str = "USD",
    group_by: str = "region",
) -> RegionPriceSummaryResult:
    """Compute per-region average pricing using all VM SKUs.

    Uses the core ``get_retail_prices`` function which already fetches and
    caches all VM prices per region (1-hour TTL).  This function adds a
    shorter plugin-level cache (10 min) on the computed results.

    Parameters
    ----------
    tenant_id:
        Azure tenant ID (optional, forwarded to discovery API).
    currency:
        ISO 4217 currency code, default ``"USD"``.
    group_by:
        ``"region"`` (default) or ``"geography"``.

    Returns
    -------
    RegionPriceSummaryResult
        Contains rows for each region and metadata.
    """
    cache_key = f"{tenant_id or ''}:{currency}"
    now = time.monotonic()
    cached = _result_cache.get(cache_key)
    if cached is not None:
        ts, data = cached
        if now - ts < _CACHE_TTL:
            return data

    # --- Discover regions via core API ---
    from az_scout.azure_api.discovery import list_regions

    try:
        regions = list_regions(tenant_id=tenant_id)
    except Exception:
        logger.exception("Failed to discover regions")
        return RegionPriceSummaryResult(
            timestamp_utc=datetime.now(UTC).isoformat(),
            currency=currency,
        )

    geo_map = _load_geography_map()
    loc_map = _load_location_map()
    timestamp = datetime.now(UTC).isoformat()

    # --- Fetch prices concurrently (limited) ---
    region_prices: dict[str, dict[str, float | None]] = {}

    def _fetch_one(region_name: str) -> tuple[str, dict[str, float | None]]:
        return region_name, _fetch_region_prices(region_name, currency)

    region_names = [r["name"] for r in regions]
    with ThreadPoolExecutor(max_workers=min(len(region_names), _MAX_CONCURRENCY)) as pool:
        futures = {pool.submit(_fetch_one, rn): rn for rn in region_names}
        for future in as_completed(futures):
            rn = futures[future]
            try:
                name, prices = future.result()
                region_prices[name] = prices
            except Exception:
                logger.warning("Failed to fetch prices for region %s", rn)

    # --- Build rows ---
    display_names: dict[str, str] = {r["name"]: r["displayName"] for r in regions}
    rows: list[RegionPriceRow] = []

    for region_name in region_names:
        prices = region_prices.get(region_name, {})
        valid_prices = [p for p in prices.values() if p is not None]
        priced_count = len(valid_prices)
        sku_count = len(prices)
        avg_price = sum(valid_prices) / priced_count if priced_count else None
        availability_pct = (priced_count / sku_count * 100) if sku_count else 0.0

        loc = loc_map.get(region_name, {})
        rows.append(
            RegionPriceRow(
                geography=geo_map.get(region_name, "Unknown"),
                region_name=display_names.get(region_name, region_name),
                region_id=region_name,
                avg_price=round(avg_price, 6) if avg_price is not None else None,
                availability_pct=availability_pct,
                sku_count=sku_count,
                priced_count=priced_count,
                timestamp_utc=timestamp,
                country_code=loc.get("countryCode", ""),
                lat=loc.get("lat"),
                lon=loc.get("lon"),
            )
        )

    # Sort by avg price ascending (None last)
    rows.sort(key=lambda r: (r.avg_price is None, r.avg_price or 0))

    result = RegionPriceSummaryResult(
        rows=rows,
        timestamp_utc=timestamp,
        currency=currency,
    )
    _result_cache[cache_key] = (time.monotonic(), result)
    return result


def get_cheapest_regions(
    tenant_id: str | None = None,
    currency: str = "USD",
    top_n: int = 10,
) -> list[dict[str, object]]:
    """Return the top N cheapest regions by average VM price.

    Each row includes a ``deltaVsCheapest`` field showing how much more
    expensive the region is compared to the cheapest.

    Parameters
    ----------
    tenant_id:
        Azure tenant ID (optional).
    currency:
        ISO 4217 currency code.
    top_n:
        Number of regions to return.

    Returns
    -------
    list[dict]
        Ranked cheapest regions with delta information.
    """
    from az_scout_plugin_regions_cheapest.models import CheapestRegionRow

    summary = compute_region_stats(tenant_id, currency, "region")
    priced = [r for r in summary.rows if r.avg_price is not None]
    priced.sort(key=lambda r: r.avg_price or 0)

    if not priced:
        return []

    cheapest_price = priced[0].avg_price or 0.0
    result: list[dict[str, object]] = []
    for i, row in enumerate(priced[:top_n]):
        cr = CheapestRegionRow(
            rank=i + 1,
            geography=row.geography,
            region_name=row.region_name,
            region_id=row.region_id,
            avg_price=row.avg_price or 0.0,
            delta_vs_cheapest=(row.avg_price or 0.0) - cheapest_price,
            availability_pct=row.availability_pct,
            sku_count=row.sku_count,
            priced_count=row.priced_count,
            timestamp_utc=row.timestamp_utc,
        )
        result.append(cr.to_dict())
    return result
