"""FastAPI routes for the Regions Cheapest plugin.

Mounted at ``/plugins/regions-cheapest/`` by the core plugin manager.
"""

import asyncio

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/summary")
async def summary(
    groupBy: str = Query("region", description="Group by 'region' or 'geography'."),  # noqa: N803
) -> JSONResponse:
    """Return per-region average VM pricing summary."""
    from az_scout_bdd_sku.api_client import ApiNotConfiguredError  # type: ignore[import-untyped]

    from az_scout_plugin_regions_cheapest.service import compute_region_stats

    try:
        result = await asyncio.to_thread(
            compute_region_stats,
            group_by=groupBy,
        )
    except ApiNotConfiguredError:
        return JSONResponse(
            {"error": "BDD SKU API is not configured. Set the API URL in plugin settings."},
            status_code=503,
        )
    return JSONResponse(
        {
            "rows": [r.to_dict() for r in result.rows],
            "timestampUtc": result.timestamp_utc,
            "dataSource": result.data_source,
        }
    )


@router.get("/cheapest")
async def cheapest(
    topN: int = Query(10, description="Number of cheapest regions to return."),  # noqa: N803
) -> JSONResponse:
    """Return the top N cheapest Azure regions by average VM price."""
    from az_scout_bdd_sku.api_client import ApiNotConfiguredError

    from az_scout_plugin_regions_cheapest.service import get_cheapest_regions

    try:
        rows, data_source = await asyncio.to_thread(
            get_cheapest_regions,
            top_n=topN,
        )
    except ApiNotConfiguredError:
        return JSONResponse(
            {"error": "BDD SKU API is not configured. Set the API URL in plugin settings."},
            status_code=503,
        )
    return JSONResponse({"rows": rows, "dataSource": data_source})
