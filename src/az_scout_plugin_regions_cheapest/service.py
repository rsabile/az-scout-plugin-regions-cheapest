"""Core business logic for the Regions Cheapest plugin.

Computes per-region average VM pricing using **all** VM SKUs available
in each region.  When the ``az-scout-plugin-bdd-sku`` DB cache plugin is
installed and populated, cached prices are used in bulk.  Otherwise the
plugin falls back to the core az-scout Retail Prices API (live calls).

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
from az_scout_plugin_regions_cheapest.providers import (
    CoreApiPricingProvider,
    DbPricingProvider,
)

logger = logging.getLogger(__name__)

_CACHE_TTL = 600  # 10 minutes
_MAX_CONCURRENCY = 8
_DB_COVERAGE_THRESHOLD = 0.7  # 70 %

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


def _collect_all_skus(region_names: list[str], currency: str) -> dict[str, dict[str, float | None]]:
    """Fetch prices for every region via the core API (live path)."""
    region_prices: dict[str, dict[str, float | None]] = {}

    def _fetch_one(region_name: str) -> tuple[str, dict[str, float | None]]:
        return region_name, _fetch_region_prices(region_name, currency)

    workers = min(len(region_names), _MAX_CONCURRENCY) if region_names else 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_one, rn): rn for rn in region_names}
        for future in as_completed(futures):
            rn = futures[future]
            try:
                name, prices = future.result()
                region_prices[name] = prices
            except Exception:
                logger.warning("Failed to fetch prices for region %s", rn)

    return region_prices


def _build_rows(
    region_names: list[str],
    display_names: dict[str, str],
    region_prices: dict[str, dict[str, float | None]],
    geo_map: dict[str, str],
    loc_map: dict[str, dict[str, Any]],
    timestamp: str,
) -> list[RegionPriceRow]:
    """Build sorted ``RegionPriceRow`` list from aggregated prices."""
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
    rows.sort(key=lambda r: (r.avg_price is None, r.avg_price or 0))
    return rows


def _try_db_provider(
    region_names: list[str],
    all_skus: list[str],
    currency: str,
    tenant_id: str | None,
) -> tuple[dict[str, dict[str, float | None]], str, float]:
    """Attempt to load prices from the DB cache plugin.

    Returns
    -------
    tuple
        (region_prices dict, data_source label, coverage ratio 0-1)
    """
    db = DbPricingProvider()

    if not db.is_available():
        return {}, "live", 0.0

    db_prices = db.get_prices_bulk(
        regions=region_names,
        skus=all_skus,
        currency=currency,
        tenant_id=tenant_id,
    )

    if not db_prices:
        return {}, "live", 0.0

    # Convert flat mapping to per-region dict
    region_prices: dict[str, dict[str, float | None]] = {}
    for (region, sku), price in db_prices.items():
        region_prices.setdefault(region, {})[sku] = price

    expected = len(region_names) * len(all_skus) if all_skus else 0
    coverage = len(db_prices) / expected if expected else 0.0

    if coverage >= _DB_COVERAGE_THRESHOLD:
        return region_prices, "db", coverage

    # --- Hybrid fill: fetch missing pairs from core API ---
    missing_regions: set[str] = set()
    for rn in region_names:
        region_data = region_prices.get(rn, {})
        for sku in all_skus:
            if sku not in region_data:
                missing_regions.add(rn)
                break  # at least one SKU missing → need full region fetch

    if missing_regions:
        core = CoreApiPricingProvider()
        core_prices = core.get_prices_bulk(
            regions=list(missing_regions),
            skus=all_skus,
            currency=currency,
            tenant_id=tenant_id,
        )
        for (region, sku), price in core_prices.items():
            region_prices.setdefault(region, {})[sku] = price

    filled = sum(len(v) for v in region_prices.values())
    final_coverage = filled / expected if expected else 0.0
    return region_prices, "hybrid", final_coverage


def compute_region_stats(
    tenant_id: str | None = None,
    currency: str = "USD",
    group_by: str = "region",
) -> RegionPriceSummaryResult:
    """Compute per-region average pricing using all VM SKUs.

    When the ``az-scout-plugin-bdd-sku`` DB cache plugin is installed and
    populated, cached prices are fetched in bulk.  If the DB plugin is
    unavailable or coverage is below 70 %, the plugin automatically falls
    back to live core API calls (hybrid or full live).

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
        Contains rows for each region, metadata, and ``data_source`` label.
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
    region_names = [r["name"] for r in regions]
    display_names: dict[str, str] = {r["name"]: r["displayName"] for r in regions}

    # --- Try DB provider first, fallback to core ---
    # We need a SKU sample list for the DB query.  We fetch one region's
    # SKU catalog from the core API to get the universe of SKU names.
    sample_skus: list[str] = []
    try:
        sample_prices = _fetch_region_prices(region_names[0], currency) if region_names else {}
        sample_skus = list(sample_prices.keys())
    except Exception:
        logger.warning("Failed to fetch sample SKU list")

    data_source = "live"
    coverage_pct = 0.0

    if sample_skus:
        region_prices, data_source, coverage_pct = _try_db_provider(
            region_names,
            sample_skus,
            currency,
            tenant_id,
        )
    else:
        region_prices = {}

    # If DB path produced data, merge the sample region we already fetched
    if data_source in ("db", "hybrid") and region_names and region_names[0] not in region_prices:
        region_prices[region_names[0]] = {
            sku: price for sku, price in (sample_prices or {}).items()
        }

    # Full live fallback
    if data_source == "live":
        region_prices = _collect_all_skus(region_names, currency)
        coverage_pct = 100.0

    rows = _build_rows(region_names, display_names, region_prices, geo_map, loc_map, timestamp)

    result = RegionPriceSummaryResult(
        rows=rows,
        timestamp_utc=timestamp,
        currency=currency,
        data_source=data_source,
        coverage_pct=round(coverage_pct * 100, 1),
    )
    _result_cache[cache_key] = (time.monotonic(), result)
    return result


def get_cheapest_regions(
    tenant_id: str | None = None,
    currency: str = "USD",
    top_n: int = 10,
) -> tuple[list[dict[str, object]], str]:
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
    tuple[list[dict], str]
        Ranked cheapest regions with delta information, and the data source label.
    """
    from az_scout_plugin_regions_cheapest.models import CheapestRegionRow

    summary = compute_region_stats(tenant_id, currency, "region")
    priced = [r for r in summary.rows if r.avg_price is not None]
    priced.sort(key=lambda r: r.avg_price or 0)

    if not priced:
        return [], summary.data_source

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
    return result, summary.data_source
