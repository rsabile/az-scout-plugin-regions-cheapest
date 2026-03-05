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
import time
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
    from az_scout_bdd_sku.api_client import v1_pricing_summary_latest  # type: ignore[import-untyped]

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
    cache_key = "summary"
    now = time.monotonic()
    cached = _result_cache.get(cache_key)
    if cached is not None:
        ts, data = cached
        if now - ts < _CACHE_TTL:
            return data

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

    result = RegionPriceSummaryResult(
        rows=rows,
        timestamp_utc=timestamp,
        data_source="bdd",
    )
    _result_cache[cache_key] = (time.monotonic(), result)
    return result


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
