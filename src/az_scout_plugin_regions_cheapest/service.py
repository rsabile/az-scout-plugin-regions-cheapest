"""Core business logic for the Regions Cheapest plugin.

Computes per-region average VM pricing using a curated SKU sample set and
the core az-scout pricing API.  Results are cached with a configurable TTL
to avoid redundant Azure Retail Prices API calls.

**Key definitions:**

* *Average price per VM per hour* — mean hourly Linux retail (pay-as-you-go)
  price across the curated SKU sample for a given region.
* *Availability %* — ``(priced_count / sample_size) * 100`` where
  ``priced_count`` is the number of sample SKUs that have a valid price
  in the region.
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

# ---------------------------------------------------------------------------
# Curated SKU sample – a representative cross-section of popular VM sizes.
# Keep deliberately small to limit API calls.
# ---------------------------------------------------------------------------
DEFAULT_SKU_SAMPLE: list[str] = [
    "Standard_B2s",
    "Standard_B4ms",
    "Standard_D2s_v5",
    "Standard_D4s_v5",
    "Standard_D8s_v5",
    "Standard_E2s_v5",
    "Standard_E4s_v5",
    "Standard_F2s_v2",
    "Standard_F4s_v2",
    "Standard_L8s_v3",
]

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
    sku_sample: list[str],
    currency_code: str,
) -> dict[str, float | None]:
    """Fetch pay-as-you-go prices for the SKU sample in one region.

    Calls the core ``get_retail_prices`` function which fetches all VM
    prices for a region in a single paginated request and caches them.
    This avoids N per-SKU API calls.
    """
    from az_scout.azure_api.pricing import get_retail_prices

    all_prices = get_retail_prices(region_name, currency_code)
    result: dict[str, float | None] = {}
    for sku in sku_sample:
        entry = all_prices.get(sku)
        result[sku] = entry["paygo"] if entry and entry.get("paygo") is not None else None
    return result


def compute_region_stats(
    tenant_id: str | None = None,
    currency: str = "USD",
    group_by: str = "region",
    sku_sample: list[str] | None = None,
) -> RegionPriceSummaryResult:
    """Compute per-region average pricing from a curated SKU sample.

    Parameters
    ----------
    tenant_id:
        Azure tenant ID (optional, forwarded to discovery API).
    currency:
        ISO 4217 currency code, default ``"USD"``.
    group_by:
        ``"region"`` (default) or ``"geography"``.
    sku_sample:
        Override the default SKU sample list.

    Returns
    -------
    RegionPriceSummaryResult
        Contains rows for each region and metadata.
    """
    sample = sku_sample or DEFAULT_SKU_SAMPLE
    cache_key = f"{tenant_id or ''}:{currency}:{','.join(sorted(sample))}"
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
            sample_skus=sample,
            currency=currency,
        )

    geo_map = _load_geography_map()
    loc_map = _load_location_map()
    timestamp = datetime.now(UTC).isoformat()

    # --- Fetch prices concurrently (limited) ---
    region_prices: dict[str, dict[str, float | None]] = {}

    def _fetch_one(region_name: str) -> tuple[str, dict[str, float | None]]:
        return region_name, _fetch_region_prices(region_name, sample, currency)

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
        sample_size = len(sample)
        avg_price = sum(valid_prices) / priced_count if priced_count else None
        availability_pct = (priced_count / sample_size * 100) if sample_size else 0.0

        loc = loc_map.get(region_name, {})
        rows.append(
            RegionPriceRow(
                geography=geo_map.get(region_name, "Unknown"),
                region_name=display_names.get(region_name, region_name),
                region_id=region_name,
                avg_price=round(avg_price, 6) if avg_price is not None else None,
                availability_pct=availability_pct,
                sample_size=sample_size,
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
        sample_skus=sample,
        currency=currency,
    )
    _result_cache[cache_key] = (time.monotonic(), result)
    return result


def get_cheapest_regions(
    tenant_id: str | None = None,
    currency: str = "USD",
    top_n: int = 10,
    sku_sample: list[str] | None = None,
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
    sku_sample:
        Override the default SKU sample list.

    Returns
    -------
    list[dict]
        Ranked cheapest regions with delta information.
    """
    from az_scout_plugin_regions_cheapest.models import CheapestRegionRow

    summary = compute_region_stats(tenant_id, currency, "region", sku_sample)
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
            sample_size=row.sample_size,
            priced_count=row.priced_count,
            timestamp_utc=row.timestamp_utc,
        )
        result.append(cr.to_dict())
    return result
