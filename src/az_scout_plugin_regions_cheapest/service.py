"""Core business logic for the Regions Cheapest plugin.

Computes per-region average VM pricing using pre-aggregated data from
the ``az-scout-plugin-bdd-sku`` API.  This plugin is a **mandatory**
dependency — without a configured BDD API URL, it returns an error.

**Key definitions:**

* *Average price per VM per hour* — mean hourly Linux retail (pay-as-you-go)
  price across all VM SKUs in a region (pre-computed by the BDD API).
* *Median price* — median hourly price across all VM SKUs.
"""

import json
import logging
import statistics
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from az_scout_plugin_regions_cheapest.models import (
    RegionPriceRow,
    RegionPriceSummaryResult,
)

logger = logging.getLogger(__name__)

_CACHE_TTL = 600  # 10 minutes

# Module-level cache: key -> (timestamp, result)
_result_cache: dict[str, tuple[float, RegionPriceSummaryResult]] = {}
_cache_lock = threading.Lock()

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


def _enrich_region(
    region: str,
    geo_map: dict[str, str],
    loc_map: dict[str, dict[str, Any]],
) -> tuple[str, str, float | None, float | None]:
    """Return (geography, country_code, lat, lon) for a region ID."""
    geography = geo_map.get(region, "Unknown")
    loc = loc_map.get(region, {})
    return geography, loc.get("countryCode", ""), loc.get("lat"), loc.get("lon")


def _fetch_all_summary_rows() -> list[dict[str, Any]]:
    """Fetch all pricing summary rows from the BDD API, handling pagination."""
    from az_scout_bdd_sku.api_client import (  # type: ignore[import-untyped]
        v1_pricing_summary_latest,
    )

    all_rows: list[dict[str, Any]] = []
    cursor = ""
    while True:
        resp = v1_pricing_summary_latest(
            price_type="retail",
            limit=1000,
            cursor=cursor,
        )
        rows = resp.get("rows", [])
        all_rows.extend(rows)
        next_cursor = resp.get("nextCursor", "")
        if not next_cursor or not rows:
            break
        cursor = next_cursor
    return all_rows


def _aggregate_by_geography(region_result: RegionPriceSummaryResult) -> RegionPriceSummaryResult:
    """Aggregate per-region rows into per-geography rows.

    Uses mean-of-means for avg/median price, min/max across all regions,
    and sum for sku_count.
    """
    geo_groups: dict[str, list[RegionPriceRow]] = defaultdict(list)
    for row in region_result.rows:
        geo_groups[row.geography].append(row)

    agg_rows: list[RegionPriceRow] = []
    for geography, rows in geo_groups.items():
        avg_prices = [r.avg_price for r in rows if r.avg_price is not None]
        median_prices = [r.median_price for r in rows if r.median_price is not None]
        min_prices = [r.min_price for r in rows if r.min_price is not None]
        max_prices = [r.max_price for r in rows if r.max_price is not None]
        agg_rows.append(
            RegionPriceRow(
                geography=geography,
                region_name=geography,
                region_id=geography,
                avg_price=statistics.mean(avg_prices) if avg_prices else None,
                median_price=statistics.mean(median_prices) if median_prices else None,
                min_price=min(min_prices) if min_prices else None,
                max_price=max(max_prices) if max_prices else None,
                sku_count=sum(r.sku_count for r in rows),
                timestamp_utc=region_result.timestamp_utc,
            )
        )

    agg_rows.sort(key=lambda r: (r.avg_price is None, r.avg_price or 0))
    return RegionPriceSummaryResult(
        rows=agg_rows,
        timestamp_utc=region_result.timestamp_utc,
        data_source=region_result.data_source,
    )


def compute_region_stats(
    group_by: str = "region",
) -> RegionPriceSummaryResult:
    """Compute per-region average pricing from BDD API data.

    Fetches all pricing summary rows (latest snapshot, retail, global
    aggregate) from the ``az-scout-plugin-bdd-sku`` API and enriches
    them with geography and location metadata.

    Parameters
    ----------
    group_by:
        ``"region"`` (default) or ``"geography"``.

    Returns
    -------
    RegionPriceSummaryResult
        Contains rows for each region and metadata.
    """
    # Raw per-region data is the base cache layer; geography is derived from it.
    cache_key = "summary_region"
    now = time.monotonic()

    with _cache_lock:
        cached = _result_cache.get(cache_key)
        _cached_region: RegionPriceSummaryResult | None = None
        _cached_geo: RegionPriceSummaryResult | None = None
        if cached is not None:
            ts, region_data = cached
            if now - ts < _CACHE_TTL:
                if group_by != "geography":
                    _cached_region = region_data
                else:
                    geo_cached = _result_cache.get("summary_geography")
                    if geo_cached is not None:
                        geo_ts, geo_data = geo_cached
                        if now - geo_ts < _CACHE_TTL:
                            _cached_geo = geo_data

    if _cached_region is not None:
        return _cached_region
    if _cached_geo is not None:
        return _cached_geo

    from az_scout_bdd_sku.api_client import ApiNotConfiguredError

    try:
        api_rows = _fetch_all_summary_rows()
    except ApiNotConfiguredError:
        raise
    except Exception:
        logger.exception("Failed to fetch pricing summary from BDD API")
        return RegionPriceSummaryResult()

    geo_map = _load_geography_map()
    loc_map = _load_location_map()

    # Filter to global aggregates (category is null → None in JSON)
    global_rows = [r for r in api_rows if r.get("category") is None]

    rows: list[RegionPriceRow] = []
    timestamp = ""
    for r in global_rows:
        region = r.get("region", "")
        if not region:
            continue
        geography, country_code, lat, lon = _enrich_region(region, geo_map, loc_map)
        snap = r.get("snapshotUtc", "")
        if snap and not timestamp:
            timestamp = snap
        rows.append(
            RegionPriceRow(
                geography=geography,
                region_name=region,
                region_id=region,
                avg_price=r.get("avgPrice"),
                median_price=r.get("medianPrice"),
                min_price=r.get("minPrice"),
                max_price=r.get("maxPrice"),
                sku_count=r.get("skuCount", 0),
                timestamp_utc=snap,
                country_code=country_code,
                lat=lat,
                lon=lon,
            )
        )

    rows.sort(key=lambda r: (r.avg_price is None, r.avg_price or 0))

    region_result = RegionPriceSummaryResult(
        rows=rows,
        timestamp_utc=timestamp,
        data_source="bdd",
    )

    with _cache_lock:
        _result_cache[cache_key] = (time.monotonic(), region_result)

    if group_by == "geography":
        geo_result = _aggregate_by_geography(region_result)
        with _cache_lock:
            _result_cache["summary_geography"] = (time.monotonic(), geo_result)
        return geo_result

    return region_result


def get_cheapest_regions(
    top_n: int = 10,
    price_type: str = "retail",
    metric: str = "median",
) -> tuple[list[dict[str, object]], str]:
    """Return the top N cheapest regions by pricing metric.

    Delegates to the BDD API ``v1_pricing_cheapest`` endpoint which
    returns pre-ranked results.

    Parameters
    ----------
    top_n:
        Number of regions to return.
    price_type:
        Price type filter (default ``"retail"``).
    metric:
        Pricing metric to rank by (default ``"median"``).

    Returns
    -------
    tuple[list[dict], str]
        Ranked cheapest regions with delta information, and the data source label.
    """
    from az_scout_bdd_sku.api_client import ApiNotConfiguredError, v1_pricing_cheapest

    from az_scout_plugin_regions_cheapest.models import CheapestRegionRow

    try:
        resp = v1_pricing_cheapest(
            price_type=price_type,
            metric=metric,
            limit=top_n,
        )
    except ApiNotConfiguredError:
        raise
    except Exception:
        logger.exception("Failed to fetch cheapest regions from BDD API")
        return [], "bdd"

    api_rows = resp.get("rows", [])
    geo_map = _load_geography_map()
    loc_map = _load_location_map()

    if not api_rows:
        return [], "bdd"

    cheapest_price = api_rows[0].get("avgPrice", 0.0) or 0.0
    result: list[dict[str, object]] = []
    for i, r in enumerate(api_rows):
        region = r.get("region", "")
        geography, _cc, _lat, _lon = _enrich_region(region, geo_map, loc_map)
        avg_price = r.get("avgPrice", 0.0) or 0.0
        cr = CheapestRegionRow(
            rank=i + 1,
            geography=geography,
            region_name=region,
            region_id=region,
            avg_price=avg_price,
            median_price=r.get("medianPrice", 0.0) or 0.0,
            delta_vs_cheapest=avg_price - cheapest_price,
            sku_count=r.get("skuCount", 0),
            timestamp_utc=r.get("snapshotUtc", ""),
        )
        result.append(cr.to_dict())
    return result, "bdd"
